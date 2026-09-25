
"""行人：用腿走（有膝盖、手臂反相摆动）、雨天撑伞、还有人牵着狗；以及骑车的人。

每个人都在路面上投下影子——方向和长短由太阳方位角与高度角决定（`shadows.py`），
与街上的车、窗台上的盆栽用的是同一束光。
"""

from __future__ import annotations

import math

import cairo

from ..palette import mix_rgb, shade
from .actors import Actor, TAU, _SKIN, clamp
from .shadows import _cast_shadow, _ground_shadow



# --------------------------------------------------------------------------
# 行人
# --------------------------------------------------------------------------

_PED_SCALE = {2: 0.66, 3: 0.94}      # 小孩小一号；牵着狗的人走得慢


def _limb(cr, pts, width, colour, alpha) -> None:
    """一条有髋/膝/踝的肢体（两条线段 + 圆头）。"""
    cr.set_source_rgba(colour[0], colour[1], colour[2], alpha)
    cr.set_line_width(max(0.7, width))
    cr.move_to(*pts[0])
    for p in pts[1:]:
        cr.line_to(*p)
    cr.stroke()


def _ped(cr, x, y, s, w, a: Actor, vis, scene, light, base_col, t, rain) -> None:
    """一个正在走路的人：有膝盖、有摆臂、有头和肩，雨天撑伞。"""
    hp = 24.0 * s * (0.86 + 0.26 * a.depth) * _PED_SCALE.get(a.variant, 1.0)
    dirn = 1.0 if a.speed >= 0 else -1.0
    cad = clamp(abs(a.speed) * w / max(6.0, hp), 0.45, 2.4)
    ph = (t * cad + a.bob / TAU) * TAU
    swing = math.sin(ph)
    lift = max(0.0, math.sin(ph * 2.0)) * 0.05

    amb = clamp(light.ambient, 0.0, 1.0)
    cloth = mix_rgb(a.colour, tuple(c / 255 for c in base_col),
                    clamp(0.28 + 0.42 * (1.0 - amb), 0, 1) * 0.5)
    cloth = shade(cloth, (0.42 + 0.72 * amb) * (0.84 + 0.28 * a.tone))
    cloth = tuple(clamp(c, 0, 1) for c in cloth)
    back = shade(cloth, 0.74)
    alpha = clamp(0.88 * vis, 0, 1)
    rim_a = 0.40 * light.rake * vis

    _ground_shadow(cr, x, y, hp * 0.40, hp * 0.11, light, 0.9 * vis)
    _cast_shadow(cr, x, y, light, hp, hp * 0.24, 0.9 * vis)

    cr.save()
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    hip = (x, y - hp * 0.50)
    sho = (x + dirn * hp * 0.02, y - hp * 0.80)

    # 后面的腿、后面的手臂（先画，暗一档）
    _limb(cr, [(hip[0], hip[1]),
               (hip[0] - swing * hp * 0.13, y - hp * 0.26),
               (hip[0] - swing * hp * 0.26, y - lift * hp)],
          hp * 0.085, back, alpha)
    _limb(cr, [(sho[0], sho[1] + hp * 0.02),
               (sho[0] + swing * hp * 0.10, y - hp * 0.62),
               (sho[0] + swing * hp * 0.17, y - hp * 0.50)],
          hp * 0.060, back, alpha)

    # 躯干：肩宽腰窄
    cr.set_source_rgba(cloth[0], cloth[1], cloth[2], alpha)
    cr.new_path()
    cr.move_to(sho[0] - hp * 0.105, sho[1])
    cr.curve_to(sho[0] - hp * 0.125, sho[1] + hp * 0.14,
                hip[0] - hp * 0.095, hip[1] - hp * 0.12,
                hip[0] - hp * 0.085, hip[1])
    cr.line_to(hip[0] + hp * 0.085, hip[1])
    cr.curve_to(hip[0] + hp * 0.095, hip[1] - hp * 0.12,
                sho[0] + hp * 0.125, sho[1] + hp * 0.14,
                sho[0] + hp * 0.105, sho[1])
    cr.curve_to(sho[0] + hp * 0.07, sho[1] - hp * 0.055,
                sho[0] - hp * 0.07, sho[1] - hp * 0.055,
                sho[0] - hp * 0.105, sho[1])
    cr.close_path()
    cr.fill()

    # 前面的腿、前面的手臂
    _limb(cr, [(hip[0], hip[1]),
               (hip[0] + swing * hp * 0.14, y - hp * 0.25),
               (hip[0] + swing * hp * 0.28, y)],
          hp * 0.088, cloth, alpha)
    _limb(cr, [(sho[0], sho[1] + hp * 0.02),
               (sho[0] - swing * hp * 0.11, y - hp * 0.63),
               (sho[0] - swing * hp * 0.18, y - hp * 0.51)],
          hp * 0.062, cloth, alpha)
    if hp > 15:
        hand = shade(mix_rgb(_SKIN, cloth, 0.25), 0.45 + 0.55 * amb)
        cr.set_source_rgba(hand[0], hand[1], hand[2], alpha)
        cr.arc(sho[0] - swing * hp * 0.18, y - hp * 0.51, hp * 0.035, 0, TAU)
        cr.fill()

    # 脖子、头、头发
    neck = (sho[0], sho[1] - hp * 0.03)
    cr.set_source_rgba(cloth[0], cloth[1], cloth[2], alpha)
    cr.rectangle(neck[0] - hp * 0.032, neck[1], hp * 0.064, hp * 0.08)
    cr.fill()
    head_c = (sho[0] + dirn * hp * 0.012, sho[1] - hp * 0.135)
    hr = hp * 0.088
    if hp > 14:
        face = shade(mix_rgb(_SKIN, cloth, 0.15), 0.45 + 0.55 * amb)
        cr.set_source_rgba(face[0], face[1], face[2], alpha)
        cr.arc(head_c[0], head_c[1], hr, 0, TAU)
        cr.fill()
        hair = shade(cloth, 0.55)
        cr.set_source_rgba(hair[0], hair[1], hair[2], alpha)
        cr.new_path()
        cr.arc(head_c[0], head_c[1] + hr * 0.12, hr * 1.03,
               math.pi * 0.92, math.pi * 2.08)
        cr.close_path()
        cr.fill()
    else:
        cr.set_source_rgba(cloth[0], cloth[1], cloth[2], alpha)
        cr.arc(head_c[0], head_c[1], hr, 0, TAU)
        cr.fill()

    # 背包 / 牵着的小狗
    if a.variant == 1:
        cr.set_source_rgba(back[0], back[1], back[2], alpha)
        cr.rectangle(hip[0] - dirn * hp * 0.16 - hp * 0.05, y - hp * 0.74,
                     hp * 0.10, hp * 0.26)
        cr.fill()
    if a.variant == 3:
        dog_x = hip[0] - dirn * hp * 0.42
        dh = hp * 0.30
        dcol = shade(cloth, 0.82)
        cr.set_source_rgba(dcol[0], dcol[1], dcol[2], alpha)
        cr.set_line_width(max(0.7, hp * 0.045))
        cr.move_to(dog_x, y - dh * 0.55)
        cr.line_to(dog_x + dirn * dh * 0.5, y - dh * 0.55)
        cr.stroke()
        for lx in (0.02, 0.34):
            cr.move_to(dog_x + dirn * dh * lx, y - dh * 0.55)
            cr.line_to(dog_x + dirn * dh * lx, y)
            cr.stroke()
        cr.arc(dog_x + dirn * dh * 0.60, y - dh * 0.74, dh * 0.20, 0, TAU)
        cr.fill()
        cr.set_line_width(max(0.5, hp * 0.018))
        cr.move_to(hip[0], y - hp * 0.52)
        cr.line_to(dog_x + dirn * dh * 0.5, y - dh * 0.82)
        cr.stroke()

    # 雨伞
    if a.umbrella and rain and scene.precip_kind == "rain" \
            and scene.precip_strength > 0.12:
        ub = hp * 0.36
        top = sho[1] - hp * 0.28
        cr.set_source_rgba(shade(cloth, 0.88)[0], shade(cloth, 0.88)[1],
                           shade(cloth, 0.88)[2], alpha)
        cr.new_path()
        cr.move_to(sho[0] - ub, top)
        cr.curve_to(sho[0] - ub * 0.55, top - ub * 0.62,
                    sho[0] + ub * 0.55, top - ub * 0.62, sho[0] + ub, top)
        cr.curve_to(sho[0] + ub * 0.72, top - ub * 0.16,
                    sho[0] + ub * 0.30, top - ub * 0.06, sho[0], top + ub * 0.04)
        cr.curve_to(sho[0] - ub * 0.30, top - ub * 0.06,
                    sho[0] - ub * 0.72, top - ub * 0.16, sho[0] - ub, top)
        cr.close_path()
        cr.fill()
        cr.set_source_rgba(0, 0, 0, 0.16 * vis)
        cr.set_line_width(max(0.5, hp * 0.014))
        for k in (-0.5, 0.0, 0.5):
            cr.move_to(sho[0] + ub * k, top + ub * 0.02)
            cr.line_to(sho[0] + ub * k, top - ub * 0.34)
        cr.stroke()
        cr.set_source_rgba(shade(cloth, 0.72)[0], shade(cloth, 0.72)[1],
                           shade(cloth, 0.72)[2], alpha)
        cr.set_line_width(max(0.6, hp * 0.020))
        cr.move_to(sho[0], top + ub * 0.04)
        cr.line_to(sho[0], top + ub * 0.36)
        cr.stroke()

    # 受光的那一侧描一条边
    if rim_a > 0.02:
        side = -1.0 if light.sun_side < 0 else 1.0
        cr.set_source_rgba(light.warm[0], light.warm[1], light.warm[2], rim_a)
        cr.set_line_width(max(0.6, hp * 0.032))
        cr.move_to(sho[0] + side * hp * 0.105, sho[1] + hp * 0.01)
        cr.line_to(hip[0] + side * hp * 0.085, hip[1])
        cr.stroke()
    cr.restore()


# --------------------------------------------------------------------------
# 自行车
# --------------------------------------------------------------------------

def _cyclist(cr, x, y, s, w, a: Actor, vis, scene, light, base_col, t) -> None:
    hp = 26.0 * s
    dirn = 1.0 if a.speed >= 0 else -1.0
    amb = clamp(light.ambient, 0.0, 1.0)
    col = mix_rgb(a.colour, tuple(c / 255 for c in base_col),
                  clamp(0.30 + 0.40 * (1 - amb), 0, 1) * 0.45)
    col = shade(col, (0.40 + 0.70 * amb) * (0.86 + 0.24 * a.tone))
    col = tuple(clamp(c, 0, 1) for c in col)
    alpha = clamp(0.84 * vis, 0, 1)
    r = hp * 0.185
    wy = y - r
    wb = hp * 0.40
    pedal = math.sin(t * (2.4 + abs(a.speed) * 40) + a.bob)

    _ground_shadow(cr, x, y, hp * 0.52, hp * 0.10, light, 0.85 * vis)
    _cast_shadow(cr, x, y, light, hp * 0.8, hp * 0.28, 0.8 * vis)

    cr.save()
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_source_rgba(col[0] * 0.7, col[1] * 0.7, col[2] * 0.7, alpha)
    cr.set_line_width(max(0.8, hp * 0.032))
    for wx in (-wb / 2, wb / 2):
        cr.arc(x + wx, wy, r, 0, TAU)
        cr.stroke()
    cr.set_line_width(max(0.9, hp * 0.045))
    cr.set_source_rgba(col[0], col[1], col[2], alpha)
    cr.move_to(x - wb / 2, wy)
    cr.line_to(x - dirn * hp * 0.07, y - hp * 0.44)
    cr.line_to(x + wb / 2, wy)
    cr.stroke()
    cr.move_to(x - dirn * hp * 0.07, y - hp * 0.44)
    cr.line_to(x + dirn * hp * 0.14, y - hp * 0.62)
    cr.stroke()
    cr.move_to(x + dirn * hp * 0.14, y - hp * 0.62)
    cr.line_to(x + wb / 2, wy)
    cr.stroke()
    cr.set_line_width(max(0.8, hp * 0.030))
    cr.move_to(x + dirn * hp * 0.14, y - hp * 0.62)
    cr.line_to(x + dirn * hp * 0.10, y - hp * 0.80)
    cr.move_to(x - dirn * hp * 0.04, y - hp * 0.80)
    cr.line_to(x + dirn * hp * 0.22, y - hp * 0.80)
    cr.stroke()
    hip = (x - dirn * hp * 0.03, y - hp * 0.68)
    sho = (x + dirn * hp * 0.14, y - hp * 1.00)
    cr.set_source_rgba(col[0], col[1], col[2], alpha)
    cr.set_line_width(max(0.9, hp * 0.105))
    cr.move_to(hip[0], hip[1])
    cr.line_to(sho[0], sho[1])
    cr.stroke()
    cr.set_line_width(max(0.7, hp * 0.055))
    cr.move_to(sho[0], sho[1])
    cr.line_to(x + dirn * hp * 0.20, y - hp * 0.80)
    cr.stroke()
    fx = clamp(amb, 0.45, 1.0)
    for i, sgn in enumerate((1, -1)):
        kx = hip[0] + dirn * pedal * sgn * hp * 0.10
        ky = y - hp * 0.38 + pedal * sgn * hp * 0.07
        cr.set_source_rgba(col[0] * fx, col[1] * fx, col[2] * fx, alpha)
        cr.set_line_width(max(0.7, hp * 0.050))
        cr.move_to(hip[0], hip[1])
        cr.line_to(kx, ky)
        cr.stroke()
    cr.set_source_rgba(col[0], col[1], col[2], alpha)
    cr.arc(sho[0] + dirn * hp * 0.02, sho[1] - hp * 0.10, hp * 0.095, 0, TAU)
    cr.fill()
    cr.restore()
