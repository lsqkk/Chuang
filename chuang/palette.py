"""天色色板：以太阳高度角为唯一驱动量，插值出天顶、地平、辉光与月光。

所有颜色都按线性光空间插值（避免 sRGB 直插产生的灰浊），
因此日落时橙→紫→深蓝的过渡是连续的、干净的。
"""

from __future__ import annotations

from dataclasses import dataclass


def _srgb_to_linear(c: float) -> float:
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(c: float) -> float:
    c = max(0.0, min(1.0, c))
    v = c * 12.92 if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055
    return v * 255.0


def hex_to_rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(float(int(h[i:i + 2], 16)) for i in (0, 2, 4))  # type: ignore


def rgb_to_hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(int(max(0, min(255, round(c)))) for c in rgb)


def mix(a, b, t: float):
    """在线性光空间混合两种颜色。"""
    t = max(0.0, min(1.0, t))
    la = [_srgb_to_linear(c) for c in a]
    lb = [_srgb_to_linear(c) for c in b]
    return tuple(_linear_to_srgb(la[i] + (lb[i] - la[i]) * t) for i in range(3))


def mix_rgb(a, b, t: float):
    """在线性光空间混合（输入为 0-1 浮点）。"""
    t = max(0.0, min(1.0, t))
    la = [_srgb_to_linear(c * 255.0) for c in a]
    lb = [_srgb_to_linear(c * 255.0) for c in b]
    return tuple(_linear_to_srgb(la[i] + (lb[i] - la[i]) * t) / 255.0 for i in range(3))


def shade(color, factor: float):
    """在光空间整体提亮/压暗（factor=1 不变）。"""
    lin = [_srgb_to_linear(c * 255.0) * factor for c in color]
    return tuple(_linear_to_srgb(c) / 255.0 for c in lin)


@dataclass(frozen=True)
class Mood:
    """由太阳高度角推出的一组"此刻天色"参数。"""

    zenith: tuple[float, float, float]
    horizon: tuple[float, float, float]
    glow_color: tuple[float, float, float]
    glow_strength: float          # 地平附近太阳辉光强度 0-1
    star_alpha: float             # 星空可见度 0-1
    ambient: float                # 地面/窗台受光强度 0-1
    sun_light: float              # 直射阳光强度 0-1（决定影子浓度）
    key: str                      # 时段名称
    summary: str                  # 一句人话


# (太阳高度角, 天顶色, 地平色, 辉光色, 辉光强度, 星空, 环境光, 直射光, 时段名)
_KEYFRAMES = [
    (90, "#1a4e93", "#c8ddf2", "#fff4d8", 0.45, 0.0, 1.00, 1.00, "正午"),
    (45, "#1e5aa6", "#cfe2f4", "#fff0cf", 0.50, 0.0, 1.00, 1.00, "白昼"),
    (18, "#2666b4", "#d6e8f7", "#ffe9c2", 0.58, 0.0, 1.00, 1.00, "白昼"),
    (8, "#2b5f9e", "#e6dcbd", "#ffd79a", 0.70, 0.0, 0.98, 0.92, "斜阳"),
    (3, "#314d84", "#f7ab63", "#ffb45e", 0.88, 0.0, 0.86, 0.70, "金色时刻"),
    (0, "#2c3f6b", "#ff8a49", "#ff7c37", 1.00, 0.02, 0.66, 0.35, "日落"),
    (-2, "#26325c", "#e0674a", "#ee5c3c", 0.92, 0.06, 0.46, 0.10, "日落"),
    (-4, "#1d2a52", "#a8506a", "#d4553f", 0.72, 0.16, 0.32, 0.02, "暮色"),
    (-6, "#151f46", "#6e4272", "#a94a55", 0.48, 0.32, 0.22, 0.0, "民用暮光"),
    (-9, "#0e163a", "#3d3268", "#6a3f6e", 0.30, 0.52, 0.15, 0.0, "航海暮光"),
    (-12, "#0a1130", "#242a56", "#43508c", 0.18, 0.72, 0.11, 0.0, "航海暮光"),
    (-15, "#070d26", "#171d40", "#33406f", 0.10, 0.86, 0.08, 0.0, "天文暮光"),
    (-18, "#050a1e", "#0e1433", "#232c52", 0.06, 0.95, 0.06, 0.0, "天光将尽"),
    (-90, "#030713", "#080e22", "#151d3c", 0.04, 1.00, 0.05, 0.0, "夜"),
]


def mood_for(sun_alt: float, moon_light: float = 0.0) -> Mood:
    """由太阳高度角插值出这一刻的天色参数。"""
    kfs = _KEYFRAMES
    if sun_alt >= kfs[0][0]:
        lo, hi, t = kfs[0], kfs[0], 0.0
    elif sun_alt <= kfs[-1][0]:
        lo, hi, t = kfs[-1], kfs[-1], 0.0
    else:
        for i in range(len(kfs) - 1):
            a, b = kfs[i], kfs[i + 1]
            if b[0] <= sun_alt <= a[0]:
                lo, hi = a, b
                span = a[0] - b[0]
                t = 0.0 if span == 0 else (a[0] - sun_alt) / span
                break
        else:
            lo = hi = kfs[-1]
            t = 0.0

    zen = mix(hex_to_rgb(lo[1]), hex_to_rgb(hi[1]), t)
    hor = mix(hex_to_rgb(lo[2]), hex_to_rgb(hi[2]), t)
    glow = mix(hex_to_rgb(lo[3]), hex_to_rgb(hi[3]), t)
    lerp = lambda i: lo[i] + (hi[i] - lo[i]) * t  # noqa: E731
    star_alpha = lerp(5)
    ambient = lerp(6)
    sun_light = lerp(7)
    key = lo[8] if t < 0.5 else hi[8]

    # 月光会整体抬起夜空亮度，并略微降低星空的对比度
    if moon_light > 0 and sun_alt < 6:
        lift = moon_light * min(1.0, (-sun_alt + 8) / 26.0)
        moon_tint = hex_to_rgb("#6f8ec6")
        zen = mix_rgb(tuple(c / 255 for c in zen), tuple(c / 255 for c in moon_tint), 0.07 * lift)
        zen = tuple(c * 255 for c in zen)
        hor = mix_rgb(tuple(c / 255 for c in hor), tuple(c / 255 for c in moon_tint), 0.10 * lift)
        hor = tuple(c * 255 for c in hor)
        ambient = ambient + (0.20 - ambient) * lift * 0.75
        star_alpha = star_alpha * (1 - 0.28 * lift)

    return Mood(
        zenith=zen,
        horizon=hor,
        glow_color=glow,
        glow_strength=lerp(4),
        star_alpha=star_alpha,
        ambient=ambient,
        sun_light=sun_light,
        key=key,
        summary="",
    )


def ribbon_color(sun_alt: float, cloud_cover: float, moon_light: float = 0.0):
    """「今日天色」长卷上某一刻的代表色。

    取天顶与地平之间 62% 处的颜色（相当于抬头看到的天空），
    再按云量混入云的灰——所以阴天的那一段长卷会自然地灰掉。
    """
    mood = mood_for(sun_alt, moon_light)
    mid = mix(mood.zenith, mood.horizon, 0.45)
    cover = max(0.0, min(1.0, cloud_cover / 100.0))
    # 云在白天偏白、夜里偏暗，这里统一按"云底亮度"处理
    cloud_lum = 0.62 + 0.30 * max(0.0, min(1.0, (sun_alt + 8) / 30.0))
    cloud_tone = tuple(255 * cloud_lum * c for c in (0.86, 0.88, 0.92))
    if sun_alt < 2:
        cloud_tone = tuple(c * (0.55 + 0.35 * max(0.0, (sun_alt + 6) / 8)) for c in cloud_tone)
    return mix(mid, cloud_tone, cover * 0.72)
