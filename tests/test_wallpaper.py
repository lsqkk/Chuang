"""壁纸的"槽位"逻辑与帧目录轮换（纯字符串/文件，不碰真的桌面）。

这里一行都不会去写用户的 dconf：`gsettings` 被替换成一个内存里的字典，
所以测试跑完桌面还是原样。
"""

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from chuang import wallpaper as W

try:
    from gi.repository import GLib as _GLib
    import cairo as _cairo                      # noqa: F401
    HAS_STACK = True
except Exception:                               # noqa: BLE001 - 缺库就跳过
    HAS_STACK = False


class FakeGsettings:
    """假 gsettings：够 current_uris / shown_key / current_options 用。"""

    def __init__(self, light="", dark="", scheme="default", options="zoom"):
        self.values = {
            ("org.gnome.desktop.background", "picture-uri"): light,
            ("org.gnome.desktop.background", "picture-uri-dark"): dark,
            ("org.gnome.desktop.background", "picture-options"): options,
            ("org.gnome.desktop.interface", "color-scheme"): scheme,
        }

    def __call__(self, *args):
        if not args or args[0] != "get":
            return 0, ""
        return 0, self.values.get((args[1], args[2]), "")


def _uri(path: Path) -> str:
    from urllib.parse import quote
    return "file://" + quote(str(path))


class TestUris(unittest.TestCase):

    def test_is_our_uri(self):
        self.assertTrue(W.is_our_uri(_uri(W.SLOTS[0])))
        self.assertTrue(W.is_our_uri(_uri(W.SLOTS[1])))
        self.assertTrue(W.is_our_uri(_uri(W.DAY_XML)))
        self.assertFalse(W.is_our_uri(_uri(Path("/usr/share/backgrounds/x.png"))))
        self.assertFalse(W.is_our_uri(""))
        self.assertFalse(W.is_our_uri("https://example.com/a.png"))

    def test_slot_uri_is_file_uri(self):
        self.assertTrue(W.slot_uri(0).startswith("file://"))
        self.assertIn("sky-a.png", W.slot_uri(0))
        self.assertIn("sky-b.png", W.slot_uri(1))


class TestSlots(unittest.TestCase):

    def test_shown_slot(self):
        cases = ((0, W.slot_uri(0)), (1, W.slot_uri(1)),
                 (-1, _uri(Path("/home/u/picture.png"))), (-1, ""))
        for want, uri in cases:
            with self.subTest(uri=uri):
                with mock.patch.object(W, "_gsettings", FakeGsettings(light=uri)):
                    self.assertEqual(W.shown_slot(), want)

    def test_shown_slot_follows_dark_mode(self):
        """深色模式下真正生效的是 picture-uri-dark，别读错那个键。"""
        dark_uri, light_uri = W.slot_uri(1), W.slot_uri(0)
        fake = FakeGsettings(light=light_uri, dark=dark_uri, scheme="prefer-dark")
        with mock.patch.object(W, "_gsettings", fake):
            self.assertEqual(W.shown_slot(), 1)
        fake = FakeGsettings(light=light_uri, dark=dark_uri, scheme="default")
        with mock.patch.object(W, "_gsettings", fake):
            self.assertEqual(W.shown_slot(), 0)

    def test_stale_shown_slot(self):
        slots = (Path("/tmp/chuang-test-a.png"), Path("/tmp/chuang-test-b.png"))
        with mock.patch.object(W, "SLOTS", slots), \
                mock.patch.object(W, "_gsettings", FakeGsettings(
                    light="file://" + str(slots[0]))), \
                mock.patch.object(W, "slot_mtimes", lambda: (100.0, 130.0)):
            # 桌面挂着 a（旧），b 新得多 → 应该把 b 顶上去
            self.assertEqual(W.stale_shown_slot(), 1)
        with mock.patch.object(W, "SLOTS", slots), \
                mock.patch.object(W, "_gsettings", FakeGsettings(
                    light="file://" + str(slots[0]))), \
                mock.patch.object(W, "slot_mtimes", lambda: (100.0, 101.0)):
            # 只差一秒：别乱换
            self.assertIsNone(W.stale_shown_slot())
        with mock.patch.object(W, "SLOTS", slots), \
                mock.patch.object(W, "_gsettings", FakeGsettings(
                    light="file:///home/u/picture.png")), \
                mock.patch.object(W, "slot_mtimes", lambda: (0.0, 999.0)):
            # 桌面挂着别人的图：不关我们的事
            self.assertIsNone(W.stale_shown_slot())


class TestOptions(unittest.TestCase):

    def test_current_options(self):
        with mock.patch.object(W, "_gsettings", FakeGsettings(options="scaled")):
            self.assertEqual(W.current_options(), "scaled")
        with mock.patch.object(W, "_gsettings", lambda *a: (1, "")):
            self.assertEqual(W.current_options(), "")

    def test_restore_puts_options_back(self):
        """「还原成原来的壁纸」必须连缩放方式一起还——接管时我们改过它。"""
        calls = []

        def fake(*args):
            calls.append(args)
            return 0, ""

        with mock.patch.object(W, "apply_uri", lambda uri: (True, "已设为桌面壁纸")), \
                mock.patch.object(W, "_gsettings", fake):
            ok, _msg = W.restore("file:///home/u/old.png", "file:///home/u/old-dark.png",
                                 "scaled")
        self.assertTrue(ok)
        self.assertIn(("set", "org.gnome.desktop.background", "picture-options", "scaled"),
                      calls)
        self.assertIn(("set", "org.gnome.desktop.background", "picture-uri-dark",
                       "file:///home/u/old-dark.png"), calls)

    def test_restore_without_record(self):
        ok, msg = W.restore("", "")
        self.assertFalse(ok)
        self.assertIn("没有记录", msg)


class TestFrameDirs(unittest.TestCase):
    """动态壁纸：换目录而不是先删后写，中途失败也不会留下空桌面。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.dirs = (root / "frames-a", root / "frames-b")
        self.legacy = root / "frames"
        self.xml = root / "sky-day.xml"
        patcher = mock.patch.multiple(
            W, CACHE=root, FRAME_DIRS=self.dirs, LEGACY_FRAME_DIR=self.legacy,
            DAY_XML=self.xml)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_active_frame_dir(self):
        self.assertIsNone(W.active_frame_dir())
        self.xml.write_text(f"<file>{self.dirs[1]}/frame-00.png</file>", encoding="utf-8")
        self.assertEqual(W.active_frame_dir(), self.dirs[1])
        self.xml.write_text(f"<file>{self.legacy}/frame-00.png</file>", encoding="utf-8")
        self.assertEqual(W.active_frame_dir(), self.legacy)

    def test_clear_dir_only_touches_our_cache(self):
        self.dirs[0].mkdir()
        (self.dirs[0] / "frame-00.png").write_bytes(b"x")
        W._clear_dir(self.dirs[0])
        self.assertTrue(self.dirs[0].is_dir())
        self.assertEqual(list(self.dirs[0].iterdir()), [])
        outsider = Path(self.tmp.name).parent / "not-ours"
        W._clear_dir(outsider)              # 父目录不对：必须一动不动
        self.assertFalse(outsider.exists())

    def test_prune_keeps_only_the_new_one(self):
        for d in (self.dirs[0], self.dirs[1], self.legacy):
            d.mkdir()
            (d / "frame-00.png").write_bytes(b"x")
        W._prune_frame_dirs(keep=self.dirs[1])
        self.assertTrue(self.dirs[1].is_dir())
        self.assertFalse(self.dirs[0].exists())
        self.assertFalse(self.legacy.exists())


class TestScreenSize(unittest.TestCase):

    def test_returns_sane_size(self):
        w, h = W.screen_size()             # 没有 Gdk 时应该退回 1920×1080
        self.assertGreaterEqual(w, 320)
        self.assertGreaterEqual(h, 240)

    def test_bottom_inset_is_sane(self):
        """屏幕底下被面板 / dock 占掉的高度：读不到就当 0，绝不能给个荒唐数。"""
        inset = W.screen_bottom_inset()
        self.assertIsInstance(inset, float)
        self.assertGreaterEqual(inset, 0.0)
        self.assertLess(inset, 400.0)


@unittest.skipUnless(HAS_STACK, "没有 pycairo / PyGObject，跳过")
class TestRibbonClearsTheDock(unittest.TestCase):
    """桌面壁纸上的长卷要给底部的 dock 让路。

    壁纸是铺满整屏的，而常驻的底部 dock 压在屏幕最下面（本机 64px）。长卷
    底下还有一行整点标签，以前正好钻到 dock 底下——用户看到的就是"下面还是
    有点挡着"。`ui.bottom_inset` 就是这时候让开的。
    """

    W, H, INSET = 1920, 1080, 64.0

    def _ribbon_rect(self, inset: float):
        import cairo
        from chuang.render import SkyPainter
        from chuang.scene import SkyEngine
        from chuang.weather import HourPoint, Weather

        tz = ZoneInfo("Asia/Shanghai")
        when = datetime(2026, 9, 24, 18, 40, tzinfo=tz)
        base = when.replace(tzinfo=None, hour=0, minute=0)
        weather = Weather(ok=True, code=63, cloud=90.0, temp=18.0, precip=2.4)
        for i in range(48):
            weather.hourly.append(HourPoint(base + timedelta(hours=i), 90.0, 63,
                                            18.0, 60.0, precip_mm=2.4))
        weather.invalidate()
        engine = SkyEngine()
        engine.set_location(34.3416, 108.9398, "Asia/Shanghai")
        scene = engine.build(when, weather, location_label="西安")
        painter = SkyPainter(seed=3)
        painter.ui.info_buttons = False
        painter.ui.bottom_inset = inset
        painter.ui.ribbon = engine.ribbon(base, weather)
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, self.W, self.H)
        painter.draw(cairo.Context(surf), self.W, self.H, scene, 180.0)
        return painter.ui.ribbon_rect

    def test_the_long_ribbon_moves_up_when_a_dock_is_there(self):
        plain = self._ribbon_rect(0.0)
        docked = self._ribbon_rect(self.INSET)
        self.assertLess(docked[1], plain[1], "让开 dock 之后长卷反而更低了")
        # 长卷下沿 + 整点标签那一行，都得在面板之上
        bottom = docked[1] + docked[3]
        self.assertLessEqual(bottom + 12.0, self.H - self.INSET,
                             "长卷压进底部面板里了")


class TestInfoMode(unittest.TestCase):
    """壁纸上那张「此刻的事实」的版式：跟随窗口 / 精简一条 / 完整版。"""

    def test_three_modes_resolve_as_documented(self):
        from chuang.wallpaper_ctl import info_compact_for
        cases = (("follow", True, True), ("follow", False, False),
                 ("slim", True, True), ("slim", False, True),
                 ("full", True, False), ("full", False, False),
                 ("认不出来的值", True, True))       # 兜底按"跟随"
        for mode, window_compact, want in cases:
            with self.subTest(mode=mode, window=window_compact):
                self.assertEqual(info_compact_for(mode, window_compact), want)


@unittest.skipUnless(HAS_STACK, "没有 pycairo / PyGObject，跳过")
class TestWallpaperCardStyle(unittest.TestCase):
    """整条链路走一遍：给了 compact，画出来的就是精简版那张卡片。"""

    def _render(self, tmp: Path, compact: bool, scene_opts: dict | None = None):
        from chuang.weather import HourPoint, Weather
        tz = ZoneInfo("Asia/Shanghai")
        now = datetime.now(tz)
        base = now.replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
        weather = Weather(ok=True, fetched_at=now.timestamp(), code=63, cloud=95.0,
                          temp=18.0, apparent=17.0, humidity=80.0, precip=2.0)
        for i in range(48):
            weather.hourly.append(HourPoint(base + timedelta(hours=i), 95.0, 63,
                                            18.0, 60.0))
        weather.invalidate()
        slots = (tmp / "sky-a.png", tmp / "sky-b.png")
        worker = W.Worker(34.3416, 108.9398, "Asia/Shanghai", "西安 · 陕西省", seed=3)
        out = []
        with mock.patch.object(W, "SLOTS", slots), \
                mock.patch.object(W, "CACHE", tmp):
            # adopt=False：就地重写槽位文件，**不碰** gsettings（测试不写用户桌面）
            worker.render_now(now, weather, True, False, (720, 460), 0,
                              lambda *a: out.append(a), compact=compact, inset=52.0,
                              scene_opts=scene_opts)
            self._pump(lambda: bool(out))
        self.assertTrue(out, "壁纸渲染没有回调（后台线程没跑完？）")
        self.assertTrue(slots[0].exists(), "槽位文件没写出来")
        ui = worker.painter.ui
        return (slots[0].read_bytes(), bool(ui.info_compact), bool(ui.info_buttons),
                float(ui.bottom_inset), ui)

    def test_the_scene_switches_reach_the_wallpaper(self):
        """菜单 → 场景那几个开关也要管到桌面上的那张图。

        壁纸有自己的画笔（后台线程里画），不同步就会出现"窗口里已经把行人收起来了，
        桌面上他们还在走"。
        """
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            base = self._render(tmp, compact=False)
            off = self._render(tmp, compact=False,
                               scene_opts={"show_people": False,
                                           "show_traffic": False,
                                           "show_skyline": False})
        self.assertTrue(off[4].show_people is False)
        self.assertTrue(off[4].show_traffic is False)
        self.assertFalse(off[4].show_skyline)
        self.assertNotEqual(base[0], off[0],
                            "场景开关没有传到壁纸那份画笔上（画出来一模一样）")

    @staticmethod
    def _pump(predicate, timeout: float = 10.0) -> bool:
        """转主循环，等后台那条渲染线程把活干完（它会 idle_add 回来）。"""
        loop = _GLib.MainLoop()

        def tick():
            if predicate():
                loop.quit()
                return False
            return True

        _GLib.timeout_add(20, tick)
        _GLib.timeout_add(int(timeout * 1000), lambda: (loop.quit(), False)[1])
        loop.run()
        return bool(predicate())

    def test_the_card_style_reaches_the_renderer(self):
        import hashlib
        with tempfile.TemporaryDirectory() as d:
            full, full_compact, buttons, inset, _ui = self._render(
                Path(d), compact=False)
            slim, slim_compact, _b, _i, _ui2 = self._render(Path(d), compact=True)
        self.assertFalse(full_compact)
        self.assertTrue(slim_compact)
        # 桌面上的那张卡不该画"能点的东西"：那儿没有鼠标
        self.assertFalse(buttons, "壁纸上的信息卡还画着收起 / 刷新按钮")
        # 底部余量也真的传到了画笔上（长卷靠它让开 dock）
        self.assertEqual(inset, 52.0)
        self.assertNotEqual(hashlib.sha256(full).hexdigest(),
                            hashlib.sha256(slim).hexdigest(),
                            "精简与完整画出来的壁纸一模一样")


if __name__ == "__main__":
    unittest.main()
