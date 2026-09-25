
"""绘制的最底层：纵向排版、颜色与几何小工具、字体与文字、矢量小图标。

这一层**不认识 Scene，也不认识那一帧**：只跟 cairo 上下文、字号与坐标打交道。
每枚图标都是一笔一笔画的，不引素材也不引图标字体（见 DESIGN.md），所以跟着窗口
缩放不会糊。

几件踩过的事都在这里，改之前先读注释：

* `_ink_cr()` 量字用的是离屏 cairo 上下文，**不能**改成
  `PangoCairo.font_map_get_default()`（那会让之后 import 的 `gi.repository.Gio`
  加载不出 override，见函数里的说明）；
* "这个字看起来齐不齐"全靠 `text_ink` / `ink_baseline` / `icon_ink_y` 这一组，
  别在画的地方再补经验值（1.1.14 那两个 0.36 / 0.38 就是这么翻的车）。
"""

from __future__ import annotations

import math
import random
import threading

import cairo
import gi

gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo  # noqa: E402


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


_NOISE_TILE: cairo.ImageSurface | None = None


def noise_tile(size: int = 96) -> cairo.ImageSurface:
    """一张"抖动"用的噪声贴片：把渐变的色带打散成看不出的颗粒。

    Cairo 的线性渐变在 8 位色深上会显出一条条色带（天刚黑那阵最明显，
    天顶到地平线一圈一圈的）。盖一层**±1 个色阶**的随机噪声就没了：
    强度只有 1/255，肉眼看不见，但足够把量化误差打散。
    整张图只生成一次，之后每帧只是贴上去（而且贴在天色那张缓存里，更便宜）。
    """
    global _NOISE_TILE
    if _NOISE_TILE is not None:
        return _NOISE_TILE
    rnd = random.Random(20260924)
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    stride = surf.get_stride()
    buf = bytearray(stride * size)
    for i in range(size * size):
        pick = rnd.random()
        off = (i // size) * stride + (i % size) * 4
        if pick < 0.34:                     # 亮一格（预乘：R=G=B=A=1）
            buf[off] = buf[off + 1] = buf[off + 2] = buf[off + 3] = 1
        elif pick < 0.68:                   # 暗一格
            buf[off + 3] = 1
        # 其余留全透明：本来就该是什么色就是什么色
    memoryview(surf.get_data()).cast("B")[:] = buf
    surf.mark_dirty()
    _NOISE_TILE = surf
    return surf


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


# 项目里所有的字都走这一份字体表（`font()` 与下面量墨迹的 `text_ink()` 必须
# 是同一份，否则"量出来的"与"画出来的"就对不上了）
_FONT_FAMILY = ("Noto Sans CJK SC, Source Han Sans SC, WenQuanYi Micro Hei, "
                "sans-serif")


def font(cr, size, weights=Pango.Weight.NORMAL):
    layout = PangoCairo.create_layout(cr)
    desc = Pango.FontDescription(_FONT_FAMILY)
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


_ASCENT_CACHE: dict = {}


def text_ascent(cr, size, weight=Pango.Weight.NORMAL) -> float:
    """这段字体的**基线**离文本框顶边有多远（按基线排版时用它换算 y）。

    两个不同字号的字如果都按"文本框左上角"对齐，基线就会错开几个像素——看上去
    就是"有的偏上、有的偏下"（信息卡 1.1.13 第一版就是这样，用户一眼就看出来了）。
    小字号的基线给大字号的基线让位，是一行字看着"齐"的全部秘密。
    """
    key = (round(size, 1), int(weight))
    value = _ASCENT_CACHE.get(key)
    if value is None:
        layout = font(cr, size, weight)
        layout.set_text("汉Ag", -1)
        value = layout.get_baseline() / Pango.SCALE
        if len(_ASCENT_CACHE) > 64:
            _ASCENT_CACHE.clear()
        _ASCENT_CACHE[key] = value
    return value


def draw_text_bl(cr, text, x, baseline, size, color, alpha=1.0,
                 weight=Pango.Weight.NORMAL, align="left"):
    """按**基线**画一行字：同一条基线上的不同字号看起来才是齐的。"""
    return draw_text(cr, text, x, baseline - text_ascent(cr, size, weight),
                     size, color, alpha, weight, align)


_INK_LOCAL = threading.local()
_INK_CACHE: dict = {}


def _ink_cr():
    """量字用的 cairo 上下文：一张 1×1 的离屏图，只用它跑 Pango 量宽度与墨迹。

    **别改用 `PangoCairo.font_map_get_default()`**：那个调用会让**之后**才 import
    的 `gi.repository.Gio` 加载不出 override（PyGObject 的一个坑，本机复现：
    `font_map_get_default()` 之后 `from chuang.infocard import ...` 直接抛
    "Can not override a type ListModel, which is not in a gobject introspection
    typelib"；`tests/test_info_card.py` 就是这么翻车的）。走 cairo 上下文没这个
    问题，而且与画的时候（`font(cr, ...)`）用的是同一个字体图，量出来一模一样。

    每个线程一份：壁纸那一路是在**渲染线程**里画卡片的（见 wallpaper.render），
    而 cairo 上下文不是线程安全的东西，共用一份迟早会出怪事。
    """
    cr = getattr(_INK_LOCAL, "cr", None)
    if cr is None:
        cr = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1))
        _INK_LOCAL.cr = cr
    return cr


def text_ink(text: str, size: float, weight=Pango.Weight.NORMAL) -> tuple[float, float]:
    """一行字**真正着墨**的那一块：相对基线的 (上缘, 下缘)（上缘是负数）。

    Pango 的文本框（logical rect）是按字体的 ascent / descent 算的，里面既有
    "19°"上边空着的一大截，也有"毛毛雨"底下多出来的那一点。要让两行字"看着齐"、
    要让一枚图标跟旁边的大字"看着齐"，都得按着墨的这一块算中线——1.1.14 第二版
    那两个"0.36 / 0.38"的经验值就差了 3~4 像素（用户第二次说的"没有上下居中"）。
    字号差得大时用 `ink_baseline` 让两行的墨迹中线重合；字号差不多时（指标格里
    的"太阳 / 南 173°"）**共用基线**更自然——两者的墨迹中线本来就差不到 1 像素。
    """
    key = (text, round(size, 2), int(weight))
    hit = _INK_CACHE.get(key)
    if hit is not None:
        return hit
    try:
        layout = font(_ink_cr(), size, weight)
        layout.set_text(text or " ", -1)
        ink, _logical = layout.get_pixel_extents()
        base = layout.get_baseline() / Pango.SCALE
        value = (float(ink.y) - base, float(ink.y + ink.height) - base)
    except Exception:                    # noqa: BLE001 - 量不出来就退回字面高度
        value = (-0.72 * size, 0.0)
    if len(_INK_CACHE) > 240:
        _INK_CACHE.clear()
    _INK_CACHE[key] = value
    return value


def ink_baseline(center: float, text: str, size: float,
                 weight=Pango.Weight.NORMAL) -> float:
    """把这一行字的**墨迹中线**放在 center 上，返回它的基线（给 draw_text_bl）。"""
    top, bottom = text_ink(text, size, weight)
    return center - (top + bottom) / 2.0


# 每枚图标的墨迹中线相对**方框中心**偏多少（单位：半径 r）。
# 用 200 磅的图逐个量过（画进离屏图取 alpha 的包围盒）：云、风、雪这几枚的墨
# 偏在上半部（雨丝与风钩挂在下边、云的底又是平的），不补这一下，图标跟旁边那行
# 大字就差着两三个像素——用户说的"没有上下居中"里有它一份。
_ICON_INK_BIAS = {
    "cloud": -0.135, "wind": -0.205, "snow": -0.055, "rain": 0.010,
    "sunrise": -0.330, "sunset": 0.090, "refresh": -0.130,
}


def icon_ink_y(kind: str, center: float, size: float) -> float:
    """要让这枚图标的**墨迹**中线落在 center 上，该给 draw_icon 的中心 y。"""
    return center - _ICON_INK_BIAS.get(kind, 0.0) * (size / 2.0)


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
