
"""轿车 / 掀背 / SUV / 厢式 / 出租 / 公交：白天车窗映天，夜里车灯在地上洒一片光。

车轮压在车身的轮拱里——只有贴着地面的那一段露出来，不是骑在圆圈上。车身轮廓由
`_SHAPES` 描述，颜色、亮度、影子都跟着"此刻的光"走。
"""

from __future__ import annotations

import math

import cairo

from ..palette import mix_rgb, shade
from .actors import Actor, TAU, clamp
from .shadows import _cast_shadow, _ground_shadow



# --------------------------------------------------------------------------
# 车
# --------------------------------------------------------------------------

# 每种车的轮廓：(u, v) —— u 沿车长（-0.5 车尾 → +0.5 车头），v 是高度（0 地面 → 1 车顶）
_SHAPES = {
    0: ((0.50, 0.30), (0.50, 0.50), (0.36, 0.56), (0.12, 0.58),
        (-0.02, 0.92), (-0.16, 1.00), (-0.30, 0.96), (-0.40, 0.62),
        (-0.50, 0.56), (-0.50, 0.30)),
    1: ((0.50, 0.28), (0.50, 0.50), (0.34, 0.56), (0.10, 0.58),
        (-0.04, 0.94), (-0.26, 1.00), (-0.44, 0.94), (-0.50, 0.60),
        (-0.50, 0.28)),
    2: ((0.50, 0.30), (0.50, 0.54), (0.36, 0.60), (0.16, 0.64),
        (0.04, 0.98), (-0.30, 1.00), (-0.44, 0.74), (-0.50, 0.36),
        (-0.50, 0.30)),
    3: ((0.50, 0.30), (0.50, 0.74), (0.40, 0.94), (0.20, 1.00),
        (-0.36, 1.00), (-0.48, 0.84), (-0.50, 0.40), (-0.50, 0.30)),
}
_WHEELS = {                      # (后轮位置, 前轮位置, 轮半径/车长)
    0: (-0.29, 0.30, 0.070),
    1: (-0.28, 0.30, 0.072),
    2: (-0.30, 0.31, 0.078),
    3: (-0.32, 0.34, 0.082),
}
_BODY_H = {0: 0.315, 1: 0.335, 2: 0.345, 3: 0.400}


def _car(cr, x, y, s, w, a: Actor, vis, scene, light, rain) -> None:
    variant = int(clamp(int(a.variant), 0, 3))
    ln = 48.0 * s * (0.86 + 0.30 * a.depth)
    hh = ln * _BODY_H[variant]
    wb_r, wb_f, wr = _WHEELS[variant]
    r = ln * wr
    wx_r, wx_f = x + wb_r * ln, x + wb_f * ln
    dirn = 1.0 if a.speed >= 0 else -1.0
    amb = clamp(light.ambient, 0.0, 1.0)
    night_lights = scene.sun_alt < 3.0
    alpha = clamp(0.94 * vis, 0, 1)

    paint = shade(mix_rgb(a.colour, tuple(c / 255 for c in light.horizon),
                          0.14 * (1 - amb)),
                  (0.36 + 0.70 * amb) * (0.9 + 0.2 * a.tone))
    paint = tuple(clamp(c, 0, 1) for c in paint)
    tyre = shade(paint, 0.07)
    ra = r * 1.10
    rocker = y - r * 0.60

    _ground_shadow(cr, x + dirn * ln * 0.03, y + max(0.5, s * 0.5),
                   ln * 0.58, r * 1.4, light, vis)
    _cast_shadow(cr, x, y, light, hh, ln * 0.8, 0.85 * vis)

    cr.save()
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    if dirn < 0:                                  # 车头朝左：整辆车镜像
        cr.translate(x, 0)
        cr.scale(-1.0, 1.0)
        cr.translate(-x, 0)

    # 1. 轮拱里那点暗 + 轮胎 + 轮毂
    #    暗腔只画在"车门槛线之上"——轮拱是车身挖出来的洞，它不该在地面附近露出
    #    一整圈黑边（那会变成"车轮外面套了个黑圈"）。
    cr.save()
    cr.rectangle(-1e5, -1e5, 2e5, 1e5 + rocker)
    cr.clip()
    for wx in (wx_r, wx_f):
        cr.set_source_rgba(*shade(paint, 0.13), alpha)
        cr.arc(wx, y - r, ra, 0, TAU)
        cr.fill()
    cr.restore()
    for wx in (wx_r, wx_f):
        cr.set_source_rgba(tyre[0], tyre[1], tyre[2], alpha)
        cr.arc(wx, y - r, r, 0, TAU)
        cr.fill()
        cr.set_source_rgba(*shade(paint, 0.60), alpha * 0.85)
        cr.arc(wx, y - r, r * 0.42, 0, TAU)
        cr.fill()

    # 2. 车身：底边绕过两个轮子挖出轮拱
    cr.new_path()
    cr.move_to(x + _SHAPES[variant][0][0] * ln, y - _SHAPES[variant][0][1] * hh)
    for u, v in _SHAPES[variant][1:]:
        cr.line_to(x + u * ln, y - v * hh)
    cr.line_to(wx_r - ra, rocker)
    cr.arc(wx_r, y - r, ra, math.pi, 0)
    cr.arc(wx_f, y - r, ra, math.pi, 0)
    cr.line_to(x + 0.5 * ln, rocker)
    cr.close_path()
    g = cairo.LinearGradient(0, y - hh, 0, y)
    g.add_color_stop_rgb(0, *[clamp(c * 1.18, 0, 1) for c in paint])
    g.add_color_stop_rgb(0.55, *paint)
    g.add_color_stop_rgb(1, *[c * 0.60 for c in paint])
    cr.set_source(g)
    cr.fill_preserve()
    cr.set_source_rgba(*[c * 0.5 for c in paint], alpha * 0.85)
    cr.set_line_width(max(0.6, s * 0.8))
    cr.stroke_preserve()
    # 之后的车窗、车门缝都裁在车身里：车窗那一圈点算得再糙，也不会从车头/车顶
    # 冒出去（以前 SUV 和轿车的玻璃会在横向错出去一块）
    cr.save()
    cr.clip()

    # 3. 车窗
    glass_top = y - hh * 0.95
    glass_bot = y - hh * 0.60
    glass_f = shade(mix_rgb(tuple(c / 255 for c in light.horizon),
                            (0.09, 0.11, 0.15), 0.5), 0.5 + 0.55 * amb)
    glass_b = shade(glass_f, 0.72)
    if variant == 3:
        win = ((0.34, glass_bot + hh * 0.06), (0.06, glass_top + hh * 0.12),
               (-0.30, glass_top + hh * 0.12), (-0.40, glass_bot + hh * 0.06))
    elif variant == 1:
        win = ((0.30, glass_bot), (0.10, glass_top + hh * 0.05),
               (-0.16, glass_top + hh * 0.04), (-0.22, glass_bot))
    else:
        win = ((0.30, glass_bot), (0.10, glass_top + hh * 0.05),
               (-0.14, glass_top + hh * 0.03), (-0.24, glass_bot))
    cr.new_path()
    cr.move_to(x + win[0][0] * ln, win[0][1])
    for u, v in win[1:]:
        cr.line_to(x + u * ln, v)
    cr.close_path()
    gg = cairo.LinearGradient(0, glass_top, 0, glass_bot + hh * 0.1)
    gg.add_color_stop_rgb(0, *[clamp(c, 0, 1) for c in glass_b])
    gg.add_color_stop_rgb(1, *[clamp(c, 0, 1) for c in glass_f])
    cr.set_source(gg)
    cr.fill()
    cr.set_source_rgba(0, 0, 0, 0.20 * alpha)
    cr.set_line_width(max(0.5, s * 0.7))
    cr.move_to(x + 0.02 * ln, y - hh * 0.30)
    cr.line_to(x + 0.02 * ln, y - hh * 0.88)
    cr.stroke()
    cr.restore()

    # 4. 车灯
    lamp_y = y - hh * 0.42
    if night_lights:
        cr.save()
        cr.set_operator(cairo.OPERATOR_ADD)
        hx = x + 0.5 * ln
        lamp = cairo.RadialGradient(hx, lamp_y, 0, hx, lamp_y, r * 4.0)
        lamp.add_color_stop_rgba(0, 1.0, 0.94, 0.76, 0.32 * vis)
        lamp.add_color_stop_rgba(0.4, 1.0, 0.90, 0.70, 0.13 * vis)
        lamp.add_color_stop_rgba(1, 1.0, 0.88, 0.70, 0)
        cr.set_source(lamp)
        cr.arc(hx, lamp_y, r * 4.0, 0, TAU)
        cr.fill()
        gc = light.glow
        pool = cairo.RadialGradient(hx + ln * 0.30, y, 0, hx + ln * 0.30, y, ln * 0.45)
        pool.add_color_stop_rgba(0, gc[0] / 255, gc[1] / 255, gc[2] / 255, 0.13 * vis)
        pool.add_color_stop_rgba(1, gc[0] / 255, gc[1] / 255, gc[2] / 255, 0)
        cr.set_source(pool)
        cr.save()
        cr.translate(hx + ln * 0.30, y)
        cr.scale(1.0, 0.28)
        cr.arc(0, 0, ln * 0.45, 0, TAU)
        cr.fill()
        cr.restore()
        cr.restore()
        cr.set_source_rgba(1.0, 0.32, 0.26, 0.85 * vis)
        cr.arc(x - 0.47 * ln, lamp_y, max(0.7, r * 0.40), 0, TAU)
        cr.fill()
    else:
        cr.set_source_rgba(1.0, 0.96, 0.86, 0.60 * alpha)
        cr.rectangle(x + 0.435 * ln, lamp_y - r * 0.26, 0.055 * ln, r * 0.52)
        cr.fill()
        cr.set_source_rgba(0.85, 0.28, 0.24, 0.50 * alpha)
        cr.rectangle(x - 0.487 * ln, lamp_y - r * 0.22, 0.042 * ln, r * 0.44)
        cr.fill()
    # 后视镜：贴在 A 柱下方、比车身暗一点，不然像贴在车顶的一小块纸
    mirror = shade(paint, 0.72)
    cr.set_source_rgba(mirror[0], mirror[1], mirror[2], alpha)
    cr.rectangle(x + 0.15 * ln, y - hh * 0.70, max(1.0, 0.045 * ln),
                 max(1.0, r * 0.30))
    cr.fill()
    # 雨天车尾的水雾
    if rain and scene.precip_strength > 0.3:
        cr.set_source_rgba(*[min(1.0, c * 1.6) for c in paint], 0.12 * vis)
        cr.save()
        cr.translate(x - 0.75 * ln, y)
        cr.scale(1.0, 0.30)
        cr.arc(0, 0, ln * 0.35, 0, TAU)
        cr.fill()
        cr.restore()
    cr.restore()


def _bus(cr, x, y, s, w, a: Actor, vis, scene, light) -> None:
    ln = 88.0 * s * (0.85 + 0.28 * a.depth)
    hh = ln * 0.30
    r = ln * 0.062
    dirn = 1.0 if a.speed >= 0 else -1.0
    amb = clamp(light.ambient, 0.0, 1.0)
    night_lights = scene.sun_alt < 3.0
    alpha = clamp(0.94 * vis, 0, 1)
    paint = shade(mix_rgb(a.colour, tuple(c / 255 for c in light.horizon),
                          0.16 * (1 - amb)),
                  (0.40 + 0.64 * amb) * (0.9 + 0.18 * a.tone))
    paint = tuple(clamp(c, 0, 1) for c in paint)
    ra = r * 1.15
    rocker = y - r * 0.55
    wx_r, wx_f = x - 0.32 * ln, x + 0.32 * ln

    _ground_shadow(cr, x + dirn * ln * 0.03, y + max(0.5, s * 0.6),
                   ln * 0.55, r * 1.5, light, vis)
    _cast_shadow(cr, x, y, light, hh, ln * 0.75, 0.85 * vis)

    cr.save()
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    if dirn < 0:
        cr.translate(x, 0)
        cr.scale(-1.0, 1.0)
        cr.translate(-x, 0)
    for wx in (wx_r, wx_f):
        cr.save()
        cr.rectangle(-1e5, -1e5, 2e5, 1e5 + rocker)      # 轮拱暗腔不出门槛线
        cr.clip()
        cr.set_source_rgba(*shade(paint, 0.16), alpha)
        cr.arc(wx, y - r, ra, 0, TAU)
        cr.fill()
        cr.restore()
        cr.set_source_rgba(*shade(paint, 0.08), alpha)
        cr.arc(wx, y - r, r, 0, TAU)
        cr.fill()
        cr.set_source_rgba(*shade(paint, 0.62), alpha * 0.85)
        cr.arc(wx, y - r, r * 0.40, 0, TAU)
        cr.fill()

    front = x + 0.5 * ln
    rear = x - 0.5 * ln
    top = y - hh
    cr.new_path()
    cr.move_to(rear, rocker)
    cr.line_to(rear, top + hh * 0.10)
    cr.curve_to(rear, top, rear + ln * 0.06, top, rear + ln * 0.10, top)
    cr.line_to(front - ln * 0.10, top)
    cr.curve_to(front, top, front, top + hh * 0.10, front, top + hh * 0.16)
    cr.line_to(front, rocker)
    cr.line_to(wx_f - ra, rocker)
    cr.arc(wx_f, y - r, ra, math.pi, 0)
    cr.line_to(wx_r - ra, rocker)
    cr.arc(wx_r, y - r, ra, math.pi, 0)
    cr.close_path()
    g = cairo.LinearGradient(0, top, 0, y)
    g.add_color_stop_rgb(0, *[clamp(c * 1.16, 0, 1) for c in paint])
    g.add_color_stop_rgb(0.6, *paint)
    g.add_color_stop_rgb(1, *[c * 0.58 for c in paint])
    cr.set_source(g)
    cr.fill_preserve()
    cr.set_source_rgba(*[c * 0.5 for c in paint], alpha * 0.85)
    cr.set_line_width(max(0.6, s * 0.8))
    cr.stroke_preserve()
    cr.save()
    cr.clip()

    wy0 = top + hh * 0.16
    wy1 = top + hh * 0.50
    if night_lights:
        win_col = shade(paint, 2.0 + 0.5 * vis)
        a_win = 0.55 + 0.30 * clamp(light.night, 0, 1)
    else:
        win_col = shade(mix_rgb(tuple(c / 255 for c in light.horizon),
                                (0.10, 0.12, 0.16), 0.45), 0.7)
        a_win = 0.75
    cr.set_source_rgba(win_col[0], win_col[1], win_col[2], a_win * alpha)
    cr.rectangle(rear + ln * 0.04, wy0, ln * 0.92, wy1 - wy0)
    cr.fill()
    cr.set_source_rgba(0, 0, 0, 0.35 * alpha)
    n = 5
    for i in range(1, n):
        px = rear + ln * 0.04 + (ln * 0.92 / n) * i
        cr.rectangle(px - ln * 0.006, wy0, ln * 0.012, wy1 - wy0)
    cr.fill()
    if night_lights:
        cr.set_source_rgba(1.0, 0.92, 0.72, 0.45 * vis)
        cr.rectangle(front - ln * 0.045, wy0 + hh * 0.04, ln * 0.02,
                     (wy1 - wy0) * 0.9)
        cr.fill()
    cr.set_source_rgba(1.0, 1.0, 1.0, 0.10 * alpha)
    cr.rectangle(rear + ln * 0.02, top + hh * 0.58, ln * 0.96, max(0.8, hh * 0.06))
    cr.fill()
    cr.set_source_rgba(0, 0, 0, 0.20 * alpha)
    for dx_ in (-0.06, 0.16):
        cr.rectangle(x + dx_ * ln, top + hh * 0.54, max(0.7, s * 0.8), hh * 0.40)
    cr.fill()
    cr.restore()                    # 车窗结束：车灯的光晕要溢到车外，不裁
    if night_lights:
        cr.save()
        cr.set_operator(cairo.OPERATOR_ADD)
        lamp = cairo.RadialGradient(front, y - hh * 0.30, 0,
                                    front, y - hh * 0.30, r * 4.0)
        lamp.add_color_stop_rgba(0, 1.0, 0.94, 0.76, 0.36 * vis)
        lamp.add_color_stop_rgba(1, 1.0, 0.90, 0.70, 0)
        cr.set_source(lamp)
        cr.arc(front, y - hh * 0.30, r * 4.0, 0, TAU)
        cr.fill()
        gc = light.glow
        pool = cairo.RadialGradient(front + ln * 0.24, y, 0,
                                    front + ln * 0.24, y, ln * 0.40)
        pool.add_color_stop_rgba(0, gc[0] / 255, gc[1] / 255, gc[2] / 255, 0.14 * vis)
        pool.add_color_stop_rgba(1, gc[0] / 255, gc[1] / 255, gc[2] / 255, 0)
        cr.set_source(pool)
        cr.save()
        cr.translate(front + ln * 0.24, y)
        cr.scale(1.0, 0.26)
        cr.arc(0, 0, ln * 0.40, 0, TAU)
        cr.fill()
        cr.restore()
        cr.restore()
        cr.set_source_rgba(1.0, 0.32, 0.26, 0.85 * vis)
        cr.arc(rear + ln * 0.01, y - hh * 0.45, max(0.7, r * 0.42), 0, TAU)
        cr.fill()
    cr.restore()
