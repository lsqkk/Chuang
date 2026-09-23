"""配置与首次运行的城市推测。"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "chuang"
CONFIG_FILE = CONFIG_DIR / "config.json"
AUTOSTART_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart"
AUTOSTART_FILE = AUTOSTART_DIR / "chuang.desktop"


@dataclass
class Location:
    name: str = ""
    admin: str = ""
    country: str = ""
    lat: float = 34.3416
    lon: float = 108.9398
    timezone: str = "Asia/Shanghai"
    guessed: bool = False

    @property
    def label(self) -> str:
        parts = [self.name]
        if self.admin and self.admin != self.name:
            parts.append(self.admin)
        return " · ".join(p for p in parts if p)

    @property
    def full_label(self) -> str:
        parts = [self.name, self.admin, self.country]
        return " · ".join(dict.fromkeys(p for p in parts if p))


@dataclass
class Config:
    location: Location = field(default_factory=Location)
    mirror_weather: bool = True
    always_on_top: bool = False
    autostart: bool = False
    close_behavior: str = "ask"          # ask / tray / quit
    wallpaper_auto: bool = False
    wallpaper_dynamic: bool = False
    wallpaper_show_info: bool = False
    wallpaper_show_ribbon: bool = False
    prev_wallpaper: str = ""
    prev_wallpaper_dark: str = ""
    wallpaper_slot: int = 0
    fov: float = 190.0
    window_w: int = 960
    window_h: int = 620
    show_ribbon: bool = True
    first_run_done: bool = False

    # ---- 读写 --------------------------------------------------------
    @classmethod
    def load(cls) -> "Config":
        cfg = cls()
        try:
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cfg
        loc = raw.pop("location", None)
        if isinstance(loc, dict):
            known = {k: v for k, v in loc.items() if k in Location.__dataclass_fields__}
            cfg.location = Location(**known)
        for key, value in raw.items():
            if key in cls.__dataclass_fields__:
                setattr(cfg, key, value)
        return cfg

    def save(self) -> None:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            data = asdict(self)
            CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        except OSError:
            pass


# --------------------------------------------------------------------------
# 首次运行：由系统时区推测一个城市（不出网，纯本地映射）
# --------------------------------------------------------------------------

_TZ_CITY = {
    "Asia/Shanghai": ("西安", "陕西省", "中国", 34.3416, 108.9398),
    "Asia/Chongqing": ("重庆", "重庆市", "中国", 29.5630, 106.5516),
    "Asia/Harbin": ("哈尔滨", "黑龙江省", "中国", 45.8038, 126.5350),
    "Asia/Urumqi": ("乌鲁木齐", "新疆", "中国", 43.8256, 87.6168),
    "Asia/Hong_Kong": ("香港", "", "中国", 22.3193, 114.1694),
    "Asia/Taipei": ("台北", "", "中国", 25.0330, 121.5654),
    "Asia/Tokyo": ("东京", "", "日本", 35.6762, 139.6503),
    "Asia/Seoul": ("首尔", "", "韩国", 37.5665, 126.9780),
    "Asia/Singapore": ("新加坡", "", "新加坡", 1.3521, 103.8198),
    "Asia/Kolkata": ("加尔各答", "", "印度", 22.5726, 88.3639),
    "Asia/Dubai": ("迪拜", "", "阿联酋", 25.2048, 55.2708),
    "Europe/London": ("伦敦", "", "英国", 51.5074, -0.1278),
    "Europe/Paris": ("巴黎", "", "法国", 48.8566, 2.3522),
    "Europe/Berlin": ("柏林", "", "德国", 52.5200, 13.4050),
    "Europe/Moscow": ("莫斯科", "", "俄罗斯", 55.7558, 37.6173),
    "America/New_York": ("纽约", "", "美国", 40.7128, -74.0060),
    "America/Chicago": ("芝加哥", "", "美国", 41.8781, -87.6298),
    "America/Denver": ("丹佛", "", "美国", 39.7392, -104.9903),
    "America/Los_Angeles": ("旧金山", "", "美国", 37.7749, -122.4194),
    "America/Sao_Paulo": ("圣保罗", "", "巴西", -23.5505, -46.6333),
    "Australia/Sydney": ("悉尼", "", "澳大利亚", -33.8688, 151.2093),
    "Africa/Cairo": ("开罗", "", "埃及", 30.0444, 31.2357),
    "UTC": ("格林尼治", "", "英国", 51.4779, 0.0015),
    "Etc/UTC": ("格林尼治", "", "英国", 51.4779, 0.0015),
}


def guess_location() -> Location:
    """给一个离线推测：系统时区 → 代表城市；时区是偏移量时按经度折算。"""
    tz = os.environ.get("TZ") or ""
    if not tz:
        try:
            tz = Path("/etc/timezone").read_text(encoding="utf-8").strip()
        except OSError:
            tz = ""

    def _zoneinfo_key():
        try:
            import zoneinfo
            return getattr(zoneinfo.ZoneInfo("localtime"), "key", "")
        except Exception:
            return ""

    key = tz or _zoneinfo_key()
    if key in _TZ_CITY:
        name, admin, country, lat, lon = _TZ_CITY[key]
        return Location(name, admin, country, lat, lon, key, guessed=True)

    # 兜底：用 UTC 偏移换算出经度，纬度取中纬
    from datetime import datetime
    from .astronomy import to_utc
    now = datetime.now()
    if now.tzinfo is None:
        now = now.astimezone()
    offset_h = now.utcoffset().total_seconds() / 3600.0 if now.utcoffset() else 0.0
    lon = max(-180.0, min(180.0, offset_h * 15.0))
    tz_name = now.tzname() or f"UTC{offset_h:+.0f}"
    return Location(f"{tz_name} 一带", "", "", 34.0, lon, key or "auto", guessed=True)


# --------------------------------------------------------------------------
# 开机自启
# --------------------------------------------------------------------------

def autostart_installed() -> bool:
    return AUTOSTART_FILE.exists()


def set_autostart(enabled: bool, exec_cmd: str) -> None:
    try:
        if enabled:
            AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
            AUTOSTART_FILE.write_text(
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Name=窗\n"
                "Name[en]=Chuang\n"
                "Comment=把你头顶此刻真实的天空搬到桌面\n"
                f"Exec={exec_cmd}\n"
                "Icon=chuang\n"
                "Terminal=false\n"
                "X-GNOME-Autostart-enabled=true\n"
                "Categories=Utility;\n",
                encoding="utf-8")
        elif AUTOSTART_FILE.exists():
            AUTOSTART_FILE.unlink()
    except OSError:
        pass
