"""菜单与动作：窗口"能做什么"只有这一处定义。

菜单长什么样、每个入口接到哪个处理器，以前散在 app.py 的两百多行里。搬出来之后：

* 菜单是**声明式**的——读一遍 build_menu() 就知道用户看到什么；
* "菜单里写了 `win.xxx` 却忘了注册同名动作"这种老毛病（1.1.0 的「自动检查更新」
  就是这么坏的），现在由 `tests/gui_smoke.py` 遍历菜单模型逐个核对，跑不掉了；
* 勾选项 / 单选项的规矩也只有一处（参数类型留空、状态是布尔；单选 target 与
  状态类型一致），见下面两个小工厂函数。

注意：勾选项的**处理器**要写进 `win._toggle_handlers`——`set_toggle()` 靠它
"设为某状态"（GLib 的 set_state 不会触发 change-state，AGENTS 3.2）。
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import config as cfgmod

# 勾选项的动作名。菜单、托盘、快捷键、信息卡上那颗按钮都指着同一个名字，
# 名字写错一个，表现就是"点了没反应"——1.1.9 信息卡右上角的收起箭头
# 就是死在这里（处理器写的是 info_compact，注册的是 infocompact，
# set_toggle() 找不到动作就静默返回）。所以名字只在这里写一遍。
INFO_TOGGLE = "info"             # 显示「此刻的事实」（空格）
COMPACT_TOGGLE = "infocompact"   # 信息卡精简模式（C）


def build_menu(win) -> Gio.Menu:
        """菜单：顶层按"这件事管的是什么"分组，一层就能找到。

        1.1.13 又重新分了一次类——以前顶层是"看"，里面塞着窗口置顶、信息卡、
        长卷、跳到某天、帧率……这几样其实管的是三件不同的事。现在是：

            换一扇窗 / 跟随真实天气 / 沉浸全屏 / 窗口置顶      ← 最常用的
            场景 ▸     窗外画什么（行人、车辆、行道树、云、星空…）
            桌面壁纸 ▸ 搬到桌面上的那一套
            显示 ▸     这扇窗自己怎么显示（信息卡、长卷、帧率、跳时间）
            开机与关窗 ▸
            关于与帮助 ▸
            退出
        """
        menu = Gio.Menu()

        # 有新版本时置顶提示
        if win.updater.available_release is not None:
            rel = win.updater.available_release
            up = Gio.Menu()
            if rel.deb_url:
                up.append(f"下载并安装 {rel.tag}", "win.installdeb")
                up.append("只下载 .deb 安装包", "win.downloaddeb")
            up.append("打开发布页", "win.openreleases")
            up.append("跳过这个版本", "win.skipversion")
            menu.append_submenu(f"有新版本 {rel.tag} · 查看", up)

        # 一、最常用的几件事：看哪片天、点开全屏、把窗钉在最上面
        primary = Gio.Menu()
        primary.append("换一扇窗（城市）…", "win.city")
        primary.append("跟随真实天气", "win.weather")
        primary.append("沉浸全屏", "win.fullscreen")
        primary.append("窗口置顶", "win.pin")
        primary.append("把这扇窗存成图片…", "win.savepicture")
        menu.append_section(None, primary)

        # 二、场景：窗外画什么。每一项都是一个开关，再加两个"一键"。
        # 名字与配置字段来自 config.SCENE_SWITCHES，加一项只改那一处。
        scene = Gio.Menu()
        for field, label in cfgmod.SCENE_SWITCHES:
            scene.append(label, "win." + field)
        quick = Gio.Menu()
        quick.append("全部画出来（默认）", "win.sceneall")
        quick.append("只看天空（收起街上的一切）", "win.scenesky")
        scene.append_section(None, quick)
        scene.append("跳到某天某时…", "win.gotodatetime")
        menu.append_submenu("场景", scene)

        # 三、桌面壁纸（"放到桌面上"的所有开关）
        wall = Gio.Menu()
        wall.append("把此刻的天空设为桌面壁纸", "win.wallpaper")
        wall.append("让壁纸跟着此刻走（定时重画）", "win.wallpaperauto")
        w2 = Gio.Menu()
        for label, target in (("每 10 秒", "10"), ("每 30 秒", "30"), ("每 1 分钟", "60")):
            item = Gio.MenuItem.new(label, "win.wallpaperinterval")
            item.set_attribute_value("target", GLib.Variant("s", target))
            w2.append_item(item)
        wall.append_submenu("壁纸跟随的节奏", w2)
        wall.append("生成整天的动态壁纸（15 分钟一帧）", "win.wallpaperday")
        w3 = Gio.Menu()
        w3.append("壁纸上显示「此刻的事实」", "win.wallpaperinfo")
        wi = Gio.Menu()
        for label, target in (("跟窗口一致", "follow"), ("精简一条", "slim"),
                              ("完整版", "full")):
            item = Gio.MenuItem.new(label, "win.wallpaperinfomode")
            item.set_attribute_value("target", GLib.Variant("s", target))
            wi.append_item(item)
        w3.append_submenu("壁纸上信息卡的版式", wi)
        w3.append("壁纸上显示「今日天色」长卷", "win.wallpaperribbon")
        w3.append("还原成原来的壁纸", "win.wallpaperrestore")
        w3.append("壁纸诊断（时间不对时用）…", "win.wallpaperdiag")
        wall.append_section(None, w3)
        menu.append_submenu("桌面壁纸", wall)

        # 四、显示：这扇窗自己长什么样
        look = Gio.Menu()
        look.append("显示「此刻的事实」（空格）", "win.info")
        look.append("信息卡精简模式（C）", "win.infocompact")
        look.append("显示「今日天色」长卷", "win.ribbon")
        # 画面流畅度：终端性能差得远，让它自己选（见 config.FRAME_RATES）
        smooth = Gio.Menu()
        for label, target in (("省电 · 24 帧", "24"), ("流畅 · 30 帧", "30"),
                              ("均衡 · 45 帧", "45"), ("顺滑 · 60 帧（默认）", "60"),
                              ("高刷 · 120 帧", "120")):
            item = Gio.MenuItem.new(label, "win.framerate")
            item.set_attribute_value("target", GLib.Variant("s", target))
            smooth.append_item(item)
        look.append_submenu("画面流畅度（帧率）", smooth)
        menu.append_submenu("显示", look)

        # 五、开机与关窗：三个"待着的方式"收在一起
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

        # 六、关于与帮助
        about = Gio.Menu()
        about.append("检查更新…", "win.checkupdate")
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


def register_actions(win) -> None:
    app = win.app

    def add(name, handler):
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", handler)
        win.add_action(action)

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
        win.add_action(action)
        win._toggle_handlers[name] = on_set

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
        win.add_action(action)

    add("fullscreen", win._act_fullscreen)
    add("city", win._act_city)
    add("wallpaper", win.wallpaper.act_now)
    add("wallpaperday", win.wallpaper.act_day)
    add("wallpaperrestore", win.wallpaper.act_restore)
    add("wallpaperdiag", win.wallpaper.act_diagnostics)
    add("show", win._act_show)
    add("checkupdate", win.updater.act_check)
    add("openreleases", win.updater.act_open_releases)
    add("downloaddeb", win.updater.act_download)
    add("skipversion", win.updater.act_skip)
    add("reportissue", win._act_report_issue)
    add("authormain", win._act_author_main)
    add("installdeb", win.updater.act_install)
    add("gotodatetime", win._act_goto_datetime)
    add("savepicture", win._act_savepicture)
    add("restart", win.updater.act_restart)
    add("quit", win._act_quit)
    add("about", win._act_about)
    # 场景那一栏的两个"一键"（不是开关，是一组开关的批量结果）
    add("sceneall", win._act_scene_all)
    add("scenesky", win._act_scene_sky)

    add_toggle("pin", win.config.always_on_top, win._act_pin)
    add_toggle("weather", win.config.mirror_weather, win._act_weather)
    add_toggle(INFO_TOGGLE, win.painter.ui.show_info, win.info.act_show)
    add_toggle(COMPACT_TOGGLE, win.config.info_compact, win.info.act_compact)
    add_toggle("ribbon", win.config.show_ribbon, win._act_ribbon)
    add_toggle("autostart", win.config.autostart, win._act_autostart)
    add_toggle("autostarthidden", win.config.autostart_hidden,
               win._act_autostart_hidden)
    add_toggle("wallpaperauto", win.config.wallpaper_auto, win.wallpaper.act_auto)
    add_toggle("wallpaperinfo", win.config.wallpaper_show_info,
               win.wallpaper.act_info)
    add_toggle("wallpaperribbon", win.config.wallpaper_show_ribbon,
               win.wallpaper.act_ribbon)
    # 这个以前漏了注册：菜单里有「自动检查更新」，点了却没有任何反应
    add_toggle("autoupdate", win.config.update_check, win.updater.act_auto)
    add_radio("closebehavior", win.config.close_behavior, win._act_close_behavior)
    add_radio("wallpaperinterval", str(win.config.wallpaper_interval),
              win.wallpaper.act_interval)
    add_radio("wallpaperinfomode", win.config.wallpaper_info_mode,
              win.wallpaper.act_info_mode)
    add_radio("framerate", str(win.config.frame_rate), win.frames.act_rate)
    # 场景里的每一个开关：字段名 = 动作名 = render.UIState 上的名字
    # （config.SCENE_SWITCHES 一处定义，菜单与处理器都照着它来）
    for field, _label in cfgmod.SCENE_SWITCHES:
        add_toggle(field, bool(getattr(win.config, field, True)),
                   win._scene_setter(field))

    quit_action = Gio.SimpleAction.new("quit", None)
    quit_action.connect("activate", lambda *_: app.quit())
    app.add_action(quit_action)

    app.set_accels_for_action("win.fullscreen", ["F11"])
    app.set_accels_for_action("win.quit", ["<Control>q"])
