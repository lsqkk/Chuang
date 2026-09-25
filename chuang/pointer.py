"""鼠标这一摊：命中信息卡、拖长卷"时间旅行"、滚轮前后挪一点。

和 `keys.py` 是一对：键盘那个管"按键送进来之后做什么"，这里管指针。两者都挂在
窗口上（`win.pointer` / `win.keys`），窗口只把 GTK 控制器的信号接过来。

几件容易互相打架的事都在这里：

* 拖长卷（按下 → 移动 → 松开）期间天色停在预览那一刻，鼠标一动就更新；
* 点一下顶上那条"正在预览"= 回到此刻，点提示条 = 收掉它（带详情的点开看）；
* 鼠标回到窗里时，把键盘焦点也交给画面——"窗口明明在最前面、按空格没反应"
  就是焦点留在别处了（见 keys.py 的说明）。

压在画布上的那几块"能点的东西"各自把方块记在 ui 里（`ui.info_rects` /
`ui.ribbon_rect` / `ui.chip_rect` / `ui.toast_rect`），这里只照着命中，
不去猜它在哪儿。
"""

from __future__ import annotations

from datetime import timedelta

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402


class Pointer:
    """"这扇窗怎么读鼠标"。窗口把几个控制器的信号接到这里的方法上。"""

    def __init__(self, win) -> None:
        self.win = win

    # ---- 命中区 ------------------------------------------------------
    def over_ribbon(self, x: float, y: float) -> bool:
        """鼠标是不是落在底下那条长卷上（含它上下一点"气口"）。"""
        x0, y0, rw, rh = self.win.painter.ui.ribbon_rect
        if rw <= 0:
            return False
        return x0 - 6 <= x <= x0 + rw + 6 and y0 - 22 <= y <= y0 + rh + 16

    # ---- 移动 --------------------------------------------------------
    def on_motion(self, _c, x, y) -> None:
        win = self.win
        ui = win.painter.ui
        if win.is_active():
            # 鼠标回到窗里 → 焦点也回到画面（"窗口在最前面但没有键盘"那种情况）
            win.keys.focus_canvas()
        if ui.dragging:
            t = win.painter.ribbon_time_at(ui, x)
            if t is not None:
                win._set_preview(t)
            return
        changed = False
        idx = win.info.hit(x, y)
        if idx != ui.info_hover:
            ui.info_hover = idx
            changed = True
        arc_dt = (win.info.arc_time(x)
                  if 0 <= idx < len(ui.info_rects) and ui.info_rects[idx][4] == "arc"
                  else None)
        if arc_dt != ui.info_hover_dt:
            ui.info_hover_dt = arc_dt
            changed = True
        over = self.over_ribbon(x, y)
        win.area.set_cursor_from_name(
            "pointer" if idx >= 0 else ("ew-resize" if over else None))
        t = win.painter.ribbon_time_at(ui, x) if over else None
        if t != ui.hover_dt:
            ui.hover_dt = t
            changed = True
        if changed:
            win.area.queue_draw()

    def on_leave(self, *_args) -> None:
        """鼠标离开窗口：悬停那几样一起清掉（卡片别停在"某一行的悬停态"）。"""
        ui = self.win.painter.ui
        ui.hover_dt = None
        ui.info_hover = -1
        ui.info_hover_dt = None
        self.win.area.queue_draw()

    # ---- 按下 / 松开 -------------------------------------------------
    def on_press(self, gesture, _n, x, y) -> None:
        """点下去：提示条 → 信息卡 → "回到此刻" → 长卷，按这个顺序认。"""
        win = self.win
        ui = win.painter.ui
        win.keys.focus_canvas()      # 手指点在画上，键盘也就该属于这幅画
        tx, ty, tw, th = ui.toast_rect
        if tw > 0 and tx <= x <= tx + tw and ty <= y <= ty + th:
            if ui.toast_detail:
                win._show_detail("详情", ui.toast_detail)
            else:                       # 点一下就把这条提示收掉
                ui.toast_until = 0.0
                win.area.queue_draw()
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            return
        idx = win.info.hit(x, y)
        if idx >= 0:
            win.info.activate(idx, x)
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            return
        cx, cy, cw, ch = ui.chip_rect
        if cw > 0 and cx <= x <= cx + cw and cy <= y <= cy + ch:
            win._set_preview(None)
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            return
        if self.over_ribbon(x, y):
            ui.dragging = True
            t = win.painter.ribbon_time_at(ui, x)
            if t is not None:
                win._set_preview(t)
                ui.dragging = True      # _set_preview 会清掉拖动标记，这里补回来
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def on_release(self, _g, _n, _x, _y) -> None:
        self.win.painter.ui.dragging = False

    # ---- 滚轮 --------------------------------------------------------
    def on_scroll(self, _c, _dx, dy):
        """滚轮：在"正在看的那一刻"（或鼠标停着的那一刻）前后挪 10 分钟。"""
        win = self.win
        ui = win.painter.ui
        if ui.preview_dt is None and ui.hover_dt is None:
            return False
        base = ui.preview_dt or ui.hover_dt or win._now()
        step = 10 if dy > 0 else -10
        win._set_preview(base + timedelta(minutes=step))
        return True
