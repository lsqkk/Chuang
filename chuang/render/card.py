
"""「此刻的事实」这张卡：内容、文案与排版（**画法在 cardpaint.py**）。

这里管"卡上该有什么字、各条之间怎么排"：

* `_rows()` 是这一帧的事实（图标 + 标题 + 数值 + 副值 + 点了做什么）；
* `_info_groups()` 把它们分成三层——主角 / 指标格 / 小字条（1.1.13 重排）；
* `_info_layout()` 把每一条的 y 与卡片高度一次算清（画的地方照着 `L["y"]` 下笔，
  两边不再各算一遍）；
* `_info_key()` 是这张卡的离屏缓存键——**卡上任何一个字变了，这里都得跟着变**
  （漏一个参数就是"卡片停在上一分钟 / 上一份天气"，见 `state.CacheSlot`）。
"""

from __future__ import annotations

from datetime import datetime

import cairo
import gi

gi.require_version("Pango", "1.0")
from gi.repository import Pango  # noqa: E402

from ..scene import Scene, compass, duration_zh, human_hint, phase_name_simple
from ..weather import uv_text
from .core import PainterCore
from .paint import (clamp, draw_icon, draw_text, ink_baseline, rounded_rect,
                    text_ink, wrap_cjk)
from .state import FactRow


class CardLayer(PainterCore):

    @staticmethod
    def _icon_button(cr, kind, cx, cy, size, color, alpha=0.72, hover=False):
        """一个"看着就能点"的小方块：淡底 + 一枚矢量图标。"""
        cr.set_source_rgba(1, 1, 1, 0.16 if hover else 0.08)
        rounded_rect(cr, cx - size / 2, cy - size / 2, size, size, size * 0.32)
        cr.fill()
        draw_icon(cr, kind, cx, cy, size * 0.52, color, alpha)

    @staticmethod
    def _weather_icon(scene: Scene) -> str:
        """窗外那一行该配哪枚图标——按"天上真正有什么"来选。"""
        if scene.precip_kind == "snow":
            return "snow"
        if scene.precip_kind == "rain":
            return "rain"
        if scene.fog:
            return "fog"
        return "cloud" if scene.cloud >= 55 else "sun"

    def _rows(self, scene: Scene) -> list[FactRow]:
        ev = scene.events
        rows: list[FactRow] = []
        noon = ev.get("noon")
        if scene.sun_alt > -0.9:
            rows.append(FactRow("sun", "太阳", f"{compass(scene.sun_az)} {scene.sun_az:.0f}°",
                                f"仰角 {scene.sun_alt:.1f}°", "open", noon))
        else:
            sunr_ = ev.get("sunrise")
            rising = bool(sunr_ and scene.when < sunr_)
            rows.append(FactRow("sunrise" if rising else "sunset", "太阳",
                                f"在{compass(scene.sun_az)}方地平线下",
                                "还没升起" if rising else "已经落下", "open", noon))
        sunr = ev.get("sunrise")
        golden = ev.get("golden_morning_end")
        rows.append(FactRow("sunrise", "日出",
                    sunr.strftime("%H:%M") if sunr else "极昼 / 极夜",
                            f"金色时刻至 {golden.strftime('%H:%M')}"
                            if sunr and golden else "",
                            "open", sunr))
        suns = ev.get("sunset")
        left = scene.daylight_left
        # 日落这一行的副值**不再重复"还剩多久"**——日弧那一行的右边已经写着同一句
        # 话了（同一张卡上同一件事说两遍，是"没有取舍"最典型的样子）。
        # 换成傍晚金色时刻，那是另一件值得知道的事。
        gold_pm = ev.get("golden_evening_start")
        sunset_note = (f"金色时刻 {gold_pm.strftime('%H:%M')} 起"
                       if gold_pm and gold_pm < (suns or gold_pm) else "")
        rows.append(FactRow("sunset", "日落",
                            suns.strftime("%H:%M") if suns else "极昼 / 极夜",
                            sunset_note or ("今天已过去" if suns and not left else ""),
                            "open", suns))
        mr, ms = ev.get("moonrise"), ev.get("moonset")
        note = " · ".join(x for x in (
            f"月出 {mr.strftime('%H:%M')}" if mr else "",
            f"月落 {ms.strftime('%H:%M')}" if ms else "") if x)
        rows.append(FactRow("moon", "月亮",
                            f"{phase_name_simple(scene.moon_phase)} {scene.moon_illum * 100:.0f}%",
                            note, "open", mr or ms))
        if scene.has_weather:
            # 说"多大雨"用真实雨量那一句（毛毛雨 / 小雨 / 中雨…），代码表那套
            # 说法只在没有雨量的时候顶上；雨量本身也写在旁边，看得见差别。
            what = scene.precip_label or scene.weather_text
            note = f"云量 {scene.cloud:.0f}%"
            if scene.precip_kind != "none" and scene.precip_mm > 0:
                note += f" · {scene.precip_mm:.1f} mm/时"
            rows.append(FactRow(self._weather_icon(scene), "窗外",
                                f"{what} {scene.temp:.0f}°C", note, "detail"))
            if scene.wind_speed:
                rows.append(FactRow("wind", "风",
                                    f"{compass(scene.wind_dir)} {scene.wind_speed:.1f} km/h",
                                    f"湿度 {scene.humidity:.0f}%" if scene.humidity else "",
                                    "detail"))
        elif scene.weather_nodata:
            # 跳到远一点的日子：这一天还没有预报，就直说没有
            rows.append(FactRow("cloud", "窗外", "这天还没有预报",
                                "Open-Meteo 只给 16 天以内", "detail"))
        elif scene.weather_disabled:
            # 用户自己关的天气，别写成"未联网"——那会把人指去查网络
            rows.append(FactRow("info", "窗外", "你关掉了天气 · 只看天", "", "detail"))
        else:
            rows.append(FactRow("cloud", "窗外", "未联网 · 仅天文模式", "", "detail"))
        return rows

    def _draw_info(self, cr, w, h, scene: Scene, az0, fov, direct):
        """把这张卡画上去——大部分时间其实是把缓存贴上去（见 _paint_info）。

        一帧要下上百笔（图标、文字、圆角），而这张卡只在"分钟变了 / 鼠标划过 /
        天气更新"时才真的不一样。所以整张卡离屏缓存，按 _info_key 判断要不要重画。
        """
        L = self._info_layout(w, h, scene)
        x = 0.028 * w
        y = 0.036 * h
        edge = 8 * L["scale"]                  # 给阴影留的边
        # 离屏图取整后 +1：多出的一像素保证右下角的圆角不被裁掉；
        # 但**存进去的尺寸必须和交给 stale() 的尺寸一致**，否则永远命中不了。
        box_w = int(L["card_w"] + edge * 2) + 1
        box_h = int(L["card_h"] + edge * 2) + 1
        bx, by = x - edge, y - edge
        key = self._info_key(w, h, scene)
        if self._info.stale(key, box_w, box_h):
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, box_w, box_h)
            inner = cairo.Context(surf)
            inner.translate(-bx, -by)
            self._info_rects = self._paint_info(inner, scene, x, y, L)
            self._info.store(key, surf)
        if self._info.surf is not None:
            cr.save()
            cr.set_source_surface(self._info.surf, bx, by)
            cr.paint()
            cr.restore()
        self.ui.info_rows = L["rows"]
        # 命中区每次照交不误：它是纯数据，窗口拿它判断鼠标点到了哪儿
        self.ui.info_rects = list(self._info_rects)

    def _info_key(self, w, h, scene: Scene):
        """这张卡的缓存 key——**卡片上任何一个字变了，这里就得跟着变**。

        漏一个参数，就是"卡片还显示着上一分钟 / 上一份天气"（CacheSlot 存在的
        理由正是这句话）。所以宁可多带几个：时刻、预览、悬停、精简模式、
        天气的每个字段、天色。
        """
        hover_dt = self.ui.info_hover_dt
        return (
            int(w), int(h),
            scene.when.strftime("%Y-%m-%d %H:%M"), scene.preview, scene.period_name,
            scene.location_name, scene.location_label,
            self.ui.info_hover, bool(self.ui.info_compact),
            # 画不画那些"能点的东西"也是看得见的差别（壁纸上的卡片没有鼠标）
            bool(self.ui.info_buttons),
            # 正有一枪在飞的时候，脚注与刷新图标都换了样子（看得见的差别）
            bool(self.ui.weather_busy),
            hover_dt.strftime("%H:%M") if hover_dt else "",
            scene.sun_alt >= -0.9, round(scene.moon_phase, 3),
            scene.has_weather, scene.weather_disabled, scene.weather_stale,
            scene.weather_nodata, scene.weather_now, int(scene.weather_at),
            scene.weather_text, round(scene.cloud), round(scene.temp),
            round(scene.apparent), round(scene.humidity),
            round(scene.wind_speed, 1), round(scene.wind_dir),
            scene.precip_kind, round(scene.precip_mm, 2),
            # 1.1.13 新上卡的那几样（小字条与主角副行）：**看得见的字都得进 key**
            tuple(self._info_chips(scene)),
            None if scene.uv is None else round(scene.uv),
            None if scene.dew is None else round(scene.dew),
            None if scene.pressure is None else round(scene.pressure),
            None if scene.temp_max is None else round(scene.temp_max),
            None if scene.temp_min is None else round(scene.temp_min),
            # 卡上那几行**画出来的字**（主角、指标格的标题 / 数值 / 副值）
            tuple((r.icon, r.label, r.value, r.note) for r in self._rows(scene)),
            tuple(round(c) for c in scene.mood.horizon),
            tuple(sorted((k, str(v)) for k, v in scene.events.items())),
        )

    def _info_layout(self, w, h, scene: Scene) -> dict:
        """这张卡的排版：所有尺寸都在这里一次算清。

        离屏图裁多大、卡片画多高，用的是同一份数字——分开算迟早会对不上
        （不是被裁掉一条边，就是底下多出一块空白）。
        1.1.14 第三版起连**每一条的 y** 也在这儿算：`_paint_info` 拿 `L["y"]` 直接下笔，
        不再自己一段段 `+=`（两边各算一遍的结果就是"卡片底下时而多一截空白、
        时而挤掉半行"）。

        版式分三层（1.1.13 重排）：**主角**是此刻的天气（大字温度那一块），
        **指标格**是太阳月亮这几个"点了能跳过去"的时刻（两列，省一半高度），
        其余读数（体感 / 湿度 / 风 / 能见度…）压成一排小字条。以前是一模一样的
        一长溜，谁也看不出哪个重要。
        """
        # 排版尺度：负责间距、方块、图标。**字号另算**（见 text_scale），
        # 因为字号缩过头就没法读了——小窗口里宁可卡片高一点。
        scale = clamp(min(w / 1000.0, h / 620.0), 0.78, 1.5)
        text_scale = max(scale, self.TEXT_FLOOR)
        ts = text_scale
        pad = 16 * scale
        card_w = clamp(w * 0.44, 316 * scale, 452 * scale)
        badge_w = 17 * scale                    # 指标格里那枚小图标的方框
        compact = bool(self.ui.info_compact)
        rows, hero, grid, rest = self._info_groups(scene)
        chips = [] if compact else self._info_chips(scene)
        per_line = max(8, int((card_w - pad * 2 - 12 * scale) / (12.4 * scale)))
        hint_lines = wrap_cjk(human_hint(scene), per_line)
        if compact:
            hint_lines = hint_lines[:2]
        sunr = scene.events.get("sunrise")
        suns = scene.events.get("sunset")
        show_arc = bool(sunr and suns and suns > sunr) and not compact
        # ---- 竖向节奏（1.1.14 第三版整体放松了一档，尤其是"段与段之间"）----
        # 每一条的高度里**含了它下边那条气口**，最后一条除外；`_paint_info`
        # 照着 L["y"] 画，所以这里改了、那边自然跟上。
        head_h = 31 * scale                     # 城市 · 此刻（收起的箭头在里面）
        time_h = (32 if compact else 44) * scale
        hero_pad = 9 * scale                    # 主角块里内容到块边的留白
        hero_tail = 14 * scale                  # 主角块 → 下面那条分隔线
        arc_h = 33 * scale if show_arc else 0
        div_h = 11 * scale                      # 中间那条分隔线 → 指标格
        # 指标格：标题与数值一行、副值一行。行距要留够——之前 33 太小，
        # 副值的下缘几乎贴到两行之间那条分隔线上（用户看到的"挤"）。
        # **按字号算，不按排版尺度算**：小窗口里字号有下限（TEXT_FLOOR），
        # 拿 scale 算出来的格子会把两行挤在一起（700×560 实测只剩 3 像素）。
        cell_line_gap = 17 * ts               # 格子里两行墨迹中线的距离
        cell_h = 28.6 * ts + 12 * scale
        grid_tail = 10 * scale                  # 指标格 → 小字条
        # 一列还是两列：**先算最宽的那一格放不放得下**。放不下就改成一列
        # （卡片高一点，但字不会溢到隔壁格里）——傍晚那几行是"在西方地平线下"
        # 这种长句，窄窗口里两列排必然会撞车（用户截图里就是这个）。
        two_col_w = (card_w - pad * 2 - 8 * scale) / 2.0
        # 一格真正需要的宽度：图标 + 气口 + **标题与数值里更宽的那个**（数值从固定
        # 的偏移起画，标题几乎总比它窄）+ 箭头。算宽了会白白退成一列（卡片变高），
        # 算窄了字会溢到隔壁格——所以这里按真实的画法算。
        arrow_w = 11 * scale if self.ui.info_buttons else 0.0
        widest = max((badge_w + 7 * scale
                      + max(36 * scale,
                            self._text_w(r.label, self.F_LABEL * text_scale) + 4 * scale)
                      + self._text_w(r.value, self.F_VALUE * text_scale)
                      + 4 * scale + arrow_w
                      for r in grid), default=0.0)
        grid_cols = 2 if two_col_w >= widest else 1
        grid_lines = 0 if compact else ((len(grid) + grid_cols - 1) // grid_cols)
        chip_gap = 8 * scale                    # 小字条行与行之间
        chip_w = card_w - pad * 2
        # 小字条按"能放下几个就放几个"折行（见 _chip_rows）
        chip_rows = (self._chip_rows(chips, chip_w, text_scale) if chips else [])
        chip_line_h = 26 * scale
        chip_h = (len(chip_rows) * (chip_line_h + chip_gap) - chip_gap
                  if chip_rows else 0.0)
        hint_top = 12 * scale                   # 小字条 → "一句人话"
        hint_line_h = 22 * scale
        foot_h = 30 * scale                     # 脚注那一行 + 到卡片底边的留白
        # 主角块的高度**按墨迹量**：左边那两样（图标 + 大字温度）与右边那一列
        # （天气 + 副行）都要落在块的中线上，块高不够就会挤在一起。
        hero_block = self._hero_block_h(scene, hero, ts, hero_pad) \
            if (hero is not None and not compact) else 0.0
        hero_band = (hero_block + hero_tail) if hero_block else 0.0

        def bands(chips_h: float, hint_n: int, cell: float, lines: int) \
                -> list[tuple[str, float]]:
            """从上到下一条一条排：每条的 y 与卡片总高都从这里出。"""
            out = [("head", head_h), ("time", time_h)]
            if show_arc:
                out.append(("arc", arc_h))
            if hero_band:
                out.append(("hero", hero_band))
            if lines:
                out.append(("divider", div_h))
                out.append(("grid", lines * cell + grid_tail))
            if chips_h:
                out.append(("chips", chips_h))
            if hint_n:
                out.append(("hint", hint_top + hint_line_h * hint_n))
            out.append(("foot", foot_h))
            return out

        def lay_out(items: list[tuple[str, float]]):
            y_of: dict[str, float] = {}
            pos = pad * 0.75                    # 卡片顶边到第一条之间的留白
            for name, height in items:
                y_of[name] = pos
                pos += height
            return y_of, pos

        y_of, card_h = lay_out(bands(chip_h, len(hint_lines), cell_h, grid_lines))
        # **卡片不许比窗口还高**（小窗口 420×300 时一列版式会到 340 像素高，
        # 底下那截就跑到窗口外面去了）。放不下就按"最不重要的先去掉"逐级降：
        #   1 少说一句人话 → 2 收起小字条 → 3 指标格不写副值 → 4 连指标格一起收
        level = 0
        room_h = h - 2 * (y_margin := 0.036 * h)
        while card_h > room_h and level < 4:
            level += 1
            if level == 1:
                hint_lines = hint_lines[:1]
            elif level == 2:
                chip_rows, chip_h = [], 0.0
            elif level == 3:
                # 指标格只留"标题 + 数值"那一行（副值不写）
                cell_h = 11 * ts + 8 * scale
            else:
                # 最后手段：连指标格一起收（太阳月亮这些在长卷上也能看）
                grid, grid_lines = [], 0
            y_of, card_h = lay_out(bands(chip_h, len(hint_lines), cell_h,
                                         grid_lines))
        return {
            "scale": scale, "tscale": text_scale, "pad": pad,
            "card_w": card_w, "card_h": card_h,
            "rows": rows, "hero": hero, "grid": grid, "rest": rest,
            "chips": chips, "chip_rows": chip_rows,
            "compact": compact, "hint_lines": hint_lines,
            "show_arc": show_arc, "sunr": sunr, "suns": suns,
            "y": y_of, "hero_block": hero_block, "cell_h": cell_h,
            "cell_line_gap": cell_line_gap,
            "grid_cols": grid_cols, "grid_lines": grid_lines,
            "chip_gap": chip_gap, "chip_w": chip_w, "chip_line_h": chip_line_h,
            "foot_h": foot_h,
            "hint_top": hint_top, "hint_line_h": hint_line_h,
            "notes": level < 3, "dense": level,
            "accent": scene.mood.horizon,
        }

    def _hero_texts(self, scene: Scene, hero) -> tuple[str, str, str]:
        """主角块上的三样字：大字温度 / 右边那句话天气 / 它底下那一行小字。

        没有天气时（"未联网 · 仅天文模式"）温度那一格空着，只有右边那两样。
        """
        if hero is None:
            return "", "", ""
        if not scene.has_weather:
            return "", hero.value, hero.note or ""
        return (f"{scene.temp:.0f}°",
                scene.precip_label or scene.weather_text or hero.value,
                self._hero_sub(scene))

    def _hero_block_h(self, scene: Scene, hero, ts: float,
                      hero_pad: float) -> float:
        """主角块该多高：按**墨迹**算，不按字体的 ascent/descent。

        块里要放两样：左边那行大字温度、右边那两行（天气 + 副行）。高的那个
        加上下留白就是块高——留白不够，温度与副行就会互相挤（1.1.14 那版把
        块高写成 `62*scale` 的死数，一换内容就不对了）。
        没有天气时右边只有**一行**"为什么没有"（+ 一小段注脚），别按两行算，
        不然那一块会平白高出一行。
        """
        temp, what, sub = self._hero_texts(scene, hero)
        if temp:
            top, bottom = text_ink(temp, self.F_HERO_TEMP * ts, Pango.Weight.LIGHT)
            inner = bottom - top
            wt, wb = text_ink(what, self.F_HERO_WHAT * ts)
            st, sb = text_ink(sub, self.F_HERO_SUB * ts)
            inner = max(inner, (wb - wt) + self.HERO_GAP * self.F_HERO_WHAT * ts
                        + (sb - st))
        else:
            top, bottom = text_ink(what, self.F_ANCHOR * ts, Pango.Weight.MEDIUM)
            inner = bottom - top
        return inner + 2 * hero_pad

    def _date_text(self, scene: Scene, room: float, size: float) -> str:
        """大字时间右边那行日期：窗口窄的时候**先说短的**。

        它跟底下的脚注一样是"能说多少说多少"——宁可少写"金色时刻"，也不让它
        伸出卡片外面（小窗口里以前会探出边框，被离屏图硬切掉半句话）。
        """
        weekday = "一二三四五六日"[scene.when.weekday()]
        head = f"{scene.when.month} 月 {scene.when.day} 日 · 周{weekday}"
        for variant in (f"{head} · {scene.period_name}", head,
                        f"{scene.when.month}/{scene.when.day}"):
            if self._text_w(variant, size) <= room:
                return variant
        return f"{scene.when.month}/{scene.when.day}"

    def _info_groups(self, scene: Scene):
        """把 `_rows()` 那几行分成三层：主角 / 指标格 / 小字条。

        "窗外"那一行是主角（此刻真正在下什么）；点了能跳到某一刻的（太阳、
        日出、日落、月亮）做成指标格；其余的（风、湿度…）归小字条那一排。
        这样分类，加一行新的事实只要给它一个 action，自己就会落到该去的位置。

        注意第三层实际画的是 `_info_chips()` 那一份（它比 `_rows` 多几样接口
        给的读数，比如紫外线与气压），`rest` 只是"哪些行归这一层"的分类结果。
        """
        rows = self._rows(scene)
        hero = next((r for r in rows if r.label == "窗外"), None)
        grid = [r for r in rows if r is not hero and r.action == "open"]
        rest = [r for r in rows if r is not hero and r.action != "open"]
        rest = [r for r in rest if r.action == "detail"] or rest
        return rows, hero, grid, rest

    @staticmethod
    def _info_chips(scene: Scene) -> list:
        """一排小字条：那些"看一眼就好"的读数（体感 / 湿度 / 风 / 能见度…）。

        这一排是**卡片上的第三层**：字号最小、颜色最淡——它重要，但不是最重要的。
        有哪几项就显示哪几项（数据没回来就不写），没联网的时候整排不出现。
        体感与今日高低温归主角那一块（见 _hero_sub），这里不重复。
        """
        out: list[tuple[str, str]] = []
        if not scene.has_weather:
            return out
        if not scene.weather_now:
            # 看的是别的日子：逐小时表里只有云、天气、气温、降水
            out.append(("云量", f"{scene.cloud:.0f}%"))
            if scene.precip_kind != "none":
                out.append(("降水", f"{scene.precip_mm:.1f} mm/时"))
            return out
        out.append(("湿度", f"{scene.humidity:.0f}%"))
        if scene.wind_speed:
            out.append(("风", f"{compass(scene.wind_dir)} "
                             f"{scene.wind_speed:.1f} km/h"))
        if scene.precip_kind != "none" or scene.precip_mm > 0:
            out.append(("降水", f"{scene.precip_mm:.1f} mm/时"))
        if scene.visibility:
            out.append(("能见度", f"{scene.visibility / 1000:.1f} km"))
        if scene.uv is not None:
            out.append(("紫外线", uv_text(scene.uv)))
        if scene.pressure:
            out.append(("气压", f"{scene.pressure:.0f} hPa"))
        if scene.dew is not None:
            out.append(("露点", f"{scene.dew:.0f}°"))
        return out

    @staticmethod
    def _hero_sub(scene: Scene) -> str:
        """主角那一块底下那行小字：云量 / 体感 / 今天的最高最低。

        **体感只在"就是此刻"的时候写**：逐小时预报表里没有体感这一列，
        预览别的日子时 `scene.apparent` 是 0——写出来就是"21° 体感 0°"
        这种一眼假的东西（用户截图里就是这样）。云量是按那一刻插值出来的，
        什么时候都能写。几个小项之间用统一的间隔，不用"·"串（串起来太吵）。
        """
        sub = [f"云量 {scene.cloud:.0f}%"]
        if scene.weather_now:
            sub.insert(0, f"体感 {scene.apparent:.0f}°")
        if (scene.temp_max is not None and scene.temp_min is not None
                and scene.temp_max - scene.temp_min >= 1.0):
            sub.append(f"今日 {scene.temp_min:.0f}~{scene.temp_max:.0f}°")
        return "   ".join(sub)

    @staticmethod
    def _chip_rows(chips: list, room: float, scale: float) -> list:
        """把小字条按宽度折行（放不下就换一行，不做等宽格子）。

        等宽三列会把"风 西南 12.4 km/h"挤成两截；按文字宽度贪心排，
        一行能放两个就放两个、能放三个就放三个。估宽用的字号必须与画的时候
        一致（名称 10.5 磅 / 数值 12 磅 + 两边各 7 像素的气口）。
        """
        items = [(label, value,
                  CardLayer._text_w(label, 10.5 * scale)
                  + CardLayer._text_w(value, 12.0 * scale) + 15.0 * scale)
                 for label, value in chips]
        return CardLayer._wrap_items(items, room, 7 * scale)

    @staticmethod
    def _text_w(text: str, size: float) -> float:
        """粗略估一行字有多宽（中文按 1 em、其余按 0.58 em）。

        排版要先算"放不放得下"再决定画几列 / 折到哪儿，而 Pango 要量宽得先有
        cr；这里的估算只用来做**分支决策**，差几个像素不影响大局。
        """
        return sum(size * (1.0 if ord(ch) > 0x2E80 else 0.58) for ch in text)

    @staticmethod
    def _wrap_items(items: list, room: float, gap: float) -> list:
        """把 (名称, 数值, 估宽) 一串小字条贪心折行。"""
        rows: list[list[tuple[str, str, float]]] = []
        line: list[tuple[str, str, float]] = []
        used = 0.0
        for label, value, w in items:
            if line and used + gap + w > room:
                rows.append(line)
                line, used = [], 0.0
            line.append((label, value, w))
            used += (gap if len(line) > 1 else 0.0) + w
        if line:
            rows.append(line)
        # 均一一点：最后一行只剩一个小字条时（3+1 这种），把前一行最后一个挪下来，
        # 变成 2+2。两行一样长看着才像"排过版"。
        if len(rows) >= 2 and len(rows[-1]) == 1 and len(rows[-2]) >= 2:
            moved = rows[-2].pop()
            used = (sum(w for _l, _v, w in rows[-1]) + gap * len(rows[-1])
                    + moved[2])
            if used <= room:
                rows[-1].insert(0, moved)
        return rows

    @staticmethod
    def _weather_stamp(scene: Scene) -> str:
        """天气是什么时候问回来的——按这扇窗所在地方的时间写。"""
        if not scene.weather_at:
            return ""
        try:
            at = datetime.fromtimestamp(scene.weather_at, scene.when.tzinfo)
        except (OverflowError, OSError, ValueError):
            return ""
        if at.date() == scene.when.date():
            return at.strftime("%H:%M")
        return at.strftime("%m-%d %H:%M")

    def _foot_text(self, scene: Scene, cr, room: float, size: float) -> str:
        """卡片底部那一行：剩多少宽度就说多少话（宁可少说，也不挤到按钮底下）。"""
        stamp = self._weather_stamp(scene)
        if self.ui.weather_busy:
            # 刚按过 R（或刚换过城市）：先给一句"正在问"，别让这一秒像卡住了。
            # 结果回来之后（成功 / 失败都算）心跳会把这一条换回下面的说法。
            options = ["天文 · 本地计算　　正在问一次真实的天气…", "正在问天气…"]
        elif scene.has_weather and scene.weather_stale:
            head = (f"网络不通 · 显示上次天气（{stamp}）" if stamp
                    else "网络不通 · 显示上次天气")
            options = [head, "网络不通 · 上次的天气"]
        elif scene.has_weather and scene.weather_now and stamp:
            options = [f"天文 · 本地计算　　天气更新于 {stamp} · Open-Meteo",
                       f"天气更新于 {stamp} · Open-Meteo",
                       f"天气更新于 {stamp}"]
        elif scene.has_weather and scene.weather_now:
            # 就是此刻那份天气，只是不知道是什么时候问回来的（假数据 / 老缓存）
            options = ["天文 · 本地计算　　天气 · Open-Meteo", "天气 · Open-Meteo"]
        elif scene.has_weather:
            options = ["天文 · 本地计算　　这一天的逐小时预报",
                       "这一天的逐小时预报"]
        elif scene.weather_nodata:
            options = ["天文 · 本地计算　　这天的预报还没问到", "这天的预报还没问到"]
        elif scene.weather_disabled:
            options = ["天文 · 本地计算　　你关掉了天气", "只看天"]
        else:
            options = ["天文 · 本地计算　　天气未接入", "天文 · 本地计算"]
        for text in options[:-1]:
            if draw_text(cr, text, 0, -1000, size, (255, 255, 255), 0.0)[0] <= room:
                return text
        return options[-1]
