
"""`WeatherService`：后台线程抓取、过期时间管理、限流与退避，以及"问一次"的回声。

约定了三件事：① 抓取在后台线程里跑，回主线程一律走 `mainloop.to_main()`；
② 同一时刻只允许一枪在飞（`busy`），飞着的时候又来一枪就记进 `_wanted`、等它回来
立刻补发（否则"刚换完城市 / 刚按 R"要等到下一个十分钟周期才有天气）；
③ `effective` 才决定"画不画天气"——用户关掉开关之后，磁盘里那份缓存也不许再画。
"""

from __future__ import annotations

import threading
import time as _time
from datetime import timedelta

from ..mainloop import to_main
from .const import (DAY_QUERY_BACKOFF, DAY_QUERY_COOLDOWN, FORECAST_DAYS,
                    MAX_FORECAST_DAYS, PAST_LIMIT_DAYS, REFRESH_SECONDS)
from .model import Weather, _day_key, load_cache, merge, save_cache, today_in
from .net import fetch



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
    def busy(self) -> bool:
        """此刻是不是正有一枪在飞（信息卡上"正在问天气"那个状态用它）。

        以前只有私有的 `_busy`，界面那边想看也看不到：按下 R 之后画面上一点
        回声都没有，只能干等结果（见 infocard.InfoCard.refresh_weather）。
        """
        return self._busy

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
                    to_main(self._note_day_failed, key)
                    return
                if w is not None:
                    w.stale = True          # 断网：接着显示上一份，并标明是旧的
                    w.error = str(exc)[:80]
                else:
                    w = Weather(ok=False, error=str(exc)[:80])
            self._busy = False
            to_main(self._deliver, w, done)

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
