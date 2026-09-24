"""左上角那张「此刻的事实」：图标、能点、不许画到框外。

1.1.9 把这张卡从"一列文本"改成了"每行一枚矢量图标 + 一个数值 + 点了有反应"，
所以这里钉住三件事：

* 每一行都带着自己的图标与"点了要干什么"（跳过去看 / 摊开数据）；
* 画出来之后，卡片上每一块能点的东西都在 ui.info_rects 里（窗口靠它做命中）；
* 图标不许越出自己的方框——那个方向上一旦有一笔路径漏出去，整幅画面上会
  突然多出一条对角线。

没有 pycairo / PyGObject 的机器上整组跳过（和 tests/test_snapshot.py 一个口径）。
"""

import hashlib
import importlib.util
import time as _time
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from chuang.scene import SkyEngine
from chuang.weather import HourPoint, Weather

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
    from chuang.render import SkyPainter, draw_icon
    HAS_STACK = True
except Exception:                       # noqa: BLE001 - 缺库就跳过，不是失败
    HAS_STACK = False

TZ = ZoneInfo("Asia/Shanghai")
DAY = datetime(2026, 9, 23, 18, 35, tzinfo=TZ)


def _engine() -> SkyEngine:
    eng = SkyEngine()
    eng.set_location(34.3416, 108.9398, "Asia/Shanghai")
    return eng


def _weather(code: int = 63, cloud: float = 95.0) -> Weather:
    w = Weather(ok=True, fetched_at=0.0, code=code, cloud=cloud, wind_speed=9.0,
                wind_dir=200.0, temp=18.0, apparent=17.0, humidity=80.0, precip=2.0,
                visibility=9000.0)
    base = DAY.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    for i in range(24):
        w.hourly.append(HourPoint(base + timedelta(hours=i), cloud, code, 18.0, 60.0))
    return w


def _weather_from(start: datetime, hours: int = 72, code: int = 63,
                  cloud: float = 95.0, fetched_at: float | None = None) -> Weather:
    """从 start 那一刻起 hours 小时的真实预报（用来测"今天"那一套排版/文案）。"""
    w = Weather(ok=True, fetched_at=_time.time() if fetched_at is None else fetched_at,
                code=code, cloud=cloud, wind_speed=9.0, wind_dir=200.0, temp=18.0,
                apparent=17.0, humidity=80.0, precip=2.0, visibility=9000.0)
    base = start.replace(tzinfo=None, minute=0, second=0, microsecond=0)
    for i in range(hours):
        w.hourly.append(HourPoint(base + timedelta(hours=i), cloud, code, 18.0, 60.0))
    w.invalidate()
    return w


@unittest.skipUnless(HAS_STACK, "没有 pycairo / PyGObject，跳过")
class TestFactRows(unittest.TestCase):

    def setUp(self):
        self.engine = _engine()

    def _rows(self, when=DAY, weather=None, weather_off=False):
        scene = self.engine.build(when, weather, location_label="西安 · 陕西省",
                                  weather_off=weather_off)
        return SkyPainter(seed=5)._rows(scene)

    def test_every_row_has_an_icon_and_a_value(self):
        rows = self._rows(weather=_weather())
        self.assertEqual([r.label for r in rows][:4],
                         ["太阳", "日出", "日落", "月亮"])
        for row in rows:
            self.assertTrue(row.icon, f"{row.label} 这一行没有图标")
            self.assertTrue(row.value, f"{row.label} 这一行没有数值")

    def test_moments_are_clickable_and_carry_a_time(self):
        rows = self._rows(weather=_weather())
        by_label = {r.label: r for r in rows}
        scene = self.engine.build(DAY, _weather(), location_label="西安")
        for label, key in (("日出", "sunrise"), ("日落", "sunset")):
            row = by_label[label]
            self.assertEqual(row.action, "open")
            self.assertEqual(row.when, scene.events[key])
        self.assertEqual(by_label["太阳"].action, "open")
        self.assertEqual(by_label["月亮"].action, "open")
        self.assertIsNotNone(by_label["月亮"].when)          # 月出或月落

    def test_weather_icon_follows_the_sky(self):
        for code, cloud, want in ((63, 95.0, "rain"), (71, 90.0, "snow"),
                                  (45, 60.0, "fog"), (3, 90.0, "cloud"),
                                  (0, 5.0, "sun")):
            rows = self._rows(weather=_weather(code, cloud))
            self.assertEqual(rows[4].icon, want, f"code={code} 该配 {want}")
            self.assertEqual(rows[4].action, "detail")

    def test_offline_and_disabled_are_worded_differently(self):
        offline = {r.label: r for r in self._rows()}                  # 没联网
        disabled = {r.label: r for r in self._rows(weather_off=True)}  # 用户关掉了
        self.assertIn("未联网", offline["窗外"].value)
        self.assertIn("关掉了", disabled["窗外"].value)
        self.assertNotIn("未联网", disabled["窗外"].value)

    def test_days_we_have_no_forecast_for_say_so(self):
        """跳到预报范围以外的日子：卡片要老实说没有，不许拿邻天顶上去。"""
        rows = self._rows(when=DAY + timedelta(days=3), weather=_weather())
        self.assertIn("预报", {r.label: r for r in rows}["窗外"].value)
        scene = self.engine.build(DAY + timedelta(days=3), _weather(),
                                  location_label="西安")
        self.assertTrue(scene.weather_nodata)
        self.assertFalse(scene.has_weather)
        # 这一天有预报的时候就不会这么说
        self.assertFalse(self.engine.build(DAY, _weather(),
                                           location_label="西安").weather_nodata)


@unittest.skipUnless(HAS_STACK, "没有 pycairo / PyGObject，跳过")
class TestInfoCardDrawing(unittest.TestCase):

    def setUp(self):
        self.painter = SkyPainter(seed=7)
        self.engine = _engine()

    def _draw(self, when=DAY, weather=None, w=1000, h=640, compact=False,
              info_buttons=True):
        scene = self.engine.build(when, weather, location_label="西安 · 陕西省")
        self.painter.ui.info_compact = compact
        self.painter.ui.info_buttons = info_buttons
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        self.painter.draw(cairo.Context(surf), w, h, scene, 180.0)
        return scene

    def test_rects_cover_everything_clickable(self):
        self._draw(weather=_weather())
        kinds = [r[4] for r in self.painter.ui.info_rects]
        self.assertIn("toggle", kinds)          # 收起
        self.assertIn("arc", kinds)             # 日出到日落那条弧
        self.assertIn("refresh", kinds)         # 立刻刷一次天气
        self.assertGreaterEqual(kinds.count("open"), 3)
        self.assertIn("detail", kinds)

    def test_every_rect_lives_inside_the_card(self):
        self._draw(weather=_weather())
        for x, y, rw, rh, _kind, _when in self.painter.ui.info_rects:
            self.assertGreaterEqual(x, 0)
            self.assertGreaterEqual(y, 0)
            self.assertLessEqual(x + rw, 1000)
            self.assertLessEqual(y + rh, 640)

    def test_a_day_without_a_forecast_still_draws_a_sane_card(self):
        """跳到一个还没有预报的日子：卡片照画，只是那一行换成"还没有预报"。"""
        self._draw(when=DAY + timedelta(days=6), weather=_weather())
        kinds = [r[4] for r in self.painter.ui.info_rects]
        self.assertIn("toggle", kinds)
        self.assertIn("refresh", kinds)
        values = " ".join(getattr(r, "value", "") for r in self.painter.ui.info_rows)
        self.assertIn("预报", values)

    def test_compact_mode_keeps_only_the_headline(self):
        self._draw(weather=_weather(), compact=True)
        kinds = [r[4] for r in self.painter.ui.info_rects]
        self.assertNotIn("arc", kinds)
        self.assertNotIn("open", kinds)
        self.assertIn("toggle", kinds)          # 还得能展开回去

    def test_the_card_has_three_tiers(self):
        """这张卡分三层：主角（此刻的天气）/ 指标格（日月）/ 小字条。

        1.1.13 之前是一模一样的一长溜，谁也看不出哪个重要。这里钉住：
        主角那一块在（点了摊开数据）、指标格 ≥3 个（点了跳过去）、
        小字条也画出来了（体感、湿度、风…）。
        """
        self._draw(weather=_weather())
        rects = self.painter.ui.info_rects
        kinds = [r[4] for r in rects]
        self.assertGreaterEqual(kinds.count("open"), 3)
        self.assertGreaterEqual(kinds.count("detail"), 2)
        now = datetime.now(TZ).replace(second=0, microsecond=0)
        chips = self.painter._info_chips(
            self.engine.build(now, _weather_from(now.replace(hour=0)),
                              location_label="西安"))
        labels = [name for name, _value in chips]
        self.assertIn("湿度", labels)
        self.assertIn("风", labels)
        # 小字条是排在主角与指标格**之后**的那些方块（顺序也是版式的一部分）
        self.assertEqual(kinds[0], "toggle")        # 抬头：收起箭头
        self.assertEqual(kinds[1], "arc")           # 日弧
        self.assertEqual(kinds[2], "detail")        # 主角
        self.assertEqual(kinds[3:7], ["open"] * 4)  # 太阳 / 日出 / 日落 / 月亮

    def test_the_little_chips_carry_the_new_readings(self):
        """接口给的那几样（紫外线 / 气压 / 露点 / 今日高低温）要上卡。"""
        now = datetime.now(TZ).replace(hour=12, minute=0, second=0, microsecond=0)
        w = _weather_from(now.replace(hour=0))
        w.dew = 12.0
        w.pressure = 1012.0
        w.daily[now.strftime("%Y-%m-%d")] = (24.0, 12.0)
        sc = self.engine.build(now, w, location_label="西安 · 陕西省")
        self.assertTrue(sc.weather_now)
        chips = dict(self.painter._info_chips(sc))
        self.assertIn("露点", chips)
        self.assertIn("气压", chips)
        # 逐小时表里带上了紫外线（老数据里没有那一列）
        w.hourly[12].uv = 6.0
        w.invalidate()
        sc2 = self.engine.build(now, w, location_label="西安 · 陕西省")
        self.assertIn("紫外线", dict(self.painter._info_chips(sc2)))
        # 主角那一行底下写着今天的最高最低
        self.assertIn("今日", self.painter._hero_sub(sc2))

    def test_the_cache_key_notices_the_new_readings(self):
        """紫外线 / 气压 / 露点变了，卡片也得重画（不然会停在上一份读数上）。"""
        base = datetime.now(TZ).replace(hour=12, minute=0, second=0, microsecond=0)
        w1 = _weather_from(base.replace(hour=0))
        w2 = _weather_from(base.replace(hour=0))
        w1.pressure, w2.pressure = 1001.0, 1019.0
        self.assertNotEqual(self._card_pixels(when=base, weather=w1),
                            self._card_pixels(when=base, weather=w2),
                            "气压变了，卡片还停在旧的那一行上")
        w3 = _weather_from(base.replace(hour=0))
        w3.dew = 3.0
        self.assertNotEqual(self._card_pixels(when=base, weather=w2),
                            self._card_pixels(when=base, weather=w3),
                            "露点变了，卡片还停在旧的那一行上")

    def test_polar_day_has_no_arc(self):
        """极昼没有日出日落——那时候不许画一条假的日弧出来。"""
        eng = SkyEngine()
        eng.set_location(78.2, 15.6, "Arctic/Longyearbyen")
        scene = eng.build(datetime(2026, 6, 21, 12, 0,
                                   tzinfo=ZoneInfo("Arctic/Longyearbyen")))
        self.assertFalse(scene.events.get("sunrise"))
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1000, 640)
        self.painter.draw(cairo.Context(surf), 1000, 640, scene, 180.0)
        self.assertNotIn("arc", [r[4] for r in self.painter.ui.info_rects])

    def _card_pixels(self, when=DAY, weather=None, **ui):
        """画一遍，把那张离屏卡片的指纹取出来（新建画笔，避免旧缓存）。"""
        painter = SkyPainter(seed=7)
        for name, value in ui.items():
            setattr(painter.ui, name, value)
        scene = self.engine.build(when, weather, location_label="西安 · 陕西省")
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1000, 640)
        painter.draw(cairo.Context(surf), 1000, 640, scene, 180.0)
        card = painter._info.surf
        self.assertIsNotNone(card)
        return hashlib.sha256(bytes(card.get_data())).hexdigest()[:16]

    def test_a_second_identical_frame_reuses_the_card(self):
        first = self._card_pixels(weather=_weather())
        second = self._card_pixels(weather=_weather())
        self.assertEqual(first, second)

    def test_the_card_is_actually_cached(self):
        """第二帧不该再下一遍笔。

        离屏图的尺寸和交给 stale() 的尺寸只要对不上（差一个像素也算），缓存就
        永远命中不了——画面还是对的，只是每帧白画上百笔，谁也看不出来。
        """
        painter = SkyPainter(seed=7)
        scene = self.engine.build(DAY, _weather(), location_label="西安 · 陕西省")
        painted = []
        original = painter._paint_info

        def spy(*args, **kwargs):
            painted.append(1)
            return original(*args, **kwargs)

        painter._paint_info = spy
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1000, 640)
        cr = cairo.Context(surf)
        painter.draw(cr, 1000, 640, scene, 180.0)
        painter.draw(cr, 1000, 640, scene, 180.0)
        self.assertEqual(len(painted), 1,
                         "第二帧又把卡片画了一遍——离屏缓存根本没命中")

    def test_cache_key_notices_every_visible_change(self):
        """缓存 key 漏掉一个参数，卡片就会停在上一分钟 / 上一份天气上。

        下面每一种"用户看得出来"的变化，都必须让那张离屏图重新画一遍。
        """
        base = self._card_pixels(weather=_weather())
        later = self._card_pixels(when=DAY.replace(minute=36), weather=_weather())
        slim = self._card_pixels(weather=_weather(), info_compact=True)
        clear = self._card_pixels(weather=_weather(code=1, cloud=8.0))
        self.assertNotEqual(base, later, "分钟变了，卡片还是老的")
        self.assertNotEqual(base, slim, "精简模式没有重新画")
        self.assertNotEqual(base, clear, "天气变了，卡片还是老的")
        # 鼠标停在哪一行：先画一遍问出那一行的下标，再带着它画第二遍
        painter = SkyPainter(seed=7)
        scene = self.engine.build(DAY, _weather(), location_label="西安 · 陕西省")
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1000, 640)
        painter.draw(cairo.Context(surf), 1000, 640, scene, 180.0)
        row = next(i for i, r in enumerate(painter.ui.info_rects) if r[4] == "open")
        self.assertNotEqual(base, self._card_pixels(weather=_weather(), info_hover=row),
                            "鼠标停在哪一行没画出来")

    def test_cache_key_notices_the_weather_time(self):
        """"天气更新于"那一行是看得见的字，时间一变卡片就得重画。"""
        today = datetime.now(TZ).replace(hour=12, minute=0, second=0, microsecond=0)
        fresh = _weather_from(today.replace(hour=0), fetched_at=_time.time())
        older = _weather_from(today.replace(hour=0), fetched_at=_time.time() - 3600)
        self.assertNotEqual(self._card_pixels(when=today, weather=fresh),
                            self._card_pixels(when=today, weather=older),
                            "天气更新于的时间变了，卡片还停在旧的那一行上")

    def test_the_wallpaper_card_has_no_buttons(self):
        """桌面壁纸上的那张卡没有鼠标：收起 / 刷新 / 每行的小箭头一概不画。

        画出来还会让人以为能点（而且要挪出位置），所以 `info_buttons=False`
        时既不下笔、也不记命中方块。
        """
        self._draw(weather=_weather(), info_buttons=False)
        kinds = [r[4] for r in self.painter.ui.info_rects]
        self.assertEqual(kinds, [], f"壁纸上的卡片还留着能点的方块：{kinds}")
        # 一行行的事实、日弧本身照画（那是内容，不是按钮）
        self.assertGreaterEqual(len(self.painter.ui.info_rows), 5)
        full = self._card_pixels(weather=_weather())
        bare = self._card_pixels(weather=_weather(), info_buttons=False)
        self.assertNotEqual(full, bare, "有按钮和没按钮画出来一模一样？")

    def test_a_hidden_card_leaves_no_hitboxes(self):
        """空格收起卡片之后，原来那些能点的方块必须一起消失。

        以前 `ui.info_rects` 会留着上一帧的清单，于是点卡片原来那块空白处
        照样会跳去预览某个时刻——卡片都不见了，画面却动了。
        """
        self._draw(weather=_weather())
        self.assertTrue(self.painter.ui.info_rects, "卡片画出来却没有命中方块")
        scene = self.engine.build(DAY, _weather(), location_label="西安 · 陕西省")
        self.painter.ui.show_info = False
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1000, 640)
        self.painter.draw(cairo.Context(surf), 1000, 640, scene, 180.0)
        self.assertEqual(self.painter.ui.info_rects, [],
                         "卡片收起来了，命中方块还在")

    def test_a_hidden_ribbon_leaves_no_rect(self):
        """长卷关掉之后，底下那条也不该还能拖（光标变成左右箭头、拖动跳时间）。"""
        weather = _weather()
        self.painter.ui.ribbon = self.engine.ribbon(DAY, weather)
        self._draw(weather=weather)
        self.assertGreater(self.painter.ui.ribbon_rect[2], 0, "长卷没画出来")
        scene = self.engine.build(DAY, _weather(), location_label="西安 · 陕西省")
        self.painter.ui.show_ribbon = False
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1000, 640)
        self.painter.draw(cairo.Context(surf), 1000, 640, scene, 180.0)
        self.assertEqual(self.painter.ui.ribbon_rect, (0.0, 0.0, 0.0, 0.0),
                         "长卷关掉了，命中区还留着")

    def test_the_arc_maps_the_whole_day(self):
        """日弧那条轨道铺的是**一整天**，鼠标映射也得按一整天来。

        以前把轨道宽度当成"日出到日落"来插值：鼠标停在左边四分之一处
        （真实时间 06:00）会被算成"日出前不久"。这里按 0 / 6 / 12 / 18 点
        各点一次，落在轨道上的位置必须是准的。
        """
        from chuang.infocard import InfoCard

        class _Win:                       # 只给 InfoCard 要用的那两个东西
            def __init__(self, painter, scene):
                self.painter = painter
                self._scene = scene

            def _current_scene(self):
                return self._scene

        day = DAY.replace(hour=12, minute=0)
        scene = self.engine.build(day, _weather(), location_label="西安 · 陕西省")
        self.painter.ui.info_compact = False
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1000, 640)
        self.painter.draw(cairo.Context(surf), 1000, 640, scene, 180.0)
        arc = next(r for r in self.painter.ui.info_rects if r[4] == "arc")
        card = InfoCard(_Win(self.painter, scene))
        rx, rw = arc[0], arc[2]
        for frac, want in ((0.0, "00:00"), (0.25, "06:00"),
                           (0.5, "12:00"), (0.75, "18:00")):
            got = card.arc_time(rx + rw * frac)
            self.assertIsNotNone(got)
            self.assertEqual(got.strftime("%H:%M"), want,
                             f"轨道 {frac:.0%} 处该是 {want}，算出来是 {got}")


@unittest.skipUnless(HAS_STACK, "没有 pycairo / PyGObject，跳过")
class TestCardFootLine(unittest.TestCase):
    """卡片底下那一行：跟着真实天气时要写清楚"天气更新于几点"。

    以前这一行只有一句固定文案（"天气 · Open-Meteo"），看的人根本没法判断
    窗上这份天气是刚刚问回来的，还是上个星期缓存下来的。
    """

    def setUp(self):
        self.painter = SkyPainter(seed=7)
        self.engine = _engine()
        self.tz = ZoneInfo("Asia/Shanghai")
        self.surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1200, 800)
        self.cr = cairo.Context(self.surf)

    def _scene(self, when, weather):
        return self.engine.build(when, weather, location_label="西安 · 陕西省")

    def test_while_following_the_real_weather_it_shows_the_time(self):
        now = datetime.now(self.tz).replace(microsecond=0)
        scene = self._scene(now, _weather_from(now.replace(hour=0)))
        self.assertTrue(scene.weather_now)
        stamp = self.painter._weather_stamp(scene)
        self.assertRegex(stamp, r"^\d{2}:\d{2}$")
        near = {now.strftime("%H:%M"),
                (now + timedelta(minutes=1)).strftime("%H:%M")}
        self.assertIn(stamp, near)
        foot = self.painter._foot_text(scene, self.cr, 320.0, 10.0)
        self.assertIn("更新于", foot)
        self.assertIn(stamp, foot)

    def test_a_narrow_card_says_less_instead_of_running_under_the_button(self):
        now = datetime.now(self.tz).replace(microsecond=0)
        scene = self._scene(now, _weather_from(now.replace(hour=0)))
        wide = self.painter._foot_text(scene, self.cr, 340.0, 10.0)
        narrow = self.painter._foot_text(scene, self.cr, 120.0, 10.0)
        self.assertGreater(len(wide), len(narrow))
        width = __import__("chuang.render", fromlist=["draw_text"]).draw_text(
            self.cr, narrow, 0, -1000, 10.0, (255, 255, 255), 0.0)[0]
        self.assertLessEqual(width, 120.0, "连最短的那句都放不下")

    def test_a_stale_fetch_from_another_day_carries_the_date(self):
        now = datetime.now(self.tz).replace(microsecond=0)
        old = _time.time() - 86400 * 2
        scene = self._scene(now, _weather_from(now.replace(hour=0), fetched_at=old))
        stamp = self.painter._weather_stamp(scene)
        self.assertRegex(stamp, r"^\d{2}-\d{2} \d{2}:\d{2}$")
        self.assertIn(stamp, self.painter._foot_text(scene, self.cr, 400.0, 10.0))

    def test_offline_looks_different_from_fresh(self):
        now = datetime.now(self.tz).replace(microsecond=0)
        weather = _weather_from(now.replace(hour=0))
        weather.stale = True
        scene = self._scene(now, weather)
        self.assertTrue(scene.weather_stale)
        self.assertIn("上次", self.painter._foot_text(scene, self.cr, 400.0, 10.0))


@unittest.skipUnless(HAS_STACK, "没有 pycairo / PyGObject，跳过")
class TestIcons(unittest.TestCase):

    KINDS = ("sun", "sunrise", "sunset", "moon", "cloud", "rain", "snow",
             "fog", "wind", "refresh", "info", "check", "warn",
             "chevron-up", "chevron-down", "chevron-right", "不认识的图标")

    def _lit(self, kind, phase=0.5, size=60.0, box=200):
        """画一枚图标，返回 (亮起来的像素数, 最远越界了多少像素)。"""
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, box, box)
        cr = cairo.Context(surf)
        cr.set_source_rgb(0, 0, 0)
        cr.paint()
        draw_icon(cr, kind, box / 2, box / 2, size, (255, 255, 255), 1.0, phase=phase)
        surf.flush()
        buf = bytes(surf.get_data())
        stride = surf.get_stride()
        lo = (box - size) / 2 - 1.0            # 允许 1px 的抗锯齿
        hi = (box + size) / 2 + 1.0
        count, outside = 0, 0.0
        for y in range(box):
            for x in range(box):
                off = y * stride + x * 4
                if buf[off] or buf[off + 1] or buf[off + 2]:
                    count += 1
                    outside = max(outside, lo - x, x - hi, lo - y, y - hi)
        return count, outside

    def test_icons_stay_inside_their_box(self):
        """曾经有一笔路径漏出边界，整幅画面上就多出一条对角线。"""
        for kind in self.KINDS:
            count, outside = self._lit(kind)
            self.assertLessEqual(outside, 0.0, f"{kind} 画到自己的框外面去了")
            self.assertGreater(count, 20, f"{kind} 几乎没画出来")

    def test_moon_icon_follows_the_phase(self):
        """亮面该随着月相变多——新月几乎不亮，满月整个圆都是亮的。"""
        new, _ = self._lit("moon", phase=0.0)
        half, _ = self._lit("moon", phase=0.25)
        full, _ = self._lit("moon", phase=0.5)
        self.assertLess(new, half)
        self.assertLess(half, full)

    def test_unknown_kind_does_not_explode(self):
        count, _ = self._lit("不认识的图标")
        self.assertGreater(count, 5)


if __name__ == "__main__":
    unittest.main()
