"""真实天气：Open-Meteo 免费接口（无需 API Key），带本地缓存与离线降级。

只上传经纬度，不含任何身份信息。断网时退回"纯天文模式"——
天空依旧正确，只是不知道有没有云。
"""

from __future__ import annotations

import json
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


def precip_kind(code: int) -> str:
    """降水类型：none / rain / snow。"""
    code = int(code)
    if code in (71, 73, 75, 77, 85, 86):
        return "snow"
    if code in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99):
        return "rain"
    return "none"


def precip_strength(code: int, precip_mm: float = 0.0) -> float:
    """降水强度 0-1，用于决定雨丝的密度与速度。"""
    table = {
        51: 0.18, 53: 0.30, 55: 0.45, 56: 0.30, 57: 0.45,
        61: 0.35, 63: 0.55, 65: 0.85, 66: 0.55, 67: 0.85,
        71: 0.30, 73: 0.50, 75: 0.75, 77: 0.25,
        80: 0.45, 81: 0.70, 82: 1.00, 85: 0.55, 86: 0.80,
        95: 0.75, 96: 0.90, 99: 1.00,
    }
    base = table.get(int(code), 0.0)
    if precip_mm:
        base = max(base, min(1.0, precip_mm / 6.0))
    return base


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
    hourly: list[HourPoint] = field(default_factory=list)
    place: str = ""

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

    def cloud_at(self, when: datetime) -> float:
        """按小时预报插值出某一时刻的云量（0-100）。"""
        if not self.hourly:
            return self.cloud
        pts = self.hourly
        when = _naive(when)
        first, last = _naive(pts[0].when), _naive(pts[-1].when)
        if when <= first:
            return pts[0].cloud
        if when >= last:
            return pts[-1].cloud
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            aw, bw = _naive(a.when), _naive(b.when)
            if aw <= when <= bw:
                span = (bw - aw).total_seconds() or 1.0
                t = (when - aw).total_seconds() / span
                return a.cloud + (b.cloud - a.cloud) * t
        return self.cloud

    def code_at(self, when: datetime) -> int:
        if not self.hourly:
            return self.code
        when = _naive(when)
        best = self.hourly[0]
        for p in self.hourly:
            pw, bw_ = _naive(p.when), _naive(best.when)
            if abs((pw - when).total_seconds()) < abs((bw_ - when).total_seconds()):
                best = p
        return best.code


def _naive(dt: datetime) -> datetime:
    """统一成"不带时区的本地时间"，方便和 Open-Meteo 返回的本地时刻比较。"""
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def _fetch_json(url: str, timeout: float = 9.0):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch(lat: float, lon: float, tz: str = "auto") -> Weather:
    params = {
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "current": ("temperature_2m,relative_humidity_2m,apparent_temperature,"
                    "precipitation,weather_code,cloud_cover,wind_speed_10m,"
                    "wind_direction_10m,wind_gusts_10m"),
        "hourly": "temperature_2m,weather_code,cloud_cover,precipitation_probability",
        "timezone": tz or "auto",
        "forecast_days": "2",
        "wind_speed_unit": "kmh",
    }
    data = _fetch_json(API + "?" + urllib.parse.urlencode(params))
    w = Weather(ok=True, fetched_at=_time.time())
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
    h = data.get("hourly", {})
    times = h.get("time") or []
    for i, t in enumerate(times):
        try:
            when = datetime.fromisoformat(t)
        except ValueError:
            continue
        w.hourly.append(HourPoint(
            when=when,
            cloud=float((h.get("cloud_cover") or [0])[i] or 0.0),
            code=int((h.get("weather_code") or [0])[i] or 0),
            temp=float((h.get("temperature_2m") or [0])[i] or 0.0),
            precip_prob=float((h.get("precipitation_probability") or [0])[i] or 0.0),
        ))
    vis = (h.get("visibility") or [])
    if vis:
        w.visibility = float(vis[0] or 24000.0)
    return w


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


# --------------------------------------------------------------------------
# 缓存与后台刷新
# --------------------------------------------------------------------------

def _serialize(w: Weather) -> dict:
    return {
        "fetched_at": w.fetched_at, "temp": w.temp, "apparent": w.apparent,
        "humidity": w.humidity, "code": w.code, "cloud": w.cloud,
        "wind_speed": w.wind_speed, "wind_dir": w.wind_dir, "gusts": w.gusts,
        "precip": w.precip, "visibility": w.visibility,
        "hourly": [[p.when.isoformat(), p.cloud, p.code, p.temp, p.precip_prob] for p in w.hourly],
    }


def _deserialize(d: dict) -> Weather:
    w = Weather(ok=True, stale=True)
    w.fetched_at = float(d.get("fetched_at") or 0.0)
    for key in ("temp", "apparent", "humidity", "cloud", "wind_speed",
                "wind_dir", "gusts", "precip", "visibility"):
        setattr(w, key, float(d.get(key) or 0.0))
    w.code = int(d.get("code") or 0)
    for row in d.get("hourly") or []:
        try:
            w.hourly.append(HourPoint(datetime.fromisoformat(row[0]), float(row[1]),
                                      int(row[2]), float(row[3]), float(row[4])))
        except (ValueError, IndexError, TypeError):
            continue
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
        self.enabled = True

    def set_location(self, lat: float, lon: float, tz: str) -> None:
        with self._lock:
            changed = (abs(lat - self.lat) > 1e-6 or abs(lon - self.lon) > 1e-6)
            self.lat, self.lon, self.tz = lat, lon, tz
        if changed:
            self.refresh(force=True)

    def maybe_refresh(self) -> None:
        if not self.enabled:
            return
        w = self.weather
        if w is None or (_time.time() - w.fetched_at) > REFRESH_SECONDS:
            self.refresh()

    def refresh(self, force: bool = False) -> None:
        if self._busy:
            return
        if not self.enabled:
            return
        self._busy = True
        lat, lon, tz = self.lat, self.lon, self.tz

        def worker():
            from gi.repository import GLib
            try:
                w = fetch(lat, lon, tz)
                save_cache(w)
            except Exception as exc:  # 网络、超时、解析失败都走这里
                w = self.weather
                if w is not None:
                    w.stale = True
                    w.error = str(exc)[:80]
                else:
                    w = Weather(ok=False, error=str(exc)[:80])
            self._busy = False
            GLib.idle_add(self._deliver, w)

        threading.Thread(target=worker, daemon=True, name="chuang-weather").start()

    def _deliver(self, w: Weather) -> bool:
        self.weather = w
        try:
            self.on_update(w)
        except Exception:
            pass
        return False
