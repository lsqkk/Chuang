
"""谁在街上：名单（`roster`）、一天里各时段的密度（`activity`）、以及"这一趟排到谁"。

位置不是"逐帧推进"的状态，而是**时间的函数**，所以同一时刻永远画成同一个样子，
动态壁纸的每一帧也能算出对应位置。数量与换班规则都在这里：同一条车道上的车速必须
**完全一致**（`Actor.rank` / `total` 就是干这个的），否则相位差会被速度差吃掉，
几十分钟后两辆车会叠在一起（AGENTS.md §9）。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass



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
