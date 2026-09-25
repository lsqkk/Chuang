"""窗 · Chuang —— GTK4 主程序：窗口本身。

这里是"窗口"：画面、心跳、动作表、生命周期。几摊子事已经各归各的模块——
鼠标在 `pointer.py`、键盘在 `keys.py`、信息卡的交互在 `infocard.py`、壳（标题栏
与控制器的接线）在 `chrome.py`、壁纸在 `wallpaper_ctl.py`、置顶那条 X11 的路在
`topmost.py`、调试用的环境变量在 `devhooks.py`、单实例锁在 `instance.py`、
「关于」那一组在 `about.py`、把这扇窗存成图片在 `export.py`。

所以这个文件只该长"窗口自己的事"。`tests/test_project.py` 里有一条 1100 行的
红线盯着它别再长回两千行（1.1.8 拆过一次，1.2.0 又收了一轮）。
"""

from __future__ import annotations

import os
import sys
import time as _time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, __version__
from . import about as aboutmod
from . import actions
from . import chrome
from . import config as cfgmod
from . import devhooks
from . import diagnostics as diag
from . import export as exportmod
from . import frames as framemod
from . import infocard as factmod
from . import instance
from . import keys as keymod
from . import pointer as pointermod
from . import topmost as topmostmod
from . import tray as traymod
from . import update as upmod
from . import update_ui as upd
from . import wallpaper as wallmod
from . import wallpaper_ctl as wctl
from .dialogs import CityDialog, CloseDialog, DetailDialog, TimeTravelDialog
from .render import SkyPainter
from .scene import SkyEngine, facing_azimuth, human_hint
from .weather import WeatherService


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
        # 置顶那条 X11 的路（需要可选的 python3-xlib，见 topmost.py）
        self.pin_ok = topmostmod.available()
        self.topmost = topmostmod.Topmost(self)
        # 调试钩子：CHUANG_TIME=2026-09-23T18:40 / CHUANG_WEATHER=63:95:9:180
        self._fake_time = devhooks.fake_time(self.engine, self.config)
        self._fake_weather = devhooks.fake_weather(self.engine)
        self.add_css_class("chuang")

        self.set_default_size(self.config.window_w, self.config.window_h)
        self.set_size_request(360, 260)
        self.painter.clock = self._now       # 人车按实时时钟连续移动
        # 画面偏好都记得住：以前用空格关掉信息卡，重启它又会自己回来
        self.painter.ui.show_info = bool(self.config.show_info)
        self.painter.ui.show_ribbon = bool(self.config.show_ribbon)
        self.painter.ui.info_compact = bool(self.config.info_compact)
        # 窗外画什么（菜单 → 场景）：配置里的值搬到画笔上，一处定义见
        # config.SCENE_SWITCHES——加一项开关只改那一处。
        for _field, _label in cfgmod.SCENE_SWITCHES:
            setattr(self.painter.ui, _field, bool(getattr(self.config, _field, True)))
        # 信息卡上那些能点的东西（命中、跳过去看、摊开数据）住在 infocard.py
        self.info = factmod.InfoCard(self)
        # 键盘（空格 / C / R / 左右 / Home / Esc）与"焦点该在画面上"住在 keys.py
        self.keys = keymod.Keys(self)
        # 鼠标（点卡片、拖长卷、滚轮）住在 pointer.py——和键盘是一对
        self.pointer = pointermod.Pointer(self)
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
        chrome.build(self)
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
        self.keys.hook_menu_popover(self.menu_button)

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
        if want and not self.pin_ok:
            self.toast("置顶需要先安装 python3-xlib")
            self.pin_button.set_active(False)
            return
        self.config.always_on_top = want
        self.config.save()
        if self.pin_button.get_active() != want:
            self.pin_button.set_active(want)
        self.topmost.set_above(want)
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
    # 场景：窗外画什么（菜单 → 场景）
    # ------------------------------------------------------------------
    def _scene_setter(self, field: str):
        """`actions` 要的那颗处理器：`win._scene_setter("show_trees")`。"""
        def set_it(want: bool) -> None:
            self._set_scene_switch(field, want)
        return set_it

    def _set_scene_switch(self, field: str, want: bool, quiet: bool = False) -> None:
        """把某一项场景开关写进配置、落到画笔上，并让画面重画。

        配置、`painter.ui`、菜单里那个勾，三处必须是同一个值——只改其中一处的
        老毛病就是"重启之后它又自己回来了"（信息卡那次就是这么坏的）。
        """
        setattr(self.config, field, bool(want))
        setattr(self.painter.ui, field, bool(want))
        act = self.lookup_action(field)
        if act is not None and act.get_state() is not None:
            act.set_state(GLib.Variant.new_boolean(bool(want)))
        self.config.save()
        self.area.queue_draw()
        if not quiet:
            label = dict(cfgmod.SCENE_SWITCHES).get(field, field)
            self.toast(f"{label}·{'画出来了' if want else '收起来了'}", 2.0)

    def _act_scene_all(self, *_):
        """一键：窗外的世界全画出来（回到出厂那幅画）。"""
        for field, _label in cfgmod.SCENE_SWITCHES:
            self._set_scene_switch(field, True, quiet=True)
        self.toast("窗外的世界都回来了", 2.4)

    def _act_scene_sky(self, *_):
        """一键：只看天空——街上的人、车、树、楼一起收起来。"""
        for field in ("show_people", "show_traffic", "show_trees", "show_lamps",
                      "show_planes", "show_skyline"):
            self._set_scene_switch(field, False, quiet=True)
        self.toast("街上收起来了 · 只剩天上那一片", 2.6)

    # ------------------------------------------------------------------
    # 桌面壁纸与诊断
    # ------------------------------------------------------------------
    def _diag_facts(self) -> "diag.Facts":
        """把诊断要用的事实抓一次——排版那件事归 diagnostics.py 管。"""
        return diag.Facts(
            version=__version__, pid=os.getpid(),
            instance_held=instance.held(),
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
    # 关窗与托盘（「关于」那一组在 about.py，检查更新在 update_ui.py）
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

    # 「关于」那一组（关于窗 / 作者主页 / 问题反馈）住在 about.py，
    # 这里只留转发——菜单里那三条动作名指着这几个方法（见 actions.register_actions）
    def _act_about(self, *_):
        aboutmod.show_about(self)

    def _act_author_main(self, *_):
        aboutmod.open_author(self)

    def _act_report_issue(self, *_):
        aboutmod.report_issue(self)

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
    # 场景与绘制（一帧怎么来的）
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
            # 这一天的天气得是真的：手上那份预报里没有它，就去问一次
            # （同一天 20 秒内只问一遍，拖动长卷不会把接口刷爆）
            self.weather.watch(when)
        self._scene_key = None
        self.area.queue_draw()

    def preview_note(self, when) -> str:
        """"跳过去看"的那句话：别的日子要看清楚是**哪一天**。"""
        today = self._now().date()
        if when.date() == today:
            return f"正在看 {when:%H:%M} 的窗外 · Esc 回到此刻"
        return f"正在看 {when:%m-%d %H:%M} 的窗外 · Esc 回到此刻"

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
        self.painter.draw(cr, w, h, scene, self._az0())
        self.painter.draw_chip(cr, w, h)

    def _az0(self) -> float:
        """这扇窗朝哪边（北半球朝南）——和壁纸、导出的图片用的是同一个值。"""
        return facing_azimuth(self.config.location.lat)

    def _act_savepicture(self, *_):
        """菜单 → 「把这扇窗存成图片…」：把此刻这一帧写进 ~/Pictures。

        画的就是**你现在看到的这一帧**（同一个尺寸、预览时就是预览那一刻），
        存在哪、叫什么名字都写在提示里——点一下提示条还能把完整路径复制走。
        """
        when = self.painter.ui.preview_dt or self._now()
        scene = self._current_scene()
        # 窗口还没被分配尺寸时（极少数情况）退回到默认大小，别存一张 0×0
        w = int(self.get_width() or 0) or self.config.window_w
        h = int(self.get_height() or 0) or self.config.window_h
        path = exportmod.default_path(when)
        try:
            exportmod.save_png(self.painter, scene, w, h, path)
        except Exception as exc:                     # noqa: BLE001 - 磁盘满 / 没权限
            self.toast(f"没能写出图片：{exc}", 6.0, icon="warn")
            return
        self.toast_detailed(
            f"存好了：{path.name}", 7.0,
            f"这一帧存在：\n{path}\n\n{w}×{h}，和你此刻看到的一样。\n"
            f"（{when:%Y-%m-%d %H:%M} · {scene.location_label or scene.location_name}）",
            icon="check")

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
        now = _time.monotonic()
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
        # 卡片要看得见"正在问一次真实天气"（按下 R 之后不再是石沉大海，
        # 见 infocard.InfoCard.refresh_weather）。这只是两个状态，进缓存键没问题。
        busy = bool(self.weather.busy)
        if busy != self.painter.ui.weather_busy:
            self.painter.ui.weather_busy = busy
            self.area.queue_draw()
        # 正在看的那一天（预览时是预览日）如果还没有真预报，就去补一次：
        # 拖动长卷时可能正忙、被限流跳过，心跳会接着把它补齐。
        self.weather.watch(self.painter.ui.preview_dt or clock)
        return True

    # ------------------------------------------------------------------
    # 提示条与第一次打开
    def _on_weather(self, weather):
        self._scene_key = None
        self._ribbon_key = None
        self.area.queue_draw()

    def toast(self, text: str, seconds: float = 3.0, icon: str = "info"):
        """屏幕下方那条一句话提示。点一下可以把它收掉（带详情的点开看）。"""
        self.painter.ui.toast = text
        self.painter.ui.toast_icon = icon
        self.painter.ui.toast_until = _time.time() + seconds
        self.painter.ui.toast_span = max(0.5, float(seconds))
        self.painter.ui.toast_detail = ""
        self.area.queue_draw()

    def toast_detailed(self, text: str, seconds: float, detail: str,
                       icon: str = "warn"):
        """短提示 + 可复制的完整内容：点一下提示条就打开详情窗。"""
        self.painter.ui.toast = text
        self.painter.ui.toast_icon = icon
        self.painter.ui.toast_until = _time.time() + seconds
        self.painter.ui.toast_span = max(0.5, float(seconds))
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
                       "拖底部长卷预览今天的天色 · 菜单 → 场景 里能收起街上的车与人",
                       9.0)
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
    if not instance.claim():
        print("「窗」已经在运行；这次只把原来那扇窗叫到了前面。", file=sys.stderr)
        return 0
    argv = list(argv or [])
    hidden = "--hidden" in argv
    if hidden:
        argv.remove("--hidden")
    app = ChuangApp(force_city=force_city, hidden=hidden)
    return app.run(argv)
