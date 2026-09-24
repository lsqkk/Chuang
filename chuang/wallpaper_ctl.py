"""「把此刻的天空放到桌面上」这一整摊：接管、跟随、动态壁纸、还原、诊断。

从 `app.py` 里搬出来的。搬的原则是：**这里只管壁纸**，窗口那边只剩
"什么时候该叫我"（心跳、换城市、关窗），用户可见的反馈一律走 `win.toast()`。

三件容易踩的事都集中在这个文件里，读的时候留意：

1. 常态是**就地重写桌面正在显示的那张图**（理由见 wallpaper.py 顶部的说明），
   只有"接管"与每 `FLIP_EVERY` 秒一次的兜底才真的换文件名。
2. 自动跟随必须 `quiet=True`，否则提示条会永远挂在窗口上。
3. 桌面上的图被用户换掉之后要**停手**，不能每 10 秒把人家覆盖回去。
"""

from __future__ import annotations

import time as _time

from . import config as cfgmod
from . import wallpaper as wallmod

# 默认的壁纸跟随间隔（秒）；用户可在菜单里改成 30 秒 / 1 分钟
DEFAULT_INTERVAL = 10
# 每隔这么久做一次"换名字接管"（常态是就地更新正在显示的那张文件；
# 偶尔真的换一次 URI，兜底那种"shell 的监听链路断了"的极端情况）
FLIP_EVERY = 300.0

# 壁纸上那张「此刻的事实」的版式说明（值见 config.INFO_MODES）
INFO_MODE_TEXT = {"follow": "跟窗口一致", "slim": "精简一条", "full": "完整版"}


def info_compact_for(mode: str, window_compact: bool) -> bool:
    """"壁纸上的信息卡要不要精简"——由设置与窗口此刻的样子一起决定。

    以前壁纸上永远是完整版：窗口收成一条了，桌面上的卡片还摊着（反过来也
    一样，只能在应用里收）。所以这里给三档：跟随窗口 / 永远精简 / 永远完整。
    """
    if mode == "slim":
        return True
    if mode == "full":
        return False
    return bool(window_compact)          # follow（默认）与认不出的值都按"跟随"


class WallpaperController:
    """窗口的壁纸管家。构造时就把渲染线程（Worker）收进自己手里。"""

    def __init__(self, win, worker) -> None:
        self.win = win
        self.worker = worker
        self.set_by_us = False        # 本进程有没有成功把壁纸换成我们的
        self.last_at = 0.0
        self.last_ok: bool | None = None
        self.last_msg = ""
        self.last_flip_at = _time.time()      # 上次真正换过 URI 的时刻
        # 壁纸立刻来一次：开机后桌面还挂着"上次关机那一刻"的图，越早换掉越好
        # （以前要等 2.5 秒，而且只有这一次机会；现在按 0.4 秒排第一张）。
        self.next_at = _time.monotonic() + 0.4
        self.next_check = _time.monotonic() + 2.0

    # ---- 与窗口的接口 -------------------------------------------------
    @property
    def config(self):
        return self.win.config

    @property
    def busy(self) -> bool:
        return self.worker.busy

    def opts(self) -> tuple[bool, bool]:
        """壁纸上要不要带「此刻的事实」与「今日天色」长卷。"""
        return (bool(self.config.wallpaper_show_info),
                bool(self.config.wallpaper_show_ribbon))

    def compact(self) -> bool:
        """壁纸上的信息卡要不要精简（跟随窗口 / 精简 / 完整，见 config.INFO_MODES）。"""
        return info_compact_for(self.config.wallpaper_info_mode,
                                bool(self.win.painter.ui.info_compact))

    def scene_opts(self) -> dict:
        """窗口里那一组"窗外画什么"的开关，交给壁纸那份画笔（见 config）。

        不一起同步就会分叉：窗口里把行人和车收起来了，桌面上照样有人走。
        """
        return {field: bool(getattr(self.config, field, True))
                for field, _label in cfgmod.SCENE_SWITCHES}

    def _render_target(self):
        """渲染用的一致性参数：地点、屏幕、天气（关掉天气时是 None）、底部余量。

        `inset` 是屏幕底下被面板 / dock 占掉的高度，**必须在主线程读**
        （Gdk 不是线程安全的），再交给渲染线程去画长卷的位置。
        """
        loc = self.config.location
        self.worker.location(loc.lat, loc.lon, loc.timezone, loc.label)
        return (wallmod.screen_size(), self.win._weather_for_paint(),
                not self.win.weather.enabled, wallmod.screen_bottom_inset())

    def schedule_soon(self) -> None:
        """下一次心跳立刻来一张（换城市、睡眠唤醒之后用）。"""
        self.next_at = 0.0

    # ---- 换图 ---------------------------------------------------------
    def remember(self) -> None:
        """第一次动壁纸前，把原来那张记下来，方便还原。

        只记"别人的"壁纸：如果此刻挂着的已经是我们自己画的槽位文件，
        就绝不能把它当成"原来的壁纸"——否则「还原成原来的壁纸」还回去的
        是一张过期的天空，用户真正的壁纸就永久丢了。

        连**缩放方式**一起记：接管时我们一定会写 picture-options=zoom（天空得
        铺满），不记下来的话，「还原」就只能还原图片、还原不了他原来是拉伸/居中。
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
        self.config.prev_wallpaper_options = wallmod.current_options()
        self.config.save()

    def apply(self, quiet: bool = False) -> None:
        """把此刻的天空画成壁纸（一张）。"""
        if self.busy:
            return
        self.remember()
        show_info, show_ribbon = self.opts()
        compact = self.compact()
        size, weather, weather_off, inset = self._render_target()
        # 写哪一张？**写桌面此刻正在显示的那一张**。
        #
        # gnome-shell 把壁纸的解码结果按文件缓存，而且只监听"当前显示的那个
        # 文件"：它有变化才会 purge + 重读。反过来，如果我们写的是另一张、
        # 再把 URI 切过去，shell 会拿"这张文件上一次的解码结果"直接显示——
        # 桌面上就会出现这个文件**上一版/上几版**的画面（时间也就是旧的）。
        # 所以常态是就地更新；只有"接管"或偶尔兜底时才真的换一次文件名。
        shown = wallmod.shown_slot()
        due_flip = (_time.time() - self.last_flip_at) > FLIP_EVERY
        if shown >= 0 and not due_flip:
            slot, adopt = shown, False
        else:
            other = 1 - (self.config.wallpaper_slot % 2)
            slot = (1 - shown) if shown >= 0 else other
            adopt = True
            self.last_flip_at = _time.time()
        self.worker.render_now(
            self.win.engine.local_now(), weather, show_info, show_ribbon, size, slot,
            lambda ok, msg, slot: self._done(ok, msg, slot, quiet),
            adopt=adopt, weather_off=weather_off, compact=compact, inset=inset,
            scene_opts=self.scene_opts())
        if not quiet:
            self.win.toast("正在把这扇窗挂到桌面上…", 2.0)

    def _done(self, ok: bool, msg: str, slot: int, quiet: bool = False) -> bool:
        self.config.wallpaper_slot = slot
        self.config.save()          # 记下槽位，只在"桌面挂着别人的图"时当兜底
        self.set_by_us = bool(ok)
        self.last_at = _time.time()
        self.last_ok = bool(ok)
        self.last_msg = msg
        # 自动跟随不弹提示，否则提示会一直挂在屏幕上
        if not quiet or not ok:
            self.win.toast(msg, 4.5 if ok else 6.0)
        return False

    # ---- 心跳里的两件事 -----------------------------------------------
    def tick(self, now: float) -> None:
        """每次心跳调用：该换就换，另做一次"桌面到底显示的是哪张"的自查。"""
        if not self.config.wallpaper_auto:
            return
        # 跟随：按秒表走，不受"分钟变化"限制（收进托盘也照常更新）
        if now >= self.next_at:
            step = int(self.config.wallpaper_interval or DEFAULT_INTERVAL)
            self.next_at = now + max(5, step)
            self.apply(quiet=True)
        # 兜底自查：桌面挂着的那张如果比另一张还旧，说明上一次换图没被接受
        # （URI 没变、GNOME 没重读、dconf 抽风……），立刻把新的那张顶上。
        if now >= self.next_check:
            self.next_check = now + 5.0
            self._self_check()

    def _self_check(self) -> None:
        shown = wallmod.shown_uri()
        if shown and not wallmod.is_our_uri(shown):
            # 桌面挂着别人的图：多半是用户自己去「外观」里换了壁纸。
            # 那就别再抢（抢起来就是"两个东西打架"），把跟随关掉并说明。
            if self.set_by_us:
                self.set_by_us = False
                self.win.set_toggle("wallpaperauto", False)
                self.win.toast_detailed(
                    "你换了壁纸，「窗」就不再自动跟着了", 9.0,
                    "检测到桌面壁纸已经换成别的了，所以「窗」停手，不再每 10 秒覆盖它。\n\n"
                    "想让它继续跟着此刻走：菜单 → 桌面壁纸 → 壁纸跟随此刻。\n"
                    "想保留刚才那张天空：不用做什么。")
            return
        stale = wallmod.stale_shown_slot()
        if stale is not None:
            ok, _ = wallmod.set_wallpaper(wallmod.SLOTS[stale])
            if ok:
                wallmod.touch(wallmod.SLOTS[stale])   # 逼 shell 丢掉旧解码
            self.next_at = 0.0                        # 顺手重画一张最新的

    # ---- 菜单里那几个动作 ---------------------------------------------
    def act_now(self, *_):
        self.apply()

    def act_auto(self, want: bool):
        self.config.wallpaper_auto = want
        if want:
            self.config.wallpaper_dynamic = False    # 两条路互斥，别同时挂着
        self.config.save()
        self.next_at = _time.monotonic() + self.config.wallpaper_interval
        if want:
            self.apply()
            self.win.toast(f"壁纸每 {self.config.wallpaper_interval} 秒跟着此刻换一张"
                           "（要一直更新，记得把「窗」留在托盘里）", 6.0)
        else:
            self.win.toast("壁纸不再自动更新，现在这张会留着", 4.0)

    def act_interval(self, value: str):
        try:
            seconds = int(value)
        except (TypeError, ValueError):
            return
        if seconds not in cfgmod.WALLPAPER_INTERVALS:
            return
        self.config.wallpaper_interval = seconds
        self.config.save()
        self.next_at = _time.monotonic() + seconds
        text = {10: "10 秒", 30: "30 秒", 60: "1 分钟"}[seconds]
        self.win.toast(f"壁纸会每 {text}跟着此刻换一张" if self.config.wallpaper_auto
                       else f"壁纸跟随的节奏已设为 {text}")

    def act_info(self, want: bool):
        self.config.wallpaper_show_info = want
        self.config.save()
        if self.config.wallpaper_auto:
            self.apply(quiet=True)
        self.win.toast("壁纸上会带上「此刻的事实」" if want else "壁纸只留下景色", 3.5)

    def act_info_mode(self, value: str):
        """壁纸上那张卡要哪种版式：跟随窗口 / 精简一条 / 完整版。"""
        self.config.wallpaper_info_mode = value
        self.config.save()
        if self.config.wallpaper_auto:
            self.apply(quiet=True)
        text = INFO_MODE_TEXT.get(value, "跟窗口一致")
        note = "（窗口收成一条，壁纸也跟着收）" if value == "follow" else ""
        self.win.toast(f"壁纸上的信息卡：{text}{note}", 3.5)

    def act_ribbon(self, want: bool):
        self.config.wallpaper_show_ribbon = want
        self.config.save()
        if self.config.wallpaper_auto:
            self.apply(quiet=True)
        self.win.toast("壁纸上会带上「今日天色」长卷" if want else "壁纸不带长卷了", 3.5)

    def act_day(self, *_):
        """生成"离线动态壁纸"：一天切成 96 帧，写成 GNOME 的定时壁纸 XML。"""
        if self.busy:
            self.win.toast("还在画，等一下…", 3.0)
            return
        self.remember()
        show_info, show_ribbon = self.opts()
        compact = self.compact()
        size, weather, weather_off, inset = self._render_target()
        self.win.toast("正在画这一天的 96 张天色，约二十秒…", 6.0)
        self.worker.render_day(self.win.engine.local_date(), weather,
                               show_info, show_ribbon, size, 96,
                               self._progress, self._day_done,
                               weather_off=weather_off, compact=compact, inset=inset,
                               scene_opts=self.scene_opts())

    def _progress(self, done: int, total: int) -> bool:
        self.win.painter.ui.toast = f"正在画今天的天色 {done}/{total}"
        self.win.painter.ui.toast_until = _time.time() + 3.0
        self.win.area.queue_draw()
        return False

    def _day_done(self, ok: bool, msg: str) -> bool:
        if ok:
            # 先把「跟随此刻」的勾去掉（顺带改配置、存盘），再挂上动态壁纸
            self.win.set_toggle("wallpaperauto", False)
            self.config.wallpaper_dynamic = True
            self.config.save()
            self.win.toast("动态壁纸做好了：今天 24 小时会自己走一遍，不开着也有效", 7.0)
        else:
            self.win.toast(msg, 6.0)
        return False

    def act_restore(self, *_):
        if not self.config.prev_wallpaper:
            self.win.toast("没有记下你原来的壁纸；可以从「设置 → 外观」里挑一张", 5.0)
            return
        ok, msg = wallmod.restore(self.config.prev_wallpaper,
                                  self.config.prev_wallpaper_dark,
                                  self.config.prev_wallpaper_options)
        if ok:
            self.win.set_toggle("wallpaperauto", False)
            self.config.wallpaper_dynamic = False
            self.config.prev_wallpaper = ""
            self.config.prev_wallpaper_dark = ""
            self.config.prev_wallpaper_options = ""
            self.config.save()
        self.win.toast(msg, 5.0)

    def act_diagnostics(self, *_):
        """把"壁纸/时间到底怎么了"摊开成一段可复制的文字。

        这类毛病是"有时候"发生的，光靠转述很难查；发生的那一刻点一下这里，
        把内容贴出来就够了。
        """
        self.win._show_detail("壁纸诊断（可以整段复制）",
                              self.win._diagnostics_wallpaper())
