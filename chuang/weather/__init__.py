"""真实天气：Open-Meteo 免费接口（无需 API Key），带本地缓存与离线降级。

只上传经纬度，不含任何身份信息。断网时退回"纯天文模式"——
天空依旧正确，只是不知道有没有云。

1.2.0 拆成了包（原来 `weather.py` 有 894 行，把"天气码表""那份数据""网络"
"后台服务"四件事挤在一起）：

| 模块 | 管什么 |
|---|---|
| `codes.py` | WMO 天气码 → 人话、雨量 → 强度（毛毛雨和大雨的差别就在这里） |
| `model.py` | `Weather` / `HourPoint`：一份预报、按时刻插值、按天合并、读写缓存 |
| `net.py` | 只碰网络：抓取、地理编码、查时区（都带超时与失败兜底） |
| `service.py` | `WeatherService`：后台线程、限流与退避、"问一次"的回声 |
| `const.py` | 接口地址与那几个"多久问一次"的常数 |

对外的名字照旧（`from chuang.weather import Weather, fetch, ...`），
`app.py` / `scene.py` / `infocard.py` / `render/card.py` 都不必改。
"""

from __future__ import annotations

from .const import API
from .const import CACHE
from .const import REFRESH_SECONDS
from .const import FORECAST_DAYS
from .const import MAX_FORECAST_DAYS
from .const import PAST_LIMIT_DAYS
from .codes import code_text
from .codes import precip_intensity
from .codes import precip_label
from .codes import precip_kind
from .codes import uv_text
from .codes import precip_strength
from .codes import is_thunder
from .codes import is_fog
from .model import HourPoint
from .model import today_in
from .model import Weather
from .model import merge
from .model import load_cache
from .model import save_cache
from .net import fetch
from .net import geocode
from .net import timezone_for
from .net import fallback_timezone
from .service import WeatherService
