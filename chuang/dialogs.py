"""「窗」自己搭的几个小窗口。

GTK 4.6 + libadwaita 1.1 上没有 `Adw.MessageDialog` / `Adw.AboutWindow` 这些
方便的东西（见 AGENTS.md 第 2 节），所以这几个对话框都是拿 Gtk.Window 手搭的。
它们彼此独立、只通过回调与外面说话，所以单独放一个文件；主窗口（ChuangWindow）
留在 app.py 里。
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone as _tz

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from . import config as cfgmod
from .mainloop import to_main
from .weather import fallback_timezone, geocode, timezone_for


def _local_clock(tz: str) -> str:
    """那个地方此刻几点？算不出来（时区名不认识）就返回空串。"""
    if not tz or tz == "auto":
        return ""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(tz)).strftime("%H:%M")
    except Exception:
        return ""


def _offset_note(tz: str, base_tz: str) -> str:
    """那个地方比"我这儿"快/慢多少——这个窗外的东西，只对看别人的天有用。"""
    if not tz or not base_tz or tz == base_tz:
        return ""
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(_tz.utc)
        delta = (now.astimezone(ZoneInfo(tz)).utcoffset()
                 - now.astimezone(ZoneInfo(base_tz)).utcoffset())
        hours = delta.total_seconds() / 3600.0
    except Exception:
        return ""
    if abs(hours) < 0.01:
        return "同时刻"
    whole = int(abs(hours))
    half = abs(hours) - whole > 0.4
    span = f"{whole} 小时" + ("半" if half else "")
    return ("快 " if hours > 0 else "慢 ") + span


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

    def __init__(self, parent, on_pick, on_notice=None, current=None):
        super().__init__(modal=True, transient_for=parent, default_width=470,
                         default_height=520)
        self.set_title("换一扇窗")
        self.on_pick = on_pick
        self.on_notice = on_notice      # 一句话提示（时区没查到之类），可以为空
        self.current = current          # 现在朝着的那个城市（列表里标出来）
        self.current_tz = getattr(current, "timezone", "") if current else ""
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
        self.manual_btn = Gtk.Button(label="使用这组坐标")
        self.manual_btn.connect("clicked", self._manual)
        inner.append(self.manual_btn)

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
            to_main(self._fill, rows)

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
            tz = r.get("timezone") or ""
            bits = [x for x in (r.get("admin"), r.get("country")) if x]
            clock = _local_clock(tz)
            if clock:
                diff = _offset_note(tz, self.current_tz)
                bits.append(f"当地 {clock}" + (f"（{diff}）" if diff else ""))
            sub = " · ".join(bits)
            row = Adw.ActionRow(title=title, subtitle=sub or "—", activatable=True)
            here = bool(self.current
                        and title == self.current.name
                        and abs(r["lat"] - self.current.lat) < 0.05)
            row.add_prefix(Gtk.Image.new_from_icon_name(
                "emblem-ok-symbolic" if here else "mark-location-symbolic"))
            if here:
                tag = Gtk.Label(label="正在看")
                tag.add_css_class("accent")
                row.add_suffix(tag)
            row.set_tooltip_text(f"{r['lat']:.4f}, {r['lon']:.4f}"
                                 + (f" · {tz}" if tz else ""))
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
        # 时区要联网问一次（IANA 名字），否则按经度取整的固定偏移在欧洲/北美
        # 夏天会差整整一小时——网络不通时退回那个偏移，并在状态栏写清楚。
        self.status.set_text("正在按经纬度确定时区…")
        self.manual_btn.set_sensitive(False)

        def worker():
            tz = timezone_for(lat, lon)
            to_main(self._manual_done, lat, lon, tz)

        threading.Thread(target=worker, daemon=True, name="chuang-tz").start()

    def _manual_done(self, lat: float, lon: float, tz: str) -> bool:
        guessed = not tz
        if not tz:
            tz = fallback_timezone(lon)
        self.on_pick(cfgmod.Location(name=f"{lat:.2f}, {lon:.2f}", admin="自定义坐标",
                                     country="", lat=lat, lon=lon, timezone=tz))
        if guessed:
            # 窗口这就关了，把话交给主窗口说（toast 会跟着新城市显示）
            if self.on_notice:
                self.on_notice("没连上时区服务，先按经度取整时区；夏令时期间可能差一小时")
        self.close()
        return False
