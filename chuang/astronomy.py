"""天文计算：太阳、月亮、恒星。纯本地数学，无网络依赖。

太阳位置采用 NOAA Solar Calculator 的算法（精度约 ±0.01°）；
月亮位置采用 Meeus《Astronomical Algorithms》第 47 章截断版（精度约 ±10"）。
这些精度对"肉眼看到的天"来说远远足够。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

DEG = math.pi / 180.0
RAD = 180.0 / math.pi

# 地球轨道与大气常数
_SUN_RISE_ALT = -0.833  # 太阳上边缘触地平（含大气折射与日面半径）
_GOLDEN_ALT = 6.0
_CIVIL_ALT = -6.0
_NAUTICAL_ALT = -12.0
_ASTRONOMICAL_ALT = -18.0
_MOON_RISE_ALT = 0.125  # 含折射与视差后的近似值


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------

def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.astimezone(timezone.utc)
    return dt.astimezone(timezone.utc)


def julian_day(dt_utc: datetime) -> float:
    """公历日期 → 儒略日（Meeus 7.1）。任何带时区的时刻都会被先换算为 UTC。"""
    dt_utc = to_utc(dt_utc)
    y, m = dt_utc.year, dt_utc.month
    d = (dt_utc.day
         + (dt_utc.hour + (dt_utc.minute + (dt_utc.second + dt_utc.microsecond / 1e6) / 60.0) / 60.0) / 24.0)
    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    return (math.floor(365.25 * (y + 4716)) + math.floor(30.6001 * (m + 1))
            + d + b - 1524.5)


def julian_centuries(jd: float) -> float:
    return (jd - 2451545.0) / 36525.0


def norm360(x: float) -> float:
    return x % 360.0


def wrap180(x: float) -> float:
    return (x + 180.0) % 360.0 - 180.0


def obliquity(t: float) -> float:
    """黄赤交角（度，含修正项）。"""
    e0 = (23.0 + 26.0 / 60.0 + 21.448 / 3600.0
          - (46.8150 * t + 0.00059 * t * t - 0.001813 * t ** 3) / 3600.0)
    return e0 + 0.00256 * math.cos((125.04 - 1934.136 * t) * DEG)


def sidereal_time_deg(jd: float, lon: float) -> float:
    """本地恒星时（度）。"""
    t = julian_centuries(jd)
    theta = (280.46061837 + 360.98564736629 * (jd - 2451545.0)
             + 0.000387933 * t * t - t ** 3 / 38710000.0)
    return norm360(theta + lon)


def _altaz_from_radec(ra_deg: float, dec_deg: float, lat: float, lon: float, jd: float):
    """赤道坐标 → 地平坐标（含大气折射修正）。返回 (alt, az)。"""
    lst = sidereal_time_deg(jd, lon)
    h = (lst - ra_deg) * DEG           # 时角
    dec = dec_deg * DEG
    phi = lat * DEG
    sin_alt = math.sin(phi) * math.sin(dec) + math.cos(phi) * math.cos(dec) * math.cos(h)
    sin_alt = max(-1.0, min(1.0, sin_alt))
    alt = math.asin(sin_alt)
    # 方位角：从北起顺时针
    y = -math.cos(dec) * math.sin(h)
    x = math.sin(dec) * math.cos(phi) - math.cos(dec) * math.sin(phi) * math.cos(h)
    az = math.atan2(y, x)
    alt_d = alt * RAD
    az_d = norm360(az * RAD)
    return alt_d + refraction(alt_d), az_d


def refraction(alt_deg: float) -> float:
    """大气折射修正（度），Bennett 公式。"""
    if alt_deg < -2.0:
        return 0.0
    r = 1.02 / math.tan((alt_deg + 10.3 / (alt_deg + 5.11)) * DEG)
    return r / 60.0


# --------------------------------------------------------------------------
# 太阳
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Equatorial:
    ra: float          # 赤经（度）
    dec: float         # 赤纬（度）
    distance: float    # 距离（km）
    app_longitude: float = 0.0


def sun_equatorial(jd: float) -> Equatorial:
    t = julian_centuries(jd)
    l0 = norm360(280.46646 + t * (36000.76983 + t * 0.0003032))
    m = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    m_r = m * DEG
    c = (math.sin(m_r) * (1.914602 - t * (0.004817 + 0.000014 * t))
         + math.sin(2 * m_r) * (0.019993 - 0.000101 * t)
         + math.sin(3 * m_r) * 0.000289)
    true_lon = l0 + c
    omega = 125.04 - 1934.136 * t
    app_lon = true_lon - 0.00569 - 0.00478 * math.sin(omega * DEG)
    eps = obliquity(t) + 0.00256 * math.cos(omega * DEG)
    lam = app_lon * DEG
    eps_r = eps * DEG
    ra = math.atan2(math.cos(eps_r) * math.sin(lam), math.cos(lam)) * RAD
    dec = math.asin(math.sin(eps_r) * math.sin(lam)) * RAD
    # 距离（AU → km）
    v = m_r + c * DEG
    r_au = (1.000001018 * (1 - e * e)) / (1 + e * math.cos(v))
    return Equatorial(norm360(ra), dec, r_au * 149597870.7, app_lon)


def sun_altaz(when_utc: datetime, lat: float, lon: float):
    jd = julian_day(to_utc(when_utc))
    eq = sun_equatorial(jd)
    return _altaz_from_radec(eq.ra, eq.dec, lat, lon, jd)


def solar_noon(when_local_date: datetime, lat: float, lon: float) -> datetime:
    """用简单迭代求当天太阳过中天时刻（本地 naive datetime）。"""
    base = when_local_date.replace(hour=12, minute=0, second=0, microsecond=0)
    for _ in range(3):
        _, az = sun_altaz(to_utc(base), lat, lon)
        delta_min = wrap180(180.0 - az) / 15.0 * 60.0  # 每 15° 一小时
        base = base + timedelta(minutes=delta_min)
    return base


# --------------------------------------------------------------------------
# 月亮
# --------------------------------------------------------------------------

_MOON_LR = (
    # (l 系数, r 系数, M', M, F, D 的倍数)  —— Meeus 表 47.A 主要项
    (6288774, -20905355, 0, 0, 0, 0),
    (1274027, -3699111, 2, 0, 0, 0),
    (658314, -2955968, 0, 0, 2, 0),
    (213618, -569925, 0, 0, 0, 2),
    (-185116, 48888, 0, 1, 0, 0),
    (-114332, -3149, 0, 0, 0, 4),
    (58793, 246158, 2, 0, -2, 0),
    (57066, -152138, 0, 0, 2, -2),
    (53322, -170733, 2, 0, 0, -2),
    (45758, -204586, 0, 0, 2, 2),
    (-40923, -129620, 0, 1, 0, -2),
    (-34720, 108743, 2, 1, 0, 0),
    (-30383, 104755, 0, 1, 2, 0),
    (15327, 10321, 2, 0, 0, 2),
    (-12528, 0, 0, 0, 4, 0),
    (10980, 79661, 0, 0, 4, -2),
    (10675, -34782, 4, 0, 0, 0),
    (10034, -23210, 2, 2, 0, 0),
    (8548, -21636, 2, 0, -2, 2),
    (-7888, 24208, 0, 0, 2, -4),
    (-6766, 30824, 2, 0, 2, -2),
    (-5163, -8379, 0, 2, 0, -2),
    (4987, -16675, 2, 2, -2, 0),
    (4036, -12831, 2, 0, 4, -2),
    (3994, -10445, 0, 2, 2, 0),
    (3861, -11650, 2, 0, 0, 4),
    (3665, 14403, 0, 1, 4, 0),
    (-2689, -7003, 0, 0, 4, 2),
    (-2602, 0, 4, 0, 2, 0),
    (2390, 10056, 2, 2, 0, -2),
    (-2348, 6322, 4, 1, 0, 0),
    (2236, -9884, 0, 0, 0, 6),
    (-2120, 5751, 0, 1, 0, 4),
    (-2069, 0, 4, 1, -2, 0),
    (2048, -4950, 2, 0, 2, 0),
    (-1773, 4130, 0, 0, 2, 4),
    (-1595, 0, 2, 0, 6, 0),
    (1215, -3958, 4, 0, -2, 0),
    (-1110, 0, 0, 0, 6, -2),
    (-892, 3258, 2, 0, 4, 2),
    (-810, 2616, 4, 0, 2, 2),
    (759, -1897, 0, 2, 0, 4),
    (-713, -2117, 0, 1, 6, 0),
    (-700, 2354, 0, 0, 6, 2),
    (691, 0, 2, 0, 8, 0),
    (596, 0, 0, 0, 2, 8),
    (549, -1423, 2, 0, -2, 6),
    (537, -1117, 4, 0, -4, 0),
    (520, -1571, 0, 1, -2, 4),
    (-487, -1739, 0, 0, 0, 8),
    (-399, 0, 2, 1, 0, 2),
    (-381, -4421, 4, 0, 2, 0),
    (351, 0, 0, 0, 8, -2),
    (-340, 0, 2, 1, 2, 0),
    (330, 0, 0, 2, 0, -2),
    (327, 0, 4, 0, 0, -4),
    (-323, 1165, 2, 0, -4, 2),
    (299, 0, 0, 0, 4, -6),
    (294, 0, 0, 2, 4, 0),
)

_MOON_B = (
    (5128122, 0, 0, 0, 0),
    (280602, 0, 0, 2, 0),
    (277693, 0, 1, 0, 0),
    (173237, 0, 0, 2, -2),
    (55413, 0, 0, 2, -4),
    (46271, 0, 0, 0, 4),
    (32573, 2, 0, 0, 0),
    (17198, 0, 0, 2, 2),
    (9266, 0, 0, 4, 0),
    (8822, 0, 0, 4, -2),
    (8216, 4, 0, 0, 0),
    (4324, 0, 0, 2, -6),
    (4200, 0, 0, 2, -2),
    (-3359, 2, 0, -2, 0),
    (2463, 2, 1, 0, 0),
    (2211, 0, 1, 2, 0),
    (2065, 0, 2, 0, 0),
    (-1870, 0, 0, 0, 6),
    (1828, 2, 1, 0, -2),
    (-1794, 0, 0, 2, 4),
    (-1749, 0, 1, -2, 0),
    (-1565, 0, 0, -2, 4),
    (-1491, 0, 0, 2, -8),
    (-1475, 2, 1, 2, 0),
    (-1410, 4, 0, 0, -2),
    (-1344, 2, 1, 0, 2),
    (-1335, 0, 2, 0, -2),
    (1107, 4, 1, 2, 0),
    (1021, 0, 0, 6, 0),
    (833, 0, 0, 2, 8),
    (777, 4, 0, -2, 0),
    (671, 2, 0, 4, 0),
    (607, 0, 0, 6, -2),
    (596, 2, 2, -2, 0),
    (491, 0, 0, 0, 8),
    (-451, 0, 1, 2, 2),
    (439, 0, 1, -2, 2),
    (422, 0, 0, 0, -4),
    (421, 2, 0, 0, 4),
    (-366, 0, 0, 2, 6),
    (-351, 0, 0, 4, 4),
    (331, 0, 0, 6, 2),
    (315, 2, 1, 0, -4),
    (302, 2, 2, -2, -2),
    (-283, 2, 1, -2, 2),
    (-229, 2, 0, 4, 2),
    (223, 2, 0, 0, -6),
    (223, 4, 0, 0, 2),
    (-220, 2, 1, 0, 4),
    (-220, 0, 2, 0, 4),
    (-185, 0, 2, -2, 0),
    (181, 2, 1, 2, -2),
)


def moon_equatorial(jd: float) -> Equatorial:
    t = julian_centuries(jd)
    lp = norm360(218.3164477 + 481267.88123421 * t - 0.0015786 * t ** 2
                 + t ** 3 / 538841.0 - t ** 4 / 65194000.0)
    d = norm360(297.8501921 + 445267.1114034 * t - 0.0018819 * t ** 2
                + t ** 3 / 545868.0 - t ** 4 / 113065000.0)
    m = norm360(357.5291092 + 35999.0502909 * t - 0.0001536 * t ** 2
                + t ** 3 / 24490000.0)
    mp = norm360(134.9633964 + 477198.8675055 * t + 0.0087414 * t ** 2
                 + t ** 3 / 69699.0 - t ** 4 / 14712000.0)
    f = norm360(93.2720950 + 483202.0175233 * t - 0.0036539 * t ** 2
                - t ** 3 / 3526000.0 + t ** 4 / 863310000.0)
    a1 = norm360(119.75 + 131.849 * t)
    a2 = norm360(53.09 + 479264.290 * t)
    a3 = norm360(313.45 + 481266.484 * t)
    e = 1 - 0.002516 * t - 0.0000074 * t * t

    d_r, m_r, mp_r, f_r = d * DEG, m * DEG, mp * DEG, f * DEG
    sum_l = sum_r = 0.0
    for sl, sr, mpn, mn, fn, dn in _MOON_LR:
        arg = mpn * mp_r + mn * m_r + fn * f_r + dn * d_r
        ecc = e ** abs(mn)
        sum_l += sl * ecc * math.sin(arg)
        sum_r += sr * ecc * math.cos(arg)
    sum_b = 0.0
    for sb, mpn, mn, fn, dn in _MOON_B:
        arg = mpn * mp_r + mn * m_r + fn * f_r + dn * d_r
        sum_b += sb * (e ** abs(mn)) * math.sin(arg)
    sum_l += (3958 * math.sin(a1 * DEG) + 1962 * math.sin((lp - f) * DEG)
              + 318 * math.sin(a2 * DEG))
    sum_b += (-2235 * math.sin(lp * DEG) + 382 * math.sin(a3 * DEG)
              + 175 * math.sin((a1 - f) * DEG) + 175 * math.sin((a1 + f) * DEG)
              + 127 * math.sin((lp - mp) * DEG) - 115 * math.sin((lp + mp) * DEG))

    lam = norm360(lp + sum_l / 1e6)
    beta = sum_b / 1e6
    delta = 385000.56 + sum_r / 1000.0

    eps = obliquity(t)
    lam_r, beta_r, eps_r = lam * DEG, beta * DEG, eps * DEG
    ra = math.atan2(math.sin(lam_r) * math.cos(eps_r) - math.tan(beta_r) * math.sin(eps_r),
                    math.cos(lam_r)) * RAD
    dec = math.asin(math.sin(beta_r) * math.cos(eps_r)
                    + math.cos(beta_r) * math.sin(eps_r) * math.sin(lam_r)) * RAD
    return Equatorial(norm360(ra), dec, delta, lam)


def moon_altaz(when_utc: datetime, lat: float, lon: float):
    jd = julian_day(to_utc(when_utc))
    eq = moon_equatorial(jd)
    return _altaz_from_radec(eq.ra, eq.dec, lat, lon, jd) + (eq,)


def moon_phase(when_utc: datetime):
    """返回 (相位 0=新月 0.5=满月 1=下一个新月, 被照亮的比例, 亮面在天空中的方向)。

    亮面方向用"从月亮指向太阳"的单位向量在 ENU 水平面的投影表示，单位为弧度，
    0 = 正东方向偏移，画面上可直接使用。
    """
    jd = julian_day(to_utc(when_utc))
    sun = sun_equatorial(jd)
    moon = moon_equatorial(jd)
    elong = math.acos(max(-1.0, min(1.0,
        math.sin(sun.dec * DEG) * math.sin(moon.dec * DEG)
        + math.cos(sun.dec * DEG) * math.cos(moon.dec * DEG)
        * math.cos((sun.ra - moon.ra) * DEG))))
    illum = (1.0 + math.cos(math.pi - elong)) / 2.0
    # 相位：用黄经差
    diff = norm360(moon.app_longitude - sun.app_longitude)
    phase = diff / 360.0
    return phase, illum, elong * RAD


def bright_limb_vector(when_utc: datetime, lat: float, lon: float):
    """月亮亮面朝向：返回地平坐标系中的单位向量 (east, north, up)。"""
    sun_alt, sun_az = sun_altaz(when_utc, lat, lon)
    moon_alt, moon_az = moon_altaz(when_utc, lat, lon)[:2]
    v1 = _unit(sun_alt, sun_az)
    v2 = _unit(moon_alt, moon_az)
    k = [v1[i] - v2[i] for i in range(3)]
    norm = math.sqrt(sum(c * c for c in k)) or 1.0
    return tuple(c / norm for c in k)


def _unit(alt, az):
    a, z = alt * DEG, az * DEG
    return (math.cos(a) * math.sin(z), math.cos(a) * math.cos(z), math.sin(a))


# --------------------------------------------------------------------------
# 升落与暮光
# --------------------------------------------------------------------------

def _find_crossings(func, start_local: datetime, lat: float, lon: float, target: float,
                    step_min: int = 4, rising: bool | None = None):
    """在 [start, start+24h) 内寻找 func(alt) 穿越 target 的时刻（本地时间）。"""
    tz = start_local.tzinfo
    base = start_local.replace(hour=0, minute=0, second=0, microsecond=0)
    samples = []
    n = int(24 * 60 / step_min)
    for i in range(n + 2):
        t = base + timedelta(minutes=i * step_min)
        alt = func(to_utc(t), lat, lon)[0] - target
        samples.append((t, alt))
    out = []
    for i in range(len(samples) - 1):
        (t0, a0), (t1, a1) = samples[i], samples[i + 1]
        if a0 == 0:
            out.append((t0, a0, a1))
            continue
        if (a0 < 0 < a1) or (a0 > 0 > a1):
            # 二分细化
            lo, hi = t0, t1
            for _ in range(18):
                mid = lo + (hi - lo) / 2
                am = func(to_utc(mid), lat, lon)[0] - target
                if (a0 < 0) == (am < 0):
                    lo = mid
                else:
                    hi = mid
            out.append((lo + (hi - lo) / 2, a0, a1))
    if rising is True:
        return [t for t, a0, _ in out if a0 < 0]
    if rising is False:
        return [t for t, a0, _ in out if a0 > 0]
    return [t for t, _, _ in out]


def _first(crossings, fallback=None):
    return crossings[0] if crossings else fallback


def sun_events(local_date: datetime, lat: float, lon: float) -> dict:
    """一天中的关键光照时刻（本地 naive datetime）。"""
    ev = {}
    ev["sunrise"] = _first(_find_crossings(sun_altaz, local_date, lat, lon, _SUN_RISE_ALT, rising=True))
    ev["sunset"] = _first(_find_crossings(sun_altaz, local_date, lat, lon, _SUN_RISE_ALT, rising=False))
    ev["golden_morning_end"] = _first(_find_crossings(sun_altaz, local_date, lat, lon, _GOLDEN_ALT, rising=True))
    ev["golden_evening_start"] = _first(_find_crossings(sun_altaz, local_date, lat, lon, _GOLDEN_ALT, rising=False))
    ev["civil_dawn"] = _first(_find_crossings(sun_altaz, local_date, lat, lon, _CIVIL_ALT, rising=True))
    ev["civil_dusk"] = _first(_find_crossings(sun_altaz, local_date, lat, lon, _CIVIL_ALT, rising=False))
    ev["nautical_dawn"] = _first(_find_crossings(sun_altaz, local_date, lat, lon, _NAUTICAL_ALT, rising=True))
    ev["nautical_dusk"] = _first(_find_crossings(sun_altaz, local_date, lat, lon, _NAUTICAL_ALT, rising=False))
    ev["astro_dawn"] = _first(_find_crossings(sun_altaz, local_date, lat, lon, _ASTRONOMICAL_ALT, rising=True))
    ev["astro_dusk"] = _first(_find_crossings(sun_altaz, local_date, lat, lon, _ASTRONOMICAL_ALT, rising=False))
    ev["noon"] = solar_noon(local_date, lat, lon)

    def moon_at(dt, la, lo):
        return moon_altaz(dt, la, lo)[:2]

    ev["moonrise"] = _first(_find_crossings(moon_at, local_date, lat, lon, _MOON_RISE_ALT, rising=True))
    ev["moonset"] = _first(_find_crossings(moon_at, local_date, lat, lon, _MOON_RISE_ALT, rising=False))
    return ev


# --------------------------------------------------------------------------
# 状态聚合
# --------------------------------------------------------------------------

PHASE_NAMES = [
    (0.02, "新月"), (0.22, "蛾眉月"), (0.28, "上弦月"), (0.47, "盈凸月"),
    (0.53, "满月"), (0.72, "亏凸月"), (0.78, "下弦月"), (0.98, "残月"),
]


def phase_name(phase: float) -> str:
    if phase < 0.02 or phase > 0.98:
        return "新月"
    for limit, name in PHASE_NAMES:
        if phase <= limit:
            return name
    return "残月"


def moon_age_days(phase: float) -> float:
    return phase * 29.530588


def moonlight_factor(alt: float, illum: float) -> float:
    """月亮对夜晚天空亮度的影响（0-1）。"""
    if alt < -2:
        return 0.0
    horizon = max(0.0, min(1.0, (alt + 2) / 8.0))
    return illum ** 1.6 * horizon
