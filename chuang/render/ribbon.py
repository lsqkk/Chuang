
"""窗底那条「今日天色」长卷：一天的光色、时刻刻度、日出日落与游标。

整条长卷先烤成一张 1×20 像素的离屏图（每一格一个颜色），画的时候只做拉伸；整点
是短刻度、日出日落带图标与时刻、此刻是一条游标、鼠标停着的那一格会亮起来。
底下那行整点标签要给系统面板让路（`ui.bottom_inset`，见 AGENTS.md §3.5/§3.7）。
"""

from __future__ import annotations

import cairo

from ..palette import shade
from ..scene import Scene
from .core import PainterCore
from .paint import RIBBON_Y, TAU, clamp, draw_icon, draw_text, rounded_rect
from .state import UIState


class RibbonLayer(PainterCore):

    # ------------------------------------------------------------------
    # 今日天色长卷
    # ------------------------------------------------------------------
    def _build_ribbon(self, ui: UIState) -> None:
        n = len(ui.ribbon)
        if n == 0:
            ui.ribbon_surface = None
            return
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, n, 20)
        cr = cairo.Context(surf)
        for i, (_, col) in enumerate(ui.ribbon):
            top = shade(tuple(c / 255 for c in col), 1.10)
            bot = shade(tuple(c / 255 for c in col), 0.86)
            g = cairo.LinearGradient(0, 0, 0, 20)
            g.add_color_stop_rgb(0, *top)
            g.add_color_stop_rgb(1, *bot)
            cr.set_source(g)
            cr.rectangle(i, 0, 1, 20)
            cr.fill()
        ui.ribbon_surface = surf

    def ribbon_time_at(self, ui: UIState, x: float):
        if not ui.ribbon:
            return None
        x0, _, rw, _ = ui.ribbon_rect
        if rw <= 0:
            return None
        t = clamp((x - x0) / rw, 0.0, 1.0)
        idx = clamp(int(t * len(ui.ribbon)), 0, len(ui.ribbon) - 1)
        return ui.ribbon[idx][0]

    def _draw_ribbon(self, cr, w, h, scene: Scene, canvas_h=None):
        """h 是"景色高度"（`scene_height` 算出来的），canvas_h 是整块画布的高度。

        两者分开传，是因为"底部被面板挡掉多少"是按**整块画布**算的，而长卷的
        位置是按景色高度排的。
        """
        ui = self.ui
        if not ui.ribbon:
            return
        if ui.ribbon_surface is None:
            self._build_ribbon(ui)
        x0 = 0.035 * w
        rw = 0.93 * w
        rh = clamp(h * 0.026, 14.0, 24.0)
        y0 = RIBBON_Y * h
        # 底下那条留白是"系统面板/dock"的地盘（壁纸是铺满整屏的，dock 会压在
        # 上面）。长卷下面还有一行整点标签，得让它整个钻出面板之外——
        # 否则就是用户说的"下面还是有点挡着"：标签一半藏进 dock 里。
        canvas_h = canvas_h or h
        label_size = max(8.0, min(h * 0.013, 12.0))
        # 整点标签实际占的高度比字号大（Pango 一行约 1.4 倍），再留 10px 气口
        limit = canvas_h - ui.bottom_inset - label_size * 1.45 - 10.0
        if y0 + rh > limit:
            y0 = max(canvas_h * 0.5, limit - rh)
        ui.ribbon_rect = (x0, y0, rw, rh)
        cr.save()
        rounded_rect(cr, x0, y0, rw, rh, rh * 0.42)
        cr.clip()
        surf = ui.ribbon_surface
        if surf is not None:
            pat = cairo.SurfacePattern(surf)
            pat.set_filter(cairo.FILTER_BILINEAR)
            pat.set_matrix(cairo.Matrix(xx=surf.get_width() / rw, yy=20.0 / rh))
            cr.save()
            cr.translate(x0, y0)
            cr.set_source(pat)
            cr.rectangle(0, 0, rw, rh)
            cr.fill()
            cr.restore()
        # 已经过去的那一段压暗一点
        frac_now = (scene.when.hour * 60 + scene.when.minute) / 1440.0
        cr.set_source_rgba(0, 0, 0, 0.22)
        cr.rectangle(x0, y0, rw * clamp(frac_now, 0, 1), rh)
        cr.fill()
        if ui.preview_dt is not None:
            pf = (ui.preview_dt.hour * 60 + ui.preview_dt.minute) / 1440.0
            cr.set_source_rgba(1, 1, 1, 0.10)
            cr.rectangle(x0 + rw * clamp(frac_now, 0, 1), y0,
                         rw * clamp(pf - frac_now, 0, 1), rh)
            cr.fill()
        # 鼠标停着（或正在预览）的那一格：铺一层很淡的高亮，整条长卷上"这是
        # 哪一格"一眼就能看见。以前只有上面那条很细的竖线，得凑近找。
        mark = ui.preview_dt or ui.hover_dt
        if mark is not None and ui.ribbon:
            cells = len(ui.ribbon)
            idx = min(cells - 1, int(clamp(self._day_frac(mark), 0.0, 0.9999) * cells))
            cell = rw / cells
            cr.set_source_rgba(1, 1, 1, 0.16 if ui.preview_dt else 0.10)
            cr.rectangle(x0 + idx * cell, y0, cell, rh)
            cr.fill()
        cr.restore()

        # 刻度与时刻标记
        for hour in range(0, 25, 3):
            hx = x0 + rw * (hour / 24.0)
            cr.set_source_rgba(1, 1, 1, 0.30)
            cr.rectangle(hx, y0 + rh * 0.16, 1, rh * 0.68)
            cr.fill()
        # 日出 / 日落：不只是两道刻度，而是带上图标与时刻的标记
        mark_size = max(9.0, min(h * 0.014, 12.5))
        for key, kind, col in (("sunrise", "sunrise", (255, 214, 150)),
                               ("sunset", "sunset", (255, 186, 128))):
            ev = scene.events.get(key)
            if not ev:
                continue
            mx = x0 + rw * clamp(self._day_frac(ev), 0, 1)
            cr.set_source_rgba(0, 0, 0, 0.35)
            cr.rectangle(mx - 1, y0, 2, rh)
            cr.fill()
            ir = rh * 0.60
            icy = y0 - ir - 3.0
            cr.set_source_rgba(0.05, 0.06, 0.10, 0.74)
            cr.arc(mx, icy, ir + 1.4, 0, TAU)
            cr.fill()
            cr.set_source_rgba(1, 1, 1, 0.16)
            cr.arc(mx, icy, ir + 1.4, 0, TAU)
            cr.set_line_width(1)
            cr.stroke()
            draw_icon(cr, kind, mx, icy, ir * 1.55, col, 0.95)
            label = ev.strftime("%H:%M")
            tw, _ = draw_text(cr, label, 0, -1000, mark_size, (255, 255, 255), 0.0)
            draw_text(cr, label, clamp(mx - tw / 2, x0, x0 + rw - tw),
                      icy - ir - mark_size - 6.0, mark_size, (244, 240, 234), 0.68)

        # 此刻的游标
        nx = x0 + rw * clamp(frac_now, 0, 1)
        cr.set_source_rgba(1, 1, 1, 0.92)
        cr.rectangle(nx - 1, y0 - 3, 2, rh + 6)
        cr.fill()
        cr.set_source_rgba(1, 1, 1, 0.55)
        cr.arc(nx, y0 - 4.5, 2.2, 0, TAU)
        cr.fill()

        # 悬停 / 预览
        mark = ui.preview_dt or ui.hover_dt
        if mark is not None:
            f = (mark.hour * 60 + mark.minute) / 1440.0
            mx = x0 + rw * clamp(f, 0, 1)
            cr.set_source_rgba(1, 1, 1, 0.95 if ui.preview_dt else 0.5)
            cr.rectangle(mx - 1, y0 - 4, 2, rh + 8)
            cr.fill()
            label = mark.strftime("%H:%M")
            size = max(10.0, h * 0.0155)
            # 顺着长卷查一查这一刻的天气（app 每次重建长卷时一并算好）
            sub = ""
            if ui.ribbon_info:
                i = clamp(int(clamp(f, 0, 0.9999) * len(ui.ribbon)),
                          0, len(ui.ribbon_info) - 1)
                sub = ui.ribbon_info[i]
            tw, th = draw_text(cr, label, 0, -1000, size, (255, 255, 255), 0.0)
            sw, sh = (draw_text(cr, sub, 0, -1000, size * 0.82, (255, 255, 255), 0.0)
                      if sub else (0.0, 0.0))
            bw = max(tw, sw) + 22
            bh = th + (sh + 5 if sub else 0) + 10
            bx = clamp(mx - bw / 2, x0, x0 + rw - bw)
            byy = y0 - 14 - bh
            cr.set_source_rgba(0.05, 0.06, 0.10, 0.76)
            rounded_rect(cr, bx, byy, bw, bh, min(bh / 2, 14.0))
            cr.fill()
            cr.set_source_rgba(1, 1, 1, 0.14)
            rounded_rect(cr, bx, byy, bw, bh, min(bh / 2, 14.0))
            cr.set_line_width(1)
            cr.stroke()
            draw_text(cr, label, bx + bw / 2, byy + 5, size, (250, 250, 255), 0.95,
                      align="center")
            if sub:
                draw_text(cr, sub, bx + bw / 2, byy + 6 + th, size * 0.82,
                          (226, 234, 248), 0.72, align="center")

        # 小时标签
        # 兜底：整点标签也不能钻到系统面板底下去（上面的 limit 已经先让开过一次；
        # 字号用的是同一个 label_size，不再算第二遍）
        label_y = min(y0 + rh + 3,
                      canvas_h - ui.bottom_inset - label_size - 3.0,
                      h - label_size - 1.5)
        for hour in (0, 6, 12, 18, 24):
            hx = x0 + rw * (hour / 24.0)
            draw_text(cr, f"{hour:02d}" if hour < 24 else "24", hx, label_y,
                      label_size, (245, 246, 255), 0.42, align="center")
