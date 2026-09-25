
"""浮在最上面的两条：屏幕下方的一句提示（带图标与倒计时细线），以及预览时顶部
那条"点这里回到此刻"。

它们不属于窗外的景色、也不属于信息卡——是"这扇窗在对你说一句话"，所以画在最后
（见 `painter.draw()` 的末尾）。
"""

from __future__ import annotations

import time as _time

from ..palette import mix_rgb
from .core import PainterCore
from .paint import SILL_Y, clamp, draw_icon, draw_text, rounded_rect


class NotifyLayer(PainterCore):

    # ------------------------------------------------------------------
    def _draw_toast(self, cr, w, h):
        ui = self.ui
        if not ui.toast or _time.time() > ui.toast_until:
            ui.toast_rect = (0.0, 0.0, 0.0, 0.0)
            return
        remain = ui.toast_until - _time.time()
        alpha = clamp(min(1.0, remain / 0.6), 0, 1)
        size = clamp(h * 0.020, 12.0, 18.0)
        text = ui.toast + ("　·　详情" if ui.toast_detail else "")
        tw, th = draw_text(cr, text, 0, -1000, size, (255, 255, 255), 0.0)
        icon = ui.toast_icon or "info"
        tint = {"check": (150, 226, 168), "warn": (255, 198, 120),
                "refresh": (176, 208, 240)}.get(icon, (196, 212, 244))
        # 这条提示还会停留多久：末尾那根细线一直在缩短。以前它只是"过一会儿
        # 自己消失"，看的人不知道还要等多久（长提示更容易被当成卡住了）。
        span = getattr(ui, "toast_span", 0.0) or 0.0
        left = clamp(remain / span, 0.0, 1.0) if span > 0.4 else 1.0
        bw, bh = tw + 34 + size * 1.7, th + 18
        bx = w / 2 - bw / 2
        by = SILL_Y * h - bh - 22
        cr.set_source_rgba(0.05, 0.06, 0.10, 0.70 * alpha)
        rounded_rect(cr, bx, by, bw, bh, bh / 2)
        cr.fill()
        # 左边一道与图标同色的窄边：一眼看出这是"成了 / 出问题了 / 正在做"
        cr.save()
        rounded_rect(cr, bx, by, bw, bh, bh / 2)
        cr.clip()
        cr.set_source_rgba(tint[0] / 255, tint[1] / 255, tint[2] / 255,
                           0.30 * alpha)
        rounded_rect(cr, bx, by, bh * 0.42, bh, bh * 0.2)
        cr.fill()
        if left < 1.0:                      # 将要消失的那一条细线
            cr.set_source_rgba(tint[0] / 255, tint[1] / 255, tint[2] / 255,
                               0.30 * alpha)
            cr.rectangle(bx + bh * 0.42, by + bh - 2.0,
                         (bw - bh * 0.42) * left, 1.6)
            cr.fill()
        cr.restore()
        cr.set_source_rgba(1, 1, 1, 0.12 * alpha)
        rounded_rect(cr, bx, by, bw, bh, bh / 2)
        cr.set_line_width(1)
        cr.stroke()
        # 图标画成浅色：底色已经是它自己的颜色了，再用同色就看不出这枚图标
        draw_icon(cr, icon, bx + bh / 2 + 1, by + bh / 2, size * 0.95,
                  mix_rgb(tint, (255, 255, 255), 0.72), 0.95 * alpha)
        draw_text(cr, text, bx + bh / 2 + 6 + size * 0.85, by + 8, size,
                  (250, 251, 255), 0.95 * alpha)
        ui.toast_rect = (bx, by, bw, bh)

    def draw_chip(self, cr, w, h):
        """预览提示（画在最上层，可点击返回此刻）。"""
        ui = self.ui
        if ui.preview_dt is None:
            ui.chip_rect = (0, 0, 0, 0)
            return
        text = f"正在预览 {ui.preview_dt.strftime('%H:%M')}　·　点这里回到此刻"
        size = clamp(h * 0.019, 12.0, 17.0)
        tw, th = draw_text(cr, text, 0, -1000, size, (255, 255, 255), 0.0)
        bw, bh = tw + 36, th + 16
        bx = w / 2 - bw / 2
        by = h * 0.028
        cr.set_source_rgba(0.05, 0.06, 0.10, 0.66)
        rounded_rect(cr, bx, by, bw, bh, bh / 2)
        cr.fill()
        cr.set_source_rgba(1, 1, 1, 0.14)
        rounded_rect(cr, bx, by, bw, bh, bh / 2)
        cr.set_line_width(1)
        cr.stroke()
        draw_text(cr, text, w / 2, by + 7, size, (250, 251, 255), 0.95, align="center")
        ui.chip_rect = (bx, by, bw, bh)
