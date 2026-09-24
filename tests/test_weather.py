"""天气服务：用户开关、缓存降级、插值。"""

import json
import tempfile
import time
import unittest
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from unittest import mock

from chuang import weather as W
from chuang.scene import SkyEngine

# 在**主线程里先**把 GLib 导进来：Gi 的类型包装不是线程安全的，等后台抓取
# 线程和测试线程同时第一次导入它，PyGObject 会给出一个类型对不上的半成品
# （"Expected GLib.MainContext, but got gi.repository.GLib.MainContext"）。
try:
    from gi.repository import GLib as _GLib
    HAS_GLIB = True
except Exception:                       # noqa: BLE001 - 没有 PyGObject 就跳过那两条
    HAS_GLIB = False


def _fake_service(tmp: Path, *, enabled: bool = True):
    """一个有缓存、但不会联网的 WeatherService。"""
    with mock.patch.object(W, "CACHE", tmp / "weather.json"):
        W.save_cache(W.Weather(ok=True, fetched_at=1e9, cloud=95.0, code=63,
                               temp=18.0, wind_speed=9.0, humidity=80.0))
        with mock.patch.object(W, "CACHE", tmp / "weather.json"):
            svc = W.WeatherService(lambda _w: None)
    svc.enabled = enabled
    svc.allow_fetch = False
    return svc


def _weather_days(first: date, count: int, *, cloud: float = 40.0, code: int = 1,
                  temp: float = 18.0, lat: float = 34.34, lon: float = 108.94):
    """造一份"有连续 count 天逐小时数据"的天气。"""
    w = W.Weather(ok=True, fetched_at=1e9, code=code, cloud=cloud, temp=temp,
                  apparent=temp, humidity=70.0, wind_speed=6.0, wind_dir=180.0,
                  precip=0.0, lat=lat, lon=lon)
    for d in range(count):
        base = datetime.combine(first + timedelta(days=d), dtime(0, 0))
        for h in range(24):
            w.hourly.append(W.HourPoint(base + timedelta(hours=h), cloud, code,
                                        temp, 50.0))
    w.invalidate()
    return w


def _pump_glib(predicate, timeout: float = 5.0) -> bool:
    """转一会儿 GLib 主循环（后台线程干完活会 idle_add 回主线程）。"""
    loop = _GLib.MainLoop()

    def tick():
        if predicate():
            loop.quit()
            return False
        return True

    _GLib.timeout_add(20, tick)
    _GLib.timeout_add(int(timeout * 1000), lambda: (loop.quit(), False)[1])
    loop.run()
    return bool(predicate())


def _service_with_days(tmp: Path, days: int, today: date):
    """缓存里有 today 起 days 天逐小时数据的服务（不联网）。"""
    p = tmp / "weather.json"
    with mock.patch.object(W, "CACHE", p):
        W.save_cache(_weather_days(today, days))
        svc = W.WeatherService(lambda _w: None)
    svc.allow_fetch = True
    svc.lat, svc.lon, svc.tz = 34.34, 108.94, "Asia/Shanghai"
    return svc


class TestEffective(unittest.TestCase):
    """「跟随真实天气」关掉之后，缓存里的天气也不能再画出来。

    1.1.7 及以前这个开关只挡住"联网刷新"，磁盘缓存（第一次成功抓取之后
    就一直在）照样被画进窗里，于是关掉开关画面里还有雨——开关看起来是坏的。
    """

    def test_disabled_means_no_weather_on_screen(self):
        with tempfile.TemporaryDirectory() as d:
            svc = _fake_service(Path(d), enabled=False)
            self.assertIsNotNone(svc.weather)          # 缓存还在（那是"有没有"）
            self.assertIsNone(svc.effective)           # 但不该拿来作画
            eng = SkyEngine()
            eng.set_location(34.34, 108.94, "Asia/Shanghai")
            sc = eng.build(eng.local_now(), svc.effective, weather_off=True)
            self.assertFalse(sc.has_weather)
            self.assertTrue(sc.weather_disabled)
            self.assertEqual(sc.cloud, 0.0)
            self.assertEqual(sc.precip_kind, "none")

    def test_enabled_still_paints_weather(self):
        with tempfile.TemporaryDirectory() as d:
            svc = _fake_service(Path(d), enabled=True)
            self.assertIs(svc.effective, svc.weather)
            eng = SkyEngine()
            eng.set_location(34.34, 108.94, "Asia/Shanghai")
            sc = eng.build(eng.local_now(), svc.effective)
            self.assertTrue(sc.has_weather)
            self.assertFalse(sc.weather_disabled)

    def test_disabled_does_not_fetch(self):
        with tempfile.TemporaryDirectory() as d:
            svc = _fake_service(Path(d), enabled=False)
            with mock.patch.object(W, "fetch") as fetch:
                svc.refresh(force=True)
                svc.maybe_refresh()
                fetch.assert_not_called()

    def test_offline_cache_is_still_used_when_enabled(self):
        """断网时用上一份缓存、并标成 stale——这是对的，别一起关掉。"""
        with tempfile.TemporaryDirectory() as d:
            svc = _fake_service(Path(d), enabled=True)
            self.assertTrue(svc.weather.ok)
            self.assertTrue(svc.weather.stale)


class TestWeatherData(unittest.TestCase):

    def _hourly(self):
        base = datetime(2026, 9, 23, 0, 0)
        return [W.HourPoint(base + timedelta(hours=i), cloud=float(i * 10),
                            code=1 if i < 12 else 63, temp=20.0, precip_prob=0.0)
                for i in range(24)]

    def test_cloud_at_interpolates_and_clamps(self):
        w = W.Weather(ok=True, hourly=self._hourly())
        self.assertEqual(w.cloud_at(datetime(2026, 9, 23, 0, 0)), 0.0)
        self.assertAlmostEqual(w.cloud_at(datetime(2026, 9, 23, 1, 30)), 15.0)
        self.assertEqual(w.cloud_at(datetime(2026, 9, 23, 23, 30)), 230.0)  # 夹到当天末尾

    def test_only_the_days_we_actually_have_are_answered(self):
        """没有那一天的预报就是"没有"，不许拿邻天顶替。

        以前表里只有两天，"跳到下周三"会拿到表尾（明天深夜）那格当预报——
        看着像真数据，其实差着好几天。
        """
        w = W.Weather(ok=True, hourly=self._hourly())
        self.assertTrue(w.has_day(datetime(2026, 9, 23)))
        self.assertFalse(w.has_day(datetime(2026, 9, 24)))
        self.assertIsNone(w.cloud_at(datetime(2026, 9, 22, 0, 0)))
        self.assertIsNone(w.cloud_at(datetime(2026, 9, 24, 0, 0)))
        self.assertIsNone(w.code_at(datetime(2026, 9, 25, 12, 0)))
        self.assertIsNone(w.temp_at(datetime(2026, 9, 25, 12, 0)))
        self.assertEqual(w.day_span(), ("2026-09-23", "2026-09-23"))

    def test_code_at_picks_nearest(self):
        w = W.Weather(ok=True, hourly=self._hourly())
        self.assertEqual(w.code_at(datetime(2026, 9, 23, 10, 10)), 1)
        self.assertEqual(w.code_at(datetime(2026, 9, 23, 20, 0)), 63)

    def test_roundtrip_serialization(self):
        w = W.Weather(ok=True, code=95, cloud=88.0, precip=3.0, hourly=self._hourly())
        back = W._deserialize(W._serialize(w))
        self.assertEqual(back.code, 95)
        self.assertEqual(back.kind, "rain")
        self.assertEqual(len(back.hourly), 24)
        self.assertAlmostEqual(back.cloud_at(datetime(2026, 9, 23, 5, 0)), 50.0)

    def test_broken_cache_does_not_raise(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "weather.json"
            p.write_text("{ 这不是 JSON", encoding="utf-8")
            with mock.patch.object(W, "CACHE", p):
                self.assertIsNone(W.load_cache())
            p.write_text(json.dumps({"code": "??"}), encoding="utf-8")
            with mock.patch.object(W, "CACHE", p):
                self.assertIsNone(W.load_cache())

    def test_precip_helpers(self):
        self.assertEqual(W.precip_kind(63), "rain")
        self.assertEqual(W.precip_kind(73), "snow")
        self.assertEqual(W.precip_kind(0), "none")
        self.assertTrue(W.is_thunder(95))
        self.assertTrue(W.is_fog(45))
        self.assertGreater(W.precip_strength(65), W.precip_strength(51))

    def test_precip_strength_follows_the_real_amount(self):
        """雨丝的密度看雨量：毛毛雨、中雨、大雨要明显三档。

        以前只看 WMO 代码表（51 是 0.18、65 是 0.85），用户看到的"毛毛雨"和
        "大雨"在画面上差不了多少。现在雨量在手上，就按雨量算。
        """
        drizzle = W.precip_strength(51, 0.2)
        mid = W.precip_strength(63, 3.0)
        heavy = W.precip_strength(65, 12.0)
        self.assertLess(drizzle, mid)
        self.assertLess(mid, heavy)
        self.assertLess(drizzle, 0.25, "毛毛雨画得太满了")
        self.assertGreater(heavy, 0.8)
        # 毛毛雨那一族封顶：就算接口给的雨量数字不对，也不许画成倾盆大雨
        self.assertLessEqual(W.precip_strength(55, 40.0), 0.34)
        # 没有雨量数字时，仍然照代码表那一档来（老缓存 / 接口给了 0）
        self.assertAlmostEqual(W.precip_strength(65, 0.0), 0.78, places=2)
        self.assertAlmostEqual(W.precip_strength(51, 0.0), 0.10, places=2)

    def test_precip_intensity_is_monotone(self):
        last = -1.0
        for mm in (0.0, 0.05, 0.2, 0.5, 1.0, 3.0, 8.0, 20.0, 40.0):
            value = W.precip_intensity(mm)
            self.assertGreaterEqual(value, last, f"{mm} mm/时 反而更弱了")
            self.assertLessEqual(value, 1.0)
            last = value

    def test_precip_label_says_how_hard_it_is_raining(self):
        """用户看得见的那句话：说"多大"得按雨量说。"""
        self.assertEqual(W.precip_label(51, 0.2), "毛毛雨")
        self.assertEqual(W.precip_label(61, 0.8), "小雨")
        self.assertEqual(W.precip_label(63, 3.0), "中雨")
        self.assertEqual(W.precip_label(65, 9.0), "大雨")
        self.assertEqual(W.precip_label(82, 30.0), "暴雨")
        self.assertEqual(W.precip_label(95, 3.0), W.code_text(95))   # 雷阵雨照实说
        # 没有雨量：退回代码上的说法，不许编
        self.assertEqual(W.precip_label(63, 0.0), W.code_text(63))
        self.assertEqual(W.precip_label(0, 5.0), "")

    def test_fallback_timezone(self):
        self.assertEqual(W.fallback_timezone(108.94), "Etc/GMT-7")
        self.assertEqual(W.fallback_timezone(-74.01), "Etc/GMT+5")
        self.assertEqual(W.fallback_timezone(0.0), "Etc/GMT-0")


class TestNearbyDays(unittest.TestCase):
    """"预览到某一天，附近几天都得是真的天气"——合并、补问、别顶替。"""

    # 用真实的"今天"：补问那套里有限流与"最多 16 天"的判断，写死日期会在
    # 别的日子上跑到界外去
    TODAY = date.today()

    def test_merge_keeps_the_days_it_already_had(self):
        old = _weather_days(self.TODAY, 3, cloud=10.0)          # 24 ～ 26
        fresh = _weather_days(self.TODAY + timedelta(days=5), 2, cloud=70.0)
        merged = W.merge(old, fresh, self.TODAY)
        self.assertTrue(merged.has_day(self.TODAY), "补问别的日子把'今天'顶掉了")
        self.assertTrue(merged.has_day(self.TODAY + timedelta(days=5)))
        day5 = datetime.combine(self.TODAY + timedelta(days=5), dtime(9, 0))
        self.assertEqual(merged.cloud_at(day5), 70.0)
        # 这一轮问的是三天后，所以"此刻"那一组读数还是旧那份
        self.assertEqual(merged.cloud, old.cloud)
        self.assertEqual(merged.fetched_at, old.fetched_at)

    def test_merge_does_not_mix_up_two_cities(self):
        old = _weather_days(self.TODAY, 3, lat=34.34, lon=108.94)
        other = _weather_days(self.TODAY, 3, cloud=99.0, lat=52.52, lon=13.40)
        merged = W.merge(old, other, self.TODAY)
        self.assertFalse(merged.matches(34.34, 108.94))
        self.assertTrue(merged.matches(52.52, 13.40))
        self.assertEqual(merged.cloud, 99.0)

    def test_very_old_days_get_dropped(self):
        old = _weather_days(self.TODAY - timedelta(days=40), 2)
        fresh = _weather_days(self.TODAY, 1)
        merged = W.merge(old, fresh, self.TODAY)
        self.assertTrue(merged.has_day(self.TODAY))
        self.assertFalse(merged.has_day(self.TODAY - timedelta(days=40)))

    @unittest.skipUnless(HAS_GLIB, "没有 PyGObject，跳过需要主循环的那两条")
    def test_ensure_day_only_asks_for_days_we_do_not_have(self):
        with tempfile.TemporaryDirectory() as d:
            svc = _service_with_days(Path(d), 2, self.TODAY)
            calls = []

            def fake_fetch(lat, lon, tz="auto", start_date=None, end_date=None,
                           forecast_days=7):
                calls.append((start_date, end_date))
                first = start_date or self.TODAY
                return _weather_days(first, (end_date - start_date).days + 1
                                     if end_date else 1)

            self.assertFalse(svc.ensure_day(self.TODAY), "手上有的天不该再问")
            far = self.TODAY + timedelta(days=30)
            self.assertFalse(svc.ensure_day(far), "30 天以外问也白问")
            self.assertEqual(calls, [])
            with mock.patch.object(W, "fetch", side_effect=fake_fetch):
                want = self.TODAY + timedelta(days=4)
                self.assertTrue(svc.ensure_day(want))
                self.assertTrue(_pump_glib(lambda: len(calls) > 0), "没去问")
                self.assertEqual(calls[0], (want - timedelta(days=1),
                                            want + timedelta(days=2)))
                # 问回来的那几天已经并进手里这份了
                self.assertTrue(_pump_glib(
                    lambda: bool(svc.weather and svc.weather.has_day(want))))
                self.assertTrue(svc.weather.has_day(self.TODAY))
                # 同一天不会反复问（拖动长卷时不至于把接口刷爆）
                self.assertFalse(svc.ensure_day(want))

    def test_effective_refuses_another_citys_cache(self):
        with tempfile.TemporaryDirectory() as d:
            svc = _service_with_days(Path(d), 2, self.TODAY)
            svc.allow_fetch = False
            self.assertIsNotNone(svc.effective)
            svc.lat, svc.lon = 52.52, 13.40          # 换到柏林，数据还是西安的
            self.assertIsNone(svc.effective, "换了城市还画着上一座城的天气")

    @unittest.skipUnless(HAS_GLIB, "没有 PyGObject，跳过需要主循环的那两条")
    def test_refresh_reports_the_result(self):
        """「问一次真实天气」要有回声：done 回调在主线程拿到新数据。"""
        with tempfile.TemporaryDirectory() as d:
            svc = _service_with_days(Path(d), 1, self.TODAY)
            got = []

            def fake_fetch(lat, lon, tz="auto", start_date=None, end_date=None,
                           forecast_days=7):
                self.assertEqual(forecast_days, W.FORECAST_DAYS)
                return _weather_days(self.TODAY, 2, cloud=55.0)

            with mock.patch.object(W, "fetch", side_effect=fake_fetch), \
                    mock.patch.object(W, "save_cache"):
                self.assertTrue(svc.refresh(force=True, done=got.append))
                self.assertTrue(_pump_glib(lambda: bool(got)), "done 没有被调到")
            self.assertTrue(got[-1].ok)
            self.assertEqual(got[-1].cloud, 55.0)


class TestFetchRequest(unittest.TestCase):
    """请求参数：默认要 7 天，给窗口就只要那几天。"""

    def test_asks_for_a_week_by_default(self):
        seen = {}

        def fake_json(url, timeout=9.0):
            seen["url"] = url
            return {"current": {}, "hourly": {"time": []}}

        with mock.patch.object(W, "_fetch_json", side_effect=fake_json):
            W.fetch(34.34, 108.94, "Asia/Shanghai")
            self.assertIn(f"forecast_days={W.FORECAST_DAYS}", seen["url"])
            self.assertNotIn("start_date", seen["url"])
            W.fetch(34.34, 108.94, "Asia/Shanghai",
                    start_date=date(2026, 10, 3), end_date=date(2026, 10, 5))
            self.assertIn("start_date=2026-10-03", seen["url"])
            self.assertIn("end_date=2026-10-05", seen["url"])
            self.assertNotIn("forecast_days", seen["url"])


if __name__ == "__main__":
    unittest.main()
