#!/usr/bin/env python3
"""不开窗口，直接把某一刻的天空渲染成 PNG——用来检查画面。

用法:
    python3 tools/snapshot.py 输出.png 18:40 34.34 108.94 [天气码] [云量] [风速] [风向]
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cairo  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from chuang.render import SkyPainter  # noqa: E402
from chuang.scene import SkyEngine  # noqa: E402
from chuang.weather import HourPoint, Weather  # noqa: E402


def main():
    out = sys.argv[1]
    clock = sys.argv[2]
    lat = float(sys.argv[3])
    lon = float(sys.argv[4])
    code = int(sys.argv[5]) if len(sys.argv) > 5 else None
    cloud = float(sys.argv[6]) if len(sys.argv) > 6 else 0.0
    wind = float(sys.argv[7]) if len(sys.argv) > 7 else 0.0
    wdir = float(sys.argv[8]) if len(sys.argv) > 8 else 0.0
    w, h = (int(sys.argv[9]), int(sys.argv[10])) if len(sys.argv) > 10 else (1200, 760)

    tz = ZoneInfo("Asia/Shanghai")
    when = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    if "T" in clock:
        when = datetime.fromisoformat(clock).replace(tzinfo=tz)
    else:
        hh, mm = (int(x) for x in clock.split(":")[:2])
        when = when.replace(hour=hh, minute=mm)

    engine = SkyEngine()
    engine.set_location(lat, lon, "Asia/Shanghai")

    weather = None
    if code is not None:
        weather = Weather(ok=True, fetched_at=0.0, code=code, cloud=cloud,
                          wind_speed=wind, wind_dir=wdir, temp=19.0, apparent=19.0,
                          humidity=70.0, precip=1.5 if code in (63, 65, 95) else 0.0,
                          visibility=9000.0)
        base = when.replace(hour=0, minute=0, tzinfo=None)
        for i in range(48):
            weather.hourly.append(HourPoint(base + timedelta(hours=i), cloud, code,
                                            19.0, 50.0))

    scene = engine.build(when, weather, preview=False, location_label="西安 · 陕西省")
    painter = SkyPainter(seed=1234)
    painter.ui.ribbon = engine.ribbon(when.replace(hour=0, minute=0), weather)
    painter.ui.ribbon_key = "snapshot"
    for _ in range(60):                      # 让云与雨落到自然位置
        painter.fx.advance(0.06, scene, __import__("time").time(), w, h)

    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surf)
    painter.draw(cr, w, h, scene, 180.0)
    painter.draw_chip(cr, w, h)
    surf.write_to_png(out)
    print(f"写出 {out}  {when:%Y-%m-%d %H:%M} sun_alt={scene.sun_alt:.1f} "
          f"moon_alt={scene.moon_alt:.1f} illum={scene.moon_illum:.2f}")


if __name__ == "__main__":
    main()
