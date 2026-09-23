"""窗 · Chuang —— GTK4 主程序。"""

from __future__ import annotations

import os
import sys
import time as _time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, __version__
from . import actions
from . import config as cfgmod
from . import diagnostics as diag
from . import frames as framemod
from . import infocard as factmod
from . import tray as traymod
from . import update as upmod
from . import update_ui as upd
from . import wallpaper as wallmod
from . import wallpaper_ctl as wctl
from .dialogs import CityDialog, CloseDialog, DetailDialog, TimeTravelDialog
from .render import SkyPainter
from .scene import SkyEngine, human_hint
from .weather import WeatherService

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
        self._really_quit = False
        self._tick_errors = 0               # 心跳里兜住的异常次数
        self._last_tick_error = ""
        self._toggle_handlers = {}          # 勾选项名字 → 真正的处理器（便于"设为"某状态）
        self._pin_ok = self._init_pin()
        # 调试钩子：CHUANG_TIME=2026-09-23T18:40 / CHUANG_WEATHER=63:95:9:180
        self._fake_time = self._parse_fake_time()
        self._fake_weather = self._parse_fake_weather()
        self.add_css_class("chuang")

        self.set_default_size(self.config.window_w, self.config.window_h)
        self.set_size_request(360, 260)
        self.painter.clock = self._now       # 人车按实时时钟连续移动
        # 画面偏好都记得住：以前用空格关掉信息卡，重启它又会自己回来
        self.painter.ui.show_info = bool(self.config.show_info)
        self.painter.ui.show_ribbon = bool(self.config.show_ribbon)
        self.painter.ui.info_compact = bool(self.config.info_compact)
        # 信息卡上那些能点的东西（命中、跳过去看、摊开数据）住在 infocard.py
        self.info = factmod.InfoCard(self)
        # 重绘交给帧时钟（见 frames.FrameDriver）：帧率上限由 config.frame_rate 决定，
        # 菜单里可以按终端性能调（都得住在这儿，动作表里要引用它们）
        self.frames = framemod.FrameDriver(self)
        loc = self.config.location
        # 壁纸那一摊（接管/跟随/动态壁纸/还原/诊断）都在 wallpaper_ctl 里，
        # 窗口这边只负责"什么时候叫它"：心跳、换城市、关窗。
        # 它得在 _build_actions 之前建好——动作表里要引用它的方法。
        self.wp = wallmod.Worker(loc.lat, loc.lon, loc.timezone, loc.label,
                                 seed=abs(hash(loc.name)) % 9973 + 11)
        self.wallpaper = wctl.WallpaperController(self, self.wp)
        self.updater = upd.UpdateController(self)
        self._build_ui()
        actions.register_actions(self)
        self._setup_tray()

        self.weather = WeatherService(self._on_weather)
        self.weather.enabled = self.config.mirror_weather
        if self._fake_weather is not None:
            # 调试用的假天气：照常画，但一个字节都不往外发
            self.weather.weather = self._fake_weather
            self.weather.allow_fetch = False
        self.engine.set_location(self.config.location.lat, self.config.location.lon,
                                 self.config.location.timezone)
        if self._fake_weather is None:
            self.weather.set_location(self.config.location.lat, self.config.location.lon,
                                      self.config.location.timezone)

        self._last_clock = self._now()
        self._timer = GLib.timeout_add(25, self._tick)
        self.frames.start()
        self.connect("close-request", self._on_close)
        if self.config.update_check:
            GLib.timeout_add(9000, self.updater.maybe_auto_check)
        self.first_run_tips()

    # ------------------------------------------------------------------
    # 调试钩子（正式使用不会用到）
    # ------------------------------------------------------------------
    def _parse_fake_time(self):
        import os
        from datetime import datetime
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
            import importlib.util
            return importlib.util.find_spec("Xlib") is not None
        except (ImportError, ValueError):
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
        # 整幅画面都是 Cairo 位图，屏幕阅读器只能读到标题栏和菜单——
        # 至少把"此刻的天气与天色"那句话（信息卡里那句人话）挂成可读的描述。
        try:
            self.area.update_property([Gtk.AccessibleProperty.LABEL],
                                      ["窗外的天空"])
        except Exception:
            pass

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

        menu = actions.build_menu(self)
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

    def refresh_menu(self) -> None:
        """菜单内容变了（比如出现了新版本入口）——窗口与托盘一起换掉。"""
        self.menu_model = actions.build_menu(self)
        if getattr(self, "menu_button", None) is not None:
            self.menu_button.set_menu_model(self.menu_model)
        if getattr(self, "tray", None) is not None:
            self.tray.reload(self.menu_model)

    def request_quit(self) -> None:
        """真的退出（不是最小化到托盘）。安装完新版、点了「退出」都走这里。"""
        self._really_quit = True
        self.app.quit()

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
        self._scene_key = None
        self._ribbon_key = None
        if want:
            self.weather.refresh(force=True)
            self.toast("窗外的天气会真实地出现在窗上")
        else:
            self.toast("只看天，不看天气")

    def _act_ribbon(self, want: bool):
        self.painter.ui.show_ribbon = want
        self.config.show_ribbon = want
        self.config.save()
        self.area.queue_draw()
        self.toast("今日淡淡的长卷回来了" if want
                   else "收起长卷，窗外只剩景色", 2.4)

    def _act_city(self, *_):
        CityDialog(self, self._set_location, self.toast,
                   current=self.config.location).present()

    # ------------------------------------------------------------------
    # 桌面壁纸
    # ------------------------------------------------------------------
    def _diag_facts(self) -> "diag.Facts":
        """把诊断要用的事实抓一次——排版那件事归 diagnostics.py 管。"""
        return diag.Facts(
            version=__version__, pid=os.getpid(),
            instance_held=bool(globals().get("_INSTANCE_HANDLE")),
            other_processes=diag.other_chuang_processes(),
            installed_deb=upmod.installed_deb_version(),
            tray_available=bool(self.tray is not None and self.tray.available),
            config=self.config,
            now=_time.time(),
            shown_uri=wallmod.shown_uri(),
            shown_slot=wallmod.shown_slot(),
            slot_mtimes=wallmod.slot_mtimes(),
            slot_names=tuple(p.name for p in wallmod.SLOTS),
            stale_slot=wallmod.stale_shown_slot(),
            last_wallpaper_at=self.wallpaper.last_at,
            last_wallpaper_ok=self.wallpaper.last_ok,
            last_wallpaper_msg=self.wallpaper.last_msg,
            last_flip_at=self.wallpaper.last_flip_at,
            flip_every=wctl.FLIP_EVERY,
            tick_errors=self._tick_errors,
            last_tick_error=self._last_tick_error,
        )

    def _diagnostics_wallpaper(self) -> str:
        return diag.wallpaper_report(self._diag_facts())

    # ------------------------------------------------------------------
    # 检查更新
    # ------------------------------------------------------------------
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
                                     prefix_items=[("显示「窗」", "win.show")],
                                     activate_action="win.show")
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

    def _tray_activate(self, name: str = "win.show", target=None):
        """托盘图标被点（双击 / 键盘激活）时把窗口叫到前面。

        默认参数不能省：tray 的 Activate/SecondaryActivate 分支不带参数调用它，
        少一个默认值就是一个 TypeError——D-Bus 方法处理器抛异常意味着这次调用
        永远不会有回复，日志里还会多一条 traceback。
        """
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
        rel = self.updater.available_release
        if rel is not None:
            version_text = f"{__version__}（有新版本 {rel.tag}）"
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
        return diag.environment_report(self._diag_facts())

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
        self.painter.invalidate_location()   # 楼群数据 + 所有离屏缓存（含城市天际线）
        self._set_preview(None)
        self.painter.ui.ribbon_key = None
        self.painter.ui.ribbon_surface = None
        self.title_widget.set_subtitle(location.label)
        self.toast(f"现在这扇窗朝着 {location.full_label or location.name}")
        self.area.queue_draw()
        # 桌面上的那张还朝着旧城市，别等下一个周期，立刻换掉
        if self.config.wallpaper_auto:
            self.wallpaper.schedule_soon()
            self.wallpaper.apply(quiet=True)

    # ------------------------------------------------------------------
    # 场景与绘制
    # ------------------------------------------------------------------
    def _weather_for_paint(self):
        """作画用的天气：关掉「跟随真实天气」时是 None（见 WeatherService.effective）。"""
        return self.weather.effective

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

    def _current_scene(self):
        ui = self.painter.ui
        now = self._now()
        when = ui.preview_dt if ui.preview_dt else now
        weather = self._weather_for_paint()
        key = (when.strftime("%Y-%m-%d %H:%M"),
               getattr(weather, "fetched_at", 0),
               self.weather.enabled,
               self.config.location.name)
        if key != self._scene_key or self._scene is None:
            self._scene = self.engine.build(
                when, weather,
                preview=ui.preview_dt is not None,
                location_label=self.config.location.label,
                weather_off=not self.weather.enabled)
            self._scene_key = key
        return self._scene

    def _refresh_ribbon(self, scene):
        day = scene.when.replace(hour=0, minute=0, second=0, microsecond=0)
        weather = self._weather_for_paint()
        key = (day.strftime("%Y-%m-%d"),
               getattr(weather, "fetched_at", 0),
               self.weather.enabled,
               self.config.location.name)
        if key == self._ribbon_key:
            return
        self._ribbon_key = key
        self.painter.ui.ribbon = self.engine.ribbon(day, weather)
        # 悬停长卷时冒出来的那句话：这一刻的天气（没联网就留空，只显示时刻）
        self.painter.ui.ribbon_info = self.info.ribbon_notes(self.painter.ui.ribbon,
                                                             weather)
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
                self.wallpaper.schedule_soon()
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
            try:                       # 给屏幕阅读器一句"此刻的窗外"
                sc = self._current_scene()
                self.area.update_property(
                    [Gtk.AccessibleProperty.DESCRIPTION],
                    [f"{self.config.location.label}，{sc.period_name}：{human_hint(sc)}"])
            except Exception:
                pass
            self.weather.maybe_refresh()
        self.wallpaper.tick(now)
        self.frames.watchdog(now)             # 帧时钟万一没在走，兜底补一次重绘
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
        changed = False
        idx = self.info.hit(x, y)
        if idx != ui.info_hover:
            ui.info_hover = idx
            changed = True
        arc_dt = (self.info.arc_time(x)
                  if 0 <= idx < len(ui.info_rects) and ui.info_rects[idx][4] == "arc"
                  else None)
        if arc_dt != ui.info_hover_dt:
            ui.info_hover_dt = arc_dt
            changed = True
        over = self._over_ribbon(x, y)
        self.area.set_cursor_from_name(
            "pointer" if idx >= 0 else ("ew-resize" if over else None))
        t = self.painter.ribbon_time_at(ui, x) if over else None
        if t != ui.hover_dt:
            ui.hover_dt = t
            changed = True
        if changed:
            self.area.queue_draw()

    def _on_leave(self, *_):
        ui = self.painter.ui
        ui.hover_dt = None
        ui.info_hover = -1
        ui.info_hover_dt = None
        self.area.queue_draw()

    def _on_press(self, gesture, _n, x, y):
        ui = self.painter.ui
        tx, ty, tw, th = ui.toast_rect
        if tw > 0 and tx <= x <= tx + tw and ty <= y <= ty + th:
            if ui.toast_detail:
                self._show_detail("详情", ui.toast_detail)
            else:                       # 点一下就把这条提示收掉
                ui.toast_until = 0.0
                self.area.queue_draw()
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            return
        idx = self.info.hit(x, y)
        if idx >= 0:
            self.info.activate(idx, x)
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
        # 和菜单里那一项是同一个处理（infocard.InfoCard.act_show）
        self.set_toggle("info", not self.painter.ui.show_info)
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
        if name in ("c", "C"):
            self.set_toggle("info_compact", not self.config.info_compact)
            return True
        if name in ("r", "R"):
            self.info.refresh_weather()
            return True
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

    def toast(self, text: str, seconds: float = 3.0, icon: str = "info"):
        """屏幕下方那条一句话提示。点一下可以把它收掉（带详情的点开看）。"""
        self.painter.ui.toast = text
        self.painter.ui.toast_icon = icon
        self.painter.ui.toast_until = _time.time() + seconds
        self.painter.ui.toast_detail = ""
        self.area.queue_draw()

    def toast_detailed(self, text: str, seconds: float, detail: str,
                       icon: str = "warn"):
        """短提示 + 可复制的完整内容：点一下提示条就打开详情窗。"""
        self.painter.ui.toast = text
        self.painter.ui.toast_icon = icon
        self.painter.ui.toast_until = _time.time() + seconds
        self.painter.ui.toast_detail = detail
        self.area.queue_draw()

    def _show_detail(self, title: str, body: str):
        DetailDialog(self, title, body).present()

    def first_run_tips(self):
        """第一次打开时说一句话。

        刻意**不弹对话框**去逼用户确认城市：这个产品的卖点是"一个动词、零输入"
        （见 DESIGN.md §4），一上来就拦一个模态框是拿设计原则换一次确认。
        所以只在推测出来的城市可能不对时，把"去哪改"说清楚。
        """
        if self.config.first_run_done:
            return
        self.config.first_run_done = True
        if self.config.location.guessed:
            self.toast(f"先按你的时区猜了「{self.config.location.name}」"
                       f"，不对的话：菜单 → 换一扇窗", 9.0)
        else:
            self.toast("空格 隐藏信息卡 · 点上面的日出/日落可以跳过去看 · "
                       "拖底部长卷预览今天的天色", 9.0)
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
            self.config.prev_wallpaper_options = ""
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
