"""多语言：把界面上的字与"本地的写法"从代码里拿出来。

「窗」的**源语言是中文**——代码里写的 msgid 就是那句中文，`.po` / `.mo` 里
放英、日、法……的译文。这样做有两个好处：中文界面永远是"原文"，不会有
"翻译反而更差"的中间层；而加一门语言只是往 `chuang/locale/<语言>/` 里放一份
编译好的 `.mo`（见 `tools/i18n.py`），代码一行都不用改。

这里管四件事：

1. **选语言**：`setup()` 按 `config.language` → `CHUANG_LANG` → `LANGUAGE` /
   `LC_ALL` / `LC_MESSAGES` / `LANG` 的顺序定；认不出就退回中文。
2. **取译文**：`_()`（单数）、`ntr()`（复数）、`ctr()`（带语境）。查不到时
   **先退到英文**（`en` 是最全的一份），再退到中文原文——半翻译的语言不会
   出现一片空白。
3. **本地的写法**：数字的小数点（法语用逗号）、日期与星期、12/24 小时制、
   度量单位（公制 / 英制）。这些和"换一门语言"是一整套，缺了它界面会变成
   "英文句子 + 中文式排版"。
4. **文字方向**：`is_rtl()` 给以后加阿拉伯语 / 希伯来语留的口子（Pango 本来
   就会逐行双向排版；整块版面的镜像还没做，见 DESIGN.md）。

只用标准库（`gettext`），不引入任何第三方依赖——这仍然是项目的卖点之一。
"""

from __future__ import annotations

import gettext
import os
import re
from pathlib import Path

DOMAIN = "chuang"
LOCALE_DIR = Path(__file__).resolve().parent / "locale"

# 源语言（代码里写的那些中文）与"认得但只当兜底"的英文
SOURCE = "zh"
FALLBACK = "en"

# 可选的界面语言：code → (自称, 英文名)。自称用它自己的字写（日本語 而不是
# "Japanese"），语言选择菜单里读起来最自然。
LANGUAGES: dict[str, tuple[str, str]] = {
    "zh": ("简体中文", "Chinese (Simplified)"),
    "en": ("English", "English"),
    "ja": ("日本語", "Japanese"),
    "fr": ("Français", "French"),
    "de": ("Deutsch", "German"),
    "es": ("Español", "Spanish"),
}
DEFAULT_LANGUAGE = "zh"

# 语言标签的别名（zh_CN / zh-Hans / en_US.UTF-8 → zh / en）
_ALIASES = {
    "zh": "zh", "zh_cn": "zh", "zh_hans": "zh", "zh_sg": "zh", "zh_tw": "zh",
    "zh_hant": "zh", "cn": "zh", "chi": "zh", "zho": "zh",
    "en": "en", "en_us": "en", "en_gb": "en", "en_au": "en", "eng": "en",
    "ja": "ja", "ja_jp": "ja", "jp": "ja", "jpn": "ja",
    "fr": "fr", "fr_fr": "fr", "fr_ca": "fr", "fra": "fr", "fre": "fr",
    "de": "de", "de_de": "de", "ger": "de", "deu": "de",
    "es": "es", "es_es": "es", "es_mx": "es", "spa": "es",
}

# 用 12 小时制的语言（其余一律 24 小时制，测试也钉在 24 小时制这一侧）
_HOUR12 = {"en"}

# 用小数逗号的语言
_COMMA_DECIMAL = {"fr", "de", "es"}

# 用英制的地区（`units=auto` 时看语言标签里的地区）。这些语言本身都是公制，
# 只有明确写着美/英属地区时才切过去。
_IMPERIAL_REGIONS = {"us", "lr", "mm"}

_WEEKDAYS = {
    "zh": ("周一", "周二", "周三", "周四", "周五", "周六", "周日"),
    "en": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
    "ja": ("月", "火", "水", "木", "金", "土", "日"),
    "fr": ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."),
    "de": ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"),
    "es": ("lun", "mar", "mié", "jue", "vie", "sáb", "dom"),
}

_MONTHS = {
    "zh": tuple(f"{n} 月" for n in range(1, 13)),
    "en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
    "ja": tuple(f"{n}月" for n in range(1, 13)),
    "fr": ("janv.", "févr.", "mars", "avr.", "mai", "juin",
           "juil.", "août", "sept.", "oct.", "nov.", "déc."),
    "de": ("Jan.", "Feb.", "März", "Apr.", "Mai", "Juni",
           "Juli", "Aug.", "Sept.", "Okt.", "Nov.", "Dez."),
    "es": ("ene.", "feb.", "mar.", "abr.", "may.", "jun.",
           "jul.", "ago.", "sept.", "oct.", "nov.", "dic."),
}

_COMPASS = {
    "zh": ("北", "北东北", "东北", "东北东", "东", "东南东", "东南", "东南南",
           "南", "西南南", "西南", "西南西", "西", "西北西", "西北", "西北北"),
    "en": ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
           "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"),
    "ja": ("北", "北北東", "北東", "東北東", "東", "東南東", "南東", "南南東",
           "南", "南南西", "南西", "西南西", "西", "西北西", "北西", "北北西"),
    "fr": ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
           "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO"),
    "de": ("N", "NNO", "NO", "ONO", "O", "OSO", "SO", "SSO",
           "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"),
    "es": ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
           "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO"),
}


# --------------------------------------------------------------------------
# 译文目录
# --------------------------------------------------------------------------

class _Catalog:
    """一条查找链：本语言 → 英文 → 原文（msgid）。"""

    def __init__(self, languages: list[str]) -> None:
        self.langs = languages
        self.tables: list[gettext.GNUTranslations] = []
        for lang in languages:
            path = LOCALE_DIR / lang / "LC_MESSAGES" / f"{DOMAIN}.mo"
            try:
                with open(path, "rb") as fh:
                    self.tables.append(gettext.GNUTranslations(fh))
            except (OSError, EOFError, ValueError):
                self.tables.append(None)  # type: ignore[arg-type]

    def gettext(self, msgid: str) -> str:
        for table in self.tables:
            if table is None:
                continue
            value = table._catalog.get(msgid)
            if value is not None:
                return value
        return msgid

    def pgettext(self, context: str, msgid: str) -> str:
        return self.gettext(context + "\x04" + msgid if context else msgid)

    def ngettext(self, singular: str, plural: str, n: int) -> str:
        for table in self.tables:
            if table is None:
                continue
            # 复数的译文在表里是 `(原文, 第几条)` 这样的元组键（见 gettext 的解析）
            if singular not in table._catalog and (singular, 0) not in table._catalog:
                continue
            try:
                return table.ngettext(singular, plural, n)
            except (KeyError, IndexError, ValueError):
                continue
        return singular if n == 1 else plural


_active: _Catalog | None = None       # 当前语言（中文 / 没装目录时是 None = 原文直出）
_language = DEFAULT_LANGUAGE
_units_imperial = False               # 公制 / 英制
_hour12 = False                       # 12 小时制 / 24 小时制
_decimal_point = "."
_others: dict[str, "_Catalog | None"] = {}


def _load(language: str) -> _Catalog | None:
    """这门语言要按什么顺序查：它自己 → 英文 → 原文。"""
    if language == SOURCE:
        return None
    chain = [language]
    if language != FALLBACK:
        chain.append(FALLBACK)
    catalog = _Catalog(chain)
    if not any(catalog.tables):
        return None                   # 一份目录都没有：就当没翻译，别拖慢渲染
    return catalog


def _norm_tag(tag: str) -> str:
    """`zh_CN.UTF-8` / `en-US` → `zh` / `en`（认不出返回空串）。"""
    if not tag:
        return ""
    tag = tag.split(":", 1)[0].split(".", 1)[0].strip().replace("-", "_").lower()
    if tag in _ALIASES:
        return _ALIASES[tag]
    head = tag.split("_", 1)[0]
    return _ALIASES.get(head, "")


def _region(tag: str) -> str:
    """语言标签里的地区（`en_US` → `us`），没有就是空串。"""
    if not tag:
        return ""
    tag = tag.split(":", 1)[0].split(".", 1)[0].replace("-", "_")
    parts = tag.split("_")
    return parts[1].lower() if len(parts) > 1 else ""


def _env_tag() -> str:
    """从环境变量里挑一个语言标签（GNU 的优先级：LANGUAGE 最高）。"""
    for name in ("CHUANG_LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(name)
        if not value:
            continue
        for part in value.split(":"):
            if part.strip():
                return part.strip()
    return ""


def detect_language(preference: str = "auto") -> str:
    """`auto` 就按环境猜，否则认配置里选的那门；都认不出退回 `DEFAULT_LANGUAGE`。"""
    if preference and preference not in ("auto", ""):
        found = _norm_tag(preference)
        return found or DEFAULT_LANGUAGE
    found = _norm_tag(_env_tag())
    return found or DEFAULT_LANGUAGE


def resolve_units(language: str, preference: str = "auto", tag: str = "") -> bool:
    """要不要用英制。`auto` 只在地区明确写着美/利比里亚/缅甸时才切过去。"""
    if preference == "imperial":
        return True
    if preference in ("metric", "", None):
        return False
    return _region(tag or _env_tag()) in _IMPERIAL_REGIONS


def setup(preference: str = "auto", units: str = "auto",
          time_format: str = "auto") -> str:
    """定下这一跑用哪门语言、哪套单位。返回真正选中的语言代码。"""
    global _active, _language, _units_imperial, _hour12, _decimal_point
    language = detect_language(preference)
    _language = language
    _active = _load(language)
    _units_imperial = resolve_units(language, units)
    if time_format == "h12":
        _hour12 = True
    elif time_format == "h24":
        _hour12 = False
    else:
        _hour12 = language in _HOUR12
    _decimal_point = "," if language in _COMMA_DECIMAL else "."
    return language


def language() -> str:
    return _language


def language_name(code: str | None = None, native: bool = True) -> str:
    entry = LANGUAGES.get(code or _language)
    if entry is None:
        return code or _language
    return entry[0] if native else entry[1]


def available_languages() -> list[str]:
    """真的带目录的语言（中文是源语言，永远算在内）。"""
    out = [SOURCE]
    for code in LANGUAGES:
        if code == SOURCE:
            continue
        if (LOCALE_DIR / code / "LC_MESSAGES" / f"{DOMAIN}.mo").exists():
            out.append(code)
    return out


def is_rtl(code: str | None = None) -> bool:
    """这门语言是不是从右往左写。目前发的几门都是 LTR，留给以后加阿语。"""
    return (code or _language) in ("ar", "he", "fa", "ur")


# --------------------------------------------------------------------------
# 取译文
# --------------------------------------------------------------------------

def tr(msgid: str) -> str:
    return _active.gettext(msgid) if _active is not None else msgid


def translate_in(lang: str, msgid: str) -> str:
    """用**指定的**一门语言翻一个词（跟当前界面语言无关）。

    `~/.config/autostart/chuang.desktop` 里要一次写好几种 `Name[xx]`，
    写它的时候得能查别的语言的目录。
    """
    if lang == _language:
        return tr(msgid)
    if lang not in _others:
        _others[lang] = _load(lang)
    catalog = _others[lang]
    return catalog.gettext(msgid) if catalog is not None else msgid


def ntr(singular: str, plural: str, n: int) -> str:
    return _active.ngettext(singular, plural, n) if _active is not None \
        else (singular if n == 1 else plural)


def ctr(context: str, msgid: str) -> str:
    return _active.pgettext(context, msgid) if _active is not None else msgid


def N_(msgid: str) -> str:
    """给 `tools/i18n.py` 的标记：这个词要被翻译，但不是现在（模块级常量用）。"""
    return msgid


# 模块里习惯写 `from .i18n import tr as _`
_ = tr


# --------------------------------------------------------------------------
# 本地的写法：数字 / 日期 / 单位
# --------------------------------------------------------------------------

def fmt_num(value, digits: int = 0) -> str:
    """按这门语言的写法写一个数（小数点用 . 还是 ,）。"""
    try:
        text = f"{float(value):.{max(0, int(digits))}f}"
    except (TypeError, ValueError):
        text = str(value)
    if _decimal_point != ".":
        text = text.replace(".", _decimal_point)
    return text


def weekday(dt) -> str:
    return _WEEKDAYS.get(_language, _WEEKDAYS["en"])[dt.weekday()]


def month_name(dt) -> str:
    return _MONTHS.get(_language, _MONTHS["en"])[dt.month - 1]


def date_long(dt) -> str:
    """卡片上那行日期：`9 月 24 日 周三` / `Wed, Sep 24` / `9月24日（水）`…"""
    m, d, w = month_name(dt), dt.day, weekday(dt)
    if _language == "zh":
        return f"{dt.month} 月 {dt.day} 日 {w}"
    if _language == "ja":
        return f"{dt.month}月{dt.day}日（{w}）"
    if _language == "en":
        return f"{w}, {m} {d}"
    if _language == "fr":
        return f"{w} {dt.day} {m}"
    return f"{w}, {dt.day}. {m}"


def date_short(dt) -> str:
    """短一点的日期（跳到别的日子时写的那句）：`9月24日` / `Sep 24` / `24 sept.`。"""
    if _language == "zh":
        return f"{dt.month} 月 {dt.day} 日"
    if _language == "ja":
        return f"{dt.month}月{dt.day}日"
    if _language == "en":
        return f"{month_name(dt)} {dt.day}"
    return f"{dt.day} {month_name(dt)}"


def clock(dt) -> str:
    """按这门语言的习惯写时刻（12 / 24 小时制）。"""
    if _hour12:
        text = dt.strftime("%I:%M").lstrip("0") or "12:00"
        return f"{text} {dt.strftime('%p')}"
    return dt.strftime("%H:%M")


def stamp(dt) -> str:
    """诊断 / 详情里那种"什么时候问回来的"：日期 + 时刻。"""
    return f"{date_short(dt)} {clock(dt)}"


def duration(delta) -> str:
    """一段时长的人话（3 小时 20 分）。用缩写，省掉各语言的复数变化。"""
    total = int(max(0, delta.total_seconds())) if delta is not None else 0
    h, m = divmod(total // 60, 60)
    if _language == "zh":
        return f"{h} 小时 {m} 分" if h and m else (f"{h} 小时" if h else f"{m} 分钟")
    if _language == "ja":
        return f"{h}時間{m}分" if h and m else (f"{h}時間" if h else f"{m}分")
    if _language == "fr":
        return f"{h} h {m} min" if h and m else (f"{h} h" if h else f"{m} min")
    if _language == "de":
        return f"{h} Std. {m} Min." if h and m else (f"{h} Std." if h else f"{m} Min.")
    return f"{h}h {m}m" if h and m else (f"{h}h" if h else f"{m}m")


def compass(azimuth: float) -> str:
    """风向 / 方位的人话（16 个方向）。"""
    names = _COMPASS.get(_language, _COMPASS["en"])
    index = int(((azimuth % 360) + 11.25) // 22.5) % 16
    return names[index]


# ---- 度量单位 ----------------------------------------------------------

def units_system() -> str:
    return "imperial" if _units_imperial else "metric"


def hour12() -> bool:
    return _hour12


def units_tag() -> str:
    """进离屏缓存键用：换单位 / 换语言之后卡片必须重画。"""
    return f"{_language}:{units_system()}:{'h12' if _hour12 else 'h24'}"


def temp_value(celsius: float) -> float:
    return celsius * 9.0 / 5.0 + 32.0 if _units_imperial else celsius


def temp_unit() -> str:
    return tr("°F") if _units_imperial else tr("°C")


def temp(celsius: float, digits: int = 0) -> str:
    return f"{fmt_num(temp_value(celsius), digits)}°"


def temp_full(celsius: float, digits: int = 0) -> str:
    return f"{fmt_num(temp_value(celsius), digits)}{temp_unit()}"


def speed_value(kmh: float) -> float:
    return kmh * 0.621371 if _units_imperial else kmh


def speed_unit() -> str:
    return tr("mph") if _units_imperial else tr("km/h")


def speed(kmh: float, digits: int = 1) -> str:
    return f"{fmt_num(speed_value(kmh), digits)} {speed_unit()}"


def precip_value(mm: float) -> float:
    return mm / 25.4 if _units_imperial else mm


def precip_unit() -> str:
    return tr("in/h") if _units_imperial else tr("mm/时")


def precip(mm: float, digits: int = 1) -> str:
    return f"{fmt_num(precip_value(mm), digits)} {precip_unit()}"


def dist_value(km: float) -> float:
    return km * 0.621371 if _units_imperial else km


def dist_unit() -> str:
    return tr("mi") if _units_imperial else tr("km")


def dist(km: float, digits: int = 1) -> str:
    return f"{fmt_num(dist_value(km), digits)} {dist_unit()}"


def pressure_value(hpa: float) -> float:
    return hpa * 0.02953 if _units_imperial else hpa


def pressure_unit() -> str:
    return tr("inHg") if _units_imperial else tr("hPa")


def pressure(hpa: float, digits: int = 0) -> str:
    return f"{fmt_num(pressure_value(hpa), digits if not _units_imperial else 2)} " \
           f"{pressure_unit()}"


def place(text: str) -> str:
    """地名：认得出就翻（默认猜出来的那几座城），认不出原样返回。"""
    return tr(text) if text else text


def place_label(text: str) -> str:
    """`西安 · 陕西省` 这种"城市 · 地区"的地名串：逐段翻。"""
    if not text:
        return text
    return " · ".join(place(part) for part in text.split(" · "))
