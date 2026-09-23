"""窗 · Chuang —— GTK4 主程序。"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
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

# 默认的壁纸跟随间隔（秒）；用户可在菜单里改成 30 秒 / 1 分钟
WALLPAPER_INTERVAL = 10
# 每隔这么久做一次"换名字接管"（常态是就地更新正在显示的那张文件；
# 偶尔真的换一次 URI，兜底那种"shell 的监听链路断了"的极端情况）
FLIP_EVERY = 300.0
# 单实例锁：同一时刻只允许一个「窗」在写壁纸，避免两个进程互相覆盖
INSTANCE_LOCK = Path.home() / ".cache" / "chuang" / "instance.lock"

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


def _other_chuang_processes() -> list:
    """除了自己，系统里还有哪些「窗」进程（用于诊断"是不是有残留进程"）。"""
    out = []
    me = os.getpid()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == me:
            continue
        try:
            argv = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        argv = [a.decode("utf-8", "replace") for a in argv if a]
        # 只看真正的启动命令（python3 /usr/bin/chuang、chuang-gui…），
        # 别把 "gsettings set … sky-a.png" 这种子进程也算进来
        if any(os.path.basename(a) in ("chuang", "chuang-gui") for a in argv[:2]):
            out.append((pid, " ".join(argv[:3])))
    return out


def escape_closes(window: Gtk.Window) -> None:
    """按 Esc 关掉这个对话框（GTK4 的裸窗口默认不认 Esc）。"""
    keys = Gtk.EventControllerKey()

    def on_key(_c, keyval, _code, _state):
        if Gdk.keyval_name(keyval) == "Escape":
            window.destroy()
            return True
        return False

    keys.connect("key-pressed", on_key)
    window.add_controller(keys)


class CloseDialog(Gtk.Window):
    """第一次关窗时问一句：留在托盘，还是直接退出。（GTK4 4.6 没有
    MessageDialog.set_extra_child，所以自己搭一个。）"""

    def __init__(self, parent, tray_available: bool, on_choice,
                 wallpaper_auto: bool = False):
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
        if wallpaper_auto:
            hint = ("你的桌面壁纸正跟着此刻更新。留在托盘里它才会一直更新；"
                    "直接退出的话，壁纸会停在现在这一刻。托盘图标点一下就能再打开。")
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
        quit_button = Gtk.Button(label="直接退出（壁纸会停住）" if wallpaper_auto else "直接退出")
        quit_button.connect("clicked", self._pick, "quit")
        row.append(quit_button)
        row.append(self.tray_button)
        box.append(row)
        self.set_child(box)

    def _pick(self, _btn, choice: str):
        self.on_choice(choice, self.remember.get_active())
        self.destroy()


class DetailDialog(Gtk.Window):
    """把一段话完整、可选中、可复制地摆出来。

    toast（屏幕下方那条提示）只能看不能抄，所以凡是"路径 / 命令 / 报错"
    这类需要照着办或贴到 issue 里的内容，都放这个窗里。
    """

    def __init__(self, parent, title: str, body: str, copy_label: str = "复制全部"):
        super().__init__(transient_for=parent, modal=True, title=title,
                         default_width=560, default_height=340)
        self.body = body
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(
            title=title, subtitle="可以选中，也可以一键复制"))
        box.append(header)

        view = Gtk.TextView(editable=False, cursor_visible=False,
                            wrap_mode=Gtk.WrapMode.WORD_CHAR, monospace=True,
                            top_margin=10, bottom_margin=10,
                            left_margin=12, right_margin=12)
        view.get_buffer().set_text(body)
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_child(view)
        box.append(scroller)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                      margin_top=10, margin_bottom=12,
                      margin_start=12, margin_end=12)
        row.set_halign(Gtk.Align.END)
        self.copy_button = Gtk.Button(label=copy_label)
        self.copy_button.add_css_class("suggested-action")
        self.copy_button.connect("clicked", self._copy)
        close = Gtk.Button(label="关闭")
        close.connect("clicked", lambda *_: self.destroy())
        row.append(self.copy_button)
        row.append(close)
        box.append(row)
        self.set_child(box)
        escape_closes(self)

    def _copy(self, _btn=None):
        display = Gdk.Display.get_default()
        if display is not None:
            display.get_clipboard().set(self.body)
            self.copy_button.set_label("已复制 ✓")


class ChoiceDialog(Gtk.Window):
    """一个简单的问题：给几个按钮，选哪个就回调哪个。"""

    def __init__(self, parent, title: str, body: str, options):
        super().__init__(transient_for=parent, modal=True, title=title,
                         resizable=False)
        self.on_choice = None
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=18, margin_bottom=16,
                      margin_start=20, margin_end=20)
        heading = Gtk.Label(xalign=0)
        heading.set_markup(f"<b>{GLib.markup_escape_text(title)}</b>")
        box.append(heading)
        label = Gtk.Label(label=body, xalign=0, wrap=True, max_width_chars=44)
        label.add_css_class("dim-label")
        box.append(label)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_halign(Gtk.Align.END)
        for text, value, suggested in options:
            btn = Gtk.Button(label=text)
            if suggested:
                btn.add_css_class("suggested-action")
            btn.connect("clicked", self._pick, value)
            row.append(btn)
        box.append(row)
        self.set_child(box)
        escape_closes(self)

    def _pick(self, _btn, value):
        self.destroy()
        if self.on_choice is not None:
            self.on_choice(value)


class TimeTravelDialog(Gtk.Window):
    """跳到任意一天任意一刻（拖长卷只能左右挪，这里可以一步到位）。"""

    def __init__(self, parent, when, tzinfo, on_pick, on_now):
        super().__init__(transient_for=parent, modal=True, title="跳到某一刻",
                         default_width=380, default_height=430)
        self.tzinfo = tzinfo
        self.on_pick = on_pick
        self.on_now = on_now

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(
            title="跳到某一刻", subtitle="选好日期与时间，窗会停在那里"))
        box.append(header)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                        margin_top=10, margin_bottom=14,
                        margin_start=16, margin_end=16)

        self.calendar = Gtk.Calendar()
        self.calendar.set_show_week_numbers(False)
        try:
            self.calendar.select_day(GLib.DateTime.new_local(
                when.year, when.month, when.day, 12, 0, 0.0))
        except Exception:
            pass
        inner.append(self.calendar)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.append(Gtk.Label(label="时间"))
        self.hour = Gtk.SpinButton.new_with_range(0, 23, 1)
        self.hour.set_value(when.hour)
        self.minute = Gtk.SpinButton.new_with_range(0, 59, 1)
        self.minute.set_value(when.minute)
        row.append(self.hour)
        row.append(Gtk.Label(label=":"))
        row.append(self.minute)
        inner.append(row)

        hint = Gtk.Label(xalign=0, wrap=True, max_width_chars=36,
                         label="提示：预览时窗里的天色停在你选的那一刻，"
                               "但街上的人车、天上的云照常动。")
        hint.add_css_class("dim-label")
        inner.append(hint)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        actions.set_halign(Gtk.Align.END)
        now_btn = Gtk.Button(label="回到此刻")
        now_btn.connect("clicked", self._now)
        cancel = Gtk.Button(label="取消")
        cancel.connect("clicked", lambda *_: self.destroy())
        go = Gtk.Button(label="跳到这一刻")
        go.add_css_class("suggested-action")
        go.connect("clicked", self._go)
        for b in (now_btn, cancel, go):
            actions.append(b)
        inner.append(actions)

        box.append(inner)
        self.set_child(box)
        escape_closes(self)

    def _picked(self):
        day = self.calendar.get_date()
        from datetime import datetime
        return datetime(day.get_year(), day.get_month(), day.get_day_of_month(),
                        int(self.hour.get_value()), int(self.minute.get_value()),
                        tzinfo=self.tzinfo)

    def _go(self, _btn=None):
        self.on_pick(self._picked())
        self.destroy()

    def _now(self, _btn=None):
        self.on_now()
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
        escape_closes(self)

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
        self._installing = False
        self._wallpaper_set_by_us = False   # 本进程有没有成功把壁纸换成我们的
        self._tick_errors = 0               # 心跳里兜住的异常次数
        self._last_tick_error = ""
        self._last_wallpaper_at = 0.0
        self._last_wallpaper_ok = None
        self._last_wallpaper_msg = ""
        self._last_flip_at = _time.time()   # 上次真正换过壁纸 URI 的时刻
        self._toggle_handlers = {}          # 勾选项名字 → 真正的处理器（便于"设为"某状态）
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
        # 壁纸立刻来一次：开机后桌面还挂着"上次关机那一刻"的图，
        # 越早换掉越好（以前要等 2.5 秒，而且只有一次机会）。
        self._next_wallpaper = _time.monotonic() + 0.4
        self._next_wallpaper_check = _time.monotonic() + 2.0
        self._last_clock = self._now()
        self._timer = GLib.timeout_add(25, self._tick)
        self.connect("close-request", self._on_close)
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

        # 快捷键接在窗口的**捕获阶段**上：事件自窗口往下走时就先被这里拿走，
        # 焦点落在标题栏那颗"图钉"或菜单按钮上时，空格也不会被按钮当"激活"吃掉。
        #
        # 这里踩过的坑：GTK 4.6 上 ShortcutController 的 MANAGED / GLOBAL 作用域
        # 对送到窗口的按键**不会触发**（实测只有 LOCAL 会），所以别用它来抢救
        # 快捷键；老老实实用 EventControllerKey + 捕获阶段，并在开窗时把焦点交给
        # 画面本身（否则默认焦点可能停在标题栏的按钮上，空格按下去像是在按按钮）。
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.add_controller(keys)
        self.connect("map", lambda *_: (self.area.grab_focus(), False)[1])

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
        # 鼠标点标题栏的按钮不该把键盘焦点从画面上抢走：
        # 否则点过一次图钉，之后按空格就变成"再按一次图钉"，卡片反而不动了。
        self.pin_button.set_focus_on_click(False)
        header.pack_end(self.pin_button)

        menu = self._build_menu()
        self.menu_model = menu
        self.menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic",
                                          menu_model=menu, tooltip_text="更多")
        self.menu_button.set_focus_on_click(False)
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
        """菜单：顶层只留"一眼能看懂"的几件事，其余按用途收进子菜单。

        以前的顶层有十几行（含一组三个单选），太长；现在顶层固定 6 行：
        换一扇窗 / 跟随天气 / 桌面壁纸 ▸ / 看 ▸ / 开机与关窗 ▸ / 关于与帮助 ▸ / 退出。
        """
        menu = Gio.Menu()

        # 有新版本时置顶提示
        if self.available_release is not None:
            rel = self.available_release
            up = Gio.Menu()
            if rel.deb_url:
                up.append(f"下载并安装 {rel.tag}", "win.installdeb")
                up.append("只下载 .deb 安装包", "win.downloaddeb")
            up.append("打开发布页", "win.openreleases")
            up.append("跳过这个版本", "win.skipversion")
            menu.append_submenu(f"有新版本 {rel.tag} · 查看", up)

        # 一、最常用的两件事：看哪片天、点开全屏
        primary = Gio.Menu()
        primary.append("换一扇窗（城市）…", "win.city")
        primary.append("跟随真实天气", "win.weather")
        primary.append("沉浸全屏", "win.fullscreen")
        menu.append_section(None, primary)

        # 二、桌面壁纸（"放到桌面上"的所有开关）
        wall = Gio.Menu()
        wall.append("把此刻的天空设为壁纸（一张）", "win.wallpaper")
        wall.append("壁纸跟随此刻（定时换一张）", "win.wallpaperauto")
        w2 = Gio.Menu()
        for label, target in (("每 10 秒", "10"), ("每 30 秒", "30"), ("每 1 分钟", "60")):
            item = Gio.MenuItem.new(label, "win.wallpaperinterval")
            item.set_attribute_value("target", GLib.Variant("s", target))
            w2.append_item(item)
        wall.append_submenu("壁纸跟随的节奏", w2)
        wall.append("生成离线动态壁纸（15 分钟一帧，关掉也有效）", "win.wallpaperday")
        w3 = Gio.Menu()
        w3.append("壁纸上显示「此刻的事实」", "win.wallpaperinfo")
        w3.append("壁纸上显示「今日天色」长卷", "win.wallpaperribbon")
        w3.append("还原成原来的壁纸", "win.wallpaperrestore")
        w3.append("壁纸诊断（时间不对时点这里）…", "win.wallpaperdiag")
        wall.append_section(None, w3)
        menu.append_submenu("桌面壁纸", wall)

        # 三、看：这扇窗自己长什么样
        look = Gio.Menu()
        look.append("窗口置顶", "win.pin")
        look.append("显示此刻的事实（空格）", "win.info")
        look.append("跳到某天某时…", "win.gotodatetime")
        menu.append_submenu("看", look)

        # 四、开机与关窗：三个"待着的方式"收在一起
        behave = Gio.Menu()
        behave.append("开机时自动打开", "win.autostart")
        behave.append("开机时直接进托盘（不弹窗）", "win.autostarthidden")
        for label, target in (("关窗时：问我", "ask"),
                              ("关窗时：最小化到托盘", "tray"),
                              ("关窗时：直接退出", "quit")):
            item = Gio.MenuItem.new(label, "win.closebehavior")
            item.set_attribute_value("target", GLib.Variant("s", target))
            behave.append_item(item)
        menu.append_submenu("开机与关窗", behave)

        # 五、关于与帮助
        about = Gio.Menu()
        about.append("检查更新", "win.checkupdate")
        about.append("自动检查更新", "win.autoupdate")
        about.append("问题反馈 / 提个建议（GitHub）", "win.reportissue")
        about.append("作者的主页（GitHub）", "win.authormain")
        about.append("关于窗", "win.about")
        about.append("重新启动「窗」", "win.restart")
        menu.append_submenu("关于与帮助", about)

        quit_item = Gio.Menu()
        quit_item.append("退出", "win.quit")
        menu.append_section(None, quit_item)
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
            self._toggle_handlers[name] = on_set

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
        add("wallpaperdiag", self._act_wallpaper_diag)
        add("show", self._act_show)
        add("checkupdate", self._act_check_update)
        add("openreleases", self._act_open_releases)
        add("downloaddeb", self._act_download_deb)
        add("skipversion", self._act_skip_version)
        add("reportissue", self._act_report_issue)
        add("authormain", self._act_author_main)
        add("installdeb", self._act_install_deb)
        add("gotodatetime", self._act_goto_datetime)
        add("restart", self._act_restart)
        add("quit", self._act_quit)
        add("about", self._act_about)

        add_toggle("pin", self.config.always_on_top, self._act_pin)
        add_toggle("weather", self.config.mirror_weather, self._act_weather)
        add_toggle("info", self.painter.ui.show_info, self._act_info)
        add_toggle("autostart", self.config.autostart, self._act_autostart)
        add_toggle("autostarthidden", self.config.autostart_hidden,
                   self._act_autostart_hidden)
        add_toggle("wallpaperauto", self.config.wallpaper_auto, self._act_wallpaper_auto)
        add_toggle("wallpaperinfo", self.config.wallpaper_show_info,
                   self._act_wallpaper_info)
        add_toggle("wallpaperribbon", self.config.wallpaper_show_ribbon,
                   self._act_wallpaper_ribbon)
        # 这个以前漏了注册：菜单里有「自动检查更新」，点了却没有任何反应
        add_toggle("autoupdate", self.config.update_check, self._act_auto_update)
        add_radio("closebehavior", self.config.close_behavior, self._act_close_behavior)
        add_radio("wallpaperinterval", str(self.config.wallpaper_interval),
                  self._act_wallpaper_interval)

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
            self.set_toggle("pin", active)

    def action_state(self, name: str) -> bool:
        act = self.lookup_action(name)
        if act is None:
            return False
        state = act.get_state()
        return bool(state.get_boolean()) if state is not None else False

    def set_toggle(self, name: str, value: bool) -> None:
        """把勾选项"设为"某个状态（而不是"翻转"）。

        勾选项的动作参数类型是 None，所以 activate(某个 Variant) 会被 GLib
        断言挡掉、静默什么都不做（AGENTS.md 里记的那条坑）；而 set_state() 又
        不会触发 change-state。于是这里两步都做：先改状态（菜单与托盘的勾跟着
        变），再显式跑一遍处理器（把配置、壁纸、提示都落实）。
        """
        act = self.lookup_action(name)
        if act is None:
            return
        state = act.get_state()
        if state is not None and state.get_boolean() == value:
            return
        act.set_state(GLib.Variant.new_boolean(value))
        handler = self._toggle_handlers.get(name)
        if handler is not None:
            handler(value)

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
        """第一次动壁纸前，把原来那张记下来，方便还原。

        只记"别人的"壁纸：如果此刻挂着的已经是我们自己画的槽位文件，
        就绝不能把它当成"原来的壁纸"——否则「还原成原来的壁纸」还回去的
        是一张过期的天空，用户真正的壁纸就永久丢了。
        """
        if self.config.prev_wallpaper:
            return
        light, dark = wallmod.current_uris()
        if wallmod.is_our_uri(light) or not light:
            light = ""
        if wallmod.is_our_uri(dark) or not dark:
            dark = light
        self.config.prev_wallpaper = light
        self.config.prev_wallpaper_dark = dark or light
        self.config.save()

    def apply_wallpaper(self, quiet: bool = False) -> None:
        if self.wp.busy:
            return
        self._remember_wallpaper()
        show_info, show_ribbon = self._wallpaper_opts()
        size = wallmod.screen_size()
        loc = self.config.location
        self.wp.location(loc.lat, loc.lon, loc.timezone, loc.label)
        # 写哪一张？**写桌面此刻正在显示的那一张**。
        #
        # gnome-shell 把壁纸的解码结果按文件缓存，而且只监听"当前显示的那个
        # 文件"：它有变化才会 purge + 重读。反过来，如果我们写的是另一张、
        # 再把 URI 切过去，shell 会拿"这张文件上一次的解码结果"直接显示——
        # 桌面上就会出现这个文件**上一版/上几版**的画面（时间也就是旧的）。
        # 所以常态是就地更新；只有"接管"或偶尔兜底时才真的换一次文件名。
        shown = wallmod.shown_slot()
        due_flip = (_time.time() - self._last_flip_at) > FLIP_EVERY
        if shown >= 0 and not due_flip:
            slot, adopt = shown, False
        else:
            other = 1 - (self.config.wallpaper_slot % 2)
            slot = (1 - shown) if shown >= 0 else other
            adopt = True
            self._last_flip_at = _time.time()
        self.wp.render_now(self.engine.local_now(), self.weather.weather,
                           show_info, show_ribbon, size, slot,
                           lambda ok, msg, slot: self._wallpaper_done(ok, msg, slot, quiet),
                           adopt=adopt)
        if not quiet:
            self.toast("正在把这扇窗挂到桌面上…", 2.0)

    def _wallpaper_done(self, ok: bool, msg: str, slot: int, quiet: bool = False) -> bool:
        self.config.wallpaper_slot = slot
        self.config.save()          # 记下槽位，只在"桌面挂着别人的图"时当兜底
        self._wallpaper_set_by_us = bool(ok)
        self._last_wallpaper_at = _time.time()
        self._last_wallpaper_ok = bool(ok)
        self._last_wallpaper_msg = msg
        # 自动跟随不弹提示，否则提示会一直挂在屏幕上
        if not quiet or not ok:
            self.toast(msg, 4.5 if ok else 6.0)
        return False

    def _act_wallpaper(self, *_):
        self.apply_wallpaper()

    def _act_wallpaper_auto(self, want: bool):
        self.config.wallpaper_auto = want
        if want:
            self.config.wallpaper_dynamic = False    # 两条路互斥，别同时挂着
        self.config.save()
        self._next_wallpaper = _time.monotonic() + self.config.wallpaper_interval
        if want:
            self.apply_wallpaper()
            self.toast(f"壁纸每 {self.config.wallpaper_interval} 秒跟着此刻换一张"
                       "（要一直更新，记得把「窗」留在托盘里）", 6.0)
        else:
            self.toast("壁纸不再自动更新，现在这张会留着", 4.0)

    def _act_wallpaper_interval(self, value: str):
        try:
            seconds = int(value)
        except (TypeError, ValueError):
            return
        if seconds not in cfgmod.WALLPAPER_INTERVALS:
            return
        self.config.wallpaper_interval = seconds
        self.config.save()
        self._next_wallpaper = _time.monotonic() + seconds
        text = {10: "10 秒", 30: "30 秒", 60: "1 分钟"}[seconds]
        self.toast(f"壁纸会每 {text}跟着此刻换一张" if self.config.wallpaper_auto
                   else f"壁纸跟随的节奏已设为 {text}")

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
            # 先把「跟随此刻」的勾去掉（顺带改配置、存盘），再挂上动态壁纸
            self.set_toggle("wallpaperauto", False)
            self.config.wallpaper_dynamic = True
            self.config.save()
            self.toast("动态壁纸做好了：今天 24 小时会自己走一遍，不开着也有效", 7.0)
        else:
            self.toast(msg, 6.0)
        return False

    def _act_wallpaper_restore(self, *_):
        if not self.config.prev_wallpaper:
            self.toast("没有记下你原来的壁纸；可以从「设置 → 外观」里挑一张", 5.0)
            return
        ok, msg = wallmod.restore(self.config.prev_wallpaper,
                                  self.config.prev_wallpaper_dark)
        if ok:
            self.set_toggle("wallpaperauto", False)
            self.config.wallpaper_dynamic = False
            self.config.prev_wallpaper = ""
            self.config.prev_wallpaper_dark = ""
            self.config.save()
        self.toast(msg, 5.0)

    def _act_wallpaper_diag(self, *_):
        """把"壁纸/时间到底怎么了"摊开成一段可复制的文字。

        这类毛病是"有时候"发生的，光靠转述很难查；发生的那一刻点一下这里，
        把内容贴出来就够了。
        """
        self._show_detail("壁纸诊断（可以整段复制）", self._diagnostics_wallpaper())

    def _diagnostics_wallpaper(self) -> str:
        import time as _t
        lines = [f"窗 · Chuang {__version__} 壁纸诊断",
                 _t.strftime("%Y-%m-%d %H:%M:%S", _t.localtime()), "─" * 34]
        others = _other_chuang_processes()
        lines.append(f"本进程 pid={os.getpid()}，单实例锁："
                     f"{'持有' if globals().get('_INSTANCE_HANDLE') else '未持有'}")
        lines.append(f"系统里其它「窗」进程：{len(others)} 个"
                     + ("（" + "; ".join(f"{p}: {c}" for p, c in others) + "）" if others else ""))
        lines.append(f"代码版本 {__version__}；dpkg 里装的是 "
                     f"{upmod.installed_deb_version() or '（不是 .deb 安装）'}")
        lines.append(f"壁纸跟随：{'开' if self.config.wallpaper_auto else '关'}"
                     f"（间隔 {self.config.wallpaper_interval} 秒）；"
                     f"托盘：{'可用' if (self.tray and self.tray.available) else '不可用'}")
        lines.append("")

        shown = wallmod.shown_uri()
        shown_slot = wallmod.shown_slot()
        mtimes = wallmod.slot_mtimes()
        now = _t.time()
        if shown_slot < 0:
            lines.append(f"桌面此刻挂的不是「窗」画的图：{shown or '（读不到）'}")
        else:
            lines.append(f"桌面此刻挂的是：{wallmod.SLOTS[shown_slot].name}"
                         f"（{now - mtimes[shown_slot]:.1f} 秒前写的）")
            other = 1 - shown_slot
            lines.append(f"另一张 {wallmod.SLOTS[other].name}："
                         f"{now - mtimes[other]:.1f} 秒前写的")
            stale = wallmod.stale_shown_slot()
            lines.append("桌面显示的是不是最新那张："
                         + ("**不是**（应该顶上 " + wallmod.SLOTS[stale].name + "）"
                            if stale is not None else "是"))
        lines.append("")
        if self._last_wallpaper_at:
            ago = _t.time() - self._last_wallpaper_at
            lines.append(f"最近一次换图：{ago:.0f} 秒前 → "
                         f"{'成功' if self._last_wallpaper_ok else '失败'}（{self._last_wallpaper_msg}）")
        else:
            lines.append("最近一次换图：本次启动以来还没有过")
        lines.append(f"上次真正换过文件名（URI）：{_t.time() - self._last_flip_at:.0f} 秒前"
                     f"（常态是就地更新正在显示的那张，每 {int(FLIP_EVERY)} 秒兜底换一次）")
        lines.append(f"心跳：本进程共兜住 {self._tick_errors} 次异常"
                     + (f"；最近一次：{self._last_tick_error}" if self._last_tick_error else ""))
        lines.append("")
        lines.append("（如果上面写着「桌面显示的不是最新那张」，那就是桌面没接受新图；"
                     "如果「最近一次换图」停在很久以前，说明心跳停了。）")
        return "\n".join(lines)

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
        if rel is None:
            # 只在真的拿到结果时才记时间戳，否则断网一次就要等一整天才会再问
            if manual:
                self.toast("检查更新失败：网络或 GitHub 接口不可用，稍后会再试", 5.0)
            return False
        self.config.last_update_check = _time.time()
        self.config.save()
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
        self._open_url(url, "已经在浏览器里打开了发布页")

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
            self.toast_detailed(
                "安装包已经下好了", 14.0,
                f"安装包：{info}\n\n手动安装（复制到终端里跑）：\n"
                f"    sudo apt-get install -y -- \"{info}\"\n\n"
                "或者在菜单里点「下载并安装」，「窗」会自己开一个终端帮你装。")
        else:
            self.toast_detailed("下载失败", 8.0,
                                f"下载失败：{info}\n\n发布页：{upmod.RELEASES_URL}")
        return False

    # ------------------------------------------------------------------
    # 下载并直接安装新版本
    # ------------------------------------------------------------------
    def _act_install_deb(self, *_):
        """下载新版本的 .deb，然后开一个终端用 sudo 装好（会弹密码）。"""
        rel = self.available_release
        if rel is None or not rel.deb_url:
            self.toast("没找到可下载的安装包，我给你打开发布页", 5.0)
            self._act_open_releases()
            return
        if getattr(self, "_installing", False):
            self.toast("上一次安装还没结束，稍等一下…", 3.0)
            return
        self._installing = True
        target = wallmod.CACHE / "updates" / (rel.deb_name or "chuang-update.deb")
        self.toast(f"正在下载 {rel.deb_name}…（大约几 MB）", 5.0)

        def worker():
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                req = urllib.request.Request(rel.deb_url,
                                             headers={"User-Agent": upmod.UA})
                with urllib.request.urlopen(req, timeout=180) as resp, \
                        open(target, "wb") as fh:
                    shutil.copyfileobj(resp, fh)
                GLib.idle_add(self._install_ready, rel, str(target))
            except Exception as exc:
                GLib.idle_add(self._install_download_failed, rel, str(exc))

        threading.Thread(target=worker, daemon=True, name="chuang-install").start()

    def _install_download_failed(self, rel, why: str) -> bool:
        self._installing = False
        self.toast_detailed("下载安装包失败", 9.0,
                            f"下载 {rel.tag} 的安装包失败：{why}\n\n"
                            f"可以到发布页手动下载：{upmod.RELEASES_URL}")
        return False

    def _install_ready(self, rel, path: str) -> bool:
        launcher = self._installer_launcher(path)
        if launcher is None:
            self._installing = False
            self.toast_detailed(
                "没有可用的安装方式", 12.0,
                "没找到终端程序，也没法弹授权框。\n\n"
                f"安装包已经下载到：\n    {path}\n\n"
                "在终端里跑这一行就装好了：\n"
                f"    sudo apt-get install -y -- \"{path}\"")
            return False
        try:
            subprocess.Popen(launcher, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except Exception as exc:
            self._installing = False
            self.toast_detailed("打不开安装终端", 9.0,
                                f"启动安装程序失败：{exc}\n\n"
                                f"安装包在：{path}\n"
                                f"手动安装：sudo apt-get install -y -- \"{path}\"")
            return False
        self.toast(f"已经开了个终端在装 {rel.tag}，输入 sudo 密码就好", 8.0)
        self._watch_install(rel)
        return False

    @staticmethod
    def _installer_launcher(path: str):
        """怎么装：优先开终端跑 sudo（看得见过程、能输密码），否则返回 None。"""
        script = (
            "echo '窗 · Chuang —— 正在安装新版本'\n"
            "echo\n"
            f"sudo apt-get install -y -- {shlex.quote(path)}\n"
            "rc=$?\n"
            "echo\n"
            "if [ $rc -eq 0 ]; then\n"
            "  echo '✓ 安装完成。回到「窗」的窗口，它会问你要不要重启。'\n"
            "else\n"
            "  echo \"× 安装失败（退出码 $rc），上面的输出就是原因。\"\n"
            "fi\n"
            "echo\n"
            "printf '按回车关闭这个窗口… '\n"
            "read _\n")
        term = shutil.which("gnome-terminal")
        if term:
            return [term, "--title=安装「窗」更新", "--", "bash", "-c", script]
        term = shutil.which("x-terminal-emulator") or shutil.which("xterm")
        if term:
            return [term, "-e", "bash", "-c", script]
        return None

    def _watch_install(self, rel) -> None:
        """盯着 dpkg 里的版本号：装好了就提示重启。"""
        self._install_target = upmod.parse_version(rel.tag)
        self._install_deadline = _time.monotonic() + 240
        GLib.timeout_add(2000, self._poll_install)

    def _poll_install(self) -> bool:
        ver = upmod.installed_deb_version()
        parsed = upmod.parse_version(ver)
        if ver and parsed and not upmod.is_newer(self._install_target, parsed):
            self._installing = False
            self.available_release = None
            self._refresh_menu()
            self.toast(f"已经装好 {ver} 了", 8.0)
            dialog = ChoiceDialog(
                self, "新版本已经装好",
                f"现在是 {ver}。重启「窗」就能用上新版本（当前的窗口会关掉，"
                "壁纸最多停一两秒就接上）。",
                [("稍后", "later", False), ("现在重启", "restart", True)])
            dialog.on_choice = self._on_restart_answer
            dialog.present()
            return False
        if _time.monotonic() > self._install_deadline:
            # 大概率是用户在终端里放弃了；安静收场，别再打扰
            self._installing = False
            return False
        return True

    def _on_restart_answer(self, value: str):
        if value == "restart":
            self._restart_app()

    def _act_restart(self, *_):
        self._confirm_restart()

    def _confirm_restart(self):
        dialog = ChoiceDialog(self, "重启「窗」？",
                              "窗口会关掉再自动打开，壁纸最多停一两秒。",
                              [("取消", "later", False), ("现在重启", "restart", True)])
        dialog.on_choice = self._on_restart_answer
        dialog.present()

    def _restart_app(self):
        """退出自己，并在自己真正退出之后再拉起新的那个进程。

        必须等旧进程退出：单实例锁（flock）还握在手上，抢在它前面启动
        会被当成"第二个实例"而退场。
        """
        argv = self.app.installed_launcher()
        wait = f"while kill -0 {os.getpid()} 2>/dev/null; do sleep 0.2; done; "
        script = wait + "exec " + " ".join(shlex.quote(part) for part in argv)
        try:
            subprocess.Popen(["setsid", "sh", "-c", script],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except Exception as exc:
            self.toast_detailed("自动重启没成功", 8.0,
                                f"启动新进程失败：{exc}\n"
                                "手动打开一次「窗」就好（终端里敲 chuang）。")
            return
        self.config.save()
        self._really_quit = True
        self.app.quit()

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
        CloseDialog(self, tray_ok, self._on_close_answer,
                    wallpaper_auto=bool(self.config.wallpaper_auto)).present()

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
        cfgmod.set_autostart(want, self.app.installed_launcher(),
                             hidden=bool(self.config.autostart_hidden))
        self.config.autostart = want
        self.config.save()
        if want:
            self.toast("每次开机，这扇窗都会自己打开" if not self.config.autostart_hidden
                       else "每次开机，这扇窗会自己待在托盘里")
        else:
            self.toast("已取消开机自启")

    def _act_autostart_hidden(self, want: bool):
        self.config.autostart_hidden = want
        self.config.save()
        if self.config.autostart:
            cfgmod.set_autostart(True, self.app.installed_launcher(), hidden=want)
        self.toast("开机时直接进托盘，不弹窗" if want else "开机时正常打开窗口", 3.5)

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
                     "没有任何内容离开这台电脑。",
            website=upmod.AUTHOR_URL,
            website_label=f"{upmod.AUTHOR} · github.com/lsqkk",
            authors=[f"{upmod.AUTHOR}（lsqkk）"],
            copyright="© 2026 蓝色奇夸克",
            license_type=Gtk.License.MIT_X11)
        # 「有问题去这里说」的直达入口（GTK 4.6 才有的属性，取不到就算了）
        try:
            about.set_issue_url(upmod.ISSUES_URL)
        except Exception:
            pass
        about.present()

    def _act_author_main(self, *_):
        self._open_url(upmod.AUTHOR_URL, "已经在浏览器里打开了作者的主页")

    def _act_report_issue(self, *_):
        """问题反馈：直接打开仓库的"新建 issue"页，并预填环境信息。

        预填的内容只有版本号和系统环境，不含位置、不含任何个人数据——
        用户看得见、也能自己删。
        """
        from urllib.parse import quote, urlencode
        title = f"[Bug] {__version__} · "
        body = (
            "### 发生了什么\n\n（请把这句换成你看到的现象）\n\n"
            "### 怎么复现\n\n1. \n2. \n\n"
            "### 环境（自动填好，可删）\n\n" + self._diagnostics() + "\n"
        )
        url = upmod.NEW_ISSUE_URL + "?" + urlencode(
            {"title": title, "body": body}, quote_via=quote)
        self._open_url(url, "已经在浏览器里打开反馈页；把上面两句写清楚就好")

    def _diagnostics(self) -> str:
        """给 issue 用的一小段环境信息（纯本地读取，不外发）。"""
        lines = [f"- 版本：{__version__}"]
        try:
            lines.append(f"- GTK：{Gtk.get_major_version()}.{Gtk.get_minor_version()}."
                         f"{Gtk.get_micro_version()}")
        except Exception:
            pass
        try:
            import gi as _gi
            info = _gi.Repository.get_default().require("Adw", "1")
            lines.append(f"- libadwaita：{info.get_version()}")
        except Exception:
            pass
        lines.append(f"- 会话：{os.environ.get('XDG_SESSION_TYPE', '?')} / "
                     f"{os.environ.get('XDG_CURRENT_DESKTOP', '?')}")
        try:
            text = Path("/etc/os-release").read_text(encoding="utf-8")
            pretty = next((l.split("=", 1)[1].strip().strip('"')
                           for l in text.splitlines()
                           if l.startswith("PRETTY_NAME=")), "")
            if pretty:
                lines.append(f"- 系统：{pretty}")
        except OSError:
            pass
        lines.append(f"- 托盘：{'可用' if (self.tray and self.tray.available) else '不可用'}"
                     f"；壁纸跟随：{'开' if self.config.wallpaper_auto else '关'}")
        return "\n".join(lines)

    def _open_url(self, url: str, ok_msg: str) -> None:
        try:
            Gio.AppInfo.launch_default_for_uri(url, None)
            self.toast(ok_msg, 4.0)
        except Exception:
            self.toast(f"打不开浏览器，地址是 {url}", 8.0)

    def _set_location(self, location: cfgmod.Location):
        self.config.location = location
        self.config.save()
        self.engine.set_location(location.lat, location.lon, location.timezone)
        self.weather.set_location(location.lat, location.lon, location.timezone)
        self._scene_key = None
        self._ribbon_key = None
        self.painter._skyline.clear()
        self._set_preview(None)
        self.painter.ui.ribbon_key = None
        self.painter.ui.ribbon_surface = None
        self.title_widget.set_subtitle(location.label)
        self.toast(f"现在这扇窗朝着 {location.full_label or location.name}")
        self.area.queue_draw()
        # 桌面上的那张还朝着旧城市，别等下一个周期，立刻换掉
        if self.config.wallpaper_auto:
            self._next_wallpaper = 0.0
            self.apply_wallpaper(quiet=True)

    # ------------------------------------------------------------------
    # 场景与绘制
    # ------------------------------------------------------------------
    def _now(self):
        if self._fake_time is not None:
            return self._fake_time
        return self.engine.local_now()

    def _set_preview(self, when):
        """进入 / 更新 / 退出「时间旅行」预览。

        记下进入预览那一刻的墙钟：天色停在预览时刻，但街上的人车要继续走
        （见 render._draw_street）——云本来就是墙钟驱动的，人车也该一样。
        """
        ui = self.painter.ui
        if when is None:
            ui.preview_dt = None
            ui.preview_started = None
            ui.dragging = False
        else:
            if ui.preview_dt is None:
                ui.preview_started = self._now()
            ui.preview_dt = when
        self._scene_key = None
        self.area.queue_draw()

    def _act_goto_datetime(self, *_):
        """选一个日期 + 时刻跳过去（比来回拖长卷省事）。"""
        ui = self.painter.ui
        when = ui.preview_dt or self._now()
        TimeTravelDialog(self, when, self.engine._tzinfo,
                         self._set_preview, lambda: self._set_preview(None)).present()

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
        """定时心跳：重绘、刷新标题与壁纸、兜底自查。

        整个函数必须包在 try 里。PyGObject 里超时回调一旦抛异常，GLib 会
        **把这个定时器整个移除**——窗口和壁纸就永远停在那一刻（进程还在、
        托盘还在、菜单还能点，就是时间不再走）。这种"偶发一次就永久定格"
        正是"有时候左上角时间不对"最难查的来源，所以宁可吞掉异常也要活着。
        """
        try:
            return self._tick_body()
        except Exception as exc:                 # noqa: BLE001 - 就是要兜住一切
            import traceback
            self._last_tick_error = f"{type(exc).__name__}: {exc}"
            if self._tick_errors < 3:            # 只往日志里写前几次，别刷屏
                traceback.print_exc()
            self._tick_errors += 1
            return True

    def _tick_body(self) -> bool:
        import time as _t
        now = _t.monotonic()
        clock = self._now()
        # 睡了一觉 / 系统时间被改 / 从挂起里醒来：墙钟会跳。这时候画面和
        # 壁纸上的"此刻"都还是旧的，立刻补一次，别等下一个周期。
        if abs((clock - self._last_clock).total_seconds()) > 90:
            self._last_clock = clock
            self._scene_key = None
            self._last_minute = -1
            if self.config.wallpaper_auto:
                self._next_wallpaper = 0.0
        else:
            self._last_clock = clock
        minute = clock.hour * 60 + clock.minute
        if minute != self._last_minute:
            self._last_minute = minute
            self._scene_key = None
            # 标题上的时间要一直跟着走：以前只在窗口可见时更新，收进托盘
            # 再打开就会停在旧的一分钟上（看起来像"时间没刷新"）。
            self.title_widget.set_subtitle(
                f"{self.config.location.label} · {clock.strftime('%H:%M')}")
            self.weather.maybe_refresh()
        # 壁纸跟随：按秒表走，不受"分钟变化"限制（收进托盘也照常更新）
        if self.config.wallpaper_auto and now >= self._next_wallpaper:
            step = int(self.config.wallpaper_interval or WALLPAPER_INTERVAL)
            self._next_wallpaper = now + max(5, step)
            self.apply_wallpaper(quiet=True)
        # 兜底自查：桌面挂着的那张如果比另一张还旧，说明上一次换图没被接受
        # （URI 没变、GNOME 没重读、dconf 抽风……），立刻把新的那张顶上。
        if self.config.wallpaper_auto and now >= self._next_wallpaper_check:
            self._next_wallpaper_check = now + 5.0
            shown = wallmod.shown_uri()
            if shown and not wallmod.is_our_uri(shown):
                # 桌面挂着别人的图：多半是用户自己去「外观」里换了壁纸。
                # 那就别再抢（抢起来就是"两个东西打架"），把跟随关掉并说明。
                if self._wallpaper_set_by_us:
                    self._wallpaper_set_by_us = False
                    self.set_toggle("wallpaperauto", False)
                    self.toast_detailed(
                        "你换了壁纸，「窗」就不再自动跟着了", 9.0,
                        "检测到桌面壁纸已经换成别的了，所以「窗」停手，不再每 10 秒覆盖它。\n\n"
                        "想让它继续跟着此刻走：菜单 → 桌面壁纸 → 壁纸跟随此刻。\n"
                        "想保留刚才那张天空：不用做什么。")
            else:
                stale = wallmod.stale_shown_slot()
                if stale is not None:
                    ok, _ = wallmod.set_wallpaper(wallmod.SLOTS[stale])
                    if ok:
                        wallmod.touch(wallmod.SLOTS[stale])   # 逼 shell 丢掉旧解码
                    self._next_wallpaper = 0.0    # 顺手重画一张最新的
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
                self._set_preview(t)
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
        tx, ty, tw, th = ui.toast_rect
        if (ui.toast_detail and tw > 0
                and tx <= x <= tx + tw and ty <= y <= ty + th):
            self._show_detail("详情", ui.toast_detail)
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            return
        cx, cy, cw, ch = ui.chip_rect
        if cw > 0 and cx <= x <= cx + cw and cy <= y <= cy + ch:
            self._set_preview(None)
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            return
        if self._over_ribbon(x, y):
            ui.dragging = True
            t = self.painter.ribbon_time_at(ui, x)
            if t is not None:
                self._set_preview(t)
                ui.dragging = True          # _set_preview 会清掉拖动标记，这里补回来
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _on_release(self, _g, _n, _x, _y):
        self.painter.ui.dragging = False

    def _on_scroll(self, _c, _dx, dy):
        ui = self.painter.ui
        if ui.preview_dt is None and ui.hover_dt is None:
            return False
        from datetime import timedelta
        base = ui.preview_dt or ui.hover_dt or self._now()
        step = 10 if dy > 0 else -10
        self._set_preview(base + timedelta(minutes=step))
        return True

    # ---- 快捷键（窗口捕获阶段的键盘控制器）----------------------------
    def _key_info(self):
        """空格：显示 / 隐藏「此刻的事实」。

        顺带把菜单（与托盘）里那一项「显示此刻的事实」的勾同步上——
        不然用空格关掉卡片之后，菜单里那个勾还挂着。
        """
        want = not self.painter.ui.show_info
        self.set_toggle("info", want)
        self.painter.ui.show_info = want
        self.area.queue_draw()
        return True

    def _key_escape(self):
        ui = self.painter.ui
        if ui.preview_dt is not None:
            self._set_preview(None)
            return True
        if self.is_fullscreen():
            self.unfullscreen()
            return True
        return False

    def _key_home(self):
        self._set_preview(None)
        return True

    def _key_step(self, minutes: int):
        from datetime import timedelta
        ui = self.painter.ui
        base = ui.preview_dt or ui.hover_dt or self._now()
        self._set_preview(base + timedelta(minutes=minutes))
        return True

    def _on_key(self, _c, keyval, _code, _state):
        name = Gdk.keyval_name(keyval)
        if name == "space":
            return self._key_info()
        if name == "Escape":
            return self._key_escape()
        if name in ("Home", "KP_Home"):
            return self._key_home()
        if name in ("Left", "Right", "KP_Left", "KP_Right"):
            return self._key_step(-10 if "Left" in name else 10)
        return False

    # ------------------------------------------------------------------
    def _on_weather(self, weather):
        self._scene_key = None
        self._ribbon_key = None
        self.area.queue_draw()

    def toast(self, text: str, seconds: float = 3.0):
        self.painter.ui.toast = text
        self.painter.ui.toast_until = _time.time() + seconds
        self.painter.ui.toast_detail = ""
        self.area.queue_draw()

    def toast_detailed(self, text: str, seconds: float, detail: str):
        """短提示 + 可复制的完整内容：点一下提示条就打开详情窗。"""
        self.painter.ui.toast = text
        self.painter.ui.toast_until = _time.time() + seconds
        self.painter.ui.toast_detail = detail
        self.area.queue_draw()

    def _show_detail(self, title: str, body: str):
        DetailDialog(self, title, body).present()

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
    def __init__(self, force_city: bool = False, hidden: bool = False):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.config = cfgmod.Config.load()
        if not self.config.location.name:
            self.config.location = cfgmod.guess_location()
            self.config.save()
        self.force_city = force_city
        self.start_hidden = hidden
        if cfgmod.autostart_installed() != self.config.autostart:
            self.config.autostart = cfgmod.autostart_installed()
        self._repair_autostart()
        self._forget_own_wallpaper()

    def _repair_autostart(self) -> None:
        """自启项只在用户勾选的那一刻写过。之后换了安装方式（源码装→.deb 装），
        它就会指着一条"已经不存在的命令"，而 GNOME 只是静默忽略这条自启项
        （日志里才有一句 Exec binary ... does not exist），用户什么都看不到。
        所以每次启动都对一遍，需要就悄悄改回来。"""
        if not self.config.autostart:
            return
        argv = self.installed_launcher()
        if cfgmod.autostart_needs_repair(argv, bool(self.config.autostart_hidden)):
            cfgmod.set_autostart(True, argv, hidden=bool(self.config.autostart_hidden))

    def _forget_own_wallpaper(self) -> None:
        """早期版本会把"我们自己画的天空"当成"用户原来的壁纸"记下来，
        于是「还原成原来的壁纸」还原出来的是一张过期的天空。发现就清掉这条坏记录。"""
        if not self.config.prev_wallpaper and not self.config.prev_wallpaper_dark:
            return
        if (wallmod.is_our_uri(self.config.prev_wallpaper)
                or wallmod.is_our_uri(self.config.prev_wallpaper_dark)):
            self.config.prev_wallpaper = ""
            self.config.prev_wallpaper_dark = ""
            self.config.save()

    def installed_launcher(self) -> list:
        """启动"这个程序"真正的 argv：自启项和"重启自己"都用它。

        优先用与当前代码同一份安装、又躺在 PATH 上的 `chuang` 命令
        （.deb 装成 /usr/bin/chuang，源码安装装成 /usr/local/bin/chuang）；
        都不是才退回当前这份代码自己的 chuang-gui。
        """
        here = Path(__file__).resolve()          # .../chuang/app.py
        root = here.parent.parent                # /opt/chuang 或仓库根目录
        launcher = root / "chuang-gui"
        for cand in ("/usr/bin/chuang", "/usr/local/bin/chuang"):
            path = Path(cand)
            try:
                if path.exists() and path.resolve() == launcher.resolve():
                    return [cand]
            except OSError:
                continue
        if launcher.exists():
            return [str(launcher)]
        return [sys.executable, str(launcher)]

    def installed_exec(self) -> str:
        """给 .desktop 文件用的 Exec= 值（每个参数各自加引号）。"""
        return " ".join(cfgmod.exec_quote(part) for part in self.installed_launcher())

    def do_activate(self):
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        win = self.props.active_window
        if win is None:
            win = ChuangWindow(self)
        tray_ok = bool(getattr(win, "tray", None) is not None and win.tray.available)
        if self.start_hidden and tray_ok:
            # 开机自启选了"直接进托盘"：不弹窗，只在托盘里待着
            self.start_hidden = False
            win.set_visible(False)
        else:
            # 托盘不可用的时候必须把窗开出来，否则这个进程就没法操作了
            self.start_hidden = False
            win.present()
        if self.force_city:
            GLib.timeout_add(500, lambda: (win._act_city(), False)[1])


def run(argv=None, force_city: bool = False) -> int:
    if not _claim_single_instance():
        print("「窗」已经在运行；这次只把原来那扇窗叫到了前面。", file=sys.stderr)
        return 0
    argv = list(argv or [])
    hidden = "--hidden" in argv
    if hidden:
        argv.remove("--hidden")
    app = ChuangApp(force_city=force_city, hidden=hidden)
    return app.run(argv)


def _claim_single_instance() -> bool:
    """同一时刻只允许一个「窗」在写壁纸。

    应用本身靠 D-Bus 保单例，但"两个写入方各自往 sky-a / sky-b 里交替写"
    正是壁纸来回闪的根源之一（一张是"现在"，另一张还是上次那张旧图）。
    这里再加一把进程间的文件锁兜底：拿不到锁就把已有实例叫到前台，然后退场。
    """
    import fcntl
    deadline = _time.monotonic() + 1.5
    while True:
        try:
            INSTANCE_LOCK.parent.mkdir(parents=True, exist_ok=True)
            handle = open(INSTANCE_LOCK, "w")
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # 锁跟着进程走：进程一退出内核就自动释放（锁文件留着也没关系）。
            # 存进模块级变量，别让 handle 被回收——回收就等于解锁。
            globals()["_INSTANCE_HANDLE"] = handle
            return True
        except OSError:
            # 同一个会话里已经有一扇窗：把它叫到前台，自己退场
            if _poke_running_instance():
                return False
            # 锁被"别的会话/正在退出的进程"占着：等一下再试；实在等不到就
            # 照常启动（总比登录后什么都没有强，此时本会话里确实没有第二个）
            if _time.monotonic() >= deadline:
                return True
            _time.sleep(0.15)


def _poke_running_instance() -> bool:
    """本会话里如果已经有「窗」在跑，就把它叫到前台。成功返回 True。"""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        path = "/" + APP_ID.replace(".", "/")
        bus.call_sync(APP_ID, path, "org.freedesktop.Application", "Activate",
                      GLib.Variant("(a{sv})", ({},)), None,
                      Gio.DBusCallFlags.NONE, 1500, None)
        return True
    except Exception:
        return False
