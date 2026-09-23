"""「此刻的事实」这张卡上的交互：命中、点开、摊开数据。

卡片长什么样是 render.py 的事（纯绘制）；这里管的是"点了会怎样"：

* 点日出 / 日落 / 月亮 / 太阳 → 画面跳到那一刻（还是那一套预览，Esc 回来）；
* 点窗外 / 风 → 摊开完整的一份天气（体感、风、能见度、数据来源与时间）；
* 点右上角箭头 → 收起成一条；点右下角刷新 → 立刻重问一次真实天气；
* 日出到日落那条"日弧"上悬停看时刻、点一下跳过去。

这些如果都写在 ChuangWindow 里，app.py 就要长回两千行——`tests/test_project.py`
里有一条 1100 行的红线盯着这件事（AGENTS §7）。
"""

from __future__ import annotations

import time as _time

from .render import clamp
from .scene import compass
from .weather import code_text


class InfoCard:
    """挂在窗口上的信息卡控制器：窗口把鼠标事件丢进来，它决定干什么。"""

    def __init__(self, win) -> None:
        self.win = win

    @property
    def ui(self):
        return self.win.painter.ui

    # ---- 命中 --------------------------------------------------------
    def hit(self, x: float, y: float) -> int:
        """落在信息卡哪一块上？返回 ui.info_rects 的下标，没命中是 -1。"""
        for i, (rx, ry, rw, rh, _kind, _when) in enumerate(self.ui.info_rects):
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                return i
        return -1

    def arc_time(self, x: float):
        """日弧上某个横坐标对应哪一刻（在日出与日落之间插值）。"""
        for rx, _ry, rw, _rh, kind, _when in self.ui.info_rects:
            if kind != "arc" or rw <= 0:
                continue
            sc = self.win._current_scene()
            rise, sett = sc.events.get("sunrise"), sc.events.get("sunset")
            if not rise or not sett or sett <= rise:
                return None
            return rise + (sett - rise) * clamp((x - rx) / rw, 0.0, 1.0)
        return None

    # ---- 点了干什么 --------------------------------------------------
    def activate(self, idx: int, x: float) -> None:
        win = self.win
        _rx, _ry, _rw, _rh, kind, when = self.ui.info_rects[idx]
        if kind == "toggle":
            win.set_toggle("info_compact", not win.config.info_compact)
            return
        if kind == "refresh":
            self.refresh_weather()
            return
        if kind == "arc":
            when = self.arc_time(x)
        if kind in ("arc", "open") and when is not None:
            win._set_preview(when)
            win.toast(f"正在看 {when.strftime('%H:%M')} 的窗外 · Esc 回到此刻", 3.0)
        elif kind == "detail":
            self.show_weather_detail()

    def refresh_weather(self) -> None:
        """右下角那个刷新：立刻去问一次真实天气。"""
        win = self.win
        if not win.weather.enabled:
            win.toast("「跟随真实天气」是关着的 · 菜单 → 跟随真实天气", 4.0, icon="warn")
            return
        win.weather.refresh(force=True)
        win.toast("正在问一次真实的天气…", 2.5, icon="refresh")

    def show_weather_detail(self) -> None:
        """把"窗外"那一行的底稿摊开：体感、风、能见度、数据来源与时间。"""
        win = self.win
        sc = win._current_scene()
        w = win.weather.weather
        lines = [f"{sc.location_label or sc.location_name}　{sc.when:%Y-%m-%d %H:%M}",
                 ""]
        if not win.weather.enabled:
            lines += ["「跟随真实天气」是关着的，画面里只有天文。",
                      "想让它显示真实的云和雨：菜单 → 跟随真实天气。"]
        elif w is None or not w.ok:
            lines += ["还没拿到天气数据。",
                      "天气来自 Open-Meteo（免费、不用账号，只上传经纬度）。",
                      "网络不通时画面会自动退回纯天文模式，天空依然是对的。"]
        else:
            lines.append(f"现在　　{sc.weather_text}　{sc.temp:.0f}°C"
                         f"（体感 {sc.apparent:.0f}°C）")
            lines.append(f"云量　　{sc.cloud:.0f}%　　湿度 {sc.humidity:.0f}%")
            lines.append(f"风　　　{compass(sc.wind_dir)}（{sc.wind_dir:.0f}°）"
                         f"{sc.wind_speed:.1f} km/h"
                         + (f"　阵风 {w.gusts:.1f} km/h" if w.gusts else ""))
            lines.append(f"降水　　{w.precip:.1f} mm/时　"
                         f"能见度 {w.visibility / 1000:.1f} km")
            when = ("更新于 " + _time.strftime("%H:%M", _time.localtime(w.fetched_at))
                    if w.fetched_at else "")
            lines.append("")
            lines.append("数据　　Open-Meteo　" + when
                         + ("（离线，上一次的结果）" if sc.weather_stale else ""))
        lines += ["", "太阳、月亮、星星的位置全是本地算的，一点数据都不外传。"]
        win._show_detail("窗外的天气", "\n".join(lines))

    # ---- 菜单 / 快捷键那一组开关 --------------------------------------
    def act_show(self, want: bool) -> None:
        """显示 / 隐藏「此刻的事实」（空格）。"""
        self.ui.show_info = want
        self.win.config.show_info = want      # 记住它：以前重启之后卡片又自己回来了
        self.win.config.save()
        self.win.area.queue_draw()
        self.win.toast("「此刻的事实」回来了 · 按空格可以再收起" if want
                       else "信息卡收起来了 · 按空格让它回来", 2.6)

    def act_compact(self, want: bool) -> None:
        """精简模式：只留时间和那句话（小窗口、看片子时用得上）。"""
        self.ui.info_compact = want
        self.win.config.info_compact = want
        self.win.config.save()
        self.win.area.queue_draw()
        self.win.toast("信息卡收成一条了 · 按 C 展开" if want
                       else "信息卡展开了 · 每一行都可以点", 2.6)

    @staticmethod
    def ribbon_notes(ribbon, weather) -> list:
        """长卷每一格配一句天气——悬停到那一格时会冒出来。"""
        if weather is None or not weather.ok:
            return [""] * len(ribbon)
        return [f"{code_text(weather.code_at(t))} {weather.temp:.0f}°C"
                f" · 云量 {weather.cloud_at(t):.0f}%" for t, _col in ribbon]
