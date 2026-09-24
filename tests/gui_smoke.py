#!/usr/bin/env python3
"""无头 GUI 冒烟：把整扇窗真的建出来、把菜单与动作挨个走一遍。

设计上有三条硬规矩（这几条以前都被人踩过）：

1. **换 HOME**：`instance.lock`、`~/.cache/chuang`、`~/.config/chuang` 全在
   `Path.home()` 底下，不换就会跟用户正在跑的实例抢单例、动他的缓存。
2. **换 APP_ID**：D-Bus 上的名字是 `io.github.chuang.SkyWindow`，同名的第二个
   实例会被 GApplication 转发给正在跑的那个——那等于把用户的窗口弹到前面。
3. **绝不碰壁纸**：`wallpaper_*` 那一组动作会调 `gsettings`，而 dconf 走的是
   同一个会话总线（换了 HOME 也照样改用户真正的桌面设置）。这些动作只检查
   "有没有注册、签名对不对"，**不激活**。

输出一行 JSON（键是探针名，值是结果），退出码 0 表示全部通过。
由 tests/test_app_gui.py 在 Xvfb 里调用；也可以手跑：
    xvfb-run -a python3 tests/gui_smoke.py
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def _isolate() -> Path:
    """建一个一次性的 HOME / XDG 环境，返回它。必须在 import chuang 之前调用。"""
    home = Path(tempfile.mkdtemp(prefix="chuang-gui-smoke-"))
    # 跑完自己收拾干净（被 kill 掉时会留下一个临时目录，无所谓）
    atexit.register(shutil.rmtree, home, True)
    os.environ["HOME"] = str(home)
    os.environ.pop("XDG_CONFIG_HOME", None)
    os.environ.pop("XDG_CACHE_HOME", None)
    runtime = home / "runtime"
    runtime.mkdir(mode=0o700)
    os.environ["XDG_RUNTIME_DIR"] = str(runtime)
    for key in ("CHUANG_TIME", "CHUANG_WEATHER"):
        os.environ.pop(key, None)
    return home


HOME = _isolate()
sys.path.insert(0, str(ROOT))

import chuang  # noqa: E402

# 别和用户那扇窗撞名。带上 pid：万一上一轮挂了、还占着 D-Bus 名字，
# 新一轮也不会变成"远程实例"（那样 app.run() 会立刻返回，什么都测不到）。
chuang.APP_ID = f"io.github.chuang.SkyWindow.SmokeTest.p{os.getpid()}"

from gi.repository import Gio, GLib  # noqa: E402

import chuang.app as appmod  # noqa: E402

try:                                    # 拆出去之后住在这儿
    import chuang.dialogs as dlg
except ImportError:                     # 还没拆的话就在 app 里
    dlg = appmod

# 勾选项与单选项的清单：改这两个集合等于改菜单长什么样，得是自觉的
EXPECTED_TOGGLES = {
    "pin", "weather", "info", "infocompact", "ribbon",
    "autostart", "autostarthidden",
    "wallpaperauto", "wallpaperinfo", "wallpaperribbon", "autoupdate",
}
EXPECTED_RADIOS = {"closebehavior", "wallpaperinterval", "wallpaperinfomode",
                   "framerate"}

# ---- 动作清单：每个动作要么"能安全激活"，要么"写明为什么不激活 -------------
# 加新动作时必须二选一，否则测试会失败——这样就不会有人悄悄加一个
# "一点就动用户桌面"的入口。
#
# 参数：(目标值或 None)，None 表示不带参数激活。
SAFE_TO_ACTIVATE = {
    "win.info": None,              # 勾选项：自己翻转
    "win.weather": None,
    "win.pin": None,               # 只是试着置顶；Xvfb 里没有窗口管理器，无害
    "win.fullscreen": None,
    "win.autostart": None,         # 只写 $HOME/.config/autostart（一次性 HOME）
    "win.autostarthidden": None,
    "win.show": None,
    "win.gotodatetime": None,      # 打开"跳到某天某时"对话框
    "win.city": None,              # 打开"换一扇窗"对话框
    "win.about": None,
    "win.wallpaperdiag": None,     # 只是把诊断文本摊进一个可复制的窗口
    "win.closebehavior": "ask",
    "win.infocompact": None,       # 只改信息卡怎么画
    "win.ribbon": None,
    "win.framerate": "60",         # 只改帧率上限（探针里还会真的验一次）
}

# 不激活的：要么会动用户的桌面，要么会联网装东西、开浏览器、结束进程。
NOT_ACTIVATED = {
    "win.quit": "会结束这次测试",
    "app.quit": "会结束这次测试",
    "win.restart": "会重启进程",
    "win.checkupdate": "联网",
    "win.autoupdate": "勾选后会联网检查更新",
    "win.downloaddeb": "联网下载安装包",
    "win.installdeb": "联网下载并用 root 安装",
    "win.openreleases": "会打开浏览器",
    "win.reportissue": "会打开浏览器",
    "win.authormain": "会打开浏览器",
    "win.skipversion": "要有新版本才有意义",
    "win.wallpaper": "会写用户的桌面设置",
    "win.wallpaperauto": "会写用户的桌面设置",
    "win.wallpaperinterval": "会写用户的桌面设置",
    "win.wallpaperday": "要画 96 帧、还会写桌面设置",
    "win.wallpaperinfo": "会写用户的桌面设置",
    "win.wallpaperribbon": "会写用户的桌面设置",
    "win.wallpaperinfomode": "壁纸跟着此刻时会立刻重画、写用户的桌面设置",
    "win.wallpaperrestore": "会改回用户的壁纸设置",
}


class FakeInvocation:
    """假装 D-Bus 调用的回复口：记下有没有回过话。"""

    def __init__(self):
        self.replied = False

    def return_value(self, _value):
        self.replied = True


def walk_menu(model: Gio.MenuModel, out: set[str]) -> None:
    """把 Gio.MenuModel 里出现过的动作名全收集起来。"""
    for i in range(model.get_n_items()):
        value = model.get_item_attribute_value(i, "action", GLib.VariantType.new("s"))
        if value is not None:
            out.add(value.get_string())
        for link in ("section", "submenu"):
            child = model.get_item_link(i, link)
            if child is not None:
                walk_menu(child, out)


def main() -> int:
    results: dict = {}
    problems: list[str] = []
    cache = HOME / ".cache" / "chuang"
    cache.mkdir(parents=True, exist_ok=True)
    cfgdir = HOME / ".config" / "chuang"
    cfgdir.mkdir(parents=True, exist_ok=True)
    # 一份"天气开着、壁纸关着"的配置 + 一份下雨的缓存：本轮的探针就用它
    (cfgdir / "config.json").write_text(json.dumps({
        "location": {"name": "西安", "admin": "陕西省", "country": "中国",
                     "lat": 34.3416, "lon": 108.9398, "timezone": "Asia/Shanghai"},
        "mirror_weather": True, "wallpaper_auto": False, "first_run_done": True,
        "close_behavior": "tray",
    }, ensure_ascii=False), encoding="utf-8")
    (cache / "weather.json").write_text(json.dumps({
        "fetched_at": 1e9, "code": 63, "cloud": 95.0, "temp": 18.0, "precip": 2.0,
        "wind_speed": 9.0, "wind_dir": 200.0, "humidity": 80.0,
        "hourly": [[f"2026-09-23T{h:02d}:00:00", 95.0, 63, 18.0, 60.0] for h in range(24)],
    }), encoding="utf-8")

    app = appmod.ChuangApp()

    def probe() -> bool:
        """探针：**无论出什么事都必须把 app 关掉**。

        踩过：探针里抛个异常，回调返回 None（GLib 会留着这个定时器），
        于是进程一直跑、还占着 D-Bus 名字——下一轮就跑成了"远程实例"，
        什么都不测、直接退出，看起来像"测试通过"。
        """
        try:
            _probe_body()
        except Exception as exc:                          # noqa: BLE001
            import traceback
            problems.append(f"探针自己炸了：{type(exc).__name__}: {exc}")
            traceback.print_exc()
        finally:
            app.quit()
        return False

    def _probe_body() -> None:
        win = app.props.active_window
        if win is None:
            problems.append("没有窗口")
            return

        # 1) 画面真的在走：场景能建、长卷在、心跳没吞异常
        scene = win._current_scene()
        results["tick_errors"] = win._tick_errors
        results["scene_period"] = scene.period_name
        results["ribbon_len"] = len(win.painter.ui.ribbon or [])
        results["tray"] = bool(win.tray is not None and win.tray.available)
        results["has_weather"] = scene.has_weather
        # 1b) 配置里的时区必须真的落到引擎上。
        #     1.1.9 的 CI 就在这里翻过车：本机系统时区恰好也是 +08:00，
        #     时区没解析出来也看不出来；跑到 UTC 的 CI 上，日出日落就落进了
        #     错的那一天（日出 06:32 变成前一天 22:32），日弧直接画不出来。
        offset = win._now().utcoffset()
        results["tz_offset"] = str(offset)
        if win.engine._tzinfo is None or offset != timedelta(hours=8):
            problems.append(f"配置的时区（Asia/Shanghai）没生效："
                            f"engine._tzinfo={win.engine._tzinfo}，_now()={win._now()}")

        # 2) 菜单里写的动作，一个都不能少（AGENTS §3.2 那条老毛病）
        menu_actions: set[str] = set()
        walk_menu(win.menu_model, menu_actions)
        win_actions = set(win.list_actions())
        app_actions = set(app.list_actions())
        missing = sorted(
            name for name in menu_actions
            if not ((name.startswith("win.") and name[4:] in win_actions)
                    or (name.startswith("app.") and name[3:] in app_actions)))
        results["menu_actions"] = len(menu_actions)
        results["missing_actions"] = missing
        if missing:
            problems.append(f"菜单里写了但没注册的动作：{missing}")

        # 3) 每个动作都得有个分类（能安全激活 / 说明为什么不激活）
        known = {f"win.{n}" for n in win_actions} | {f"app.{n}" for n in app_actions}
        unclassified = sorted(known - set(SAFE_TO_ACTIVATE) - set(NOT_ACTIVATED))
        results["unclassified_actions"] = unclassified
        if unclassified:
            problems.append(f"新动作没有分类：{unclassified}")

        # 4) 勾选项必须是"参数类型 None + 布尔状态"（否则菜单会画成灰的、
        #    或者一激活就撞 GLib 断言），单选项必须是"参数类型 = 状态类型 's'"。
        toggles, radios, plain = set(), set(), set()
        for name in sorted(win_actions):
            act = win.lookup_action(name)
            ptype = act.get_parameter_type()
            state = act.get_state()
            if ptype is None and state is not None and state.get_type_string() == "b":
                toggles.add(name)
            elif ptype is not None and state is not None \
                    and ptype.dup_string() == state.get_type_string():
                radios.add(name)
            elif ptype is None and state is None:
                plain.add(name)
            else:
                problems.append(f"{name} 的参数/状态类型怪："
                                f"param={ptype and ptype.dup_string()} "
                                f"state={state and state.get_type_string()}")
        results["toggles"] = sorted(toggles)
        results["radios"] = sorted(radios)
        if toggles != EXPECTED_TOGGLES:
            problems.append(f"勾选项清单变了：多了 {sorted(toggles - EXPECTED_TOGGLES)}，"
                            f"少了 {sorted(EXPECTED_TOGGLES - toggles)}")
        if radios != EXPECTED_RADIOS:
            problems.append(f"单选项清单变了：{sorted(radios)}")
        all_names = {f"win.{n}" for n in plain | toggles | radios} \
            | {f"app.{n}" for n in app_actions}
        missing_actions = sorted((set(SAFE_TO_ACTIVATE) | set(NOT_ACTIVATED))
                                 - all_names)
        results["actions_expected_but_gone"] = missing_actions
        if missing_actions:
            problems.append(f"清单里有、窗口里没有的动作：{missing_actions}")

        # 5) 挨个激活"可以安全激活"的那些，不许抛异常
        fired = []
        for name, target in SAFE_TO_ACTIVATE.items():
            scope, _, short = name.partition(".")
            act = (app.lookup_action(short) if scope == "app" else win.lookup_action(short))
            if act is None:
                problems.append(f"{name} 没注册")
                continue
            try:
                variant = GLib.Variant(act.get_parameter_type().dup_string(), target) \
                    if act.get_parameter_type() is not None else None
                act.activate(variant)
                fired.append(name)
            except Exception as exc:                     # noqa: BLE001 - 就是要抓住
                problems.append(f"激活 {name} 抛了 {type(exc).__name__}: {exc}")
        results["activated"] = fired

        # 6) 勾选项要真的翻得动（set_toggle 是 AGENTS §3.2 指定的唯一姿势）
        win.set_toggle("info", False)
        results["info_off"] = win.action_state("info")
        win.set_toggle("info", True)
        results["info_on"] = win.action_state("info")
        if results["info_off"] or not results["info_on"]:
            problems.append("set_toggle 没有把勾选项翻过来")

        # 7) 托盘 Activate：以前这里少传一个参数，直接 TypeError
        inv = FakeInvocation()
        try:
            win.tray._on_sni_call(None, None, None, None, "Activate", [], inv)
            results["tray_activate"] = "ok" if inv.replied else "没回复"
        except Exception as exc:                          # noqa: BLE001
            results["tray_activate"] = f"{type(exc).__name__}: {exc}"
        if results["tray_activate"] != "ok":
            problems.append(f"托盘 Activate：{results['tray_activate']}")

        # 7b) 信息卡：离屏画一帧，上面能点的每一块都得在，点了要真有效果
        import cairo as _cairo
        win.set_toggle("infocompact", False)     # 先按"展开"的样子验（上一节可能翻过它）
        surf = _cairo.ImageSurface(_cairo.FORMAT_ARGB32, 1000, 640)
        cr = _cairo.Context(surf)
        sc = win._current_scene()
        win._refresh_ribbon(sc)
        win.painter.draw(cr, 1000, 640, sc, 180.0)
        results["ribbon_info_len"] = len(win.painter.ui.ribbon_info)
        if len(win.painter.ui.ribbon_info) != len(win.painter.ui.ribbon):
            problems.append("长卷的天气注脚与格子数对不上（悬停时会指错）")
        rects = list(win.painter.ui.info_rects)
        kinds = [r[4] for r in rects]
        results["info_rows"] = len(win.painter.ui.info_rows)
        results["info_kinds"] = kinds
        for need in ("toggle", "refresh"):
            if need not in kinds:
                problems.append(f"信息卡上没有 {need} 这一块")
        if "arc" not in kinds:
            problems.append("信息卡上没有画出日弧")
        opens = [r for r in rects if r[4] == "open" and r[5] is not None]
        if not opens:
            problems.append("信息卡上没有可以点着跳过去的时刻")
        else:                       # 点最早那一行（日出）应当停在它那一刻
            row = opens[0]
            win._set_preview(None)
            win.info.activate(rects.index(row), row[0] + 2.0)
            results["info_preview"] = str(win.painter.ui.preview_dt)
            if win.painter.ui.preview_dt is None:
                problems.append("点信息卡里的一行没有进入预览")
        if "arc" in kinds:          # 点日弧中点应当落在白天里
            arc = next(r for r in rects if r[4] == "arc")
            win._set_preview(None)
            win.info.activate(rects.index(arc), arc[0] + arc[2] / 2)
            mid = win.painter.ui.preview_dt
            rise, sett = sc.events.get("sunrise"), sc.events.get("sunset")
            results["info_arc_mid"] = str(mid)
            if not (rise and sett and mid and rise < mid < sett):
                problems.append(f"点日弧中间没落在日出与日落之间：{mid}")
        win._set_preview(None)
        win.set_toggle("infocompact", True)
        results["info_compact"] = bool(win.painter.ui.info_compact
                                       and win.config.info_compact)
        win.set_toggle("infocompact", False)
        if not results["info_compact"]:
            problems.append("信息卡的精简模式没有生效")
        # 精简模式只留时间和那句话：不该再有"行"和日弧
        win.set_toggle("infocompact", True)
        win.painter.draw(cr, 1000, 640, sc, 180.0)
        slim = [r[4] for r in win.painter.ui.info_rects]
        win.set_toggle("infocompact", False)
        results["info_compact_kinds"] = slim
        if any(k in ("open", "arc", "detail") for k in slim):
            problems.append(f"精简模式下还画了事实行：{slim}")

        # 7b-2) 右上角那颗"收起/展开"：**点它**要真的收起来。
        #       1.1.9 死在这里——处理器写的是 set_toggle("info_compact")，
        #       而注册的动作叫 infocompact，lookup 找不到就静默返回，
        #       于是"点了没反应"。这里直接照鼠标点击那条路走一遍。
        win.set_toggle("infocompact", False)
        win.painter.draw(cr, 1000, 640, sc, 180.0)
        toggle = next((i for i, r in enumerate(win.painter.ui.info_rects)
                       if r[4] == "toggle"), -1)
        results["info_toggle_rect"] = toggle
        if toggle < 0:
            problems.append("信息卡上没有可点的收起/展开发方块")
        else:
            rect = win.painter.ui.info_rects[toggle]
            win.info.activate(toggle, rect[0] + rect[2] / 2)
            click_compact = bool(win.config.info_compact and win.painter.ui.info_compact)
            results["info_toggle_click"] = click_compact
            if not click_compact:
                problems.append("点信息卡右上角没有收起（动作名对不上？）")
            win.painter.draw(cr, 1000, 640, sc, 180.0)
            back = next((i for i, r in enumerate(win.painter.ui.info_rects)
                         if r[4] == "toggle"), -1)
            win.info.activate(back, win.painter.ui.info_rects[back][0] + 5)
            results["info_toggle_click_back"] = bool(win.config.info_compact)
            if win.config.info_compact:
                problems.append("再点一次没有展开回来")

        # 7b-3) 快捷键：空格 / C / 左右 / R。以前是"事件送进来了没人接"
        #       （焦点留在菜单弹层里那颗小按钮上），现在至少处理器这一层
        #       必须真的动起来——按下键盘走的就是这个函数。
        from gi.repository import Gdk as _Gdk

        def press(name):
            return win.keys.on_key(None, _Gdk.keyval_from_name(name), 0, 0)

        win.set_toggle("info", True)
        handled_space = press("space")
        results["key_space"] = [handled_space, bool(win.painter.ui.show_info)]
        if not handled_space or win.painter.ui.show_info:
            problems.append("空格没有收起信息卡")
        press("space")
        if not win.painter.ui.show_info:
            problems.append("再按空格没有把信息卡放回来")
        win.set_toggle("infocompact", False)
        handled_c = press("c")
        results["key_c"] = [handled_c, bool(win.config.info_compact)]
        if not handled_c or not win.config.info_compact:
            problems.append("C 键没有切到精简模式（动作名对不上？）")
        press("c")
        win._set_preview(None)
        press("Right")
        results["key_right"] = str(win.painter.ui.preview_dt)
        if win.painter.ui.preview_dt is None:
            problems.append("右方向键没有进入预览")
        win._set_preview(None)
        press("Home")
        # R：重问一次天气——问出去是一句话，问完了（成功或失败）还得再说一句
        win.set_toggle("weather", True)
        win.painter.ui.toast = ""
        if not press("r"):                       # R 要接得上
            problems.append("R 键没有接上")
        results["key_r_toast"] = win.painter.ui.toast
        if "问" not in (win.painter.ui.toast or ""):
            problems.append("按 R 没有任何回声（成功 / 失败都该说一句）")

        # 7b-4) 焦点必须留在"这幅画"上。菜单弹层关掉之后焦点会留在弹层里
        #       那颗小按钮上，那块画布跟窗不是同一块——键盘事件就此没有了
        #       回声，看起来就是"窗口在最前面却按不动"。这里钉住这条状态。
        def pump(seconds: float, until=None) -> None:
            """把主循环转一会儿（弹层开/关要几帧才走完）。嵌套 iteration 没问题。"""
            end = time.monotonic() + seconds
            ctx = GLib.MainContext.default()
            while time.monotonic() < end:
                while ctx.pending():
                    ctx.iteration(False)
                if until is not None and until():
                    return
                time.sleep(0.02)

        win.menu_button.popup()
        pump(0.4)
        win.menu_button.popdown()
        pump(0.4)
        focused = win.get_focus()
        same = focused is not None and focused.get_native() is win.get_native()
        results["focus_after_menu"] = f"{type(focused).__name__}·同画布={same}"
        if focused is not win.area:
            problems.append(f"菜单关掉之后焦点没回到画面上：{type(focused).__name__}")

        # 7b-5) 壁纸上的信息卡版式：跟随窗口 / 精简 / 完整
        from chuang.wallpaper_ctl import info_compact_for
        results["wallpaper_info_modes"] = {
            "follow": [info_compact_for("follow", True),
                       info_compact_for("follow", False)],
            "slim": [info_compact_for("slim", True), info_compact_for("slim", False)],
            "full": [info_compact_for("full", True), info_compact_for("full", False)],
        }
        if results["wallpaper_info_modes"] != {"follow": [True, False],
                                              "slim": [True, True],
                                              "full": [False, False]}:
            problems.append(f"壁纸信息卡的三档没对上：{results['wallpaper_info_modes']}")
        win.activate("wallpaperinfomode", GLib.Variant.new_string("slim"))
        if win.config.wallpaper_info_mode != "slim" or not win.wallpaper.compact():
            problems.append("壁纸信息卡版式没有写进配置")
        win.activate("wallpaperinfomode", GLib.Variant.new_string("follow"))
        if win.config.wallpaper_info_mode != "follow":
            problems.append("壁纸信息卡版式改回'跟随'失败")

        # 7b-6) 预览到远处的一天：那天必须**真的去问一次**，问回来的要并进手里
        #       这份；同时"此刻"那份不能被顶掉（这是 1.1.11 的天气改造）。
        import chuang.weather as wmod
        from datetime import datetime as _dt, time as _dtime
        today = win._now().date()
        far = today + timedelta(days=9)
        asked: list = []

        def fake_fetch(lat, lon, tz="auto", start_date=None, end_date=None,
                       forecast_days=7):
            asked.append((start_date, end_date))
            first = start_date or today
            last = end_date or (today + timedelta(days=forecast_days - 1))
            w = wmod.Weather(ok=True, fetched_at=time.time(), code=3, cloud=88.0,
                             temp=11.0, apparent=10.0, humidity=70.0, precip=0.0,
                             lat=lat, lon=lon)
            day = first
            while day <= last:
                base = _dt.combine(day, _dtime(0, 0))
                for h in range(24):
                    w.hourly.append(wmod.HourPoint(base + timedelta(hours=h), 88.0,
                                                   3, 11.0, 40.0))
                day += timedelta(days=1)
            w.invalidate()
            return w

        allow = win.weather.allow_fetch
        win.weather.allow_fetch = False          # 先别让它去碰真网络
        pump(14, until=lambda: not win.weather._busy)
        with mock.patch.object(wmod, "fetch", side_effect=fake_fetch):
            win.weather.allow_fetch = True
            win.weather.refresh(force=True)
            pump(6, until=lambda: bool(win.weather.weather
                                       and win.weather.weather.has_day(today)))
            results["weather_days_after_first"] = (
                win.weather.weather.day_span() if win.weather.weather else None)
            tzinfo = win._now().tzinfo
            win._set_preview(_dt.combine(far, _dtime(14, 0), tzinfo=tzinfo))
            pump(6, until=lambda: bool(win.weather.weather
                                       and win.weather.weather.has_day(far)))
            # JSON 里放字符串（date 不能直接序列化）
            results["weather_asked_for_far"] = [
                str(x) for x in asked[-1] if x is not None] if asked else None
            far_scene = win._current_scene()
            results["far_preview"] = [far_scene.has_weather, far_scene.weather_now,
                                      round(far_scene.cloud), far_scene.weather_nodata]
            if not far_scene.has_weather or far_scene.weather_now \
                    or far_scene.weather_nodata:
                problems.append(f"跳到 {far} 之后那天的天气还是不对："
                                f"{results['far_preview']}")
            win._set_preview(None)
            now_scene = win._current_scene()
            results["now_after_preview"] = [now_scene.has_weather,
                                            now_scene.weather_now]
            if not now_scene.has_weather or not now_scene.weather_now:
                problems.append("从预览回到此刻之后，天气没了（被补问那份顶掉了？）")
            # 真的没有那天的数据时（超出预报范围），画面要老实说"没有预报"
            past_limit = today + timedelta(days=40)
            win._set_preview(_dt.combine(past_limit, _dtime(14, 0), tzinfo=tzinfo))
            nodata_scene = win._current_scene()
            results["beyond_forecast"] = [nodata_scene.has_weather,
                                          nodata_scene.weather_nodata]
            if nodata_scene.has_weather or not nodata_scene.weather_nodata:
                problems.append("40 天以后的日子还画着天气（应该是'这天还没有预报'）")
            win._set_preview(None)
        win.weather.allow_fetch = allow

        # 7c) 帧率可调：选一个就得记下来，而且立刻按新节奏走
        win.activate("framerate", GLib.Variant.new_string("120"))
        results["framerate_120"] = win.config.frame_rate
        if win.config.frame_rate != 120:
            problems.append("画面流畅度没有写进配置")
        if not 0 < win.frames.interval() <= 1.0:
            problems.append(f"帧间隔不合理：{win.frames.interval()}")
        win.activate("framerate", GLib.Variant.new_string("60"))
        if win.config.frame_rate != 60:
            problems.append("画面流畅度改回 60 失败")

        # 8) 五个自绘对话框都得能建出来（GTK4.6 上没有 Adw 的新控件）
        try:
            opened = []
            for maker in (
                lambda: dlg.CloseDialog(win, True, lambda *_a: None),
                lambda: dlg.DetailDialog(win, "标题", "正文"),
                lambda: dlg.ChoiceDialog(win, "标题", "正文",
                                         [("现在重启", "now", True),
                                          ("稍后", "later", False)]),
                lambda: dlg.TimeTravelDialog(win, win._now(), win.engine._tzinfo,
                                             lambda *_a: None, lambda: None),
                lambda: dlg.CityDialog(win, lambda *_a: None, win.toast),
            ):
                d = maker()
                opened.append(type(d).__name__)
                d.destroy()
            results["dialogs"] = opened
        except Exception as exc:                          # noqa: BLE001
            problems.append(f"对话框建不出来：{type(exc).__name__}: {exc}")

        # 9) 天气开关：关掉之后画面里不能再有天气（1.1.7 的毛病）
        win.set_toggle("weather", False)
        off = win._current_scene()
        results["weather_off_has_weather"] = off.has_weather
        results["weather_off_marked"] = off.weather_disabled
        win.set_toggle("weather", True)
        if off.has_weather or not off.weather_disabled:
            problems.append("关掉天气之后画面里还有天气")

        # 10) 换城市：楼群与缓存都得跟着换
        from chuang import config as cfgmod
        before = win._current_scene().location_name
        win._set_location(cfgmod.Location(name="柏林", admin="", country="德国",
                                          lat=52.52, lon=13.40,
                                          timezone="Europe/Berlin"))
        after = win._current_scene()
        results["city_change"] = f"{before} → {after.location_name}"
        results["city_tz"] = str(win.engine._tzinfo)
        if after.location_name != "柏林" or results["city_tz"] != "Europe/Berlin":
            problems.append("换城市没有生效")
        if win.painter._sky.surf is not None and before == after.location_name:
            problems.append("换城市没有让离屏缓存失效")

        results["tick_errors_after"] = win._tick_errors
        results["last_tick_error"] = win._last_tick_error or "-"

    GLib.timeout_add_seconds(6, probe)
    # 兜底：万一连探针都没被调到（窗口没建起来之类），十秒后也要退出
    # 兜底：探针里有"等上一次联网抓取结束"这种等待，给宽一点
    GLib.timeout_add_seconds(30, lambda: (problems.append("超时：探针没有跑完"),
                                          app.quit(), False)[-1])
    app.run([])

    results["problems"] = problems
    print(json.dumps(results, ensure_ascii=False, indent=1))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
