
"""WMO 天气码 → 人话，以及"雨下得有多大"这套换算。

雨量（mm/时）要过一遍**对数刻度**才拿来画：雨丝的条数、长度、速度、透明度、
有没有那层"雨幕"全看它（`precip_strength`），说"多大"则用 `precip_label`
（毛毛雨 / 小雨 / 中雨…）——不能再拿代码表当雨强，那会让同一份数据里
"中雨"和"小雨"自己打架（AGENTS.md §3.6）。毛毛雨那一族封顶，接口给的数再离谱
也不画成倾盆。
"""

from __future__ import annotations

import math


WMO_TEXT = {
    0: "晴", 1: "大致晴朗", 2: "多云", 3: "阴",
    45: "有雾", 48: "雾凇",
    51: "细微毛毛雨", 53: "毛毛雨", 55: "密毛毛雨",
    56: "冻毛毛雨", 57: "强冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "强冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "米雪",
    80: "阵雨", 81: "强阵雨", 82: "暴阵雨",
    85: "阵雪", 86: "强阵雪",
    95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "强雷暴伴冰雹",
}


def code_text(code: int) -> str:
    return WMO_TEXT.get(int(code), "未知")


# 各档降水的"底子强度"（0-1）：只有在拿不到真实雨量时才单独用它。
# 数字是按"看起来多猛"排的：毛毛雨要明显轻于大雨，雷阵雨最重。
_PRECIP_BASE = {
    51: 0.10, 53: 0.17, 55: 0.26, 56: 0.17, 57: 0.26,      # 毛毛雨 / 冻毛毛雨
    61: 0.30, 63: 0.50, 65: 0.78, 66: 0.50, 67: 0.78,      # 小雨 / 中雨 / 大雨
    71: 0.22, 73: 0.42, 75: 0.66, 77: 0.16,                # 雪
    80: 0.36, 81: 0.58, 82: 0.92, 85: 0.44, 86: 0.70,      # 阵雨 / 阵雪
    95: 0.62, 96: 0.85, 99: 1.00,                          # 雷雨
}
# 毛毛雨那一族（含冻毛毛雨）：它们再怎么算也不该画成大雨
DRIZZLE_CODES = frozenset({51, 53, 55, 56, 57})

# 雨量 → 强度 的刻度点（mm/时, 0-1），中间按对数插值：
# 0.1 mm/时 是"飘着几丝"，1 是小雨，8 是让人眯眼的雨，25 以上满格。
_PRECIP_CURVE = (
    (0.02, 0.03), (0.05, 0.07), (0.10, 0.13), (0.20, 0.20),
    (0.50, 0.30), (1.00, 0.40), (2.00, 0.52), (4.00, 0.64),
    (8.00, 0.78), (15.0, 0.90), (25.0, 1.00),
)


def clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def precip_intensity(precip_mm: float) -> float:
    """降水量（mm/时）→ 0-1 的强度。跨数量级的东西要按对数看。"""
    try:
        mm = float(precip_mm or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if mm <= 0.0:
        return 0.0
    lo_mm, lo_v = _PRECIP_CURVE[0]
    if mm < lo_mm:                      # 比"飘几丝"还小：按比例收着
        return clamp01(lo_v * mm / lo_mm)
    for (m0, v0), (m1, v1) in zip(_PRECIP_CURVE, _PRECIP_CURVE[1:]):
        if mm <= m1:
            span = math.log10(m1) - math.log10(m0)
            t = (math.log10(mm) - math.log10(m0)) / span if span else 0.0
            return clamp01(v0 + (v1 - v0) * t)
    return 1.0


def precip_label(code: int, precip_mm: float = 0.0) -> str:
    """这一刻的雨有多大——**按真实雨量说话**，没有雨量才退回代码上的说法。

    这是用户看得见的那句话（信息卡、详情、长卷悬停）。以前只有 WMO 代码，
    于是"毛毛雨"和"大雨"配的文字都来自代码表；雨量既然在手上，就按雨强分档说。
    分档（mm/时）刻意和 WMO 那几档对齐，免得同一份数据里"中雨"和"小雨"
    自己打架：<0.2 毛毛雨（毛毛雨那一族 <0.6）、≤1.2 小雨、≤4.5 中雨、
    ≤12 大雨、再往上暴雨。
    """
    n = int(code)
    kind = precip_kind(n)
    if kind == "none":
        return ""
    if kind == "snow":
        return code_text(n)             # 雪不按"雨量"分档，照代码说
    if n in (95, 96, 99):
        return code_text(n)             # 雷阵雨（可能带冰雹）：照实说
    try:
        mm = max(0.0, float(precip_mm or 0.0))
    except (TypeError, ValueError):
        mm = 0.0
    if mm <= 0.0:
        return code_text(n)             # 没有雨量：代码说是什么就是什么
    if mm < 0.2 or (n in DRIZZLE_CODES and mm < 0.6):
        return "毛毛雨"
    if mm <= 1.2:
        return "小雨"
    if mm <= 4.5:
        return "中雨"
    if mm <= 12.0:
        return "大雨"
    return "暴雨"


def precip_kind(code: int) -> str:
    """降水类型：none / rain / snow。"""
    code = int(code)
    if code in (71, 73, 75, 77, 85, 86):
        return "snow"
    if code in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99):
        return "rain"
    return "none"


def uv_text(index) -> str:
    """紫外线指数说人话：数字 + 一档强度（WHO 那套分档：3 / 6 / 8 / 11）。

    接口里一直有这个数，以前拿到手就扔了——可它是"要不要拉窗帘"这件事里
    唯一说得清的数字。
    """
    try:
        v = max(0.0, float(index))
    except (TypeError, ValueError):
        return "—"
    if v < 3:
        level = "弱"
    elif v < 6:
        level = "中等"
    elif v < 8:
        level = "强"
    elif v < 11:
        level = "很强"
    else:
        level = "极强"
    return f"{v:.0f} {level}"


def precip_strength(code: int, precip_mm: float = 0.0) -> float:
    """降水强度 0-1：雨丝的密度、长度、速度、透明度都看它。

    两条腿走路（以前只看 WMO 代码表，于是"毛毛雨"和"大雨"在画面上差不了多少）：

    * **真实降水量**（mm/时）是主：雨量本身跨三个数量级（毛毛雨 0.1、
      中雨 3、暴雨 30+），线性的刻度既不像画面也不像感觉，所以过一遍
      对数刻度（见 `precip_intensity`）。
    * WMO 代码只当"这一档大概多强"的**下限**：接口偶尔把雨量四舍五入成 0
      （51 那档经常是 0.0），这时候至少还有代码兜着。

    毛毛雨那一档另外封顶 0.34——用户看到的就是"毛毛雨"，不能画成大雨。
    """
    n = int(code)
    base = _PRECIP_BASE.get(n, 0.0)
    amount = precip_intensity(precip_mm)
    if amount <= 0.0:
        value = base                    # 没有雨量数字：以代码为准
    else:
        value = max(amount, base * 0.7)   # 以雨量为准，代码兜住下限
    if n in DRIZZLE_CODES:
        value = min(value, 0.34)
    return clamp01(value)


def is_thunder(code: int) -> bool:
    return int(code) >= 95


def is_fog(code: int) -> bool:
    return int(code) in (45, 48)
