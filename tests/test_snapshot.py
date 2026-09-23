"""冒烟测试：整条绘制链路（离屏）能不能出一张像样的图。

跑的是 `tools/snapshot.py`，和手工验收用的是同一条命令——它不需要显示器，
只要有 cairo + Pango。环境里没有这些时自动跳过（而不是报失败）。
"""

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "snapshot.py"


def _has_drawing_stack() -> bool:
    if importlib.util.find_spec("cairo") is None:
        return False
    try:
        import gi
        gi.require_version("Pango", "1.0")
        gi.require_version("PangoCairo", "1.0")
        importlib.import_module("gi.repository.PangoCairo")   # 能导入才算数
    except Exception:
        return False
    return True


@unittest.skipUnless(_has_drawing_stack(), "没有 cairo / gi，跳过绘制冒烟测试")
class TestSnapshot(unittest.TestCase):

    def _render(self, args, name):
        out = Path(self.tmp.name) / name
        r = subprocess.run([sys.executable, str(TOOL), str(out)] + args,
                           capture_output=True, text=True, timeout=180, cwd=ROOT)
        self.assertEqual(r.returncode, 0, f"渲染失败：{r.stdout}\n{r.stderr}")
        self.assertTrue(out.exists(), "没有写出 PNG")
        self.assertGreater(out.stat().st_size, 20000, "PNG 太小了，画面大概是空的")
        return out

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_clear_evening(self):
        self._render(["18:35", "34.34", "108.94"], "evening.png")

    def test_rainy_night_with_weather(self):
        out = self._render(["21:30", "34.34", "108.94", "63", "95", "18", "200"],
                           "rain.png")
        self.assertGreater(out.stat().st_size, 20000)

    def test_skyline_cache_keeps_cities_apart(self):
        """换城市必须换天际线：缓存键里漏掉城市种子的话，第二座城还是第一座的楼。

        这里故意**不调用** invalidate_location()：光没变、只是城市名变了，
        缓存键本身就得让画面重画——这是 1.1.8 修的那条路。
        """
        import cairo
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from chuang.render import SkyPainter
        from chuang.scene import SkyEngine

        when = datetime(2026, 9, 23, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        eng = SkyEngine()
        eng.set_location(34.34, 108.94, "Asia/Shanghai")
        painter = SkyPainter(seed=7)
        keys = []
        for name in ("西安", "咸阳", "西安"):
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 600, 400)
            scene = eng.build(when, None, location_label=name)
            painter.draw(cairo.Context(surf), 600, 400, scene, 180.0)
            keys.append(painter._city_key)
        self.assertNotEqual(keys[0], keys[1], "换了城市却没重画天际线")
        self.assertEqual(keys[0], keys[2], "同一座城市不该反复重画")


if __name__ == "__main__":
    unittest.main()
