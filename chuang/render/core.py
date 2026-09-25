
"""各绘制层的公共底座：镜头、光、天际线数据、离屏缓存的失效。

这一层原来是 `SkyPainter` 的一部分。拆成"层"之后有几件事是每一层都要用的——环境
光怎么算、太阳的方位怎么落到屏幕的横坐标、这座城市的天际线数据、换城市时哪些缓存
要作废——放在这里，免得每层各抄一份（抄两份迟早对不上）。
"""

from __future__ import annotations

from .. import city as _city
from ..palette import mix
from ..scene import Scene, skyline_seed
from .paint import clamp


class PainterCore:

    # ------------------------------------------------------------------
    def _fov(self, w: float, h: float, scene: Scene) -> float:
        ratio = w / max(1.0, h)
        return clamp(190.0 * (ratio / 1.55), 82.0, 205.0)

    @staticmethod
    def _direct_light(scene: Scene) -> float:
        """到达窗台的直射光：太阳高度 × 云量 × 雾。"""
        l = scene.mood.sun_light
        if scene.has_weather:
            l *= 1.0 - 0.86 * clamp(scene.cloud / 100.0, 0, 1) ** 1.2
            if scene.fog:
                l *= 0.15
            if scene.precip_kind != "none":
                l *= 0.35
        return clamp(l, 0.0, 1.0)

    @staticmethod
    def _ambient(scene: Scene) -> float:
        """到达窗台/屋顶的总环境光：太阳高度给出的环境光，再被厚云削弱。"""
        amb = scene.mood.ambient
        if scene.has_weather:
            amb *= 1.0 - 0.55 * clamp(scene.cloud / 100.0, 0, 1)
            if scene.fog:
                amb *= 0.8
        return clamp(amb, 0.0, 1.0)

    # ------------------------------------------------------------------
    # 地平线上的剪影：远山 + 城市屋顶
    # ------------------------------------------------------------------
    def _layers(self, scene: Scene):
        seed = self._city_seed(scene)
        if seed not in self._skyline:
            self._skyline[seed] = _city.generate(seed)
        return self._skyline[seed]

    @staticmethod
    def _city_seed(scene: Scene) -> int:
        """这座城市的天际线种子：由名字 + 经纬度决定，同一个城市永远一样。"""
        return skyline_seed(scene.location_name or "窗", scene.lat, scene.lon)

    def invalidate_location(self) -> None:
        """换城市之后调用：楼群数据与所有离屏缓存都得重来。

        城市那块位图的缓存键现在带了城市种子（见 _draw_skyline），所以只换名字
        也会重画；这里再整片清一次，是因为天空、云、窗台那几张的键只管"光 +
        尺寸"——换城市意味着换纬度，太阳的走法整个变了，留着旧键没有意义。
        换城市是低频操作，一次清干净最省心。
        """
        self._skyline.clear()
        for slot in (self._sky, self._cloud, self._city, self._sill, self._overlay):
            slot.invalidate()
        self._layout_cache.clear()

    def _light(self, scene: Scene, az0: float, direct: float) -> _city.Light:
        """把"此刻的天色"翻译成建筑与街道能用的光照参数。"""
        mu = scene.mood
        rel = ((scene.sun_az - az0 + 180.0) % 360.0) - 180.0
        moon_rel = ((scene.moon_az - az0 + 180.0) % 360.0) - 180.0
        return _city.Light(
            ambient=self._ambient(scene),
            direct=direct,
            sun_alt=scene.sun_alt,
            rel_az=rel,
            zenith=mu.zenith,
            horizon=mu.horizon,
            glow=mu.glow_color,
            night=clamp(-scene.sun_alt / 10.0, 0.0, 1.0),
            moon=clamp(scene.moon_light, 0.0, 1.0),
            moon_rel_az=moon_rel if scene.moon_alt > 0 else 0.0,
            lit_frac=self._lit_fraction(scene),
        )

    def _colors(self, scene: Scene):
        """近景剪影的基色（街上的人车用它来配色调）。"""
        mu = scene.mood
        amb = self._ambient(scene)
        near_col = mix(mu.horizon, (14, 16, 24), 0.90)
        near_col = tuple(c * (0.34 + 0.58 * amb) for c in near_col)
        return near_col

    def _lit_fraction(self, scene: Scene) -> float:
        """此刻有多少比例的窗户亮着灯。"""
        alt = scene.sun_alt
        if alt > 2.5:
            return 0.0
        base = clamp((2.5 - alt) / 9.0, 0.0, 1.0) ** 0.7 * 0.38
        hour = scene.when.hour + scene.when.minute / 60.0
        if 1.0 <= hour < 5.0:
            base *= 0.5
        elif hour >= 23.0 or hour < 1.0:
            base *= 0.78
        return base

    # ------------------------------------------------------------------
    # 信息卡片
    # ------------------------------------------------------------------
    #
    # 这张卡是**可以点的**（1.1.9 起）：
    #   * 每一行都是"图标 + 标题 + 数值"，鼠标停上去整行亮起来；
    #   * 点日出/日落/月亮 → 画面跳到那一刻（还是那套预览，Esc 或点提示条回此刻）；
    #   * 点窗外/风     → 摊开这条背后的完整数据（体感温度、能见度、数据来源…）；
    #   * 右上角箭头收起卡片，右下角刷新按钮立刻重问一次真实天气；
    #   * 日出到日落那条"日弧"可以悬停看时刻、点一下跳过去。
    # 命中方块全部记进 ui.info_rects，窗口那边（app.py）照着 kind 决定做什么。
    # ------------------------------------------------------------------

    @staticmethod
    def _day_frac(dt) -> float:
        return clamp((dt.hour * 60 + dt.minute) / 1440.0, 0.0, 1.0)
