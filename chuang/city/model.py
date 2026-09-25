
"""长出这座城：远山 / 远景楼群 / 近景建筑三层，以及照在这座城上的那束光。

形状由"城市名 + 经纬度"做种子生成，所以同一座城市永远长成同一片屋顶（换个名字
就是另一座城，缓存键里带着这个种子）。`Light` 是"把此刻的天色翻译成建筑能用的
光照参数"——窗户、街道、行人和窗台上的盆栽全都读它，`render/core.py` 的
`_light()` 负责把它算出来。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

import cairo

from ..palette import mix_rgb, shade


TAU = math.pi * 2


def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def _norm(col):
    return (col[0] / 255.0, col[1] / 255.0, col[2] / 255.0)


def _grad(x0, y0, x1, y1):
    return cairo.LinearGradient(x0, y0, x1, y1)


def _hash01(*parts) -> float:
    """稳定的小伪随机数：同一扇窗永远亮/暗一样。"""
    h = 2166136261
    for p in parts:
        v = int(p * 1000003) & 0xFFFFFFFF
        h ^= v & 0xFFFF
        h = (h * 16777619) & 0xFFFFFFFF
        h ^= (v >> 16) & 0xFFFF
        h = (h * 16777619) & 0xFFFFFFFF
    return (h % 100000) / 100000.0


# --------------------------------------------------------------------------
# 此刻打在建筑上的光
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Light:
    """一帧的光照参数（由 render 从场景里算好）。"""

    ambient: float            # 环境光 0-1（决定整体亮度与夜的浓度）
    direct: float             # 直射阳光 0-1（已被云量、雾、雨削弱）
    sun_alt: float            # 太阳高度角（度）
    rel_az: float             # 太阳相对窗口视野的方位角（度，负 = 窗外左侧）
    zenith: tuple             # 天顶色 0-255
    horizon: tuple            # 地平色 0-255
    glow: tuple               # 太阳辉光色 0-255
    night: float              # 夜色浓度 0-1
    moon: float               # 月光强度 0-1
    moon_rel_az: float        # 月亮相对窗口的方位角（度）
    lit_frac: float           # 此刻有多少比例的窗亮着灯

    # ---- 派生量 ------------------------------------------------------
    @property
    def sun_side(self) -> int:
        """太阳在窗外的哪一侧：-1 左 / +1 右 / 0 正前方（分不出侧光）。"""
        if self.direct < 0.04 or self.sun_alt < -1.0:
            return 0
        if self.rel_az < -2.5:
            return -1
        if self.rel_az > 2.5:
            return 1
        return 0

    @property
    def rake(self) -> float:
        """侧光的"斜射"程度 0-1：太阳越低、越偏，侧面受光越明显。"""
        if self.sun_side == 0:
            return 0.0
        lateral = abs(math.sin(math.radians(self.rel_az)))
        low = clamp(1.0 - max(self.sun_alt, 0.0) / 42.0, 0.0, 1.0)
        return clamp(self.direct * lateral * (0.35 + 0.65 * low), 0.0, 1.0)

    @property
    def front_lit(self) -> float:
        """朝我们的那面墙接到多少直射光：太阳在窗后时最亮，正前方时背光。"""
        front = clamp(-math.cos(math.radians(self.rel_az)), 0.0, 1.0)
        vertical = clamp(math.sin(math.radians(max(self.sun_alt, 0.0))), 0.0, 1.0)
        return clamp(self.direct * (0.22 + 0.78 * front) * (0.35 + 0.65 * vertical),
                     0.0, 1.0)

    @property
    def warm(self) -> tuple:
        """受光面的颜色：太阳越低越暖。"""
        low = clamp(1.0 - self.sun_alt / 22.0, 0.0, 1.0)
        return mix_rgb(_norm(self.glow), (1.0, 0.97, 0.90), 0.45 - 0.35 * low)


# --------------------------------------------------------------------------
# 形状：远山 / 远景楼群 / 近景建筑
# --------------------------------------------------------------------------

@dataclass
class Building:
    """一栋建筑。尺寸都归一化：x、w 相对窗口宽度，h 相对窗口高度。"""

    x: float
    w: float
    h: float
    kind: str                 # tower / office / block / house / shop / factory / far
    pal: tuple                # 墙面基色（0-1）
    tone: float = 1.0         # 每栋楼的明暗差异
    seed: float = 0.0
    volumes: tuple = ()       # ((x0, x1, top), ...) 相对自身宽/高，top=1 是楼顶
    gable: bool = False       # 坡屋顶（画在最高的一层上）
    sawtooth: int = 0         # 厂房锯齿屋顶的齿数
    antenna: float = 0.0      # 天线高度（相对 h）
    tank: bool = False        # 屋顶水箱
    bulkhead: bool = False    # 屋顶楼梯间
    chimney: float = 0.0      # 烟囱高度（相对 h）
    ac: int = 0               # 屋顶空调机组数量
    balcony: int = 0          # 住宅阳台层数
    awning: bool = False      # 店面的雨棚
    glass: bool = False       # 玻璃幕墙
    win_w: float = 3.1        # 窗格尺寸（按 1200px 窗宽折算的像素）
    win_h: float = 4.2
    lit: bool = True          # 夜里这栋楼会不会亮灯

    @property
    def top(self) -> float:
        """最高一层相对自身高度的比例。"""
        return max((v[2] for v in self.volumes), default=1.0)


@dataclass
class Layers:
    """一座城市的三层剪影。"""

    ridge: list = field(default_factory=list)      # [(x, 高度比例, 宽)]
    far: list = field(default_factory=list)        # 远景楼群 [Building...]
    near: list = field(default_factory=list)       # 近景建筑 [Building...]
    lamps: list = field(default_factory=list)      # 路灯的横向位置 0-1


# 墙面基色：不是纯黑也不是纯白，一眼能分出"这是另一栋楼"
_PALETTES = (
    ((0.76, 0.74, 0.70), 0.20),     # 水泥灰
    ((0.80, 0.74, 0.60), 0.13),     # 米黄
    ((0.58, 0.62, 0.60), 0.12),     # 青灰
    ((0.54, 0.59, 0.66), 0.13),     # 灰蓝
    ((0.66, 0.47, 0.38), 0.12),     # 砖红
    ((0.72, 0.56, 0.44), 0.09),     # 陶土
    ((0.43, 0.47, 0.57), 0.10),     # 深蓝
    ((0.84, 0.83, 0.80), 0.11),     # 雪白
)


def _pick_pal(rnd, shift: float) -> tuple:
    r = rnd.random()
    acc = 0.0
    base = _PALETTES[0][0]
    for col, wgt in _PALETTES:
        acc += wgt
        if r <= acc:
            base = col
            break
    if abs(shift) > 1e-6:                      # 一条街上的楼也有冷暖差别
        tgt = (1.0, 0.88, 0.76) if shift > 0 else (0.78, 0.86, 1.0)
        base = mix_rgb(base, tgt, abs(shift) * 0.30)
    return base


def generate(seed: int) -> Layers:
    """由城市种子生成三层城市。"""
    rnd = random.Random((seed ^ 0x5BF03635) & 0xFFFFFFFF)

    # ---- 远山：一条起伏的山脊 -----------------------------------------
    ridge = []
    x = -0.06
    hgt = 0.055
    while x < 1.06:
        w = rnd.uniform(0.05, 0.14)
        hgt = clamp(hgt + rnd.uniform(-0.022, 0.022), 0.018, 0.088)
        ridge.append((x, hgt, w))
        x += w * rnd.uniform(0.75, 1.15)

    # ---- 远景楼群：被空气洗淡的一层 -----------------------------------
    far = []
    x = -0.04
    while x < 1.04:
        w = rnd.uniform(0.014, 0.042)
        hh = rnd.uniform(0.020, 0.070)
        if rnd.random() < 0.14:
            hh *= rnd.uniform(1.3, 1.9)
        far.append(Building(
            x=x, w=w, h=hh, kind="far",
            pal=_pick_pal(rnd, 0.0),
            tone=rnd.uniform(0.9, 1.08),
            seed=rnd.random(),
            volumes=((0.0, 1.0, 1.0),),
            win_w=3.4, win_h=4.6,
        ))
        x += w * rnd.uniform(0.9, 1.5)

    # ---- 近景建筑 -----------------------------------------------------
    near: list[Building] = []
    lamps: list[float] = []
    x = -0.035
    while x < 1.04:
        b = _make_building(rnd, _pick_kind(rnd), x)
        near.append(b)
        x += b.w + rnd.uniform(-0.004, 0.014)
        if rnd.random() < 0.10:            # 偶尔留一道空档，给路灯与远处天光
            lamps.append(clamp(x - 0.006 + rnd.uniform(-0.01, 0.01), 0.0, 1.0))
            x += rnd.uniform(0.012, 0.03)
    for _ in range(4):
        lamps.append(round(rnd.uniform(0.02, 0.98), 3))
    lamps.sort()

    return Layers(ridge=ridge, far=far, near=near, lamps=lamps)


def _pick_kind(rnd) -> str:
    r = rnd.random()
    if r < 0.13:
        return "tower"
    if r < 0.38:
        return "office"
    if r < 0.60:
        return "block"
    if r < 0.80:
        return "house"
    if r < 0.90:
        return "shop"
    return "factory"


def _make_building(rnd, kind: str, x: float) -> Building:
    shift = rnd.uniform(-0.6, 0.6)
    pal = _pick_pal(rnd, shift)
    seed = rnd.random()
    tone = rnd.uniform(0.90, 1.10)

    if kind == "tower":
        w = rnd.uniform(0.017, 0.036)
        h = rnd.uniform(0.085, 0.185)
        steps = rnd.randint(1, 3)
        vols = [(0.0, 1.0, 1.0 - 0.10 * (steps + 1))]
        for i in range(steps):
            inset = 0.10 + 0.11 * i
            vols.append((inset, 1.0 - inset, 1.0 - 0.10 * (steps - i)))
        return Building(
            x=x, w=w, h=h, kind=kind, pal=pal, tone=tone, seed=seed,
            volumes=tuple(vols),
            antenna=rnd.uniform(0.03, 0.09) if rnd.random() < 0.55 else 0.0,
            bulkhead=rnd.random() < 0.4,
            glass=rnd.random() < 0.5,
            win_w=3.4, win_h=3.6, lit=rnd.random() < 0.9,
        )
    if kind == "office":
        w = rnd.uniform(0.028, 0.068)
        h = rnd.uniform(0.045, 0.098)
        vols = [(0.0, 1.0, 1.0)]
        if rnd.random() < 0.45:
            vols.append((0.16, 0.84, 1.0 + rnd.uniform(0.08, 0.20)))
        if rnd.random() < 0.30:
            vols.append((0.34, 0.66, 1.0 + rnd.uniform(0.24, 0.42)))
        return Building(
            x=x, w=w, h=h, kind=kind, pal=pal, tone=tone, seed=seed,
            volumes=tuple(vols),
            antenna=rnd.uniform(0.05, 0.10) if rnd.random() < 0.30 else 0.0,
            bulkhead=rnd.random() < 0.5,
            ac=rnd.randint(0, 3),
            glass=rnd.random() < 0.35,
            win_w=3.0, win_h=3.8, lit=rnd.random() < 0.85,
        )
    if kind == "block":
        w = rnd.uniform(0.038, 0.088)
        h = rnd.uniform(0.028, 0.062)
        vols = [(0.0, 1.0, 1.0)]
        if rnd.random() < 0.35:
            vols.append((0.12, 0.88, 1.0 + rnd.uniform(0.06, 0.14)))
        return Building(
            x=x, w=w, h=h, kind=kind, pal=pal, tone=tone, seed=seed,
            volumes=tuple(vols),
            balcony=rnd.randint(0, 2),
            tank=rnd.random() < 0.35,
            bulkhead=rnd.random() < 0.45,
            ac=rnd.randint(0, 2),
            win_w=3.6, win_h=4.6, lit=rnd.random() < 0.8,
        )
    if kind == "house":
        w = rnd.uniform(0.020, 0.042)
        h = rnd.uniform(0.014, 0.030)
        return Building(
            x=x, w=w, h=h, kind=kind, pal=pal, tone=tone, seed=seed,
            volumes=((0.0, 1.0, 0.74),), gable=True,
            chimney=rnd.uniform(0.16, 0.34) if rnd.random() < 0.5 else 0.0,
            win_w=3.0, win_h=3.4, lit=rnd.random() < 0.85,
        )
    if kind == "shop":
        w = rnd.uniform(0.026, 0.050)
        h = rnd.uniform(0.016, 0.030)
        return Building(
            x=x, w=w, h=h, kind=kind, pal=pal, tone=tone, seed=seed,
            volumes=((0.0, 1.0, 0.80),), awning=rnd.random() < 0.7,
            win_w=3.4, win_h=4.0, lit=True,
        )
    w = rnd.uniform(0.045, 0.085)                     # factory
    h = rnd.uniform(0.020, 0.038)
    return Building(
        x=x, w=w, h=h, kind=kind, pal=pal, tone=tone, seed=seed,
        volumes=((0.0, 1.0, 0.78),), sawtooth=3,
        chimney=rnd.uniform(0.30, 0.75), ac=rnd.randint(0, 1),
        win_w=4.4, win_h=2.6, lit=rnd.random() < 0.6,
    )


# --------------------------------------------------------------------------
# 颜色
# --------------------------------------------------------------------------

def _sky_mix(light: Light, t: float) -> tuple:
    return mix_rgb(_norm(light.zenith), _norm(light.horizon), t)


def _dim(col255, k: float) -> tuple:
    """把 0-255 的天色按亮度缩放成 0-1 的固有色——直接拿天空色去混，
    线性光空间里会被一比一抬起来，于是"黄昏的楼"会变成一块发光的橘。"""
    return tuple(c * k / 255.0 for c in col255)


def wall_color(b: Building, light: Light) -> tuple:
    """这栋楼的墙面颜色：白天是它自己的颜色，夜里退到只剩一点城市光。"""
    amb = clamp(light.ambient, 0.0, 1.0)
    sky = _sky_mix(light, 0.55)
    # 白天：墙面的固有色，被天光照到多少就亮多少
    day_c = mix_rgb(b.pal, sky, 0.24)
    day_wall = shade(day_c, b.tone * (0.10 + 0.34 * amb ** 1.7
                                      + 0.78 * light.front_lit))
    # 夜里：不是纯黑——城市光晕、天光与一点月光
    night_wall = mix_rgb((0.035, 0.040, 0.056), _dim(light.glow, 0.30), 0.55)
    night_wall = mix_rgb(night_wall, (0.24, 0.29, 0.40), 0.30 * light.moon)
    night_wall = shade(night_wall, b.tone)
    # 白天／夜里之间按太阳高度过渡：太阳落到地平线下，楼就退回剪影
    t = clamp((light.sun_alt + 2.0) / 20.0, 0.0, 1.0) ** 0.75
    base = mix_rgb(night_wall, day_wall, t)
    # 背光的时候，天光会把背光面洗成天空的颜色
    base = mix_rgb(base, _dim(light.horizon, 0.42), 0.10 * (1.0 - light.front_lit))
    base = mix_rgb(base, light.warm, 0.22 * light.front_lit)
    return tuple(clamp(c, 0.0, 1.0) for c in base)


def _lit_window_colors(light: Light):
    """夜里窗灯的颜色：暖为主，偶尔一盏冷白。"""
    warm = mix_rgb((0.98, 0.76, 0.50), _dim(light.horizon, 0.5), 0.18)
    cool = mix_rgb((0.72, 0.84, 1.0), _dim(light.horizon, 0.5), 0.16)
    return warm, cool


def _far_color(light: Light) -> tuple:
    """远景楼群：被空气洗淡，永远比近景亮一点。"""
    sky = _sky_mix(light, 0.72)
    night_c = mix_rgb((0.050, 0.056, 0.078), _dim(light.glow, 0.32), 0.55)
    day_c = shade(mix_rgb((0.46, 0.49, 0.55), sky, 0.46), 0.86)
    t = clamp((light.sun_alt + 4.0) / 16.0, 0.0, 1.0)
    base = mix_rgb(night_c, day_c, t)
    return tuple(clamp(c, 0.0, 1.0) for c in base)
