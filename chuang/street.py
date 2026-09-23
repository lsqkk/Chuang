"""街上的行人与车辆。

位置不是"逐帧推进"的状态，而是**时间的函数**：x = f(相位 + 速度 × 当天秒数)。
好处有三个：窗口里自然而然地走起来；动态壁纸的每一帧都能算出对应的位置，
于是帧与帧之间过渡时人会真的在移动；同一时刻永远画成同一个样子。
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
ZONE_BOTTOM = 0.846


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


def roster(seed: int) -> list[Actor]:
    """一整天的固定班底：谁在路上、开多快、往哪边，由种子决定。"""
    rnd = random.Random(seed)
    actors: list[Actor] = []
    # 车道：远处那条向左开，近处那条向右开
    for i in range(5):
        near = i % 2 == 0
        actors.append(Actor(
            kind="car",
            depth=rnd.uniform(0.45, 0.62) if not near else rnd.uniform(0.70, 0.85),
            speed=rnd.uniform(0.030, 0.055) * (1 if near else -1),
            phase=rnd.random(),
            when="always",
            umbrella=False,
            tone=rnd.uniform(0.75, 1.15),
            bob=0.0,
        ))
    actors.append(Actor("bus", rnd.uniform(0.48, 0.55), rnd.uniform(-0.026, -0.020),
                        rnd.random(), "always", False, 0.9, 0.0))
    actors.append(Actor("bus", rnd.uniform(0.72, 0.80), rnd.uniform(0.022, 0.028),
                        rnd.random(), "always", False, 1.0, 0.0))
    # 骑车的人：晴天多，雨雪少（靠 alpha 淡出）
    # 速度单位是"屏幕宽度/秒"：行人横穿画面约 30-45 秒，自行车约 15-22 秒，
    # 汽车约 20-30 秒——和真实街景的观感一致。
    for i in range(3):
        actors.append(Actor(
            kind="cyclist",
            depth=rnd.uniform(0.60, 0.86),
            speed=rnd.uniform(0.045, 0.070) * (1 if rnd.random() < 0.5 else -1),
            phase=rnd.random(),
            when="day",
            umbrella=False,
            tone=rnd.uniform(0.8, 1.1),
            bob=rnd.uniform(0, TAU),
        ))
    # 行人：贴最前面走（离窗台最近）
    for i in range(7):
        actors.append(Actor(
            kind="ped",
            depth=rnd.uniform(0.86, 1.0),
            speed=rnd.uniform(0.022, 0.032) * (1 if rnd.random() < 0.5 else -1),
            phase=rnd.random(),
            when=rnd.choice(("always", "always", "always", "day", "night")),
            umbrella=rnd.random() < 0.8,
            tone=rnd.uniform(0.7, 1.2),
            bob=rnd.uniform(0, TAU),
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
         ambient: float, near_col, glow_color, night_lights: bool) -> None:
    """把人和车画在地面带上（在剪影之上、窗台之下）。"""
    if not actors:
        return
    s = h / 760.0
    y_top = ZONE_TOP * h
    y_bottom = ZONE_BOTTOM * h
    car_col = mix_rgb((0.16, 0.16, 0.18), tuple(c / 255 for c in near_col), 0.35)
    car_col = shade(car_col, 0.55 + 0.5 * ambient)
    for a in actors:
        vis = _visibility(a, scene)
        if vis <= 0.03:
            continue
        x = ((a.phase + a.speed * t) % 1.3 - 0.15) * w
        y = y_top + (y_bottom - y_top) * a.depth
        if a.kind == "ped":
            _ped(cr, x, y, s, a, vis, scene, car_col, ambient, t)
        elif a.kind == "cyclist":
            _cyclist(cr, x, y, s, a, vis, car_col, ambient)
        elif a.kind == "car":
            _car(cr, x, y, s, a, vis, car_col, glow_color, night_lights)
        else:
            _bus(cr, x, y, s, a, vis, car_col, glow_color, night_lights)


# --------------------------------------------------------------------------
# 各种角色：都是剪影，夜里靠灯说话
# --------------------------------------------------------------------------

def _ped(cr, x, y, s, a: Actor, vis: float, scene, base_col, ambient, t):
    hp = 21.0 * s * (0.85 + 0.3 * a.depth)          # 身高
    col = shade(base_col, 0.75 + 0.35 * a.tone)
    alpha = clamp(0.72 * vis, 0, 1)
    step = math.sin(t * max(6.0, abs(a.speed) * 260.0) + a.bob)
    bob = abs(step) * 0.9 * s
    y -= bob
    cr.save()
    cr.set_line_cap(1)
    # 腿
    cr.set_source_rgba(col[0], col[1], col[2], alpha)
    cr.set_line_width(max(1.0, 1.5 * s))
    for sign in (-1, 1):
        cr.move_to(x, y - hp * 0.46)
        cr.line_to(x + sign * step * hp * 0.16, y)
        cr.stroke()
    # 身体
    cr.set_source_rgba(col[0], col[1], col[2], alpha)
    cr.rectangle(x - hp * 0.11, y - hp * 0.78, hp * 0.22, hp * 0.34)
    cr.fill()
    # 头
    cr.arc(x, y - hp * 0.86, hp * 0.105, 0, TAU)
    cr.fill()
    # 雨伞
    if a.umbrella and scene.has_weather and scene.precip_kind == "rain" \
            and scene.precip_strength > 0.12:
        ub = hp * 0.40
        cr.set_source_rgba(col[0], col[1], col[2], alpha * 0.95)
        cr.set_line_width(max(0.8, 1.1 * s))
        cr.move_to(x, y - hp * 1.02)
        cr.line_to(x, y - hp * 1.55)
        cr.stroke()
        cr.new_path()
        cr.move_to(x - ub, y - hp * 1.50)
        cr.curve_to(x - ub * 0.55, y - hp * 1.92, x + ub * 0.55, y - hp * 1.92,
                    x + ub, y - hp * 1.50)
        cr.close_path()
        cr.fill()
    cr.restore()


def _cyclist(cr, x, y, s, a: Actor, vis: float, base_col, ambient):
    hp = 25.0 * s
    col = shade(base_col, 0.80 + 0.3 * a.tone)
    alpha = clamp(0.70 * vis, 0, 1)
    r = hp * 0.17
    dirn = 1 if a.speed >= 0 else -1
    cr.save()
    cr.set_source_rgba(col[0], col[1], col[2], alpha)
    for wx in (-hp * 0.32, hp * 0.32):
        cr.arc(x + wx, y - r, r, 0, TAU)
        cr.set_line_width(max(1.0, 1.4 * s))
        cr.stroke()
    cr.set_line_width(max(1.0, 1.5 * s))
    cr.move_to(x - hp * 0.32, y - r)
    cr.line_to(x + hp * 0.30, y - r)
    cr.line_to(x + dirn * hp * 0.10, y - hp * 0.52)
    cr.line_to(x - hp * 0.14, y - hp * 0.52)
    cr.close_path()
    cr.stroke()
    # 骑手
    cr.set_source_rgba(col[0], col[1], col[2], alpha)
    cr.rectangle(x - hp * 0.08, y - hp * 0.86, hp * 0.17, hp * 0.34)
    cr.fill()
    cr.arc(x + dirn * hp * 0.01, y - hp * 0.94, hp * 0.09, 0, TAU)
    cr.fill()
    cr.restore()


def _car(cr, x, y, s, a: Actor, vis: float, base_col, glow_color, night_lights):
    ln = 46.0 * s * (0.85 + 0.35 * a.depth)
    hh = ln * 0.42
    col = shade(base_col, 0.70 + 0.4 * a.tone)
    alpha = clamp(0.82 * vis, 0, 1)
    dirn = 1 if a.speed >= 0 else -1
    cr.save()
    rounded_path(cr, x - ln / 2, y - hh, ln, hh * 0.72, hh * 0.22)
    cr.set_source_rgba(col[0], col[1], col[2], alpha)
    cr.fill()
    rounded_path(cr, x - ln * 0.26 + dirn * ln * 0.02, y - hh * 1.42,
                 ln * 0.52, hh * 0.62, hh * 0.26)
    cr.fill()
    # 车轮
    cr.set_source_rgba(col[0] * 0.5, col[1] * 0.5, col[2] * 0.5, alpha)
    for wx in (-ln * 0.28, ln * 0.28):
        cr.arc(x + wx, y - hh * 0.10, hh * 0.17, 0, TAU)
        cr.fill()
    # 白天的车窗：一条较亮的横带，让车身不是一坨黑
    if not night_lights:
        win = shade(col, 1.9)
        cr.set_source_rgba(min(1.0, win[0]), min(1.0, win[1]), min(1.0, win[2]),
                           0.55 * alpha)
        rounded_path(cr, x - ln * 0.20 + dirn * ln * 0.02, y - hh * 1.28,
                     ln * 0.40, hh * 0.34, hh * 0.14)
        cr.fill()
    if night_lights:
        # 车灯与地面光斑
        cr.set_operator(cairo.OPERATOR_ADD)
        hx = x + dirn * ln * 0.5
        g = glow_color
        lamp = cairo.RadialGradient(hx, y - hh * 0.45, 0, hx, y - hh * 0.45, hh * 1.35)
        lamp.add_color_stop_rgba(0, 1.0, 0.95, 0.78, 0.50 * vis)
        lamp.add_color_stop_rgba(0.35, 1.0, 0.90, 0.70, 0.18 * vis)
        lamp.add_color_stop_rgba(1, 1.0, 0.88, 0.70, 0)
        cr.set_source(lamp)
        cr.arc(hx, y - hh * 0.45, hh * 1.35, 0, TAU)
        cr.fill()
        pool = cairo.RadialGradient(hx + dirn * ln * 0.35, y, 0,
                                    hx + dirn * ln * 0.35, y, ln * 0.55)
        pool.add_color_stop_rgba(0, g[0] / 255, g[1] / 255, g[2] / 255, 0.13 * vis)
        pool.add_color_stop_rgba(1, g[0] / 255, g[1] / 255, g[2] / 255, 0)
        cr.set_source(pool)
        cr.save()
        cr.translate(hx + dirn * ln * 0.35, y)
        cr.scale(1.0, hh * 0.42 / (ln * 0.55))
        cr.arc(0, 0, ln * 0.55, 0, TAU)
        cr.fill()
        cr.restore()
        cr.set_operator(cairo.OPERATOR_OVER)
        # 尾灯
        cr.set_source_rgba(1.0, 0.30, 0.24, 0.85 * vis)
        cr.arc(x - dirn * ln * 0.48, y - hh * 0.50, hh * 0.14, 0, TAU)
        cr.fill()
    cr.restore()


def _bus(cr, x, y, s, a: Actor, vis: float, base_col, glow_color, night_lights):
    ln = 86.0 * s * (0.85 + 0.3 * a.depth)
    hh = ln * 0.34
    col = shade(base_col, 0.68 + 0.35 * a.tone)
    alpha = clamp(0.85 * vis, 0, 1)
    dirn = 1 if a.speed >= 0 else -1
    cr.save()
    rounded_path(cr, x - ln / 2, y - hh * 1.7, ln, hh * 1.7, hh * 0.22)
    cr.set_source_rgba(col[0], col[1], col[2], alpha)
    cr.fill()
    if night_lights:
        win = shade(col, 2.2 + 0.6 * vis)
        cr.set_source_rgba(min(1.0, win[0] + 0.35), min(1.0, win[1] + 0.28),
                           min(1.0, win[2] + 0.12), 0.75 * vis)
        n = 5
        for i in range(n):
            wx = x - ln * 0.40 + i * (ln * 0.8 / (n - 1))
            cr.rectangle(wx - ln * 0.035, y - hh * 1.45, ln * 0.07, hh * 0.42)
            cr.fill()
        cr.set_operator(cairo.OPERATOR_ADD)
        hx = x + dirn * ln * 0.5
        lamp = cairo.RadialGradient(hx, y - hh * 0.35, 0, hx, y - hh * 0.35, hh * 1.3)
        lamp.add_color_stop_rgba(0, 1.0, 0.95, 0.78, 0.42 * vis)
        lamp.add_color_stop_rgba(1, 1.0, 0.90, 0.70, 0)
        cr.set_source(lamp)
        cr.arc(hx, y - hh * 0.35, hh * 1.3, 0, TAU)
        cr.fill()
        cr.set_operator(cairo.OPERATOR_OVER)
    else:
        win = shade(col, 1.35)
        cr.set_source_rgba(win[0], win[1], win[2], 0.5 * alpha)
        cr.rectangle(x - ln * 0.42, y - hh * 1.48, ln * 0.84, hh * 0.44)
        cr.fill()
    cr.set_source_rgba(col[0] * 0.5, col[1] * 0.5, col[2] * 0.5, alpha)
    for wx in (-ln * 0.30, ln * 0.30):
        cr.arc(x + wx, y - hh * 0.16, hh * 0.20, 0, TAU)
        cr.fill()
    cr.restore()


def rounded_path(cr, x, y, w, h, r):
    """建立一条圆角矩形路径（调用方决定 fill / stroke）。"""
    r = min(r, abs(w) / 2, abs(h) / 2)
    x2, y2 = x + w, y + h
    cr.new_path()
    cr.move_to(x + r, y)
    cr.line_to(x2 - r, y)
    cr.arc(x2 - r, y + r, r, -math.pi / 2, 0)
    cr.line_to(x2, y2 - r)
    cr.arc(x2 - r, y2 - r, r, 0, math.pi / 2)
    cr.line_to(x + r, y2)
    cr.arc(x + r, y2 - r, r, math.pi / 2, math.pi)
    cr.line_to(x, y + r)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()
