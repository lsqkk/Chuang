"""天气服务：用户开关、缓存降级、插值。"""

import json
import tempfile
import time
import unittest
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from unittest import mock

from chuang import weather as W
# 拆成包之后，各处要**打在名字真正住的地方**：`CACHE` 与那份数据的读写住
# 在 model.py，网络在 net.py，后台服务在 service.py。打在门面上是打不中的
# （`from .net import fetch` 是把函数对象绑到 service 里），于是假数据进不来、
# 真网络溜出去——1.2.0 拆包时踩过这一脚。
from chuang.weather import model as Wmodel
from chuang.weather import net as Wnet
from chuang.weather import service as Wsvc
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
    with mock.patch.object(Wmodel, "CACHE", tmp / "weather.json"):
        W.save_cache(W.Weather(ok=True, fetched_at=1e9, cloud=95.0, code=63,
                               temp=18.0, wind_speed=9.0, humidity=80.0))
        with mock.patch.object(Wmodel, "CACHE", tmp / "weather.json"):
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
    with mock.patch.object(Wmodel, "CACHE", p):
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
            with mock.patch.object(Wsvc, "fetch") as fetch:
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
        back = Wmodel._deserialize(Wmodel._serialize(w))
        self.assertEqual(back.code, 95)
        self.assertEqual(back.kind, "rain")
        self.assertEqual(len(back.hourly), 24)
        self.assertAlmostEqual(back.cloud_at(datetime(2026, 9, 23, 5, 0)), 50.0)

    def test_broken_cache_does_not_raise(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "weather.json"
            p.write_text("{ 这不是 JSON", encoding="utf-8")
            with mock.patch.object(Wmodel, "CACHE", p):
                self.assertIsNone(W.load_cache())
            p.write_text(json.dumps({"code": "??"}), encoding="utf-8")
            with mock.patch.object(Wmodel, "CACHE", p):
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
            with mock.patch.object(Wsvc, "fetch", side_effect=fake_fetch):
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

    def test_a_day_query_never_stomps_on_another_citys_table(self):
        """刚换完城市、新一轮还没回来时，**不按天补**。

        手里那份是上一座城的表，按天补那一枪只带回薄薄一窗口（那天的前后两三天），
        而 `merge()` 遇到"换了城市"是"旧的一律不算数"——整张表会被这一窗口顶掉，
        连"此刻"都没了：卡片上写着"这天还没有预报"，要等下一个十分钟周期才恢复。
        （2026-09-24 的 CI 就是在这条路上翻的车。）换城市本来就会强制整表刷一次，
        等它回来再按天补就行。
        """
        with tempfile.TemporaryDirectory() as d:
            svc = _service_with_days(Path(d), 2, self.TODAY)   # 表是西安的
            svc.lat, svc.lon = 52.52, 13.40                     # 窗已经挪到柏林
            asked = []
            with mock.patch.object(Wsvc, "fetch",
                                   side_effect=lambda *a, **k: asked.append(a)):
                self.assertFalse(svc.ensure_day(self.TODAY + timedelta(days=5)))
            self.assertEqual(asked, [], "手里还是别的城的表，却按天补了一枪")
            # 等整表刷新回来（数据地点也对上了），按天补就该照常工作
            svc.weather = _weather_days(self.TODAY, 2, lat=52.52, lon=13.40)
            with mock.patch.object(Wsvc, "fetch", side_effect=lambda *a, **k: asked.append(a)):
                self.assertTrue(svc.ensure_day(self.TODAY + timedelta(days=5)))
            self.assertEqual(len(asked), 1)

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

            with mock.patch.object(Wsvc, "fetch", side_effect=fake_fetch), \
                    mock.patch.object(Wmodel, "save_cache"):
                self.assertTrue(svc.refresh(force=True, done=got.append))
                self.assertTrue(_pump_glib(lambda: bool(got)), "done 没有被调到")
            self.assertTrue(got[-1].ok)
            self.assertEqual(got[-1].cloud, 55.0)

    @unittest.skipUnless(HAS_GLIB, "没有 PyGObject，跳过需要主循环的那几条")
    def test_a_forced_refresh_during_a_fetch_is_not_dropped(self):
        """抓取还在飞的时候按 R / 换城市：那一枪不能被吞掉。

        `refresh()` 撞上 `_busy` 就直接返回 False、什么也不记——于是"刚换完城市"
        要等下一次十分钟周期才有天气（画面上写着"未联网"，可网络明明好好的）；
        按 R 也只是得到一句"稍等一下"，然后就真的没有下文了。
        现在记一笔，等手上这枪回来立刻补上，连那句回声一起。
        """
        with tempfile.TemporaryDirectory() as d:
            svc = _service_with_days(Path(d), 1, self.TODAY)
            asked, got = [], []

            def fake_fetch(lat, lon, tz="auto", start_date=None, end_date=None,
                           forecast_days=7):
                asked.append(forecast_days)
                return _weather_days(self.TODAY, 2, cloud=61.0)

            svc._busy = True                     # 假装上一枪还在飞
            with mock.patch.object(Wsvc, "fetch", side_effect=fake_fetch), \
                    mock.patch.object(Wmodel, "save_cache"):
                self.assertFalse(svc.refresh(force=True, done=got.append))
                self.assertTrue(svc._wanted, "这一枪被吞了，没记下来")
                svc._busy = False
                svc._deliver(_weather_days(self.TODAY, 1), None)   # 上一枪回来了
                self.assertTrue(_pump_glib(lambda: bool(asked)), "没有自动补一枪")
                self.assertTrue(_pump_glib(lambda: bool(got)), "补的那一枪没有回声")
            self.assertTrue(got[-1].ok)
            self.assertEqual(got[-1].cloud, 61.0)


class TestFetchRequest(unittest.TestCase):
    """请求参数：默认要 7 天，给窗口就只要那几天。"""

    def test_asks_for_a_week_by_default(self):
        seen = {}

        def fake_json(url, timeout=9.0):
            seen["url"] = url
            return {"current": {}, "hourly": {"time": []}}

        with mock.patch.object(Wnet, "_fetch_json", side_effect=fake_json):
            W.fetch(34.34, 108.94, "Asia/Shanghai")
            self.assertIn(f"forecast_days={W.FORECAST_DAYS}", seen["url"])
            self.assertNotIn("start_date", seen["url"])
            W.fetch(34.34, 108.94, "Asia/Shanghai",
                    start_date=date(2026, 10, 3), end_date=date(2026, 10, 5))
            self.assertIn("start_date=2026-10-03", seen["url"])
            self.assertIn("end_date=2026-10-05", seen["url"])
            self.assertNotIn("forecast_days", seen["url"])


class TestExtraReadings(unittest.TestCase):
    """接口里给了、我们以前没用的那几样：露点、气压、紫外线、云的层次、日高低温。

    1.1.13 起它们会出现在信息卡上（小字条）与主角那一行里，所以：
    请求里要带上、解析时不能丢、缓存来回一趟还在、老缓存没有它们也不能报错。
    """

    RAW = {
        "current": {
            "temperature_2m": 21.5, "relative_humidity_2m": 46,
            "apparent_temperature": 22.0, "weather_code": 1, "cloud_cover": 30,
            "wind_speed_10m": 7.2, "wind_direction_10m": 210,
            "wind_gusts_10m": 14.0, "precipitation": 0.0,
            "dew_point_2m": 8.4, "pressure_msl": 1012.3,
            "cloud_cover_low": 10, "cloud_cover_mid": 20, "cloud_cover_high": 60,
        },
        "hourly": {
            "time": ["2026-09-24T11:00", "2026-09-24T12:00"],
            "temperature_2m": [21.0, 22.0],
            "weather_code": [1, 2],
            "cloud_cover": [25, 35],
            "precipitation_probability": [0, 5],
            "precipitation": [0.0, 0.0],
            "visibility": [24000.0, 22000.0],
            "dew_point_2m": [8.0, 8.6],
            "pressure_msl": [1012.0, 1012.6],
            "uv_index": [5.4, 6.1],
            "cloud_cover_low": [8, 12],
            "cloud_cover_mid": [18, 22],
            "cloud_cover_high": [55, 65],
        },
        "daily": {"time": ["2026-09-24"],
                  "temperature_2m_max": [24.0], "temperature_2m_min": [12.0]},
    }

    def _fetch(self):
        with mock.patch.object(Wnet, "_fetch_json", return_value=self.RAW):
            return W.fetch(34.34, 108.94, "Asia/Shanghai")

    def test_the_request_asks_for_them(self):
        seen = {}

        def fake(url, timeout=9.0):
            seen["url"] = url
            return self.RAW

        with mock.patch.object(Wnet, "_fetch_json", side_effect=fake):
            W.fetch(34.34, 108.94, "Asia/Shanghai")
        for field in ("dew_point_2m", "pressure_msl", "uv_index",
                      "cloud_cover_low", "cloud_cover_mid", "cloud_cover_high"):
            self.assertIn(field, seen["url"], f"请求里没要 {field}")
        self.assertIn("temperature_2m_max", seen["url"])
        # 日出日落**不要**：那是本地天文算的，不能拿接口的顶掉（见 DESIGN）
        self.assertNotIn("sunrise", seen["url"])

    def test_parsed_into_the_object(self):
        w = self._fetch()
        self.assertAlmostEqual(w.dew, 8.4)
        self.assertAlmostEqual(w.pressure, 1012.3)
        self.assertAlmostEqual(w.cloud_high, 60.0)
        self.assertEqual(w.day_extremes(date(2026, 9, 24)), (24.0, 12.0))
        when = datetime(2026, 9, 24, 11, 0)
        self.assertAlmostEqual(w.hourly_at(when, "uv"), 5.4)
        self.assertAlmostEqual(w.hourly_at(when, "cloud_low"), 8.0)

    def test_missing_columns_come_back_as_none(self):
        """老缓存 / 别的城市那份数据里没有这些列：要 None，别编一个 0 出来。"""
        w = self._fetch()
        self.assertIsNone(w.hourly_at(datetime(2026, 9, 24, 11, 0), "不存在的列"))
        self.assertIsNone(W.Weather(ok=True).hourly_at(datetime(2026, 9, 24), "uv"))
        self.assertIsNone(W.Weather(ok=True).day_extremes(date(2026, 9, 24)))

    def test_extremes_fall_back_to_the_hourly_table(self):
        """接口没给日预报（老缓存）时，按逐小时那张表算当天的最高最低。"""
        w = W.Weather(ok=True, fetched_at=1e9)
        base = datetime(2026, 9, 24, 0, 0)
        for h in range(24):
            w.hourly.append(W.HourPoint(base + timedelta(hours=h), 40.0, 1,
                                        10.0 + h))
        w.invalidate()
        self.assertEqual(w.day_extremes(date(2026, 9, 24)), (33.0, 10.0))
        # 有日预报时以接口的为准（逐小时是逐点采样，算出来的极值会差一点）
        w.daily["2026-09-24"] = (35.0, 9.0)
        self.assertEqual(w.day_extremes(date(2026, 9, 24)), (35.0, 9.0))

    def test_cache_roundtrip_keeps_them(self):
        w = self._fetch()
        back = Wmodel._deserialize(Wmodel._serialize(w))
        self.assertAlmostEqual(back.dew, 8.4)
        self.assertAlmostEqual(back.pressure, 1012.3)
        self.assertAlmostEqual(back.cloud_mid, 20.0)
        self.assertEqual(back.day_extremes(date(2026, 9, 24)), (24.0, 12.0))
        self.assertAlmostEqual(back.hourly_at(datetime(2026, 9, 24, 12, 0), "uv"),
                               6.1)

    def test_an_old_cache_without_them_still_loads(self):
        """老缓存的行只有 5-7 列：读得进来，缺的那几样就是默认值。"""
        old = {"fetched_at": 1e9, "code": 63, "cloud": 95.0, "temp": 18.0,
               "hourly": [["2026-09-24T00:00:00", 95.0, 63, 18.0, 60.0]]}
        w = Wmodel._deserialize(old)
        self.assertEqual(len(w.hourly), 1)
        self.assertIsNone(w.hourly[0].uv)
        self.assertIsNone(w.dew)
        self.assertEqual(w.daily, {})

    def test_uv_text_says_a_level(self):
        self.assertIn("弱", W.uv_text(1))
        self.assertIn("中等", W.uv_text(4))
        self.assertIn("很强", W.uv_text(9))
        self.assertIn("极强", W.uv_text(12))
        self.assertEqual(W.uv_text(None), "—")


if __name__ == "__main__":
    unittest.main()
