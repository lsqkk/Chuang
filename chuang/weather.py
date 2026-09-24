"""真实天气：Open-Meteo 免费接口（无需 API Key），带本地缓存与离线降级。

只上传经纬度，不含任何身份信息。断网时退回"纯天文模式"——
天空依旧正确，只是不知道有没有云。
"""

from __future__ import annotations

import json
import math
import threading
import time as _time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from . import __version__

API = "https://api.open-meteo.com/v1/forecast"
GEOCODE_API = "https://geocoding-api.open-meteo.com/v1/search"
CACHE = Path.home() / ".cache" / "chuang" / "weather.json"
REFRESH_SECONDS = 600
UA = f"Chuang/{__version__} (desktop window; +local)"

# 一次要几天的逐小时数据。
#
# 这里以前是 2 天，于是"把长卷拖到后天／跳到某一天去看看"时，画面拿的是窗口
# 里**最近的那一格**（也就是明天深夜）顶上去的——看着像预报，其实是另一天。
# 现在默认一次要 7 天：用户能预览到的"附近几天"都是真的。更远的（最多 16 天）
# 由 WeatherService.ensure_day() 按那一天单独补问一次，并进同一张表。
FORECAST_DAYS = 7
# Open-Meteo 的硬上限：预报最远 16 天，往过去最多 92 天。
MAX_FORECAST_DAYS = 16
PAST_LIMIT_DAYS = 7
# 同一天两次补问之间至少隔这么久（长卷一路拖过去时不至于把接口刷爆）
DAY_QUERY_COOLDOWN = 20.0
# 补问失败（多半是那天超出了预报范围）之后，隔这么久再试
DAY_QUERY_BACKOFF = 600.0
# 经纬度差在这以内算"同一座城市"：缓存里的天气只认自己那座城
SAME_PLACE_DEG = 0.05

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


@dataclass
class HourPoint:
    when: datetime
    cloud: float
    code: int
    temp: float
    precip_prob: float = 0.0
    precip_mm: float = 0.0        # 那一小时的降水量（mm）
    vis: float = 24000.0          # 那一小时的能见度（米）
    # 1.1.13 起的几样"接口给了、我们以前没用"的读数（老缓存里没有，就是 None）
    cloud_low: float = 0.0
    cloud_mid: float = 0.0
    cloud_high: float = 0.0
    dew: float | None = None      # 露点（°C）
    pressure: float = 0.0         # 海平面气压（hPa）
    uv: float | None = None       # 紫外线指数


def _day_key(when) -> str:
    return when.strftime("%Y-%m-%d")


def today_in(tz: str):
    """那个地方"今天"是几号？——Open-Meteo 按天返回数据，判断"有没有覆盖此刻"用它。"""
    if tz and tz != "auto":
        try:
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo(tz)).date()
        except Exception:
            pass
    return datetime.now().date()


@dataclass
class Weather:
    ok: bool = False
    stale: bool = False
    fetched_at: float = 0.0
    error: str = ""
    temp: float = 0.0
    apparent: float = 0.0
    humidity: float = 0.0
    code: int = 0
    cloud: float = 0.0
    wind_speed: float = 0.0      # km/h
    wind_dir: float = 0.0        # 气象学风向（来向，度）
    gusts: float = 0.0
    precip: float = 0.0
    visibility: float = 24000.0
    # 1.1.13：几样以前"拿到手就扔掉"的读数——露点、气压、紫外线、云的高低层。
    # 老缓存里没有它们（就是默认值），画面照旧，只是少几个字。
    dew: float | None = None
    pressure: float = 0.0
    uv: float | None = None
    cloud_low: float = 0.0
    cloud_mid: float = 0.0
    cloud_high: float = 0.0
    daily: dict = field(default_factory=dict)   # {"2026-09-24": (最高, 最低)}
    hourly: list[HourPoint] = field(default_factory=list)
    place: str = ""
    lat: float = 0.0               # 这份数据是给哪座城的（0 = 未知）
    lon: float = 0.0
    _by_day: dict | None = field(default=None, repr=False, compare=False)

    # ---- 派生量 -------------------------------------------------------
    @property
    def kind(self) -> str:
        return precip_kind(self.code)

    @property
    def strength(self) -> float:
        return precip_strength(self.code, self.precip)

    @property
    def text(self) -> str:
        return code_text(self.code)

    # ---- 逐小时数据 ------------------------------------------------
    def _index(self) -> dict:
        """{哪一天: 那一天的逐小时点}——按天分开存，缺的天就是真的没有。

        分开的理由：以前只有一张 2 天的表，"跳到下周三"会拿到表尾那一格
        （也就是明天深夜）当预报。按天分开之后，没有的那天查不到就是查不到，
        界面上会老老实实写"这天还没有预报"，而不是拿邻天顶替。
        """
        if self._by_day is None:
            idx: dict = {}
            for p in self.hourly:
                idx.setdefault(_day_key(p.when), []).append(p)
            self._by_day = {k: tuple(v) for k, v in idx.items()}
        return self._by_day

    def invalidate(self) -> None:
        """逐小时表被改过之后要叫一声（合并、读缓存之后）。"""
        self._by_day = None

    def has_day(self, day) -> bool:
        """这一天的逐小时数据在手上吗？"""
        return _day_key(day) in self._index()

    def day_span(self) -> tuple[str, str] | None:
        """手上有数据的日期范围（"2026-09-24", "2026-09-30"）。"""
        keys = sorted(self._index())
        return (keys[0], keys[-1]) if keys else None

    def matches(self, lat: float, lon: float) -> bool:
        """这份数据是不是这一带地方的？"""
        if not self.lat and not self.lon:
            return True                  # 老缓存 / 调试用的假天气：不拦
        return (abs(self.lat - lat) <= SAME_PLACE_DEG
                and abs(self.lon - lon) <= SAME_PLACE_DEG)

    def _at(self, when: datetime, attr: str, linear: bool = True):
        """某一时刻的某个逐小时值；没有那一天的返回 None。"""
        pts = self._index().get(_day_key(_naive(when)))
        if not pts:
            return None
        return self._interp(pts, when, attr, linear)

    @staticmethod
    def _interp(pts, when: datetime, attr: str, linear: bool = True):
        """在某一天的逐小时点里插值出 attr 那一列（pts 已保证非空）。"""
        if len(pts) == 1:
            return float(getattr(pts[0], attr))
        when = _naive(when)
        if when <= pts[0].when:
            return float(getattr(pts[0], attr))
        if when >= pts[-1].when:
            return float(getattr(pts[-1], attr))
        for a, b in zip(pts, pts[1:]):
            if a.when <= when <= b.when:
                va, vb = float(getattr(a, attr)), float(getattr(b, attr))
                if not linear:
                    return va if (when - a.when) <= (b.when - when) else vb
                span = (b.when - a.when).total_seconds() or 1.0
                return va + (vb - va) * ((when - a.when).total_seconds() / span)
        return float(getattr(pts[-1], attr))

    def cloud_at(self, when: datetime) -> float | None:
        """按小时预报插值出某一时刻的云量（0-100）；那天没有数据就是 None。"""
        return self._at(when, "cloud")

    def temp_at(self, when: datetime) -> float | None:
        return self._at(when, "temp")

    def precip_at(self, when: datetime) -> float | None:
        return self._at(when, "precip_mm")

    def visibility_at(self, when: datetime) -> float | None:
        return self._at(when, "vis")

    def code_at(self, when: datetime) -> int | None:
        v = self._at(when, "code", linear=False)
        return None if v is None else int(round(v))

    def hourly_at(self, when: datetime, attr: str):
        """逐小时表里任意一列，插值到某一刻；没有这一列 / 这一列是空的返回 None。

        1.1.13 加的露点、气压、紫外线、云的高低层都走这里——它们不属于
        `_at()` 那几个"一定有值"的列（老缓存里就没有），所以缺了要给 None，
        而不是抛异常或者编一个 0 出来。

        某一列只有部分时刻有值（接口偶尔缺格）时，只在**有值的那些点**之间插值：
        否则一个 None 会把前后几小时全变成"查不到"。
        """
        points = self._index().get(_day_key(_naive(when)))
        if not points:
            return None
        try:
            filled = [p for p in points if getattr(p, attr, None) is not None]
            if not filled:
                return None
            return self._interp(filled, when, attr)
        except (TypeError, ValueError):
            return None

    def day_extremes(self, day) -> tuple[float, float] | None:
        """那一天的（最高, 最低）气温：优先用接口给的日预报，没有就按逐小时算。

        卡片上写"今日 12° ~ 24°"用的是它。两个来源都要能用：日预报是接口直接
        给的，逐小时那张表是本来就有的——老缓存里没有 daily，也不会因此空着。
        """
        key = _day_key(_naive(day)) if not isinstance(day, str) else day[:10]
        row = self.daily.get(key)
        if row:
            try:
                return float(row[0]), float(row[1])
            except (TypeError, ValueError, IndexError):
                pass
        points = self._index().get(key)
        if not points:
            return None
        temps = [p.temp for p in points if p.temp is not None]
        if not temps:
            return None
        return max(temps), min(temps)


def _naive(dt: datetime) -> datetime:
    """统一成"不带时区的本地时间"，方便和 Open-Meteo 返回的本地时刻比较。"""
    if isinstance(dt, datetime):
        return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt
    return dt                          # 只是个 date：原样用


def _opt_float(value):
    """能变成数就是数，否则 None（接口给 null / 缺字段时都用得上）。"""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fetch_json(url: str, timeout: float = 9.0):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch(lat: float, lon: float, tz: str = "auto",
          start_date=None, end_date=None,
          forecast_days: int = FORECAST_DAYS) -> Weather:
    """问一次 Open-Meteo。

    不给日期就是"从今天起 forecast_days 天"；给了 start_date / end_date 就只
    要那一段（预览到某一天、手上又没有那天时用，见 WeatherService.ensure_day）。
    两个参数不能同时给：接口会把它们当成自相矛盾的请求。
    """
    params = {
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "current": ("temperature_2m,relative_humidity_2m,apparent_temperature,"
                    "precipitation,weather_code,cloud_cover,wind_speed_10m,"
                    "wind_direction_10m,wind_gusts_10m,dew_point_2m,pressure_msl,"
                    "cloud_cover_low,cloud_cover_mid,cloud_cover_high"),
        "hourly": ("temperature_2m,weather_code,cloud_cover,precipitation_probability,"
                   "precipitation,visibility,dew_point_2m,pressure_msl,uv_index,"
                   "cloud_cover_low,cloud_cover_mid,cloud_cover_high"),
        # 日预报：卡片上"今日 12° ~ 24°"用它（逐小时表也算得出来，但接口直接
        # 给了更准的日最高/最低）。日出日落**不要**——那是本地算的，见 DESIGN。
        "daily": "temperature_2m_max,temperature_2m_min",
        "timezone": tz or "auto",
        "wind_speed_unit": "kmh",
    }
    if start_date is not None and end_date is not None:
        params["start_date"] = (_day_key(start_date) if not isinstance(start_date, str)
                                else start_date)
        params["end_date"] = (_day_key(end_date) if not isinstance(end_date, str)
                              else end_date)
    else:
        params["forecast_days"] = str(max(1, min(MAX_FORECAST_DAYS, int(forecast_days))))
    data = _fetch_json(API + "?" + urllib.parse.urlencode(params))
    w = Weather(ok=True, fetched_at=_time.time(), lat=lat, lon=lon)
    cur = data.get("current", {})
    w.temp = float(cur.get("temperature_2m") or 0.0)
    w.apparent = float(cur.get("apparent_temperature") or w.temp)
    w.humidity = float(cur.get("relative_humidity_2m") or 0.0)
    w.code = int(cur.get("weather_code") or 0)
    w.cloud = float(cur.get("cloud_cover") or 0.0)
    w.wind_speed = float(cur.get("wind_speed_10m") or 0.0)
    w.wind_dir = float(cur.get("wind_direction_10m") or 0.0)
    w.gusts = float(cur.get("wind_gusts_10m") or 0.0)
    w.precip = float(cur.get("precipitation") or 0.0)
    w.dew = _opt_float(cur.get("dew_point_2m"))
    w.pressure = float(cur.get("pressure_msl") or 0.0)
    w.cloud_low = float(cur.get("cloud_cover_low") or 0.0)
    w.cloud_mid = float(cur.get("cloud_cover_mid") or 0.0)
    w.cloud_high = float(cur.get("cloud_cover_high") or 0.0)
    h = data.get("hourly", {})
    times = h.get("time") or []

    def col(name, i, fallback=0.0):
        values = h.get(name) or []
        if i >= len(values) or values[i] is None:
            return float(fallback)
        return float(values[i])

    def opt(name, i):
        """可选项：没有那一列 / 那一格是空的 → None（别编一个 0 出来）。"""
        values = h.get(name) or []
        if i >= len(values) or values[i] is None:
            return None
        try:
            return float(values[i])
        except (TypeError, ValueError):
            return None

    for i, t in enumerate(times):
        try:
            when = datetime.fromisoformat(t)
        except ValueError:
            continue
        w.hourly.append(HourPoint(
            when=when,
            cloud=col("cloud_cover", i),
            code=int(col("weather_code", i)),
            temp=col("temperature_2m", i),
            precip_prob=col("precipitation_probability", i),
            precip_mm=col("precipitation", i),
            vis=col("visibility", i, 24000.0),
            cloud_low=col("cloud_cover_low", i),
            cloud_mid=col("cloud_cover_mid", i),
            cloud_high=col("cloud_cover_high", i),
            dew=opt("dew_point_2m", i),
            pressure=col("pressure_msl", i),
            uv=opt("uv_index", i),
        ))
    w.invalidate()
    d = data.get("daily", {})
    days = d.get("time") or []
    for i, day in enumerate(days):
        hi = (d.get("temperature_2m_max") or [])
        lo = (d.get("temperature_2m_min") or [])
        if i < len(hi) and i < len(lo) and hi[i] is not None and lo[i] is not None:
            try:
                w.daily[str(day)[:10]] = (float(hi[i]), float(lo[i]))
            except (TypeError, ValueError):
                continue
    # "此刻"的能见度取离现在最近的那一格。以前直接拿 00:00 那格，而且请求里
    # 根本没要 visibility，于是详情里永远写着默认的 24 km。
    vis_now = w.visibility_at(_now_in(tz) if tz and tz != "auto" else datetime.now())
    if vis_now:
        w.visibility = float(vis_now)
    return w


def _now_in(tz: str) -> datetime:
    """那个地方的"现在"（时区名字不认识就退回本机时间）。"""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(tz))
    except Exception:
        return datetime.now().astimezone()


def merge(older: Weather | None, newer: Weather, today=None) -> Weather:
    """把新一轮的数据并到旧的那份上（同一座城市时）。

    预览"附近几天"时我们会按那一天单独问一次；如果直接把整张表换掉，"此刻"
    那一份就没了（界面上会变成没有天气）。所以逐小时表按天取并集：哪天有数据
    哪天是真的，缺的天照旧没有——绝不拿邻天顶替。

    "此刻"那一组字段（气温、湿度、风…）只在**这一轮覆盖到今天**时才更新，
    否则保留旧的：它们是此刻的读数，不能被一份三天后的预报顶掉。
    """
    if older is None or not older.ok or not older.hourly or not newer.ok:
        return newer
    if not newer.hourly or not older.matches(newer.lat, newer.lon):
        return newer                    # 换了城市：旧的一律不算数
    # 这一轮**自己**有没有问到"今天"？合并之后今天总会有点数据（旧的还在），
    # 所以一定要在合并之前问这一句，否则"补问三天后"会把此刻的读数也顶掉。
    covers_now = today is None or newer.has_day(today)
    rows = {p.when: p for p in older.hourly}
    for p in newer.hourly:
        rows[p.when] = p
    newer.hourly = [rows[k] for k in sorted(rows)]
    # 日预报（最高 / 最低）按天取并集：补问某一小时的那一枪只带回那几天
    merged_daily = dict(older.daily or {})
    merged_daily.update(newer.daily or {})
    newer.daily = merged_daily
    if today is not None:               # 太老的那些天留着也没用
        cutoff = today - timedelta(days=PAST_LIMIT_DAYS + 1)
        newer.hourly = [p for p in newer.hourly if p.when.date() >= cutoff]
    newer.invalidate()
    if not covers_now:
        for name in ("temp", "apparent", "humidity", "code", "cloud", "wind_speed",
                     "wind_dir", "gusts", "precip", "visibility", "dew",
                     "pressure", "uv", "cloud_low", "cloud_mid", "cloud_high"):
            setattr(newer, name, getattr(older, name))
        newer.fetched_at = older.fetched_at
        newer.stale, newer.error = older.stale, older.error
    return newer


def geocode(query: str, count: int = 6) -> list[dict]:
    params = {"name": query, "count": str(count), "language": "zh", "format": "json"}
    data = _fetch_json(GEOCODE_API + "?" + urllib.parse.urlencode(params))
    out = []
    for r in data.get("results") or []:
        out.append({
            "name": r.get("name") or "",
            "admin": r.get("admin1") or "",
            "country": r.get("country") or "",
            "lat": float(r.get("latitude")),
            "lon": float(r.get("longitude")),
            "timezone": r.get("timezone") or "auto",
            "population": r.get("population") or 0,
        })
    return out


def timezone_for(lat: float, lon: float, timeout: float = 6.0) -> str:
    """按经纬度问一次时区，返回 IANA 名字（如 Asia/Shanghai）；问不到就返回 ""。

    手输经纬度时以前只按经度取整成 `Etc/GMT±N`，那是**固定偏移、没有夏令时**：
    柏林（13.4°E）夏天会得到一个比真实时间早一小时的"窗"。Open-Meteo 的
    `timezone=auto` 正好会回一个 IANA 名字，而 zoneinfo 认的就是这个——
    夏令时、历史规则全都跟着对了。断网时退回按经度取整（见 fallback_timezone）。
    """
    params = {
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "timezone": "auto",
        "forecast_days": "1",
        "current": "temperature_2m",
    }
    try:
        data = _fetch_json(API + "?" + urllib.parse.urlencode(params), timeout=timeout)
    except Exception:
        return ""
    tz = data.get("timezone") or ""
    # 只认 zoneinfo 真的能解析的名字，"GMT"/"auto" 这类不算
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(tz)
    except Exception:
        return ""
    return tz


def fallback_timezone(lon: float) -> str:
    """断网时的将就方案：按经度取整成固定偏移（没有夏令时，最多差一小时）。"""
    offset = max(-12, min(12, int(round(lon / 15.0))))
    return f"Etc/GMT{'-' if offset >= 0 else '+'}{abs(offset)}"


# --------------------------------------------------------------------------
# 缓存与后台刷新
# --------------------------------------------------------------------------

def _serialize(w: Weather) -> dict:
    return {
        "fetched_at": w.fetched_at, "temp": w.temp, "apparent": w.apparent,
        "humidity": w.humidity, "code": w.code, "cloud": w.cloud,
        "wind_speed": w.wind_speed, "wind_dir": w.wind_dir, "gusts": w.gusts,
        "precip": w.precip, "visibility": w.visibility,
        "dew": w.dew, "pressure": w.pressure, "uv": w.uv,
        "cloud_low": w.cloud_low, "cloud_mid": w.cloud_mid,
        "cloud_high": w.cloud_high,
        "daily": {k: [v[0], v[1]] for k, v in (w.daily or {}).items()},
        "lat": w.lat, "lon": w.lon,
        "hourly": [[p.when.isoformat(), p.cloud, p.code, p.temp, p.precip_prob,
                    p.precip_mm, p.vis, p.cloud_low, p.cloud_mid, p.cloud_high,
                    p.dew, p.pressure, p.uv] for p in w.hourly],
    }


def _deserialize(d: dict) -> Weather:
    w = Weather(ok=True, stale=True)
    w.fetched_at = float(d.get("fetched_at") or 0.0)
    for key in ("temp", "apparent", "humidity", "cloud", "wind_speed",
                "wind_dir", "gusts", "precip", "visibility", "pressure",
                "cloud_low", "cloud_mid", "cloud_high"):
        setattr(w, key, float(d.get(key) or 0.0))
    w.dew = _opt_float(d.get("dew"))
    w.uv = _opt_float(d.get("uv"))
    for key, value in (d.get("daily") or {}).items():
        try:
            w.daily[str(key)[:10]] = (float(value[0]), float(value[1]))
        except (TypeError, ValueError, IndexError):
            continue
    w.lat = float(d.get("lat") or 0.0)
    w.lon = float(d.get("lon") or 0.0)
    w.code = int(d.get("code") or 0)
    for row in d.get("hourly") or []:
        try:
            # 老缓存的行只有 5 列（没有降水与能见度）、再老一点的更短，
            # 照旧读得进来：多出来的那几列（云层 / 露点 / 气压 / 紫外线）
            # 老缓存里没有，读成默认值就行。
            opt = _opt_float
            w.hourly.append(HourPoint(
                datetime.fromisoformat(row[0]), float(row[1]), int(row[2]),
                float(row[3]), float(row[4]),
                float(row[5]) if len(row) > 5 else 0.0,
                float(row[6]) if len(row) > 6 else 24000.0,
                float(row[7] or 0.0) if len(row) > 7 else 0.0,
                float(row[8] or 0.0) if len(row) > 8 else 0.0,
                float(row[9] or 0.0) if len(row) > 9 else 0.0,
                opt(row[10]) if len(row) > 10 else None,
                float(row[11] or 0.0) if len(row) > 11 else 0.0,
                opt(row[12]) if len(row) > 12 else None))
        except (ValueError, IndexError, TypeError):
            continue
    w.invalidate()
    return w


def load_cache() -> Weather | None:
    try:
        raw = json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        return _deserialize(raw)
    except (TypeError, ValueError):
        return None


def save_cache(w: Weather) -> None:
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(_serialize(w)), encoding="utf-8")
    except OSError:
        pass


class WeatherService:
    """后台线程抓取 + 过期时间管理。回调在主线程被调用。"""

    def __init__(self, on_update):
        self.on_update = on_update
        self.weather: Weather | None = load_cache()
        self._lock = threading.Lock()
        self._busy = False
        self.lat = 0.0
        self.lon = 0.0
        self.tz = "auto"
        self.enabled = True       # 用户开关：「跟随真实天气」开不开
        self.allow_fetch = True   # 允不允许联网（CHUANG_WEATHER 的假天气会关掉它）
        self._asked_at: dict[str, float] = {}   # 某一天上次补问的时刻（限流）
        self._failed_at: dict[str, float] = {}  # 某一天上次问失败的时刻（退避）
        self._wanted = False        # 有一次强制刷新没发出去（上一次还在飞）
        self._wanted_done = None    # 那一枪的回声（按 R 时给的那句话）

    def today(self):
        """这扇窗所在的地方"今天"是几号。"""
        return today_in(self.tz)

    @property
    def effective(self) -> Weather | None:
        """此刻**该用来作画**的那份天气；用户关掉「跟随真实天气」时是 None。

        和 self.weather 的区别是"有没有"与"要不要"：weather 是缓存里有什么
        （断网也能接着用上一份，那是对的），effective 是用户要不要看天气。
        画面、长卷、壁纸一律用 effective——否则这个开关只挡住了联网刷新，
        磁盘里那份缓存（第一次成功抓取之后就一直存在）还会永远画在窗上，
        开关看起来就跟坏了一样。
        """
        if not self.enabled or self.weather is None:
            return None
        # 换了城市之后、新数据还没回来之前，缓存里那份是**上一座城**的天气：
        # 画出来就是"在柏林的窗里下着西安的雨"。宁可先说"还没问到"。
        if not self.weather.matches(self.lat, self.lon):
            return None
        return self.weather

    def set_location(self, lat: float, lon: float, tz: str) -> None:
        with self._lock:
            changed = (abs(lat - self.lat) > 1e-6 or abs(lon - self.lon) > 1e-6)
            self.lat, self.lon, self.tz = lat, lon, tz
        if changed:
            self.refresh(force=True)

    def maybe_refresh(self) -> None:
        if not (self.enabled and self.allow_fetch):
            return
        w = self.weather
        if w is None or (_time.time() - w.fetched_at) > REFRESH_SECONDS:
            self.refresh()

    def watch(self, when) -> None:
        """心跳里叫一句：画面正在看的那一天，手上得有真数据（预览用的）。"""
        if when is not None:
            self.ensure_day(when.date())

    def ensure_day(self, day, done=None) -> bool:
        """预览到某一天：手上没有那天的预报，就单独按那一天再问一次。

        返回"有没有真的去问"。同一天 20 秒内只问一遍、失败后退避 10 分钟，
        所以长卷一路拖过去也不会把接口刷爆；拖动停下之后心跳会接着把它补齐。
        """
        if not (self.enabled and self.allow_fetch) or self._busy:
            return False
        w = self.weather
        if w is None or not w.ok or not w.hourly:
            return False                     # 连"此刻"都还没有，等常规刷新
        if w.has_day(day):
            return False
        # 手里这份还是**别的城市**的（刚换完城市、新一轮还没回来）：别按天补。
        # 那一枪只带回薄薄一窗口（那天的前后两三天），而 merge() 遇到"换了城市"
        # 是"旧的一律不算数"——于是整张表会被这一窗口顶掉，连"此刻"都没了，
        # 卡片上写着"这天还没有预报"，要等下一个十分钟周期才恢复。
        # 换城市本来就会强制整表刷一次，等它回来再按天补。
        if not w.matches(self.lat, self.lon):
            return False
        today = self.today()
        try:
            delta = (day - today).days
        except TypeError:                    # 传进来的是 datetime 之类
            return False
        if delta > MAX_FORECAST_DAYS or delta < -PAST_LIMIT_DAYS:
            return False                     # 预报只到 16 天，问也白问
        key = _day_key(day) if not isinstance(day, str) else day
        now = _time.monotonic()
        if now - self._asked_at.get(key, -1e9) < DAY_QUERY_COOLDOWN:
            return False
        if now - self._failed_at.get(key, -1e9) < DAY_QUERY_BACKOFF:
            return False
        self._asked_at[key] = now
        return self.refresh(
            window=(day - timedelta(days=1), day + timedelta(days=2)), done=done)

    def refresh(self, force: bool = False, window=None, done=None) -> bool:
        """去问一次天气。

        window=(第一天, 最后一天) 时只问那几天（预览到远处时补问用）；
        否则问"今天起 FORECAST_DAYS 天"。done 会在**主线程**拿到新的天气之后
        被调一次——「问一次真实天气」那个按钮靠它给出成功 / 失败的反馈。
        """
        if self._busy:
            if force:
                # 换城市、按 R 都走这条：上一次还在飞，这一枪**没发出去**。
                # 记下来，等它回来立刻补上——否则"刚换完城市"要等到下一个
                # 十分钟周期才有天气，画面上写着"未联网"，可网络明明好好的。
                self._wanted = True
                self._wanted_done = done or self._wanted_done
            return False
        if not (self.enabled and self.allow_fetch):
            return False
        if force:
            # 这一枪真的发得出去：之前"没发出去、等回来补"的那笔作数，别再补一次
            self._wanted = False
            self._wanted_done = None
        self._busy = True
        lat, lon, tz = self.lat, self.lon, self.tz
        start, end = window if window else (None, None)
        today = today_in(tz)

        def worker():
            from gi.repository import GLib
            try:
                fresh = fetch(lat, lon, tz, start_date=start, end_date=end)
                w = merge(self.weather if self.weather is not None else None,
                          fresh, today)
                save_cache(w)
            except Exception as exc:  # 网络、超时、解析失败都走这里
                w = self.weather
                if window is not None:
                    # 补问某一天失败（多半是那天超出预报范围了）：手上那份
                    # 一个字节都不动，只把这一天记下来、过一阵再说。
                    key = _day_key(start)
                    self._busy = False
                    GLib.idle_add(self._note_day_failed, key)
                    return
                if w is not None:
                    w.stale = True          # 断网：接着显示上一份，并标明是旧的
                    w.error = str(exc)[:80]
                else:
                    w = Weather(ok=False, error=str(exc)[:80])
            self._busy = False
            GLib.idle_add(self._deliver, w, done)

        threading.Thread(target=worker, daemon=True, name="chuang-weather").start()
        return True

    def _note_day_failed(self, day_key: str) -> bool:
        self._failed_at[day_key] = _time.monotonic()
        return False

    def _deliver(self, w: Weather, done=None) -> bool:
        self.weather = w
        try:
            self.on_update(w)
        except Exception:
            pass
        if done is not None:
            try:
                done(w)
            except Exception:               # 反馈那一步炸了不该带走天气
                pass
        # 这一枪在飞的时候还按过"问一次" / 换过城市：现在补上，别让那句话
        # 石沉大海（以前它就是被静默吞掉的）
        if self._wanted and self.enabled and self.allow_fetch:
            self._wanted = False
            nxt, self._wanted_done = self._wanted_done, None
            self.refresh(force=True, done=nxt)
        return False
