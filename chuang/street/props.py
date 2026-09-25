
"""街边不动的那几样：行道树（按季节换色）与路灯。

树站在远侧人行道上，尺寸比人车矮一档（透视）；叶群用几团渐变堆出来，秋天会往
黄褐走。路灯杆白天只是剪影，夜里灯头下有自己的一圈光——那不是"加个亮点"，
而是路边真的会有的一小块亮斑。
"""

from __future__ import annotations

import math
import random

import cairo

from ..palette import mix_rgb, shade
from .actors import TAU, ZONE_TOP, clamp
from .shadows import _cast_shadow, _ground_shadow



# --------------------------------------------------------------------------
# 路灯
# --------------------------------------------------------------------------

def trees(seed: int) -> list[tuple[float, float, int]]:
    """路边的行道树：(横向位置 0-1, 大小系数, 形状种子)。

    它们站在**远侧人行道**上——比车道更远，所以按透视只能比人和车"矮一档"：
    树冠大致到旁边那几栋楼的二三层，不挡城市，也不抢街上的人。
    """
    rnd = random.Random((seed ^ 0x1F2E3D4C) & 0xFFFFFFFF)
    out: list[tuple[float, float, int]] = []
    x = rnd.uniform(0.03, 0.10)
    while x < 0.98:
        out.append((round(x, 4), rnd.uniform(0.82, 1.12), rnd.randrange(100000)))
        x += rnd.uniform(0.11, 0.26)
    return out


def _season_tint(scene, green):
    """秋天叶子会黄一点——按日期（南北半球分开）往琥珀色偏。

    只偏一点点：这是行道树、不是调色板，天色该压过季节才是主角。
    常绿的塔形树（种子里那一类）不受影响，见 _trees 里的用法。
    """
    try:
        day = scene.when.timetuple().tm_yday
    except Exception:                      # 调试用的假场景
        return green
    south = getattr(scene, "lat", 0.0) < 0
    if south:
        day = (day + 182) % 365
    # 9 月下旬到 11 月最黄，其余时候几乎不偏
    if 255 <= day <= 330:
        k = 1.0 - abs((day - 292) / 38.0)
        k = clamp(k, 0.0, 1.0) ** 1.2 * 0.42
        return mix_rgb(green, (0.62, 0.45, 0.13), k)
    return green


def _trees(cr, w, h, scene, light, trees, t) -> None:
    if not trees:
        return
    amb = clamp(light.ambient, 0, 1)
    base_y = (ZONE_TOP + 0.0035) * h
    scale = h / 760.0
    wind = clamp(getattr(scene, "wind_speed", 0.0) / 26.0, 0.0, 1.0)
    # 绿：白天是叶子的绿，夜里退成很暗的青灰
    day_green = mix_rgb((0.15, 0.32, 0.16), tuple(c / 255 for c in light.horizon), 0.12)
    night_green = mix_rgb((0.045, 0.055, 0.065),
                          tuple(c / 255 for c in light.glow), 0.12)
    green = mix_rgb(night_green, day_green, clamp((light.sun_alt + 3.0) / 14.0, 0, 1))
    green = shade(green, 0.55 + 0.45 * amb)
    green = _season_tint(scene, green)
    sun_side = light.sun_side
    for fx, size, seed in trees:
        rnd = random.Random(seed)
        x = fx * w
        sway = math.sin(t * 0.55 + seed * 0.017) * wind * 1.6 * scale
        th = 40.0 * scale * size          # 整棵树的高度
        trunk_w = max(1.0, th * 0.10)
        kind = seed % 3                   # 0 圆头 / 1 塔形（常绿）/ 2 伞形
        leaf = green
        if kind == 1:                     # 常绿：压暗一点、偏青
            leaf = shade(mix_rgb(green, (0.10, 0.22, 0.16), 0.55), 0.92)
        cr.save()
        # 影子：和人和车用同一束光（太阳低就长、阴天就没有）
        _ground_shadow(cr, x + sway * 0.4, base_y + th * 0.02,
                       th * 0.34, th * 0.10, light, 0.7)
        _cast_shadow(cr, x, base_y, light, th * 0.9, th * 0.5, 0.5)

        # 树池：树脚下那一小块深色（行道树长在硬地里，这一点很出气质）
        cr.set_source_rgba(0.05, 0.06, 0.05, 0.24 + 0.16 * amb)
        cr.save()
        cr.translate(x, base_y + th * 0.012)
        cr.scale(th * 0.20, max(1.0, th * 0.045))
        cr.arc(0, 0, 1.0, 0, TAU)
        cr.fill()
        cr.restore()

        # 树干：下粗上细，再分出两三条枝（枝条伸进树冠里，树才不是"插"上去的）
        trunk = shade(mix_rgb((0.22, 0.18, 0.15), green, 0.25),
                      0.55 + 0.55 * amb)
        cr.set_source_rgba(trunk[0], trunk[1], trunk[2], 0.96)
        cr.new_path()
        cr.move_to(x - trunk_w / 2, base_y)
        cr.line_to(x - trunk_w * 0.30 + sway * 0.45, base_y - th * 0.52)
        cr.line_to(x + trunk_w * 0.30 + sway * 0.45, base_y - th * 0.52)
        cr.line_to(x + trunk_w / 2, base_y)
        cr.close_path()
        cr.fill()
        fork = base_y - th * 0.52 + sway * 0.45
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_source_rgba(*[min(1.0, c * 0.92) for c in trunk], 0.95)
        for bdx, blen in ((-0.30, 0.30), (0.26, 0.34), (0.02, 0.42)):
            cr.set_line_width(max(0.8, trunk_w * (0.62 if bdx else 0.74)))
            cr.move_to(x + bdx * trunk_w * 0.5, fork)
            cr.line_to(x + bdx * th * blen + sway * 0.8,
                       fork - th * (0.16 + blen * 0.5))
            cr.stroke()

        # 树冠
        cx = x + sway
        cy = base_y - th * (0.68 if kind != 1 else 0.62)
        rw = th * (0.46 if kind != 2 else 0.52)
        rh = th * (0.34 if kind == 0 else 0.30 if kind == 1 else 0.26)
        back = shade(leaf, 0.74)
        front = shade(leaf, 1.12)
        if kind == 1:                       # 塔形：往上收
            blobs = ((-0.20, 0.30, 0.44), (0.22, 0.28, 0.42), (0.0, 0.02, 0.58),
                     (-0.13, -0.30, 0.40), (0.14, -0.32, 0.38), (0.0, -0.60, 0.30))
        elif kind == 2:                     # 伞形：压扁、往两边摊开
            blobs = ((-0.62, 0.06, 0.40), (0.60, 0.08, 0.38), (-0.30, -0.10, 0.50),
                     (0.32, -0.08, 0.48), (0.0, -0.34, 0.52), (0.0, -0.02, 0.78))
        else:                               # 圆头
            blobs = ((-0.48, -0.02, 0.54), (0.46, -0.04, 0.52), (0.02, -0.46, 0.58),
                     (-0.26, 0.26, 0.56), (0.28, 0.22, 0.54), (0.0, 0.0, 0.80))
        # 每一团上再叠几片"叶群"：光一个圆脑袋太像贴纸
        clumps = []
        for i in range(9):
            a = rnd.uniform(0, TAU)
            rr = rnd.uniform(0.25, 0.92)
            clumps.append((math.cos(a) * rr, math.sin(a) * rr * 0.72,
                           rnd.uniform(0.16, 0.30),
                           rnd.choice((-1, 1))))
        # 树冠下缘压暗一点，圆脑袋才有体积
        shade_under = shade(leaf, 0.62)
        for bx, by, br in blobs:
            r = rw * br
            if by > 0.24:
                col = back
            elif bx * (1 if sun_side >= 0 else -1) > 0:
                col = front
            else:
                col = back
            cr.set_source_rgba(col[0], col[1], col[2], 0.97)
            cr.save()
            cr.translate(cx + bx * rw, cy + by * rh)
            cr.scale(1.0, max(0.55, rh / rw))
            cr.arc(0, 0, r, 0, TAU)
            cr.fill()
            cr.restore()
        for bx, by, br, side in clumps:
            col = front if bx * side >= 0 else back
            col = shade(col, 1.06 if side > 0 else 0.88)
            cr.set_source_rgba(col[0], col[1], col[2], 0.55)
            cr.save()
            cr.translate(cx + bx * rw, cy + by * rh)
            cr.scale(1.0, max(0.55, rh / rw))
            cr.arc(0, 0, rw * br, 0, TAU)
            cr.fill()
            cr.restore()
        # 树冠底部：一条暗（树荫的味道）
        cr.set_source_rgba(shade_under[0], shade_under[1], shade_under[2], 0.6)
        cr.save()
        cr.translate(cx, cy + rh * 0.42)
        cr.scale(1.0, 0.5)
        cr.arc(0, 0, rw * 0.72, 0, TAU)
        cr.fill()
        cr.restore()
        # 阳光下树冠上缘的一点亮边（只一点点，不然整棵树会浮起来）
        if light.direct > 0.05 and amb > 0.25:
            hl = cairo.RadialGradient(cx, cy - rh * 0.5, 0, cx, cy - rh * 0.4,
                                      rw * 1.15)
            wc = light.warm
            hl.add_color_stop_rgba(0, wc[0], wc[1], wc[2],
                                   0.075 * light.direct * (1.0 - 0.5 * amb))
            hl.add_color_stop_rgba(1, wc[0], wc[1], wc[2], 0)
            cr.set_source(hl)
            cr.save()
            cr.translate(cx, cy)
            cr.scale(1.0, max(0.5, rh / rw))
            cr.arc(0, 0, rw * 1.05, 0, TAU)
            cr.fill()
            cr.restore()
        cr.restore()


def _lamps(cr, w, h, scene, light, lamps) -> None:
    if not lamps:
        return
    amb = clamp(light.ambient, 0, 1)
    on = clamp((0.55 - amb) / 0.5, 0.0, 1.0) if light.night > 0.05 else 0.0
    base_y = (ZONE_TOP + 0.005) * h
    pole_h = h * 0.028
    lw = max(1.0, h * 0.0018)
    arm = w * 0.005
    for fx in lamps:
        x = fx * w
        col = shade(mix_rgb((0.52, 0.52, 0.54),
                            tuple(c / 255 for c in scene.mood.horizon), 0.25),
                    0.32 + 0.60 * amb)
        cr.set_source_rgba(col[0], col[1], col[2], 0.92)
        cr.rectangle(x - lw / 2, base_y - pole_h, lw, pole_h)
        cr.fill()
        cr.rectangle(x - lw / 2, base_y - pole_h, arm, lw * 0.9)
        cr.fill()
        head_x = x + arm
        cr.set_source_rgba(*shade(col, 1.15), 0.92)
        cr.rectangle(head_x - arm * 0.3, base_y - pole_h - lw * 1.3,
                     arm * 0.8 + lw, lw * 1.5)
        cr.fill()
        if on <= 0.02:
            continue
        cr.save()
        cr.set_operator(cairo.OPERATOR_ADD)
        gy = base_y + h * 0.013
        gr = cairo.RadialGradient(head_x, gy, 0, head_x, gy, h * 0.055)
        gr.add_color_stop_rgba(0, 1.0, 0.88, 0.66, 0.22 * on)
        gr.add_color_stop_rgba(0.45, 1.0, 0.86, 0.64, 0.09 * on)
        gr.add_color_stop_rgba(1, 1.0, 0.86, 0.64, 0)
        cr.set_source(gr)
        cr.save()
        cr.translate(head_x, gy)
        cr.scale(1.0, 0.40)
        cr.arc(0, 0, h * 0.055, 0, TAU)
        cr.fill()
        cr.restore()
        halo = cairo.RadialGradient(head_x, base_y - pole_h - lw, 0,
                                    head_x, base_y - pole_h - lw, h * 0.014)
        halo.add_color_stop_rgba(0, 1.0, 0.93, 0.78, 0.36 * on)
        halo.add_color_stop_rgba(1, 1.0, 0.93, 0.78, 0)
        cr.set_source(halo)
        cr.arc(head_x, base_y - pole_h - lw, h * 0.014, 0, TAU)
        cr.fill()
        cr.restore()
