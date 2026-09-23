"""窗 · Chuang —— GTK4 主程序。"""

from __future__ import annotations

import shutil
import threading
import time as _time
import urllib.request
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, __version__, config as cfgmod
from .render import SkyPainter
from .scene import SkyEngine
from . import wallpaper as wallmod
from . import update as upmod
from . import tray as traymod
from .weather import WeatherService, geocode

# 静态壁纸跟随此刻的间隔（秒）
WALLPAPER_INTERVAL = 10

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


class CloseDialog(Gtk.Window):
    """第一次关窗时问一句：留在托盘，还是直接退出。（GTK4 4.6 没有
    MessageDialog.set_extra_child，所以自己搭一个。）"""

    def __init__(self, parent, tray_available: bool, on_choice):
        super().__init__(transient_for=parent, modal=True, resizable=False,
                         title="关掉「窗」吗？")
        self.on_choice = on_choice
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=20, margin_bottom=18, margin_start=20, margin_end=20)
        title = Gtk.Label(xalign=0)
        title.set_markup("<b>要把「窗」关掉吗？</b>")
        box.append(title)
        hint = ("留在托盘里的话，它还会继续把壁纸跟着天空更新；"
                "托盘图标点一下就能再打开。")
        if not tray_available:
            hint = "这台机器的系统托盘不可用（需要 GNOME 的 AppIndicator 扩展），只能直接退出。"
        sub = Gtk.Label(label=hint, xalign=0, wrap=True, max_width_chars=38)
        sub.add_css_class("dim-label")
        box.append(sub)
        self.remember = Gtk.CheckButton(label="记住我的选择（之后可在菜单里改）")
        box.append(self.remember)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_halign(Gtk.Align.END)
        self.tray_button = Gtk.Button(label="最小化到托盘")
        self.tray_button.add_css_class("suggested-action")
        self.tray_button.set_sensitive(tray_available)
        self.tray_button.connect("clicked", self._pick, "tray")
        quit_button = Gtk.Button(label="直接退出")
        quit_button.connect("clicked", self._pick, "quit")
        row.append(quit_button)
        row.append(self.tray_button)
        box.append(row)
        self.set_child(box)

    def _pick(self, _btn, choice: str):
        self.on_choice(choice, self.remember.get_active())
        self.destroy()


class CityDialog(Gtk.Window):
    """换一个城市（联网搜索；也可以直接输入经纬度）。"""

    def __init__(self, parent, on_pick):
        super().__init__(modal=True, transient_for=parent, default_width=470,
                         default_height=520)
        self.set_title("换一扇窗")
        self.on_pick = on_pick
        self._searching = False

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="换一扇窗",
                                                subtitle="选择你想看的那片天空"))
        box.append(header)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                        margin_top=6, margin_bottom=14, margin_start=14, margin_end=14)
        self.entry = Gtk.SearchEntry(placeholder_text="搜索城市，例如 西安 / Kyoto / 里斯本")
        inner.append(self.entry)

        self.status = Gtk.Label(label="输入城市名开始搜索", xalign=0)
        self.status.add_css_class("dim-label")
        inner.append(self.status)

        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.listbox.add_css_class("boxed-list")
        self.listbox.connect("row-activated", self._activated)
        sw = Gtk.ScrolledWindow(vexpand=True)
        sw.set_child(self.listbox)
        inner.append(sw)

        manual = Gtk.Expander(label="或者，直接输入经纬度")
        mbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                       margin_top=8, margin_bottom=4)
        self.lat_entry = Gtk.Entry(placeholder_text="纬度 34.34")
        self.lon_entry = Gtk.Entry(placeholder_text="经度 108.94")
        self.lat_entry.set_hexpand(True)
        self.lon_entry.set_hexpand(True)
        mbox.append(self.lat_entry)
        mbox.append(self.lon_entry)
        manual.set_child(mbox)
        inner.append(manual)
        btn = Gtk.Button(label="使用这组坐标")
        btn.connect("clicked", self._manual)
        inner.append(btn)

        box.append(inner)
        self.set_child(box)

        self._debounce = None
        self.entry.connect("changed", self._on_changed)

    def _on_changed(self, entry):
        if self._debounce:
            GLib.source_remove(self._debounce)
        self._debounce = GLib.timeout_add(350, self._kick_search)

    def _kick_search(self):
        self._debounce = None
        text = self.entry.get_text().strip()
        if len(text) < 1:
            return False
        if self._searching:
            return False
        self._searching = True
        self.status.set_text("正在搜索…")

        def worker():
            try:
                rows = geocode(text, 8)
            except Exception as exc:            # 网络错误
                rows = [{"error": str(exc)}]
            GLib.idle_add(self._fill, rows)

        threading.Thread(target=worker, daemon=True, name="chuang-geocode").start()
        return False

    def _fill(self, rows):
        self._searching = False
        while (child := self.listbox.get_first_child()) is not None:
            self.listbox.remove(child)
        real = [r for r in rows if "error" not in r]
        if not real:
            self.status.set_text("没找到 · 可能是网络不通，试试直接输入经纬度")
            return False
        if any("error" in r for r in rows):
            self.status.set_text("网络不通，结果可能不完整")
        else:
            self.status.set_text(f"找到 {len(real)} 个地方")
        for r in real:
            title = r["name"]
            sub = " · ".join(x for x in (r.get("admin"), r.get("country")) if x)
            row = Adw.ActionRow(title=title, subtitle=sub or "—", activatable=True)
            row.add_prefix(Gtk.Image.new_from_icon_name("mark-location-symbolic"))
            row.set_activatable_widget(None)
            row.data = r  # type: ignore[attr-defined]
            self.listbox.append(row)
        return False

    def _activated(self, _list, row):
        r = getattr(row, "data", None)
        if not r:
            return
        self.on_pick(cfgmod.Location(name=r["name"], admin=r.get("admin") or "",
                                     country=r.get("country") or "",
                                     lat=r["lat"], lon=r["lon"],
                                     timezone=r.get("timezone") or "auto"))
        self.close()

    def _manual(self, _btn):
        try:
            lat = float(self.lat_entry.get_text().strip())
            lon = float(self.lon_entry.get_text().strip())
        except ValueError:
            self.status.set_text("经纬度需要是数字，例如 34.34 与 108.94")
            return
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            self.status.set_text("纬度要在 ±90 之间，经度要在 ±180 之间")
            return
        offset = int(round(lon / 15.0))
        offset = max(-12, min(12, offset))
        tz = f"Etc/GMT{'-' if offset >= 0 else '+'}{abs(offset)}"
        self.on_pick(cfgmod.Location(name=f"{lat:.2f}, {lon:.2f}", admin="自定义坐标",
                                     country="", lat=lat, lon=lon, timezone=tz))
        self.close()


class ChuangWindow(Adw.ApplicationWindow):
    def __init__(self, app: "ChuangApp"):
        super().__init__(application=app, title="窗")
        self.app = app
        self.config = app.config
        self.engine = SkyEngine()
        self.painter = SkyPainter(seed=abs(hash((self.config.location.lat,
                                                 self.config.location.lon))) % 9973 + 7)
        self._scene = None
        self._scene_key = None
        self._ribbon_key = None
        self._last_minute = -1
        self.tray = None
        self.menu_model = None
        self.available_release = None
        self._checking_update = False
        self._really_quit = False
        self._pin_ok = self._init_pin()
        # 调试钩子：CHUANG_TIME=2026-09-23T18:40 / CHUANG_WEATHER=63:95:9:180
        self._fake_time = self._parse_fake_time()
        self._fake_weather = self._parse_fake_weather()
        self.add_css_class("chuang")

        self.set_default_size(self.config.window_w, self.config.window_h)
        self.set_size_request(360, 260)
        self.painter.clock = self._now       # 人车按实时时钟连续移动
        self._build_ui()
        self._build_actions()

        loc = self.config.location
        self.wp = wallmod.Worker(loc.lat, loc.lon, loc.timezone, loc.label,
                                 seed=abs(hash(loc.name)) % 9973 + 11)
        self._setup_tray()

        self.weather = WeatherService(self._on_weather)
        self.weather.enabled = self.config.mirror_weather
        if self._fake_weather is not None:
            self.weather.weather = self._fake_weather
            self.weather.enabled = False
        self.engine.set_location(self.config.location.lat, self.config.location.lon,
                                 self.config.location.timezone)
        if self._fake_weather is None:
            self.weather.set_location(self.config.location.lat, self.config.location.lon,
                                      self.config.location.timezone)

        self._next_draw = 0.0
        self._next_wallpaper = _time.monotonic() + 60
        self._timer = GLib.timeout_add(25, self._tick)
        self.connect("close-request", self._on_close)
        if self.config.wallpaper_auto:
            GLib.timeout_add(2500, self._refresh_wallpaper_once)
        if self.config.update_check:
            GLib.timeout_add(9000, self._maybe_auto_check)
        self.first_run_tips()

    # ------------------------------------------------------------------
    # 调试钩子（正式使用不会用到）
    # ------------------------------------------------------------------
    def _parse_fake_time(self):
        import os
        from datetime import datetime, timedelta
        raw = os.environ.get("CHUANG_TIME")
        if not raw:
            return None
        tz = self.engine._resolve_tz(self.config.location.timezone)
        now = datetime.now(tz) if tz else datetime.now().astimezone()
        try:
            if "T" in raw:
                dt = datetime.fromisoformat(raw)
                return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt
            hh, mm = (int(x) for x in raw.split(":")[:2])
            return now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        except ValueError:
            return None

    def _parse_fake_weather(self):
        import os
        from datetime import timedelta
        from .weather import HourPoint, Weather
        raw = os.environ.get("CHUANG_WEATHER")
        if not raw:
            return None
        try:
            parts = [float(x) for x in raw.split(":")]
            code = int(parts[0])
            cloud = parts[1] if len(parts) > 1 else 0.0
            wind = parts[2] if len(parts) > 2 else 0.0
            wdir = parts[3] if len(parts) > 3 else 0.0
            streak = parts[4] if len(parts) > 4 else 0.0
        except ValueError:
            return None
        w = Weather(ok=True, fetched_at=_time.time(), code=code, cloud=cloud,
                    wind_speed=wind, wind_dir=wdir, temp=21.0, apparent=21.0,
                    humidity=68.0, precip=streak, visibility=12000.0)
        base = self.engine.local_date().replace(tzinfo=None)
        for i in range(48):
            w.hourly.append(HourPoint(base + timedelta(hours=i), cloud, code, 20.0, 40.0))
        return w

    # ------------------------------------------------------------------
    def _init_pin(self) -> bool:
        """GTK4 去掉了 keep-above，这里用 X11 的 _NET_WM_STATE_ABOVE 实现。"""
        try:
            import Xlib  # noqa: F401
            return True
        except ImportError:
            return False

    def _set_above(self, above: bool) -> bool:
        if not self._pin_ok:
            return False
        try:
            from Xlib import X, display, protocol
            d = display.Display()
            xid = self._xid()
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

    def _xid(self) -> int:
        surf = self.get_surface()
        try:
            gi.require_version("GdkX11", "4.0")
            from gi.repository import GdkX11
            return GdkX11.X11Surface.get_xid(surf)  # type: ignore[attr-defined]
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------
    def _build_ui(self):
        self.area = Gtk.DrawingArea()
        self.area.set_hexpand(True)
        self.area.set_vexpand(True)
        self.area.set_draw_func(self._on_draw)
        self.area.set_focusable(True)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_leave)
        self.area.add_controller(motion)

        click = Gtk.GestureClick()
        click.connect("pressed", self._on_press)
        click.connect("released", self._on_release)
        self.area.add_controller(click)

        scroll = Gtk.EventControllerScroll()
        scroll.set_flags(Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self._on_scroll)
        self.area.add_controller(scroll)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.add_controller(keys)

        self.title_widget = Adw.WindowTitle(title="窗", subtitle="")
        header = Adw.HeaderBar()
        header.set_title_widget(self.title_widget)

        self.pin_button = Gtk.ToggleButton(icon_name="view-pin-symbolic",
                                           tooltip_text="让这扇窗一直浮在最上面")
        self.pin_button.set_active(self.config.always_on_top and self._pin_ok)
        if not self._pin_ok:
            self.pin_button.set_sensitive(False)
            self.pin_button.set_tooltip_text("需要 python3-xlib 才能置顶")
        self.pin_button.connect("toggled", self._on_pin_toggled)
        header.pack_end(self.pin_button)

        menu = self._build_menu()
        self.menu_model = menu
        self.menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic",
                                          menu_model=menu, tooltip_text="更多")
        header.pack_end(self.menu_button)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(header)
        box.append(self.area)
        self.set_content(box)

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS.encode("utf-8"))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        if self.config.always_on_top and self._pin_ok:
            GLib.timeout_add(400, lambda: (self._set_above(True), False)[1])

    def activate(self, name: str, target=None) -> bool:
        """稳妥地激活一个动作。

        GTK4 里 Gtk.Widget.activate_action 在 PyGObject 下按参数格式走，
        传 Variant 会静默失败；这里直接拿 action 对象来 activate。
        """
        scope, _, short = name.partition(".")
        act = None
        if scope == "app":
            act = self.app.lookup_action(short)
        elif scope == "win":
            act = self.lookup_action(short)
        else:
            act = self.lookup_action(name)
        if act is None:
            return False
        act.activate(target)
        return True

    def _build_menu(self) -> Gio.Menu:
        menu = Gio.Menu()
        # 零、有新版本时置顶提示
        if self.available_release is not None:
            rel = self.available_release
            up = Gio.Menu()
            up.append("打开发布页", "win.openreleases")
            if rel.deb_url:
                up.append("下载 .deb 安装包", "win.downloaddeb")
            up.append("跳过这个版本", "win.skipversion")
            menu.append_submenu(f"有新版本 {rel.tag} · 查看", up)

        # 一、看（当前这扇窗本身）
        sec_view = Gio.Menu()
        sec_view.append("沉浸全屏", "win.fullscreen")
        sec_view.append("窗口置顶", "win.pin")
        sec_view.append("显示此刻的事实", "win.info")
        menu.append_section(None, sec_view)

        # 二、换（看哪片天）
        sec_place = Gio.Menu()
        sec_place.append("换一扇窗（城市）…", "win.city")
        sec_place.append("跟随真实天气", "win.weather")
        menu.append_section(None, sec_place)

        # 三、桌面壁纸（子菜单）
        wall = Gio.Menu()
        wall.append("把此刻的天空设为壁纸（一张）", "win.wallpaper")
        wall.append("壁纸跟随此刻（每 10 秒换一张）", "win.wallpaperauto")
        wall.append("生成离线动态壁纸（15 分钟一帧，关掉也有效）", "win.wallpaperday")
        w3 = Gio.Menu()
        w3.append("壁纸上显示「此刻的事实」", "win.wallpaperinfo")
        w3.append("壁纸上显示「今日天色」长卷", "win.wallpaperribbon")
        w3.append("还原成原来的壁纸", "win.wallpaperrestore")
        wall.append_section(None, w3)
        menu.append_submenu("桌面壁纸", wall)

        # 四、这扇窗怎么待着
        sec_behave = Gio.Menu()
        sec_behave.append("开机时自动打开", "win.autostart")
        for label, target in (("关窗时：问我", "ask"),
                              ("关窗时：最小化到托盘", "tray"),
                              ("关窗时：直接退出", "quit")):
            item = Gio.MenuItem.new(label, "win.closebehavior")
            item.set_attribute_value("target", GLib.Variant("s", target))
            sec_behave.append_item(item)
        menu.append_section(None, sec_behave)

        sec_last = Gio.Menu()
        sec_last.append("检查更新", "win.checkupdate")
        sec_last.append("自动检查更新", "win.autoupdate")
        sec_last.append("关于窗", "win.about")
        sec_last.append("退出", "win.quit")
        menu.append_section(None, sec_last)
        return menu

    def _build_actions(self):
        app = self.app

        def add(name, handler):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)

        def add_toggle(name, initial: bool, on_set):
            """勾选项：无论菜单是"带 target"还是"不带 target"地激活，
            也不管是通过 activate 还是 change-state 进来，都收敛到同一个结果。"""
            # 勾选项：参数类型留空，GTK 才会把它画成"可点的勾选项"，
            # 由 activate 处理器自己翻转状态（托盘那边也是不带参数地激活）。
            action = Gio.SimpleAction.new_stateful(
                name, None, GLib.Variant.new_boolean(initial))

            def do(want: bool):
                if action.get_state().get_boolean() != want:
                    action.set_state(GLib.Variant.new_boolean(want))
                on_set(want)

            def on_activate(act, param):
                want = param.get_boolean() if param is not None \
                    else not act.get_state().get_boolean()
                do(want)

            def on_change(act, value):
                do(value.get_boolean())

            action.connect("activate", on_activate)
            action.connect("change-state", on_change)
            self.add_action(action)

        def add_radio(name, initial: str, on_set):
            action = Gio.SimpleAction.new_stateful(
                name, GLib.VariantType.new("s"), GLib.Variant.new_string(initial))

            def do(value: str):
                if action.get_state().get_string() != value:
                    action.set_state(GLib.Variant.new_string(value))
                on_set(value)

            def on_activate(act, param):
                do(param.get_string() if param is not None
                   else act.get_state().get_string())

            def on_change(act, value):
                do(value.get_string())

            action.connect("activate", on_activate)
            action.connect("change-state", on_change)
            self.add_action(action)

        add("fullscreen", self._act_fullscreen)
        add("city", self._act_city)
        add("wallpaper", self._act_wallpaper)
        add("wallpaperday", self._act_wallpaper_day)
        add("wallpaperrestore", self._act_wallpaper_restore)
        add("show", self._act_show)
        add("checkupdate", self._act_check_update)
        add("openreleases", self._act_open_releases)
        add("downloaddeb", self._act_download_deb)
        add("skipversion", self._act_skip_version)
        add("quit", self._act_quit)
        add("about", self._act_about)

        add_toggle("pin", self.config.always_on_top, self._act_pin)
        add_toggle("weather", self.config.mirror_weather, self._act_weather)
        add_toggle("info", self.painter.ui.show_info, self._act_info)
        add_toggle("autostart", self.config.autostart, self._act_autostart)
        add_toggle("wallpaperauto", self.config.wallpaper_auto, self._act_wallpaper_auto)
        add_toggle("wallpaperinfo", self.config.wallpaper_show_info,
                   self._act_wallpaper_info)
        add_toggle("wallpaperribbon", self.config.wallpaper_show_ribbon,
                   self._act_wallpaper_ribbon)
        add_radio("closebehavior", self.config.close_behavior, self._act_close_behavior)

        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: app.quit())
        app.add_action(quit_action)

        app.set_accels_for_action("win.fullscreen", ["F11"])
        app.set_accels_for_action("win.quit", ["<Control>q"])

    # ------------------------------------------------------------------
    # 动作
    # ------------------------------------------------------------------
    def _act_fullscreen(self, *_):
        if self.is_fullscreen():
            self.unfullscreen()
        else:
            self.fullscreen()

    def _on_pin_toggled(self, button):
        active = button.get_active()
        if active != self.action_state("pin"):
            self.activate("pin", GLib.Variant.new_boolean(active))

    def action_state(self, name: str) -> bool:
        act = self.lookup_action(name)
        if act is None:
            return False
        state = act.get_state()
        return bool(state.get_boolean()) if state is not None else False

    def _act_pin(self, want: bool):
        if want and not self._pin_ok:
            self.toast("置顶需要先安装 python3-xlib")
            self.pin_button.set_active(False)
            return
        self.config.always_on_top = want
        self.config.save()
        if self.pin_button.get_active() != want:
            self.pin_button.set_active(want)
        self._set_above(want)
        self.toast("已置顶，可以缩成一条小窗放在角落" if want else "取消置顶")

    def _act_weather(self, want: bool):
        self.config.mirror_weather = want
        self.config.save()
        self.weather.enabled = want
        self._ribbon_key = None
        if want:
            self.weather.refresh(force=True)
            self.toast("窗外的天气会真实地出现在窗上")
        else:
            self.toast("只看天，不看天气")

    def _act_info(self, want: bool):
        self.painter.ui.show_info = want
        self.area.queue_draw()

    def _act_city(self, *_):
        CityDialog(self, self._set_location).present()

    # ------------------------------------------------------------------
    # 桌面壁纸
    # ------------------------------------------------------------------
    def _wallpaper_opts(self) -> tuple[bool, bool]:
        return (bool(self.config.wallpaper_show_info),
                bool(self.config.wallpaper_show_ribbon))

    def _remember_wallpaper(self):
        """第一次动壁纸前，把原来那张记下来，方便还原。"""
        if self.config.prev_wallpaper:
            return
        light, dark = wallmod.current_uris()
        self.config.prev_wallpaper = light
        self.config.prev_wallpaper_dark = dark
        self.config.save()

    def apply_wallpaper(self, quiet: bool = False) -> None:
        if self.wp.busy:
            return
        self._remember_wallpaper()
        show_info, show_ribbon = self._wallpaper_opts()
        size = wallmod.screen_size()
        loc = self.config.location
        self.wp.location(loc.lat, loc.lon, loc.timezone, loc.label)
        self.wp.render_now(self.engine.local_now(), self.weather.weather,
                           show_info, show_ribbon, size, self.config.wallpaper_slot,
                           lambda ok, msg, slot: self._wallpaper_done(ok, msg, slot, quiet))
        if not quiet:
            self.toast("正在把这扇窗挂到桌面上…", 2.0)

    def _wallpaper_done(self, ok: bool, msg: str, slot: int, quiet: bool = False) -> bool:
        self.config.wallpaper_slot = slot
        if ok:
            self.config.save()
        # 每 10 秒的自动跟随不弹提示，否则提示会一直挂在屏幕上
        if not quiet or not ok:
            self.toast(msg, 4.5 if ok else 6.0)
        return False

    def _act_wallpaper(self, *_):
        self.apply_wallpaper()

    def _refresh_wallpaper_once(self):
        if self.config.wallpaper_auto:
            self.apply_wallpaper(quiet=True)
            self._next_wallpaper = _time.monotonic() + WALLPAPER_INTERVAL
        return False

    def _act_wallpaper_auto(self, want: bool):
        self.config.wallpaper_auto = want
        self.config.save()
        self._next_wallpaper = _time.monotonic() + WALLPAPER_INTERVAL
        if want:
            self.apply_wallpaper()
            self.toast("壁纸每 10 秒跟着此刻换一张（要一直更新，记得把「窗」留在托盘里）", 6.0)
        else:
            self.toast("壁纸不再自动更新，现在这张会留着", 4.0)

    def _act_wallpaper_info(self, want: bool):
        self.config.wallpaper_show_info = want
        self.config.save()
        if self.config.wallpaper_auto:
            self.apply_wallpaper(quiet=True)
        self.toast("壁纸上会带上「此刻的事实」" if want else "壁纸只留下景色", 3.5)

    def _act_wallpaper_ribbon(self, want: bool):
        self.config.wallpaper_show_ribbon = want
        self.config.save()
        if self.config.wallpaper_auto:
            self.apply_wallpaper(quiet=True)
        self.toast("壁纸上会带上「今日天色」长卷" if want else "壁纸不带长卷了", 3.5)

    def _act_wallpaper_day(self, *_):
        if self.wp.busy:
            self.toast("还在画，等一下…", 3.0)
            return
        self._remember_wallpaper()
        show_info, show_ribbon = self._wallpaper_opts()
        loc = self.config.location
        self.wp.location(loc.lat, loc.lon, loc.timezone, loc.label)
        size = wallmod.screen_size()
        self._wp_progress = 0
        self.toast("正在画这一天的 96 张天色，约二十秒…", 6.0)
        self.wp.render_day(self.engine.local_date(), self.weather.weather,
                           show_info, show_ribbon, size, 96,
                           self._wallpaper_progress, self._wallpaper_day_done)

    def _wallpaper_progress(self, done: int, total: int) -> bool:
        self.painter.ui.toast = f"正在画今天的天色 {done}/{total}"
        self.painter.ui.toast_until = _time.time() + 3.0
        self.area.queue_draw()
        return False

    def _wallpaper_day_done(self, ok: bool, msg: str) -> bool:
        if ok:
            self.config.wallpaper_dynamic = True
            self.config.wallpaper_auto = False
            self.activate("wallpaperauto", GLib.Variant.new_boolean(False))
            self.config.save()
            self.toast("动态壁纸做好了：今天 24 小时会自己走一遍，不开着也有效", 7.0)
        else:
            self.toast(msg, 6.0)
        return False

    def _act_wallpaper_restore(self, *_):
        ok, msg = wallmod.restore(self.config.prev_wallpaper,
                                  self.config.prev_wallpaper_dark)
        if ok:
            self.config.wallpaper_auto = False
            self.config.wallpaper_dynamic = False
            self.config.prev_wallpaper = ""
            self.config.prev_wallpaper_dark = ""
            self.config.save()
            self.activate("wallpaperauto", GLib.Variant.new_boolean(False))
        self.toast(msg, 5.0)

    # ------------------------------------------------------------------
    # 检查更新
    # ------------------------------------------------------------------
    def _maybe_auto_check(self) -> bool:
        if not self.config.update_check:
            return False
        if _time.time() - float(self.config.last_update_check or 0) < upmod.CHECK_INTERVAL:
            return False
        self.check_updates(manual=False)
        return False

    def check_updates(self, manual: bool = True) -> None:
        if getattr(self, "_checking_update", False):
            if manual:
                self.toast("正在检查更新…", 2.0)
            return
        self._checking_update = True
        if manual:
            self.toast("正在检查更新…", 2.0)

        def worker():
            rel = upmod.fetch_latest()
            GLib.idle_add(self._update_result, manual, rel)

        threading.Thread(target=worker, daemon=True, name="chuang-update").start()

    def _update_result(self, manual: bool, rel) -> bool:
        self._checking_update = False
        self.config.last_update_check = _time.time()
        self.config.save()
        if rel is None:
            if manual:
                self.toast("检查更新失败：网络或 GitHub 接口不可用", 5.0)
            return False
        newer = upmod.is_newer(rel.version, upmod.parse_version(__version__))
        if newer and rel.tag != self.config.skipped_version:
            self.available_release = rel
            self._refresh_menu()
            self.toast(f"有新版本 {rel.tag}：菜单最上面可以打开发布页", 8.0)
        else:
            if self.available_release is not None:
                self.available_release = None
                self._refresh_menu()
            if manual:
                self.toast(f"已经是最新的 {__version__}", 4.0)
        return False

    def _refresh_menu(self) -> None:
        """菜单内容变了（比如出现了新版本入口）——窗口和托盘一起换掉。"""
        self.menu_model = self._build_menu()
        if getattr(self, "menu_button", None) is not None:
            self.menu_button.set_menu_model(self.menu_model)
        if getattr(self, "tray", None) is not None:
            self.tray.reload(self.menu_model)

    def _act_check_update(self, *_):
        self.check_updates(manual=True)

    def _act_auto_update(self, want: bool):
        self.config.update_check = want
        self.config.save()
        self.toast("会自动检查更新（一天一次，只问版本号）" if want
                   else "不再自动检查更新", 4.0)
        if want:
            self.check_updates(manual=False)

    def _act_open_releases(self, *_):
        rel = self.available_release
        url = rel.url if rel is not None else upmod.RELEASES_URL
        try:
            Gio.AppInfo.launch_default_for_uri(url, None)
            self.toast("已经在浏览器里打开了发布页", 4.0)
        except Exception as exc:
            self.toast(f"打不开浏览器，地址是 {url}", 8.0)

    def _act_skip_version(self, *_):
        rel = self.available_release
        if rel is None:
            return
        self.config.skipped_version = rel.tag
        self.config.save()
        self.available_release = None
        self._refresh_menu()
        self.toast(f"已跳过 {rel.tag}，下一个版本再提醒", 4.0)

    def _act_download_deb(self, *_):
        rel = self.available_release
        if rel is None or not rel.deb_url:
            self.toast("这个版本没有提供 .deb 安装包", 4.0)
            return
        target = self._download_dir() / rel.deb_name
        self.toast(f"正在下载 {rel.deb_name}…", 3.0)

        def worker():
            try:
                req = urllib.request.Request(rel.deb_url,
                                             headers={"User-Agent": upmod.UA})
                with urllib.request.urlopen(req, timeout=60) as resp, \
                        open(target, "wb") as fh:
                    shutil.copyfileobj(resp, fh)
                GLib.idle_add(self._download_done, True, str(target))
            except Exception as exc:
                GLib.idle_add(self._download_done, False, str(exc))

        threading.Thread(target=worker, daemon=True, name="chuang-deb").start()

    def _download_done(self, ok: bool, info: str) -> bool:
        if ok:
            self.toast(f"已下载到 {info} · 安装：sudo dpkg -i {info}", 12.0)
        else:
            self.toast(f"下载失败：{info}", 6.0)
        return False

    @staticmethod
    def _download_dir():
        try:
            path = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
        except Exception:
            path = None
        return Path(path or Path.home() / "下载") if path else (Path.home() / "Downloads")

    # ------------------------------------------------------------------
    # 关闭行为与托盘
    # ------------------------------------------------------------------
    def _act_show(self, *_):
        self.set_visible(True)
        self.present()

    def _act_quit(self, *_):
        self._really_quit = True
        self.close()

    def _act_close_behavior(self, value: str):
        self.config.close_behavior = value
        self.config.save()
        names = {"ask": "每次问你", "tray": "最小化到托盘", "quit": "直接退出"}
        self.toast("关闭窗口时：" + names.get(self.config.close_behavior, ""), 3.0)

    def _ask_close(self):
        """第一次关窗时问一次，并记住。"""
        tray_ok = bool(self.tray is not None and self.tray.available)
        CloseDialog(self, tray_ok, self._on_close_answer).present()

    def _on_close_answer(self, choice: str, remember: bool):
        to_tray = choice == "tray"
        if to_tray and not (self.tray and self.tray.available):
            self.toast("系统托盘不可用，这次先直接退出了", 5.0)
            to_tray = False
        if remember:
            value = GLib.Variant.new_string("tray" if to_tray else "quit")
            self.activate("closebehavior", value)
        if to_tray:
            self.set_visible(False)
            self.toast("还在托盘里看着你的天空", 3.0)
        else:
            self._really_quit = True
            self.close()

    def _setup_tray(self):
        try:
            self.tray = traymod.Tray(self.menu_model, self._tray_activate,
                                     self._tray_lookup,
                                     prefix_items=[("显示「窗」", "win.show")])
            self.tray.start()
        except Exception:
            self.tray = None

    def _tray_lookup(self, name: str):
        if not name:
            return None
        scope, _, short = name.partition(".")
        if scope == "app":
            return self.app.lookup_action(short)
        return self.lookup_action(short)

    def _tray_activate(self, name: str, target=None):
        self.activate(name, target)

    def _act_autostart(self, want: bool):
        exe = self.app.installed_exec()
        cfgmod.set_autostart(want, exe)
        self.config.autostart = want
        self.config.save()
        self.toast("每次开机，这扇窗都会自己打开" if want else "已取消开机自启")

    def _act_about(self, *_):
        version_text = __version__
        if self.available_release is not None:
            version_text = f"{__version__}（有新版本 {self.available_release.tag}）"
        about = Gtk.AboutDialog(
            transient_for=self, modal=True,
            program_name="窗 · Chuang", version=version_text,
            logo_icon_name="chuang",
            comments="把你头顶此刻真实的天空，搬到桌面的一扇窗里。\n\n"
                     "太阳、月亮、星星的位置由本地天文算法计算，"
                     "云、雨、雪来自 Open-Meteo 的真实天气。\n"
                     "没有任何内容离开这台电脑。")
        about.present()

    def _set_location(self, location: cfgmod.Location):
        self.config.location = location
        self.config.save()
        self.engine.set_location(location.lat, location.lon, location.timezone)
        self.weather.set_location(location.lat, location.lon, location.timezone)
        self._scene_key = None
        self._ribbon_key = None
        self.painter._skyline.clear()
        self.painter.ui.preview_dt = None
        self.title_widget.set_subtitle(location.label)
        self.toast(f"现在这扇窗朝着 {location.full_label or location.name}")
        self.area.queue_draw()

    # ------------------------------------------------------------------
    # 场景与绘制
    # ------------------------------------------------------------------
    def _now(self):
        if self._fake_time is not None:
            return self._fake_time
        return self.engine.local_now()

    def _has_precip(self) -> bool:
        sc = self._scene
        return bool(sc is not None and sc.precip_kind != "none" and sc.precip_strength > 0)

    def _current_scene(self):
        ui = self.painter.ui
        now = self._now()
        when = ui.preview_dt if ui.preview_dt else now
        key = (when.strftime("%Y-%m-%d %H:%M"),
               getattr(self.weather.weather, "fetched_at", 0),
               self.config.location.name)
        if key != self._scene_key or self._scene is None:
            self._scene = self.engine.build(
                when, self.weather.weather,
                preview=ui.preview_dt is not None,
                location_label=self.config.location.label)
            self._scene_key = key
        return self._scene

    def _refresh_ribbon(self, scene):
        day = scene.when.replace(hour=0, minute=0, second=0, microsecond=0)
        key = (day.strftime("%Y-%m-%d"),
               getattr(self.weather.weather, "fetched_at", 0),
               self.config.location.name)
        if key == self._ribbon_key:
            return
        self._ribbon_key = key
        self.painter.ui.ribbon = self.engine.ribbon(day, self.weather.weather)
        self.painter.ui.ribbon_key = key
        self.painter.ui.ribbon_surface = None

    def _on_draw(self, _area, cr, w, h):
        scene = self._current_scene()
        self._refresh_ribbon(scene)
        az0 = 180.0 if self.config.location.lat >= 0 else 0.0
        self.painter.draw(cr, w, h, scene, az0)
        self.painter.draw_chip(cr, w, h)

    def _tick(self):
        import time as _t
        now = _t.monotonic()
        clock = self._now()
        minute = clock.hour * 60 + clock.minute
        if minute != self._last_minute:
            self._last_minute = minute
            self._scene_key = None
            if self.get_visible():
                self.title_widget.set_subtitle(
                    f"{self.config.location.label} · {clock.strftime('%H:%M')}")
            self.weather.maybe_refresh()
        # 壁纸跟随：按秒表走，不受"分钟变化"限制（收进托盘也照常更新）
        if self.config.wallpaper_auto and now >= self._next_wallpaper:
            self._next_wallpaper = now + WALLPAPER_INTERVAL
            self.apply_wallpaper(quiet=True)
        # 画面只在窗口可见时重绘；有降水画得勤一点，安静时省电
        if self.get_visible():
            interval = 0.07 if self._has_precip() else 0.10
            if not self.is_active():
                interval *= 5
            if now >= self._next_draw:
                self._next_draw = now + interval
                self.area.queue_draw()
        return True

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------
    def _over_ribbon(self, x, y) -> bool:
        x0, y0, rw, rh = self.painter.ui.ribbon_rect
        if rw <= 0:
            return False
        return x0 - 6 <= x <= x0 + rw + 6 and y0 - 22 <= y <= y0 + rh + 16

    def _on_motion(self, _c, x, y):
        ui = self.painter.ui
        if ui.dragging:
            t = self.painter.ribbon_time_at(ui, x)
            if t is not None:
                ui.preview_dt = t
                self.area.queue_draw()
            return
        over = self._over_ribbon(x, y)
        self.area.set_cursor_from_name("ew-resize" if over else None)
        t = self.painter.ribbon_time_at(ui, x) if over else None
        if t != ui.hover_dt:
            ui.hover_dt = t
            self.area.queue_draw()

    def _on_leave(self, *_):
        self.painter.ui.hover_dt = None
        self.area.queue_draw()

    def _on_press(self, gesture, _n, x, y):
        ui = self.painter.ui
        cx, cy, cw, ch = ui.chip_rect
        if cw > 0 and cx <= x <= cx + cw and cy <= y <= cy + ch:
            ui.preview_dt = None
            ui.dragging = False
            self._scene_key = None
            self.area.queue_draw()
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            return
        if self._over_ribbon(x, y):
            ui.dragging = True
            t = self.painter.ribbon_time_at(ui, x)
            if t is not None:
                ui.preview_dt = t
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.area.queue_draw()

    def _on_release(self, _g, _n, _x, _y):
        self.painter.ui.dragging = False

    def _on_scroll(self, _c, _dx, dy):
        ui = self.painter.ui
        if ui.preview_dt is None and ui.hover_dt is None:
            return False
        from datetime import timedelta
        base = ui.preview_dt or ui.hover_dt or self._now()
        step = 10 if dy > 0 else -10
        ui.preview_dt = base + timedelta(minutes=step)
        self._scene_key = None
        self.area.queue_draw()
        return True

    def _on_key(self, _c, keyval, _code, _state):
        ui = self.painter.ui
        name = Gdk.keyval_name(keyval)
        from datetime import timedelta
        if name == "space":
            ui.show_info = not ui.show_info
            self.area.queue_draw()
            return True
        if name == "Escape":
            if ui.preview_dt is not None:
                ui.preview_dt = None
                self._scene_key = None
                self.area.queue_draw()
                return True
            if self.is_fullscreen():
                self.unfullscreen()
                return True
            return False
        if name in ("Home", "KP_Home"):
            ui.preview_dt = None
            self._scene_key = None
            self.area.queue_draw()
            return True
        if name in ("Left", "Right", "KP_Left", "KP_Right"):
            base = ui.preview_dt or self._now()
            step = -10 if "Left" in name else 10
            ui.preview_dt = base + timedelta(minutes=step)
            self._scene_key = None
            self.area.queue_draw()
            return True
        return False

    # ------------------------------------------------------------------
    def _on_weather(self, weather):
        self._scene_key = None
        self._ribbon_key = None
        self.area.queue_draw()

    def toast(self, text: str, seconds: float = 3.0):
        self.painter.ui.toast = text
        self.painter.ui.toast_until = _time.time() + seconds
        self.area.queue_draw()

    def first_run_tips(self):
        if self.config.first_run_done:
            return
        self.config.first_run_done = True
        if self.config.location.guessed:
            self.toast(f"按你所在时区推测为「{self.config.location.name}」"
                       f" · 菜单里可以换城市", 7.0)
        else:
            self.toast("空格 隐藏信息 · 拖动底部长卷可以预览今天的天色", 7.0)
        self.config.save()

    def _on_close(self, *_):
        try:
            self.config.window_w = self.get_width()
            self.config.window_h = self.get_height()
            self.config.save()
        except Exception:
            pass
        if self._really_quit:
            return False
        behavior = self.config.close_behavior
        if behavior == "quit":
            return False
        if behavior == "tray":
            if self.tray is not None and self.tray.available:
                self.set_visible(False)
                return True
            return False
        # 还没选过：问一次
        self._ask_close()
        return True


class ChuangApp(Adw.Application):
    def __init__(self, force_city: bool = False):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.config = cfgmod.Config.load()
        if not self.config.location.name:
            self.config.location = cfgmod.guess_location()
            self.config.save()
        self.force_city = force_city
        if cfgmod.autostart_installed() != self.config.autostart:
            self.config.autostart = cfgmod.autostart_installed()

    def installed_exec(self) -> str:
        import os
        import sys
        if os.path.exists("/usr/local/bin/chuang"):
            return "/usr/local/bin/chuang"
        exe = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "chuang-gui")
        return f"{sys.executable} {exe}"

    def do_activate(self):
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        win = self.props.active_window
        if win is None:
            win = ChuangWindow(self)
        win.present()
        if self.force_city:
            GLib.timeout_add(500, lambda: (win._act_city(), False)[1])


def run(argv=None) -> int:
    app = ChuangApp()
    return app.run(argv or [])
