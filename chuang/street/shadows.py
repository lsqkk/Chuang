
"""人、车、树投在路面上的影子——全都来自窗外的同一束太阳光。

影子的方向与长度由太阳方位角与高度角算出来（低太阳影子长、太阳偏左影子往右倒），
云厚、下雨、起雾时直射光被削掉，影子跟着淡到没有。街上的影子和窗台上那盆植物的
影子用的是同一套参数（见 `render/sill.py`），所以整个画面里的影子指向同一边。
"""

from __future__ import annotations

import math

import cairo

from .actors import TAU, clamp



# --------------------------------------------------------------------------
# 影子：人和车投在路面上的那一小片
# --------------------------------------------------------------------------

def _shadow_geom(light, height_px: float):
    """(横向偏移, 纵向偏移, 长短系数)：太阳越偏、越低，影子越长越斜。

    纵向这一项以前**根本没有**——人和车的影子只会横向斜一条，于是"光从哪儿
    来"在画面里读不出来。地面在画面上是一条极窄的带子（横向几十像素一米，
    往观察者这一侧被透视压得很扁），所以纵向分量要收着画，但它必须在：
    太阳在窗外正对面的时候，影子是朝窗里（屏幕下方）铺过来的。
    """
    rel = math.radians(light.rel_az)
    alt = max(light.sun_alt, 2.0)
    length = clamp(1.0 / math.tan(math.radians(alt)), 0.0, 6.0)
    return (-math.sin(rel) * length * height_px * 0.34,
            math.cos(rel) * length * height_px * 0.17,
            clamp(length / 2.6, 0.35, 1.0))


def _ground_shadow(cr, x, y, rx, ry, light, strength) -> None:
    """一片贴地的软影（椭圆渐变）：车底、人脚下的那一点。"""
    a = clamp(strength * (0.22 + 0.46 * light.direct), 0.0, 0.7)
    if a <= 0.01 or rx <= 0.2:
        return
    g = cairo.RadialGradient(0, 0, 0, 0, 0, 1.0)
    g.add_color_stop_rgba(0, 0.02, 0.02, 0.03, a)
    g.add_color_stop_rgba(0.55, 0.02, 0.02, 0.03, a * 0.5)
    g.add_color_stop_rgba(1, 0.02, 0.02, 0.03, 0)
    cr.save()
    cr.translate(x, y)
    cr.scale(rx, max(0.35, ry))
    cr.set_source(g)
    cr.arc(0, 0, 1.0, 0, TAU)
    cr.fill()
    cr.restore()


def _cast_shadow(cr, x, y, light, height, width, strength=1.0) -> None:
    """人/车拖在地上的长影：往背光侧斜过去的一条。"""
    if light.direct <= 0.02:
        return
    dx, dy, k = _shadow_geom(light, height)
    a = 0.44 * light.direct * strength
    reach = width * 0.5 + abs(dx)
    cx, cy = x + dx * 0.42, y + dy * 0.55 - height * 0.04
    g = cairo.RadialGradient(cx, cy, 0, cx, cy, max(2.0, reach))
    g.add_color_stop_rgba(0, 0.02, 0.02, 0.03, a)
    g.add_color_stop_rgba(0.55, 0.02, 0.02, 0.03, a * 0.55)
    g.add_color_stop_rgba(1, 0.02, 0.02, 0.03, 0)
    cr.save()
    cr.translate(cx, cy)
    # 纵向半径：影子朝观察者这一侧铺得越远，看着越"厚"
    ry = (height * 0.12 * k + abs(dy) * 0.55) / max(2.0, reach)
    cr.scale(1.0, max(0.16, min(0.8, ry)))
    cr.translate(-cx, -cy)
    cr.set_source(g)
    cr.arc(cx, cy, max(2.0, reach), 0, TAU)
    cr.fill()
    cr.restore()
