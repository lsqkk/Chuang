"""场景组装：一帧里的数据是不是自洽。"""

import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from chuang.scene import SkyEngine, human_hint, phase_name_simple, skyline_seed
from chuang.weather import HourPoint, Weather


def _weather(cloud=20.0, code=1):
    base = datetime(2026, 9, 23, 0, 0)
    w = Weather(ok=True, code=code, cloud=cloud, temp=19.0, wind_speed=8.0,
                wind_dir=200.0, humidity=60.0)
    w.hourly = [HourPoint(base + timedelta(hours=i), cloud, code, 19.0, 0.0)
                for i in range(48)]
    return w


class TestEngine(unittest.TestCase):

    def setUp(self):
        self.eng = SkyEngine()
        self.eng.set_location(34.34, 108.94, "Asia/Shanghai")
        self.zi = ZoneInfo("Asia/Shanghai")

    def test_default_location_still_resolves_the_timezone(self):
        """引擎的出厂值恰好就是西安 + Asia/Shanghai。

        `set_location()` 在"参数与当前值完全相同"时会提前返回——1.1.9 的 CI 就是
        在这条路上翻的车：时区没被解析，`local_now()` 退回**系统时区**，于是
        日出日落落进了"错的那一天"（在这台 +08:00 的机器上看不出来，跑在 UTC 的
        机器上就原形毕露：日出 06:32 变成了前一天晚上的 22:32）。
        """
        eng = SkyEngine()                       # 什么都不改，就是默认值
        self.assertIsNotNone(eng._tzinfo, "默认时区没有被解析")
        eng.set_location(34.3416, 108.9398, "Asia/Shanghai")   # 与默认值一字不差
        self.assertIsNotNone(eng._tzinfo, "set_location 提前返回，把时区留成了 None")
        self.assertEqual(eng.local_now().utcoffset(),
                         timedelta(hours=8), "本地时刻没有按配置的时区算")
        events = eng.events(eng.local_now())
        self.assertTrue(events["sunrise"] < events["noon"] < events["sunset"])

    def test_build_with_weather(self):
        when = datetime(2026, 9, 23, 18, 30, tzinfo=self.zi)
        sc = self.eng.build(when, _weather(cloud=95.0, code=63), location_label="西安")
        self.assertTrue(sc.has_weather)
        self.assertFalse(sc.weather_disabled)
        self.assertEqual(sc.weather_text, "中雨")
        self.assertEqual(sc.precip_kind, "rain")
        self.assertGreater(sc.precip_strength, 0.3)
        self.assertEqual(sc.location_name, "西安")
        self.assertTrue(sc.events["sunrise"] < sc.events["noon"] < sc.events["sunset"])

    def test_build_without_weather(self):
        when = datetime(2026, 9, 23, 18, 30, tzinfo=self.zi)
        sc = self.eng.build(when, None)
        self.assertFalse(sc.has_weather)
        self.assertFalse(sc.weather_disabled)      # 断网 ≠ 用户关掉了
        self.assertEqual(sc.cloud, 0.0)

    def test_weather_off_is_marked(self):
        when = datetime(2026, 9, 23, 18, 30, tzinfo=self.zi)
        sc = self.eng.build(when, None, weather_off=True)
        self.assertTrue(sc.weather_disabled)
        self.assertFalse(sc.has_weather)
        # 关掉天气之后，那句人话只该讲天色，不能还在说"正在下雨"
        hint = human_hint(sc)
        self.assertNotIn("正在下", hint)
        self.assertNotIn("起雾", hint)

    def test_ribbon_spans_one_day(self):
        day = datetime(2026, 9, 23, tzinfo=self.zi)
        w = _weather(cloud=95.0, code=63)
        ribbon = self.eng.ribbon(day, w)
        self.assertEqual(len(ribbon), 288)                    # 每 5 分钟一格
        self.assertEqual(ribbon[0][0].strftime("%H:%M"), "00:00")
        self.assertEqual(ribbon[-1][0].strftime("%H:%M"), "23:55")
        # 下着中雨的那一格必须比正午晴天暗（云量被吃进去了）
        self.assertNotEqual(ribbon[0][1], ribbon[144][1])

    def test_ribbon_without_weather(self):
        day = datetime(2026, 9, 23, tzinfo=self.zi)
        ribbon = self.eng.ribbon(day, None)
        self.assertEqual(len(ribbon), 288)
        for _, color in ribbon:
            self.assertEqual(len(color), 3)

    def test_events_are_cached_per_day(self):
        day = datetime(2026, 9, 23, tzinfo=self.zi)
        again = self.eng.events(day)
        self.assertIs(again, self.eng.events(day))

    def test_set_location_resets_caches(self):
        day = datetime(2026, 9, 23, tzinfo=self.zi)
        self.eng.events(day)
        self.eng.set_location(-33.87, 151.21, "Australia/Sydney")
        self.assertEqual(self.eng._events, {})
        self.assertEqual(self.eng.lat, -33.87)


class TestHelpers(unittest.TestCase):

    def test_skyline_seed_is_stable_and_location_specific(self):
        a = skyline_seed("西安", 34.34, 108.94)
        self.assertEqual(a, skyline_seed("西安", 34.34, 108.94))
        self.assertNotEqual(a, skyline_seed("咸阳", 34.34, 108.94))
        self.assertNotEqual(a, skyline_seed("西安", 34.35, 108.94))

    def test_phase_names(self):
        self.assertEqual(phase_name_simple(0.0), "新月")
        self.assertEqual(phase_name_simple(0.5), "满")
        self.assertIn(phase_name_simple(0.25), ("半月", "细细的"))


if __name__ == "__main__":
    unittest.main()
