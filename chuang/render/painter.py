
"""把各层组装成一支画笔：`SkyPainter`。

一帧的顺序全在 `draw()` 里，**顺序是内容的一部分**（AGENTS.md §3.6）：

    天色 → 星星 → 太阳 → 月亮 → 云 → 飞机            （天空）
    → 天际线 → 路面 → 街上的人车                      （地面）
    → 雨雪 → 暗角与玻璃反光 → 窗台 → 盆栽 → 闪电       （窗外那一层 → 窗里）
    → 长卷 → 信息卡 → 提示条                         （画在画布上的东西）

雨要下在窗外、影子要落在窗台上、信息卡锚在顶部——把哪一层挪个位置，画面就不对了。
每一层自己的画法在各自的模块里，这里只管顺序与"这一帧的参数"（镜头、光照、日月的
屏幕位置）。

离屏缓存的槽位（`self._sky` / `_cloud` / `_city` / `_sill` / `_overlay` / `_info`）
都挂在画笔上、各层共用，而且**都是有主的**：谁画谁负责把键拼对（见 state.CacheSlot）。
"""

from __future__ import annotations

import time as _time

from ..scene import Scene
from .cardpaint import CardPaintLayer
from .ground import GroundLayer
from .notify import NotifyLayer
from .paint import clamp, scene_height, x_for_az, y_for_alt
from .ribbon import RibbonLayer
from .sill import SillLayer
from .sky import SkyLayer
from .state import CacheSlot, FactRow, UIState
from .weatherfx import Sprites, WeatherFX


# 各层都从 `core.PainterCore`（镜头、光、缓存）长出来，所以这里不必再写它一次
# ——写在前面反而不成 MRO（基类不能排在自己的子类前面）。
class SkyPainter(SkyLayer, GroundLayer, SillLayer, RibbonLayer,
                 CardPaintLayer, NotifyLayer):
    """把一帧场景画到 Cairo 上。"""

    # ---- 信息卡的字体档位（都在这里，改字号不用翻绘制代码）----
    # 字**不跟着窗口一路缩到看不清**：窗口小的时候字号只缩到 0.94 倍，
    # 宁可把卡片排高一点、少写两行小字。scale 只负责间距与方块的尺寸。
    TEXT_FLOOR = 0.94
    F_CITY = 15.5
    F_TIME = 33.0
    F_DATE = 12.0
    F_HERO_TEMP = 27.0
    F_HERO_WHAT = 14.0
    F_HERO_SUB = 11.0
    F_ANCHOR = 15.0       # 没有天气时主角块上那一句（"未联网 · 仅天文模式"）
    HERO_GAP = 0.62       # 主角块右列两行的墨迹气口（× 天气那句的字号）
    F_LABEL = 11.5        # 指标格的标题（"太阳"）
    F_VALUE = 14.5        # 指标格的数值（"南 173°"）
    F_NOTE = 10.5         # 指标格的副值（"仰角 54.6°"）
    F_CHIP_KEY = 10.5     # 小字条的名称（"湿度"）
    F_CHIP_VAL = 12.0     # 小字条的数值（"80%"）
    F_HINT = 12.5
    F_FOOT = 10.0

    def __init__(self, seed: int = 1) -> None:
        self.sprites = Sprites()
        self.fx = WeatherFX(seed)
        self.ui = UIState()
        self._skyline: dict[int, tuple] = {}
        self._last = _time.time()
        self._lit_phase = 0.0
        # 离屏缓存：云层、天空底色、城市、暗角与玻璃反光、窗台
        self._sky = CacheSlot()
        self._cloud = CacheSlot()
        self._city = CacheSlot()
        self._overlay = CacheSlot()
        self._sill = CacheSlot()
        self._info = CacheSlot()            # 信息卡整张的离屏图（见 _draw_info）
        self._info_rects: list = []         # 卡上能点的方块，随那张图一起缓存
        self._layout_cache: dict = {}
        self._street_roster: list | None = None
        self._street_trees: list | None = None
        # 由窗口注入"现在几点"的回调，让街上的人车按真实时间连续移动
        self.clock = None

    # ------------------------------------------------------------------
    # 顶层
    # ------------------------------------------------------------------
    def draw(self, cr, w: float, h: float, scene: Scene, az0: float,
             chrome: bool = True) -> None:
        """chrome=False 时不画信息卡与今日天色长卷——壁纸只用景色本身。"""
        now = _time.time()
        dt = clamp(now - self._last, 0.0, 0.2)
        self._last = now
        self._lit_phase = now

        fov = self._fov(w, h, scene)
        self.fx.sync(scene, now)
        self.fx.advance(dt, scene, now, w, h)
        # 景色按 hs 排版，底下的余量留给系统面板（见 scene_height）
        hs = scene_height(h)

        sun_x, sun_d = x_for_az(scene.sun_az, az0, fov, w)
        glow_x = clamp(sun_x, -0.35 * w, 1.35 * w)
        sun_y = y_for_alt(max(scene.sun_alt, -3.0), hs)
        direct = self._direct_light(scene)

        self._draw_sky(cr, w, hs, scene, glow_x, sun_y)
        if self.ui.show_stars:
            self._draw_stars(cr, w, hs, scene, az0, fov)
        self._draw_sun(cr, w, hs, scene, sun_x, sun_d, fov, direct)
        self._draw_moon(cr, w, hs, scene, az0, fov)
        if self.ui.show_clouds:
            self._draw_clouds(cr, w, hs, scene, az0, fov)
        if self.ui.show_planes:
            self._draw_plane(cr, w, hs, scene)
        light = self._light(scene, az0, direct)
        if self.ui.show_skyline:
            self._draw_skyline(cr, w, hs, scene, light)
        self._draw_ground(cr, w, hs, scene, az0, fov, light)
        if self.ui.show_people or self.ui.show_traffic or self.ui.show_trees \
                or self.ui.show_lamps:
            self._draw_street(cr, w, hs, scene, light)
        # 雨雪画在"窗外"那一层：窗台、玻璃反光、盆栽都在它前面。
        # （以前它排在最后，雨会落在窗台和花盆上——那是"下在屋里"了。）
        if self.ui.show_weatherfx:
            self._draw_precip(cr, w, h, scene)
        self._draw_vignette(cr, w, h, scene)
        self._draw_sill(cr, w, h, scene, az0, fov, sun_x, direct, hs)
        self._draw_plant_layer(cr, w, h, scene, az0, fov, direct, hs)
        if self.ui.show_weatherfx:
            self._draw_flash(cr, w, h, scene)
        if chrome and self.ui.show_ribbon:
            self._draw_ribbon(cr, w, hs, scene, canvas_h=h)
        else:
            self.ui.ribbon_rect = (0.0, 0.0, 0.0, 0.0)
        if chrome and self.ui.show_info:
            self._draw_info(cr, w, h, scene, az0, fov, direct)
        else:
            # 卡片没画的时候，命中方块必须一起清掉：以前它们留在 ui 里，
            # 空格收起卡片之后，点原来那块空白处照样会跳去预览别的时候。
            self.ui.info_rects = []
            self.ui.info_rows = []
        if chrome:
            self._draw_toast(cr, w, hs)
