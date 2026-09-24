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

from datetime import datetime, timedelta

from . import actions as actionmod
from .render import clamp
from .scene import compass
from .weather import code_text, precip_kind, precip_label, uv_text


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
        """日弧上某个横坐标对应哪一刻。

        **这条轨道铺的是整整一天**：画的时候，日出到日落那一段上了色、其余是灰的
        （见 render._paint_info）。以前这里却把整条轨道的宽度当成"日出到日落"
        来插值——鼠标在轨道左边四分之一处（真实时间 06:00 上下）会被算成
        "日出前不久"，越往左差得越多。这就是"鼠标从一天的区间被映射到白天的
        区间"：现在照着画法反过来算，横坐标 → 一天里的比例 → 那一刻。
        """
        for rx, _ry, rw, _rh, kind, _when in self.ui.info_rects:
            if kind != "arc" or rw <= 0:
                continue
            sc = self.win._current_scene()
            day = sc.when.replace(hour=0, minute=0, second=0, microsecond=0)
            frac = clamp((x - rx) / rw, 0.0, 1.0)
            # 右端是"这一天的 24:00"，也就是 23:59，别把预览甩到第二天去
            return day + timedelta(minutes=min(1439.0, frac * 1440.0))
        return None

    # ---- 点了干什么 --------------------------------------------------
    def activate(self, idx: int, x: float) -> None:
        win = self.win
        _rx, _ry, _rw, _rh, kind, when = self.ui.info_rects[idx]
        if kind == "toggle":
            win.set_toggle(actionmod.COMPACT_TOGGLE, not win.config.info_compact)
            return
        if kind == "refresh":
            self.refresh_weather()
            return
        if kind == "arc":
            when = self.arc_time(x)
        if kind in ("arc", "open") and when is not None:
            win._set_preview(when)
            win.toast(win.preview_note(when), 3.0)
        elif kind == "detail":
            self.show_weather_detail()

    def refresh_weather(self) -> None:
        """右下角那个刷新（也是 R 键）：去问一次真实天气，并且报个结果。

        "问了一次"必须有回声：以前这里只弹一句"正在问…"就没了下文，网络
        不通、接口改了口径，都看不出来。现在成功 / 失败都会再弹一句。
        """
        win = self.win
        if not win.weather.enabled:
            win.toast("「跟随真实天气」是关着的 · 菜单 → 跟随真实天气", 4.0, icon="warn")
            return
        if win.weather.refresh(force=True, done=self._done):
            win.toast("正在问一次真实的天气…", 2.5, icon="refresh")
        else:
            # 上一次还在飞：这一枪排到它后面，回来之后立刻再问一次（见
            # WeatherService._wanted）。所以这里说的是"马上会再问"，不是"算了"。
            win.toast("上一次还没问完 · 它回来我马上再问一次", 3.0, icon="refresh")

    def _done(self, w) -> None:
        """那一次"问天气"的答案：成功了就说清楚拿到的是什么、什么时候的。"""
        win = self.win
        if w is None:
            win.toast("这次没问到天气", 5.0, icon="warn")
            return
        if not w.ok:
            win.toast(f"没问到天气：{w.error or '网络不通'}", 6.0, icon="warn")
            return
        when = ""
        if w.fetched_at:
            try:
                when = datetime.fromtimestamp(w.fetched_at,
                                              win.engine._tzinfo).strftime("%H:%M")
            except (OverflowError, OSError, ValueError):
                when = ""
        if w.stale:
            win.toast(f"这次没问到（{w.error or '网络不通'}）"
                      + (f"，还是 {when} 那份" if when else "，还是上一次那份"),
                      6.0, icon="warn")
            return
        head = f"天气更新于 {when}：{w.text} {w.temp:.0f}°C" if when else \
            f"天气拿到手了：{w.text} {w.temp:.0f}°C"
        span = w.day_span()
        tail = f"　·　附近几天（{span[0][5:]} ～ {span[1][5:]}）也一起拿回来了" if span else ""
        win.toast(head + tail, 5.5, icon="check")

    def show_weather_detail(self) -> None:
        """把"窗外"那一行的底稿摊开：体感、风、能见度、数据来源与时间。"""
        win = self.win
        sc = win._current_scene()
        # 用"此刻该用来作画的那份"：关掉天气时是 None，换了城市而新数据还没
        # 回来时也是 None（那时候手上那份是上一座城的，摊开看只会误导人）
        w = win.weather.effective
        stamp = self._stamp(w)
        lines = [f"{sc.location_label or sc.location_name}　{sc.when:%Y-%m-%d %H:%M}",
                 ""]
        if not win.weather.enabled:
            lines += ["「跟随真实天气」是关着的，画面里只有天文。",
                      "想让它显示真实的云和雨：菜单 → 跟随真实天气。"]
        elif sc.weather_nodata:
            span = w.day_span() if (w is not None and w.ok) else None
            lines += ["这一天的预报还没问到。",
                      "Open-Meteo 的预报只到 16 天以内；"
                      "手上这份覆盖的是"
                      + (f" {span[0][5:]} ～ {span[1][5:]}。" if span else " 更近的几天。"),
                      "把长卷拖远一点、或者过一会儿再看，这一天的云和雨就会补上。"]
        elif w is None or not w.ok:
            lines += ["还没拿到天气数据。",
                      "天气来自 Open-Meteo（免费、不用账号，只上传经纬度）。",
                      "网络不通时画面会自动退回纯天文模式，天空依然是对的。"]
        elif not sc.weather_now:
            # 预览到别的日子：逐小时预报里有云、天气现象、气温，
            # 但没有"体感 / 湿度 / 风"这三样（接口只给此刻的）
            lines += [f"那会儿　{sc.weather_text}　{sc.temp:.0f}°C",
                      f"云量　　{sc.cloud:.0f}%"]
            if sc.temp_max is not None and sc.temp_min is not None:
                lines.append(f"当天　　{sc.temp_min:.0f}°C ~ {sc.temp_max:.0f}°C")
            # 逐小时表里也有降水量：说"那会儿下多大"同样按真实雨量说
            if sc.precip_kind != "none":
                who = sc.precip_label or sc.weather_text
                lines.append(f"降水　　{sc.precip_mm:.1f} mm/时（{who}）")
            lines += ["",
                      "看的是别的日子，所以只报逐小时的云、天气、气温和降水量；"
                      "体感、湿度、风是此刻的读数，这里就不写了。",
                      f"数据　　Open-Meteo　更新于 {stamp}" if stamp
                      else "数据　　Open-Meteo　（逐小时预报）"]
        else:
            lines.append(f"现在　　{sc.weather_text}　{sc.temp:.0f}°C"
                         f"（体感 {sc.apparent:.0f}°C）")
            if sc.temp_max is not None and sc.temp_min is not None:
                lines.append(f"今日　　{sc.temp_min:.0f}°C ~ {sc.temp_max:.0f}°C"
                             f"（当天最高 / 最低）")
            lines.append(f"云量　　{sc.cloud:.0f}%　　湿度 {sc.humidity:.0f}%")
            if sc.dew is not None:
                lines.append(f"露点　　{sc.dew:.0f}°C")
            lines.append(f"风　　　{compass(sc.wind_dir)}（{sc.wind_dir:.0f}°）"
                         f"{sc.wind_speed:.1f} km/h"
                         + (f"　阵风 {w.gusts:.1f} km/h" if w.gusts else ""))
            mm = max(0.0, sc.precip_mm or w.precip)
            if sc.precip_kind != "none" or mm > 0:
                # 说"多大"用真实雨量那一句：毛毛雨 0.2 mm/时 和大雨 12 mm/时
                # 不能都写成"有雨"
                who = sc.precip_label or sc.weather_text or "降水"
                lines.append(f"降水　　{mm:.1f} mm/时（{who}）　"
                             f"能见度 {w.visibility / 1000:.1f} km")
            else:
                lines.append(f"降水　　此刻没有　能见度 {w.visibility / 1000:.1f} km")
            if sc.uv is not None:
                lines.append(f"紫外线　{uv_text(sc.uv)}")
            if sc.pressure:
                lines.append(f"气压　　{sc.pressure:.0f} hPa")
            lines.append("")
            lines.append("数据　　Open-Meteo　" + (f"更新于 {stamp}" if stamp else "")
                         + ("（离线，上一次的结果）" if sc.weather_stale else ""))
        lines += ["", "太阳、月亮、星星的位置全是本地算的，一点数据都不外传。"]
        win._show_detail("窗外的天气", "\n".join(lines))

    def _stamp(self, w) -> str:
        """"这份天气是什么时候问回来的"，按这扇窗所在地方的时间写。"""
        if w is None or not getattr(w, "fetched_at", 0):
            return ""
        tz = self.win.engine._tzinfo
        try:
            return datetime.fromtimestamp(w.fetched_at, tz).strftime("%m-%d %H:%M")
        except (OverflowError, OSError, ValueError):
            return ""

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
        out = []
        for t, _col in ribbon:
            code, cloud = weather.code_at(t), weather.cloud_at(t)
            if code is None or cloud is None:
                out.append("")            # 那天的预报没问回来：不编
                continue
            temp = weather.temp_at(t)
            warmth = f"{temp if temp is not None else weather.temp:.0f}°C"
            mm = weather.precip_at(t) or 0.0
            # 有雨就按雨量说"多大"：悬停到毛毛雨那一格，不该和大雨一个说法
            what = precip_label(code, mm) if precip_kind(code) != "none" else code_text(code)
            note = f"{what or code_text(code)} {warmth} · 云量 {cloud:.0f}%"
            if precip_kind(code) != "none" and mm > 0:
                note += f" · {mm:.1f} mm/时"
            out.append(note)
        return out
