"""帧率：上限怎么算、拿不到焦点时降多少、帧时钟停摆时怎么兜底。

这组不需要显示器、也不需要真的建窗口——FrameDriver 只跟"窗口"要四件东西：
config.frame_rate、is_active()、get_visible()、area.queue_draw()。
帧率是 1.1.9 新加的开关，算错了就是"画面顿"或者"风扇转"，所以这里钉死。
"""

import unittest

from chuang.frames import IDLE_FPS, FrameDriver


class _FakeArea:
    def __init__(self):
        self.draws = 0

    def queue_draw(self):
        self.draws += 1


class _FakeConfig:
    """只记账，不写盘——真 Config.save() 会写 ~/.config/chuang/config.json。"""

    def __init__(self, frame_rate):
        self.frame_rate = frame_rate
        self.saved = 0

    def save(self):
        self.saved += 1


class _FakeWindow:
    def __init__(self, frame_rate=60, active=True, visible=True):
        self.config = _FakeConfig(frame_rate)
        self.area = _FakeArea()
        self.toasts = []
        self._active = active
        self._visible = visible

    def is_active(self):
        return self._active

    def get_visible(self):
        return self._visible

    def toast(self, text, seconds=3.0, icon="info"):
        self.toasts.append(text)


class TestFrameDriver(unittest.TestCase):

    def test_interval_follows_the_config(self):
        for fps in (24, 30, 45, 60, 120):
            driver = FrameDriver(_FakeWindow(fps))
            self.assertAlmostEqual(driver.interval(), 1.0 / fps, places=4)

    def test_broken_frame_rate_falls_back_to_60(self):
        win = _FakeWindow(60)
        for broken in (0, None, "乱写的", -5):
            win.config.frame_rate = broken        # 配置是纯文本，谁都能改坏它
            self.assertAlmostEqual(FrameDriver(win).interval(), 1.0 / 60, places=4)

    def test_unfocused_window_is_capped_but_never_raised(self):
        idle = FrameDriver(_FakeWindow(120, active=False))
        self.assertAlmostEqual(idle.interval(), 1.0 / IDLE_FPS, places=4)
        slow = FrameDriver(_FakeWindow(24, active=False))   # 用户自己调得比空闲档还低
        self.assertAlmostEqual(slow.interval(), 1.0 / 24, places=4)

    def test_watchdog_only_fires_when_visible_and_stalled(self):
        win = _FakeWindow(60, visible=True)
        driver = FrameDriver(win)
        driver.watchdog(100.0)                    # 一帧都还没画过：补一次
        self.assertEqual(win.area.draws, 1)
        driver.watchdog(100.1)                    # 刚补过，不重复
        self.assertEqual(win.area.draws, 1)
        driver.watchdog(103.0)                    # 又停了一秒以上：再补
        self.assertEqual(win.area.draws, 2)
        hidden_win = _FakeWindow(60, visible=False)
        FrameDriver(hidden_win).watchdog(100.0)   # 收进托盘了，一笔都不该画
        self.assertEqual(hidden_win.area.draws, 0)

    def test_act_rate_writes_config_and_says_so(self):
        win = _FakeWindow(60)
        driver = FrameDriver(win)
        driver.act_rate("120")
        self.assertEqual(win.config.frame_rate, 120)
        self.assertEqual(win.config.saved, 1)
        self.assertIn("120", win.toasts[-1])
        self.assertIn(str(IDLE_FPS), win.toasts[-1])   # 失焦降到多少，得说出来
        driver.act_rate("乱写的")                       # 坏值：一个字节都不动
        self.assertEqual(win.config.frame_rate, 120)
        self.assertEqual(win.config.saved, 1)

    def test_act_rate_resets_the_clock(self):
        driver = FrameDriver(_FakeWindow(60))
        driver._next_draw = 999.0
        driver.act_rate("30")
        self.assertEqual(driver._next_draw, 0.0)       # 立刻按新节奏来


if __name__ == "__main__":
    unittest.main()
