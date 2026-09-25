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
    # 递归：`chuang/render/` 这样的子包也要过同样的规矩（1.2.0 拆包之后加的）
    return sorted(PKG.rglob("*.py")) + sorted((ROOT / "tools").glob("*.py"))


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
    """app.py 拆分之后，各管一摊；别又长回去（见 CHANGELOG 1.1.8 与 1.2.0）。"""

    # 拆分后 app.py 从 2036 行降到 1000 上下；留一点余量，但别再长回去。
    APP_PY_LIMIT = 1100
    # 其余任何**一个**模块也别超过这么多行。拆分不是"把大文件剪成两半"：
    # 一个 800 行的模块和一个 1600 行的模块一样难改（1.2.0 把 3235 行的
    # render.py 拆成 12 个模块，最长的两个是信息卡的排版与画法）。
    MODULE_LIMIT = 700
    # 只有"窗口本身"允许大一点：它管的是画面、心跳、动作表与生命周期这一整套。
    OVERSIZE_OK = {"app.py": "窗口本身（见 AGENTS §7）"}

    def test_app_py_stays_a_window(self):
        lines = (PKG / "app.py").read_text(encoding="utf-8").count("\n")
        self.assertLess(lines, self.APP_PY_LIMIT,
                        f"app.py 又长到 {lines} 行了——该拆的拆出去")

    def test_no_module_is_a_monolith(self):
        fat = []
        for path in _py_files():
            if path.parent != PKG:          # 只看 chuang/ 自己的模块
                continue
            if path.name in self.OVERSIZE_OK:
                continue
            lines = path.read_text(encoding="utf-8").count("\n")
            if lines > self.MODULE_LIMIT:
                fat.append(f"{path.name}({lines})")
        self.assertEqual(fat, [],
                         f"这些模块太长了（>{self.MODULE_LIMIT} 行）：{fat}"
                         "——按「一层管一件事」拆开，见 chuang/render/__init__.py")

    def test_split_modules_exist(self):
        for name in ("actions.py", "dialogs.py", "diagnostics.py",
                     "update_ui.py", "wallpaper_ctl.py",
                     # 1.2.0 从 app.py 收出来的那几摊
                     "chrome.py", "pointer.py", "devhooks.py", "topmost.py",
                     "about.py", "instance.py", "export.py"):
            self.assertTrue((PKG / name).exists(), f"少了 {name}")
        for name in ("paint.py", "state.py", "weatherfx.py", "core.py", "sky.py",
                     "ground.py", "sill.py", "ribbon.py", "card.py",
                     "cardpaint.py", "notify.py", "painter.py"):
            self.assertTrue((PKG / "render" / name).exists(),
                            f"render/ 里少了 {name}")

    def test_every_module_explains_itself(self):
        """每个模块开头都有一段"我在管什么"。

        这个项目里"为什么这么写"和代码一样重要（AGENTS.md 那些事故笔记就是这么来的）：
        一个文件如果不先说清自己管哪一摊，下一个人只能靠猜——1.2.0 拆包时每个模块
        开头都写了这一段，别让新加的模块漏掉。
        """
        missing = []
        for path in _py_files():
            if path.parent.parent != PKG and path.parent != PKG:
                continue                       # tools/ 那几个小脚本不强制
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            head = tree.body[0] if tree.body else None
            if not (isinstance(head, ast.Expr)
                    and isinstance(head.value, ast.Constant)
                    and isinstance(head.value.value, str)):
                missing.append(str(path.relative_to(ROOT)))
        self.assertEqual(missing, [], f"这些模块没有模块文档：{missing}")


class TestMainThreadHops(unittest.TestCase):
    """后台线程回主线程，只能走 `mainloop.to_main()`（**不能**用裸的 `GLib.idle_add`）。

    2026-09-25 的 CI 红在这里：GLib 的空闲源只在一件优先级更高的事都没有时才被
    调用，而 `idle_add` 的默认优先级低于帧时钟与各处定时器——机器一忙，主循环里
    永远有事可做，那个 idle 就一直轮不到。表现是"天气抓回来了却送不到画面上"
    与"菜单关掉之后焦点收不回画面"，本机用 8 个满载进程能稳定复现（见
    `chuang/mainloop.py`）。这条静态检查盯着别有人再写回去。
    """

    ALLOWED = {"mainloop.py"}          # to_main 自己住在那里

    def test_no_bare_idle_add(self):
        problems = []
        for path, src in _sources():
            if path.name in self.ALLOWED:
                continue
            if "idle_add" in TestForbiddenApis._attributes(src):
                problems.append(path.name)
        self.assertEqual(problems, [],
                         f"这些文件用了裸的 GLib.idle_add：{problems}"
                         "（应该走 mainloop.to_main，否则满载时会被饿死）")


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

    1.2.0 起还认**混入类**：`chuang/render/` 那几层就是拼出来的（`SkyPainter`
    自己几乎没有方法，全在 `SkyLayer` / `GroundLayer` / `CardPaintLayer` … 里），
    所以父类上有的名字也算数。父类只在"同一份文件里 import 进来、或者就地
    定义"时才认——不然随便哪个同名方法都能把错字遮住，这条检查就白写了。

    子类也要看：一层的画法常常由**子类**接上（`CardLayer._draw_info` 调的
    `_paint_info` 就住在 `CardPaintLayer` 里），而 `self.clock` 这种成员是
    `SkyPainter.__init__` 挂上的——两者都靠"谁继承了我"这一侧补齐。
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
        "get_focus", "get_native", "list_actions", "update_property", "notify",
    }

    @staticmethod
    def _defined_names(cls: ast.ClassDef) -> set:
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

    def _scopes(self) -> tuple[dict, dict, dict]:
        """全项目扫一遍：每个类"自己定义的名字 / 父类 / 在哪个文件"。

        顺带记下每个文件里**看得见**的名字（自己定义的与 import 进来的），
        以及"谁继承了谁"（反向的那张表）。
        """
        table: dict = {}
        visible: dict[Path, set] = {}
        children: dict[str, list] = {}
        for path, src in _sources():
            tree = ast.parse(src, filename=str(path))
            seen_names: set = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    seen_names |= {a.asname or a.name for a in node.names}
                elif isinstance(node, ast.Import):
                    seen_names |= {a.asname or a.name.split(".")[0] for a in node.names}
            for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
                seen_names.add(cls.name)
                table[cls.name] = (self._defined_names(cls),
                                   [_dotted(b) for b in cls.bases], path)
                for base in cls.bases:
                    children.setdefault(_dotted(base).split(".")[-1], []).append(
                        cls.name)
            visible[path] = seen_names
        return table, visible, children

    def _up(self, cls_name: str, table: dict, visible: dict,
            seen: set | None = None) -> set:
        """自己 + 混入进来的父类（递归）。"""
        seen = set() if seen is None else seen
        own, bases, path = table.get(cls_name, (set(), [], None))
        out = set(own)
        for base in bases:
            short = base.split(".")[-1]
            if not short or short in seen or short not in table:
                continue
            if short not in visible.get(path, set()):     # 没 import 进来，不算
                continue
            seen.add(short)
            out |= self._up(short, table, visible, seen)
        return out

    def _down(self, cls_name: str, table: dict, children: dict,
              seen: set | None = None) -> set:
        """继承了这个类的那些类（只取它们**自己**定义的名字）。

        一层的画法常由子类接上（`CardLayer._draw_info` 调的 `_paint_info` 就在
        `CardPaintLayer` 里），而 `self.clock` 那种成员是最终那个
        `SkyPainter.__init__` 挂上的——两者都在这一侧。
        """
        seen = set() if seen is None else seen
        out: set = set()
        for child in children.get(cls_name, []):
            if child in seen:
                continue
            seen.add(child)
            out |= table.get(child, (set(), [], None))[0]
            out |= self._down(child, table, children, seen)
        return out

    def _all_names(self, cls_name: str, table: dict, visible: dict,
                   children: dict) -> set:
        return (self._up(cls_name, table, visible)
                | self._down(cls_name, table, children))

    def test_every_self_call_exists(self):
        table, visible, children = self._scopes()
        problems = []
        for path, src in _sources():
            tree = ast.parse(src, filename=str(path))
            for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
                defined = self._all_names(cls.name, table, visible, children)
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


class TestWindowCollaboratorCalls(unittest.TestCase):
    """协作者（keys / pointer / infocard / chrome / wallpaper_ctl / update_ui …）
    都拿着一个窗口，通过 `win.xxx()` 调它。

    这跟"`self.xxx()` 打错一个字母"是完全同源的一种死法：**名字对不上就静默
    什么都不做**，而且往往只在某一条路上才炸（AGENTS §3.2 里那两颗按钮就是
    这么坏的）。所以这里也静态查一遍：`win.xxx(...)` 里的 xxx 必须是
    `ChuangWindow` / `ChuangApp` 上真的有的方法（或 GTK 基类给的那些）。

    只查**直接调用**（`win.toast(...)`）：`win.weather.refresh(...)` 这种经过
    某个成员的链不归这条管。
    """

    IGNORED = TestSelfAttributeCalls.IGNORED

    @staticmethod
    def _window_names() -> set:
        src = (PKG / "app.py").read_text(encoding="utf-8")
        names: set = set()
        for cls in [n for n in ast.walk(ast.parse(src))
                    if isinstance(n, ast.ClassDef)]:
            if cls.name in ("ChuangWindow", "ChuangApp"):
                names |= TestSelfAttributeCalls._defined_names(cls)
        return names

    def test_every_window_call_exists(self):
        names = self._window_names()
        self.assertIn("toast", names)          # 兜一句：别把这张表扫空了
        problems = []
        for path, src in _sources():
            for node in ast.walk(ast.parse(src, filename=str(path))):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not isinstance(func, ast.Attribute) or not func.attr:
                    continue
                value = func.value
                is_win = (
                    (isinstance(value, ast.Name) and value.id == "win")
                    or (isinstance(value, ast.Attribute) and value.attr == "win"
                        and isinstance(value.value, ast.Name)
                        and value.value.id == "self"))
                if not is_win:
                    continue
                if func.attr in names or func.attr in self.IGNORED:
                    continue
                if func.attr.startswith("__"):
                    continue
                problems.append(f"{path.name}:{node.lineno} win.{func.attr}()")
        self.assertEqual(problems, [], f"窗口上没有这些方法：{problems}")


if __name__ == "__main__":
    unittest.main()
