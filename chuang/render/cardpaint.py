
"""把「此刻的事实」画出来：一笔一笔的都在这里。

入口是 `_paint_info`，它把这张卡分成九段（玻璃底 / 抬头 / 时间 / 日弧 / 主角 /
指标格 / 小字条 / 一句人话 / 脚注），一段一个小方法——1.2.0 之前它是一个三百八十
行的整体，改一段要在一屏里上下找三遍。整张卡按 `_info_key` 离屏缓存，所以这一段
只在"卡上真的变了"的时候跑一次。

画的时候顺手把"能点的方块"记进 `rects`（窗口靠它做命中）：**每加一个能点的东西
就要在这里按顺序 append 一次**——鼠标悬停的下标就是它在这个列表里的位置
（`_hover_slot` 说的就是"下一个要画的那个"）。
"""

from __future__ import annotations

import cairo
import gi

gi.require_version("Pango", "1.0")
from gi.repository import Pango  # noqa: E402

from ..scene import Scene, duration_zh
from .card import CardLayer
from .paint import (TAU, clamp, draw_icon, draw_text, draw_text_bl, icon_ink_y,
                    icon_tint, ink_baseline, rounded_rect, text_ink)


class CardPaintLayer(CardLayer):

    def _paint_info(self, cr, scene: Scene, x: float, y: float, L: dict) -> list:
        """真正下笔的那一遍：画在离屏图上，返回这张卡上所有能点的方块。

        这一遍分九段，一段一个方法（1.2.0 之前它是一整个三百八十行的方法：
        改"脚注"那一段要在一屏里上下找三遍）。`rects` 一路往下传——**能点的
        东西按画出来的顺序 append**，鼠标悬停的下标就是它在列表里的位置
        （见 `_hover_slot`），所以这段顺序不能改。
        """
        rects: list = []
        cr.save()
        self._paint_card_back(cr, x, y, L)                 # 玻璃底 + 顶光 + 亮边
        self._paint_card_head(cr, scene, x, y, L, rects)   # 城市 · 此刻 · 收起
        self._paint_card_time(cr, scene, x, y, L)           # 大字时间 + 日期
        self._paint_card_arc(cr, scene, x, y, L, rects)     # 日出到日落那条日弧
        self._paint_card_hero(cr, scene, x, y, L, rects)    # 主角：此刻的天气
        self._paint_card_grid(cr, scene, x, y, L, rects)    # 指标格：日月与日出日落
        self._paint_card_chips(cr, x, y, L, rects)           # 小字条：湿度风能见度…
        self._paint_card_hint(cr, scene, x, y, L)           # 一句人话
        self._paint_card_foot(cr, scene, x, y, L, rects)    # 数据从哪来 + 刷新
        cr.restore()
        return rects

    def _hover_slot(self, rects: list) -> bool:
        """轮到这一块了吗？"鼠标正停着的那一块"就是**下一个要 append 的**。"""
        return self.ui.info_hover == len(rects)

    # ------------------------------------------------------------------
    def _paint_card_back(self, cr, x: float, y: float, L: dict) -> None:
        """卡片本身：阴影 + 玻璃底 + 随天色的一道顶光 + 一圈亮边。"""
        scale, card_w, card_h = L["scale"], L["card_w"], L["card_h"]
        accent = L["accent"]
        # 卡片：阴影 + 玻璃底 + 随天色的一道顶光
        for i, a in ((5, 0.05), (3, 0.06), (1.5, 0.08)):
            cr.set_source_rgba(0, 0, 0, a)
            rounded_rect(cr, x - i, y - i + 2, card_w + i * 2, card_h + i * 2,
                         18 * scale)
            cr.fill()
        grad = cairo.LinearGradient(x, y, x + card_w * 0.7, y + card_h)
        grad.add_color_stop_rgba(0, 0.07, 0.08, 0.12, 0.74)
        grad.add_color_stop_rgba(1, 0.03, 0.04, 0.07, 0.62)
        rounded_rect(cr, x, y, card_w, card_h, 18 * scale)
        cr.set_source(grad)
        cr.fill()
        cr.save()
        rounded_rect(cr, x, y, card_w, card_h, 18 * scale)
        cr.clip()
        top = cairo.LinearGradient(x, y, x, y + 2.4 * scale)
        top.add_color_stop_rgba(0, accent[0] / 255, accent[1] / 255, accent[2] / 255, 0.5)
        top.add_color_stop_rgba(1, accent[0] / 255, accent[1] / 255, accent[2] / 255, 0.0)
        cr.set_source(top)
        cr.rectangle(x, y, card_w, 2.4 * scale)
        cr.fill()
        cr.restore()
        cr.set_source_rgba(1, 1, 1, 0.10)
        rounded_rect(cr, x, y, card_w, card_h, 18 * scale)
        cr.set_line_width(1)
        cr.stroke()

    def _paint_card_head(self, cr, scene: Scene, x: float, y: float, L: dict,
                         rects: list) -> None:
        """抬头：城市名 · 此刻/预览 · 右上角那颗收起箭头。"""
        scale, pad, card_w = L["scale"], L["pad"], L["card_w"]
        ts, ymap, compact = L["tscale"], L["y"], L["compact"]
        accent = L["accent"]
        cx = x + pad
        # 卡片上的字与图标一律按**墨迹中线**对齐（见 text_ink）：15.5 磅的城市名、
        # 11 磅的"此刻"标签、右下角那枚方按钮，中线在同一条线上才叫"一条线"；
        # 按文本框左上角（或共用基线）对齐都会差出好几个像素。以前收起箭头就比
        # 城市名高出 2~3 像素，看着像没摆平。
        cy = y + ymap["head"]
        head_mid = cy + 12 * scale
        name = scene.location_name or scene.location_label or ""
        base_head = ink_baseline(head_mid, name, self.F_CITY * ts,
                                 Pango.Weight.MEDIUM)
        tw, _ = draw_text_bl(cr, name, cx, base_head, self.F_CITY * ts,
                             (240, 244, 252), 0.94, weight=Pango.Weight.MEDIUM)
        chip_text = f"预览 {scene.when.strftime('%H:%M')}" if scene.preview else "此刻"
        chip_w = (78 if scene.preview else 46) * scale
        tag_h = 19 * scale
        # 有收起箭头的时候，城市名不能压到它底下；壁纸上的卡片没有那颗箭头，
        # 于是这里也不用白白让出 28 像素
        head_room = (30 if self.ui.info_buttons else 6) * scale
        chip_x = min(cx + tw + 9 * scale,
                     x + card_w - pad - chip_w - head_room)
        cc = (255, 176, 96) if scene.preview else accent
        cr.set_source_rgba(cc[0] / 255, cc[1] / 255, cc[2] / 255, 0.26)
        rounded_rect(cr, chip_x, head_mid - tag_h / 2, chip_w, tag_h, tag_h / 2)
        cr.fill()
        draw_text_bl(cr, chip_text, chip_x + chip_w / 2,
                     ink_baseline(head_mid, chip_text, self.F_LABEL * ts),
                     self.F_LABEL * ts,
                     (255, 255, 255), 0.94, align="center")
        if self.ui.info_buttons:
            btn = 23 * scale
            bx = x + card_w - pad - btn
            by = head_mid - btn / 2
            self._icon_button(cr, "chevron-down" if compact else "chevron-up",
                              bx + btn / 2, by + btn / 2, btn, (234, 240, 252), 0.74,
                              hover=self._hover_slot(rects))
            rects.append((bx, by, btn, btn, "toggle", None))

    def _paint_card_time(self, cr, scene: Scene, x: float, y: float,
                         L: dict) -> None:
        """大字时间，和它右边那行日期（窄窗口会先说短的，见 `_date_text`）。"""
        scale, pad, card_w = L["scale"], L["pad"], L["card_w"]
        ts, ymap, compact = L["tscale"], L["y"], L["compact"]
        cx = x + pad
        cy = y + ymap["time"]
        time_size = (self.F_TIME * 0.78 if compact else self.F_TIME) * ts
        time_text = scene.when.strftime("%H:%M")
        base_time = cy + time_size * 0.78
        tw, _ = draw_text_bl(cr, time_text, cx, base_time, time_size,
                             (255, 255, 255), 0.97, weight=Pango.Weight.LIGHT)
        weekday = "一二三四五六日"[scene.when.weekday()]
        date_text = self._date_text(
            scene, (x + card_w - pad) - (cx + tw + 10 * scale), self.F_DATE * ts)
        # 日期那一行跟着大字时间走，**按墨迹中线对齐**：字号差一倍以上时共用基线
        # 会让大字的下缘压着小字（小字看着往下掉），而那两个"0.36 / 0.38"的
        # 经验值又差着 3~4 像素——现在量的是真实的墨迹。
        tt, tb = text_ink(time_text, time_size, Pango.Weight.LIGHT)
        draw_text_bl(cr, date_text, cx + tw + 10 * scale,
                     ink_baseline(base_time + (tt + tb) / 2.0, date_text,
                                  self.F_DATE * ts),
                     self.F_DATE * ts, (226, 232, 245), 0.60)

    def _paint_card_arc(self, cr, scene: Scene, x: float, y: float, L: dict,
                        rects: list) -> None:
        """日弧：日出到日落那条轨道，此刻在哪儿（极昼没有，就不画）。

        轨道铺的是**一整天**，亮的那一段才是白天——鼠标悬停与点击的映射
        必须和这个画法一致（见 infocard.InfoCard.arc_time）。
        """
        scale, pad, card_w = L["scale"], L["pad"], L["card_w"]
        ts, ymap = L["tscale"], L["y"]
        sunr, suns, show_arc = L["sunr"], L["suns"], L["show_arc"]
        accent = L["accent"]
        cx = x + pad
        if show_arc:
            cy = y + ymap["arc"]
            ax0 = cx
            aw = card_w - pad * 2
            ah = 5 * scale
            ay = cy + 4 * scale
            f0, f1 = self._day_frac(sunr), self._day_frac(suns)
            cr.set_source_rgba(1, 1, 1, 0.13)
            rounded_rect(cr, ax0, ay, aw, ah, ah / 2)
            cr.fill()
            cr.save()
            rounded_rect(cr, ax0, ay, aw, ah, ah / 2)
            cr.clip()
            if f1 > f0:
                g = cairo.LinearGradient(ax0 + aw * f0, 0, ax0 + aw * f1, 0)
                g.add_color_stop_rgba(0, 1.0, 0.80, 0.45, 0.95)
                g.add_color_stop_rgba(1, accent[0] / 255, accent[1] / 255,
                                      accent[2] / 255, 0.95)
                cr.set_source(g)
                cr.rectangle(ax0 + aw * f0, ay, aw * (f1 - f0), ah)
                cr.fill()
            if self.ui.info_hover_dt is not None:        # 悬停：一条跟随的细游标
                hx = ax0 + aw * clamp(self._day_frac(self.ui.info_hover_dt), 0, 1)
                cr.set_source_rgba(1, 1, 1, 0.85)
                cr.rectangle(hx - 0.7 * scale, ay - 5 * scale, 1.4 * scale,
                             ah + 10 * scale)
                cr.fill()
            mx = ax0 + aw * clamp(self._day_frac(scene.when), 0, 1)
            cr.set_source_rgba(1, 1, 1, 0.95)            # 此刻：一枚小圆点
            cr.arc(mx, ay + ah / 2, ah * 0.92, 0, TAU)
            cr.fill()
            cr.set_source_rgba(0.06, 0.07, 0.11, 0.92)
            cr.arc(mx, ay + ah / 2, ah * 0.42, 0, TAU)
            cr.fill()
            cr.restore()
            ly = ay + ah + 5 * scale
            # 这枚小图标与时刻**画在日出真正落在轨道上的那个位置**：轨道铺的是
            # 整天，日出刻度以前却钉在左端，看着就像"日出=00:00"。
            rx0 = ax0 + aw * clamp(f0, 0, 1)
            rx1 = ax0 + aw * clamp(f1, 0, 1)
            cr.set_source_rgba(0, 0, 0, 0.30)
            cr.rectangle(rx0 - 0.5 * scale, ay - 1 * scale, 1.2 * scale, ah + 2 * scale)
            cr.fill()
            cr.rectangle(rx1 - 0.5 * scale, ay - 1 * scale, 1.2 * scale, ah + 2 * scale)
            cr.fill()
            ix = clamp(rx0, ax0, ax0 + aw - 44 * scale)
            # 这一行也是"一枚图标 + 两行字"，同样按墨迹中线对齐（见 text_ink）：
            # 以前用文本框左上角定位，日出那枚图标比它旁边的时刻高出 3~4 像素。
            sun_text = sunr.strftime("%H:%M")
            sun_mid = ly + 8 * scale
            draw_icon(cr, "sunrise", ix + 6 * scale,
                      icon_ink_y("sunrise", sun_mid, 13 * scale), 13 * scale,
                      icon_tint("sunrise"), 0.9)
            draw_text_bl(cr, sun_text, ix + 15 * scale,
                         ink_baseline(sun_mid, sun_text, 11 * scale),
                         11 * scale, (246, 240, 232), 0.68)
            tail = (f"还剩 {duration_zh(scene.daylight_left)}"
                    if scene.daylight_left else "今天已过去")
            draw_text_bl(cr, tail, ax0 + aw,
                         ink_baseline(sun_mid, tail, 11 * scale),
                         11 * scale, (250, 250, 255), 0.74, align="right")
            if self.ui.info_buttons:
                rects.append((ax0, ay - 7 * scale, aw, ah + 16 * scale, "arc", None))

    def _paint_card_hero(self, cr, scene: Scene, x: float, y: float, L: dict,
                         rects: list) -> None:
        """主角块：此刻的天气（这张卡上最大、最亮的那一块）。

        左列是天气图标 + 大字温度、右列是"天气 + 依云量/体感"，两列都落在块的
        中线上（量的都是**墨迹**，见 text_ink / icon_ink_y）。
        """
        scale, pad, card_w = L["scale"], L["pad"], L["card_w"]
        ts, ymap, compact = L["tscale"], L["y"], L["compact"]
        hero, hero_block = L["hero"], L["hero_block"]
        cx = x + pad
        if not compact and hero is not None:
            hy = y + ymap["hero"]
            hh = hero_block
            hx = cx - 6 * scale
            hw = card_w - pad * 2 + 12 * scale
            hovered = self._hover_slot(rects)
            # 一块比卡片略亮的底：主角就是主角。**不画左边那道竖线**——
            # 它在"卡片本身已经有边界"的前提下只是多余的一道噪点（用户点名去掉）。
            cr.set_source_rgba(1, 1, 1, 0.10 if hovered else 0.06)
            rounded_rect(cr, hx, hy, hw, hh, 12 * scale)
            cr.fill()

            badge = 26 * scale
            # 主角块的竖向节奏（1.1.14 第三版重排）：**左边那两样——天气图标与大字温度
            # ——落在块的中线上**，右边那一列（天气 + 副行）也整组居中。
            # 只让"左列跟右列的第一行对齐"（1.1.14 第二版就是那样）时，左列一定
            # 偏在块的上半部：用户第二次说的"温度和天气图标没有上下居中"就是它。
            # 对齐量的是**墨迹**（text_ink / icon_ink_y）——真正着墨的那一块的
            # 中线才是眼睛看的中线，文本框与"0.36 经验值"都不是。
            T = self.F_HERO_TEMP * ts
            C = self.F_HERO_WHAT * ts
            S = self.F_HERO_SUB * ts
            temp_text, what, sub = self._hero_texts(scene, hero)
            mid = hy + hh / 2.0
            icon_size = badge * 0.92
            # 图标与文字的左边线跟下面的指标格**对齐**（都是 cx 起）
            draw_icon(cr, hero.icon, hx + 6 * scale + badge / 2,
                      icon_ink_y(hero.icon, mid, icon_size),
                      icon_size, icon_tint(hero.icon), 0.95,
                      phase=scene.moon_phase)
            tx = hx + 6 * scale + badge + 9 * scale
            if temp_text:
                # **两列**：左边温度，右边天气与那行小字。右边这一列钉在固定的 x 上
                # ——以前它是"跟在温度后面 9 像素"，于是温度是 21° / 9° / -12°
                # 时，右边那两行会跟着左右挪，看着就是"随手摆的、没对齐"。
                slot = max(66 * scale,
                           self._text_w(temp_text, self.F_HERO_TEMP * ts))
                rx = tx + slot + 10 * scale
                draw_text_bl(cr, temp_text, tx,
                             ink_baseline(mid, temp_text, T, Pango.Weight.LIGHT),
                             T, (255, 255, 255), 0.97, weight=Pango.Weight.LIGHT)
                # 右列：两行当成一整块居中（块的中线就是温度的中线）
                wt, wb = text_ink(what, C)
                st, sb = text_ink(sub, S)
                gap = self.HERO_GAP * self.F_HERO_WHAT * ts
                group_h = (wb - wt) + gap + (sb - st)
                base_what = mid - group_h / 2.0 - wt
                base_sub = base_what + wb + gap - st
                draw_text_bl(cr, what, rx, base_what, C, (238, 242, 250), 0.88)
                draw_text_bl(cr, sub, rx, base_sub, S, (208, 219, 238), 0.62)
            else:
                # 没有天气：老实说为什么没有，别摆一个假的度数在那儿
                # 整块垂直居中、副值跟在右边同一行上——这一段本来就是一句话，
                # 拆成两行会让那块板子显得又空又吊。
                base_h1 = ink_baseline(mid, hero.value, self.F_ANCHOR * ts,
                                       Pango.Weight.MEDIUM)
                vw, _ = draw_text_bl(cr, hero.value, tx, base_h1,
                                     self.F_ANCHOR * ts,
                                     (238, 242, 250), 0.92,
                                     weight=Pango.Weight.MEDIUM)
                if hero.note:
                    draw_text_bl(cr, hero.note, tx + vw + 12 * scale, base_h1,
                                 self.F_HERO_SUB * ts, (208, 219, 238), 0.58)
            if self.ui.info_buttons:
                rects.append((hx, hy, hw, hh, "detail", None))

    def _paint_card_grid(self, cr, scene: Scene, x: float, y: float, L: dict,
                         rects: list) -> None:
        """指标格：太阳 / 日出 / 日落 / 月亮（两列，各是一个能跳过去的时刻）。

        一列还是两列由 `_info_layout` 先算好（放不下就整列铺开）；两行字在格子里
        按**墨迹中线**对称居中，副值是明显小一号的那一档。
        """
        scale, pad, card_w = L["scale"], L["pad"], L["card_w"]
        ts, ymap, compact = L["tscale"], L["y"], L["compact"]
        grid, cell_h = L["grid"], L["cell_h"]
        cx = x + pad
        if not compact and grid:
            # 主角块与指标格之间那一条分隔线（**两行之间**原来还有一条，1.1.14
            # 去掉了：每一格自己就有图标方框与两行字，中间再横一道只是噪点）
            cr.set_source_rgba(1, 1, 1, 0.09)
            cr.rectangle(cx, y + ymap["divider"], card_w - pad * 2, 1)
            cr.fill()
            cy = y + ymap["grid"]
            cols = L["grid_cols"]
            gap = 8 * scale
            cw = (card_w - pad * 2 - gap * (cols - 1)) / cols
            for i, row in enumerate(grid):
                col, line = i % cols, i // cols
                gx = cx + col * (cw + gap)
                gy = cy + line * cell_h
                hovered = self._hover_slot(rects)
                rh = cell_h - 3 * scale
                ry = gy - 1.5 * scale
                badge = 17 * scale
                # 两行字在格子里**居中**：标题 + 数值一行、副值一行，两行墨迹中线
                # 对称落在格子中线上（`notes` 关掉时只剩一行，那一行自己居中）。
                line_gap = L["cell_line_gap"]
                mid1 = gy + cell_h / 2 - (line_gap / 2 if L["notes"] else 0.0)
                mid2 = gy + cell_h / 2 + (line_gap / 2 if L["notes"] else 0.0)
                base_c1 = ink_baseline(mid1, row.label, self.F_LABEL * ts)
                base_c2 = ink_baseline(mid2, row.note or row.label, self.F_NOTE * ts)
                bcx = gx + badge / 2
                bcy = mid1                         # 图标对齐"这一行的视觉中心"
                cr.set_source_rgba(1, 1, 1, 0.11 if hovered else 0.06)
                rounded_rect(cr, bcx - badge / 2, bcy - badge / 2, badge, badge,
                             badge * 0.34)
                cr.fill()
                draw_icon(cr, row.icon, bcx, bcy, badge * 0.68, icon_tint(row.icon),
                          1.0 if hovered else 0.95, phase=scene.moon_phase)
                tx = gx + badge + 7 * scale
                # 这一格的字号分三档：标题 11 / 数值 14 / 副值 9.5 —— 副值**明显**
                # 小一号（用户点名："仰角"不该和"太阳"一样大）。
                draw_text_bl(cr, row.label, tx, base_c1, self.F_LABEL * ts,
                             (228, 235, 249) if hovered else (198, 207, 226),
                             0.80 if hovered else 0.55)
                arrow = 12 * scale if self.ui.info_buttons else 0.0
                vw, _ = draw_text(cr, row.value, 0, -1000, self.F_VALUE * ts,
                                  (255, 255, 255), 0.0)
                # 数值**紧跟在标题后面**（像一张表），只有长到要撞上右边界时才
                # 退回右对齐——整列都飘在最右边时，眼睛要来回跳着找它。
                vx = tx + 36 * scale
                if vx + vw > gx + cw - arrow:
                    vx = max(tx + 20 * scale, gx + cw - vw - arrow)
                draw_text_bl(cr, row.value, vx, base_c1, self.F_VALUE * ts,
                             (255, 255, 255) if hovered else (246, 249, 255),
                             1.0 if hovered else 0.95)
                if row.note and L["notes"]:
                    # 副值在自己的第二行上，只要不超出这一格就画；装不下就整条
                    # 不写，也不截半句（1.1.13 第一版这里和数值叠在一起过）。
                    nw, _ = draw_text(cr, row.note, 0, -1000, self.F_NOTE * ts,
                                      (255, 255, 255), 0.0)
                    if tx + nw <= gx + cw - 3 * scale:
                        draw_text_bl(cr, row.note, tx, base_c2,
                                     self.F_NOTE * ts,
                                     (226, 234, 248) if hovered else (196, 207, 228),
                                     0.74 if hovered else 0.50)
                if self.ui.info_buttons:
                    # 箭头**紧跟在数值后面**（不是钉在格子右边）：它是"这一条能点"
                    # 的意思，放在数值边上比放在格子尽头更容易看懂。
                    ax = min(vx + vw + 5 * scale, gx + cw - 8 * scale)
                    draw_icon(cr, "chevron-right", ax,
                              icon_ink_y("chevron-right", mid1, 11 * scale),
                              11 * scale, (236, 241, 252),
                              0.48 if hovered else 0.30)
                    rects.append((gx - 3 * scale, ry, cw + 6 * scale, rh,
                                  row.action, row.when))

    def _paint_card_chips(self, cr, x: float, y: float, L: dict,
                          rects: list) -> None:
        """小字条：湿度 / 风 / 能见度…（第三层，最小最淡，一眼扫过就好）。"""
        scale, pad = L["scale"], L["pad"]
        ts, ymap, compact = L["tscale"], L["y"], L["compact"]
        chip_rows, chip_line_h, chip_gap = (L["chip_rows"], L["chip_line_h"],
                                            L["chip_gap"])
        accent = L["accent"]
        cx = x + pad
        if not compact and chip_rows:
            cy = y + ymap["chips"]
            for line in chip_rows:
                xoff = cx
                for label, value, w in line:
                    pill_w = w
                    hovered = self._hover_slot(rects)
                    pill_h = chip_line_h - 4 * scale
                    cr.set_source_rgba(1, 1, 1, 0.11 if hovered else 0.045)
                    rounded_rect(cr, xoff, cy, pill_w, pill_h, pill_h / 2)
                    cr.fill()
                    if hovered:            # 停上去：左边压一道天色，说明"能点"
                        cr.set_source_rgba(accent[0] / 255, accent[1] / 255,
                                           accent[2] / 255, 0.85)
                        rounded_rect(cr, xoff + 1.5 * scale, cy + 2 * scale,
                                     2.0 * scale, pill_h - 4 * scale,
                                     1.0 * scale)
                        cr.fill()
                    # 小字条里的"名称"比"数值"小半档：一眼扫过去先看见数字
                    # 文字在胶囊里按**墨迹中线**居中（见 text_ink）
                    base_chip = ink_baseline(cy + pill_h * 0.5, value,
                                             self.F_CHIP_VAL * ts)
                    lw, _ = draw_text_bl(cr, label, xoff + 7 * scale, base_chip,
                                         self.F_CHIP_KEY * ts,
                                         (214, 223, 240) if hovered else (196, 206, 226),
                                         0.72 if hovered else 0.58)
                    draw_text_bl(cr, value, xoff + 7 * scale + lw + 4 * scale,
                                 base_chip, self.F_CHIP_VAL * ts,
                                 (246, 250, 255) if hovered else (234, 240, 252),
                                 0.95 if hovered else 0.84)
                    if self.ui.info_buttons:
                        rects.append((xoff, cy, pill_w, pill_h, "detail", None))
                    xoff += pill_w + chip_gap
                cy += chip_line_h + chip_gap

    def _paint_card_hint(self, cr, scene: Scene, x: float, y: float,
                         L: dict) -> None:
        """一句人话（"太阳在你的西南方，仰角 12°"），左边压一道天色的细条。"""
        scale, pad = L["scale"], L["pad"]
        ts, ymap = L["tscale"], L["y"]
        hint_lines = L["hint_lines"]
        hint_top, hint_line_h = L["hint_top"], L["hint_line_h"]
        accent = L["accent"]
        cx = x + pad
        if hint_lines:
            cy = y + ymap["hint"] + hint_top
            bar_h = max(14 * scale, hint_line_h * len(hint_lines) - 6 * scale)
            cr.set_source_rgba(accent[0] / 255, accent[1] / 255, accent[2] / 255,
                               0.85)
            rounded_rect(cr, cx, cy + 3 * scale, 2.5 * scale, bar_h, 1.2 * scale)
            cr.fill()
            for i, line in enumerate(hint_lines):
                draw_text_bl(cr, line, cx + 10 * scale,
                             ink_baseline(cy + 11 * scale + i * hint_line_h, line,
                                          self.F_HINT * ts),
                             self.F_HINT * ts, (250, 250, 255), 0.84)

    def _paint_card_foot(self, cr, scene: Scene, x: float, y: float, L: dict,
                         rects: list) -> None:
        """脚注：数据从哪来、什么时候问回来的 + 右下角那枚刷新。

        一行里要塞下"来源 + 更新于几点"，窗口窄的时候先让短的顶上来：与其把字
        挤到刷新按钮底下（或截半句），不如少说几个字（见 `_foot_text`）。
        壁纸上的那张卡没有刷新按钮，右下角整块都是给这行字的。
        正有枪在飞的时候（`ui.weather_busy`）字与图标都换了样子——"正在问"。
        """
        scale, pad, card_w = L["scale"], L["pad"], L["card_w"]
        ts, ymap = L["tscale"], L["y"]
        foot_h = L["foot_h"]
        cx = x + pad
        # 一行里要塞下"来源 + 更新于几点"，窗口窄的时候先让短的顶上来：
        cy = y + ymap["foot"] + foot_h / 2.0      # 这一条的中线：字与按钮都对齐它
        rb = 20 * scale if self.ui.info_buttons else 0.0
        rbx, rby = x + card_w - pad - rb, cy - rb / 2
        draw_text_room = (x + card_w - pad - rb - (10 * scale if rb else 0.0)) - cx
        foot = self._foot_text(scene, cr, draw_text_room, self.F_FOOT * ts)
        draw_text_bl(cr, foot, cx,
                     ink_baseline(cy, foot, self.F_FOOT * ts), self.F_FOOT * ts,
                     (198, 208, 228), 0.42)
        if self.ui.info_buttons:
            # 正在问的时候把图标压暗："已经在做了"，再点一次也没用
            busy = bool(self.ui.weather_busy)
            self._icon_button(cr, "refresh", rbx + rb / 2, rby + rb / 2, rb,
                              (228, 236, 250), 0.42 if busy else 0.8,
                              hover=self._hover_slot(rects) and not busy)
            rects.append((rbx - 2 * scale, rby - 2 * scale, rb + 4 * scale,
                          rb + 4 * scale, "refresh", None))
