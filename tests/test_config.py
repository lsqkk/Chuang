"""配置：默认值、坏值纠正、XDG 目录的取值。"""

import importlib
import os
import unittest
from pathlib import Path
from unittest import mock

from chuang import config as C


class TestXdgDir(unittest.TestCase):

    def test_falls_back_when_unset(self):
        fallback = Path("/home/u/.config")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(C._xdg_dir("XDG_CONFIG_HOME", fallback), fallback)

    def test_empty_string_is_not_a_directory(self):
        """`XDG_CONFIG_HOME=` （空串）以前会让配置写进当前工作目录。"""
        fallback = Path("/home/u/.config")
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": ""}):
            self.assertEqual(C._xdg_dir("XDG_CONFIG_HOME", fallback), fallback)

    def test_uses_the_env_var_when_set(self):
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/tmp/pxdg"}):
            self.assertEqual(C._xdg_dir("XDG_CONFIG_HOME", Path("/home/u/.config")),
                             Path("/tmp/pxdg"))

    def test_module_paths_follow_home(self):
        """换个 HOME（测试实例就该这么隔离）之后，配置与自启项都跟着走。"""
        with mock.patch.dict(os.environ, {"HOME": "/tmp/fakehome"}, clear=False):
            os.environ.pop("XDG_CONFIG_HOME", None)
            importlib.reload(C)
            try:
                self.assertTrue(str(C.CONFIG_FILE).startswith("/tmp/fakehome/"))
                self.assertTrue(str(C.AUTOSTART_FILE).startswith("/tmp/fakehome/"))
            finally:
                importlib.reload(C)          # 还原成真实环境


class TestSanitize(unittest.TestCase):

    def test_bad_values_are_corrected(self):
        cfg = C.Config()
        cfg.wallpaper_interval = "abc"
        cfg.close_behavior = "nonsense"
        cfg.wallpaper_slot = 7
        cfg.fov = 9999
        cfg.window_w = 10
        cfg.window_h = "x"
        cfg.sanitize()
        self.assertEqual(cfg.wallpaper_interval, 10)
        self.assertEqual(cfg.close_behavior, "ask")
        self.assertEqual(cfg.wallpaper_slot, 1)      # 7 % 2
        self.assertEqual(cfg.fov, 360.0)
        self.assertEqual(cfg.window_w, 320)
        self.assertEqual(cfg.window_h, 620)

    def test_wallpaper_modes_are_exclusive(self):
        cfg = C.Config(wallpaper_auto=True, wallpaper_dynamic=True)
        cfg.sanitize()
        self.assertFalse(cfg.wallpaper_dynamic)

    def test_new_options_field_defaults_empty(self):
        """1.1.8 加的字段：老配置里没有它，读出来必须是空串而不是崩。"""
        self.assertEqual(C.Config().prev_wallpaper_options, "")

    def test_schema_is_written_and_migrated(self):
        """老配置（没有 schema）读进来要升到当前版本，坏值也不能崩。"""
        self.assertEqual(C.Config().schema, C.SCHEMA)
        for bad in (None, "x", {}, 99.5):
            cfg = C.Config(schema=bad)
            cfg._migrate()
            self.assertEqual(cfg.schema, C.SCHEMA)
        # 真的从"没有 schema 键"的老文件读一遍
        import json
        import tempfile
        from pathlib import Path
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            path.write_text(json.dumps({"mirror_weather": False}), encoding="utf-8")
            with mock.patch.object(C, "CONFIG_FILE", path):
                cfg = C.Config.load()
            self.assertEqual(cfg.schema, C.SCHEMA)
            self.assertFalse(cfg.mirror_weather)

    def test_save_includes_schema(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with mock.patch.object(C, "CONFIG_DIR", root), \
                    mock.patch.object(C, "CONFIG_FILE", root / "config.json"):
                C.Config().save()
                raw = json.loads((root / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(raw["schema"], C.SCHEMA)

    def test_location_label(self):
        loc = C.Location(name="西安", admin="陕西省", country="中国")
        self.assertEqual(loc.label, "西安 · 陕西省")
        self.assertEqual(loc.full_label, "西安 · 陕西省 · 中国")
        self.assertEqual(C.Location(name="西安", admin="西安").label, "西安")


if __name__ == "__main__":
    unittest.main()
