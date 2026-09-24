"""项目自己的规矩，写成可执行的检查。

这些不是"功能测试"，而是把 `AGENTS.md` 里那些**用事故换来的约定**钉住：
哪条被违反，这里就会红。以前它们只是文档里的几行字，靠人记得。
"""

import ast
import re
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


class TestActionNames(unittest.TestCase):
    """`set_toggle("info_compact")` 这种名字写错一个字母 = 点了没反应。

    1.1.9 信息卡右上角那颗收起箭头就是这么坏的：处理器找的是 info_compact，
    而注册的动作叫 infocompact——`lookup_action()` 找不到就静默返回，界面上
    看起来就是"点了没反应"（AGENTS §3.2）。这里把动作名钉死。
    """

    @staticmethod
    def _actions_src() -> str:
        return (PKG / "actions.py").read_text(encoding="utf-8")

    def _registered(self) -> set:
        src = self._actions_src()
        consts = dict(re.findall(r'^([A-Z_]+) = "([a-z]+)"', src, re.M))
        names = set(re.findall(r'(?:add|add_toggle|add_radio)\("([a-z]+)"', src))
        names |= {consts[c] for c in
                  re.findall(r'(?:add|add_toggle|add_radio)\(([A-Z_]+),', src)
                  if c in consts}
        return names

    def test_every_toggle_name_is_registered(self):
        """只看**真的调用**（注释和文档里提到名字不算）。"""
        registered = self._registered()
        consts = dict(re.findall(r'^([A-Z_]+) = "([a-z]+)"',
                                 self._actions_src(), re.M))
        self.assertIn("info", registered)
        problems = []
        for path, src in _sources():
            for node in ast.walk(ast.parse(src, filename=str(path))):
                if not (isinstance(node, ast.Call) and node.args):
                    continue
                func = node.func
                if not (isinstance(func, ast.Attribute)
                        and func.attr in ("set_toggle", "action_state")):
                    continue
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    resolved = arg.value
                elif isinstance(arg, ast.Attribute):
                    resolved = consts.get(arg.attr, "")
                else:
                    continue
                if resolved and resolved not in registered:
                    problems.append(f"{path.name}:{node.lineno} {resolved}")
        self.assertEqual(problems, [], f"这些动作名没有注册过：{problems}")


class TestSelfAttributeCalls(unittest.TestCase):
    """**类里自己调自己的方法，名字必须真的存在。**

    1.1.8 的「安装更新」按钮坏在这上面：调用写的是 `self._verify_deb(...)`，
    而方法叫 `verify_deb`（多打了一个下划线）。这只会在"下载完了、要校验"的
    那一刻炸，而且被 except 兜成了"下载失败"——用户重下多少次都没用，
    测试（只测 `verify_deb()` 本身）也一直绿着。

    静态查一遍：类体里出现的 `self.xxx(...)`，xxx 必须在同一个类里有定义
    （def / 赋值 / 注解 / property）。继承来的、setattr 出来的会漏网，
    但那两类本来就不靠这条兜——漏网也只能放过，不会误报。
    """

    IGNORED = {
        # Gtk.Window / Gtk.Widget / Adw.ApplicationWindow 这些基类给的（静态看不出来），
        # 以及运行时才挂上的。真配错了这些名字，一开窗冒烟测试就会炸，不用静态兜。
        "app", "get_width", "get_height", "get_visible", "is_active",
        "is_fullscreen", "fullscreen", "unfullscreen", "set_visible", "present",
        "close", "destroy", "connect", "add_controller", "set_content", "set_child",
        "set_title",
        "set_default_size", "set_size_request", "add_css_class", "get_surface",
        "lookup_action", "add_action", "activate", "props", "run", "quit",
    }

    def _defined_names(self, cls: ast.ClassDef) -> set:
        names = set()
        for node in ast.walk(cls):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if (isinstance(target, ast.Attribute)
                            and isinstance(target.value, ast.Name)
                            and target.value.id == "self"):
                        names.add(target.attr)
            elif isinstance(node, ast.AnnAssign):
                target = node.target
                if (isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "self"):
                    names.add(target.attr)
        return names

    def test_every_self_call_exists(self):
        problems = []
        for path, src in _sources():
            tree = ast.parse(src, filename=str(path))
            for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
                defined = self._defined_names(cls)
                for node in ast.walk(cls):
                    if not isinstance(node, ast.Call):
                        continue
                    func = node.func
                    if not (isinstance(func, ast.Attribute)
                            and isinstance(func.value, ast.Name)
                            and func.value.id == "self"):
                        continue
                    if func.attr in defined or func.attr in self.IGNORED:
                        continue
                    if func.attr.startswith("__"):
                        continue
                    problems.append(f"{path.name}:{node.lineno} {cls.name}.self.{func.attr}()")
        self.assertEqual(problems, [], f"调了不存在的方法：{problems}")


if __name__ == "__main__":
    unittest.main()
