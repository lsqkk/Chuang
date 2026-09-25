"""「窗口置顶」：GTK4 去掉了 keep-above，这里直接跟 X11 说。

做法是给窗口自己发一个 EWMH 的 `_NET_WM_STATE` 客户端消息（source=1 表示
应用发起，窗口管理器收到后自己把状态设上去）——比"绕过 WM 直接改属性"规矩，
GNOME 也认。

`python3-xlib` 是**可选**依赖（见 README 的依赖表）：装了就能置顶，没装就把
菜单里那颗图钉置灰、并说明为什么——绝不因为缺一个可选包就起不来，更不能抛
异常（这里是 X11 特有的一条路，Wayland 会话下取不到 XID，同样只是"不可用"）。
"""

from __future__ import annotations

import importlib.util

import gi


def available() -> bool:
    """这台机器上能不能走 X11 这条置顶的路。"""
    try:
        return importlib.util.find_spec("Xlib") is not None
    except (ImportError, ValueError):
        return False


class Topmost:
    """一支画笔旁边的"把窗钉在最上面"。窗口持有一个（`win.topmost`）。"""

    def __init__(self, win) -> None:
        self.win = win

    @staticmethod
    def _xid(window) -> int:
        """窗口的 X11 id；Wayland 或还没有 surface 的时候是 0。"""
        surf = window.get_surface()
        try:
            gi.require_version("GdkX11", "4.0")
            from gi.repository import GdkX11
            return GdkX11.X11Surface.get_xid(surf)  # type: ignore[attr-defined]
        except Exception:
            return 0

    def set_above(self, above: bool) -> bool:
        """把窗口设为 / 取消"总在最前面"。成功返回 True。"""
        if not available():
            return False
        try:
            from Xlib import X, display, protocol
            d = display.Display()
            xid = self._xid(self.win)
            if not xid:
                return False
            win = d.create_resource_object("window", xid)
            state_atom = d.intern_atom("_NET_WM_STATE")
            source = 1        # 1 = 应用发起（EWMH 规范），WM 收到后会直接设置状态
            data = [1 if above else 0, d.intern_atom("_NET_WM_STATE_ABOVE"), 0,
                    source, 0]
            event = protocol.event.ClientMessage(window=win, client_type=state_atom,
                                                 data=(32, data))
            d.screen().root.send_event(
                event, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
            d.flush()
            d.close()
            return True
        except Exception:
            return False
