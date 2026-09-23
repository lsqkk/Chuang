"""街上的行人与车辆。

位置不是"逐帧推进"的状态，而是**时间的函数**：x = f(相位 + 速度 × 当天秒数)。
好处有三个：窗口里自然而然地走起来；动态壁纸的每一帧都能算出对应的位置，
于是帧与帧之间过渡时人会真的在移动；同一时刻永远画成同一个样子。

1.1.4 之前这里只有"长条车身 + 两个露在外面的圆轱辘"的车和"火柴人"的行人，
而且人和车都不投影。现在：

  · 车轮压在车身的轮拱里——只有贴着地面的那一段露出来，不是骑在圆圈上；
  · 车有轿车 / 掀背 / SUV / 厢式 / 出租 / 公交几种轮廓，白天车窗映天、
    夜里车灯会在地上洒一片光；
  · 人是"走"的：腿有膝盖、手臂反相摆动、有躯干和头，雨天撑伞，还可以牵着狗；
  · 每个人、每辆车都在路面上投下影子，方向与长短由太阳方位角与高度角决定。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

import cairo

from .palette import mix_rgb, shade


def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


TAU = math.pi * 2

# 地面带（地平线到窗台之间那一条）
ZONE_TOP = 0.800
ZONE_BOTTOM = 0.853

# 车身颜色（0-1）：真实街上的车大多是这几个色
_CAR_COLORS = (
    (0.88, 0.88, 0.89),     # 白
    (0.64, 0.66, 0.69),     # 银
    (0.17, 0.18, 0.21),     # 黑
    (0.13, 0.21, 0.38),     # 深蓝
    (0.55, 0.14, 0.13),     # 红
    (0.21, 0.30, 0.27),     # 墨绿
    (0.32, 0.34, 0.38),     # 深灰
    (0.80, 0.66, 0.24),     # 出租车的黄
)

# 衣服颜色：白天看得见，夜里会自然压暗成剪影
_CLOTHES = (
    (0.18, 0.22, 0.34),     # 深蓝
    (0.30, 0.31, 0.34),     # 灰
    (0.46, 0.20, 0.20),     # 暗红
    (0.20, 0.33, 0.26),     # 松绿
    (0.54, 0.46, 0.32),     # 卡其
    (0.16, 0.17, 0.19),     # 黑
    (0.64, 0.60, 0.56),     # 米白
    (0.38, 0.27, 0.42),     # 紫
)

_SKIN = (0.78, 0.63, 0.51)


@dataclass
class Actor:
    kind: str          # ped / cyclist / car / bus
    depth: float       # 0 远 → 1 近
    speed: float       # 屏幕宽度 / 秒（正数向右，负数向左）
    phase: float       # 0-1
    when: str          # always / day / night
    umbrella: bool
    tone: float
    bob: float
    variant: int = 0   # 车型 / 行人的样子
    colour: tuple = (0.6, 0.6, 0.6)


def roster(seed: int) -> list[Actor]:
    """一整天的固定班底：谁在路上、开多快、往哪边，由种子决定。

    速度单位是"屏幕宽度/秒"，按真实观感分级（以前行人几乎和车一样快，
    看上去像在赶车）：
      · 汽车 ~9-13 秒穿画面（0.080-0.115）
      · 公交 ~14-18 秒（0.055-0.072）
      · 自行车 ~20-28 秒（0.036-0.050）
      · 行人 ~53-83 秒（0.012-0.019）——散步的速度，比车慢一个数量级
    """
    rnd = random.Random(seed)
    actors: list[Actor] = []
    # 车道：远处那条向左开，近处那条向右开
    variants = (0, 0, 1, 2, 0, 3, 1, 4)      # 轿车多，也有掀背/SUV/厢式/出租
    for i in range(8):
        near = i % 2 == 0
        variant = variants[i % len(variants)]
        colour = (_CAR_COLORS[7] if variant == 4
                  else _CAR_COLORS[rnd.randrange(len(_CAR_COLORS))])
        actors.append(Actor(
            kind="car",
            depth=rnd.uniform(0.48, 0.64) if not near else rnd.uniform(0.72, 0.82),
            speed=rnd.uniform(0.080, 0.115) * (1 if near else -1),
            phase=rnd.random(),
            when="always",
            umbrella=False,
            tone=rnd.uniform(0.9, 1.1),
            bob=0.0,
            variant=variant,
            colour=colour,
        ))
    actors.append(Actor("bus", rnd.uniform(0.52, 0.58), rnd.uniform(-0.072, -0.055),
                        rnd.random(), "always", False, 0.95, 0.0, 0,
                        (0.36, 0.46, 0.54)))
    actors.append(Actor("bus", rnd.uniform(0.76, 0.82), rnd.uniform(0.055, 0.072),
                        rnd.random(), "always", False, 1.0, 0.0, 0,
                        (0.66, 0.32, 0.24)))
    # 骑车的人：晴天多，雨雪少（靠 alpha 淡出）
    for _ in range(3):
        actors.append(Actor(
            kind="cyclist",
            depth=rnd.uniform(0.62, 0.80),
            speed=rnd.uniform(0.036, 0.050) * (1 if rnd.random() < 0.5 else -1),
            phase=rnd.random(),
            when="day",
            umbrella=False,
            tone=rnd.uniform(0.8, 1.1),
            bob=rnd.uniform(0, TAU),
            colour=_CLOTHES[rnd.randrange(len(_CLOTHES))],
        ))
    # 行人：贴最前面走（离窗台最近）
    for _ in range(9):
        variant = rnd.choices((0, 1, 2, 3, 4), weights=(5, 2, 1, 1, 2))[0]
        actors.append(Actor(
            kind="ped",
            depth=rnd.uniform(0.88, 1.0),
            speed=rnd.uniform(0.012, 0.019) * (1 if rnd.random() < 0.5 else -1),
            phase=rnd.random(),
            when=rnd.choice(("always", "always", "always", "day", "night")),
            umbrella=rnd.random() < 0.8,
            tone=rnd.uniform(0.7, 1.2),
            bob=rnd.uniform(0, TAU),
            variant=variant,
            colour=_CLOTHES[rnd.randrange(len(_CLOTHES))],
        ))
    return actors


def _visibility(a: Actor, scene) -> float:
    """这个人在此刻路上出现的程度 0-1：昼夜偏好 × 天气。"""
    day = clamp((scene.sun_alt + 6.0) / 8.0, 0.0, 1.0)
    if a.when == "day":
        v = day
    elif a.when == "night":
        v = 1.0 - day
    else:
        v = 1.0
    if scene.has_weather:
        kind = scene.precip_kind
        strength = scene.precip_strength
        if kind != "none" and strength > 0.15:
            if a.kind == "cyclist":
                v *= clamp(1.0 - 1.6 * strength, 0.0, 1.0)
            elif a.kind == "ped":
                v *= clamp(1.0 - 0.5 * strength, 0.2, 1.0)
            else:
                v *= clamp(1.0 - 0.2 * strength, 0.6, 1.0)
        if scene.fog:
            v *= 0.55
    return clamp(v, 0.0, 1.0)


def draw(cr, w: float, h: float, scene, actors: list[Actor], t: float,
         light, base_col, lamps=()) -> None:
    """把人和车画在地面带上（在剪影之上、窗台之下）。"""
    if not actors:
        return
    _lamps(cr, w, h, scene, light, lamps)
    rain = (scene.has_weather and scene.precip_kind == "rain"
            and scene.precip_strength > 0.15)
    for a in sorted(actors, key=lambda a: a.depth):
        vis = _visibility(a, scene)
        if vis <= 0.03:
            continue
        x = ((a.phase + a.speed * t) % 1.3 - 0.15) * w
        y = (ZONE_TOP + (ZONE_BOTTOM - ZONE_TOP) * a.depth) * h
        s = h / 760.0
        if a.kind == "ped":
            _ped(cr, x, y, s, w, a, vis, scene, light, base_col, t, rain)
        elif a.kind == "cyclist":
            _cyclist(cr, x, y, s, w, a, vis, scene, light, base_col, t)
        elif a.kind == "car":
            _car(cr, x, y, s, w, a, vis, scene, light, rain)
        else:
            _bus(cr, x, y, s, w, a, vis, scene, light)


# --------------------------------------------------------------------------
# 影子：人和车投在路面上的那一小片
# --------------------------------------------------------------------------

def _shadow_geom(light, height_px: float):
    """(横向偏移, 长短系数)：太阳越偏、越低，影子越长越斜。"""
    sun_hx = math.sin(math.radians(light.rel_az))
    alt = max(light.sun_alt, 1.5)
    length = clamp(1.0 / math.tan(math.radians(alt)), 0.0, 8.0)
    return (-sun_hx * length * height_px * 0.40,
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
    dx, k = _shadow_geom(light, height)
    a = 0.44 * light.direct * strength
    reach = width * 0.5 + abs(dx)
    cx, cy = x + dx * 0.40, y - height * 0.05
    g = cairo.RadialGradient(cx, cy, 0, cx, cy, max(2.0, reach))
    g.add_color_stop_rgba(0, 0.02, 0.02, 0.03, a)
    g.add_color_stop_rgba(0.55, 0.02, 0.02, 0.03, a * 0.55)
    g.add_color_stop_rgba(1, 0.02, 0.02, 0.03, 0)
    cr.save()
    cr.translate(cx, cy)
    cr.scale(1.0, max(0.16, min(0.7, (height * 0.12 * k) / max(2.0, reach))))
    cr.translate(-cx, -cy)
    cr.set_source(g)
    cr.arc(cx, cy, max(2.0, reach), 0, TAU)
    cr.fill()
    cr.restore()


# --------------------------------------------------------------------------
# 路灯
# --------------------------------------------------------------------------

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
    ra = r * 1.18
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

    # 1. 轮拱的暗腔 + 轮胎 + 轮毂
    for wx in (wx_r, wx_f):
        cr.set_source_rgba(*shade(paint, 0.16), alpha)
        cr.arc(wx, y - r, ra, 0, TAU)
        cr.fill()
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
    cr.stroke()

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
        cr.rectangle(x + 0.42 * ln, lamp_y - r * 0.30, 0.07 * ln, r * 0.60)
        cr.fill()
        cr.set_source_rgba(0.85, 0.28, 0.24, 0.50 * alpha)
        cr.rectangle(x - 0.49 * ln, lamp_y - r * 0.26, 0.05 * ln, r * 0.52)
        cr.fill()
    # 后视镜
    cr.set_source_rgba(paint[0], paint[1], paint[2], alpha)
    cr.rectangle(x + 0.16 * ln, y - hh * 0.76, 0.05 * ln, r * 0.35)
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
        cr.set_source_rgba(*shade(paint, 0.16), alpha)
        cr.arc(wx, y - r, ra, 0, TAU)
        cr.fill()
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
    cr.stroke()

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
