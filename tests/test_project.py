"""项目自己的规矩，写成可执行的检查。

这些不是"功能测试"，而是把 `AGENTS.md` 里那些**用事故换来的约定**钉住：
哪条被违反，这里就会红。以前它们只是文档里的几行字，靠人记得。
"""

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "chuang"


def _py_files() -> list[Path]:
    return sorted(PKG.glob("*.py")) + sorted((ROOT / "tools").glob("*.py"))


def _dotted(node) -> str:
    """把 `a.b.c` 这样的属性链拼回字符串；拼不出来就给个空串。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    return ""


def _sources():
    for path in _py_files():
        yield path, path.read_text(encoding="utf-8")


class TestVersion(unittest.TestCase):

    def test_version_lives_in_exactly_one_place(self):
        """版本号只在 chuang/__init__.py 里（AGENTS §8）。"""
        from chuang import __version__
        for path, src in _sources():
            if path.name == "__init__.py":
                continue
            self.assertNotIn(f'"{__version__}"', src,
                             f"{path} 里又写了一遍版本号，应该只读 chuang.__version__")


class TestForbiddenApis(unittest.TestCase):
    """AGENTS §2：本机 GTK 4.6 + libadwaita 1.1.7 上没有这些 API。"""

    BANNED = ("ToolbarView", "AboutWindow", "MessageDialog", "EntryRow", "SwitchRow",
              "Adw.Dialog", "Gtk.MessageDialog")

    @staticmethod
    def _attributes(src: str) -> set[str]:
        """源码里**真的用到**的属性名（注释与文档字符串不算）。"""
        out = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Attribute):
                out.add(f"{_dotted(node.value)}.{node.attr}")
                out.add(node.attr)
        return out

    def test_no_newer_libadwaita_widgets(self):
        for path, src in _sources():
            used = self._attributes(src)
            for name in self.BANNED:
                self.assertNotIn(name, used,
                                 f"{path} 用了这台机器上没有的 {name}"
                                 "（GTK 4.6 / libadwaita 1.1.7，见 AGENTS §2）")

    def test_never_uses_widget_activate_action(self):
        """AGENTS §3.2：PyGObject 下 activate_action(name, variant) 会静默失败。"""
        for path, src in _sources():
            self.assertNotIn("activate_action", self._attributes(src),
                             f"{path} 里出现了 activate_action，应该走 window.activate()")


class TestNoThirdPartyImports(unittest.TestCase):
    """项目的卖点之一：只用标准库 + 系统里的 PyGObject / pycairo。"""

    # gi / cairo 是系统包（PyGObject、pycairo）；Xlib 是**可选**的
    # python3-xlib（只有"窗口置顶"用得上，缺了也照常跑），见 README 的依赖表。
    ALLOWED = {"gi", "cairo", "Xlib", "chuang"}

    def test_only_stdlib_and_system_bindings(self):
        for path, src in _sources():
            tree = ast.parse(src, filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level:          # 相对导入，都是自己的
                        continue
                    names = [(node.module or "").split(".")[0]]
                else:
                    continue
                for name in names:
                    if not name or name in self.ALLOWED:
                        continue
                    self.assertIn(name, sys.stdlib_module_names,
                                  f"{path} 导入了第三方库 {name}")


class TestSubprocessTimeouts(unittest.TestCase):
    """AGENTS §3.6：没超时的 subprocess 会把 GTK 主循环一起拖死。"""

    @staticmethod
    def _calls(src: str, attr: str):
        for node in ast.walk(ast.parse(src)):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == attr
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "subprocess"):
                yield node

    def test_every_run_has_a_timeout(self):
        for path, src in _sources():
            for node in self._calls(src, "run"):
                keywords = {k.arg for k in node.keywords}
                self.assertIn("timeout", keywords,
                              f"{path}:{node.lineno} 的 subprocess.run 没有 timeout")

    def test_popen_never_blocks_on_output(self):
        """Popen 只用来"开个东西就走"（安装终端、重启自己），必须把输出丢掉。"""
        for path, src in _sources():
            for node in self._calls(src, "Popen"):
                keywords = {k.arg: k.value for k in node.keywords}
                self.assertIn("stdout", keywords,
                              f"{path}:{node.lineno} 的 Popen 没把 stdout 丢掉")
                self.assertEqual(_dotted(keywords["stdout"]), "subprocess.DEVNULL",
                                 f"{path}:{node.lineno} 的 Popen 应该 stdout=DEVNULL")


class TestModuleLayout(unittest.TestCase):
    """app.py 拆分之后，各管一摊；别又长回去（见 CHANGELOG 1.1.8）。"""

    # 拆分后 app.py 从 2036 行降到 1000 上下；留一点余量，但别再长回去。
    APP_PY_LIMIT = 1100

    def test_app_py_stays_a_window(self):
        lines = (PKG / "app.py").read_text(encoding="utf-8").count("\n")
        self.assertLess(lines, self.APP_PY_LIMIT,
                        f"app.py 又长到 {lines} 行了——该拆的拆出去")

    def test_split_modules_exist(self):
        for name in ("actions.py", "dialogs.py", "diagnostics.py",
                     "update_ui.py", "wallpaper_ctl.py"):
            self.assertTrue((PKG / name).exists(), f"少了 {name}")


if __name__ == "__main__":
    unittest.main()
