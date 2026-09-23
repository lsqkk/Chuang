"""天气服务：用户开关、缓存降级、插值。"""

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from chuang import weather as W
from chuang.scene import SkyEngine


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
        self.assertEqual(w.cloud_at(datetime(2026, 9, 22, 0, 0)), 0.0)      # 之前
        self.assertEqual(w.cloud_at(datetime(2026, 9, 24, 0, 0)), 230.0)    # 之后
        self.assertAlmostEqual(w.cloud_at(datetime(2026, 9, 23, 1, 30)), 15.0)

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

    def test_fallback_timezone(self):
        self.assertEqual(W.fallback_timezone(108.94), "Etc/GMT-7")
        self.assertEqual(W.fallback_timezone(-74.01), "Etc/GMT+5")
        self.assertEqual(W.fallback_timezone(0.0), "Etc/GMT-0")


if __name__ == "__main__":
    unittest.main()
