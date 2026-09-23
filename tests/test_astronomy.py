"""天文计算的回归测试。

期望值取自美国海军天文台（USNO）的官方接口
`aa.usno.navy.mil/api/rstt/oneday`——它是独立实现，不是我们自己算的，
所以能同时钉住"算法对不对"和"判据口径对不对"（折射有没有算两遍、
月亮有没有做地平视差、中天是不是真的中天）。
"""

import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from chuang import astronomy as A


def _minutes(dt: datetime) -> float:
    return dt.hour * 60 + dt.minute + dt.second / 60.0


# (地名, 纬度, 经度, 时区, 日期, 时差小时, USNO 给的本地时刻)
USNO = [
    ("西安", 34.34, 108.94, 8, "Asia/Shanghai", "2026-09-23",
     {"sunrise": "06:33", "sunset": "18:40", "noon": "12:37",
      "moonrise": "16:51", "moonset": "02:48"}),
    ("西安", 34.34, 108.94, 8, "Asia/Shanghai", "2026-06-21",
     {"sunrise": "05:32", "sunset": "20:00", "noon": "12:46",
      "moonrise": "12:06", "moonset": "00:03"}),
    ("西安", 34.34, 108.94, 8, "Asia/Shanghai", "2026-12-21",
     {"sunrise": "07:46", "sunset": "17:38", "noon": "12:42",
      "moonrise": "14:45", "moonset": "04:26"}),
    ("柏林", 52.52, 13.40, 2, "Europe/Berlin", "2026-06-21",
     {"sunrise": "04:43", "sunset": "21:33", "noon": "13:08",
      "moonrise": "12:43", "moonset": "00:41"}),
    ("纽约", 40.71, -74.01, -5, "America/New_York", "2026-12-21",
     {"sunrise": "07:17", "sunset": "16:32", "noon": "11:54",
      "moonrise": "14:00", "moonset": "04:35"}),
    ("悉尼", -33.87, 151.21, 11, "Australia/Sydney", "2026-03-21",
     {"sunrise": "06:59", "sunset": "19:06", "noon": "13:02",
      "moonrise": "09:05", "moonset": "20:06"}),
]


class TestAgainstUSNO(unittest.TestCase):
    """升落与中天：和官方数据差不超过 1.5 分钟。"""

    def test_events(self):
        for name, lat, lon, _off, zone, date, want in USNO:
            zi = ZoneInfo(zone)
            day = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=zi)
            ev = A.sun_events(day, lat, lon)
            for field in ("sunrise", "sunset", "noon", "moonrise", "moonset"):
                with self.subTest(city=name, date=date, event=field):
                    got = ev[field]
                    self.assertIsNotNone(got, f"{name} {date} 的 {field} 没算出来")
                    h, m = (int(x) for x in want[field].split(":"))
                    delta = _minutes(got) - (h * 60 + m)
                    self.assertLess(abs(delta), 1.5,
                                    f"{name} {date} {field}: 本代码 {got:%H:%M:%S}，"
                                    f"USNO {want[field]}，差 {delta:+.1f} 分钟")


class TestMoonTable(unittest.TestCase):
    """月亮那两张表（Meeus 47.A / 47.B）的回归。

    1.1.7 及以前这两张表的**角度系数是错的**：第一项 6288774·sin(M′) 的 M′
    被写成了 0，于是六度多的主项直接消失，月亮的黄经整体偏 5°、距离偏 3 万公里。
    所以这里不只比结果，也把表的形状钉死。
    """

    def test_lr_table_shape(self):
        self.assertEqual(len(A._MOON_LR), 60)
        # 第一项：6288774·sin(M′)，M′ 必须是 1
        self.assertEqual(tuple(A._MOON_LR[0]), (6288774, -20905355, 1, 0, 0, 0))
        # 第二项：1274027·sin(2D − M′)
        self.assertEqual(tuple(A._MOON_LR[1]), (1274027, -3699111, -1, 0, 0, 2))
        # 任何一项都不该是"全零角度"——那会让系数变成常数项
        for row in A._MOON_LR:
            self.assertNotEqual(tuple(row[2:]), (0, 0, 0, 0))

    def test_b_table_shape(self):
        self.assertEqual(len(A._MOON_B), 60)
        # 第一项：5128122·sin(F)
        self.assertEqual(tuple(A._MOON_B[0]), (5128122, 0, 0, 1, 0))

    def test_moon_longitude_against_ephemeris(self):
        """2026-09-23 01:01:48 UTC 的地心黄经（参考值取自 JPL 历表口径）。"""
        jd = A.julian_day(datetime(2026, 9, 23, 1, 1, 48, tzinfo=A.timezone.utc))
        eq = A.moon_equatorial(jd)
        self.assertAlmostEqual(eq.app_longitude, 316.209, delta=0.03)
        self.assertAlmostEqual(eq.distance, 395312.0, delta=200.0)


class TestMoonParallax(unittest.TestCase):
    """月亮离得近，画的位置必须是"地面看出去"的位置。"""

    def test_altitude_is_reduced_by_parallax(self):
        """月亮中天（高度 60° 左右，折射可以忽略）时比"不修视差"低多少。"""
        import math
        when = datetime(2026, 9, 23, 22, 17, tzinfo=ZoneInfo("Asia/Shanghai"))
        lat, lon = 34.34, 108.94
        geo = A.moon_geocentric_altaz(when, lat, lon)[0]
        ours, _, eq = A.moon_altaz(when, lat, lon)
        jd = A.julian_day(A.to_utc(when))
        naive = A._altaz_from_radec(eq.ra, eq.dec, lat, lon, jd)[0]   # 老实现
        par = A.moon_parallax(eq.distance)
        self.assertGreater(par, 0.85)               # 38 万公里外，视差接近 1°
        self.assertLess(par, 1.05)
        self.assertGreater(geo, 40.0)               # 确实是高高度那一刻
        self.assertAlmostEqual(naive - ours, par * math.cos(math.radians(geo)),
                               delta=0.02)
        self.assertGreater(naive - ours, 0.2)       # 修正没有被写丢

    def test_rise_uses_geocentric_criterion(self):
        """月出那一刻，地面视高度应该正好"上边缘贴地平"。"""
        zi = ZoneInfo("Asia/Shanghai")
        day = datetime(2026, 9, 23, tzinfo=zi)
        rise = A.sun_events(day, 34.34, 108.94)["moonrise"]
        apparent, _, eq = A.moon_altaz(rise, 34.34, 108.94)
        semidiameter = 0.2725 * A.moon_parallax(eq.distance)
        self.assertAlmostEqual(apparent + semidiameter, 0.0, delta=0.06)


class TestSolarNoon(unittest.TestCase):
    """中天必须是"当天太阳最高"的那一刻。"""

    def test_noon_is_the_maximum(self):
        for name, lat, lon, _off, zone, date, _want in USNO[:3]:
            zi = ZoneInfo(zone)
            day = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=zi)
            noon = A.solar_noon(day, lat, lon)
            here = A.sun_geometric_altaz(noon, lat, lon)[0]
            for delta in (-30, -10, 10, 30):
                alt = A.sun_geometric_altaz(noon + timedelta(minutes=delta), lat, lon)[0]
                with self.subTest(city=name, date=date, delta=delta):
                    self.assertGreaterEqual(here + 1e-9, alt)

    def test_hour_angle_is_zero(self):
        day = datetime(2026, 6, 21, tzinfo=ZoneInfo("Asia/Shanghai"))
        noon = A.solar_noon(day, 34.34, 108.94)
        self.assertAlmostEqual(A.sun_hour_angle(noon, 108.94), 0.0, delta=0.01)

    def test_high_sun_does_not_diverge(self):
        """1.1.7 的老实现（把方位角迭代到 180°）在夏季会跑飞 2 小时 46 分。"""
        day = datetime(2026, 6, 21, tzinfo=ZoneInfo("Asia/Shanghai"))
        noon = A.solar_noon(day, 34.34, 108.94)
        self.assertLess(abs(_minutes(noon) - (12 * 60 + 46)), 2.0)


class TestBasics(unittest.TestCase):

    def test_star_positions_above_horizon(self):
        """星表里不存在"地平线下还算进来"的点。"""
        from chuang.scene import SkyEngine
        eng = SkyEngine()
        eng.set_location(34.34, 108.94, "Asia/Shanghai")
        field = eng.starfield(datetime(2026, 9, 23, 22, 0, tzinfo=A.timezone.utc))
        self.assertGreater(len(field.points), 100)
        for az, alt, _mag, _rgb, _tw in field.points:
            self.assertGreaterEqual(alt, 0.0)
            self.assertLessEqual(az, 360.0)

    def test_refraction_shrinks_with_altitude(self):
        self.assertGreater(A.refraction(0.0), A.refraction(10.0))
        self.assertEqual(A.refraction(-5.0), 0.0)

    def test_phase_and_bright_limb(self):
        when = datetime(2026, 9, 27, 16, 0, tzinfo=A.timezone.utc)   # 满月附近
        phase, illum, elong = A.moon_phase(when)
        self.assertGreater(illum, 0.95)
        self.assertLessEqual(elong, 180.0)
        v = A.bright_limb_vector(when, 34.34, 108.94)
        self.assertAlmostEqual(sum(c * c for c in v) ** 0.5, 1.0, delta=1e-6)


if __name__ == "__main__":
    unittest.main()
