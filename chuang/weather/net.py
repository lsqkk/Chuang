
"""只碰网络的那几件事：抓预报、按名字找城市、按经纬度查时区。

每个请求都带超时（`_fetch_json`），失败一律抛给上层——上层（`service.py`）负责
"退回上一份缓存、标成 stale"，这里不做决定。只上传经纬度，不含任何身份信息。
"""

from __future__ import annotations

import json
import time as _time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .const import (API, FORECAST_DAYS, GEOCODE_API, MAX_FORECAST_DAYS,
                    PAST_LIMIT_DAYS, UA)
from .model import HourPoint, Weather, _day_key, _naive, _opt_float



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
