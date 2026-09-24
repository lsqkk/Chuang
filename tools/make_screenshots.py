#!/usr/bin/env python3
"""重新生成 README 里那组截图（不需要开窗口，直接离屏渲染）。

用法：
    python3 tools/make_screenshots.py [输出目录]      # 默认 screenshots/

每一张都是「某地某刻 + 某种天气」的真实计算结果——用的是程序自己的渲染器，
所以截图永远和实际画面一致，改了画法重新跑一遍就行。
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cairo  # noqa: E402

from chuang.render import SkyPainter  # noqa: E402
from chuang.scene import SkyEngine  # noqa: E402
from chuang.weather import HourPoint, Weather  # noqa: E402

XIAN = (34.3416, 108.9398)          # 西安
TZ = ZoneInfo("Asia/Shanghai")

# (文件名, 时刻, 城市, 天气码, 云量, 风速, 风向, 宽, 高, 墙上的卡片与长卷)
SHOTS = [
    ("01-dawn.png", "06:40", XIAN, 1, 20, 3, 90, 1400, 860, True, True),
    ("02-morning.png", "09:20", XIAN, 1, 10, 4, 90, 1400, 860, True, True),
    ("03-daylight.png", "12:40", XIAN, 2, 45, 8, 240, 1400, 860, True, True),
    ("04-golden-hour.png", "18:30", XIAN, 1, 20, 3, 100, 1400, 860, True, True),
    ("05-dusk-rain.png", "19:10", XIAN, 63, 90, 18, 200, 1400, 860, True, True),
    ("06-night-rain.png", "21:40", XIAN, 63, 85, 14, 210, 1400, 860, True, True),
    ("07-overcast.png", "15:10", XIAN, 3, 100, 6, 90, 1400, 860, True, True),
    ("08-snow.png", "08:10", XIAN, 73, 90, 6, 300, 1400, 860, True, True),
    ("09-fog.png", "05:50", XIAN, 45, 95, 2, 90, 1400, 860, True, True),
    ("10-panorama.png", "17:20", XIAN, 1, 25, 5, 120, 1800, 720, False, False),
    ("11-wallpaper.png", "19:30", XIAN, 2, 35, 6, 200, 1920, 1080, True, True),
]


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else \
        Path(__file__).resolve().parent.parent / "screenshots"
    out.mkdir(parents=True, exist_ok=True)
    day = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    engine = SkyEngine()
    for name, clock, (lat, lon), code, cloud, wind, wdir, w, h, info, ribbon in SHOTS:
        hh, mm = (int(x) for x in clock.split(":"))
        when = day.replace(hour=hh, minute=mm)
        engine.set_location(lat, lon, "Asia/Shanghai")
        # fetched_at 给个"几分钟前"的值：卡片底下那行会写"天气更新于 19:05"
        # （设成 0 的话这一行会退成"天气 · Open-Meteo"，看不出新功能）
        weather = Weather(ok=True, fetched_at=when.timestamp() - 300.0,
                          code=code, cloud=cloud,
                          wind_speed=wind, wind_dir=wdir, temp=19.0, apparent=19.0,
                          humidity=70.0,
                          precip=2.0 if code in (63, 65, 95) else 0.0,
                          visibility=9000.0 if code in (45, 48) else 24000.0)
        base = when.replace(tzinfo=None, hour=0, minute=0)
        for i in range(48):
            weather.hourly.append(HourPoint(base + timedelta(hours=i), cloud, code,
                                            19.0, 50.0))
        scene = engine.build(when, weather, location_label="西安 · 陕西省")
        painter = SkyPainter(seed=20260923)
        painter.ui.show_info = info
        painter.ui.show_ribbon = ribbon
        # 最后一张是"桌面壁纸"那一路：桌面上没有鼠标，卡片上那些能点的东西
        # （收起箭头 / 每行的小箭头 / 刷新）一概不画——与 wallpaper.render 一致
        # （见 AGENTS.md §3.5），不然截图里会比真实的壁纸多出一排按钮。
        painter.ui.info_buttons = name != "11-wallpaper.png"
        if ribbon:
            painter.ui.ribbon = engine.ribbon(day, weather)
            painter.ui.ribbon_surface = None
        for _ in range(40):        # 让云和雨走到自然的位置
            painter.fx.advance(0.05, scene, 0.0, w, h)
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(surf)
        painter.draw(cr, w, h, scene, 180.0)
        path = out / name
        surf.write_to_png(str(path))
        print(f"  {name}  {clock}  {w}×{h}  {path.stat().st_size // 1024} KB")
    print(f"共 {len(SHOTS)} 张，已写入 {out}")


if __name__ == "__main__":
    main()
