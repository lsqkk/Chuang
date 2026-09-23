"""诊断文本：把事实排成文字这件事，现在可以在没有显示器的情况下测。"""

import time
import unittest

from chuang import diagnostics as diag
from chuang.config import Config


def _facts(**kw) -> diag.Facts:
    base = dict(version="1.1.8", pid=4242, instance_held=True,
                other_processes=[], installed_deb="1.1.7",
                tray_available=True, config=Config(),
                now=1_000_000.0, shown_uri="file:///home/u/.cache/chuang/sky-a.png",
                shown_slot=0, slot_mtimes=(999_995.0, 999_970.0),
                slot_names=("sky-a.png", "sky-b.png"), stale_slot=None,
                last_wallpaper_at=999_990.0, last_wallpaper_ok=True,
                last_wallpaper_msg="桌面壁纸已更新", last_flip_at=999_700.0,
                flip_every=300.0, tick_errors=0, last_tick_error="")
    base.update(kw)
    return diag.Facts(**base)


class TestEnvironmentReport(unittest.TestCase):

    def test_shape(self):
        text = diag.environment_report(_facts())
        self.assertIn("- 版本：1.1.8", text)
        self.assertIn("托盘：可用", text)
        self.assertIn("壁纸跟随：关", text)          # Config 默认关闭
        self.assertTrue(text.startswith("- 版本"))
        self.assertNotIn("None", text)

    def test_wallpaper_auto_is_reported(self):
        cfg = Config(wallpaper_auto=True)
        self.assertIn("壁纸跟随：开", diag.environment_report(_facts(config=cfg)))


class TestWallpaperReport(unittest.TestCase):

    def test_healthy_state(self):
        text = diag.wallpaper_report(_facts())
        self.assertIn("单实例锁：持有", text)
        self.assertIn("桌面此刻挂的是：sky-a.png（5.0 秒前写的）", text)
        self.assertIn("另一张 sky-b.png：30.0 秒前写的", text)
        self.assertIn("桌面显示的是不是最新那张：是", text)
        self.assertIn("最近一次换图：10 秒前 → 成功", text)
        self.assertIn("心跳：本进程共兜住 0 次异常", text)

    def test_stale_slot_is_called_out(self):
        text = diag.wallpaper_report(_facts(stale_slot=1))
        self.assertIn("**不是**（应该顶上 sky-b.png）", text)

    def test_foreign_wallpaper(self):
        text = diag.wallpaper_report(_facts(shown_slot=-1,
                                            shown_uri="file:///home/u/图.jpg"))
        self.assertIn("桌面此刻挂的不是「窗」画的图：file:///home/u/图.jpg", text)

    def test_never_applied_yet(self):
        text = diag.wallpaper_report(_facts(last_wallpaper_at=0.0,
                                            last_wallpaper_ok=None))
        self.assertIn("最近一次换图：本次启动以来还没有过", text)

    def test_failure_and_tick_errors_are_reported(self):
        text = diag.wallpaper_report(_facts(last_wallpaper_ok=False,
                                            last_wallpaper_msg="这个桌面环境没接受这次换图",
                                            tick_errors=3,
                                            last_tick_error="UnboundLocalError: slot"))
        self.assertIn("→ 失败（这个桌面环境没接受这次换图）", text)
        self.assertIn("共兜住 3 次异常；最近一次：UnboundLocalError: slot", text)

    def test_uses_now_when_missing(self):
        text = diag.wallpaper_report(_facts(now=0.0))
        self.assertIn(time.strftime("%Y"), text.splitlines()[1])   # 时间戳是今年的


class TestOtherProcesses(unittest.TestCase):

    def test_does_not_count_itself(self):
        for pid, cmd in diag.other_chuang_processes():
            self.assertNotEqual(pid, __import__("os").getpid())
            self.assertTrue(cmd)


if __name__ == "__main__":
    unittest.main()
