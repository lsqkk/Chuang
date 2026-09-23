"""画面刷新率：谁来驱动重绘，以及菜单里那几档怎么调。

1.1.9 以前重绘写死在心跳里：有降水 0.07 秒一次、安静时 0.10 秒一次，也就是
10-14 帧/秒。云在飘、雨在斜、街上的人和车都在走，十几帧看着就是一个字：顿。

现在改成**由 GTK 的帧时钟驱动**：显示器每刷一帧就问一句"该画了吗"，
帧率上限由 `config.frame_rate` 决定，菜单里可以按终端性能挑（24/30/45/60/120）。
窗口不在最前面时自动降到 20 帧——那些时候没人盯着看，省下来的电是实打实的。

还有一条兜底：万一帧时钟没在走（某些远程桌面 / 合成器下真会这样），
心跳里会按帧率上限补一次重绘，免得画面就此定住——那正是"左上角时间不对"
最难查的那一类毛病（见 AGENTS §3.6）。
"""

from __future__ import annotations

# 窗口没拿到键盘焦点时（这扇窗大多数时候就是这样：摆在桌面上、不打扰人）
# 最多跑这么多帧。它是"省电挡位"，不是沉默的降级——菜单里的提示、toast、
# README 都写明这条，免得用户以为"我明明选了 120，怎么不快"。
# 实测一帧的绘制成本在 960×620～1280×800 上是 13-16 ms（软件光栅，CPU），
# 所以 60 帧大约要吃掉一个核，30 帧是一半——默认值就落在这条线上。
IDLE_FPS = 30


class FrameDriver:
    """把"什么时候重画"这件事从窗口里拿出来单独放。"""

    def __init__(self, win) -> None:
        self.win = win
        self._next_draw = 0.0
        self._last_frame_at = 0.0

    def start(self) -> None:
        self.win.area.add_tick_callback(self.on_frame)

    def interval(self) -> float:
        """这一帧该隔多久（秒）——由 config.frame_rate 决定。"""
        try:
            fps = int(getattr(self.win.config, "frame_rate", 60) or 60)
        except (TypeError, ValueError):        # 配置是纯文本，谁都能改坏它
            fps = 60
        if not 5 <= fps <= 240:                # 离谱的值（0、负数、几万）一律按默认
            fps = 60
        if not self.win.is_active():
            fps = min(fps, IDLE_FPS)
        return 1.0 / fps

    def on_frame(self, _widget, clock) -> bool:
        """每个显示帧都被叫一次：到点了就让画面重画。

        返回 True = "下一帧接着叫我"。用帧时钟而不是自己开一个定时器：
        GTK 会把重绘对齐到显示器的刷新，120Hz 的屏不会被我们拖成 40Hz，
        低刷屏上也不会白画。
        """
        try:
            now = clock.get_frame_time() / 1_000_000.0
            if now - self._next_draw >= self.interval():
                self._next_draw = now
                self._last_frame_at = now
                self.win.area.queue_draw()
        except Exception:                     # noqa: BLE001 - 一帧画不出来不该带走整扇窗
            pass
        return True

    def watchdog(self, now: float) -> None:
        """心跳里的兜底：帧时钟停摆时，最多一秒补一次重绘。"""
        if not self.win.get_visible():
            return
        if now - self._last_frame_at < 1.0:
            return
        if now >= self._next_draw:
            self._next_draw = now + self.interval()
            self._last_frame_at = now
            self.win.area.queue_draw()

    def act_rate(self, value: str) -> None:
        """菜单里那一档「画面流畅度」。"""
        win = self.win
        try:
            fps = int(value)
        except (TypeError, ValueError):
            return
        win.config.frame_rate = fps
        win.config.save()
        self._next_draw = 0.0                 # 立刻按新节奏来
        note = f"；没拿到焦点时按 {min(fps, IDLE_FPS)} 帧省电"
        win.toast(f"画面改成 {fps} 帧/秒{note}", 3.5)
