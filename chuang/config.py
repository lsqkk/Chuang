"""配置与首次运行的城市推测。"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .i18n import N_, available_languages, translate_in, tr as _


def _xdg_dir(name: str, fallback: Path) -> Path:
    """XDG 目录：变量没设、或者被设成了空串，都退回默认值。

    空串是个真会咬人的坑：`os.environ.get("XDG_CONFIG_HOME", 默认)` 遇到
    `XDG_CONFIG_HOME=` 时拿到的是 ""，`Path("")` 就是当前目录——于是配置会被
    悄悄写进"运行时所在的那个目录"里（从别处启动一次就多出一份配置）。
    """
    value = os.environ.get(name)
    return Path(value) if value else fallback


_HOME = Path.home()
CONFIG_DIR = _xdg_dir("XDG_CONFIG_HOME", _HOME / ".config") / "chuang"
CONFIG_FILE = CONFIG_DIR / "config.json"
AUTOSTART_DIR = _xdg_dir("XDG_CONFIG_HOME", _HOME / ".config") / "autostart"
AUTOSTART_FILE = AUTOSTART_DIR / "chuang.desktop"

# 配置的"年代"。以后**删字段、改语义**时在这里加一档，并在 Config._migrate 里
# 写清怎么把上一版的数据搬过来——否则旧文件只会被 sanitize() 悄悄纠成默认值，
# 用户配好的东西看起来"自己变回去了"。
SCHEMA = 1

# 壁纸跟随此刻的可选间隔（秒）
WALLPAPER_INTERVALS = (10, 30, 60)
CLOSE_BEHAVIORS = ("ask", "tray", "quit")
# 单位与时间制：auto 时按语言 / 地区定，用户也可以钉死一种
UNIT_CHOICES = ("auto", "metric", "imperial")
TIME_FORMATS = ("auto", "h24", "h12")


def language_choices() -> tuple:
    """语言选择里能列出来的：auto + 真的带目录的那几门（见 chuang/i18n.py）。"""
    return ("auto",) + tuple(available_languages())
# 画面刷新率（帧/秒）。默认 60，允许按终端性能往下调：
# 24 近似电影、30 省电、45 折中、60 顺滑、120 高刷屏（屏幕跟不上时会自动以屏幕为准）。
FRAME_RATES = (24, 30, 45, 60, 120)
DEFAULT_FRAME_RATE = 60
# 壁纸上那张「此刻的事实」用哪种版式：
#   follow = 跟窗口里的一样（窗口收成一条，壁纸也跟着收）；slim = 永远精简一条；
#   full = 永远完整版（壁纸大、站远看，很多人更喜欢完整的）。
INFO_MODES = ("follow", "slim", "full")

# 窗外"画什么"的开关（菜单 → 场景）。每一项都能单独关掉，也能一键回到全部。
# 名字和渲染里的字段一一对应（render/state.py 的 UIState、菜单里的动作名），
# 改名字要三处一起改（`tests/test_project.py` 盯着动作名那一条）。
SCENE_SWITCHES = (
    ("show_people", N_("行人")),
    ("show_traffic", N_("车辆")),
    ("show_trees", N_("行道树")),
    ("show_lamps", N_("路灯")),
    ("show_planes", N_("天上的飞机")),
    ("show_skyline", N_("城市天际线")),
    ("show_clouds", N_("云")),
    ("show_stars", N_("星空")),
    ("show_weatherfx", N_("雨雪与闪电")),
    ("show_plant", N_("窗台盆栽")),
    ("plant_sway", N_("盆栽随风摇曳")),
)
# 这几条是**给翻译抽取**用的标记：.desktop 与「关于」窗里的那几句在下面
# `set_autostart()` 里拼出来，标一下才不会漏进 .pot。
N_("实时天空之窗")
N_("把你头顶此刻真实的天空搬到桌面的一扇窗里")


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
    schema: int = SCHEMA           # 写回文件里，便于以后识别"这是哪一版写的"
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
    wallpaper_info_mode: str = "follow"   # follow / slim / full（见 INFO_MODES）
    prev_wallpaper: str = ""
    prev_wallpaper_dark: str = ""
    prev_wallpaper_options: str = ""     # 原来是 zoom / scaled / centered / wallpaper
    wallpaper_slot: int = 0
    fov: float = 190.0
    window_w: int = 960
    window_h: int = 620
    show_info: bool = True               # 显示「此刻的事实」（空格）
    info_compact: bool = False           # 信息卡精简模式（只留时间与那句话）
    show_ribbon: bool = True
    frame_rate: int = DEFAULT_FRAME_RATE  # 画面刷新率（帧/秒），见 FRAME_RATES
    # 窗外画什么（菜单 → 场景）。默认全开＝和以前一模一样；
    # 想只看天、或者嫌街上太热闹，就在这里关。
    show_people: bool = True
    show_traffic: bool = True
    show_trees: bool = True
    show_lamps: bool = True
    show_planes: bool = True
    show_skyline: bool = True
    show_clouds: bool = True
    show_stars: bool = True
    show_weatherfx: bool = True          # 雨雪粒子与闪电
    show_plant: bool = True
    plant_sway: bool = True              # 盆栽随风摇曳（风大才看得出来）
    language: str = "auto"               # 界面语言，见 i18n.LANGUAGES
    units: str = "auto"                  # auto / metric / imperial
    time_format: str = "auto"            # auto / h24 / h12
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
        cfg._migrate()
        cfg.sanitize()
        return cfg

    def _migrate(self) -> None:
        """把旧版本的配置升到当前 schema。

        现在只有第 1 版，所以只做"把版本号写对"这一件事；真正的迁移在
        SCHEMA 往上加的时候写在这里（例如 1 → 2 时把某个字段换算过去，
        然后把 self.schema 设成 2）。
        """
        try:
            self.schema = int(self.schema)
        except (TypeError, ValueError):
            self.schema = SCHEMA
        if self.schema < SCHEMA:
            # 将来在这里按版本逐级升级
            self.schema = SCHEMA
        elif self.schema > SCHEMA:
            # 文件比本程序还新（用户降级过）：不假装认识它，交给 sanitize 兜底
            self.schema = SCHEMA

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
        if self.wallpaper_info_mode not in INFO_MODES:
            self.wallpaper_info_mode = "follow"
        if self.language not in language_choices():
            self.language = "auto"
        if self.units not in UNIT_CHOICES:
            self.units = "auto"
        if self.time_format not in TIME_FORMATS:
            self.time_format = "auto"
        try:
            self.wallpaper_slot = 1 if int(self.wallpaper_slot or 0) % 2 else 0
        except (TypeError, ValueError):
            self.wallpaper_slot = 0
        for name in ("wallpaper_auto", "wallpaper_dynamic", "mirror_weather",
                     "autostart", "autostart_hidden", "update_check",
                     "always_on_top", "show_ribbon", "wallpaper_show_info",
                     "wallpaper_show_ribbon", "show_info", "info_compact",
                     "show_people", "show_traffic", "show_trees", "show_lamps",
                     "show_planes", "show_skyline", "show_clouds", "show_stars",
                     "show_weatherfx", "show_plant", "plant_sway",
                     "first_run_done"):
            setattr(self, name, bool(getattr(self, name, False)))
        try:
            self.frame_rate = int(self.frame_rate)
        except (TypeError, ValueError):
            self.frame_rate = DEFAULT_FRAME_RATE
        if self.frame_rate not in FRAME_RATES:
            self.frame_rate = DEFAULT_FRAME_RATE
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
    "Asia/Shanghai": (N_("西安"), N_("陕西省"), N_("中国"), 34.3416, 108.9398),
    "Asia/Chongqing": (N_("重庆"), N_("重庆市"), N_("中国"), 29.5630, 106.5516),
    "Asia/Harbin": (N_("哈尔滨"), N_("黑龙江省"), N_("中国"), 45.8038, 126.5350),
    "Asia/Urumqi": (N_("乌鲁木齐"), N_("新疆"), N_("中国"), 43.8256, 87.6168),
    "Asia/Hong_Kong": (N_("香港"), "", N_("中国"), 22.3193, 114.1694),
    "Asia/Taipei": (N_("台北"), "", N_("中国"), 25.0330, 121.5654),
    "Asia/Tokyo": (N_("东京"), "", N_("日本"), 35.6762, 139.6503),
    "Asia/Seoul": (N_("首尔"), "", N_("韩国"), 37.5665, 126.9780),
    "Asia/Singapore": (N_("新加坡"), "", N_("新加坡"), 1.3521, 103.8198),
    "Asia/Kolkata": (N_("加尔各答"), "", N_("印度"), 22.5726, 88.3639),
    "Asia/Dubai": (N_("迪拜"), "", N_("阿联酋"), 25.2048, 55.2708),
    "Europe/London": (N_("伦敦"), "", N_("英国"), 51.5074, -0.1278),
    "Europe/Paris": (N_("巴黎"), "", N_("法国"), 48.8566, 2.3522),
    "Europe/Berlin": (N_("柏林"), "", N_("德国"), 52.5200, 13.4050),
    "Europe/Moscow": (N_("莫斯科"), "", N_("俄罗斯"), 55.7558, 37.6173),
    "America/New_York": (N_("纽约"), "", N_("美国"), 40.7128, -74.0060),
    "America/Chicago": (N_("芝加哥"), "", N_("美国"), 41.8781, -87.6298),
    "America/Denver": (N_("丹佛"), "", N_("美国"), 39.7392, -104.9903),
    "America/Los_Angeles": (N_("旧金山"), "", N_("美国"), 37.7749, -122.4194),
    "America/Sao_Paulo": (N_("圣保罗"), "", N_("巴西"), -23.5505, -46.6333),
    "Australia/Sydney": (N_("悉尼"), "", N_("澳大利亚"), -33.8688, 151.2093),
    "Africa/Cairo": (N_("开罗"), "", N_("埃及"), 30.0444, 31.2357),
    "UTC": (N_("格林尼治"), "", N_("英国"), 51.4779, 0.0015),
    "Etc/UTC": (N_("格林尼治"), "", N_("英国"), 51.4779, 0.0015),
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
    now = datetime.now()
    if now.tzinfo is None:
        now = now.astimezone()
    offset_h = now.utcoffset().total_seconds() / 3600.0 if now.utcoffset() else 0.0
    lon = max(-180.0, min(180.0, offset_h * 15.0))
    tz_name = now.tzname() or f"UTC{offset_h:+.0f}"
    return Location(_("{zone} 一带").format(zone=tz_name), "", "", 34.0, lon,
                    key or "auto", guessed=True)


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
            AUTOSTART_FILE.write_text(autostart_entry(argv, hidden),
                                      encoding="utf-8")
        elif AUTOSTART_FILE.exists():
            AUTOSTART_FILE.unlink()
    except OSError:
        pass


def autostart_entry(argv, hidden: bool = False) -> str:
    """自启 .desktop 文件的内容：名字与说明按**每一种**语言各写一份。

    GNOME 的程序菜单 / 自启项会在 `Name[ja]` 这种键里按系统语言挑一条，
    所以这里一次把认得的所有语言都写出来（中文是 `Name=` 本身）。
    """
    lines = ["[Desktop Entry]", "Type=Application", "Name=" + translate_in("zh", "窗")]
    for code in available_languages():
        if code == "zh":
            continue
        lines.append(f"Name[{code}]={translate_in(code, '窗')}")
    lines.append("GenericName=" + translate_in("zh", "实时天空之窗"))
    lines.append("Comment=" + translate_in("zh", "把你头顶此刻真实的天空搬到桌面的一扇窗里"))
    for code in available_languages():
        if code == "zh":
            continue
        lines.append(f"GenericName[{code}]={translate_in(code, '实时天空之窗')}")
        lines.append(f"Comment[{code}]={translate_in(code, '把你头顶此刻真实的天空搬到桌面的一扇窗里')}")
    lines += [f"Exec={autostart_wanted_exec(argv, hidden)}",
              "Icon=chuang", "Terminal=false", "StartupNotify=false",
              "X-GNOME-Autostart-enabled=true", "Categories=Utility;"]
    return "\n".join(lines) + "\n"
