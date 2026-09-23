"""壁纸的"槽位"逻辑与帧目录轮换（纯字符串/文件，不碰真的桌面）。

这里一行都不会去写用户的 dconf：`gsettings` 被替换成一个内存里的字典，
所以测试跑完桌面还是原样。
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from chuang import wallpaper as W


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


if __name__ == "__main__":
    unittest.main()
