"""「把这扇窗存成图片」：文件名、绝不覆盖、真的写出一张像样的 PNG。

这一摊是纯函数（`chuang/export.py`），所以不用开窗口就能整条验一遍：给一个
时刻算文件名、画一帧、写盘、再把 PNG 读回来看尺寸。没有 pycairo / PyGObject
的机器上只跑前半截（那几件与画面无关的事）。
"""

import importlib.util
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from chuang import export

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
    from chuang.render import SkyPainter
    from chuang.scene import SkyEngine
    HAS_STACK = True
except Exception:                       # noqa: BLE001 - 缺库就跳过，不是失败
    HAS_STACK = False

TZ = ZoneInfo("Asia/Shanghai")
DAY = datetime(2026, 9, 25, 18, 35, tzinfo=TZ)


class TestPictureName(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_the_name_carries_the_moment(self):
        path = export.default_path(DAY, self.dir)
        self.assertEqual(path.name, "chuang-2026-09-25-1835.png")
        self.assertEqual(path.parent, self.dir)

    def test_an_existing_picture_is_never_overwritten(self):
        """"上一张"不该被悄悄顶掉：同名的往后顺延。"""
        first = export.default_path(DAY, self.dir)
        first.write_bytes(b"x")
        second = export.default_path(DAY, self.dir)
        second.write_bytes(b"x")
        third = export.default_path(DAY, self.dir)
        self.assertEqual([first.name, second.name, third.name],
                         ["chuang-2026-09-25-1835.png",
                          "chuang-2026-09-25-1835-2.png",
                          "chuang-2026-09-25-1835-3.png"])

    def test_pictures_dir_follows_xdg(self):
        with mock.patch.dict(os.environ, {"XDG_PICTURES_DIR": "/tmp/某个相册"}):
            self.assertEqual(export.pictures_dir(), Path("/tmp/某个相册"))
        env = dict(os.environ)
        env.pop("XDG_PICTURES_DIR", None)
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(export.pictures_dir(), Path.home() / "Pictures")


@unittest.skipUnless(HAS_STACK, "没有 pycairo / PyGObject，跳过")
class TestPicturePixels(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.engine = SkyEngine()
        self.engine.set_location(34.3416, 108.9398, "Asia/Shanghai")
        self.scene = self.engine.build(DAY, None, location_label="西安 · 陕西省")

    def test_it_writes_a_png_of_the_size_we_asked_for(self):
        painter = SkyPainter(seed=11)
        painter.ui.ribbon = self.engine.ribbon(DAY.replace(hour=0), None)
        path = export.save_png(painter, self.scene, 900, 600,
                               self.dir / "窗.png")
        self.assertTrue(path.exists(), "没写出图片")
        self.assertGreater(path.stat().st_size, 20000, "PNG 太小了，画面大概是空的")
        back = cairo.ImageSurface.create_from_png(str(path))
        self.assertEqual((back.get_width(), back.get_height()), (900, 600))

    def test_the_parent_directory_is_created(self):
        painter = SkyPainter(seed=12)
        path = export.save_png(painter, self.scene, 600, 420,
                               self.dir / "新目录" / "a.png")
        self.assertTrue(path.exists(), "父目录没被建出来")

    def test_it_keeps_the_window_facing(self):
        """朝北的窗（南半球）与朝南的窗画出来不一样——朝向只有一处定义。"""
        from chuang.scene import facing_azimuth
        self.assertEqual(facing_azimuth(34.3), 180.0)
        self.assertEqual(facing_azimuth(-33.9), 0.0)


if __name__ == "__main__":
    unittest.main()
