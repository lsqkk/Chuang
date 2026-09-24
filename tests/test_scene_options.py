"""菜单 → 场景（窗外画什么），以及窗台上那盆植物的光影方向。

两件事在这里钉住：

* **每一项开关都要真的改变画面**。1.1.13 之前"窗外画什么"是写死的，用户
  想安静一点只能忍着；现在能关，但"关了却没变"是最容易悄悄发生的事
  （画的时候忘了看那个开关）。
* **盆栽的影子必须落在盆的前方（屏幕下方）**。光源是窗外远处的太阳，
  影子自然朝观察者这一侧拉长。以前那条影子是"沿 x 斜切"出来的：太阳一偏西
  就会被推到画面外（x 偏移上千像素）、正午又几乎是横的，看上去就是用户说的
  "影子朝上"。这里既钉几何（`_plant_shadow_geom`），也钉真的画出来的那一帧。

没有 pycairo / PyGObject 的机器上整组跳过（和 tests/test_info_card.py 一个口径）。
"""

import importlib.util
import time as _time
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from chuang.scene import SkyEngine

HAS_STACK = False
try:
    if (importlib.util.find_spec("cairo") is None
            or importlib.util.find_spec("gi") is None):
        raise ImportError("没有 pycairo / PyGObject")
    import cairo
    import gi
    gi.require_version("Pango", "1.0")
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import PangoCairo

    PangoCairo.create_layout(
        cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 8, 8)))
    from chuang.render import SkyPainter, scene_height
    from chuang import street
    HAS_STACK = True
except Exception:                       # noqa: BLE001 - 缺库就跳过，不是失败
    HAS_STACK = False

TZ = ZoneInfo("Asia/Shanghai")
NOON = datetime(2026, 9, 24, 12, 20, tzinfo=TZ)
MORNING = datetime(2026, 9, 24, 8, 0, tzinfo=TZ)
EVENING = datetime(2026, 9, 24, 17, 30, tzinfo=TZ)


def _engine() -> SkyEngine:
    eng = SkyEngine()
    eng.set_location(34.3416, 108.9398, "Asia/Shanghai")
    return eng


@unittest.skipUnless(HAS_STACK, "没有 pycairo / PyGObject，跳过")
class TestSceneSwitches(unittest.TestCase):
    """每个场景开关都得改到画面；全关掉时那一层不该留下任何像素。"""

    def setUp(self):
        self.engine = _engine()
        self.w, self.h = 900, 600

    def _frame(self, when=NOON, weather=None, **toggles):
        painter = SkyPainter(seed=11)
        for name, value in toggles.items():
            setattr(painter.ui, name, value)
        scene = self.engine.build(when, weather, location_label="西安")
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, self.w, self.h)
        painter.draw(cairo.Context(surf), self.w, self.h, scene, 180.0)
        surf.flush()
        return bytes(surf.get_data()), surf.get_stride(), scene

    def _diff_rows(self, a, b, stride, y0, y1):
        """两帧在 y0..y1 这一段里有多少行真的不一样。"""
        rows = 0
        for y in range(int(y0), int(y1)):
            off = y * stride
            if a[off:off + self.w * 4] != b[off:off + self.w * 4]:
                rows += 1
        return rows

    def test_turning_off_the_skyline_changes_the_horizon(self):
        full, stride, _ = self._frame()
        bare, _, _ = self._frame(show_skyline=False)
        horizon = self.h * 0.795
        self.assertGreater(self._diff_rows(full, bare, stride,
                                           horizon - 90, horizon + 8), 5,
                           "关掉城市剪影之后，地平线上那一排楼还在")

    def test_turning_off_the_plant_changes_the_sill(self):
        full, stride, _ = self._frame()
        bare, _, _ = self._frame(show_plant=False)
        sill_top = self.h * 0.871
        self.assertGreater(self._diff_rows(full, bare, stride, sill_top, self.h),
                           3, "关掉盆栽之后，窗台上那一盆还在")

    def test_turning_off_the_stars_changes_the_night_sky(self):
        night = datetime(2026, 9, 24, 22, 30, tzinfo=TZ)
        full, stride, _ = self._frame(when=night)
        bare, _, _ = self._frame(when=night, show_stars=False)
        self.assertGreater(self._diff_rows(full, bare, stride, 0, self.h * 0.4),
                           2, "关掉星空之后，天上那些星星还亮着")

    def test_turning_off_clouds_and_weather_fx_changes_the_sky(self):
        """云与雨雪的开关：天气要按**今天**造（预报表只覆盖它自己的那几天）。"""
        from chuang.weather import Weather
        from chuang.weather import HourPoint
        now = datetime.now(TZ).replace(second=0, microsecond=0)
        when = now.replace(hour=9, minute=20)
        base = now.replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
        weather = Weather(ok=True, fetched_at=_time.time(), code=63, cloud=95.0,
                          temp=18.0, precip=2.0, lat=34.3416, lon=108.9398)
        for h in range(48):
            weather.hourly.append(HourPoint(base + timedelta(hours=h), 95.0, 63,
                                            18.0, 60.0, 2.0))
        weather.invalidate()
        full, stride, scene = self._frame(when=when, weather=weather)
        self.assertTrue(scene.has_weather, "造出来的这份天气没有落到场景里")
        no_cloud, _, _ = self._frame(when=when, weather=weather,
                                     show_clouds=False)
        no_rain, _, _ = self._frame(when=when, weather=weather,
                                    show_weatherfx=False)
        self.assertGreater(self._diff_rows(full, no_cloud, stride, 0, self.h * 0.5),
                           10, "关掉云之后，天上那些云还在")
        self.assertGreater(self._diff_rows(full, no_rain, stride, 0, self.h),
                           10, "关掉雨雪之后，雨丝还在")

    def test_street_respects_the_people_and_traffic_switches(self):
        """街这一层：都关掉时一笔都不下；开着时确实有像素。"""
        scene = self.engine.build(NOON, None, location_label="西安")
        painter = SkyPainter(seed=5)
        light = painter._light(scene, 180.0, painter._direct_light(scene))
        roster = street.roster(5)
        trees = street.trees(3)

        def painted(**kw):
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, self.w, self.h)
            cr = cairo.Context(surf)
            cr.set_source_rgb(0, 0, 0)
            cr.paint()
            street.draw(cr, self.w, self.h, scene, roster, 43200.0, light,
                        (0.3, 0.3, 0.32), (), trees, **kw)
            surf.flush()
            return bytes(surf.get_data())

        def lit(frame: bytes) -> int:
            """底是全黑的，所以"有没有画东西"看 RGB 就够了（别看 alpha 那一位）。"""
            return max((b for i, b in enumerate(frame) if i % 4 != 3), default=0)

        self.assertEqual(lit(painted(people=False, traffic=False, trees_on=False,
                                     lamps_on=False)), 0,
                         "四个开关全关掉，街上还是画了东西")
        self.assertGreater(lit(painted()), 0, "开着的时候街上什么都没有？")
        self.assertNotEqual(painted(people=False), painted(), "关掉行人没变化")
        self.assertNotEqual(painted(traffic=False), painted(), "关掉车辆没变化")
        self.assertNotEqual(painted(trees_on=False), painted(), "关掉行道树没变化")

    def test_the_scene_switches_default_to_on(self):
        """默认全开＝和以前一模一样（老用户不该一升级就少一半东西）。"""
        painter = SkyPainter(seed=1)
        from chuang.config import Config, SCENE_SWITCHES
        cfg = Config()
        for field, _label in SCENE_SWITCHES:
            self.assertTrue(getattr(painter.ui, field), f"画笔的 {field} 默认该是开的")
            self.assertTrue(getattr(cfg, field), f"配置里的 {field} 默认该是开的")


@unittest.skipUnless(HAS_STACK, "没有 pycairo / PyGObject，跳过")
class TestPlantShadow(unittest.TestCase):
    """盆栽的影子：朝盆的前方（屏幕下方）落在窗台上。"""

    PLANT_H = 104.0

    def setUp(self):
        self.engine = _engine()

    def _geom(self, when, az0=180.0):
        return SkyPainter._plant_shadow_geom(self.engine.build(when), az0,
                                             self.PLANT_H)

    def test_the_shadow_falls_towards_the_window_at_noon(self):
        """正午太阳在窗外正对面：影子朝观察者这一侧（屏幕下方）落。"""
        dx, dy, fade = self._geom(NOON)
        self.assertGreater(dy, 0, "正午的影子没有朝下（用户报的就是这个）")
        self.assertLess(abs(dx), 0.2 * self.PLANT_H, "正午的影子斜得太多了")
        self.assertGreater(fade, 0.4)

    def test_the_shadow_leans_away_from_the_sun(self):
        """上午太阳偏东 → 影子偏西（屏幕右侧）；傍晚反过来。"""
        dx_am, dy_am, _ = self._geom(MORNING)
        dx_pm, dy_pm, _ = self._geom(EVENING)
        self.assertGreater(dx_am, 0, "上午的影子该往另一边斜")
        self.assertLess(dx_pm, 0, "傍晚的影子该往另一边斜")
        self.assertGreater(dy_am, 0, "上午的影子也该朝下")
        self.assertGreater(dy_pm, 0, "傍晚的影子也该朝下")

    def test_the_shadow_always_stays_inside_the_window(self):
        """低太阳下真按 1/tan(高度角) 算，那团影子会长到横穿整扇窗——
        所以屏幕上要收着画，而且越长越淡。"""
        for hour, minute in ((6, 30), (7, 30), (12, 0), (17, 30), (18, 20)):
            dx, dy, fade = self._geom(
                datetime(2026, 9, 24, hour, minute, tzinfo=TZ))
            self.assertLessEqual(abs(dx), 0.63 * self.PLANT_H)
            self.assertLessEqual(abs(dy), 0.86 * self.PLANT_H)
            self.assertTrue(0.3 <= fade <= 1.0)

    def test_the_rendered_shadow_is_actually_below_the_pot(self):
        """真的画一帧：盆底之下要出现"比不画盆栽时更暗"的像素。"""
        w, h = 900, 600
        scene = self.engine.build(NOON, None, location_label="西安")

        def frame(with_plant):
            p = SkyPainter(seed=9)
            p.ui.show_plant = with_plant
            p.ui.plant_sway = False          # 别让风把这一帧吹得不可复现
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
            p.draw(cairo.Context(surf), w, h, scene, 180.0)
            surf.flush()
            return surf

        on, off = frame(True), frame(False)
        hs = scene_height(h)
        base_y = int(0.918 * hs)
        stride = on.get_stride()
        a, b = bytes(on.get_data()), bytes(off.get_data())
        darker_below = 0
        for y in range(base_y + 3, min(h, base_y + 60)):
            for x in range(int(0.03 * w), int(0.24 * w)):
                o = y * stride + x * 4
                if (int(a[o]) + int(a[o + 1]) + int(a[o + 2]) + 6
                        < int(b[o]) + int(b[o + 1]) + int(b[o + 2])):
                    darker_below += 1
        self.assertGreater(darker_below, 40,
                           "盆的下方/前方没有影子——光影方向还是不对")


if __name__ == "__main__":
    unittest.main()
