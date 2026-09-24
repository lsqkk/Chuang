"""键盘：空格 / C / R / 左右 / Home / Esc，以及"焦点该留在画面上"这件事。

两件踩过的事都集中在这里：

1. **焦点必须留在这幅画上。** GTK4 的键盘事件只送到"当前有焦点的那个小部件
   所在的那块画布"。菜单弹层一关，焦点会留在弹层里那颗已经不显示的小按钮上
   （另一块画布），于是空格、左右键全都没了回声——用户看到的就是"窗口明明在
   最前面，按空格没反应 / 左右键不动"。所以：窗口重新拿到焦点、鼠标回到窗里、
   点了画、菜单合上，这四个时刻都把焦点交回画面。
2. **动作名只写一遍。** `set_toggle("info_compact")` 这种名字写错一个字母就是
   静默无效（1.1.9 信息卡右上角那颗收起箭头就是这么坏的：注册的是
   infocompact，处理器找的是 info_compact）。名字统一从 `actions` 取。

`tests/gui_smoke.py` 里对着这个类把键盘走一遍（空格 / C / 左右 / R），还有
一条"菜单关掉之后焦点回到画面"的探针盯着第 1 条。
"""

from __future__ import annotations

from datetime import timedelta

import gi

gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib  # noqa: E402

from . import actions as actionmod


class Keys:
    """"这扇窗怎么读键盘"。窗口把键盘控制器接到这里的方法上。"""

    def __init__(self, win) -> None:
        self.win = win

    # ---- 焦点 --------------------------------------------------------
    def attach_handlers(self) -> None:
        """窗口一活跃就把焦点交回画面（菜单弹层关掉之后也要补这一下）。"""
        self.win.connect("notify::is-active", self._on_active_changed)

    def _on_active_changed(self, *_args) -> None:
        if self.win.is_active():
            self.focus_canvas()

    def focus_canvas(self) -> None:
        """焦点跑到**别的画布**上时，把它交回"这幅画"，让空格 / 左右键有着落。

        只治那一种毛病：焦点还在窗里的时候一概不抢（比如有人用 Tab 走到了
        标题栏的按钮上）——那种情况下键盘事件照样会经过窗口的捕获阶段。
        """
        try:
            focused = self.win.get_focus()
            if focused is self.win.area:
                return
            native = focused.get_native() if focused is not None else None
            if focused is not None and native is self.win.get_native():
                return                  # 焦点还在窗里：不抢
            self.win.area.grab_focus()
        except Exception:               # 窗口还没建好 / 正在销毁：无所谓
            pass

    def hook_menu_popover(self, button) -> None:
        """给菜单弹层挂上"关掉了"的回声（弹层是懒创建的，出现之后再挂）。

        收焦点那一下等**弹层拆完**再动（排到 idle 里）：在 "closed" 里面直接
        grab_focus，等于扎进 GTK 正在拆弹层的过程中——轻则焦点没落上，重则跟
        X 那边的 grab 收尾纠缠不清。这种"只在某些环境下"的毛病不值得赌。
        """
        pop = button.get_popover()
        if pop is None or pop is getattr(self, "_hooked_popover", None):
            return
        self._hooked_popover = pop

        def restore(*_args):
            GLib.idle_add(self.focus_canvas)
            return False

        pop.connect("closed", restore)

    # ---- 按键 --------------------------------------------------------
    def on_key(self, _ctrl, keyval, _code, _state) -> bool:
        name = Gdk.keyval_name(keyval)
        if name == "space":
            return self.toggle_info()
        if name in ("c", "C"):
            return self.toggle_compact()
        if name in ("r", "R"):
            self.win.info.refresh_weather()
            return True
        if name == "Escape":
            return self.escape()
        if name in ("Home", "KP_Home"):
            self.win._set_preview(None)
            return True
        if name in ("Left", "Right", "KP_Left", "KP_Right"):
            return self.step(-10 if "Left" in name else 10)
        return False

    def toggle_info(self) -> bool:
        """空格：显示 / 隐藏「此刻的事实」。

        走的是和菜单里那一项**同一个**处理——菜单与托盘里那个勾也跟着变。
        """
        self.win.set_toggle(actionmod.INFO_TOGGLE, not self.win.painter.ui.show_info)
        return True

    def toggle_compact(self) -> bool:
        """C：信息卡精简模式 / 展开。"""
        self.win.set_toggle(actionmod.COMPACT_TOGGLE,
                            not self.win.config.info_compact)
        return True

    def escape(self) -> bool:
        win = self.win
        if win.painter.ui.preview_dt is not None:
            win._set_preview(None)
            return True
        if win.is_fullscreen():
            win.unfullscreen()
            return True
        return False

    def step(self, minutes: int) -> bool:
        """左右方向键：在"正在看的那一刻"前后挪（鼠标停在长卷上就从那儿挪）。"""
        ui = self.win.painter.ui
        base = ui.preview_dt or ui.hover_dt or self.win._now()
        self.win._set_preview(base + timedelta(minutes=minutes))
        return True
