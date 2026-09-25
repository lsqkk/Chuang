"""这扇窗自己的壳：标题栏（图钉 + 菜单）、画布、几个事件控制器、CSS。

`build(win)` 把壳装好，并把控制器接到窗口那些协作者上（`win.pointer` 管鼠标、
`win.keys` 管键盘）——窗口那边只剩"什么时候叫它"。

几条 GTK 4.6 上的规矩都在这里，改之前先读：

* 快捷键接在窗口的**捕获阶段**（`ShortcutController` 的 MANAGED / GLOBAL 在
  这台机器上对送到窗口的按键不触发，见 keys.py 的说明）；
* 标题栏那两颗按钮 `set_focus_on_click(False)`——点一次图钉不该把键盘焦点从
  画面上抢走（否则之后按空格变成"再按一次图钉"，卡片反而不动了）；
* 菜单弹层"关掉了"那一记回声要排到 idle 里（`mainloop.to_main`），默认优先级
  的 idle 会被帧时钟饿死；
* 整幅画面是一张 Cairo 位图，屏幕阅读器只能读到标题栏和菜单——至少把"窗外的
  天空"挂成可读的描述。
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk, GLib  # noqa: E402

from . import actions
from .mainloop import to_main

CSS = """
window.chuang, .chuang-bg { background: #05070d; }
headerbar {
  background: rgba(9, 12, 20, 0.88);
  box-shadow: none;
  border-bottom: 1px solid rgba(255, 255, 255, 0.07);
  min-height: 40px;
}
headerbar windowtitle { color: #eef2fb; }
headerbar windowtitle:backdrop { color: #aab4c8; }
headerbar button { color: #dfe6f5; }
popover > contents { background: rgba(18, 22, 33, 0.97); }
"""


def build(win) -> None:
    """把窗口的壳装起来（画布、标题栏、控制器）。"""
    win.area = Gtk.DrawingArea()
    win.area.set_hexpand(True)
    win.area.set_vexpand(True)
    win.area.set_draw_func(win._on_draw)
    win.area.set_focusable(True)
    try:
        win.area.update_property([Gtk.AccessibleProperty.LABEL],
                                 ["窗外的天空"])
    except Exception:
        pass

    motion = Gtk.EventControllerMotion()
    motion.connect("motion", win.pointer.on_motion)
    motion.connect("leave", win.pointer.on_leave)
    win.area.add_controller(motion)

    click = Gtk.GestureClick()
    click.connect("pressed", win.pointer.on_press)
    click.connect("released", win.pointer.on_release)
    win.area.add_controller(click)

    scroll = Gtk.EventControllerScroll()
    scroll.set_flags(Gtk.EventControllerScrollFlags.VERTICAL)
    scroll.connect("scroll", win.pointer.on_scroll)
    win.area.add_controller(scroll)

    # 键盘接在窗口的**捕获阶段**：事件自窗口往下走时就先被这里拿走，焦点落在
    # 标题栏那颗图钉或菜单按钮上时，空格也不会被按钮当"激活"吃掉。
    keys = Gtk.EventControllerKey()
    keys.connect("key-pressed", win.keys.on_key)
    keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    win.add_controller(keys)
    win.connect("map", lambda *_: (win.area.grab_focus(), False)[1])
    # 焦点会跑到"别的画布"上去（菜单弹层关掉之后最典型），见 keys.Keys
    win.keys.attach_handlers()

    win.title_widget = Adw.WindowTitle(title="窗", subtitle="")
    header = Adw.HeaderBar()
    header.set_title_widget(win.title_widget)

    win.pin_button = Gtk.ToggleButton(icon_name="view-pin-symbolic",
                                      tooltip_text="让这扇窗一直浮在最上面")
    win.pin_button.set_active(win.config.always_on_top and win.pin_ok)
    if not win.pin_ok:
        win.pin_button.set_sensitive(False)
        win.pin_button.set_tooltip_text("需要 python3-xlib 才能置顶")
    win.pin_button.connect("toggled", win._on_pin_toggled)
    win.pin_button.set_focus_on_click(False)
    header.pack_end(win.pin_button)

    win.menu_model = actions.build_menu(win)
    win.menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic",
                                     menu_model=win.menu_model, tooltip_text="更多")
    win.menu_button.set_focus_on_click(False)
    # 菜单弹层关掉之后焦点会留在弹层里那颗小按钮上（见 keys.Keys.focus_canvas），
    # 这一跳也得走 to_main：默认优先级的 idle 会被帧时钟饿死（见 mainloop.py）
    to_main(lambda: (win.keys.hook_menu_popover(win.menu_button), False)[1])
    header.pack_end(win.menu_button)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.append(header)
    box.append(win.area)
    win.set_content(box)

    provider = Gtk.CssProvider()
    provider.load_from_data(CSS.encode("utf-8"))
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    if win.config.always_on_top and win.pin_ok:
        GLib.timeout_add(400, lambda: (win.topmost.set_above(True), False)[1])
