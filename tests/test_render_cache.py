"""离屏缓存的那把小工具（CacheSlot）。

它存在的理由就是"别再手工拼 key"——这里把它的三条语义钉住：
没画过 / key 变了 / 尺寸变了，都该重画。
"""

import unittest

import cairo

from chuang.render import CacheSlot, SkyPainter


def _surface(w=40, h=30):
    return cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)


class TestCacheSlot(unittest.TestCase):

    def test_empty_slot_is_stale(self):
        self.assertTrue(CacheSlot().stale(("a",), 40, 30))

    def test_reuses_same_key_and_size(self):
        slot = CacheSlot()
        slot.store(("a",), _surface())
        self.assertFalse(slot.stale(("a",), 40, 30))

    def test_new_key_or_size_is_stale(self):
        slot = CacheSlot()
        slot.store(("a",), _surface())
        self.assertTrue(slot.stale(("b",), 40, 30))      # key 变了
        self.assertTrue(slot.stale(("a",), 41, 30))      # 宽变了
        self.assertTrue(slot.stale(("a",), 40, 31))      # 高变了
        self.assertFalse(slot.stale(("a",), 40.4, 30.4))  # 四舍五入后还是同一张

    def test_invalidate(self):
        slot = CacheSlot()
        slot.store(("a",), _surface())
        slot.invalidate()
        self.assertIsNone(slot.surf)
        self.assertTrue(slot.stale(("a",), 40, 30))

    def test_store_returns_the_surface(self):
        slot = CacheSlot()
        surf = _surface()
        self.assertIs(slot.store(("a",), surf), surf)
        self.assertIs(slot.surf, surf)


class TestPainterSlots(unittest.TestCase):

    def test_invalidate_location_clears_every_slot(self):
        painter = SkyPainter(seed=3)
        for slot in (painter._sky, painter._cloud, painter._city,
                     painter._sill, painter._overlay):
            slot.store(("x",), _surface())
        painter._skyline[123] = "楼群数据"
        painter.invalidate_location()
        for slot in (painter._sky, painter._cloud, painter._city,
                     painter._sill, painter._overlay):
            self.assertIsNone(slot.surf)
        self.assertEqual(painter._skyline, {})


if __name__ == "__main__":
    unittest.main()
