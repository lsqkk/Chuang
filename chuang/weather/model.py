
"""一份天气预报是什么：`Weather` / `HourPoint`，按时刻插值、按天合并、读写缓存。

两条规矩写在函数注释里、改之前先看：① **逐小时表新加列只能往后追加**（缓存按
列号读，插在中间会让老缓存整段错位）；② `merge()` 遇到"换了城市"时旧的一律不算数
（否则刚换完城市会出现"柏林窗里下着西安的雨"）。这一层**不碰网络**。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .const import CACHE, PAST_LIMIT_DAYS, SAME_PLACE_DEG
from .codes import code_text, precip_kind, precip_strength



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
