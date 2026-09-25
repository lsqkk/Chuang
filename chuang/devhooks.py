"""两个只有开发时才用的环境变量：`CHUANG_TIME` 与 `CHUANG_WEATHER`。

    CHUANG_TIME=21:30               假装此刻是 21:30
    CHUANG_TIME=2026-09-23T18:40    假装到那一分钟
    CHUANG_WEATHER=63:95:18:200     中雨 / 云量 95% / 风 18km/h / 风来向 200°

它们**只影响这一跑**：不写配置、不碰缓存、一个字节也不往外发（假天气那条路会
把 `weather.allow_fetch` 关掉）。解析不出来就当没设——宁可退回真实的时间与
天气，也不要让一个打错的调试变量把画面变成假的，那正是"看不到真东西又找不到
原因"的那类麻烦（见 AGENTS.md §6）。

真出现假数据的时候，卡片底下那行会照常写"天气更新于 …"，不会假装是刚从
Open-Meteo 拿回来的。
"""

from __future__ import annotations

import os
import time as _time
from datetime import datetime, timedelta


def fake_time(engine, config) -> datetime | None:
    """`CHUANG_TIME` → 一个"假装此刻"的时刻；没设或写坏了就是 None。"""
    raw = os.environ.get("CHUANG_TIME")
    if not raw:
        return None
    tz = engine._resolve_tz(config.location.timezone)
    now = datetime.now(tz) if tz else datetime.now().astimezone()
    try:
        if "T" in raw:
            dt = datetime.fromisoformat(raw)
            return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt
        hh, mm = (int(x) for x in raw.split(":")[:2])
        return now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    except ValueError:
        return None


def fake_weather(engine) -> "Weather | None":
    """`CHUANG_WEATHER=天气码:云量:风速:风向[:雨量]` → 一份当天的假天气。"""
    from .weather import HourPoint, Weather
    raw = os.environ.get("CHUANG_WEATHER")
    if not raw:
        return None
    try:
        parts = [float(x) for x in raw.split(":")]
        code = int(parts[0])
        cloud = parts[1] if len(parts) > 1 else 0.0
        wind = parts[2] if len(parts) > 2 else 0.0
        wdir = parts[3] if len(parts) > 3 else 0.0
        streak = parts[4] if len(parts) > 4 else 0.0
    except ValueError:
        return None
    w = Weather(ok=True, fetched_at=_time.time(), code=code, cloud=cloud,
                wind_speed=wind, wind_dir=wdir, temp=21.0, apparent=21.0,
                humidity=68.0, precip=streak, visibility=12000.0)
    base = engine.local_date().replace(tzinfo=None)
    for i in range(48):
        w.hourly.append(HourPoint(base + timedelta(hours=i), cloud, code, 20.0, 40.0))
    return w
