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
    rank: int = -1     # 在这条道（这一群）里的编号，决定"这一趟谁上路"
    total: int = 0     # 这一群一共几个（0 = 不按趟数调度，永远上路）


def roster(seed: int) -> list[Actor]:
    """一整天的固定班底：谁在路上、开多快、往哪边，由种子决定。

    速度单位是"屏幕宽度/秒"，按真实观感分级（以前行人几乎和车一样快，
    看上去像在赶车）：
      · 汽车 ~9-13 秒穿画面（0.080-0.115）
      · 公交 ~14-18 秒（0.055-0.072）
      · 自行车 ~20-28 秒（0.036-0.050）
      · 行人 ~53-83 秒（0.012-0.019）——散步的速度，比车慢一个数量级

    同一条车道上的车**速度一致、发车时间均匀错开**：于是不会出现"两辆车叠成
    一辆"，也不会互相追尾。真正"路上有几辆车"由 activity() 按时刻与天气决定
    （班底比路上能同时看到的车多，上不上的都在画面外决定）。
    """
    rnd = random.Random(seed)
    actors: list[Actor] = []
    # 车道：远处那条向左开，近处那条向右开
    variants = (0, 0, 1, 2, 0, 3, 1, 4)      # 轿车多，也有掀背/SUV/厢式/出租
    bus_colours = ((0.36, 0.46, 0.54), (0.66, 0.32, 0.24))
    per_lane = 6
    for lane, (dirn, d0, d1) in enumerate(((-1, 0.50, 0.62), (1, 0.72, 0.82))):
        lane_speed = rnd.uniform(0.086, 0.112) * dirn
        for i in range(per_lane):
            variant = variants[(i + lane * 3) % len(variants)]
            colour = (_CAR_COLORS[7] if variant == 4
                      else _CAR_COLORS[rnd.randrange(len(_CAR_COLORS))])
            actors.append(Actor(
                kind="car",
                depth=rnd.uniform(d0, d1),
                # 同一条道上的车速度**完全一致**：差一点点都会在几十分钟后
                # 累积成"两辆车叠在一起"（相位差被速度差吃掉）
                speed=lane_speed,
                phase=(i + rnd.uniform(-0.05, 0.05)) / per_lane,
                when="always",
                umbrella=False,
                tone=rnd.uniform(0.9, 1.1),
                bob=0.0,
                variant=variant,
                colour=colour,
                rank=i, total=per_lane + 1,
            ))
        # 每条道上再排一辆公交：插在两辆车中间，速度跟这条道一致
        actors.append(Actor(
            "bus", rnd.uniform(d0, d1), lane_speed,
            (0.5 + rnd.uniform(-0.04, 0.04)) / per_lane,
            "always", False, rnd.uniform(0.92, 1.0), 0.0, 0, bus_colours[lane],
            per_lane, per_lane + 1))
    # 骑车的人：晴天多，雨雪少（靠 alpha 淡出）
    for i in range(4):
        actors.append(Actor(
            kind="cyclist",
            depth=rnd.uniform(0.62, 0.80),
            speed=(0.042 if i % 2 == 0 else 0.046) * (1 if i < 2 else -1),
            phase=(i % 2 + rnd.uniform(-0.12, 0.12)) / 2,
            when="day",
            umbrella=False,
            tone=rnd.uniform(0.8, 1.1),
            bob=rnd.uniform(0, TAU),
            colour=_CLOTHES[rnd.randrange(len(_CLOTHES))],
            rank=i % 2, total=2,
        ))
    # 行人：贴最前面走（离窗台最近）
    for i in range(12):
        back = i % 2
        variant = rnd.choices((0, 1, 2, 3, 4), weights=(5, 2, 1, 1, 2))[0]
        actors.append(Actor(
            kind="ped",
            depth=rnd.uniform(0.88, 1.0),
            speed=(0.0145 if back else 0.0165),
            phase=(i // 2 + rnd.uniform(-0.16, 0.16)) / 6.0,
            when=rnd.choice(("always", "always", "always", "day", "night")),
            umbrella=rnd.random() < 0.8,
            tone=rnd.uniform(0.7, 1.2),
            bob=rnd.uniform(0, TAU),
            variant=variant,
            colour=_CLOTHES[rnd.randrange(len(_CLOTHES))],
            rank=i // 2, total=6,
        ))
    return actors


def _visibility(a: Actor, scene) -> float:
    """这一个"是白天的人还是夜里的人"：0-1。

    天气与"这个点路上有几个"由 activity() 决定（见下），这里只管昼夜偏好，
    免得同一件事被扣两次。
    """
    day = clamp((scene.sun_alt + 6.0) / 8.0, 0.0, 1.0)
    if a.when == "day":
        v = day
    elif a.when == "night":
        v = 1.0 - day
    else:
        v = 1.0
    return clamp(v, 0.0, 1.0)


# --------------------------------------------------------------------------
# 这个点路上该有多少人、多少车
# --------------------------------------------------------------------------

def _hash01(*parts) -> float:
    """稳定的小伪随机数（同一趟车、同一时刻永远同样的结果）。"""
    h = 2166136261
    for p in parts:
        v = int(p * 10007.0) & 0xFFFFFFFF
        h ^= v & 0xFFFF
        h = (h * 16777619) & 0xFFFFFFFF
        h ^= (v >> 16) & 0xFFFF
        h = (h * 16777619) & 0xFFFFFFFF
    return (h % 100000) / 100000.0


# 一天里的"车流"与"人流"（0-1，按本地时刻插值）
_CAR_WEEKDAY = ((0, 0.10), (4.5, 0.06), (6.0, 0.30), (7.5, 0.85), (8.5, 1.00),
                (9.5, 0.80), (12.0, 0.62), (15.0, 0.60), (17.0, 0.90),
                (18.3, 1.00), (19.5, 0.78), (21.0, 0.55), (23.0, 0.26),
                (24, 0.10))
_CAR_WEEKEND = ((0, 0.26), (5.0, 0.10), (8.0, 0.30), (10.5, 0.70), (14.0, 0.82),
                (17.0, 0.86), (19.0, 0.72), (22.0, 0.55), (24, 0.26))
_PED_WEEKDAY = ((0, 0.05), (5.0, 0.12), (6.5, 0.38), (8.0, 1.00), (9.5, 0.42),
                (12.0, 0.58), (14.0, 0.40), (16.0, 0.46), (18.0, 1.00),
                (19.5, 0.62), (21.0, 0.36), (23.0, 0.12), (24, 0.05))
_PED_WEEKEND = ((0, 0.20), (6.0, 0.06), (9.0, 0.30), (11.0, 0.68), (14.0, 0.82),
                (17.0, 0.76), (19.0, 0.70), (22.0, 0.46), (24, 0.20))


def _curve(hour: float, points) -> float:
    """按控制点线性插值一天里的曲线（points 覆盖 0-24 点）。"""
    hour = hour % 24.0
    for i in range(len(points) - 1):
        h0, v0 = points[i]
        h1, v1 = points[i + 1]
        if h0 <= hour <= h1:
            t = 0.0 if h1 == h0 else (hour - h0) / (h1 - h0)
            return v0 + (v1 - v0) * t
    return points[-1][1]


def activity(when, scene) -> dict[str, float]:
    """此刻各类角色上路的密度 0-1：由**时刻 × 星期 × 天气**决定。

    工作日两个高峰（8 点、18 点）车最多、人也最多；周末的高峰晚一点、平一点；
    凌晨 2-5 点几乎没人；下雨行人明显变少、骑车几乎消失，雪天更少；
    起雾时大家都少一些。
    """
    hour = when.hour + when.minute / 60.0 + when.second / 3600.0
    weekend = when.weekday() >= 5
    cars = _curve(hour, _CAR_WEEKEND if weekend else _CAR_WEEKDAY)
    peds = _curve(hour, _PED_WEEKEND if weekend else _PED_WEEKDAY)
    # 公交有自己的班次：白天一直在跑，夜里稀，但不跟着高峰那么陡
    buses = clamp(0.18 + 0.85 * cars, 0.0, 1.0)
    cyclists = peds * clamp(0.55 + 0.45 * (1.0 - abs(hour - 13.0) / 9.0), 0.0, 1.0)

    if scene.has_weather:
        s = clamp(scene.precip_strength, 0.0, 1.0)
        if scene.precip_kind == "rain":
            peds *= 1.0 - 0.62 * s
            cyclists *= 1.0 - 0.88 * s
            cars *= 1.0 - 0.12 * s
            buses *= 1.0 - 0.08 * s
        elif scene.precip_kind == "snow":
            peds *= 1.0 - 0.45 * s
            cyclists *= 1.0 - 0.92 * s
            cars *= 1.0 - 0.35 * s
            buses *= 1.0 - 0.25 * s
        if scene.thunder:
            peds *= 0.55
            cyclists *= 0.4
        if scene.fog:
            peds *= 0.8
            cars *= 0.85
    return {"car": clamp(cars, 0, 1), "bus": clamp(buses, 0, 1),
            "ped": clamp(peds, 0, 1), "cyclist": clamp(cyclists, 0, 1)}


def _on_road(a: Actor, t: float, density: float) -> bool:
    """这一趟它上不上路。

    "路上有几辆车"要跟着时刻与天气走，但又不能让人看见车在马路上凭空出现：
    每辆车一趟一趟地过画面（相位 + 速度 × 时间），每过一趟就是"第几趟"；
    每趟按当时的密度决定**这一群里放几辆上路**，具体哪几辆则逐趟轮换。
    这样：
      · 数量是确定的（一群体 6 辆、密度 0.5 就是 3 辆），不会一会儿满街一会儿空街；
      · 轮换发生在画面外（车正好绕回起点那一刻），看不见谁消失；
      · 高密度时按"均匀挑"的次序选车，留下的车在街上仍然分布得开。
    """
    if density >= 0.999:
        return True
    if density <= 0.002:
        return False
    if a.total > 0 and a.rank >= 0:
        span = 1.30
        slot = math.floor((a.phase + a.speed * t) / span)
        want = int(round(density * a.total))
        if want >= a.total:
            return True
        if want <= 0:
            return False
        rot = slot % a.total
        # 均匀挑选（Bresenham 那套）：6 选 2 会挑到第 3、第 6 辆，而不是前两辆
        i = (a.rank + rot) % a.total
        return math.floor((i + 1) * want / a.total) > math.floor(i * want / a.total)
    span = 1.30
    slot = math.floor((a.phase + a.speed * t) / span)
    return _hash01(a.phase * 977.0, a.depth * 613.0, slot * 0.6180339) < density


def draw(cr, w: float, h: float, scene, actors: list[Actor], t: float,
         light, base_col, lamps=(), trees=()) -> None:
    """把人和车画在地面带上（在剪影之上、窗台之下）。"""
    if not actors:
        return
    _trees(cr, w, h, scene, light, trees, t)
    _lamps(cr, w, h, scene, light, lamps)
    rain = (scene.has_weather and scene.precip_kind == "rain"
            and scene.precip_strength > 0.15)
    dens = activity(scene.when, scene)
    for a in sorted(actors, key=lambda a: a.depth):
        vis = _visibility(a, scene)
        if vis <= 0.03:
            continue
        if not _on_road(a, t, dens.get(a.kind, 0.6)):
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

def trees(seed: int) -> list[tuple[float, float, int]]:
    """路边的行道树：(横向位置 0-1, 大小系数, 形状种子)。

    它们站在**远侧人行道**上——比车道更远，所以按透视只能比人和车"矮一档"：
    树冠大致到旁边那几栋楼的二三层，不挡城市，也不抢街上的人。
    """
    rnd = random.Random((seed ^ 0x1F2E3D4C) & 0xFFFFFFFF)
    out: list[tuple[float, float, int]] = []
    x = rnd.uniform(0.03, 0.10)
    while x < 0.98:
        out.append((round(x, 4), rnd.uniform(0.82, 1.12), rnd.randrange(1000)))
        x += rnd.uniform(0.11, 0.26)
    return out


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
    sun_side = light.sun_side
    for fx, size, seed in trees:
        x = fx * w
        sway = math.sin(t * 0.55 + seed * 0.017) * wind * 1.6 * scale
        th = 40.0 * scale * size          # 整棵树的高度
        trunk_w = max(1.0, th * 0.10)
        cr.save()
        # 影子：和人和车用同一束光（太阳低就长、阴天就没有）
        _ground_shadow(cr, x + sway * 0.4, base_y + th * 0.02,
                       th * 0.34, th * 0.10, light, 0.7)
        _cast_shadow(cr, x, base_y, light, th * 0.9, th * 0.5, 0.5)
        # 树干
        trunk = shade(mix_rgb((0.22, 0.18, 0.15), green, 0.25),
                      0.55 + 0.55 * amb)
        cr.set_source_rgba(trunk[0], trunk[1], trunk[2], 0.96)
        cr.new_path()
        cr.move_to(x - trunk_w / 2, base_y)
        cr.line_to(x - trunk_w * 0.28 + sway * 0.5, base_y - th * 0.62)
        cr.line_to(x + trunk_w * 0.28 + sway * 0.5, base_y - th * 0.62)
        cr.line_to(x + trunk_w / 2, base_y)
        cr.close_path()
        cr.fill()
        # 树冠：几团叠出来的圆脑袋（先暗后亮，太阳那一侧更亮）
        cx = x + sway
        cy = base_y - th * 0.70
        rw = th * 0.46
        rh = th * 0.34
        back = shade(green, 0.74)
        front = shade(green, 1.12)
        blobs = ((-0.46, -0.04, 0.56), (0.44, -0.02, 0.54), (0.02, -0.44, 0.60),
                 (-0.24, 0.26, 0.58), (0.26, 0.22, 0.56), (0.0, 0.02, 0.82))
        # 树冠下缘压暗一点，圆脑袋才有体积
        shade_under = shade(green, 0.62)
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
