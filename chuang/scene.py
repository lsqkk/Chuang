"""把「时刻 + 地点 + 天气」组装成一帧可绘制的场景（纯数据）。"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import astronomy as A
from .palette import Mood, mood_for, ribbon_color
from .weather import Weather

DATA = Path(__file__).resolve().parent / "data"


def _load_stars() -> list[tuple[float, float, float, float]]:
    try:
        rows = json.loads((DATA / "stars.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [(float(r[0]) * A.DEG, float(r[1]) * A.DEG, float(r[2]), float(r[3]))
            for r in rows]


def bv_to_rgb(bv: float) -> tuple[float, float, float]:
    """B-V 色指数 → 大致色温的 RGB（只是让星空有冷暖差别）。"""
    t = (bv + 0.4) / 2.0
    t = max(0.0, min(1.0, t))
    warm = (1.00, 0.82, 0.62)
    mid = (1.00, 0.97, 0.94)
    cool = (0.72, 0.82, 1.00)
    if t < 0.5:
        k = t / 0.5
        return tuple(cool[i] + (mid[i] - cool[i]) * k for i in range(3))
    k = (t - 0.5) / 0.5
    return tuple(mid[i] + (warm[i] - mid[i]) * k for i in range(3))


@dataclass
class StarField:
    """一份已经算好地平坐标的星空（按需重算，不必每帧都算）。"""

    points: list[tuple[float, float, float, tuple[float, float, float], float]] = field(default_factory=list)
    stamp: float = 0.0


@dataclass
class Scene:
    when: datetime                  # 地点当地时间
    utc: datetime
    location_label: str
    location_name: str = ""
    lat: float = 0.0
    lon: float = 0.0
    preview: bool = False

    sun_alt: float = 0.0
    sun_az: float = 0.0
    moon_alt: float = 0.0
    moon_az: float = 0.0
    moon_illum: float = 0.0
    moon_phase: float = 0.0
    moon_bright: tuple[float, float, float] = (0.0, 1.0, 0.0)
    moon_light: float = 0.0

    mood: Mood = None  # type: ignore
    stars: StarField = field(default_factory=StarField)
    events: dict = field(default_factory=dict)

    # 天气
    has_weather: bool = False
    weather_disabled: bool = False  # 用户自己把「跟随真实天气」关掉了
    weather_stale: bool = False
    cloud: float = 0.0
    code: int = 0
    weather_text: str = ""
    temp: float = 0.0
    apparent: float = 0.0
    humidity: float = 0.0
    wind_speed: float = 0.0
    wind_dir: float = 0.0
    precip_strength: float = 0.0
    precip_kind: str = "none"
    thunder: bool = False
    fog: bool = False

    @property
    def is_night(self) -> bool:
        return self.sun_alt < -6

    @property
    def daylight_left(self):
        sunset = self.events.get("sunset")
        if sunset and self.when < sunset:
            return sunset - self.when
        return None

    @property
    def period_name(self) -> str:
        a = self.sun_alt
        if a > 12:
            return "白天"
        if a > 6:                      # 6° 是天文那套"金色时刻结束"的高度
            return "斜阳"
        if a > 0:
            return "金色时刻"
        # -0.833° 是"太阳正好压在地平线上"（含大气折射），再往下就算落下了
        if a > -2.5:
            return "日落"
        if a > -6:
            return "暮色"
        if a > -18:
            return "夜色渐深"
        return "夜"


class SkyEngine:
    """按天缓存，保证滚动/动画时不会重复做重活。"""

    def __init__(self) -> None:
        self._stars_raw = _load_stars()
        self.lat = 34.3416
        self.lon = 108.9398
        self.tz = "Asia/Shanghai"
        self._tzinfo = None
        self._events: dict[str, dict] = {}
        self._starfield = StarField()

    # ---- 地点与时间 ---------------------------------------------------
    def set_location(self, lat: float, lon: float, tz: str) -> None:
        if (abs(lat - self.lat) < 1e-9 and abs(lon - self.lon) < 1e-9
                and tz == self.tz):
            return
        self.lat, self.lon, self.tz = lat, lon, tz
        self._tzinfo = self._resolve_tz(tz)
        self._events.clear()
        self._starfield = StarField()

    @staticmethod
    def _resolve_tz(name: str):
        if not name or name == "auto":
            return None
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError, KeyError, OSError):
            return None

    def local_now(self) -> datetime:
        if self._tzinfo is not None:
            return datetime.now(self._tzinfo)
        return datetime.now().astimezone()

    def local_date(self) -> datetime:
        return self.local_now().replace(hour=0, minute=0, second=0, microsecond=0)

    def events(self, day: datetime | None = None) -> dict:
        day = (day or self.local_now()).replace(hour=0, minute=0, second=0, microsecond=0)
        key = day.strftime("%Y-%m-%d")
        if key not in self._events:
            self._events[key] = A.sun_events(day, self.lat, self.lon)
        return self._events[key]

    # ---- 星空 ---------------------------------------------------------
    def starfield(self, utc: datetime, soft_seconds: float = 45.0) -> StarField:
        stamp = utc.timestamp()
        if self._starfield.points and abs(stamp - self._starfield.stamp) < soft_seconds:
            return self._starfield
        jd = A.julian_day(utc)
        lst = A.sidereal_time_deg(jd, self.lon) * A.DEG
        phi = self.lat * A.DEG
        sin_phi, cos_phi = math.sin(phi), math.cos(phi)
        pts = []
        for ra, dec, mag, bv in self._stars_raw:
            h = lst - ra
            sin_alt = sin_phi * math.sin(dec) + cos_phi * math.cos(dec) * math.cos(h)
            sin_alt = max(-1.0, min(1.0, sin_alt))
            alt = math.asin(sin_alt)
            if alt < -0.02:          # 地平线以下不绘制
                continue
            y = -math.cos(dec) * math.sin(h)
            x = math.sin(dec) * cos_phi - math.cos(dec) * sin_phi * math.cos(h)
            az = math.atan2(y, x)
            alt_d = alt * A.RAD + A.refraction(alt * A.RAD)
            if alt_d < 0:
                continue
            twinkle = (ra * 13.7 + dec * 7.3) % (2 * math.pi)
            pts.append((A.norm360(az * A.RAD), alt_d, mag, bv_to_rgb(bv), twinkle))
        self._starfield = StarField(points=pts, stamp=stamp)
        return self._starfield

    # ---- 主构建 -------------------------------------------------------
    def build(self, when: datetime, weather: Weather | None = None,
              preview: bool = False, location_label: str = "",
              weather_off: bool = False) -> Scene:
        if when.tzinfo is None:
            when = when.replace(tzinfo=self._tzinfo) if self._tzinfo else when.astimezone()
        utc = A.to_utc(when)
        events = self.events(when)

        sun_alt, sun_az = A.sun_altaz(utc, self.lat, self.lon)
        moon_alt, moon_az, moon_eq = A.moon_altaz(utc, self.lat, self.lon)
        moon_phase, moon_illum, _ = A.moon_phase(utc)
        moon_bright = A.bright_limb_vector(utc, self.lat, self.lon)
        moon_light = A.moonlight_factor(moon_alt, moon_illum)

        sc = Scene(
            when=when, utc=utc,
            location_label=location_label or "",
            location_name=location_label.split(" · ")[0] if location_label else "",
            lat=self.lat, lon=self.lon,
            preview=preview,
            sun_alt=sun_alt, sun_az=sun_az,
            moon_alt=moon_alt, moon_az=moon_az,
            moon_illum=moon_illum, moon_phase=moon_phase, moon_bright=moon_bright,
            moon_light=moon_light,
            mood=mood_for(sun_alt, moon_light),
            stars=self.starfield(utc),
            events=events,
        )

        if weather is not None and weather.ok:
            sc.has_weather = True
            sc.weather_stale = weather.stale
            sc.cloud = weather.cloud_at(when)
            sc.code = weather.code_at(when)
            from .weather import code_text, is_fog, is_thunder, precip_kind, precip_strength
            sc.weather_text = code_text(sc.code)
            sc.temp = weather.temp
            sc.apparent = weather.apparent
            sc.humidity = weather.humidity
            sc.wind_speed = weather.wind_speed
            sc.wind_dir = weather.wind_dir
            sc.precip_kind = precip_kind(sc.code)
            sc.precip_strength = precip_strength(sc.code, weather.precip if not preview else 0.0)
            sc.thunder = is_thunder(sc.code)
            sc.fog = is_fog(sc.code)
        elif weather_off:
            # "没有天气"和"你关掉了天气"是两回事：前者要写"未联网"，
            # 后者写成"未联网"会把用户指去检查网络。让画面知道这个区别。
            sc.weather_disabled = True
        return sc

    # ---- 今日天色长卷 -------------------------------------------------
    def ribbon(self, day: datetime, weather: Weather | None) -> list[tuple[datetime, tuple[float, float, float]]]:
        base = day.replace(hour=0, minute=0, second=0, microsecond=0)
        steps = 288  # 每 5 分钟一格
        out = []
        for i in range(steps):
            t = base + timedelta(minutes=i * 5)
            alt, _ = A.sun_altaz(A.to_utc(t), self.lat, self.lon)
            cloud = weather.cloud_at(t) if (weather is not None and weather.ok) else 0.0
            if weather is not None and weather.ok:
                from .weather import precip_kind
                code = weather.code_at(t)
                cloud = max(cloud, 55.0 if precip_kind(code) != "none" else cloud)
            out.append((t, ribbon_color(alt, cloud)))
        return out


def skyline_seed(name: str, lat: float, lon: float) -> int:
    """由城市名与经纬度生成稳定的随机种子——同一个城市永远长出同一片屋顶。"""
    h = 2166136261
    for ch in f"{name}|{lat:.2f}|{lon:.2f}":
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return h


# --------------------------------------------------------------------------
# 把数据翻译成人话
# --------------------------------------------------------------------------

_COMPASS = ["北", "北东北", "东北", "东北东", "东", "东南东", "东南", "东南南",
            "南", "西南南", "西南", "西南西", "西", "西北西", "西北", "西北北"]


def compass(az: float) -> str:
    return _COMPASS[int(((az % 360) + 11.25) // 22.5) % 16]


def _hm(dt: datetime | None) -> str:
    return dt.strftime("%H:%M") if dt else "—"


def duration_zh(delta) -> str:
    total = int(max(0, delta.total_seconds()))
    h, m = divmod(total // 60, 60)
    if h and m:
        return f"{h} 小时 {m} 分"
    if h:
        return f"{h} 小时"
    return f"{m} 分钟"


def human_hint(sc: "Scene") -> str:
    """一句人话：把此刻最值得知道的事情说出来。"""
    if sc.has_weather and sc.precip_kind == "rain" and sc.precip_strength > 0.2:
        if sc.thunder:
            return "外面正在打雷下雨，雨会斜着打在窗上。"
        return f"外面正在下{sc.weather_text}，这种时候最适合发呆。"
    if sc.has_weather and sc.precip_kind == "snow" and sc.precip_strength > 0.2:
        return "外面在下雪，雪花是慢慢飘下来的。"
    if sc.has_weather and sc.fog:
        return "外面起雾了，远处的屋顶已经看不清。"
    if sc.has_weather and sc.cloud > 82:
        return "云很厚，今天的光是匀匀地洒下来的，没有影子。"

    alt = sc.sun_alt
    if alt > 45:
        return f"太阳几乎在头顶，{compass(sc.sun_az)}边的天空最亮。"
    if alt > 20:
        return f"太阳在{compass(sc.sun_az)}方，仰角 {alt:.0f}°，影子短短的。"
    if alt > 6:
        return f"太阳在{compass(sc.sun_az)}方只有 {alt:.0f}° 高，光斜斜地照进来。"
    if alt > 0.5:
        return f"{compass(sc.sun_az)}边的天被染成了橘色，这是今天最好的光。"
    if alt > -3:
        left = sc.daylight_left
        tail = f"天光只剩 {duration_zh(left)}。" if left else "太阳刚刚离开。"
        return f"太阳正在{compass(sc.sun_az)}方落到地平线下，{tail}"
    if alt > -7:
        return "暮色最浓的时候，天边还剩一线橘红。"
    if alt > -14:
        return "夜色正在从东边漫上来，第一批星星已经亮了。"
    if sc.moon_alt > 3 and sc.moon_illum > 0.15:
        return f"{compass(sc.moon_az)}方挂着{phase_name_simple(sc.moon_phase)}的月亮，天已经黑透了。"
    if sc.has_weather and sc.cloud < 25:
        return "天已经黑透，云不多，星星应该看得见。"
    return "天已经黑透了，窗外是一片沉下来的夜色。"


def phase_name_simple(phase: float) -> str:
    if phase < 0.03 or phase > 0.97:
        return "新月"
    if phase < 0.22:
        return "细细的"
    if phase < 0.30:
        return "半月"
    if phase < 0.47:
        return "将满的"
    if phase <= 0.53:
        return "满"
    if phase < 0.72:
        return "刚过满的"
    if phase < 0.80:
        return "半月"
    if phase < 0.97:
        return "残"
    return "新月"
