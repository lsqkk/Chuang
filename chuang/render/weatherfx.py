
"""云的精灵图与天气粒子：云、雨、雪、闪电、偶尔飞过的飞机。

状态全部归一化到窗口的 0-1 坐标（`sync()` 决定这一场雨要多少雨丝，`advance()`
每帧往前推一步），所以窗口怎么缩放都不影响它。两条硬约束写在函数里的注释里：
**粒子重建的 key 里必须带雨量**、**云的层次也要进 key**（AGENTS.md §3.6）。
"""

from __future__ import annotations

import math
import random

import cairo

from ..scene import Scene
from .paint import TAU, clamp, lerp



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
        self.splashes: list[list[float]] = []   # 落在马路上的雨点涟漪
        self.flash = 0.0
        self.next_flash = 0.0
        self.plane: dict | None = None
        self.next_plane = 0.0
        self.elapsed = 0.0
        self.signature = None

    def sync(self, scene: Scene, now: float) -> None:
        """场景（云量/天气/风/雨量）变了就重建粒子。"""
        kind = scene.precip_kind if scene.has_weather else "none"
        strength = scene.precip_strength if scene.has_weather else 0.0
        # **雨量也要进 key**：以前只有天气代码与云量。同一档雨下着下着变大了
        # （代码还是 63、降水量从 1 涨到 5），雨丝一根都不会多——画面看着
        # "毛毛雨和大雨差不多"，有一半是这里来的。
        sig = (round(scene.cloud / 4), scene.code, round(scene.wind_speed),
               round(scene.wind_dir / 10), kind, round(strength * 12),
               # 云的**层次**也要进 key：低云满、高云空 和 反过来，画出来不一样
               round(scene.cloud_low / 8), round(scene.cloud_mid / 8),
               round(scene.cloud_high / 8))
        if sig == self.signature:
            return
        self.signature = sig
        rnd = self.rnd
        cover = clamp(scene.cloud / 100.0, 0.0, 1.0)
        if scene.has_weather and scene.fog:
            cover = max(cover, 0.85)
        # 三层的量各算各的：接口给了分层云量就用真的（高层的卷云薄、低层的
        # 层云厚），没给（老缓存 / 假天气）就按原来那套比例从总云量里分。
        low = clamp(getattr(scene, "cloud_low", 0.0) / 100.0, 0.0, 1.0)
        mid = clamp(getattr(scene, "cloud_mid", 0.0) / 100.0, 0.0, 1.0)
        high = clamp(getattr(scene, "cloud_high", 0.0) / 100.0, 0.0, 1.0)
        if max(low, mid, high) <= 0.0:
            low, mid, high = cover, 0.84 * cover, 0.34 * cover
        cover = max(cover, low, mid, high)
        layers = [
            ("high", high, 30, (28, 74), (0.30, 0.52), 2.1),
            ("mid", mid, 22, (6, 40), (0.52, 0.84), 1.0),
            ("low", low, 18, (0, 13), (0.64, 1.00), 0.55),
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

        self.drops = []
        self.flakes = []
        self.splashes = []
        if kind == "rain" and strength > 0:
            # 雨丝：数量随雨量陡涨（毛毛雨是"飘着几丝"，暴雨才是"满窗都是"），
            # 并且分成远近两层——远处的细、短、暗、慢，近处的粗、长、亮、快。
            # 全都一样的时候，一团等宽的灰线看着就是"雨有点糙"。
            n = int(16 + 520 * strength ** 1.35)
            for _ in range(n):
                depth = rnd.random() ** 1.5           # 0 远 → 1 近
                self.drops.append([
                    rnd.random(), rnd.random(),
                    (0.35 + 0.95 * depth) * rnd.uniform(0.8, 1.3),   # 长度倍数
                    (0.55 + 0.75 * depth) * rnd.uniform(0.85, 1.2),  # 速度倍数
                    (0.30 + 0.60 * depth) * rnd.uniform(0.75, 1.1),  # 透明度
                    (0.45 + 2.0 * depth) * rnd.uniform(0.8, 1.25),   # 粗细
                    rnd.uniform(0, TAU),                             # 摇摆相位
                ])
            # 雨点砸在湿马路上溅开的小涟漪（窗外的地面，不是屋里那条窗台）
            for _ in range(int(8 + 46 * strength)):
                self.splashes.append([rnd.random(), rnd.random(),
                                      rnd.uniform(0.55, 1.35)])
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

        # 雨丝下落的速度也跟着雨量走：毛毛雨是慢慢飘，暴雨是砸下来
        fall = 0.28 + 0.55 * (scene.precip_strength if scene.has_weather else 0.0)
        for d in self.drops:
            d[1] += d[3] * dt * fall
            d[0] += drift * d[3] * dt * 0.10
            if d[1] > 1.02:
                d[1] = -0.03
                d[0] = self.rnd.random()
            if d[0] > 1.03:
                d[0] -= 1.06
            elif d[0] < -0.03:
                d[0] += 1.06

        splash_speed = 0.9 + 1.6 * (scene.precip_strength if scene.has_weather else 0.0)
        for s in self.splashes:
            s[1] += dt * splash_speed
            if s[1] >= 1.0:
                s[1] -= 1.0
                s[0] = self.rnd.random()

        for f in self.flakes:
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
