"""自动更新那条链路：装之前先把 .deb 校验一遍，以及"下载 → 校验 → 交出去"整条路。

这一条是"拿 root 装一个从网上拿来的包"的整个流程里唯一的完整性证据，
所以既要测"对的包放行"，也要测"错的包拦住、没有校验文件也拦住"。

还要真的走一遍 `act_install` / `act_download` 那两个后台线程：**1.1.8 的
"安装更新"按钮就坏在这条路上**——里面写的是 `self._verify_deb(...)`，而方法
其实叫 `verify_deb`（多了一个下划线），于是包下完了、校验那一步抛
AttributeError，还被 catch 成"下载失败"，用户重下多少次都没用。只测
`verify_deb()` 自己是发现不了的：得有人真的调一遍。

测试用 `file://` 当 Release 地址，完全不联网；不导入 GTK 的环境里自动跳过。
"""

import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import quote

try:
    from chuang import update as upmod
    from chuang import update_ui as updui
    from chuang.update_ui import UpdateController   # 需要 gi / GTK4 / libadwaita
    IMPORT_ERROR = None
except Exception as exc:                      # pragma: no cover - 看环境
    IMPORT_ERROR = exc


def _file_url(path: Path) -> str:
    return "file://" + quote(str(path))


@unittest.skipIf(IMPORT_ERROR is not None, f"没有 GTK4/libadwaita：{IMPORT_ERROR}")
class TestVerifyDeb(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.deb = root / "chuang_1.1.8_all.deb"
        self.deb.write_bytes(b"this stands for a .deb")
        digest = upmod.sha256_file(self.deb)
        self.sums = root / "SHA256SUMS"
        self.sums.write_text(
            f"{digest}  chuang_1.1.8_all.deb\n"
            "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef  other.deb\n",
            encoding="utf-8")

    def _release(self, sums: Path | None):
        return upmod.Release(tag="v1.1.8", deb_name=self.deb.name,
                             deb_url=_file_url(self.deb),
                             sums_url=_file_url(sums) if sums else "")

    def test_accepts_matching_digest(self):
        ok, why = UpdateController.verify_deb(None, self._release(self.sums), self.deb)
        self.assertTrue(ok, why)
        self.assertEqual(why, "")

    def test_rejects_tampered_package(self):
        self.deb.write_bytes(b"this stands for a TAMPERED .deb")
        ok, why = UpdateController.verify_deb(None, self._release(self.sums), self.deb)
        self.assertFalse(ok)
        self.assertIn("校验不通过", why)
        self.assertIn(upmod.sha256_file(self.deb), why)      # 两个摘要都要摆出来

    def test_rejects_when_no_sums_asset(self):
        ok, why = UpdateController.verify_deb(None, self._release(None), self.deb)
        self.assertFalse(ok)
        self.assertIn("SHA256SUMS", why)

    def test_rejects_when_deb_not_listed(self):
        self.sums.write_text("0123456789abcdef0123456789abcdef0123456789abcdef"
                             "0123456789abcdef  something-else.deb\n", encoding="utf-8")
        ok, why = UpdateController.verify_deb(None, self._release(self.sums), self.deb)
        self.assertFalse(ok)
        self.assertIn("没有", why)

    def test_unreachable_sums_is_a_refusal(self):
        ok, why = UpdateController.verify_deb(
            None, self._release(Path(self.tmp.name) / "nope"), self.deb)
        self.assertFalse(ok)
        self.assertIn("失败", why)


class _StubConfig:
    """够用的假配置：一个字段都不写盘（真 Config.save() 会写 ~/.config/chuang）。"""

    update_check = False
    skipped_version = ""
    last_update_check = 0.0

    def save(self):
        pass


class _StubWindow:
    """UpdateController 只跟窗口要这几件东西（见 update_ui.py 开头那段说明）。"""

    def __init__(self):
        self.config = _StubConfig()
        self.toasts = []
        self.details = []

    def toast(self, text, seconds=3.0, icon="info"):
        self.toasts.append(text)

    def toast_detailed(self, text, seconds, detail, icon="warn"):
        self.details.append((text, detail))

    def refresh_menu(self):
        pass

    def _open_url(self, url, ok_msg):
        self.toasts.append(ok_msg)


@unittest.skipIf(IMPORT_ERROR is not None, f"没有 GTK4/libadwaita：{IMPORT_ERROR}")
class TestInstallFlow(unittest.TestCase):
    """真的跑一遍后台线程：下载 → 校验 → 交给安装器。

    这个类就是照着 1.1.8 那次事故写的（`self._verify_deb` 根本不存在）。
    用 `file://` 当下载地址，所以不联网；`GLib.idle_add` 换成"当场就调用"，
    于是不用起主循环也能看到回调结果。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.deb = root / "chuang_1.1.9_all.deb"
        self.deb.write_bytes(b"pretend this is a .deb payload")
        digest = upmod.sha256_file(self.deb)
        self.sums = root / "SHA256SUMS"
        self.sums.write_text(f"{digest}  {self.deb.name}\n", encoding="utf-8")

        self.win = _StubWindow()
        self.ctl = UpdateController(self.win)
        self.ctl._installer_launcher = lambda path: None   # 别真去开终端
        self.ctl.available_release = self._release(self.sums)
        # 下载落到临时目录里，别碰 ~/.cache/chuang
        patch = mock.patch.object(updui.wallmod, "CACHE", root / "cache")
        patch.start()
        self.addCleanup(patch.stop)
        # 后台线程里那句 GLib.idle_add：当场调用，省掉主循环
        patch = mock.patch.object(updui.GLib, "idle_add",
                                  lambda func, *args: (func(*args), 0)[1])
        patch.start()
        self.addCleanup(patch.stop)

    def _release(self, sums):
        return upmod.Release(tag="v1.1.9", deb_name=self.deb.name,
                             deb_url=_file_url(self.deb),
                             sums_url=_file_url(sums) if sums else "")

    def _join(self, name: str):
        for thread in threading.enumerate():
            if thread.name == name:
                thread.join(timeout=20)

    def _titles(self):
        return [title for title, _detail in self.win.details]

    def _detail_text(self):
        return "\n".join(detail for _title, detail in self.win.details)

    def test_install_downloads_verifies_and_hands_off(self):
        self.ctl.act_install()
        self._join("chuang-install")
        self.assertIn("没有可用的安装方式", self._titles())   # 走到了 _install_ready
        self.assertNotIn("下载安装包失败", self._titles())
        self.assertFalse(self.ctl.installing)
        landed = Path(self.tmp.name) / "cache" / "updates" / self.deb.name
        self.assertTrue(landed.exists(), "安装包没有落到缓存目录里")
        self.assertEqual(landed.read_bytes(), self.deb.read_bytes())
        self.assertIn(str(landed), self._detail_text())

    def test_install_refuses_a_tampered_package(self):
        self.sums.write_text("0" * 64 + f"  {self.deb.name}\n", encoding="utf-8")
        self.ctl.available_release = self._release(self.sums)
        self.ctl.act_install()
        self._join("chuang-install")
        self.assertIn("没有安装：安装包没通过校验", self._titles())
        self.assertNotIn("没有可用的安装方式", self._titles())

    def test_install_reports_a_real_download_failure(self):
        rel = upmod.Release(tag="v1.1.9", deb_name=self.deb.name,
                            deb_url=_file_url(Path(self.tmp.name) / "不存在.deb"),
                            sums_url=_file_url(self.sums))
        self.ctl.available_release = rel
        self.ctl.act_install()
        self._join("chuang-install")
        self.assertIn("下载安装包失败", self._titles())
        self.assertFalse(self.ctl.installing)

    def test_download_only_path_also_verifies(self):
        """「只下载 .deb」那条路同样要过校验——两条路都得叫得动 verify_deb。"""
        downloads = Path(self.tmp.name) / "下载"
        self.ctl._download_dir = lambda: downloads
        self.ctl.act_download()
        self._join("chuang-deb")
        self.assertIn("安装包已经下好并通过校验", self._titles())
        self.assertTrue((downloads / self.deb.name).exists())


if __name__ == "__main__":
    unittest.main()
