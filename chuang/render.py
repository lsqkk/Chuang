"""天空绘制：全部由 Cairo 逐帧画出，没有一张位图素材。"""

from __future__ import annotations

import math
import random
import time as _time
from dataclasses import dataclass
from datetime import datetime

import cairo
import gi

gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo  # noqa: E402

from .palette import mix, mix_rgb, shade
from .scene import (Scene, compass, duration_zh, human_hint, phase_name_simple,
                    skyline_seed)
from . import city as _city
from . import street

TAU = math.pi * 2

# 垂直布局（相对窗口高度的比例）
HORIZON_Y = 0.795
GROUND_Y = 0.872
SILL_Y = 0.855
SILL_TOP = 0.871          # 窗台面：窗框下框之下的那条台面（植物站在这里）
RIBBON_Y = 0.940
# 地面：楼根那条远侧人行道 → 马路 → 近侧人行道（行人在上面走）
GROUND_TOP = 0.800
ROAD_BOTTOM = 0.842
ALT_TOP = 88.0
ALT_GROUND = -9.0


def scene_height(h: float) -> float:
    """"景色"占的高度：窗口底下留一条空给系统任务栏 / dock。

    很多 Linux 桌面把面板或 dock 放在屏幕底部（Plank、Cairo-Dock、扩展版
    Dash to Dock、KDE 面板……），窗口最大化和"贴底摆放"时，窗口最下面那一条
    会被面板压住——原来「今日天色」长卷画在 94% 高度上，正好被挡住。

    面板的高度是"多少像素"而不是"百分之几"，所以这里按**像素**倒推需要的余量：
    让长卷下沿离窗口底边约 65px（长卷本身在 94% 高度上，所以余量要比 65 小），
    景色整体按这个略矮的高度排版，最下面那条留给窗台继续铺下去。
    """
    reserve = clamp(64.0 + 24.0 - 0.06 * h, 14.0, h * 0.14)
    return h - reserve


def rgba(color, alpha=1.0):
    return (color[0] / 255.0, color[1] / 255.0, color[2] / 255.0, alpha)


def rgba01(color, alpha=1.0):
    return (color[0], color[1], color[2], alpha)


def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def lerp(a, b, t):
    return a + (b - a) * t


def x_for_az(az: float, az0: float, fov: float, w: float):
    d = ((az - az0 + 180.0) % 360.0) - 180.0
    return w * (0.5 + d / fov), d


def y_for_alt(alt: float, h: float) -> float:
    if alt >= 0:
        t = (min(alt, ALT_TOP) / ALT_TOP) ** 0.75
        return HORIZON_Y * h * (1.0 - t)
    t = min(1.0, alt / ALT_GROUND)
    return (HORIZON_Y + (GROUND_Y - HORIZON_Y) * t) * h


def font(cr, size, weights=Pango.Weight.NORMAL):
    layout = PangoCairo.create_layout(cr)
    desc = Pango.FontDescription("Noto Sans CJK SC, Source Han Sans SC, "
                                 "WenQuanYi Micro Hei, sans-serif")
    desc.set_absolute_size(size * Pango.SCALE)
    desc.set_weight(weights)
    layout.set_font_description(desc)
    return layout


_TEXT_CACHE: dict = {}


def draw_text(cr, text, x, y, size, color, alpha=1.0, weight=Pango.Weight.NORMAL,
              align="left"):
    key = (text, round(size, 1), int(weight))
    layout = _TEXT_CACHE.get(key)
    if layout is None:
        layout = font(cr, size, weight)
        layout.set_text(text, -1)
        if len(_TEXT_CACHE) > 240:
            _TEXT_CACHE.clear()
        _TEXT_CACHE[key] = layout
    tw, th = layout.get_pixel_size()
    xx = x - tw / 2 if align == "center" else (x - tw if align == "right" else x)
    cr.move_to(xx, y)
    cr.set_source_rgba(color[0] / 255, color[1] / 255, color[2] / 255, alpha)
    PangoCairo.show_layout(cr, layout)
    return tw, th


def rounded_rect(cr, x, y, w, h, r):
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def wrap_cjk(text: str, per_line: int) -> list[str]:
    """按字数折行（中文每个字都是等宽的，够用且不需要逐字测量）。"""
    if per_line <= 0:
        return [text]
    lines, cur = [], ""
    for ch in text:
        if len(cur) >= per_line:
            lines.append(cur)
            cur = ""
        cur += ch
    if cur:
        lines.append(cur)
    return lines


# --------------------------------------------------------------------------
# 矢量小图标
# --------------------------------------------------------------------------
#
# 「此刻的事实」里每一行前面那枚图标，是拿 Cairo 几笔画出来的——不引图片、
# 不引图标字体、不加依赖，和整幅画一个路子（见 DESIGN.md）。所以它跟着
# 卡片缩放不会糊，也不必往仓库里塞素材。
#
# 约定：图标画在以 (cx, cy) 为心、直径 size 的方框里；颜色是 0-255 三元组。
# moon 额外吃一个 phase（0 新月 / 0.5 满月 / 1 又回到新月），画出来的亮面
# 朝向与窗外那轮月亮一致——这里也不许"差不多就行"。

_ICON_TINTS = {
    "sun": (255, 216, 146),
    "sunrise": (255, 200, 136),
    "sunset": (255, 176, 126),
    "moon": (232, 235, 246),
    "cloud": (198, 210, 232),
    "rain": (150, 192, 242),
    "snow": (206, 228, 250),
    "fog": (198, 208, 224),
    "wind": (176, 208, 236),
    "info": (198, 210, 232),
}


def icon_tint(kind: str):
    return _ICON_TINTS.get(kind, (210, 218, 236))


def draw_icon(cr, kind: str, cx: float, cy: float, size: float, color, alpha: float = 1.0,
              phase: float = 0.5) -> None:
    """一枚矢量图标。认不出来的 kind 画一个小圆点兜底，绝不抛异常。"""
    r = max(2.0, size / 2.0)
    lw = max(1.0, size * 0.11)

    def paint(a: float = 1.0):
        cr.set_source_rgba(color[0] / 255.0, color[1] / 255.0, color[2] / 255.0,
                           clamp(alpha * a, 0.0, 1.0))

    def line(x1, y1, x2, y2):
        cr.move_to(x1, y1)
        cr.line_to(x2, y2)

    def stroke(a: float = 1.0, width: float | None = None):
        cr.set_line_width(width if width else lw)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        paint(a)
        cr.stroke()

    cr.save()
    if kind == "sun":
        cr.arc(cx, cy, r * 0.46, 0, TAU)
        paint()
        cr.fill()
        for i in range(8):
            a = i * TAU / 8.0
            ca, sa = math.cos(a), math.sin(a)
            line(cx + ca * r * 0.66, cy + sa * r * 0.66,
                 cx + ca * r * 0.90, cy + sa * r * 0.90)
        stroke(0.85, lw * 0.85)
    elif kind in ("sunrise", "sunset"):
        rise = kind == "sunrise"
        hy = cy + r * 0.10
        line(cx - r * 0.86, hy, cx + r * 0.86, hy)
        stroke(0.85, lw * 0.9)
        cr.arc(cx - r * 0.32, hy, r * 0.46, math.pi, TAU)      # 地平线上半个太阳
        paint()
        cr.fill()
        for deg in (65, 90, 115):                              # 三道朝上的光
            a = math.radians(deg)
            ca, sa = math.cos(a), math.sin(a)
            line(cx - r * 0.32 + ca * r * 0.62, hy - sa * r * 0.62,
                 cx - r * 0.32 + ca * r * 0.86, hy - sa * r * 0.86)
        stroke(0.7, lw * 0.75)
        ax = cx + r * 0.66                                     # 箭头：升起来还是落下去
        if rise:
            line(ax, hy - r * 0.08, ax, hy - r * 0.84)
            line(ax - r * 0.22, hy - r * 0.60, ax, hy - r * 0.84)
            line(ax + r * 0.22, hy - r * 0.60, ax, hy - r * 0.84)
        else:
            line(ax, hy - r * 0.08, ax, hy + r * 0.84)
            line(ax - r * 0.22, hy + r * 0.60, ax, hy + r * 0.84)
            line(ax + r * 0.22, hy + r * 0.60, ax, hy + r * 0.84)
        stroke(0.9, lw * 0.9)
    elif kind == "moon":
        # 亮面比例 f：0 新月 / 0.5 上下弦 / 1 满月。明暗交界线（终结线）是球面上
        # 大圆的投影，所以是一段椭圆弧，半宽 r·|cos(相位角)|；蛾眉月时它鼓向
        # 亮面那一侧，凸月时鼓向暗面那一侧。
        f = (1.0 - math.cos(TAU * clamp(phase, 0.0, 1.0))) / 2.0
        rr = r * 0.92
        b = max(abs(2.0 * f - 1.0) * rr, rr * 0.04)
        cr.translate(cx, cy)
        if phase > 0.5:                       # 下弦：亮面在左，照镜子
            cr.scale(-1.0, 1.0)
        cr.arc(0, 0, rr, 0, TAU)              # 先描一圈暗轮廓：新月也不能"看不见"
        stroke(0.30, max(1.0, lw * 0.62))
        cr.arc(0, 0, rr, -math.pi / 2, math.pi / 2)            # 右半圆：上 → 下
        cr.save()
        cr.scale(b / rr, 1.0)
        if f < 0.5:                                            # 蛾眉：鼓向右边
            cr.arc_negative(0, 0, rr, math.pi / 2, -math.pi / 2)
        else:                                                  # 凸月：鼓向左边
            cr.arc(0, 0, rr, math.pi / 2, 3 * math.pi / 2)
        cr.restore()
        cr.close_path()
        paint()
        cr.fill()
    elif kind in ("cloud", "rain", "snow"):
        top = cy - (r * 0.30 if kind != "cloud" else r * 0.10)
        for bx, by, br in ((cx - r * 0.44, top + r * 0.20, r * 0.36),
                           (cx + r * 0.02, top - r * 0.14, r * 0.48),
                           (cx + r * 0.46, top + r * 0.18, r * 0.32)):
            cr.arc(bx, by, br, 0, TAU)
        cr.rectangle(cx - r * 0.62, top + r * 0.12, r * 1.32, r * 0.34)
        paint()
        cr.fill()
        if kind == "rain":
            for dx in (-r * 0.42, 0.0, r * 0.42):
                line(cx + dx - r * 0.10, cy + r * 0.40, cx + dx + r * 0.10, cy + r * 0.86)
            stroke(0.85, lw * 0.8)
        elif kind == "snow":
            for dx in (-r * 0.42, 0.0, r * 0.42):
                cr.arc(cx + dx, cy + r * 0.66, lw * 0.7, 0, TAU)
            paint(0.85)
            cr.fill()
    elif kind == "fog":
        for dy, frac in ((-r * 0.52, 0.72), (0.0, 0.92), (r * 0.52, 0.58)):
            y = cy + dy
            x1, x2 = cx - r * frac, cx + r * frac
            cr.move_to(x1, y)
            cr.curve_to(x1 + (x2 - x1) * 0.30, y - r * 0.26,
                        x1 + (x2 - x1) * 0.70, y + r * 0.26, x2, y)
        stroke(0.9, lw * 0.85)
    elif kind == "wind":
        for dy, frac, hook in ((-r * 0.48, 0.66, True), (0.0, 0.88, False),
                               (r * 0.48, 0.46, True)):
            y = cy + dy
            x2 = cx + r * frac
            line(cx - r * 0.88, y, x2 - r * 0.20, y)
            if hook:
                cr.arc(x2 - r * 0.20, y - r * 0.20, r * 0.20, math.pi / 2, -math.pi / 2)
            else:
                line(x2 - r * 0.20, y, x2, y)
        stroke(0.9, lw * 0.85)
    elif kind == "refresh":
        rr = r * 0.66
        a1, a2 = math.radians(55), math.radians(305)
        cr.arc(cx, cy, rr, a1, a2)
        stroke(0.95, lw * 0.9)
        tipx, tipy = cx + math.cos(a2) * rr, cy + math.sin(a2) * rr
        back = a2 + math.pi / 2 + math.pi     # 顺着切线往回，画出箭头
        for s in (-1, 1):
            ang = back + s * math.radians(33)
            line(tipx, tipy, tipx + math.cos(ang) * r * 0.40,
                 tipy + math.sin(ang) * r * 0.40)
        stroke(0.95, lw * 0.9)
    elif kind.startswith("chevron"):
        d = 1.0 if kind.endswith("down") else -1.0
        if kind.endswith("right"):
            line(cx - r * 0.24, cy - r * 0.55, cx + r * 0.30, cy)
            line(cx + r * 0.30, cy, cx - r * 0.24, cy + r * 0.55)
        else:
            line(cx - r * 0.55, cy - d * r * 0.26, cx, cy + d * r * 0.30)
            line(cx, cy + d * r * 0.30, cx + r * 0.55, cy - d * r * 0.26)
        stroke(0.9)
    elif kind == "info":
        cr.arc(cx, cy, r * 0.86, 0, TAU)
        stroke(0.95, lw * 0.9)
        cr.arc(cx, cy - r * 0.40, lw * 0.58, 0, TAU)
        paint()
        cr.fill()
        line(cx, cy - r * 0.06, cx, cy + r * 0.46)
        stroke(0.95, lw * 0.9)
    elif kind == "check":
        line(cx - r * 0.55, cy + r * 0.02, cx - r * 0.12, cy + r * 0.46)
        line(cx - r * 0.12, cy + r * 0.46, cx + r * 0.58, cy - r * 0.46)
        stroke(0.95, lw)
    elif kind == "warn":
        cr.move_to(cx, cy - r * 0.88)
        cr.line_to(cx + r * 0.92, cy + r * 0.62)
        cr.line_to(cx - r * 0.92, cy + r * 0.62)
        cr.close_path()
        stroke(0.95, lw * 0.9)
        line(cx, cy - r * 0.30, cx, cy + r * 0.18)
        stroke(0.95, lw * 0.9)
        cr.arc(cx, cy + r * 0.42, lw * 0.55, 0, TAU)
        paint(0.95)
        cr.fill()
    else:
        cr.arc(cx, cy, r * 0.5, 0, TAU)
        paint(0.8)
        cr.fill()
    cr.restore()


# --------------------------------------------------------------------------
# 云的精灵图：启动时预渲染几种色调，之后每帧只做缩放绘制
# --------------------------------------------------------------------------

def _make_puff(shape_seed: int, size: int = 280) -> cairo.ImageSurface:
    """一团积云：上部蓬松、底部被压平（白色遮罩，靠 alpha 塑形）。"""
    h = int(size * 0.58)
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, h)
    cr = cairo.Context(surf)
    rnd = random.Random(shape_seed)
    cx, cy = size / 2, h * 0.66
    blobs = []
    for _ in range(6):                      # 云的主体
        blobs.append((cx + rnd.uniform(-0.30, 0.30) * size,
                      cy - rnd.uniform(0.02, 0.13) * h,
                      rnd.uniform(0.15, 0.26) * size,
                      rnd.uniform(0.55, 0.85)))
    for _ in range(8):                      # 顶上的小团，做出蓬松感
        blobs.append((cx + rnd.uniform(-0.34, 0.34) * size,
                      cy - rnd.uniform(0.12, 0.36) * h,
                      rnd.uniform(0.07, 0.16) * size,
                      rnd.uniform(0.42, 0.72)))
    for bx, by, br, a in blobs:
        grad = cairo.RadialGradient(bx, by, br * 0.05, bx, by, br)
        grad.add_color_stop_rgba(0, 1, 1, 1, a)
        grad.add_color_stop_rgba(0.48, 1, 1, 1, a * 0.82)
        grad.add_color_stop_rgba(0.80, 1, 1, 1, a * 0.32)
        grad.add_color_stop_rgba(1, 1, 1, 1, 0)
        cr.set_source(grad)
        cr.arc(bx, by, br, 0, TAU)
        cr.fill()
    # 云底压平
    grad = cairo.LinearGradient(0, h * 0.70, 0, h)
    grad.add_color_stop_rgba(0, 1, 1, 1, 0)
    grad.add_color_stop_rgba(1, 1, 1, 1, 0.95)
    cr.set_operator(cairo.OPERATOR_DEST_OUT)
    cr.set_source(grad)
    cr.paint()
    cr.set_operator(cairo.OPERATOR_OVER)
    return surf


class Sprites:
    """预渲染的云（多种光线色调）与光晕。"""

    TINTS = (
        ("day", (1.00, 1.00, 1.00), (0.86, 0.90, 0.96)),
        ("warm", (1.00, 0.86, 0.66), (0.72, 0.58, 0.52)),
        ("dusk", (0.62, 0.55, 0.66), (0.28, 0.26, 0.38)),
        ("night", (0.42, 0.45, 0.55), (0.16, 0.18, 0.26)),
    )

    def __init__(self) -> None:
        self.shapes = [_make_puff(seed) for seed in (11, 29, 47)]
        self.tinted: dict[str, list[cairo.ImageSurface]] = {}
        for name, top, bottom in self.TINTS:
            self.tinted[name] = [self._tint(s, top, bottom) for s in self.shapes]

    @staticmethod
    def _tint(mask: cairo.ImageSurface, top, bottom) -> cairo.ImageSurface:
        w, h = mask.get_width(), mask.get_height()
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(surf)
        grad = cairo.LinearGradient(0, 0, 0, h)
        grad.add_color_stop_rgb(0, *top)
        grad.add_color_stop_rgb(0.62, *[lerp(top[i], bottom[i], 0.55) for i in range(3)])
        grad.add_color_stop_rgb(1, *bottom)
        cr.set_source(grad)
        cr.mask(cairo.SurfacePattern(mask))
        return surf


# --------------------------------------------------------------------------
# 天气粒子（云、雨、雪、闪电、飞机）
# --------------------------------------------------------------------------

class WeatherFX:
    """带动画的天气效果状态。坐标全部归一化到窗口 0-1。"""

    def __init__(self, seed: int = 1, stars=None) -> None:
        self.rnd = random.Random(seed)
        self.seed = seed
        self.clouds: list[dict] = []
        self.drops: list[list[float]] = []
        self.flakes: list[list[float]] = []
        self.flash = 0.0
        self.next_flash = 0.0
        self.plane: dict | None = None
        self.next_plane = 0.0
        self.elapsed = 0.0
        self.signature = None

    def sync(self, scene: Scene, now: float) -> None:
        """场景（云量/天气/风向）变了就重建粒子。"""
        sig = (round(scene.cloud / 4), scene.code, round(scene.wind_speed),
               round(scene.wind_dir / 10), scene.precip_kind)
        if sig == self.signature:
            return
        self.signature = sig
        rnd = self.rnd
        cover = clamp(scene.cloud / 100.0, 0.0, 1.0)
        if scene.has_weather and scene.fog:
            cover = max(cover, 0.85)
        layers = [
            ("high", 0.10 + 0.60 * cover, 26, (26, 70), (0.26, 0.46), 1.9),
            ("mid", 0.80 * cover, 22, (6, 38), (0.50, 0.82), 1.0),
            ("low", 0.95 * cover, 18, (0, 13), (0.62, 1.00), 0.55),
        ]
        clouds = []
        for name, amount, n, (alo, ahi), (slo, shi), speed in layers:
            count = int(n * amount)
            for i in range(count):
                # 横向铺开，避免全挤在一起
                slot = (i + rnd.random() * 0.9) / max(1, count)
                clouds.append({
                    "layer": name,
                    "x": slot * 1.7 - 0.35,
                    "alt": rnd.uniform(alo, ahi),
                    "scale": rnd.uniform(0.60, 1.45) * (1.7 if name == "high" else 1.0),
                    "alpha": rnd.uniform(slo, shi) * (0.68 + 0.32 * cover),
                    "speed": speed * rnd.uniform(0.7, 1.3),
                    "shape": rnd.randrange(3),
                    "wobble": rnd.uniform(0, TAU),
                })
        clouds.sort(key=lambda c: (c["layer"] != "high", c["alt"]))
        self.clouds = clouds

        kind = scene.precip_kind if scene.has_weather else "none"
        strength = scene.precip_strength if scene.has_weather else 0.0
        self.drops = []
        self.flakes = []
        if kind == "rain" and strength > 0:
            n = int(70 + 320 * strength)
            for _ in range(n):
                self.drops.append([
                    rnd.random(), rnd.random(),
                    rnd.uniform(0.06, 0.16) * (0.6 + strength),   # 长度
                    rnd.uniform(0.55, 1.15) * (0.75 + strength),  # 速度
                    rnd.uniform(0.25, 0.75),                      # 透明度
                    rnd.uniform(0.6, 2.2),                        # 粗细
                ])
        elif kind == "snow" and strength > 0:
            n = int(60 + 220 * strength)
            for _ in range(n):
                self.flakes.append([
                    rnd.random(), rnd.random(),
                    rnd.uniform(0.15, 0.34) * (0.7 + 0.5 * strength),
                    rnd.uniform(0.55, 1.25),          # 半径
                    rnd.uniform(0, TAU),              # 摆动相位
                    rnd.uniform(0.25, 0.9),
                ])
        if kind == "rain" and scene.has_weather and scene.thunder:
            self.next_flash = now + rnd.uniform(1.0, 4.0)
        self.next_plane = self.next_plane or now + rnd.uniform(40, 130)

    # -- 每帧推进 -------------------------------------------------------
    def advance(self, dt: float, scene: Scene, now: float, w: float, h: float) -> None:
        self.elapsed += dt
        # 风向 → 屏幕上的水平分量（风是"来向"，雨往反方向飘）
        wind = scene.wind_speed if scene.has_weather else 0.0
        to_dir = (scene.wind_dir + 180.0) % 360.0
        rel = math.radians(((to_dir - 180.0 + 180.0) % 360.0) - 180.0)
        drift = math.sin(rel) * wind / 22.0

        for c in self.clouds:
            c["x"] += (c["speed"] * 0.0035 * (1.0 + abs(drift)) * dt * 12.0
                       + drift * 0.0022 * dt * 12.0)
            if c["x"] > 1.35:
                c["x"] = -0.35
            elif c["x"] < -0.35:
                c["x"] = 1.35

        for d in getattr(self, "drops", []):
            d[1] += d[3] * dt * 0.55
            d[0] += drift * d[3] * dt * 0.10
            if d[1] > 1.02:
                d[1] = -0.03
                d[0] = self.rnd.random()
            if d[0] > 1.03:
                d[0] -= 1.06
            elif d[0] < -0.03:
                d[0] += 1.06

        for f in getattr(self, "flakes", []):
            f[5] += 0.02
            f[1] += f[3] * dt * 0.055
            f[0] += (math.sin(f[5] * 2.1) * 0.0016 + drift * 0.002) * dt * 12.0
            if f[1] > 1.02:
                f[1] = -0.03
                f[0] = self.rnd.random()
            if f[0] > 1.03:
                f[0] -= 1.06
            elif f[0] < -0.03:
                f[0] += 1.06

        if scene.has_weather and scene.thunder and self.drops:
            if now >= self.next_flash and self.flash <= 0.0:
                self.flash = 1.0
                self.next_flash = now + self.rnd.uniform(4.0, 13.0)
            if self.flash > 0:
                self.flash = max(0.0, self.flash - dt * 2.6)

        # 偶尔飞过的飞机
        if self.plane is None and now >= self.next_plane:
            self.plane = {
                "x": -0.06, "alt": self.rnd.uniform(26, 62),
                "speed": self.rnd.uniform(0.045, 0.085),
                "blink": self.rnd.uniform(0, 2),
                "trail": self.rnd.uniform(0.4, 0.9),
            }
        if self.plane is not None:
            self.plane["x"] += self.plane["speed"] * dt
            self.plane["blink"] += dt * 1.6
            if self.plane["x"] > 1.08:
                self.plane = None
                self.next_plane = now + self.rnd.uniform(60, 220)


# --------------------------------------------------------------------------
# 主画笔
# --------------------------------------------------------------------------

class UIState:
    """界面状态：信息面板、长卷游标、预览、提示。"""

    def __init__(self) -> None:
        self.show_info = True
        self.info_compact = False       # 信息卡精简模式（只剩时间与那句话）
        self.info_rows: list = []       # 这一帧画出来的"事实"（FactRow 列表）
        self.info_rects: list = []      # 信息卡上可点的方块 (x, y, w, h, kind, when)
        self.info_hover = -1            # 鼠标停在哪一块上（-1 = 没有）
        self.info_hover_dt = None       # 悬停在"日弧"上时指到的时刻
        self.show_ribbon = True
        self.ribbon: list[tuple] = []
        self.ribbon_info: list[str] = []   # 长卷每一格的那句天气（悬停时显示）
        self.ribbon_key = None
        self.ribbon_surface: cairo.ImageSurface | None = None
        self.ribbon_rect = (0.0, 0.0, 0.0, 0.0)
        self.preview_dt = None          # 正在预览的时刻
        self.preview_started = None     # 进入预览那一刻的墙钟（让人车在预览里也继续走）
        self.hover_dt = None
        self.dragging = False
        self.chip_rect = (0.0, 0.0, 0.0, 0.0)
        self.toast = ""
        self.toast_icon = "info"        # 提示条左边那枚小图标
        self.toast_until = 0.0
        self.toast_rect = (0.0, 0.0, 0.0, 0.0)
        self.toast_detail = ""          # 非空时：点提示条可以看/复制完整内容
        self.hint_shown = False


@dataclass
class FactRow:
    """「此刻的事实」里的一行：图标 + 标题 + 主值 + 副值 + 点它做什么。

    action 有三种：""（只是看看）、"open"（跳到 when 那一刻去预览）、
    "detail"（摊开这条背后的完整数据）。窗口那边照 action 决定点下去干什么，
    画的地方只管把方块记进 ui.info_rects。
    """

    icon: str
    label: str
    value: str
    note: str = ""
    action: str = ""
    when: datetime | None = None


class CacheSlot:
    """一张"按参数缓存"的离屏图：key + surface。

    这个文件里有 5 张这样的图（天空底色、云、城市、暗角、窗台）。以前每处都
    自己写一遍

        if self._x_surf is None or self._x_key != key or 尺寸对不上:

    ——而"手工拼 key"正是这里最容易出错的地方：漏一个参数，就会出现"换了城市/
    换了天气，画面还是上一张"。1.1.8 修的那次（城市种子没进 key）就是这条路
    的产物。把判断收进这个二十行的小类之后：**要加参数就往 key 里加**，
    判断逻辑只有一处，换地方也不用再抄一遍。
    """

    __slots__ = ("key", "surf")

    def __init__(self) -> None:
        self.key = None
        self.surf: cairo.ImageSurface | None = None

    def stale(self, key, w: int, h: int) -> bool:
        """该重画了吗？（没画过 / key 变了 / 尺寸变了）"""
        return (self.surf is None or self.key != key
                or self.surf.get_width() != int(w)
                or self.surf.get_height() != int(h))

    def store(self, key, surf: cairo.ImageSurface) -> cairo.ImageSurface:
        self.key, self.surf = key, surf
        return surf

    def invalidate(self) -> None:
        self.key, self.surf = None, None


class SkyPainter:
    """把一帧场景画到 Cairo 上。"""

    def __init__(self, seed: int = 1) -> None:
        self.sprites = Sprites()
        self.fx = WeatherFX(seed)
        self.ui = UIState()
        self._skyline: dict[int, tuple] = {}
        self._last = _time.time()
        self._lit_phase = 0.0
        # 离屏缓存：云层、天空底色、城市、暗角与玻璃反光、窗台
        self._sky = CacheSlot()
        self._cloud = CacheSlot()
        self._city = CacheSlot()
        self._overlay = CacheSlot()
        self._sill = CacheSlot()
        self._info = CacheSlot()            # 信息卡整张的离屏图（见 _draw_info）
        self._info_rects: list = []         # 卡上能点的方块，随那张图一起缓存
        self._layout_cache: dict = {}
        self._street_roster: list | None = None
        self._street_trees: list | None = None
        # 由窗口注入"现在几点"的回调，让街上的人车按真实时间连续移动
        self.clock = None

    # ------------------------------------------------------------------
    # 顶层
    # ------------------------------------------------------------------
    def draw(self, cr, w: float, h: float, scene: Scene, az0: float,
             chrome: bool = True) -> None:
        """chrome=False 时不画信息卡与今日天色长卷——壁纸只用景色本身。"""
        now = _time.time()
        dt = clamp(now - self._last, 0.0, 0.2)
        self._last = now
        self._lit_phase = now

        fov = self._fov(w, h, scene)
        self.fx.sync(scene, now)
        self.fx.advance(dt, scene, now, w, h)
        # 景色按 hs 排版，底下的余量留给系统面板（见 scene_height）
        hs = scene_height(h)

        sun_x, sun_d = x_for_az(scene.sun_az, az0, fov, w)
        glow_x = clamp(sun_x, -0.35 * w, 1.35 * w)
        sun_y = y_for_alt(max(scene.sun_alt, -3.0), hs)
        direct = self._direct_light(scene)

        self._draw_sky(cr, w, hs, scene, glow_x, sun_y)
        self._draw_stars(cr, w, hs, scene, az0, fov)
        self._draw_sun(cr, w, hs, scene, sun_x, sun_d, fov, direct)
        self._draw_moon(cr, w, hs, scene, az0, fov)
        self._draw_clouds(cr, w, hs, scene, az0, fov)
        self._draw_plane(cr, w, hs, scene)
        light = self._light(scene, az0, direct)
        self._draw_skyline(cr, w, hs, scene, light)
        self._draw_ground(cr, w, hs, scene, az0, fov, light)
        self._draw_street(cr, w, hs, scene, light)
        self._draw_vignette(cr, w, h, scene)
        self._draw_sill(cr, w, h, scene, az0, fov, sun_x, direct, hs)
        self._draw_precip(cr, w, h, scene)
        self._draw_flash(cr, w, h, scene)
        if chrome and self.ui.show_ribbon:
            self._draw_ribbon(cr, w, hs, scene)
        if chrome and self.ui.show_info:
            self._draw_info(cr, w, h, scene, az0, fov, direct)
        if chrome:
            self._draw_toast(cr, w, hs)

    # ------------------------------------------------------------------
    def _fov(self, w: float, h: float, scene: Scene) -> float:
        ratio = w / max(1.0, h)
        return clamp(190.0 * (ratio / 1.55), 82.0, 205.0)

    @staticmethod
    def _direct_light(scene: Scene) -> float:
        """到达窗台的直射光：太阳高度 × 云量 × 雾。"""
        l = scene.mood.sun_light
        if scene.has_weather:
            l *= 1.0 - 0.86 * clamp(scene.cloud / 100.0, 0, 1) ** 1.2
            if scene.fog:
                l *= 0.15
            if scene.precip_kind != "none":
                l *= 0.35
        return clamp(l, 0.0, 1.0)

    @staticmethod
    def _facing(lat: float) -> float:
        """北半球朝南，南半球朝北——让太阳的弧线尽量落在窗里。"""
        return 180.0 if lat >= 0 else 0.0

    @staticmethod
    def _ambient(scene: Scene) -> float:
        """到达窗台/屋顶的总环境光：太阳高度给出的环境光，再被厚云削弱。"""
        amb = scene.mood.ambient
        if scene.has_weather:
            amb *= 1.0 - 0.55 * clamp(scene.cloud / 100.0, 0, 1)
            if scene.fog:
                amb *= 0.8
        return clamp(amb, 0.0, 1.0)

    # ------------------------------------------------------------------
    # 天空
    # ------------------------------------------------------------------
    def _draw_sky(self, cr, w, h, scene: Scene, glow_x, sun_y):
        key = (int(w), int(h), round(scene.sun_alt * 3), round(scene.sun_az * 1.5),
               round((scene.cloud if scene.has_weather else 0) / 5),
               scene.fog, scene.mood.key)
        if self._sky.stale(key, w, h):
            self._sky.store(key, self._render_sky(int(w), int(h), scene, glow_x, sun_y))
        cr.set_source_surface(self._sky.surf, 0, 0)
        cr.paint()

    def _render_sky(self, w: int, h: int, scene: Scene, glow_x, sun_y):
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(surf)
        mu = scene.mood
        horizon_y = HORIZON_Y * h
        grad = cairo.LinearGradient(0, 0, 0, horizon_y)
        grad.add_color_stop_rgb(0.00, *[c / 255 for c in mu.zenith])
        z2 = mix(mu.zenith, mu.horizon, 0.12)
        grad.add_color_stop_rgb(0.44, *[c / 255 for c in z2])
        z3 = mix(mu.zenith, mu.horizon, 0.30)
        grad.add_color_stop_rgb(0.68, *[c / 255 for c in z3])
        z4 = mix(mu.zenith, mu.horizon, 0.58)
        grad.add_color_stop_rgb(0.88, *[c / 255 for c in z4])
        grad.add_color_stop_rgb(1.00, *[c / 255 for c in mu.horizon])
        cr.set_source(grad)
        cr.rectangle(0, 0, w, horizon_y + 1)
        cr.fill()

        # 地平线以下（被剪影和窗台挡住之前）先铺一层暗色
        below = cairo.LinearGradient(0, horizon_y, 0, h)
        below.add_color_stop_rgb(0, *[c / 255 for c in mu.horizon])
        dark = mix(mu.horizon, (4, 5, 10), 0.93)
        dark = tuple(c * (0.35 + 0.55 * clamp(mu.ambient, 0, 1)) for c in dark)
        below.add_color_stop_rgb(0.35, *[c / 255 for c in dark])
        below.add_color_stop_rgb(1, 0.015, 0.016, 0.025)
        cr.set_source(below)
        cr.rectangle(0, horizon_y, w, h - horizon_y)
        cr.fill()

        # 太阳一方的辉光
        strength = mu.glow_strength * (1.0 - 0.7 * clamp(scene.cloud / 100.0, 0, 1))
        if strength > 0.01:
            radius = max(w, h) * lerp(0.80, 0.42, clamp(strength, 0, 1))
            gx = glow_x
            gy = clamp(sun_y, -0.1 * h, horizon_y + 0.02 * h)
            rad = cairo.RadialGradient(gx, gy, 0.0, gx, gy, radius)
            gc = [c / 255 for c in mu.glow_color]
            rad.add_color_stop_rgba(0.00, gc[0], gc[1], gc[2], 0.46 * strength)
            rad.add_color_stop_rgba(0.24, gc[0], gc[1], gc[2], 0.22 * strength)
            rad.add_color_stop_rgba(0.58, gc[0], gc[1], gc[2], 0.07 * strength)
            rad.add_color_stop_rgba(1.00, gc[0], gc[1], gc[2], 0.0)
            cr.save()
            cr.set_operator(cairo.OPERATOR_ADD)
            cr.set_source(rad)
            cr.rectangle(0, 0, w, horizon_y + 1)
            cr.fill()
            cr.restore()

        # 贴近地平线的一层空气雾
        haze_h = h * 0.16
        haze = cairo.LinearGradient(0, horizon_y - haze_h, 0, horizon_y)
        hz = mix(mu.horizon, mu.glow_color, 0.35)
        haze.add_color_stop_rgba(0, hz[0] / 255, hz[1] / 255, hz[2] / 255, 0.0)
        haze.add_color_stop_rgba(1, hz[0] / 255, hz[1] / 255, hz[2] / 255, 0.22)
        cr.set_source(haze)
        cr.rectangle(0, horizon_y - haze_h, w, haze_h)
        cr.fill()

        if scene.has_weather and scene.fog:
            cr.set_source_rgba(*[c / 255 for c in mix(mu.horizon, (200, 205, 210), 0.5)], 0.42)
            cr.rectangle(0, 0, w, h)
            cr.fill()

        # 阴天：整片天空被云幕盖住，太阳只剩下一个隐约的亮斑
        cover = clamp(scene.cloud / 100.0, 0, 1) if scene.has_weather else 0.0
        if cover > 0.55:
            veil = (cover - 0.55) / 0.45
            if scene.sun_alt > 12:
                tone = (200, 207, 216)
            elif scene.sun_alt > 1:
                tone = (146, 136, 132)
            elif scene.sun_alt > -8:
                tone = (84, 76, 90)
            else:
                tone = (28, 32, 44)
            vg = cairo.LinearGradient(0, 0, 0, horizon_y)
            vg.add_color_stop_rgba(0, tone[0] / 255, tone[1] / 255, tone[2] / 255,
                                   0.93 * veil)
            vg.add_color_stop_rgba(0.7, tone[0] / 255, tone[1] / 255, tone[2] / 255,
                                   0.90 * veil)
            vg.add_color_stop_rgba(1, tone[0] / 255, tone[1] / 255, tone[2] / 255,
                                   0.78 * veil)
            cr.set_source(vg)
            cr.rectangle(0, 0, w, horizon_y)
            cr.fill()
        return surf

    # ------------------------------------------------------------------
    def _draw_stars(self, cr, w, h, scene: Scene, az0, fov):
        mu = scene.mood
        if mu.star_alpha <= 0.02 or not scene.stars.points:
            return
        if scene.has_weather and scene.cloud > 78:      # 厚云下星星本来也看不见
            return
        haze = 1.0 - 0.75 * clamp(scene.cloud / 100.0, 0, 1) if scene.has_weather else 1.0
        alpha_base = mu.star_alpha * haze
        if alpha_base <= 0.02:
            return
        t = self._lit_phase
        cr.save()
        for az, alt, mag, rgb, phase in scene.stars.points:
            x, d = x_for_az(az, az0, fov, w)
            if d < -fov / 2 - 2 or d > fov / 2 + 2:
                continue
            y = y_for_alt(alt, h)
            flux = 10 ** (-0.4 * mag)
            base = clamp(flux / 0.265, 0.0, 1.0) ** 0.42
            a = clamp(0.08 + 0.92 * base, 0, 1) * alpha_base
            if a <= 0.012:
                continue
            tw = 1.0 + 0.28 * math.sin(t * 2.7 + phase) * (1.0 - alt / 90.0)
            a = clamp(a * tw, 0, 1)
            r = 0.6 + 1.35 * base
            cr.set_source_rgba(rgb[0], rgb[1], rgb[2], a)
            cr.arc(x, y, r, 0, TAU)
            cr.fill()
            if base > 0.85:
                g = cairo.RadialGradient(x, y, 0, x, y, r * 4.2)
                g.add_color_stop_rgba(0, rgb[0], rgb[1], rgb[2], a * 0.34)
                g.add_color_stop_rgba(1, rgb[0], rgb[1], rgb[2], 0)
                cr.set_source(g)
                cr.arc(x, y, r * 4.2, 0, TAU)
                cr.fill()
        cr.restore()

    # ------------------------------------------------------------------
    def _draw_sun(self, cr, w, h, scene: Scene, sun_x, sun_d, fov, direct):
        if sun_d < -fov / 2 - 6 or sun_d > fov / 2 + 6:
            return
        if scene.sun_alt < -1.2:
            return
        cover = clamp(scene.cloud / 100.0, 0, 1) if scene.has_weather else 0.0
        vis = clamp(1.0 - 0.92 * cover ** 1.25, 0.05, 1.0)
        x = clamp(sun_x, -0.1 * w, 1.1 * w)
        y = y_for_alt(scene.sun_alt, h)
        r = max(6.0, w * 0.0060)
        # 高度越低，太阳越大越红
        low = clamp(1.0 - scene.sun_alt / 18.0, 0.0, 1.0)
        r *= (1.0 + 0.5 * low)
        warm = mix(scene.mood.glow_color, (255, 246, 226), 0.55 - 0.5 * low)
        cr.save()
        cr.set_operator(cairo.OPERATOR_ADD)
        halo = cairo.RadialGradient(x, y, r * 0.6, x, y, r * 9)
        halo.add_color_stop_rgba(0, warm[0] / 255, warm[1] / 255, warm[2] / 255, 0.34 * vis)
        halo.add_color_stop_rgba(0.22, warm[0] / 255, warm[1] / 255, warm[2] / 255, 0.13 * vis)
        halo.add_color_stop_rgba(1, warm[0] / 255, warm[1] / 255, warm[2] / 255, 0)
        cr.set_source(halo)
        cr.arc(x, y, r * 9, 0, TAU)
        cr.fill()
        disc = cairo.RadialGradient(x, y, 0, x, y, r)
        disc.add_color_stop_rgba(0, 1, 0.99, 0.95,
                                 (0.75 + 0.25 * min(1.0, direct + 0.4)) * vis)
        disc.add_color_stop_rgba(0.7, warm[0] / 255, warm[1] / 255, warm[2] / 255,
                                 0.95 * vis)
        disc.add_color_stop_rgba(1, warm[0] / 255, warm[1] / 255, warm[2] / 255, 0.0)
        cr.set_source(disc)
        cr.arc(x, y, r * 1.15, 0, TAU)
        cr.fill()
        cr.restore()

    # ------------------------------------------------------------------
    def _draw_moon(self, cr, w, h, scene: Scene, az0, fov):
        if scene.moon_alt < -1.0:
            return
        x, d = x_for_az(scene.moon_az, az0, fov, w)
        if d < -fov / 2 - 6 or d > fov / 2 + 6:
            return
        x = clamp(x, -0.1 * w, 1.1 * w)
        y = y_for_alt(scene.moon_alt, h)
        r = max(5.5, w * 0.0068)
        illum = scene.moon_illum
        darkness = clamp((-scene.sun_alt - 1) / 12.0, 0.0, 1.0)
        vis = clamp(0.35 + 0.65 * illum, 0, 1) * clamp((scene.moon_alt + 2) / 10.0, 0, 1)
        vis *= 1.0 - 0.6 * clamp(scene.cloud / 100.0, 0, 1) if scene.has_weather else 1.0

        cr.save()
        if vis > 0.02:
            cr.set_operator(cairo.OPERATOR_ADD)
            halo_r = r * lerp(3.0, 11.0, illum) * (0.5 + 0.5 * darkness)
            halo = cairo.RadialGradient(x, y, r * 0.8, x, y, halo_r)
            halo.add_color_stop_rgba(0, 0.86, 0.90, 1.0, 0.26 * vis)
            halo.add_color_stop_rgba(0.35, 0.80, 0.86, 1.0, 0.10 * vis)
            halo.add_color_stop_rgba(1, 0.80, 0.86, 1.0, 0)
            cr.set_source(halo)
            cr.arc(x, y, halo_r, 0, TAU)
            cr.fill()
            cr.set_operator(cairo.OPERATOR_OVER)

        # 月面：先画满月的圆，再用晨昏线切出暗面
        cr.save()
        cr.translate(x, y)
        face = cairo.RadialGradient(-r * 0.25, -r * 0.25, r * 0.1, 0, 0, r)
        face.add_color_stop_rgb(0, 1.0, 0.98, 0.92)
        face.add_color_stop_rgb(0.75, 0.94, 0.92, 0.86)
        face.add_color_stop_rgb(1, 0.82, 0.82, 0.80)
        cr.set_source(face)
        cr.arc(0, 0, r, 0, TAU)
        cr.fill()

        v = scene.moon_bright
        right = (math.sin(math.radians(az0 + 90)), math.cos(math.radians(az0 + 90)), 0.0)
        sx = v[0] * right[0] + v[1] * right[1] + v[2] * right[2]
        sy = v[2]
        ang = math.atan2(-sy, sx) if (abs(sx) + abs(sy)) > 1e-6 else 0.0
        cr.rotate(ang)
        cos_i = 2.0 * illum - 1.0
        kx = -r * cos_i          # 亮面在 +x 方向：满月时晨昏线退到 -r，新月时鼓到 +r
        K = 0.5523
        cr.new_path()
        cr.arc(0, 0, r, math.pi / 2, 3 * math.pi / 2)
        cr.curve_to(kx * K, -r, kx, -r * K, kx, 0)
        cr.curve_to(kx, r * K, kx * K, r, 0, r)
        cr.close_path()
        cr.set_source_rgba(0.045, 0.055, 0.095, 0.93)
        cr.fill()
        cr.restore()
        cr.restore()

    # ------------------------------------------------------------------
    def _cloud_tint(self, scene: Scene) -> str:
        alt = scene.sun_alt
        if alt > 12:
            return "day"
        if alt > 1:
            return "warm"
        if alt > -7:
            return "dusk"
        return "night"

    def _draw_clouds(self, cr, w, h, scene: Scene, az0, fov):
        if not self.fx.clouds:
            return
        tint = self._cloud_tint(scene)
        sprites = self.sprites.tinted[tint]
        moonlit = clamp(scene.moon_light * 0.35, 0, 0.35)
        # 云画在 1/3 分辨率的离屏层上再放大——既快，边缘也更柔
        s = 0.36
        lw = max(1, int(w * s))
        lh = max(1, int((HORIZON_Y * h + h * 0.06) * s))
        if self._cloud.stale((int(w), int(h)), lw, lh):
            self._cloud.store((int(w), int(h)),
                              cairo.ImageSurface(cairo.FORMAT_ARGB32, lw, lh))
        surf = self._cloud.surf
        lcr = cairo.Context(surf)
        lcr.set_operator(cairo.OPERATOR_SOURCE)
        lcr.set_source_rgba(0, 0, 0, 0)
        lcr.paint()
        lcr.set_operator(cairo.OPERATOR_OVER)
        lcr.scale(s, s)
        lcr.save()
        for c in self.fx.clouds:
            x = c["x"] * w
            y = y_for_alt(c["alt"], h) + math.sin(self._lit_phase * 0.06 + c["wobble"]) * 3
            scale = c["scale"] * (w / 1100.0) * 3.3
            alpha = clamp(c["alpha"], 0, 1)
            if tint == "night":
                alpha *= 0.85
            lcr.save()
            lcr.translate(x, y)
            lcr.scale(scale, scale * 0.58)
            pat = cairo.SurfacePattern(sprites[c["shape"]])
            pat.set_filter(cairo.FILTER_BILINEAR)
            lcr.set_source(pat)
            lcr.paint_with_alpha(alpha)
            lcr.restore()
            if moonlit > 0.02 and tint == "night":
                lcr.save()
                lcr.translate(x, y - 6 * scale)
                lcr.scale(scale * 0.8, scale * 0.45)
                pat = cairo.SurfacePattern(sprites[c["shape"]])
                pat.set_filter(cairo.FILTER_BILINEAR)
                lcr.set_source(pat)
                lcr.paint_with_alpha(moonlit * alpha)
                lcr.restore()
        lcr.restore()
        cr.save()
        cr.translate(0, 0)
        cr.scale(1 / s, 1 / s)
        pat = cairo.SurfacePattern(surf)
        pat.set_filter(cairo.FILTER_BILINEAR)
        cr.set_source(pat)
        cr.rectangle(0, 0, lw, lh)
        cr.fill()
        cr.restore()

    # ------------------------------------------------------------------
    def _draw_plane(self, cr, w, h, scene: Scene):
        p = self.fx.plane
        if p is None:
            return
        x, y = p["x"] * w, y_for_alt(p["alt"], h)
        night = scene.sun_alt < -2
        cr.save()
        if night:
            blink = (math.sin(p["blink"] * 3.0) + 1) * 0.5
            body_a = 0.22 + 0.2 * blink
            cr.set_source_rgba(0.85, 0.88, 0.95, body_a)
            cr.rectangle(x - 5, y - 1.0, 10, 2.0)
            cr.fill()
            if blink > 0.55:
                cr.set_source_rgba(1.0, 0.42, 0.32, 0.55 * blink)
                cr.arc(x + 6, y - 3.2, 1.6, 0, TAU)
                cr.fill()
                g = cairo.RadialGradient(x + 6, y - 3.2, 0, x + 6, y - 3.2, 7)
                g.add_color_stop_rgba(0, 1.0, 0.45, 0.35, 0.35 * blink)
                g.add_color_stop_rgba(1, 1.0, 0.45, 0.35, 0)
                cr.set_source(g)
                cr.arc(x + 6, y - 3.2, 7, 0, TAU)
                cr.fill()
        else:
            cr.set_source_rgba(0.35, 0.38, 0.44, 0.45)
            cr.rectangle(x - 6, y - 1.1, 12, 2.2)
            cr.fill()
            trail = cairo.LinearGradient(x - 60, y, x - 6, y)
            trail.add_color_stop_rgba(0, 1, 1, 1, 0)
            trail.add_color_stop_rgba(1, 1, 1, 1, 0.18 * p["trail"])
            cr.set_source(trail)
            cr.rectangle(x - 60, y - 1.0, 54, 2.0)
            cr.fill()
        cr.restore()

    # ------------------------------------------------------------------
    # 地平线上的剪影：远山 + 城市屋顶
    # ------------------------------------------------------------------
    def _layers(self, scene: Scene):
        seed = self._city_seed(scene)
        if seed not in self._skyline:
            self._skyline[seed] = _city.generate(seed)
        return self._skyline[seed]

    @staticmethod
    def _city_seed(scene: Scene) -> int:
        """这座城市的天际线种子：由名字 + 经纬度决定，同一个城市永远一样。"""
        return skyline_seed(scene.location_name or "窗", scene.lat, scene.lon)

    def invalidate_location(self) -> None:
        """换城市之后调用：楼群数据与所有离屏缓存都得重来。

        城市那块位图的缓存键现在带了城市种子（见 _draw_skyline），所以只换名字
        也会重画；这里再整片清一次，是因为天空、云、窗台那几张的键只管"光 +
        尺寸"——换城市意味着换纬度，太阳的走法整个变了，留着旧键没有意义。
        换城市是低频操作，一次清干净最省心。
        """
        self._skyline.clear()
        for slot in (self._sky, self._cloud, self._city, self._sill, self._overlay):
            slot.invalidate()
        self._layout_cache.clear()

    def _light(self, scene: Scene, az0: float, direct: float) -> _city.Light:
        """把"此刻的天色"翻译成建筑与街道能用的光照参数。"""
        mu = scene.mood
        rel = ((scene.sun_az - az0 + 180.0) % 360.0) - 180.0
        moon_rel = ((scene.moon_az - az0 + 180.0) % 360.0) - 180.0
        return _city.Light(
            ambient=self._ambient(scene),
            direct=direct,
            sun_alt=scene.sun_alt,
            rel_az=rel,
            zenith=mu.zenith,
            horizon=mu.horizon,
            glow=mu.glow_color,
            night=clamp(-scene.sun_alt / 10.0, 0.0, 1.0),
            moon=clamp(scene.moon_light, 0.0, 1.0),
            moon_rel_az=moon_rel if scene.moon_alt > 0 else 0.0,
            lit_frac=self._lit_fraction(scene),
        )

    def _colors(self, scene: Scene):
        """近景剪影的基色（街上的人车用它来配色调）。"""
        mu = scene.mood
        amb = self._ambient(scene)
        near_col = mix(mu.horizon, (14, 16, 24), 0.90)
        near_col = tuple(c * (0.34 + 0.58 * amb) for c in near_col)
        return near_col

    # ------------------------------------------------------------------
    # 地面：楼前的人行道、马路、建筑投在路面上的影子
    # ------------------------------------------------------------------
    def _draw_ground(self, cr, w, h, scene: Scene, az0, fov, light) -> None:
        """把楼底下那条路面画出来：远人行道 → 马路 → 近人行道。"""
        layers = self._layers(scene)
        mu = scene.mood
        amb = clamp(light.ambient, 0.0, 1.0)
        y_kerb_far = GROUND_TOP * h
        y_road_top = (GROUND_TOP + 0.006) * h
        y_road_bot = ROAD_BOTTOM * h
        y_kerb_near = SILL_Y * h
        horizon01 = tuple(c / 255 for c in mu.horizon)

        # 路面：整体是被天光点亮的中性灰，夜里退成很暗的蓝灰
        road_day = mix_rgb((0.23, 0.23, 0.235), horizon01, 0.20)
        road_night = mix_rgb((0.045, 0.048, 0.062), horizon01, 0.32)
        road = mix_rgb(road_night, road_day, amb ** 1.35)
        g = cairo.LinearGradient(0, y_road_top, 0, y_kerb_near)
        g.add_color_stop_rgb(0, *[c * 1.14 for c in road])
        g.add_color_stop_rgb(0.5, *road)
        g.add_color_stop_rgb(1, *[c * 0.86 for c in road])
        cr.set_source(g)
        cr.rectangle(0, y_road_top, w, y_kerb_near - y_road_top)
        cr.fill()

        # 阳光斜着铺在路面上：太阳那一侧更暖更亮
        if light.direct > 0.02:
            sun_x = x_for_az(scene.sun_az, az0, fov, w)[0]
            cx = clamp(sun_x, -0.4 * w, 1.4 * w)
            rg = cairo.RadialGradient(cx, y_road_top, 0, cx, y_road_top,
                                      max(w * 0.75, h * 0.5))
            gc = mu.glow_color
            a = 0.11 * light.direct
            rg.add_color_stop_rgba(0, gc[0] / 255, gc[1] / 255, gc[2] / 255, a)
            rg.add_color_stop_rgba(0.55, gc[0] / 255, gc[1] / 255, gc[2] / 255, a * 0.35)
            rg.add_color_stop_rgba(1, gc[0] / 255, gc[1] / 255, gc[2] / 255, 0)
            cr.save()
            cr.set_operator(cairo.OPERATOR_ADD)
            cr.set_source(rg)
            cr.rectangle(0, y_road_top, w, y_kerb_near - y_road_top)
            cr.fill()
            cr.restore()

        # 建筑投在路面上的影子（太阳低就长、云厚就淡）
        ground_shadow_h = y_kerb_near - y_road_top
        _city.ground_shadow(cr, w, h, layers, light, y_road_top, ground_shadow_h)

        # 马路靠近楼的那一条暗（楼把光挡住了）
        ao = cairo.LinearGradient(0, y_road_top, 0, y_road_top + h * 0.016)
        ao.add_color_stop_rgba(0, 0, 0, 0, 0.34 + 0.14 * amb)
        ao.add_color_stop_rgba(1, 0, 0, 0, 0)
        cr.set_source(ao)
        cr.rectangle(0, y_road_top, w, h * 0.016)
        cr.fill()

        # 远侧人行道（楼根那一小条，被天光照得比路面亮）
        kerb_day = mix_rgb((0.36, 0.35, 0.34), horizon01, 0.28)
        kerb_night = mix_rgb((0.075, 0.080, 0.098), horizon01, 0.38)
        kerb = mix_rgb(kerb_night, kerb_day, amb ** 1.3)
        cr.set_source_rgb(*[clamp(c, 0, 1) for c in kerb])
        cr.rectangle(0, y_kerb_far, w, y_road_top - y_kerb_far)
        cr.fill()
        cr.set_source_rgba(0, 0, 0, 0.28)
        cr.rectangle(0, y_road_top - max(1.0, h * 0.0015), w, max(1.0, h * 0.0015))
        cr.fill()

        # 近侧人行道（窗下这一条，行人在上面走）
        kerb2 = shade(kerb, 1.10)
        cr.set_source_rgb(*[clamp(c, 0, 1) for c in kerb2])
        cr.rectangle(0, y_road_bot, w, y_kerb_near - y_road_bot)
        cr.fill()
        cr.set_source_rgba(1, 1, 1, 0.10 + 0.10 * amb)
        cr.rectangle(0, y_road_bot, w, max(1.0, h * 0.0016))
        cr.fill()
        cr.set_source_rgba(0, 0, 0, 0.20)
        cr.rectangle(0, y_road_bot + max(1.0, h * 0.0016), w, max(1.0, h * 0.0016))
        cr.fill()

        # 车道中心那一点点标线：只在光够的时候露出来
        if amb > 0.25:
            y_mid = (y_road_top + y_road_bot) / 2
            dash = max(6.0, w * 0.012)
            x = -dash
            cr.set_source_rgba(1.0, 0.96, 0.86, 0.10 + 0.16 * amb)
            while x < w:
                cr.rectangle(x, y_mid, dash * 0.55, max(1.0, h * 0.0016))
                x += dash * 2.1
            cr.fill()

        # 雨天的路面反光
        if scene.has_weather and scene.precip_kind == "rain" and scene.precip_strength > 0.15:
            wet = cairo.LinearGradient(0, y_road_top, 0, y_kerb_near)
            tone = mix(mu.horizon, (255, 255, 255), 0.2)
            wet.add_color_stop_rgba(0, tone[0] / 255, tone[1] / 255, tone[2] / 255,
                                    0.16 * scene.precip_strength)
            wet.add_color_stop_rgba(1, tone[0] / 255, tone[1] / 255, tone[2] / 255,
                                    0.06 * scene.precip_strength)
            cr.set_source(wet)
            cr.rectangle(0, y_road_top, w, y_kerb_near - y_road_top)
            cr.fill()

    def _draw_street(self, cr, w, h, scene: Scene, light):
        """地平线之下、窗台之前的那条街上的人与车。"""
        if self._street_roster is None:
            self._street_roster = street.roster(self.fx.seed * 7 + 13)
        if self._street_trees is None:
            self._street_trees = street.trees(self.fx.seed * 5 + 3)
        ui = self.ui
        near_col = self._colors(scene)
        # 时间取"正在走动的墙钟"，而不是每分钟才重建一次的 scene.when，
        # 否则人与车会像定格一样一分钟跳一次（云是用墙钟画的，所以一直很顺）。
        # 时间旅行（预览）时以预览那一刻为起点，再加上之后真正走过的秒数：
        # 天色停在被预览的时刻，但街上的人车照常走，不会冻成一张照片。
        when = scene.when
        if self.clock is not None:
            try:
                now = self.clock()
            except Exception:
                now = None
            if now is not None:
                started = ui.preview_started if ui.preview_dt is not None else None
                if scene.preview and started is not None:
                    when = scene.when + (now - started)
                else:
                    when = now
        t = (when.hour * 3600 + when.minute * 60 + when.second
             + getattr(when, "microsecond", 0) / 1e6)
        street.draw(cr, w, h, scene, self._street_roster, t, light, near_col,
                    self._layers(scene).lamps, self._street_trees or ())

    def _lit_fraction(self, scene: Scene) -> float:
        """此刻有多少比例的窗户亮着灯。"""
        alt = scene.sun_alt
        if alt > 2.5:
            return 0.0
        base = clamp((2.5 - alt) / 9.0, 0.0, 1.0) ** 0.7 * 0.38
        hour = scene.when.hour + scene.when.minute / 60.0
        if 1.0 <= hour < 5.0:
            base *= 0.5
        elif hour >= 23.0 or hour < 1.0:
            base *= 0.78
        return base

    def _draw_skyline(self, cr, w, h, scene: Scene, light) -> None:
        layers = self._layers(scene)
        mu = scene.mood
        horizon_y = HORIZON_Y * h
        # 城市是整帧里最贵的一块（每帧上千条路径），而它只随光变化——
        # 把光照量化成很细的档位缓存下来，太阳走过一格才重画一次。
        # key 里必须带上城市种子：光没变、城市变了的时候，一样得重画。
        key = (int(w), int(h), self._city_seed(scene),
               round(light.sun_alt * 4), round(light.rel_az * 4),
               round(light.ambient * 60), round(light.direct * 60),
               round(light.night * 40), round(light.moon * 30),
               round(light.lit_frac * 60))
        if self._city.stale(key, w, h):
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, int(w), int(h))
            _city.draw(cairo.Context(surf), w, h, layers, light, horizon_y)
            self._city.store(key, surf)
        cr.set_source_surface(self._city.surf, 0, 0)
        cr.paint()
        # 雾：把远处的屋顶糊掉
        if scene.fog:
            top = horizon_y - h * 0.20
            fog = cairo.LinearGradient(0, top, 0, horizon_y + h * 0.01)
            tone = mix(mu.horizon, (210, 214, 218), 0.45)
            fog.add_color_stop_rgba(0, tone[0] / 255, tone[1] / 255, tone[2] / 255, 0.0)
            fog.add_color_stop_rgba(0.65, tone[0] / 255, tone[1] / 255, tone[2] / 255, 0.34)
            fog.add_color_stop_rgba(1, tone[0] / 255, tone[1] / 255, tone[2] / 255, 0.52)
            cr.set_source(fog)
            cr.rectangle(0, top, w, h * 0.21)
            cr.fill()

    # ------------------------------------------------------------------
    def _draw_vignette(self, cr, w, h, scene: Scene):
        key = (int(w), int(h))
        if self._overlay.stale(key, w, h):
            self._overlay.store(key, self._render_overlay(int(w), int(h)))
        cr.set_source_surface(self._overlay.surf, 0, 0)
        cr.paint()

    def _render_overlay(self, w: int, h: int):
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(surf)
        top = cairo.LinearGradient(0, 0, 0, h * 0.09)
        top.add_color_stop_rgba(0, 0, 0, 0, 0.26)
        top.add_color_stop_rgba(1, 0, 0, 0, 0)
        cr.set_source(top)
        cr.rectangle(0, 0, w, h * 0.09)
        cr.fill()
        side_w = w * 0.05
        for x0, x1 in ((0.0, side_w), (w, w - side_w)):
            g = cairo.LinearGradient(x0, 0, x1, 0)
            g.add_color_stop_rgba(0, 0, 0, 0, 0.18)
            g.add_color_stop_rgba(1, 0, 0, 0, 0)
            cr.set_source(g)
            cr.rectangle(min(x0, x1), 0, side_w, h)
            cr.fill()
        vg = cairo.RadialGradient(w / 2, h * 0.55, min(w, h) * 0.35,
                                  w / 2, h * 0.55, max(w, h) * 0.78)
        vg.add_color_stop_rgba(0, 0, 0, 0, 0)
        vg.add_color_stop_rgba(1, 0, 0, 0, 0.20)
        cr.set_source(vg)
        cr.rectangle(0, 0, w, h)
        cr.fill()
        # 玻璃反光
        cr.save()
        cr.translate(0, -h * 0.1)
        cr.rotate(-0.16)
        g = cairo.LinearGradient(w * 0.02, 0, w * 0.55, 0)
        g.add_color_stop_rgba(0, 1, 1, 1, 0.0)
        g.add_color_stop_rgba(0.45, 1, 1, 1, 0.035)
        g.add_color_stop_rgba(0.62, 1, 1, 1, 0.055)
        g.add_color_stop_rgba(1, 1, 1, 1, 0.0)
        cr.set_source(g)
        cr.rectangle(0, 0, w * 1.2, h * 1.4)
        cr.fill()
        cr.restore()
        return surf

    # ------------------------------------------------------------------
    # 窗台：受光、光斑、那盆小植物与它的影子
    # ------------------------------------------------------------------
    def _draw_sill(self, cr, w, h, scene: Scene, az0, fov, sun_x, direct,
                   hs=None):
        amb = self._ambient(scene)
        hs = hs or h      # 窗台的排版高度（底下那条留给系统面板）
        # 窗台 + 那盆植物也是静态的（只随光与太阳位置变），同样缓存。
        key = (int(w), int(h), int(hs), round(amb * 60), round(direct * 60),
               round(sun_x / 3.0), round(scene.sun_alt * 4),
               round(scene.sun_az * 4), round(fov))
        if not self._sill.stale(key, w, h):
            cr.set_source_surface(self._sill.surf, 0, 0)
            cr.paint()
            return
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, int(w), int(h))
        c2 = cairo.Context(surf)
        self._paint_sill(c2, w, h, scene, az0, fov, sun_x, direct, hs)
        self._sill.store(key, surf)
        cr.set_source_surface(surf, 0, 0)
        cr.paint()

    def _paint_sill(self, cr, w, h, scene: Scene, az0, fov, sun_x, direct,
                    hs=None):
        mu = scene.mood
        amb = self._ambient(scene)
        hs = hs or h
        y0 = SILL_Y * hs
        sill_h = hs - y0            # 露出来的台面（"景色"里的那一段）
        sh = h - y0                 # 一直到窗口最底下（含留给面板的余量）
        horizon01 = tuple(c / 255 for c in mu.horizon)

        # 窗框的下框：一条横在街与窗台之间的木框，把"窗外"和"窗里"分开
        rail_h = hs * 0.012
        frame = mix_rgb((0.20, 0.185, 0.175), horizon01, 0.16)
        frame = shade(frame, 0.26 + 0.66 * amb)
        day_tone = mix_rgb((0.54, 0.52, 0.49), horizon01, 0.22)
        night_tone = mix_rgb((0.045, 0.05, 0.08), horizon01, 0.40)
        lit = mix_rgb(night_tone, day_tone, amb ** 1.7)
        rg = cairo.LinearGradient(0, y0, 0, y0 + rail_h)
        rg.add_color_stop_rgb(0, *[c * 1.35 for c in frame])
        rg.add_color_stop_rgb(0.18, *[c * 1.10 for c in frame])
        rg.add_color_stop_rgb(0.55, *frame)
        rg.add_color_stop_rgb(1, *[c * 0.72 for c in frame])
        cr.set_source(rg)
        cr.rectangle(0, y0, w, rail_h)
        cr.fill()
        cr.set_source_rgba(0, 0, 0, 0.28)
        cr.rectangle(0, y0 + rail_h, w, max(1.0, hs * 0.0035))
        cr.fill()
        y0 = y0 + rail_h + max(1.0, hs * 0.0035)
        sill_h -= (y0 - SILL_Y * hs)
        sh = h - y0

        g = cairo.LinearGradient(0, y0, 0, h)
        g.add_color_stop_rgb(0, *[c * 1.10 for c in lit])
        g.add_color_stop_rgb(0.30, *lit)
        g.add_color_stop_rgb(1, *[c * 0.66 for c in lit])
        cr.set_source(g)
        cr.rectangle(0, y0, w, sh)
        cr.fill()
        # 窗框投在窗台上的阴影
        shadow = cairo.LinearGradient(0, y0, 0, y0 + sill_h * 0.55)
        shadow.add_color_stop_rgba(0, 0, 0, 0, 0.30)
        shadow.add_color_stop_rgba(0.45, 0, 0, 0, 0.10)
        shadow.add_color_stop_rgba(1, 0, 0, 0, 0)
        cr.set_source(shadow)
        cr.rectangle(0, y0, w, sill_h * 0.55)
        cr.fill()
        cr.set_source_rgba(1, 1, 1, 0.04 + 0.10 * amb)
        cr.rectangle(0, y0, w, 1.2)
        cr.fill()

        # 窗台是块石头：拉一点细纹与几道浅浅的石缝，免得是一整片平色
        grain_h = min(sh, hs * 0.10)
        cr.save()
        cr.rectangle(0, y0, w, grain_h)
        cr.clip()
        for i in range(26):
            frac = (i * 0.041) % 1.0
            seed = (i * 7919) % 997 / 997.0
            yy = y0 + grain_h * frac
            cr.set_source_rgba(0, 0, 0, 0.03 + 0.05 * seed)
            cr.rectangle(0, yy, w, 0.8 + 0.8 * seed)
            cr.fill()
        for i in range(4):
            xx = w * ((i + 0.5) / 4.0) + (i % 2) * w * 0.03
            cr.set_source_rgba(0, 0, 0, 0.05)
            cr.rectangle(xx, y0, max(0.8, w * 0.0012), grain_h)
            cr.fill()
        cr.restore()

        # 窗台上的光斑：太阳越低，光斑越宽越斜
        if direct > 0.02:
            d_rel = ((scene.sun_az - az0 + 180) % 360) - 180
            if abs(d_rel) < fov / 2 + 30:
                px = clamp(sun_x, -0.15 * w, 1.15 * w)
                low = clamp(1.0 - scene.sun_alt / 35.0, 0.0, 1.0)
                rx = w * lerp(0.20, 0.46, low)
                ry = sill_h * lerp(0.55, 1.25, low)
                py = y0 + sill_h * 0.42
                cr.save()
                cr.set_operator(cairo.OPERATOR_ADD)
                spot = cairo.RadialGradient(px, py, 0, px, py, max(rx, ry))
                gc = mu.glow_color
                a = 0.30 * direct
                spot.add_color_stop_rgba(0, gc[0] / 255, gc[1] / 255, gc[2] / 255, a)
                spot.add_color_stop_rgba(0.45, gc[0] / 255, gc[1] / 255, gc[2] / 255, a * 0.42)
                spot.add_color_stop_rgba(1, gc[0] / 255, gc[1] / 255, gc[2] / 255, 0)
                cr.save()
                cr.translate(px, py)
                cr.scale(rx / max(rx, ry), ry / max(rx, ry))
                cr.translate(-px, -py)
                cr.set_source(spot)
                cr.rectangle(0, y0, w, sh)
                cr.fill()
                cr.restore()
                cr.restore()

        # 台面纹理
        cr.set_source_rgba(0, 0, 0, 0.045)
        for i in range(1, 5):
            yy = y0 + sill_h * (i / 5.2)
            cr.rectangle(0, yy, w, 1)
            cr.fill()

        self._draw_plant(cr, w, hs, scene, az0, fov, direct, lit)

    def _plant_shapes(self, cr, x, y, scale, green=(0.20, 0.40, 0.26), flat=None):
        """在 (x, y) 画一盆小植物：y 是花盆底，scale 由窗口高度决定。

        flat 不为空时整株用同一个颜色填（画影子用）。
        """
        pot_w = 54.0 * scale
        pot_h = 30.0 * scale
        rim_w = pot_w * 1.14
        rim_h = 8.0 * scale
        top = y - pot_h

        # 花盆：上宽下窄，带一圈盆沿
        cr.new_path()
        cr.move_to(x - rim_w / 2, top - rim_h)
        cr.line_to(x + rim_w / 2, top - rim_h)
        cr.line_to(x + rim_w / 2, top)
        cr.line_to(x + pot_w / 2, top)
        cr.line_to(x + pot_w * 0.40, y)
        cr.line_to(x - pot_w * 0.40, y)
        cr.line_to(x - pot_w / 2, top)
        cr.line_to(x - rim_w / 2, top)
        cr.close_path()
        if flat is None:
            cr.save()
            cr.clip_preserve()
            pot = cairo.LinearGradient(x - rim_w / 2, 0, x + rim_w / 2, 0)
            pot.add_color_stop_rgb(0, 0.34, 0.25, 0.20)
            pot.add_color_stop_rgb(0.36, 0.56, 0.42, 0.33)
            pot.add_color_stop_rgb(0.66, 0.48, 0.35, 0.28)
            pot.add_color_stop_rgb(1, 0.27, 0.20, 0.17)
            cr.set_source(pot)
            cr.paint()
            # 盆沿下面的那一道暗，盆就有了厚度
            sh = cairo.LinearGradient(0, top, 0, top + rim_h * 0.9)
            sh.add_color_stop_rgba(0, 0, 0, 0, 0.30)
            sh.add_color_stop_rgba(1, 0, 0, 0, 0)
            cr.set_source(sh)
            cr.paint()
            # 盆身的竖向高光
            hl = cairo.LinearGradient(x - rim_w * 0.30, 0, x - rim_w * 0.02, 0)
            hl.add_color_stop_rgba(0, 1, 1, 1, 0)
            hl.add_color_stop_rgba(0.6, 1, 1, 1, 0.10)
            hl.add_color_stop_rgba(1, 1, 1, 1, 0)
            cr.set_source(hl)
            cr.paint()
            cr.restore()
            # 盆沿：上面一条亮线，下面一条暗线
            cr.set_source_rgba(1.0, 0.96, 0.90, 0.26)
            cr.rectangle(x - rim_w / 2, top - rim_h, rim_w, max(0.8, rim_h * 0.22))
            cr.fill()
            cr.set_source_rgba(0, 0, 0, 0.22)
            cr.rectangle(x - rim_w / 2, top - rim_h * 0.16, rim_w,
                         max(0.8, rim_h * 0.18))
            cr.fill()
            # 盆里的土
            soil = cairo.LinearGradient(x - rim_w / 2, 0, x + rim_w / 2, 0)
            soil.add_color_stop_rgb(0, 0.15, 0.11, 0.09)
            soil.add_color_stop_rgb(0.5, 0.26, 0.19, 0.14)
            soil.add_color_stop_rgb(1, 0.13, 0.10, 0.08)
            cr.set_source(soil)
            cr.rectangle(x - rim_w * 0.44, top - rim_h * 0.88,
                         rim_w * 0.88, max(1.2, rim_h * 0.34))
            cr.fill()
            for i, (fx, fs) in enumerate(((-0.24, 0.9), (0.04, 0.6), (0.26, 0.8))):
                cr.set_source_rgba(0.45, 0.40, 0.34, 0.55)
                cr.arc(x + rim_w * fx, top - rim_h * 0.68,
                       max(0.5, 1.3 * scale * fs), 0, TAU)
                cr.fill()
        else:
            cr.set_source_rgba(*flat)
            cr.fill_preserve()

        # 叶丛：一根主干 + 几根分枝，叶子大小、朝向都不一样
        base_y = top - rim_h * 0.55
        cr.save()
        cr.translate(x, base_y)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        trunk = 15.0 * scale
        if flat is None:                       # 露出土面的那一小段主干
            cr.set_source_rgba(*[min(1.0, c * 0.55) for c in green], 0.95)
            cr.set_line_width(max(1.0, 3.4 * scale))
            cr.move_to(0, 0)
            cr.line_to(0.6 * scale, -trunk)
            cr.stroke()
        # (角度, 柄长, 叶长, 叶宽, 明暗, 冷暖) —— 角度 -π/2 是正上方
        leaves = (
            (-2.86, 26, 20, 8, 0.74, -0.5), (-0.30, 24, 19, 8, 0.72, -0.5),
            (-2.52, 30, 26, 11, 0.82, -0.3), (-0.68, 28, 25, 10, 0.80, -0.3),
            (-2.16, 34, 32, 15, 0.90, 0.2), (-1.02, 32, 30, 14, 0.88, 0.2),
            (-1.86, 38, 40, 19, 0.98, 0.0), (-1.28, 36, 38, 18, 0.96, 0.0),
            (-2.40, 22, 34, 13, 0.86, -0.2), (-0.80, 20, 32, 12, 0.84, -0.2),
            (-1.57, 30, 30, 13, 0.92, 0.35), (-1.57, 42, 46, 20, 1.04, 0.0),
            (-1.10, 40, 26, 16, 0.94, 0.3), (-2.04, 42, 25, 15, 0.92, 0.3),
        )
        for i, (ang, stem_px, L, Wd, tone, hue) in enumerate(leaves):
            stem = stem_px * scale
            length = L * scale
            width = Wd * scale
            droop = 0.16 if i % 3 == 0 else 0.05
            leaf_col = green
            if abs(hue) > 1e-6:
                tgt = (0.42, 0.46, 0.20) if hue > 0 else (0.16, 0.34, 0.30)
                leaf_col = mix_rgb(green, tgt, abs(hue) * 0.5)
            bx = math.cos(ang) * stem
            by = -trunk + math.sin(ang) * stem + ((i % 3) - 1) * 2.2 * scale
            if flat is None:                     # 叶柄
                cr.set_source_rgba(*[min(1.0, c * 0.66) for c in leaf_col], 0.95)
                cr.set_line_width(max(0.8, 1.8 * scale))
                cr.move_to(0, -trunk)
                cr.line_to(bx, by)
                cr.stroke()
            cr.save()
            cr.translate(bx, by)
            cr.rotate(ang)
            if flat is not None:
                cr.set_source_rgba(*flat)
            cr.new_path()
            cr.move_to(0, 0)
            cr.curve_to(length * 0.26, -width * 0.64,
                        length * 0.76, -width * 0.40,
                        length * 0.99, droop * length * 0.9)
            cr.curve_to(length * 0.74, width * 0.42,
                        length * 0.26, width * 0.64, 0, 0)
            cr.close_path()
            if flat is None:
                lg = cairo.LinearGradient(0, 0, length, 0)
                c0 = shade(leaf_col, 0.58 * tone)
                c1 = shade(leaf_col, 1.24 * tone)
                lg.add_color_stop_rgb(0, *[min(1.0, c) for c in c0])
                lg.add_color_stop_rgb(0.55, *[min(1.0, c) for c in shade(leaf_col, tone)])
                lg.add_color_stop_rgb(1, *[min(1.0, c) for c in c1])
                cr.set_source(lg)
                cr.fill_preserve()
                # 叶脉
                cr.set_source_rgba(*[min(1.0, c * 1.3) for c in leaf_col], 0.28)
                cr.set_line_width(max(0.5, 0.9 * scale))
                cr.move_to(length * 0.06, 0)
                cr.line_to(length * 0.90, -droop * length * 0.7)
                cr.stroke()
            else:
                cr.fill()
            cr.restore()
        cr.restore()

    def _draw_plant(self, cr, w, h, scene: Scene, az0, fov, direct, lit_color):
        mu = scene.mood
        x = 0.115 * w
        y = 0.918 * h
        s = clamp(h / 700.0, 0.72, 1.7)
        plant_h = 104.0 * s
        d_rel = ((scene.sun_az - az0 + 180) % 360) - 180
        alt = max(scene.sun_alt, 0.6)
        shadow_len = clamp(1.0 / math.tan(math.radians(alt)), 0.0, 6.0) * plant_h
        dx = -math.sin(math.radians(d_rel)) * shadow_len * 0.42
        dy = math.cos(math.radians(d_rel)) * shadow_len * 0.09

        # 环境遮光（花盆底下那圈）
        cr.save()
        soft = cairo.RadialGradient(x, y, 0, x, y, 46 * s)
        soft.add_color_stop_rgba(0, 0, 0, 0, 0.34)
        soft.add_color_stop_rgba(1, 0, 0, 0, 0)
        cr.set_source(soft)
        cr.arc(x, y, 46 * s, 0, TAU)
        cr.fill()
        cr.restore()

        # 真实的影子：方向与长短由太阳方位角、高度角决定
        if direct > 0.03:
            shadow_col = mix_rgb((0.09, 0.08, 0.10), tuple(c / 255 for c in mu.horizon), 0.35)
            kx = dx / plant_h
            ky = dy / plant_h
            for k, a in ((0.96, 0.62), (1.06, 0.30), (1.18, 0.14)):
                cr.save()
                # 影子只落在窗台上；按"高度→地面偏移"的仿射变换把整株压到台面上
                cr.rectangle(0, SILL_TOP * h, w, h - SILL_TOP * h)
                cr.clip()
                cr.transform(cairo.Matrix(xx=1.0, yx=0.0, xy=-kx * k, yy=1.0 - ky * k,
                                          x0=kx * k * y, y0=ky * k * y))
                self._plant_shapes(cr, x, y, s,
                                   flat=(shadow_col[0], shadow_col[1], shadow_col[2],
                                         a * direct))
                cr.restore()

        self._draw_plant_body(cr, w, h, x, y, s, scene, az0)

    def _draw_plant_body(self, cr, w, h, x, y, s, scene: Scene, az0):
        mu = scene.mood
        amb = self._ambient(scene)
        green = mix_rgb((0.20, 0.40, 0.26), tuple(c / 255 for c in mu.horizon),
                        0.45 * (1.0 - amb))
        green = shade(green, 0.34 + 0.66 * amb)
        cr.save()
        self._plant_shapes(cr, x, y, s, green)
        cr.restore()
        # 受光面：朝太阳那一侧的叶子亮一点
        d_rel = ((scene.sun_az - az0 + 180) % 360) - 180
        light = clamp(mu.ambient * 0.9, 0.0, 1.0)
        if light > 0.05:
            cr.save()
            cr.translate(x, y)
            gl = cairo.RadialGradient(
                math.sin(math.radians(d_rel)) * 34 * s,
                -58 * s, 0, 0, -58 * s, 120 * s)
            gc = mu.glow_color
            gl.add_color_stop_rgba(0, gc[0] / 255, gc[1] / 255, gc[2] / 255, 0.16 * light)
            gl.add_color_stop_rgba(1, gc[0] / 255, gc[1] / 255, gc[2] / 255, 0)
            cr.set_source(gl)
            cr.arc(0, -58 * s, 120 * s, 0, TAU)
            cr.fill()
            cr.restore()

    # ------------------------------------------------------------------
    # 降水与闪电
    # ------------------------------------------------------------------
    def _draw_precip(self, cr, w, h, scene: Scene):
        if not self.fx.drops and not self.fx.flakes:
            return
        mu = scene.mood
        darkness = clamp((-scene.sun_alt) / 12.0, 0.0, 1.0)
        drop_col = mix((236, 242, 255), mu.horizon, 0.30 * (1 - darkness))
        cr.save()
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        wind = scene.wind_speed if scene.has_weather else 0.0
        to_dir = (scene.wind_dir + 180.0) % 360.0
        slant = math.sin(math.radians(((to_dir - 180 + 180) % 360) - 180)) * wind / 26.0
        for x, y, ln, speed, a, thick in self.fx.drops:
            px, py = x * w, y * h
            L = ln * h * (0.5 + 0.5 * a)
            dx = slant * L * 0.8
            cr.set_source_rgba(drop_col[0] / 255, drop_col[1] / 255, drop_col[2] / 255,
                               a * (0.30 + 0.45 * darkness))
            cr.set_line_width(max(0.7, thick * w / 1400.0))
            cr.move_to(px, py)
            cr.line_to(px + dx, py + L)
            cr.stroke()
        for x, y, r_, radius, phase, a in self.fx.flakes:
            px, py = x * w, y * h
            rr = max(0.9, radius * w / 1500.0)
            cr.set_source_rgba(0.96, 0.97, 1.0, a * (0.32 + 0.5 * darkness))
            cr.arc(px, py, rr, 0, TAU)
            cr.fill()
        cr.restore()

    def _draw_flash(self, cr, w, h, scene: Scene):
        f = self.fx.flash
        if f <= 0.01:
            return
        v = clamp(f * f, 0, 1)
        cr.set_source_rgba(0.88, 0.92, 1.0, 0.34 * v)
        cr.rectangle(0, 0, w, h)
        cr.fill()

    # ------------------------------------------------------------------
    # 今日天色长卷
    # ------------------------------------------------------------------
    def _build_ribbon(self, ui: UIState) -> None:
        n = len(ui.ribbon)
        if n == 0:
            ui.ribbon_surface = None
            return
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, n, 20)
        cr = cairo.Context(surf)
        for i, (_, col) in enumerate(ui.ribbon):
            top = shade(tuple(c / 255 for c in col), 1.10)
            bot = shade(tuple(c / 255 for c in col), 0.86)
            g = cairo.LinearGradient(0, 0, 0, 20)
            g.add_color_stop_rgb(0, *top)
            g.add_color_stop_rgb(1, *bot)
            cr.set_source(g)
            cr.rectangle(i, 0, 1, 20)
            cr.fill()
        ui.ribbon_surface = surf

    def ribbon_time_at(self, ui: UIState, x: float):
        if not ui.ribbon:
            return None
        x0, _, rw, _ = ui.ribbon_rect
        if rw <= 0:
            return None
        t = clamp((x - x0) / rw, 0.0, 1.0)
        idx = clamp(int(t * len(ui.ribbon)), 0, len(ui.ribbon) - 1)
        return ui.ribbon[idx][0]

    def _draw_ribbon(self, cr, w, h, scene: Scene):
        ui = self.ui
        if not ui.ribbon:
            return
        if ui.ribbon_surface is None:
            self._build_ribbon(ui)
        x0 = 0.035 * w
        rw = 0.93 * w
        rh = clamp(h * 0.026, 14.0, 24.0)
        y0 = RIBBON_Y * h
        ui.ribbon_rect = (x0, y0, rw, rh)
        cr.save()
        rounded_rect(cr, x0, y0, rw, rh, rh * 0.42)
        cr.clip()
        surf = ui.ribbon_surface
        if surf is not None:
            pat = cairo.SurfacePattern(surf)
            pat.set_filter(cairo.FILTER_BILINEAR)
            pat.set_matrix(cairo.Matrix(xx=surf.get_width() / rw, yy=20.0 / rh))
            cr.save()
            cr.translate(x0, y0)
            cr.set_source(pat)
            cr.rectangle(0, 0, rw, rh)
            cr.fill()
            cr.restore()
        # 已经过去的那一段压暗一点
        frac_now = (scene.when.hour * 60 + scene.when.minute) / 1440.0
        cr.set_source_rgba(0, 0, 0, 0.22)
        cr.rectangle(x0, y0, rw * clamp(frac_now, 0, 1), rh)
        cr.fill()
        if ui.preview_dt is not None:
            pf = (ui.preview_dt.hour * 60 + ui.preview_dt.minute) / 1440.0
            cr.set_source_rgba(1, 1, 1, 0.10)
            cr.rectangle(x0 + rw * clamp(frac_now, 0, 1), y0,
                         rw * clamp(pf - frac_now, 0, 1), rh)
            cr.fill()
        cr.restore()

        # 刻度与时刻标记
        for hour in range(0, 25, 3):
            hx = x0 + rw * (hour / 24.0)
            cr.set_source_rgba(1, 1, 1, 0.30)
            cr.rectangle(hx, y0 + rh * 0.16, 1, rh * 0.68)
            cr.fill()
        # 日出 / 日落：不只是两道刻度，而是带上图标与时刻的标记
        mark_size = max(9.0, min(h * 0.014, 12.5))
        for key, kind, col in (("sunrise", "sunrise", (255, 214, 150)),
                               ("sunset", "sunset", (255, 186, 128))):
            ev = scene.events.get(key)
            if not ev:
                continue
            mx = x0 + rw * clamp(self._day_frac(ev), 0, 1)
            cr.set_source_rgba(0, 0, 0, 0.35)
            cr.rectangle(mx - 1, y0, 2, rh)
            cr.fill()
            ir = rh * 0.60
            icy = y0 - ir - 3.0
            cr.set_source_rgba(0.05, 0.06, 0.10, 0.74)
            cr.arc(mx, icy, ir + 1.4, 0, TAU)
            cr.fill()
            cr.set_source_rgba(1, 1, 1, 0.16)
            cr.arc(mx, icy, ir + 1.4, 0, TAU)
            cr.set_line_width(1)
            cr.stroke()
            draw_icon(cr, kind, mx, icy, ir * 1.55, col, 0.95)
            label = ev.strftime("%H:%M")
            tw, _ = draw_text(cr, label, 0, -1000, mark_size, (255, 255, 255), 0.0)
            draw_text(cr, label, clamp(mx - tw / 2, x0, x0 + rw - tw),
                      icy - ir - mark_size - 6.0, mark_size, (244, 240, 234), 0.68)

        # 此刻的游标
        nx = x0 + rw * clamp(frac_now, 0, 1)
        cr.set_source_rgba(1, 1, 1, 0.92)
        cr.rectangle(nx - 1, y0 - 3, 2, rh + 6)
        cr.fill()
        cr.set_source_rgba(1, 1, 1, 0.55)
        cr.arc(nx, y0 - 4.5, 2.2, 0, TAU)
        cr.fill()

        # 悬停 / 预览
        mark = ui.preview_dt or ui.hover_dt
        if mark is not None:
            f = (mark.hour * 60 + mark.minute) / 1440.0
            mx = x0 + rw * clamp(f, 0, 1)
            cr.set_source_rgba(1, 1, 1, 0.95 if ui.preview_dt else 0.5)
            cr.rectangle(mx - 1, y0 - 4, 2, rh + 8)
            cr.fill()
            label = mark.strftime("%H:%M")
            size = max(10.0, h * 0.0155)
            # 顺着长卷查一查这一刻的天气（app 每次重建长卷时一并算好）
            sub = ""
            if ui.ribbon_info:
                i = clamp(int(clamp(f, 0, 0.9999) * len(ui.ribbon)),
                          0, len(ui.ribbon_info) - 1)
                sub = ui.ribbon_info[i]
            tw, th = draw_text(cr, label, 0, -1000, size, (255, 255, 255), 0.0)
            sw, sh = (draw_text(cr, sub, 0, -1000, size * 0.82, (255, 255, 255), 0.0)
                      if sub else (0.0, 0.0))
            bw = max(tw, sw) + 22
            bh = th + (sh + 5 if sub else 0) + 10
            bx = clamp(mx - bw / 2, x0, x0 + rw - bw)
            byy = y0 - 14 - bh
            cr.set_source_rgba(0.05, 0.06, 0.10, 0.76)
            rounded_rect(cr, bx, byy, bw, bh, min(bh / 2, 14.0))
            cr.fill()
            cr.set_source_rgba(1, 1, 1, 0.14)
            rounded_rect(cr, bx, byy, bw, bh, min(bh / 2, 14.0))
            cr.set_line_width(1)
            cr.stroke()
            draw_text(cr, label, bx + bw / 2, byy + 5, size, (250, 250, 255), 0.95,
                      align="center")
            if sub:
                draw_text(cr, sub, bx + bw / 2, byy + 6 + th, size * 0.82,
                          (226, 234, 248), 0.72, align="center")

        # 小时标签
        label_size = max(8.0, min(h * 0.013, 12.0))
        label_y = min(y0 + rh + 3, h - label_size - 1.5)
        for hour in (0, 6, 12, 18, 24):
            hx = x0 + rw * (hour / 24.0)
            draw_text(cr, f"{hour:02d}" if hour < 24 else "24", hx, label_y,
                      label_size, (245, 246, 255), 0.42, align="center")

    # ------------------------------------------------------------------
    # 信息卡片
    # ------------------------------------------------------------------
    #
    # 这张卡是**可以点的**（1.1.9 起）：
    #   * 每一行都是"图标 + 标题 + 数值"，鼠标停上去整行亮起来；
    #   * 点日出/日落/月亮 → 画面跳到那一刻（还是那套预览，Esc 或点提示条回此刻）；
    #   * 点窗外/风     → 摊开这条背后的完整数据（体感温度、能见度、数据来源…）；
    #   * 右上角箭头收起卡片，右下角刷新按钮立刻重问一次真实天气；
    #   * 日出到日落那条"日弧"可以悬停看时刻、点一下跳过去。
    # 命中方块全部记进 ui.info_rects，窗口那边（app.py）照着 kind 决定做什么。
    # ------------------------------------------------------------------

    @staticmethod
    def _day_frac(dt) -> float:
        return clamp((dt.hour * 60 + dt.minute) / 1440.0, 0.0, 1.0)

    @staticmethod
    def _icon_button(cr, kind, cx, cy, size, color, alpha=0.72, hover=False):
        """一个"看着就能点"的小方块：淡底 + 一枚矢量图标。"""
        cr.set_source_rgba(1, 1, 1, 0.16 if hover else 0.08)
        rounded_rect(cr, cx - size / 2, cy - size / 2, size, size, size * 0.32)
        cr.fill()
        draw_icon(cr, kind, cx, cy, size * 0.52, color, alpha)

    @staticmethod
    def _weather_icon(scene: Scene) -> str:
        """窗外那一行该配哪枚图标——按"天上真正有什么"来选。"""
        if scene.precip_kind == "snow":
            return "snow"
        if scene.precip_kind == "rain":
            return "rain"
        if scene.fog:
            return "fog"
        return "cloud" if scene.cloud >= 55 else "sun"

    def _rows(self, scene: Scene) -> list[FactRow]:
        ev = scene.events
        rows: list[FactRow] = []
        noon = ev.get("noon")
        if scene.sun_alt > -0.9:
            rows.append(FactRow("sun", "太阳", f"{compass(scene.sun_az)} {scene.sun_az:.0f}°",
                                f"仰角 {scene.sun_alt:.1f}°", "open", noon))
        else:
            sunr_ = ev.get("sunrise")
            rising = bool(sunr_ and scene.when < sunr_)
            rows.append(FactRow("sunrise" if rising else "sunset", "太阳",
                                f"在{compass(scene.sun_az)}方地平线下",
                                "还没升起" if rising else "已经落下", "open", noon))
        sunr = ev.get("sunrise")
        golden = ev.get("golden_morning_end")
        rows.append(FactRow("sunrise", "日出",
                            sunr.strftime("%H:%M") if sunr else "极昼 / 极夜",
                            f"金色时刻至 {golden.strftime('%H:%M')}"
                            if sunr and golden else "",
                            "open", sunr))
        suns = ev.get("sunset")
        left = scene.daylight_left
        rows.append(FactRow("sunset", "日落",
                            suns.strftime("%H:%M") if suns else "极昼 / 极夜",
                            f"还剩 {duration_zh(left)}" if left
                            else ("今天已过去" if suns else ""),
                            "open", suns))
        mr, ms = ev.get("moonrise"), ev.get("moonset")
        note = " · ".join(x for x in (
            f"月出 {mr.strftime('%H:%M')}" if mr else "",
            f"月落 {ms.strftime('%H:%M')}" if ms else "") if x)
        rows.append(FactRow("moon", "月亮",
                            f"{phase_name_simple(scene.moon_phase)} {scene.moon_illum * 100:.0f}%",
                            note, "open", mr or ms))
        if scene.has_weather:
            rows.append(FactRow(self._weather_icon(scene), "窗外",
                                f"{scene.weather_text} {scene.temp:.0f}°C",
                                f"云量 {scene.cloud:.0f}%", "detail"))
            if scene.wind_speed:
                rows.append(FactRow("wind", "风",
                                    f"{compass(scene.wind_dir)} {scene.wind_speed:.1f} km/h",
                                    f"湿度 {scene.humidity:.0f}%" if scene.humidity else "",
                                    "detail"))
        elif scene.weather_nodata:
            # 跳到远一点的日子：这一天还没有预报，就直说没有
            rows.append(FactRow("cloud", "窗外", "这天还没有预报",
                                "Open-Meteo 只给 16 天以内", "detail"))
        elif scene.weather_disabled:
            # 用户自己关的天气，别写成"未联网"——那会把人指去查网络
            rows.append(FactRow("info", "窗外", "你关掉了天气 · 只看天", "", "detail"))
        else:
            rows.append(FactRow("cloud", "窗外", "未联网 · 仅天文模式", "", "detail"))
        return rows

    def _draw_info(self, cr, w, h, scene: Scene, az0, fov, direct):
        """把这张卡画上去——大部分时间其实是把缓存贴上去（见 _paint_info）。

        一帧要下上百笔（图标、文字、圆角），而这张卡只在"分钟变了 / 鼠标划过 /
        天气更新"时才真的不一样。所以整张卡离屏缓存，按 _info_key 判断要不要重画。
        """
        L = self._info_layout(w, h, scene)
        x = 0.028 * w
        y = 0.036 * h
        edge = 8 * L["scale"]                  # 给阴影留的边
        # 离屏图取整后 +1：多出的一像素保证右下角的圆角不被裁掉；
        # 但**存进去的尺寸必须和交给 stale() 的尺寸一致**，否则永远命中不了。
        box_w = int(L["card_w"] + edge * 2) + 1
        box_h = int(L["card_h"] + edge * 2) + 1
        bx, by = x - edge, y - edge
        key = self._info_key(w, h, scene)
        if self._info.stale(key, box_w, box_h):
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, box_w, box_h)
            inner = cairo.Context(surf)
            inner.translate(-bx, -by)
            self._info_rects = self._paint_info(inner, scene, x, y, L)
            self._info.store(key, surf)
        if self._info.surf is not None:
            cr.save()
            cr.set_source_surface(self._info.surf, bx, by)
            cr.paint()
            cr.restore()
        self.ui.info_rows = L["rows"]
        # 命中区每次照交不误：它是纯数据，窗口拿它判断鼠标点到了哪儿
        self.ui.info_rects = list(self._info_rects)

    def _info_key(self, w, h, scene: Scene):
        """这张卡的缓存 key——**卡片上任何一个字变了，这里就得跟着变**。

        漏一个参数，就是"卡片还显示着上一分钟 / 上一份天气"（CacheSlot 存在的
        理由正是这句话）。所以宁可多带几个：时刻、预览、悬停、精简模式、
        天气的每个字段、天色。
        """
        hover_dt = self.ui.info_hover_dt
        return (
            int(w), int(h),
            scene.when.strftime("%Y-%m-%d %H:%M"), scene.preview, scene.period_name,
            scene.location_name, scene.location_label,
            self.ui.info_hover, bool(self.ui.info_compact),
            hover_dt.strftime("%H:%M") if hover_dt else "",
            scene.sun_alt >= -0.9, round(scene.moon_phase, 3),
            scene.has_weather, scene.weather_disabled, scene.weather_stale,
            scene.weather_nodata, scene.weather_now, int(scene.weather_at),
            scene.weather_text, round(scene.cloud), round(scene.temp),
            round(scene.apparent), round(scene.humidity),
            round(scene.wind_speed, 1), round(scene.wind_dir),
            tuple(round(c) for c in scene.mood.horizon),
            tuple(sorted((k, str(v)) for k, v in scene.events.items())),
        )

    def _info_layout(self, w, h, scene: Scene) -> dict:
        """这张卡的排版：所有尺寸都在这里一次算清。

        离屏图裁多大、卡片画多高，用的是同一份数字——分开算迟早会对不上
        （不是被裁掉一条边，就是底下多出一块空白）。
        """
        scale = clamp(min(w / 1000.0, h / 620.0), 0.78, 1.5)
        pad = 16 * scale
        card_w = clamp(w * 0.42, 296 * scale, 428 * scale)
        compact = bool(self.ui.info_compact)
        rows = self._rows(scene)
        per_line = max(8, int((card_w - pad * 2 - 12 * scale) / (12.4 * scale)))
        hint_lines = wrap_cjk(human_hint(scene), per_line)
        if compact:
            hint_lines = hint_lines[:2]
        sunr = scene.events.get("sunrise")
        suns = scene.events.get("sunset")
        show_arc = bool(sunr and suns and suns > sunr) and not compact
        head_h = 30 * scale
        time_h = (34 if compact else 44) * scale
        arc_h = 30 * scale if show_arc else 0
        div_h = 12 * scale if not compact else 0
        row_h = 24 * scale
        rows_h = 0 if compact else row_h * len(rows)
        hint_h = 19 * scale * len(hint_lines) + 6 * scale
        foot_h = 22 * scale
        card_h = (pad + head_h + time_h + arc_h + div_h + rows_h + hint_h
                  + foot_h + pad * 0.5)
        return {
            "scale": scale, "pad": pad, "card_w": card_w, "card_h": card_h,
            "rows": rows, "compact": compact, "hint_lines": hint_lines,
            "show_arc": show_arc, "sunr": sunr, "suns": suns,
            "head_h": head_h, "time_h": time_h, "arc_h": arc_h, "div_h": div_h,
            "row_h": row_h, "rows_h": rows_h, "hint_h": hint_h, "foot_h": foot_h,
            "accent": scene.mood.horizon,
        }

    def _paint_info(self, cr, scene: Scene, x: float, y: float, L: dict) -> list:
        """真正下笔的那一遍：画在离屏图上，返回这张卡上所有能点的方块。"""
        scale, pad, card_w, card_h = L["scale"], L["pad"], L["card_w"], L["card_h"]
        rows, compact = L["rows"], L["compact"]
        hint_lines = L["hint_lines"]
        sunr, suns, show_arc = L["sunr"], L["suns"], L["show_arc"]
        head_h, time_h, arc_h = L["head_h"], L["time_h"], L["arc_h"]
        div_h, row_h, hint_h = L["div_h"], L["row_h"], L["hint_h"]
        accent = L["accent"]
        hover = self.ui.info_hover
        rects: list = []

        cr.save()
        # 卡片：阴影 + 玻璃底 + 随天色的一道顶光
        for i, a in ((5, 0.05), (3, 0.06), (1.5, 0.08)):
            cr.set_source_rgba(0, 0, 0, a)
            rounded_rect(cr, x - i, y - i + 2, card_w + i * 2, card_h + i * 2,
                         18 * scale)
            cr.fill()
        grad = cairo.LinearGradient(x, y, x + card_w * 0.7, y + card_h)
        grad.add_color_stop_rgba(0, 0.07, 0.08, 0.12, 0.74)
        grad.add_color_stop_rgba(1, 0.03, 0.04, 0.07, 0.62)
        rounded_rect(cr, x, y, card_w, card_h, 18 * scale)
        cr.set_source(grad)
        cr.fill()
        cr.save()
        rounded_rect(cr, x, y, card_w, card_h, 18 * scale)
        cr.clip()
        top = cairo.LinearGradient(x, y, x, y + 2.4 * scale)
        top.add_color_stop_rgba(0, accent[0] / 255, accent[1] / 255, accent[2] / 255, 0.5)
        top.add_color_stop_rgba(1, accent[0] / 255, accent[1] / 255, accent[2] / 255, 0.0)
        cr.set_source(top)
        cr.rectangle(x, y, card_w, 2.4 * scale)
        cr.fill()
        cr.restore()
        cr.set_source_rgba(1, 1, 1, 0.10)
        rounded_rect(cr, x, y, card_w, card_h, 18 * scale)
        cr.set_line_width(1)
        cr.stroke()

        cx = x + pad
        cy = y + pad * 0.75

        # ---- 抬头：城市 · 此刻/预览 · 收起 ----
        name = scene.location_name or scene.location_label or ""
        tw, _ = draw_text(cr, name, cx, cy, 16 * scale, (240, 244, 252), 0.94,
                          weight=Pango.Weight.MEDIUM)
        chip_text = f"预览 {scene.when.strftime('%H:%M')}" if scene.preview else "此刻"
        chip_w = (78 if scene.preview else 46) * scale
        chip_x = min(cx + tw + 9 * scale,
                     x + card_w - pad - chip_w - 28 * scale)
        cc = (255, 176, 96) if scene.preview else accent
        cr.set_source_rgba(cc[0] / 255, cc[1] / 255, cc[2] / 255, 0.26)
        rounded_rect(cr, chip_x, cy + 1.5 * scale, chip_w, 18 * scale, 9 * scale)
        cr.fill()
        draw_text(cr, chip_text, chip_x + chip_w / 2, cy + 3.5 * scale, 11 * scale,
                  (255, 255, 255), 0.94, align="center")
        btn = 22 * scale
        bx, by = x + card_w - pad - btn, cy + 0.5 * scale
        self._icon_button(cr, "chevron-down" if compact else "chevron-up",
                          bx + btn / 2, by + btn / 2, btn, (234, 240, 252), 0.74,
                          hover=hover == len(rects))
        rects.append((bx, by, btn, btn, "toggle", None))
        cy += head_h

        # ---- 大字时间 + 日期 ----
        tw, _ = draw_text(cr, scene.when.strftime("%H:%M"), cx, cy,
                          27 * scale if compact else 34 * scale,
                          (255, 255, 255), 0.97, weight=Pango.Weight.LIGHT)
        weekday = "一二三四五六日"[scene.when.weekday()]
        draw_text(cr, f"{scene.when.month} 月 {scene.when.day} 日 · 周{weekday}"
                      f" · {scene.period_name}",
                  cx + tw + 10 * scale, cy + (11 if compact else 15) * scale,
                  12.5 * scale, (226, 232, 245), 0.62)
        cy += time_h

        # ---- 日弧：日出到日落，此刻在哪儿 ----
        if show_arc:
            ax0 = cx
            aw = card_w - pad * 2
            ah = 5 * scale
            ay = cy + 4 * scale
            f0, f1 = self._day_frac(sunr), self._day_frac(suns)
            cr.set_source_rgba(1, 1, 1, 0.13)
            rounded_rect(cr, ax0, ay, aw, ah, ah / 2)
            cr.fill()
            cr.save()
            rounded_rect(cr, ax0, ay, aw, ah, ah / 2)
            cr.clip()
            if f1 > f0:
                g = cairo.LinearGradient(ax0 + aw * f0, 0, ax0 + aw * f1, 0)
                g.add_color_stop_rgba(0, 1.0, 0.80, 0.45, 0.95)
                g.add_color_stop_rgba(1, accent[0] / 255, accent[1] / 255,
                                      accent[2] / 255, 0.95)
                cr.set_source(g)
                cr.rectangle(ax0 + aw * f0, ay, aw * (f1 - f0), ah)
                cr.fill()
            if self.ui.info_hover_dt is not None:        # 悬停：一条跟随的细游标
                hx = ax0 + aw * clamp(self._day_frac(self.ui.info_hover_dt), 0, 1)
                cr.set_source_rgba(1, 1, 1, 0.85)
                cr.rectangle(hx - 0.7 * scale, ay - 5 * scale, 1.4 * scale,
                             ah + 10 * scale)
                cr.fill()
            mx = ax0 + aw * clamp(self._day_frac(scene.when), 0, 1)
            cr.set_source_rgba(1, 1, 1, 0.95)            # 此刻：一枚小圆点
            cr.arc(mx, ay + ah / 2, ah * 0.92, 0, TAU)
            cr.fill()
            cr.set_source_rgba(0.06, 0.07, 0.11, 0.92)
            cr.arc(mx, ay + ah / 2, ah * 0.42, 0, TAU)
            cr.fill()
            cr.restore()
            ly = ay + ah + 5 * scale
            draw_icon(cr, "sunrise", ax0 + 6 * scale, ly + 6 * scale, 13 * scale,
                      icon_tint("sunrise"), 0.9)
            draw_text(cr, sunr.strftime("%H:%M"), ax0 + 15 * scale, ly, 11 * scale,
                      (246, 240, 232), 0.68)
            tail = (f"还剩 {duration_zh(scene.daylight_left)}"
                    if scene.daylight_left else "今天已过去")
            ttw, _ = draw_text(cr, tail, 0, -1000, 11 * scale, (255, 255, 255), 0.0)
            draw_text(cr, tail, ax0 + aw - ttw, ly, 11 * scale, (250, 250, 255), 0.74)
            rects.append((ax0, ay - 7 * scale, aw, ah + 16 * scale, "arc", None))
            cy += arc_h

        # ---- 一行行事实 ----
        if not compact:
            cr.set_source_rgba(1, 1, 1, 0.09)
            cr.rectangle(cx, cy + 2 * scale, card_w - pad * 2, 1)
            cr.fill()
            cy += div_h
            for row in rows:
                rx = cx - 5 * scale
                rw2 = card_w - pad * 2 + 10 * scale
                rh = row_h - 3 * scale
                ry = cy - 2.5 * scale
                idx = len(rects)
                hovered = hover == idx
                if hovered:
                    cr.set_source_rgba(1, 1, 1, 0.10)
                    rounded_rect(cr, rx, ry, rw2, rh, 8 * scale)
                    cr.fill()
                    cr.set_source_rgba(accent[0] / 255, accent[1] / 255,
                                       accent[2] / 255, 0.9)
                    rounded_rect(cr, rx, ry + 3 * scale, 2.2 * scale, rh - 6 * scale,
                                 1.1 * scale)
                    cr.fill()
                badge = 20 * scale
                bcx, bcy = rx + 5 * scale + badge / 2, cy + badge / 2 + 1 * scale
                cr.set_source_rgba(1, 1, 1, 0.14 if hovered else 0.07)
                rounded_rect(cr, bcx - badge / 2, bcy - badge / 2, badge, badge,
                             badge * 0.34)
                cr.fill()
                draw_icon(cr, row.icon, bcx, bcy, badge * 0.70, icon_tint(row.icon),
                          0.95, phase=scene.moon_phase)
                tx = rx + 5 * scale + badge + 9 * scale
                draw_text(cr, row.label, tx, cy + 1 * scale, 12 * scale,
                          (206, 214, 232), 0.56)
                vx = tx + 40 * scale
                vw, _ = draw_text(cr, row.value, vx, cy + 0.5 * scale, 13 * scale,
                                  (240, 244, 252), 0.94)
                if row.note:
                    # 小窗口里装不下就别硬挤：副值整条不画，也不截半句
                    nw, _ = draw_text(cr, row.note, 0, -1000, 11.5 * scale,
                                      (255, 255, 255), 0.0)
                    # 右边那条"点了能跳过去"的小箭头也要留出位置
                    room = 18 * scale if row.action == "open" else 12 * scale
                    if vx + vw + 7 * scale + nw <= rx + rw2 - room:
                        draw_text(cr, row.note, vx + vw + 7 * scale,
                                  cy + 2.5 * scale, 11.5 * scale,
                                  (214, 222, 238), 0.5)
                if row.action == "open":
                    draw_icon(cr, "chevron-right", rx + rw2 - 9 * scale,
                              cy + badge / 2 + 1 * scale, 12 * scale,
                              (236, 241, 252), 0.32)
                rects.append((rx, ry, rw2, rh, row.action, row.when))
                cy += row_h

        # ---- 一句人话 ----
        cy += 4 * scale
        bar_h = max(16 * scale, 19 * scale * len(hint_lines) - 5 * scale)
        cr.set_source_rgba(accent[0] / 255, accent[1] / 255, accent[2] / 255, 0.85)
        rounded_rect(cr, cx, cy + 1.5 * scale, 2.5 * scale, bar_h, 1.2 * scale)
        cr.fill()
        for i, line in enumerate(hint_lines):
            draw_text(cr, line, cx + 10 * scale, cy + i * 19 * scale, 12.5 * scale,
                      (250, 250, 255), 0.82)
        cy += hint_h

        rb = 20 * scale
        rbx, rby = x + card_w - pad - rb, cy - 5 * scale
        # ---- 脚注：数据从哪来、什么时候问回来的 + 立刻刷新一次 ----
        # 一行里要塞下"来源 + 更新于几点"，窗口窄的时候先让短的顶上来：
        # 与其把字挤到刷新按钮底下（或截半句），不如少说几个字。
        draw_text_room = (x + card_w - pad - rb - 10 * scale) - cx
        foot = self._foot_text(scene, cr, draw_text_room, 10 * scale)
        draw_text(cr, foot, cx, cy, 10 * scale, (200, 210, 230), 0.38)
        self._icon_button(cr, "refresh", rbx + rb / 2, rby + rb / 2, rb,
                          (228, 236, 250), 0.8,
                          hover=hover == len(rects))
        rects.append((rbx - 2 * scale, rby - 2 * scale, rb + 4 * scale,
                      rb + 4 * scale, "refresh", None))
        cr.restore()
        return rects

    @staticmethod
    def _weather_stamp(scene: Scene) -> str:
        """天气是什么时候问回来的——按这扇窗所在地方的时间写。"""
        if not scene.weather_at:
            return ""
        try:
            at = datetime.fromtimestamp(scene.weather_at, scene.when.tzinfo)
        except (OverflowError, OSError, ValueError):
            return ""
        if at.date() == scene.when.date():
            return at.strftime("%H:%M")
        return at.strftime("%m-%d %H:%M")

    def _foot_text(self, scene: Scene, cr, room: float, size: float) -> str:
        """卡片底部那一行：剩多少宽度就说多少话（宁可少说，也不挤到按钮底下）。"""
        stamp = self._weather_stamp(scene)
        if scene.has_weather and scene.weather_stale:
            head = (f"网络不通 · 显示上次天气（{stamp}）" if stamp
                    else "网络不通 · 显示上次天气")
            options = [head, "网络不通 · 上次的天气"]
        elif scene.has_weather and scene.weather_now and stamp:
            options = [f"天文 · 本地计算　　天气更新于 {stamp} · Open-Meteo",
                       f"天气更新于 {stamp} · Open-Meteo",
                       f"天气更新于 {stamp}"]
        elif scene.has_weather and scene.weather_now:
            # 就是此刻那份天气，只是不知道是什么时候问回来的（假数据 / 老缓存）
            options = ["天文 · 本地计算　　天气 · Open-Meteo", "天气 · Open-Meteo"]
        elif scene.has_weather:
            options = ["天文 · 本地计算　　这一天的逐小时预报",
                       "这一天的逐小时预报"]
        elif scene.weather_nodata:
            options = ["天文 · 本地计算　　这天的预报还没问到", "这天的预报还没问到"]
        elif scene.weather_disabled:
            options = ["天文 · 本地计算　　你关掉了天气", "只看天"]
        else:
            options = ["天文 · 本地计算　　天气未接入", "天文 · 本地计算"]
        for text in options[:-1]:
            if draw_text(cr, text, 0, -1000, size, (255, 255, 255), 0.0)[0] <= room:
                return text
        return options[-1]

    # ------------------------------------------------------------------
    def _draw_toast(self, cr, w, h):
        ui = self.ui
        if not ui.toast or _time.time() > ui.toast_until:
            ui.toast_rect = (0.0, 0.0, 0.0, 0.0)
            return
        remain = ui.toast_until - _time.time()
        alpha = clamp(min(1.0, remain / 0.6), 0, 1)
        size = clamp(h * 0.020, 12.0, 18.0)
        text = ui.toast + ("　·　详情" if ui.toast_detail else "")
        tw, th = draw_text(cr, text, 0, -1000, size, (255, 255, 255), 0.0)
        icon = ui.toast_icon or "info"
        bw, bh = tw + 34 + size * 1.7, th + 18
        bx = w / 2 - bw / 2
        by = SILL_Y * h - bh - 22
        cr.set_source_rgba(0.05, 0.06, 0.10, 0.70 * alpha)
        rounded_rect(cr, bx, by, bw, bh, bh / 2)
        cr.fill()
        cr.set_source_rgba(1, 1, 1, 0.12 * alpha)
        rounded_rect(cr, bx, by, bw, bh, bh / 2)
        cr.set_line_width(1)
        cr.stroke()
        tint = {"check": (150, 226, 168), "warn": (255, 198, 120),
                "refresh": (176, 208, 240)}.get(icon, (196, 212, 244))
        draw_icon(cr, icon, bx + bh / 2 + 1, by + bh / 2, size * 0.95, tint,
                  0.95 * alpha)
        draw_text(cr, text, bx + bh / 2 + 6 + size * 0.85, by + 8, size,
                  (250, 251, 255), 0.95 * alpha)
        ui.toast_rect = (bx, by, bw, bh)

    def draw_chip(self, cr, w, h):
        """预览提示（画在最上层，可点击返回此刻）。"""
        ui = self.ui
        if ui.preview_dt is None:
            ui.chip_rect = (0, 0, 0, 0)
            return
        text = f"正在预览 {ui.preview_dt.strftime('%H:%M')}　·　点这里回到此刻"
        size = clamp(h * 0.019, 12.0, 17.0)
        tw, th = draw_text(cr, text, 0, -1000, size, (255, 255, 255), 0.0)
        bw, bh = tw + 36, th + 16
        bx = w / 2 - bw / 2
        by = h * 0.028
        cr.set_source_rgba(0.05, 0.06, 0.10, 0.66)
        rounded_rect(cr, bx, by, bw, bh, bh / 2)
        cr.fill()
        cr.set_source_rgba(1, 1, 1, 0.14)
        rounded_rect(cr, bx, by, bw, bh, bh / 2)
        cr.set_line_width(1)
        cr.stroke()
        draw_text(cr, text, w / 2, by + 7, size, (250, 251, 255), 0.95, align="center")
        ui.chip_rect = (bx, by, bw, bh)
