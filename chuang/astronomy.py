"""天文计算：太阳、月亮、恒星。纯本地数学，无网络依赖。

太阳位置采用 NOAA Solar Calculator 的算法；月亮位置采用 Meeus
《Astronomical Algorithms》第 47 章（表 47.A / 47.B 各 60 项）。
与 JPL 历表（用 pyephem 代查）逐点对照过：地心黄经残差 < 1′、距离残差 < 10 km，
这已经是"肉眼绝对看不出来"的量级（1′ 相当于月面视直径的 1/30）。

月亮还有一处不能省：它的地心坐标要换算成**地面观测者**看到的坐标（地平视差，
最大 61′），否则月亮会"提前一个月亮直径"升起来。见 moon_altaz()。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

DEG = math.pi / 180.0
RAD = 180.0 / math.pi

# 地球轨道与大气常数
# 太阳上边缘触地平：−16′（日面半径）− 34′（大气折射）。这是太阳中心的
# **几何**高度，所以只能拿 sun_geometric_altaz() 去比——拿带折射的高度去比
# 就等于把折射算了两遍，日出会早、日落会晚各约 4 分钟。
_SUN_RISE_ALT = -0.833
_GOLDEN_ALT = 6.0
_CIVIL_ALT = -6.0
_NAUTICAL_ALT = -12.0
_ASTRONOMICAL_ALT = -18.0

# 地球赤道半径（km）：算月亮的地平视差用
_EARTH_RADIUS_KM = 6378.14


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


def _altaz_geometric(ra_deg: float, dec_deg: float, lat: float, lon: float, jd: float):
    """赤道坐标 → 地平坐标的**几何位置**（不含折射）。返回 (alt, az)，单位度。"""
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
    return alt * RAD, norm360(az * RAD)


def _altaz_from_radec(ra_deg: float, dec_deg: float, lat: float, lon: float, jd: float):
    """赤道坐标 → 地平坐标（含大气折射修正）。返回 (alt, az)。"""
    alt_d, az_d = _altaz_geometric(ra_deg, dec_deg, lat, lon, jd)
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


def sun_geometric_altaz(when_utc: datetime, lat: float, lon: float):
    """太阳的**几何**位置（不含大气折射）。

    升落、金色时刻、暮光的判据（−0.833° / ±6° / −12° / −18°）说的都是几何高度，
    所以判定一律用这个；画面上的位置用带折射的 sun_altaz()（看到的确实是偏高的）。
    """
    jd = julian_day(to_utc(when_utc))
    eq = sun_equatorial(jd)
    return _altaz_geometric(eq.ra, eq.dec, lat, lon, jd)


def sun_hour_angle(when_utc: datetime, lon: float) -> float:
    """太阳的时角（度，-180..180）。时角为 0 就是太阳过中天那一刻。"""
    jd = julian_day(to_utc(when_utc))
    return wrap180(sidereal_time_deg(jd, lon) - sun_equatorial(jd).ra)


def solar_noon(when_local_date: datetime, lat: float, lon: float) -> datetime:
    """当天太阳过中天的时刻（与输入同样带不带时区）。

    以前这里是把方位角"迭代到 180°"的定点迭代，而太阳接近天顶时方位角一秒
    能翻几十度，迭代会直接跑飞——西安 2026-06-21 能差出 2 小时 46 分。
    时角才是那个单调的量（一天里匀速走一圈），所以改成对时角求零点。
    """
    base = when_local_date.replace(hour=0, minute=0, second=0, microsecond=0)
    step = timedelta(minutes=10)
    prev_t, prev_h = base, sun_hour_angle(base, lon)
    for i in range(1, 24 * 6 + 1):
        t = base + step * i
        h = sun_hour_angle(t, lon)
        if prev_h <= 0.0 <= h:          # 时角由负变正：中天在这一步里
            lo, hi = prev_t, t
            for _ in range(24):
                mid = lo + (hi - lo) / 2
                if sun_hour_angle(mid, lon) < 0.0:
                    lo = mid
                else:
                    hi = mid
            return lo + (hi - lo) / 2
        prev_t, prev_h = t, h
    return base + timedelta(hours=12)    # 理论到不了这里，兜个底


# --------------------------------------------------------------------------
# 月亮
# --------------------------------------------------------------------------

_MOON_LR = (
    # Meeus《Astronomical Algorithms》表 47.A（全 60 项）：
    #   (l 系数 1e-6 度, r 系数 1e-3 km, M', M, F, D 的倍数)
    # 这几列**必须与表里的顺序一一对应**，写错了月亮就会整体偏出几度——
    # 曾经这里就是错的（第一项的 M' 写成了 0，最大的 6.29° 项直接消失），
    # 见 CHANGELOG。改这张表时请对着原表逐列核一遍。
    (6288774, -20905355, 1, 0, 0, 0),
    (1274027, -3699111, -1, 0, 0, 2),
    (658314, -2955968, 0, 0, 0, 2),
    (213618, -569925, 2, 0, 0, 0),
    (-185116, 48888, 0, 1, 0, 0),
    (-114332, -3149, 0, 0, 2, 0),
    (58793, 246158, -2, 0, 0, 2),
    (57066, -152138, -1, -1, 0, 2),
    (53322, -170733, 1, 0, 0, 2),
    (45758, -204586, 0, -1, 0, 2),
    (-40923, -129620, -1, 1, 0, 0),
    (-34720, 108743, 0, 0, 0, 1),
    (-30383, 104755, 1, 1, 0, 0),
    (15327, 10321, 0, 0, -2, 2),
    (-12528, 0, 1, 0, 2, 0),
    (10980, 79661, 1, 0, -2, 0),
    (10675, -34782, -1, 0, 0, 4),
    (10034, -23210, 3, 0, 0, 0),
    (8548, -21636, -2, 0, 0, 4),
    (-7888, 24208, -1, 1, 0, 2),
    (-6766, 30824, 0, 1, 0, 2),
    (-5163, -8379, -1, 0, 0, 1),
    (4987, -16675, 0, 1, 0, 1),
    (4036, -12831, 1, -1, 0, 2),
    (3994, -10445, 2, 0, 0, 2),
    (3861, -11650, 0, 0, 0, 4),
    (3665, 14403, -3, 0, 0, 2),
    (-2689, -7003, -2, 1, 0, 0),
    (-2602, 0, -1, 0, 2, 2),
    (2390, 10056, -2, -1, 0, 2),
    (-2348, 6322, 1, 0, 0, 1),
    (2236, -9884, 0, -2, 0, 2),
    (-2120, 5751, 2, 1, 0, 0),
    (-2069, 0, 0, 2, 0, 0),
    (2048, -4950, -1, -2, 0, 2),
    (-1773, 4130, 1, 0, -2, 2),
    (-1595, 0, 0, 0, 2, 2),
    (1215, -3958, -1, -1, 0, 4),
    (-1110, 0, 2, 0, 2, 0),
    (-892, 3258, -1, 0, 0, 3),
    (-810, 2616, 1, 1, 0, 2),
    (759, -1897, -2, -1, 0, 4),
    (-713, -2117, -1, 2, 0, 0),
    (-700, 2354, -1, 2, 0, 2),
    (691, 0, -2, 1, 0, 2),
    (596, 0, 0, -1, -2, 2),
    (549, -1423, 1, 0, 0, 4),
    (537, -1117, 4, 0, 0, 0),
    (520, -1571, 0, -1, 0, 4),
    (-487, -1739, -2, 0, 0, 1),
    (-399, 0, 0, 1, -2, 2),
    (-381, -4421, 2, 0, -2, 0),
    (351, 0, 1, 1, 0, 1),
    (-340, 0, -2, 0, 0, 3),
    (330, 0, -3, 0, 0, 4),
    (327, 0, 2, -1, 0, 2),
    (-323, 1165, 1, 2, 0, 0),
    (299, 0, -1, 1, 0, 1),
    (294, 0, 3, 0, 0, 2),
    (0, 8752, -1, 0, -2, 2),
)

_MOON_B = (
    # 表 47.B（全 60 项）：(b 系数, M', M, F, D 的倍数)
    (5128122, 0, 0, 1, 0),
    (280602, 1, 0, 1, 0),
    (277693, 1, 0, -1, 0),
    (173237, 0, 0, -1, 2),
    (55413, -1, 0, 1, 2),
    (46271, -1, 0, -1, 2),
    (32573, 0, 0, 1, 2),
    (17198, 2, 0, 1, 0),
    (9266, 1, 0, -1, 2),
    (8822, 2, 0, -1, 0),
    (8216, 0, -1, -1, 2),
    (4324, -2, 0, -1, 2),
    (4200, 1, 0, 1, 2),
    (-3359, 0, 1, -1, 2),
    (2463, -1, -1, 1, 2),
    (2211, 0, -1, 1, 2),
    (2065, -1, -1, -1, 2),
    (-1870, -1, 1, -1, 0),
    (1828, -1, 0, -1, 4),
    (-1794, 0, 1, 1, 0),
    (-1749, 0, 0, 3, 0),
    (-1565, -1, 1, 1, 0),
    (-1491, 0, 0, 1, 1),
    (-1475, 1, 1, 1, 0),
    (-1410, 1, 1, -1, 0),
    (-1344, 0, 1, -1, 0),
    (-1335, 0, 0, -1, 1),
    (1107, 3, 0, 1, 0),
    (1021, 0, 0, -1, 4),
    (833, -1, 0, 1, 4),
    (777, 1, 0, -3, 0),
    (671, -2, 0, 1, 4),
    (607, 0, 0, -3, 2),
    (596, 2, 0, -1, 2),
    (491, 1, -1, -1, 2),
    (-451, -2, 0, 1, 2),
    (439, 3, 0, -1, 0),
    (422, 2, 0, 1, 2),
    (421, -3, 0, -1, 2),
    (-366, -1, 1, 1, 2),
    (-351, 0, 1, 1, 2),
    (331, 0, 0, 1, 4),
    (315, 1, -1, 1, 2),
    (302, 0, -2, -1, 2),
    (-283, 1, 0, 3, 0),
    (-229, 1, 1, -1, 2),
    (223, 0, 1, -1, 1),
    (223, 0, 1, 1, 1),
    (-220, -2, 1, -1, 0),
    (-220, -1, 1, -1, 2),
    (-185, 1, 0, 1, 1),
    (181, -2, -1, -1, 2),
    (-177, 2, 1, 1, 0),
    (176, -2, 0, -1, 4),
    (166, -1, -1, -1, 4),
    (-164, 1, 0, -1, 1),
    (132, 1, 0, -1, 4),
    (-119, -1, 0, -1, 1),
    (115, 0, -1, -1, 4),
    (107, 0, -2, 1, 2),
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


def moon_parallax(distance_km: float) -> float:
    """月亮的地平视差（度）：地心与地面两个视角之间最多差这么多。"""
    return math.asin(min(1.0, _EARTH_RADIUS_KM / max(1.0, distance_km))) * RAD


def moon_altaz(when_utc: datetime, lat: float, lon: float):
    """月亮在**地面观测者**眼里的位置（含地平视差与大气折射）。

    返回 (alt, az, 地心赤道坐标)。原来的实现只做了折射修正、把地心坐标直接当
    成了看到的位置——可月亮只有 38 万公里远，地平视差最大 61′（约两个月面直径），
    于是月亮会"还没升起来就先出现在窗里"。这里按

        alt_地面 ≈ alt_地心 − π·cos(alt_地心),   π = asin(R_地球 / 距离)

    做修正（一阶项即是全部，忽略项 < 1″）；方位角的变化只有 0.0x°，不修。
    地心坐标仍是准确的，升落判定用的就是它，见 moon_geocentric_altaz()。
    """
    jd = julian_day(to_utc(when_utc))
    eq = moon_equatorial(jd)
    alt, az = _altaz_geometric(eq.ra, eq.dec, lat, lon, jd)
    alt -= moon_parallax(eq.distance) * math.cos(alt * DEG)
    return alt + refraction(alt), az, eq


def moon_geocentric_altaz(when_utc: datetime, lat: float, lon: float):
    """月亮**地心**的几何位置（无折射、无视差）。

    升落判定必须用这个口径：Meeus 的月出月落判据 h0 = 0.7275π − 34′ = +0.125°
    本来就是"地心几何高度"，里面已经把折射与视差一起折进去了。用别的高度
    （比如带折射的）去比这个阈值，月出会晚、月落会早一两分钟。
    """
    jd = julian_day(to_utc(when_utc))
    eq = moon_equatorial(jd)
    return _altaz_geometric(eq.ra, eq.dec, lat, lon, jd)


def moon_rise_alt(when, lat: float, lon: float) -> float:
    """月出月落的判据高度 h0 = 0.7275π − 34′（Meeus 15 章）。

    拆开看就是"上边缘贴着地平"：π 是地平视差（把地心坐标换算到地面），
    0.2725π 正好是月面视半径（月面半径/距离 ≈ 0.2725），34′ 是地平附近的大气折射。
    视差随距离变（0.89°～1.02°），所以 h0 也是变的（+0.08°～+0.18°），
    取常数 0.125° 会让月出月落差半分钟左右——不值得省这一步。
    """
    jd = julian_day(to_utc(when))
    return 0.7275 * moon_parallax(moon_equatorial(jd).distance) - 0.5667


def moon_phase(when_utc: datetime):
    """返回 (相位 0=新月 0.5=满月 1=下一个新月, 被照亮的比例, 日月角距/度)。

    第三个值是"太阳与月亮在天空中的角距"（elongation，度），只有 0-180：
    它不表示亮面朝向。要画亮面朝哪，用 bright_limb_vector()。
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
    """在 [start, start+24h) 内寻找 func(alt) 穿越 target 的时刻（本地时间）。

    target 可以是常数，也可以是 target(dt, lat, lon)——月出月落的判据本身
    随距离变化（h0 = 0.7275π − 34′），所以允许它是个函数。
    """
    base = start_local.replace(hour=0, minute=0, second=0, microsecond=0)

    def diff(t: datetime) -> float:
        tgt = target(t, lat, lon) if callable(target) else target
        return func(to_utc(t), lat, lon)[0] - tgt

    samples = []
    n = int(24 * 60 / step_min)
    for i in range(n + 2):
        t = base + timedelta(minutes=i * step_min)
        samples.append((t, diff(t)))
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
                am = diff(mid)
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
    sun = sun_geometric_altaz          # 判据都是几何高度，别用带折射的那个
    ev["sunrise"] = _first(_find_crossings(sun, local_date, lat, lon, _SUN_RISE_ALT, rising=True))
    ev["sunset"] = _first(_find_crossings(sun, local_date, lat, lon, _SUN_RISE_ALT, rising=False))
    ev["golden_morning_end"] = _first(_find_crossings(sun, local_date, lat, lon, _GOLDEN_ALT, rising=True))
    ev["golden_evening_start"] = _first(_find_crossings(sun, local_date, lat, lon, _GOLDEN_ALT, rising=False))
    ev["civil_dawn"] = _first(_find_crossings(sun, local_date, lat, lon, _CIVIL_ALT, rising=True))
    ev["civil_dusk"] = _first(_find_crossings(sun, local_date, lat, lon, _CIVIL_ALT, rising=False))
    ev["nautical_dawn"] = _first(_find_crossings(sun, local_date, lat, lon, _NAUTICAL_ALT, rising=True))
    ev["nautical_dusk"] = _first(_find_crossings(sun, local_date, lat, lon, _NAUTICAL_ALT, rising=False))
    ev["astro_dawn"] = _first(_find_crossings(sun, local_date, lat, lon, _ASTRONOMICAL_ALT, rising=True))
    ev["astro_dusk"] = _first(_find_crossings(sun, local_date, lat, lon, _ASTRONOMICAL_ALT, rising=False))
    ev["noon"] = solar_noon(local_date, lat, lon)

    ev["moonrise"] = _first(_find_crossings(moon_geocentric_altaz, local_date, lat, lon,
                                            moon_rise_alt, rising=True))
    ev["moonset"] = _first(_find_crossings(moon_geocentric_altaz, local_date, lat, lon,
                                           moon_rise_alt, rising=False))
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
