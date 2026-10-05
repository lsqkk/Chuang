"""多语言这一摊的自动化检查。

盯四件事：

1. **选语言**：配置 / `CHUANG_LANG` / 系统变量怎么定，认不出退回中文；
2. **查译文**：本语言 → 英文 → 原文 这条链真的按顺序走；
3. **本地的写法**：数字的小数点、日期、12/24 小时制、公制/英制；
4. **目录还在**：源码里的每个词各语言的 `.po` 都有；`.mo` 与 `.po` 一致
   （`.mo` 是提交进仓库的，改完 `.po` 忘了 `compile` 就会被这条抓住）。
"""

import importlib.util
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from chuang import i18n

ROOT = Path(__file__).resolve().parent.parent


def _tool():
    spec = importlib.util.spec_from_file_location(
        "chuang_i18n_tool", ROOT / "tools" / "i18n.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class I18nCase(unittest.TestCase):
    def tearDown(self):
        # 全局状态：一个测试把语言换走了，后面的测试（卡片、场景…）看到的
        # 就不是中文了——每个测试自己负责还原。
        i18n.setup("zh")


class TestCatalog(I18nCase):

    def test_shipped_languages_have_catalogs(self):
        langs = i18n.available_languages()
        self.assertIn("zh", langs)                 # 源语言永远在
        for code in ("en", "ja", "fr"):
            self.assertIn(code, langs, f"{code} 的目录没编出来")
            self.assertTrue((i18n.LOCALE_DIR / code / "LC_MESSAGES"
                             / "chuang.mo").exists())
            self.assertIn(code, i18n.LANGUAGES)

    def test_english_is_the_fallback(self):
        i18n.setup("en")
        self.assertEqual(i18n.tr("太阳"), "Sun")
        self.assertEqual(i18n.tr("窗外的天气"), "Weather outside")

    def test_source_language_returns_the_original(self):
        i18n.setup("zh")
        self.assertEqual(i18n.tr("太阳"), "太阳")
        self.assertEqual(i18n.tr("一个原文里没有的词"), "一个原文里没有的词")

    def test_chain_falls_back_to_english_then_source(self):
        """自己做的假语言目录：它有 A、没有 B；B 该由英文顶上，C 谁的没有→原文。"""
        tool = _tool()
        with tempfile.TemporaryDirectory() as tmp:
            loc = Path(tmp)
            for lang, table in (
                    ("xx", {b"": b"Content-Type: text/plain; charset=UTF-8\n"
                                  b"Plural-Forms: nplurals=1; plural=0;\n",
                            "太阳".encode(): "Soleil-X".encode()}),
                    ("en", {b"": b"Content-Type: text/plain; charset=UTF-8\n"
                                  b"Plural-Forms: nplurals=2; plural=(n != 1);\n",
                            "太阳".encode(): b"Sun",
                            "月亮".encode(): b"Moon"})):
                target = loc / lang / "LC_MESSAGES"
                target.mkdir(parents=True)
                (target / "chuang.mo").write_bytes(tool.build_mo(table))
            old = i18n.LOCALE_DIR
            i18n.LOCALE_DIR = loc
            try:
                catalog = i18n._load("xx")
                self.assertIsNotNone(catalog)
                self.assertEqual(catalog.gettext("太阳"), "Soleil-X")   # 本语言
                self.assertEqual(catalog.gettext("月亮"), "Moon")       # 退到英文
                self.assertEqual(catalog.gettext("星星"), "星星")       # 退到原文
            finally:
                i18n.LOCALE_DIR = old

    def test_plural_uses_the_language_rule(self):
        i18n.setup("en")
        self.assertEqual(i18n.ntr("找到 {n} 个地方", "找到 {n} 个地方", 1)
                         .format(n=1), "Found 1 place")
        self.assertEqual(i18n.ntr("找到 {n} 个地方", "找到 {n} 个地方", 4)
                         .format(n=4), "Found 4 places")
        i18n.setup("zh")
        self.assertIn("4", i18n.ntr("找到 {n} 个地方", "找到 {n} 个地方", 4)
                      .format(n=4))

    def test_translate_in_another_language(self):
        i18n.setup("zh")
        self.assertEqual(i18n.translate_in("ja", "太阳"), "太陽")
        self.assertEqual(i18n.translate_in("fr", "巴黎"), "Paris")

    def test_rtl_flag(self):
        self.assertFalse(i18n.is_rtl("fr"))
        self.assertTrue(i18n.is_rtl("ar"))


class TestDetection(I18nCase):

    def test_language_tags(self):
        self.assertEqual(i18n.detect_language("en_US.UTF-8"), "en")
        self.assertEqual(i18n.detect_language("ja_JP"), "ja")
        self.assertEqual(i18n.detect_language("zh-Hans"), "zh")
        self.assertEqual(i18n.detect_language("fr_FR:fr"), "fr")
        self.assertEqual(i18n.detect_language("kl_KL"), "zh")   # 认不出 → 中文
        self.assertEqual(i18n.detect_language("auto"), i18n.detect_language())

    def test_setup_applies_language(self):
        with _env("CHUANG_LANG", "ja"):
            self.assertEqual(i18n.setup(), "ja")
        i18n.setup("fr")
        self.assertEqual(i18n.language(), "fr")
        self.assertEqual(i18n.tr("太阳"), "Soleil")

    def test_unknown_language_falls_back_to_chinese(self):
        self.assertEqual(i18n.setup("nope"), "zh")


class TestLocalFormats(I18nCase):

    def test_decimal_separator(self):
        i18n.setup("fr")
        self.assertEqual(i18n.fmt_num(12.4, 1), "12,4")
        i18n.setup("en")
        self.assertEqual(i18n.fmt_num(12.4, 1), "12.4")

    def test_clock_follows_the_language(self):
        when = datetime(2026, 10, 5, 18, 35)
        i18n.setup("en")
        self.assertTrue(i18n.hour12())
        self.assertIn("PM", i18n.clock(when))
        i18n.setup("ja")
        self.assertFalse(i18n.hour12())
        self.assertEqual(i18n.clock(when), "18:35")

    def test_dates_are_localized(self):
        when = datetime(2026, 10, 5, 18, 35)
        i18n.setup("zh")
        self.assertIn("10 月 5 日", i18n.date_long(when))
        i18n.setup("ja")
        self.assertEqual(i18n.date_long(when), "10月5日（月）")
        i18n.setup("fr")
        self.assertIn("oct.", i18n.date_long(when))

    def test_duration(self):
        delta = timedelta(hours=3, minutes=20)
        i18n.setup("zh")
        self.assertEqual(i18n.duration(delta), "3 小时 20 分")
        i18n.setup("en")
        self.assertEqual(i18n.duration(delta), "3h 20m")


class TestUnits(I18nCase):

    def test_metric_default(self):
        i18n.setup("en")
        self.assertEqual(i18n.units_system(), "metric")
        self.assertEqual(i18n.temp_full(21), "21°C")
        self.assertEqual(i18n.speed(12.4), "12.4 km/h")
        self.assertEqual(i18n.precip(1.5), "1.5 mm/h")
        self.assertEqual(i18n.dist(24.0), "24.0 km")

    def test_imperial_switch(self):
        i18n.setup("en", units="imperial")
        self.assertEqual(i18n.units_system(), "imperial")
        self.assertAlmostEqual(i18n.temp_value(0.0), 32.0)
        self.assertIn("°F", i18n.temp_full(0))
        self.assertIn("mph", i18n.speed(12.4))
        self.assertIn("in/h", i18n.precip(25.4))
        self.assertIn("inHg", i18n.pressure(1013.25))

    def test_auto_units_use_the_region(self):
        with _env("LANG", "en_US.UTF-8"), _env("CHUANG_LANG", None):
            self.assertTrue(i18n.resolve_units("en", "auto",
                                               tag=os.environ["LANG"]))
        self.assertFalse(i18n.resolve_units("fr", "auto", tag="fr_FR"))

    def test_units_tag_changes_with_setting(self):
        i18n.setup("en")
        metric = i18n.units_tag()
        i18n.setup("en", units="imperial")
        self.assertNotEqual(metric, i18n.units_tag())


class TestCatalogsAreComplete(I18nCase):

    def test_every_source_word_is_translated(self):
        tool = _tool()
        wanted = tool._wanted()
        for po in sorted((ROOT / "po").glob("*.po")):
            singles, plurals = tool.parse_po(po)
            have = {key.split("\x04")[-1] for key in singles}
            for key, (msgid_plural, _joined) in plurals.items():
                have.add(key.split("\x04")[-1])
                have.add(msgid_plural)
            missing = wanted - have
            self.assertEqual(missing, set(),
                             f"{po.name} 还差 {len(missing)} 个词："
                             f"{sorted(missing)[:5]}")
            stale = have - wanted
            self.assertEqual(stale, set(),
                             f"{po.name} 里有源码里已经没有的词："
                             f"{sorted(stale)[:5]}")

    def test_mo_is_up_to_date(self):
        """`.mo` 是从 `.po` 编出来的，改了 `.po` 忘了 compile，这条会红。"""
        tool = _tool()
        for po in sorted((ROOT / "po").glob("*.po")):
            singles, plurals = tool.parse_po(po)
            mo: dict = {b"": tool.header(po).encode("utf-8")}
            for key, value in singles.items():
                mo[key.encode("utf-8")] = value.encode("utf-8")
            for key, (plural, joined) in plurals.items():
                mo[(key + "\x00" + plural).encode("utf-8")] = joined.encode("utf-8")
            expected = tool.build_mo(mo)
            actual = (i18n.LOCALE_DIR / po.stem / "LC_MESSAGES"
                      / "chuang.mo").read_bytes()
            self.assertEqual(actual, expected,
                             f"{po.stem}.mo 与 po 不一致：跑 python3 tools/i18n.py compile")


class TestDesktopEntry(I18nCase):

    def test_autostart_entry_lists_each_language(self):
        from chuang import config
        i18n.setup("zh")
        text = config.autostart_entry(["/usr/bin/chuang"])
        self.assertIn("Name=窗", text)              # 中文源语言 → Name=
        self.assertIn("Name[en]=Chuang", text)
        self.assertIn("Name[ja]=", text)
        self.assertIn("Name[fr]=", text)
        self.assertIn("Comment[en]=", text)


class TestInfoCardCache(I18nCase):

    def test_card_key_changes_with_language_and_units(self):
        """卡片按 `_info_key` 离屏缓存——换语言 / 换单位必须让它重画。"""
        from chuang.render import SkyPainter
        from chuang.scene import SkyEngine
        engine = SkyEngine()
        engine.set_location(34.34, 108.94, "Asia/Shanghai")
        when = datetime.now(engine._tzinfo).replace(microsecond=0)
        scene = engine.build(when)
        i18n.setup("zh")
        painter = SkyPainter(seed=3)
        base = painter._info_key(1000, 650, scene)
        i18n.setup("en")
        self.assertNotEqual(base, painter._info_key(1000, 650, scene))
        i18n.setup("en", units="imperial")
        self.assertNotEqual(base, painter._info_key(1000, 650, scene))


class _env:
    """临时改一个环境变量，退出时还原（None = 删掉）。"""

    def __init__(self, name, value):
        self.name = name
        self.value = value
        self.old = None
        self.had = False

    def __enter__(self):
        self.had = self.name in os.environ
        self.old = os.environ.get(self.name)
        if self.value is None:
            os.environ.pop(self.name, None)
        else:
            os.environ[self.name] = self.value
        return self

    def __exit__(self, *exc):
        if self.had:
            os.environ[self.name] = self.old
        else:
            os.environ.pop(self.name, None)
        return False


if __name__ == "__main__":
    unittest.main()
