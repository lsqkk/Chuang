
"""天气这一摊的常数：接口地址、缓存路径，以及"多久问一次"那几个数。

单独立一个文件，是为了让 `net.py`（只管发请求）与 `service.py`（只管什么时候发）
都能读同一份，而不必互相 import——两边互相 import 会立刻变成循环。
"""

from __future__ import annotations

from pathlib import Path

from .. import __version__


API = "https://api.open-meteo.com/v1/forecast"
GEOCODE_API = "https://geocoding-api.open-meteo.com/v1/search"
CACHE = Path.home() / ".cache" / "chuang" / "weather.json"
REFRESH_SECONDS = 600
UA = f"Chuang/{__version__} (desktop window; +local)"

# 一次要几天的逐小时数据。
#
# 这里以前是 2 天，于是"把长卷拖到后天／跳到某一天去看看"时，画面拿的是窗口
# 里**最近的那一格**（也就是明天深夜）顶上去的——看着像预报，其实是另一天。
# 现在默认一次要 7 天：用户能预览到的"附近几天"都是真的。更远的（最多 16 天）
# 由 WeatherService.ensure_day() 按那一天单独补问一次，并进同一张表。
FORECAST_DAYS = 7
# Open-Meteo 的硬上限：预报最远 16 天，往过去最多 92 天。
MAX_FORECAST_DAYS = 16
PAST_LIMIT_DAYS = 7
# 同一天两次补问之间至少隔这么久（长卷一路拖过去时不至于把接口刷爆）
DAY_QUERY_COOLDOWN = 20.0
# 补问失败（多半是那天超出了预报范围）之后，隔这么久再试
DAY_QUERY_BACKOFF = 600.0
# 经纬度差在这以内算"同一座城市"：缓存里的天气只认自己那座城
SAME_PLACE_DEG = 0.05
