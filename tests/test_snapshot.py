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


def _stack_status() -> tuple[bool, str]:
    """返回 (能不能测, 不能测的原因)。

    区分两种情况，别混成一句"跳过"：

    * 机器上**根本没有** GTK/pycairo → 跳过（开发机可能就没装）；
    * 有 gi 有 cairo、却少了 `python3-gi-cairo` 那块胶水 → **失败**。这时
      `PangoCairo.create_layout()` 会抛 `KeyError: 'could not find foreign type
      Context'`，所有离屏出图都会挂。2026-09-23 的 CI 就是漏了这个包：
      测试红了 → 打包与发版被跳过 → Release 里什么都没有（见 CHANGELOG 1.1.8）。
    """
    if importlib.util.find_spec("cairo") is None:
        return False, "没装 pycairo"
    if importlib.util.find_spec("gi") is None:
        return False, "没装 PyGObject"
    try:
        import cairo
        import gi
        gi.require_version("Pango", "1.0")
        gi.require_version("PangoCairo", "1.0")
        from gi.repository import PangoCairo
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 8, 8)
        PangoCairo.create_layout(cairo.Context(surface))
    except Exception as exc:                      # 有 GTK 却画不了：要人管
        return True, (f"有 gi/cairo 但离屏出图用不了（多半缺 python3-gi-cairo）："
                      f"{type(exc).__name__}: {exc}")
    return True, ""


HAS_STACK, STACK_PROBLEM = _stack_status()


@unittest.skipUnless(HAS_STACK, STACK_PROBLEM)
class TestSnapshot(unittest.TestCase):

    def test_cairo_glue_is_present(self):
        """有 gi 有 cairo，就得能真的画出来。"""
        self.assertEqual(STACK_PROBLEM, "", STACK_PROBLEM)

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
            keys.append(painter._city.key)
        self.assertNotEqual(keys[0], keys[1], "换了城市却没重画天际线")
        self.assertEqual(keys[0], keys[2], "同一座城市不该反复重画")


if __name__ == "__main__":
    unittest.main()
