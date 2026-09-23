"""配置与首次运行的城市推测。"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "chuang"
CONFIG_FILE = CONFIG_DIR / "config.json"
AUTOSTART_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart"
AUTOSTART_FILE = AUTOSTART_DIR / "chuang.desktop"

# 壁纸跟随此刻的可选间隔（秒）
WALLPAPER_INTERVALS = (10, 30, 60)
CLOSE_BEHAVIORS = ("ask", "tray", "quit")


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
    autostart_hidden: bool = False       # 开机自启时不弹窗，直接进托盘
    close_behavior: str = "ask"          # ask / tray / quit
    update_check: bool = True            # 自动检查更新（一天一次）
    last_update_check: float = 0.0
    skipped_version: str = ""
    wallpaper_auto: bool = False
    wallpaper_interval: int = 10         # 壁纸跟随此刻的间隔（秒）
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
        if not isinstance(raw, dict):
            return cfg
        loc = raw.pop("location", None)
        if isinstance(loc, dict):
            known = {k: v for k, v in loc.items() if k in Location.__dataclass_fields__}
            try:
                cfg.location = Location(**known)
            except (TypeError, ValueError):
                pass
        for key, value in raw.items():
            if key in cls.__dataclass_fields__:
                setattr(cfg, key, value)
        cfg.sanitize()
        return cfg

    def sanitize(self) -> None:
        """把外部改坏或过期的字段纠回合法值（配置是纯文本，用户和旧版本都会写它）。"""
        try:
            self.wallpaper_interval = int(self.wallpaper_interval)
        except (TypeError, ValueError):
            self.wallpaper_interval = 10
        if self.wallpaper_interval not in WALLPAPER_INTERVALS:
            self.wallpaper_interval = 10
        if self.close_behavior not in CLOSE_BEHAVIORS:
            self.close_behavior = "ask"
        try:
            self.wallpaper_slot = 1 if int(self.wallpaper_slot or 0) % 2 else 0
        except (TypeError, ValueError):
            self.wallpaper_slot = 0
        for name in ("wallpaper_auto", "wallpaper_dynamic", "mirror_weather",
                     "autostart", "autostart_hidden", "update_check",
                     "always_on_top", "show_ribbon", "wallpaper_show_info",
                     "wallpaper_show_ribbon", "first_run_done"):
            setattr(self, name, bool(getattr(self, name, False)))
        try:
            self.fov = min(360.0, max(60.0, float(self.fov)))
        except (TypeError, ValueError):
            self.fov = 190.0
        for name, fallback in (("window_w", 960), ("window_h", 620)):
            try:
                setattr(self, name, max(320, int(getattr(self, name))))
            except (TypeError, ValueError):
                setattr(self, name, fallback)
        # 动态壁纸和"跟随此刻"是两条互斥的路，别让配置里同时为真
        if self.wallpaper_auto and self.wallpaper_dynamic:
            self.wallpaper_dynamic = False

    def save(self) -> None:
        """原子写入：先写同目录的临时文件再 replace。

        非原子写会在断电/被 kill 时留下半截 JSON，下次启动整份配置都会被重置。
        """
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            data = asdict(self)
            text = json.dumps(data, ensure_ascii=False, indent=2)
            tmp = CONFIG_FILE.with_name(CONFIG_FILE.name + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, CONFIG_FILE)
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


def exec_quote(path: str) -> str:
    """按 Desktop Entry 规范给 Exec 参数加引号（路径里有空格/中文时才需要）。"""
    if path and all(c not in path for c in ' \t\n"\'\\><~|&;$*?#()`'):
        return path
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


def autostart_exec() -> str:
    """读回自启文件里的 Exec= 行（用于判断它是不是已经过期）。"""
    try:
        for line in AUTOSTART_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("Exec="):
                return line[5:].strip()
    except OSError:
        pass
    return ""


def _first_token(exec_line: str) -> str:
    """取出 Exec= 里的第一个参数（可执行文件本身），认双引号包裹。"""
    text = exec_line.lstrip()
    if not text:
        return ""
    if text[0] == '"':
        out = []
        i = 1
        while i < len(text):
            ch = text[i]
            if ch == "\\" and i + 1 < len(text):
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                break
            out.append(ch)
            i += 1
        return "".join(out)
    return text.split(None, 1)[0]


def autostart_wanted_exec(argv, hidden: bool = False) -> str:
    """自启文件里 Exec= 这一行应有的内容（argv 是启动命令的参数列表）。"""
    if isinstance(argv, str):
        argv = [argv]
    return " ".join(exec_quote(part) for part in argv) + (" --hidden" if hidden else "")


def autostart_needs_repair(argv, hidden: bool = False) -> bool:
    """自启文件是否存在、且指向的正是我们想运行的那个程序。"""
    if not AUTOSTART_FILE.exists():
        return False
    current = autostart_exec()
    if current != autostart_wanted_exec(argv, hidden):
        return True
    # 命令看着对，但它指向的那个二进制可能已经被卸载/搬走：GNOME 会静默
    # 忽略这条自启项（只剩日志里一行 Exec binary ... does not exist）。
    first = _first_token(current)
    if not first:
        return True
    if Path(first).is_absolute():
        return not Path(first).exists()
    # 只是命令名（如 chuang）：交给 PATH 去找，找不到才需要重写
    return shutil.which(first) is None


def set_autostart(enabled: bool, argv, hidden: bool = False) -> None:
    """写入 / 删除 ~/.config/autostart/chuang.desktop。

    Exec 里放的必须是"这个程序此刻真正的命令行"，否则 GNOME 的
    systemd-xdg-autostart-generator 会因为找不到可执行文件而整条忽略它
    （日志里只会留一句 "Exec binary ... does not exist"，用户看不到任何提示）。
    """
    try:
        if enabled:
            AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
            AUTOSTART_FILE.write_text(
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Name=窗\n"
                "Name[en]=Chuang\n"
                "GenericName=实时天空之窗\n"
                "Comment=把你头顶此刻真实的天空搬到桌面\n"
                f"Exec={autostart_wanted_exec(argv, hidden)}\n"
                "Icon=chuang\n"
                "Terminal=false\n"
                "StartupNotify=false\n"
                "X-GNOME-Autostart-enabled=true\n"
                "Categories=Utility;\n",
                encoding="utf-8")
        elif AUTOSTART_FILE.exists():
            AUTOSTART_FILE.unlink()
    except OSError:
        pass
