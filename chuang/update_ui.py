"""检查更新这条链路：问版本号 → 下载 .deb → 校验 → 用 sudo 装 → 重启。

从 `app.py` 里搬出来的。用户可见的反馈一律走 `win.toast()` / `win.toast_detailed()`，
打开浏览器走 `win._open_url()`，重画菜单走 `win.refresh_menu()`——所以这个文件
不碰窗口内部，改动它不会牵动界面。

两条不能松的线（都是被现实咬过之后加的）：
1. **装之前一定要校验**：拿 Release 上的 `SHA256SUMS` 比对刚下载的 `.deb`，
   对不上就不装、没有校验文件也不装（把摘要摆出来让用户自己决定）。
2. **网络与哈希都在后台线程里做**，回主线程只走 `GLib.idle_add`。
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import threading
import time as _time
import urllib.request
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib  # noqa: E402

from . import __version__, update as upmod
from . import wallpaper as wallmod
from .dialogs import ChoiceDialog


class UpdateController:
    """「检查更新」的管家：状态、下载、校验、安装、重启。"""

    def __init__(self, win) -> None:
        self.win = win
        self.available_release = None
        self.checking = False
        self.installing = False
        self._install_target = ()
        self._install_deadline = 0.0

    @property
    def config(self):
        return self.win.config

    def refresh_menu(self) -> None:
        """有新版本入口之类：菜单内容变了。"""
        self.win.refresh_menu()

    def _open_url(self, url: str, ok_msg: str) -> None:
        self.win._open_url(url, ok_msg)

    def _toast(self, text: str, seconds: float = 3.0) -> None:
        self.win.toast(text, seconds)

    def _toast_detailed(self, text: str, seconds: float, detail: str) -> None:
        self.win.toast_detailed(text, seconds, detail)


    def maybe_auto_check(self) -> bool:
        if not self.config.update_check:
            return False
        if _time.time() - float(self.config.last_update_check or 0) < upmod.CHECK_INTERVAL:
            return False
        self.check(manual=False)
        return False

    def check(self, manual: bool = True) -> None:
        if self.checking:
            if manual:
                self._toast("正在检查更新…", 2.0)
            return
        self.checking = True
        if manual:
            self._toast("正在检查更新…", 2.0)

        def worker():
            rel = upmod.fetch_latest()
            GLib.idle_add(self._result, manual, rel)

        threading.Thread(target=worker, daemon=True, name="chuang-update").start()

    def _result(self, manual: bool, rel) -> bool:
        self.checking = False
        if rel is None:
            # 只在真的拿到结果时才记时间戳，否则断网一次就要等一整天才会再问
            if manual:
                self._toast("检查更新失败：网络或 GitHub 接口不可用，稍后会再试", 5.0)
            return False
        self.config.last_update_check = _time.time()
        self.config.save()
        newer = upmod.is_newer(rel.version, upmod.parse_version(__version__))
        if newer and rel.tag != self.config.skipped_version:
            self.available_release = rel
            self.refresh_menu()
            self._toast(f"有新版本 {rel.tag}：菜单最上面可以打开发布页", 8.0)
        else:
            if self.available_release is not None:
                self.available_release = None
                self.refresh_menu()
            if manual:
                self._toast(f"已经是最新的 {__version__}", 4.0)
        return False

    def act_check(self, *_):
        self.check(manual=True)

    def act_auto(self, want: bool):
        self.config.update_check = want
        self.config.save()
        self._toast("会自动检查更新（一天一次，只问版本号）" if want
                   else "不再自动检查更新", 4.0)
        if want:
            self.check(manual=False)

    def act_open_releases(self, *_):
        rel = self.available_release
        url = rel.url if rel is not None else upmod.RELEASES_URL
        self._open_url(url, "已经在浏览器里打开了发布页")

    def act_skip(self, *_):
        rel = self.available_release
        if rel is None:
            return
        self.config.skipped_version = rel.tag
        self.config.save()
        self.available_release = None
        self.refresh_menu()
        self._toast(f"已跳过 {rel.tag}，下一个版本再提醒", 4.0)

    def act_download(self, *_):
        rel = self.available_release
        if rel is None or not rel.deb_url:
            self._toast("这个版本没有提供 .deb 安装包", 4.0)
            return
        target = self._download_dir() / (Path(rel.deb_name).name or "chuang-update.deb")
        self._toast(f"正在下载 {rel.deb_name}…", 3.0)

        def worker():
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                req = urllib.request.Request(rel.deb_url,
                                             headers={"User-Agent": upmod.UA})
                with urllib.request.urlopen(req, timeout=60) as resp, \
                        open(target, "wb") as fh:
                    shutil.copyfileobj(resp, fh)
            except Exception as exc:
                GLib.idle_add(self._download_done, False, str(exc), "")
                return
            # 下载和校验分开兜：校验自己出错时，包其实已经躺在磁盘上了，
            # 这时候报"下载失败"会把人指去重下（1.1.8 就是这么把一个
            # AttributeError 说成"下载失败"的，用户重下多少次都没用）。
            try:
                ok, why = self.verify_deb(rel, target)
            except Exception as exc:                  # noqa: BLE001
                ok, why = False, f"校验这一步自己出错了：{type(exc).__name__}: {exc}"
            GLib.idle_add(self._download_done, True, str(target), "" if ok else why)

        threading.Thread(target=worker, daemon=True, name="chuang-deb").start()

    def _download_done(self, ok: bool, info: str, warn: str = "") -> bool:
        if ok:
            tail = f"\n\n【没通过校验，先别急着装】\n{warn}" if warn else ""
            self._toast_detailed(
                "安装包已经下好并通过校验" if not warn else "安装包已经下好了",
                20.0 if warn else 14.0,
                f"安装包：{info}\n\n手动安装（复制到终端里跑）：\n"
                f"    sudo apt-get install -y -- \"{info}\"\n\n"
                "或者在菜单里点「下载并安装」，「窗」会自己开一个终端帮你装。"
                + tail)
        else:
            self._toast_detailed("下载失败", 8.0,
                                f"下载失败：{info}\n\n发布页：{upmod.RELEASES_URL}")
        return False

    # ------------------------------------------------------------------
    # 下载并直接安装新版本
    # ------------------------------------------------------------------
    def act_install(self, *_):
        """下载新版本的 .deb，然后开一个终端用 sudo 装好（会弹密码）。"""
        rel = self.available_release
        if rel is None or not rel.deb_url:
            self._toast("没找到可下载的安装包，我给你打开发布页", 5.0)
            self.act_open_releases()
            return
        if self.installing:
            self._toast("上一次安装还没结束，稍等一下…", 3.0)
            return
        self.installing = True
        # 远端文件名先过一遍 basename：别让 `../` 之类的名字决定往哪写
        target = wallmod.CACHE / "updates" / (Path(rel.deb_name).name
                                              or "chuang-update.deb")
        self._toast(f"正在下载 {rel.deb_name}…（大约几 MB）", 5.0)

        def worker():
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                req = urllib.request.Request(rel.deb_url,
                                             headers={"User-Agent": upmod.UA})
                with urllib.request.urlopen(req, timeout=180) as resp, \
                        open(target, "wb") as fh:
                    shutil.copyfileobj(resp, fh)
            except Exception as exc:
                GLib.idle_add(self._install_download_failed, rel, str(exc))
                return
            try:
                ok, why = self.verify_deb(rel, target)
            except Exception as exc:                  # noqa: BLE001
                ok, why = False, f"校验这一步自己出错了：{type(exc).__name__}: {exc}"
            if not ok:
                GLib.idle_add(self._install_verify_failed, rel, str(target), why)
                return
            GLib.idle_add(self._install_ready, rel, str(target))

        threading.Thread(target=worker, daemon=True, name="chuang-install").start()

    def verify_deb(self, rel, path: Path) -> tuple[bool, str]:
        """用 Release 上的 SHA256SUMS 核对刚下载的 .deb（在后台线程里跑）。

        这一步几乎没有成本，却是整条链路上唯一"内容有没有被动过"的证据：
        HTTPS 只挡住了传输层，而我们要拿这个二进制去按 root 装。所以
        **对不上就不装；压根没有校验文件也不装**（把两个摘要摆出来，
        让用户自己决定要不要手动装）。
        """
        if not rel.sums_url:
            return False, "这次发布里没有 SHA256SUMS 校验文件，没法核对这个包。"
        try:
            req = urllib.request.Request(rel.sums_url,
                                         headers={"User-Agent": upmod.UA})
            with urllib.request.urlopen(req, timeout=30) as resp:
                table = upmod.parse_sha256sums(resp.read().decode("utf-8", "replace"))
        except Exception as exc:
            return False, f"下载 SHA256SUMS 失败：{exc}"
        want = table.get(Path(rel.deb_name).name)
        if not want:
            return False, f"SHA256SUMS 里没有 {rel.deb_name} 这一项，没法核对这个包。"
        got = upmod.sha256_file(path)
        if got != want:
            return False, ("校验不通过：下载到的文件与发布页上的摘要对不上。\n"
                           f"    期望 {want}\n    实际 {got}")
        return True, ""

    def _install_verify_failed(self, rel, path: str, why: str) -> bool:
        self.installing = False
        self._toast_detailed("没有安装：安装包没通过校验", 20.0,
                            f"{why}\n\n安装包已经下载到：\n    {path}\n\n"
                            "如果你确认这个文件没问题，可以自己手动装：\n"
                            f"    sudo apt-get install -y -- \"{path}\"\n\n"
                            f"发布页：{upmod.RELEASES_URL}")
        return False

    def _install_download_failed(self, rel, why: str) -> bool:
        self.installing = False
        self._toast_detailed("下载安装包失败", 9.0,
                            f"下载 {rel.tag} 的安装包失败：{why}\n\n"
                            f"可以到发布页手动下载：{upmod.RELEASES_URL}")
        return False

    def _install_ready(self, rel, path: str) -> bool:
        launcher = self._installer_launcher(path)
        if launcher is None:
            self.installing = False
            self._toast_detailed(
                "没有可用的安装方式", 12.0,
                "没找到终端程序，也没法弹授权框。\n\n"
                f"安装包已经下载到：\n    {path}\n\n"
                "在终端里跑这一行就装好了：\n"
                f"    sudo apt-get install -y -- \"{path}\"")
            return False
        try:
            subprocess.Popen(launcher, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except Exception as exc:
            self.installing = False
            self._toast_detailed("打不开安装终端", 9.0,
                                f"启动安装程序失败：{exc}\n\n"
                                f"安装包在：{path}\n"
                                f"手动安装：sudo apt-get install -y -- \"{path}\"")
            return False
        self._toast(f"已经开了个终端在装 {rel.tag}，输入 sudo 密码就好", 8.0)
        self._watch_install(rel)
        return False

    @staticmethod
    def _installer_launcher(path: str):
        """怎么装：优先开终端跑 sudo（看得见过程、能输密码），否则返回 None。"""
        script = (
            "echo '窗 · Chuang —— 正在安装新版本'\n"
            "echo\n"
            f"sudo apt-get install -y -- {shlex.quote(path)}\n"
            "rc=$?\n"
            "echo\n"
            "if [ $rc -eq 0 ]; then\n"
            "  echo '✓ 安装完成。回到「窗」的窗口，它会问你要不要重启。'\n"
            "else\n"
            "  echo \"× 安装失败（退出码 $rc），上面的输出就是原因。\"\n"
            "fi\n"
            "echo\n"
            "printf '按回车关闭这个窗口… '\n"
            "read _\n")
        term = shutil.which("gnome-terminal")
        if term:
            return [term, "--title=安装「窗」更新", "--", "bash", "-c", script]
        term = shutil.which("x-terminal-emulator") or shutil.which("xterm")
        if term:
            return [term, "-e", "bash", "-c", script]
        return None

    def _watch_install(self, rel) -> None:
        """盯着 dpkg 里的版本号：装好了就提示重启。"""
        self._install_target = upmod.parse_version(rel.tag)
        self._install_deadline = _time.monotonic() + 240
        GLib.timeout_add(2000, self._poll_install)

    def _poll_install(self) -> bool:
        ver = upmod.installed_deb_version()
        parsed = upmod.parse_version(ver)
        if ver and parsed and not upmod.is_newer(self._install_target, parsed):
            self.installing = False
            self.available_release = None
            self.refresh_menu()
            self._toast(f"已经装好 {ver} 了", 8.0)
            dialog = ChoiceDialog(
                self.win, "新版本已经装好",
                f"现在是 {ver}。重启「窗」就能用上新版本（当前的窗口会关掉，"
                "壁纸最多停一两秒就接上）。",
                [("稍后", "later", False), ("现在重启", "restart", True)])
            dialog.on_choice = self._on_restart_answer
            dialog.present()
            return False
        if _time.monotonic() > self._install_deadline:
            # 大概率是用户在终端里放弃了；安静收场，别再打扰
            self.installing = False
            return False
        return True

    def _on_restart_answer(self, value: str):
        if value == "restart":
            self.restart_app()

    def act_restart(self, *_):
        self.confirm_restart()

    def confirm_restart(self):
        dialog = ChoiceDialog(self.win, "重启「窗」？",
                              "窗口会关掉再自动打开，壁纸最多停一两秒。",
                              [("取消", "later", False), ("现在重启", "restart", True)])
        dialog.on_choice = self._on_restart_answer
        dialog.present()

    def restart_app(self):
        """退出自己，并在自己真正退出之后再拉起新的那个进程。

        必须等旧进程退出：单实例锁（flock）还握在手上，抢在它前面启动
        会被当成"第二个实例"而退场。
        """
        argv = self.win.app.installed_launcher()
        wait = f"while kill -0 {os.getpid()} 2>/dev/null; do sleep 0.2; done; "
        script = wait + "exec " + " ".join(shlex.quote(part) for part in argv)
        try:
            subprocess.Popen(["setsid", "sh", "-c", script],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except Exception as exc:
            self._toast_detailed("自动重启没成功", 8.0,
                                f"启动新进程失败：{exc}\n"
                                "手动打开一次「窗」就好（终端里敲 chuang）。")
            return
        self.config.save()
        self.win.request_quit()

    @staticmethod
    def _download_dir():
        try:
            path = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
        except Exception:
            path = None
        return Path(path or Path.home() / "下载") if path else (Path.home() / "Downloads")
