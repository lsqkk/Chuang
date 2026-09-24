"""地平线上的那片城市：远山、远景楼群、近景屋顶。

1.1.4 之前这里是"一排深浅不一的矩形"：所有楼长得一模一样、白天是一块块没有
受光面的色块、黄昏一律压成纯黑的剪影，也看不见影子。现在把城市拆成三层——

  · 远山      空气糊掉的山脊，只有一条起伏的轮廓；
  · 远景楼群  更远的一圈楼，颜色被天空洗淡，夜里只有零星几盏灯；
  · 近景建筑  有屋顶构件（水箱、楼梯间、天线、烟囱、坡屋顶）、有窗格、
               有受光面与背光面，彼此之间还会互相遮挡。

形状由"城市名 + 经纬度"做种子生成，所以同一座城市永远长成同一片屋顶；颜色与
光影全部由太阳（夜里是月亮）的高度角与方位角、直射光与环境光算出来——太阳偏
左，受光面就在左边、影子就往右倒；太阳低，影子就长；云厚，影子就淡。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

import cairo

from .palette import mix_rgb, shade

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


# --------------------------------------------------------------------------
# 绘制
# --------------------------------------------------------------------------

def draw(cr, w: float, h: float, layers: Layers, light: Light,
         horizon_y: float) -> None:
    """把整片城市画到地平线上（不含雾，雾由调用方叠在最上面）。"""
    _city_glow(cr, w, h, light, horizon_y)
    _draw_ridge(cr, w, h, layers.ridge, light, horizon_y)
    _draw_distant(cr, w, h, layers.far, light, horizon_y)
    _draw_near(cr, w, h, layers.near, light, horizon_y)


def _city_glow(cr, w, h, light: Light, horizon_y: float) -> None:
    """夜里的城市光晕：从楼后漫上来的暖色。"""
    night = light.night
    if night <= 0.05:
        return
    gh = h * 0.24
    g = _grad(0, horizon_y - gh, 0, horizon_y + h * 0.012)
    warm = (255, 176, 116)
    g.add_color_stop_rgba(0, warm[0] / 255, warm[1] / 255, warm[2] / 255, 0.0)
    g.add_color_stop_rgba(0.70, warm[0] / 255, warm[1] / 255, warm[2] / 255,
                          0.15 * night)
    g.add_color_stop_rgba(1, warm[0] / 255, warm[1] / 255, warm[2] / 255,
                          0.30 * night)
    cr.set_source(g)
    cr.rectangle(0, horizon_y - gh, w, gh + h * 0.014)
    cr.fill()


def _draw_ridge(cr, w, h, ridge, light: Light, horizon_y: float) -> None:
    if not ridge:
        return
    # 远山：比天空暗一档、再向天空洗淡一点——一条安静的剪影，
    # 不能比天空亮，否则会变成一道横在楼后的土黄土包。
    col = mix_rgb(_far_color(light), _dim(light.horizon, 0.55), 0.55)
    cr.set_source_rgb(*col)
    cr.new_path()
    cr.move_to(-0.06 * w, horizon_y + h * 0.012)
    pts = [(x * w, horizon_y - hv * h * 0.85) for x, hv, _ in ridge]
    cr.line_to(*pts[0])
    for i in range(1, len(pts) - 1):
        cx, cy = pts[i]
        mx = (cx + pts[i + 1][0]) / 2
        my = (cy + pts[i + 1][1]) / 2
        cr.curve_to(cx, cy, cx, cy, mx, my)
    cr.line_to(*pts[-1])
    cr.line_to(w * 1.06, horizon_y + h * 0.012)
    cr.close_path()
    cr.fill()


def _draw_distant(cr, w, h, far, light: Light, horizon_y: float) -> None:
    if not far:
        return
    col = shade(mix_rgb(_far_color(light), _norm(light.horizon), 0.18), 0.92)
    for b in far:
        bx = b.x * w
        bw = max(2.0, b.w * w)
        bh = max(2.0, b.h * h)
        by = horizon_y - bh + h * 0.006
        cr.set_source_rgb(*col)
        cr.rectangle(bx, by, bw, bh + h * 0.02)
        cr.fill()
        if light.rake > 0.03:
            _side_strip(cr, bx, by, bw, bh, light, light.warm, 0.35 * light.rake, 0.35)
        if light.lit_frac > 0.02 and b.lit:
            _distant_windows(cr, bx, by, bw, bh, b, light)


def _distant_windows(cr, bx, by, bw, bh, b: Building, light: Light) -> None:
    cols = int(clamp(bw / (b.win_w * 1.9), 1, 8))
    rows = int(clamp(bh / (b.win_h * 2.1), 1, 9))
    cw = bw / (cols + 0.6)
    ch = bh / (rows + 0.6)
    if cw < 0.9 or ch < 0.9:
        return
    warm, cool = _lit_window_colors(light)
    for gy in range(rows):
        for gx in range(cols):
            hv = _hash01(b.seed * 977, gx * 13 + 1, gy * 29 + 3)
            if hv > light.lit_frac * 1.1:
                continue
            a = 0.30 + 0.45 * hv
            col = cool if hv > 0.86 else warm
            cr.set_source_rgba(col[0], col[1], col[2], a * 0.85)
            cr.rectangle(bx + cw * (0.5 + gx), by + ch * (0.5 + gy),
                         max(0.8, cw * 0.45), max(0.8, ch * 0.42))
            cr.fill()


def _side_strip(cr, bx, by, bw, bh, light: Light, warm, strength, width=0.26):
    """受光的那一侧：太阳偏左，左边就有一条被照亮的窄面。"""
    if strength <= 0.004:
        return
    sw = bw * width
    if light.sun_side < 0:
        g = _grad(bx, 0, bx + sw, 0)
        g.add_color_stop_rgba(0, warm[0], warm[1], warm[2], strength)
        g.add_color_stop_rgba(1, warm[0], warm[1], warm[2], 0.0)
        x0 = bx
    else:
        g = _grad(bx + bw - sw, 0, bx + bw, 0)
        g.add_color_stop_rgba(0, warm[0], warm[1], warm[2], 0.0)
        g.add_color_stop_rgba(1, warm[0], warm[1], warm[2], strength)
        x0 = bx + bw - sw
    cr.set_source(g)
    cr.rectangle(x0, by, sw, bh)
    cr.fill()


def _draw_near(cr, w, h, near, light: Light, horizon_y: float) -> None:
    base_y = horizon_y + h * 0.012
    shapes = []
    for b in near:
        bx = b.x * w
        bw = max(2.5, b.w * w)
        bh = max(2.5, b.h * h)
        col = wall_color(b, light)
        shapes.append((b, bx, bw, bh, col))
        _draw_one(cr, b, bx, bw, bh, base_y, col, light)
    if light.rake > 0.05:            # 楼与楼之间互相投影
        _mutual_shadows(cr, w, h, shapes, base_y, light)


def _volume_path(cr, b: Building, bx, bw, bh, base_y) -> None:
    """一栋楼的轮廓：底座 + 逐层收进 + 坡屋顶 / 锯齿屋顶。"""
    cr.new_path()
    for x0, x1, top in b.volumes:
        vx = bx + x0 * bw
        vw = (x1 - x0) * bw
        cr.rectangle(vx, base_y - top * bh, vw, top * bh)
    x0, x1, top = b.volumes[-1]
    vx = bx + x0 * bw
    vw = (x1 - x0) * bw
    vy = base_y - top * bh
    if b.gable:
        over = vw * 0.08
        cr.move_to(vx - over, vy)
        cr.line_to(vx + vw / 2, base_y - bh * (top + 0.30))
        cr.line_to(vx + vw + over, vy)
        cr.close_path()
    if b.sawtooth:
        n = b.sawtooth
        step = vw / n
        for i in range(n):
            cr.move_to(vx + i * step, vy)
            cr.line_to(vx + i * step + step * 0.70, vy - bh * 0.13)
            cr.line_to(vx + (i + 1) * step, vy)
            cr.close_path()


def _draw_one(cr, b: Building, bx, bw, bh, base_y, col, light: Light) -> None:
    _volume_path(cr, b, bx, bw, bh, base_y)
    roof_up = bh * (0.30 if b.gable else 0.13 if b.sawtooth else 0.0)
    top_y = base_y - bh * b.top - roof_up
    g = _grad(0, top_y, 0, base_y)
    hi = shade(col, 1.18 + 0.12 * clamp(light.ambient, 0, 1))
    lo = shade(col, 0.62)
    g.add_color_stop_rgb(0, *[clamp(c, 0, 1) for c in hi])
    g.add_color_stop_rgb(0.60, *col)
    g.add_color_stop_rgb(1, *[clamp(c, 0, 1) for c in lo])
    cr.set_source(g)
    cr.fill_preserve()
    cr.save()
    cr.clip()
    _facade(cr, b, bx, bw, bh, base_y, col, light)
    cr.restore()
    _roof_gear(cr, b, bx, bw, bh, base_y, col, light)


def _facade(cr, b: Building, bx, bw, bh, base_y, col, light: Light) -> None:
    """墙面上的东西：玻璃幕墙 / 窗格 / 雨棚 / 受光面 / 月光。"""
    if b.glass and bh > 26:
        _glass_wall(cr, b, bx, bw, bh, base_y, col, light)
    else:
        _windows(cr, b, bx, bw, bh, base_y, col, light)

    if b.awning and bh > 16:
        aw_h = max(1.8, bh * 0.15)
        aw_y = base_y - bh * 0.46
        col_a = shade(mix_rgb(b.pal, (0.86, 0.34, 0.32), 0.35),
                      0.55 + 0.45 * clamp(light.ambient, 0, 1))
        cr.set_source_rgb(*col_a)
        cr.rectangle(bx + bw * 0.03, aw_y, bw * 0.94, aw_h)
        cr.fill()
        cr.set_source_rgba(0, 0, 0, 0.28)
        cr.rectangle(bx + bw * 0.03, aw_y + aw_h, bw * 0.94, max(0.8, aw_h * 0.5))
        cr.fill()

    if b.balcony and bh > 22:
        rows = max(1, b.balcony + 1)
        for i in range(rows):
            byy = base_y - bh * (0.34 + 0.26 * i)
            if byy < base_y - bh * 0.88:
                break
            cr.set_source_rgba(1, 1, 1, 0.10 + 0.10 * clamp(light.ambient, 0, 1))
            cr.rectangle(bx + bw * 0.06, byy, bw * 0.88, max(0.9, bh * 0.03))
            cr.fill()
            cr.set_source_rgba(0, 0, 0, 0.22)
            cr.rectangle(bx + bw * 0.06, byy + max(0.9, bh * 0.03),
                         bw * 0.88, max(0.9, bh * 0.02))
            cr.fill()

    if light.rake > 0.03:
        _side_strip(cr, bx, base_y - bh, bw, bh, light, light.warm,
                    0.46 * light.rake, 0.22 if bh > 30 else 0.32)
    if light.rake > 0.05 and bh > 10:
        # 太阳压得很低时，受光的那一条边会亮成一条线
        side = -1.0 if light.sun_side < 0 else 1.0
        ew = max(1.0, bw * 0.022)
        ex = bx if side < 0 else bx + bw - ew
        a = 0.55 * light.rake * (1.0 - 0.5 * clamp(light.ambient, 0, 1))
        cr.set_source_rgba(light.warm[0], light.warm[1], light.warm[2], a)
        cr.rectangle(ex, base_y - bh, ew, bh)
        cr.fill()
    if light.front_lit > 0.05:
        g = _grad(0, base_y - bh, 0, base_y)
        wc = light.warm
        a = 0.16 * light.front_lit
        g.add_color_stop_rgba(0, wc[0], wc[1], wc[2], a)
        g.add_color_stop_rgba(1, wc[0], wc[1], wc[2], a * 0.25)
        cr.set_source(g)
        cr.rectangle(bx, base_y - bh, bw, bh)
        cr.fill()
    if light.moon > 0.03 and abs(light.moon_rel_az) > 2.5:
        side = -1 if light.moon_rel_az < 0 else 1
        sw = bw * 0.22
        x0 = bx if side < 0 else bx + bw - sw
        g = _grad(x0, 0, x0 + sw, 0)
        a = 0.10 * light.moon
        g.add_color_stop_rgba(0 if side < 0 else 1, 0.72, 0.80, 1.0, a)
        g.add_color_stop_rgba(1 if side < 0 else 0, 0.72, 0.80, 1.0, 0.0)
        cr.set_source(g)
        cr.rectangle(x0, base_y - bh, sw, bh)
        cr.fill()
    # 楼根压在街上的那一条暗（环境光遮蔽），让楼"站"在地面上
    ao = _grad(0, base_y - bh * 0.16, 0, base_y + bh * 0.02)
    ao.add_color_stop_rgba(0, 0, 0, 0, 0)
    ao.add_color_stop_rgba(1, 0, 0, 0, 0.30)
    cr.set_source(ao)
    cr.rectangle(bx, base_y - bh * 0.16, bw, bh * 0.18)
    cr.fill()


def _glass_wall(cr, b: Building, bx, bw, bh, base_y, col, light: Light) -> None:
    """玻璃幕墙：从上到下映着天空，带竖向分格与一条斜高光。"""
    amb = clamp(light.ambient, 0, 1)
    top_y = base_y - bh
    c_top = mix_rgb(_sky_mix(light, 0.30), (1.0, 1.0, 1.0), 0.10 * amb)
    c_bot = mix_rgb(shade(col, 0.55), _norm(light.horizon), 0.22)
    g = _grad(0, top_y - bh * 0.20, 0, base_y)
    g.add_color_stop_rgb(0, *[clamp(c, 0, 1) for c in c_top])
    g.add_color_stop_rgb(0.55, *[clamp(c, 0, 1) for c in mix_rgb(c_top, c_bot, 0.6)])
    g.add_color_stop_rgb(1, *[clamp(c, 0, 1) for c in c_bot])
    cr.set_source(g)
    cr.rectangle(bx, top_y - bh * 0.2, bw, bh * 1.2)
    cr.fill()
    n = int(bw / max(2.6, b.win_w * 1.15))
    if n >= 2:
        cr.set_source_rgba(0, 0, 0, 0.16 + 0.10 * light.night)
        step = bw / n
        for i in range(1, n):
            cr.rectangle(bx + i * step - 0.5, top_y - bh * 0.2, 1.0, bh * 1.2)
        cr.fill()
    m = int(bh / max(4.0, b.win_h * 1.5))
    if m >= 2:
        cr.set_source_rgba(0, 0, 0, 0.10 + 0.08 * light.night)
        step = bh / m
        for i in range(1, m):
            cr.rectangle(bx, base_y - i * step - 0.5, bw, 1.0)
        cr.fill()
    if amb > 0.30:
        cr.save()
        cr.translate(bx, base_y)
        cr.rotate(-0.5 if light.sun_side >= 0 else 0.5)
        sg = _grad(0, 0, 0, -bh * 0.75)
        sg.add_color_stop_rgba(0, 1, 1, 1, 0)
        sg.add_color_stop_rgba(0.45, 1, 1, 1, 0.11 * amb)
        sg.add_color_stop_rgba(1, 1, 1, 1, 0)
        cr.set_source(sg)
        cr.rectangle(-bw, -bh * 1.6, bw * 3, bh * 1.6)
        cr.fill()
        cr.restore()


def _windows(cr, b: Building, bx, bw, bh, base_y, col, light: Light) -> None:
    """一排排窗：白天是映着天的深色玻璃，夜里是一部分亮着的灯。"""
    if bh < 9 or bw < 5:
        return
    pad_x = bw * (0.10 if b.kind != "house" else 0.18)
    pad_top = bh * 0.12
    pad_bot = bh * (0.10 if b.kind != "house" else 0.16)
    cols = int(clamp((bw - 2 * pad_x) / max(2.0, b.win_w * 1.55), 1, 30))
    rows = int(clamp((bh - pad_top - pad_bot) / max(2.4, b.win_h * 1.62), 1, 26))
    cw = (bw - 2 * pad_x) / cols
    ch = (bh - pad_top - pad_bot) / rows
    ww = cw * 0.58
    wh = ch * 0.52
    if ww < 0.85 or wh < 0.85:
        return

    day_glass = mix_rgb(_norm(light.zenith), (0.06, 0.07, 0.10), 0.62)
    day_glass = mix_rgb(day_glass, _norm(light.horizon), 0.25)
    lit_warm, lit_cool = _lit_window_colors(light)
    lit_frac = light.lit_frac
    amb = clamp(light.ambient, 0, 1)
    night = clamp(light.night, 0, 1)

    # 窗格按"样式 + 三档亮度"分组攒成一条路径再一次填充——
    # 一栋楼几十扇窗，逐扇 fill 会把每帧的开销推到十几毫秒。
    glass_day = shade(mix_rgb(day_glass, shade(col, 1.35), 0.22 * (1 - night)), 0.82)
    blind_day = mix_rgb(glass_day, (0.86, 0.84, 0.78), 0.34)
    rects: dict[tuple, list] = {}
    for gy in range(rows):
        wy = base_y - pad_bot - ch * (gy + 1) + (ch - wh) / 2
        for gx in range(cols):
            wx = bx + pad_x + cw * gx + (cw - ww) / 2
            hv = _hash01(b.seed * 7919, gx * 17 + 5, gy * 31 + 7)
            bucket = int(_hash01(b.seed * 331, gx * 7 + 1, gy * 11 + 2) * 3) % 3
            if b.lit and hv < lit_frac:
                kind = "litcool" if hv > 0.88 else "lit"
                rects.setdefault((kind, bucket), []).append((wx, wy, ww, wh))
                rects.setdefault(("glow", bucket), []).append(
                    (wx - ww * 0.35, wy - wh * 0.30, ww * 1.7, wh * 1.6))
            else:
                kind = "blind" if (hv > 0.72 and night < 0.5) else "glass"
                rects.setdefault((kind, bucket), []).append((wx, wy, ww, wh))

    for (kind, bucket), group in rects.items():
        cr.new_path()
        for r in group:
            cr.rectangle(*r)
        if kind in ("glass", "blind"):
            colr = glass_day if kind == "glass" else blind_day
            a = (0.62 + 0.30 * amb) * (0.72, 0.86, 1.0)[bucket]
        else:
            a = (0.62, 0.80, 0.96)[bucket]
            if kind == "glow":
                a *= 0.14
            colr = lit_cool if kind == "litcool" else lit_warm
        a = clamp(a, 0.0, 1.0)
        cr.set_source_rgba(colr[0], colr[1], colr[2], a)
        cr.fill()


def _roof_gear(cr, b: Building, bx, bw, bh, base_y, col, light: Light) -> None:
    """屋顶上的零碎：栏杆线、水箱、楼梯间、空调、天线、烟囱。"""
    x0, x1, top = b.volumes[-1]
    vx = bx + x0 * bw
    vw = (x1 - x0) * bw
    vy = base_y - top * bh
    amb = clamp(light.ambient, 0, 1)
    dark = shade(col, 0.62)
    light_edge = shade(col, 1.22 + 0.10 * amb)

    if not b.gable and not b.sawtooth:
        # 女儿墙：顶上一条亮线 + 下面一点暗，楼顶就有了厚度
        cr.set_source_rgba(*light_edge, 0.45 + 0.35 * light.front_lit)
        cr.rectangle(vx, vy - max(1.0, bh * 0.025), vw, max(1.0, bh * 0.025))
        cr.fill()
        cr.set_source_rgba(0, 0, 0, 0.22)
        cr.rectangle(vx, vy, vw, max(0.8, bh * 0.014))
        cr.fill()
        # 最上面一层的收进处也描一条亮线
        if len(b.volumes) > 1:
            px0, px1, ptop = b.volumes[-2]
            cr.set_source_rgba(*light_edge, 0.30)
            cr.rectangle(bx + px0 * bw, base_y - ptop * bh - max(0.8, bh * 0.02),
                         (px1 - px0) * bw, max(0.8, bh * 0.02))
            cr.fill()

    roof_w = vw
    if b.bulkhead and roof_w > 7:
        bwid = roof_w * 0.30
        bhei = max(2.2, bh * 0.10)
        cr.set_source_rgb(*dark)
        cr.rectangle(vx + roof_w * 0.12, vy - bhei, bwid, bhei)
        cr.fill()
        cr.set_source_rgba(*light_edge, 0.35)
        cr.rectangle(vx + roof_w * 0.12, vy - bhei, bwid, max(0.7, bhei * 0.18))
        cr.fill()
    if b.tank and roof_w > 8:
        tw = max(3.0, roof_w * 0.20)
        th = max(2.6, bh * 0.11)
        tx = vx + roof_w * 0.62
        cr.set_source_rgb(*shade(col, 0.78))
        cr.rectangle(tx + tw * 0.15, vy - th * 0.42, tw * 0.18, th * 0.42)
        cr.rectangle(tx + tw * 0.67, vy - th * 0.42, tw * 0.18, th * 0.42)
        cr.fill()
        cr.set_source_rgb(*shade(col, 1.05))
        cr.rectangle(tx, vy - th, tw, th * 0.62)
        cr.fill()
        cr.set_source_rgba(0, 0, 0, 0.20)
        cr.rectangle(tx, vy - th * 0.62, tw, max(0.7, th * 0.10))
        cr.fill()
    if b.ac and roof_w > 6:
        for i in range(b.ac):
            aw = max(2.0, roof_w * 0.13)
            ah = max(1.6, bh * 0.055)
            ax = vx + roof_w * (0.20 + 0.22 * i)
            if ax + aw > vx + roof_w * 0.95:
                break
            cr.set_source_rgb(*shade(col, 0.70))
            cr.rectangle(ax, vy - ah, aw, ah)
            cr.fill()
    if b.chimney and roof_w > 4:
        cw_ = max(1.6, bw * 0.10)
        ch_ = bh * b.chimney
        cx = vx + roof_w * 0.30
        cr.set_source_rgb(*shade(col, 0.66))
        cr.rectangle(cx, vy - ch_, cw_, ch_ + (bh * 0.30 if b.gable else 0.0))
        cr.fill()
    if b.antenna and roof_w > 2:
        ax = vx + roof_w * 0.5
        ah = bh * b.antenna
        cr.set_source_rgba(*shade(col, 0.72), 0.92)
        cr.rectangle(ax - 0.5, vy - ah, max(0.8, bw * 0.035), ah)
        cr.fill()
        if bh > 40:                     # 高塔上的红灯，夜里才看得见
            blink = 0.5 + 0.5 * math.sin(light.night * 17.0 + b.seed * 9.0)
            a = 0.35 + 0.6 * light.night * blink
            cr.set_source_rgba(1.0, 0.35, 0.30, a)
            cr.arc(ax, vy - ah, max(0.9, bw * 0.05), 0, TAU)
            cr.fill()


def _mutual_shadows(cr, w, h, shapes, base_y, light: Light) -> None:
    """楼与楼之间的投影：每栋楼往背光侧投出一片暗，只落在别的楼上。"""
    if not shapes:
        return
    cr.save()
    cr.new_path()
    for b, bx, bw, bh, _col in shapes:
        _volume_path(cr, b, bx, bw, bh, base_y)
    cr.clip()
    dx = -light.sun_side * (5.0 + 22.0 * light.rake) * (w / 1200.0)
    alpha = 0.26 * light.rake
    for b, bx, bw, bh, _col in shapes:
        cr.save()
        cr.translate(dx, 0)
        _volume_path(cr, b, bx, bw, bh, base_y)
        cr.set_source_rgba(0.02, 0.03, 0.05, alpha)
        cr.fill()
        cr.restore()
    cr.restore()


def ground_shadow(cr, w, h, layers: Layers, light: Light, ground_y: float,
                  ground_h: float) -> None:
    """建筑落在地面上的影子：从楼根朝观察者这一侧铺过来的一片。

    以前每栋楼画的都是一个**通到马路对面的竖直矩形**（只有横向渐变）：一排楼
    排下来，路面上就是一道一道等宽的明暗竖带——用户看到的就是这个。

    现在按真正的投影画：

    * 影子是一个**平行四边形**——楼根的宽度在远端朝背光侧斜出去（斜多少看太阳
      偏了多少），所以影子是斜的，不再是一根根竖条；
    * 铺多远按 `1/tan(太阳高度角)` **乘以那栋楼自己多高**算（高楼下影子长，
      太阳低的时候整条马路都在影子里）；
    * 越远越淡（远端是软边），根下最深。
    """
    if light.direct <= 0.03 or not layers.near:
        return
    alt = max(light.sun_alt, 1.0)
    ratio = clamp(1.0 / math.tan(math.radians(alt)), 0.0, 9.0)
    heights = sorted(max(1.0, b.h) for b in layers.near)
    typical = heights[len(heights) // 2] or 1.0
    kick = -math.sin(math.radians(light.rel_az))     # 影子往哪边斜（0 = 正对面）
    cr.save()
    cr.rectangle(0, ground_y, w, ground_h)
    cr.clip()
    for b in layers.near:
        bx = b.x * w
        bw = max(2.5, b.w * w)
        rel = clamp(b.h / typical, 0.55, 2.0)
        reach = clamp(0.34 * ratio * rel, 0.12, 1.0)          # 铺到马路多深
        depth = ground_h * reach
        skew = clamp(kick * depth * 1.6, -0.7 * ground_h, 0.7 * ground_h)
        a = 0.42 * light.direct * clamp(0.55 + 0.45 * reach, 0.4, 1.0)
        g = _grad(0, ground_y, 0, ground_y + depth)
        g.add_color_stop_rgba(0, 0.02, 0.03, 0.05, a)
        g.add_color_stop_rgba(0.55, 0.02, 0.03, 0.05, a * 0.62)
        g.add_color_stop_rgba(1, 0.02, 0.03, 0.05, a * 0.10)
        cr.set_source(g)
        cr.new_path()
        cr.move_to(bx, ground_y)
        cr.line_to(bx + bw, ground_y)
        cr.line_to(bx + bw + skew, ground_y + depth)
        cr.line_to(bx + skew, ground_y + depth)
        cr.close_path()
        cr.fill()
    cr.restore()
