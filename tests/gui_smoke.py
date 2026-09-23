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
from pathlib import Path

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
    "pin", "weather", "info", "autostart", "autostarthidden",
    "wallpaperauto", "wallpaperinfo", "wallpaperribbon", "autoupdate",
}
EXPECTED_RADIOS = {"closebehavior", "wallpaperinterval"}

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
    GLib.timeout_add_seconds(10, lambda: (problems.append("超时：探针没有跑完"),
                                          app.quit(), False)[-1])
    app.run([])

    results["problems"] = problems
    print(json.dumps(results, ensure_ascii=False, indent=1))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
