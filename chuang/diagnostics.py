"""诊断文本：给 issue 用的环境信息，以及"壁纸/时间到底怎么了"的现场报告。

这两段文字是要**整段复制进 issue** 的，所以这里只做一件事：把事实排成文字。
事实由调用方（窗口）抓成 `Facts` 传进来——于是排版这回事只有一处定义，
而且不依赖 GTK，能在没有显示器的环境里直接测（`tests/test_diagnostics.py`）。
"""

from __future__ import annotations

import os
import time as _t
from dataclasses import dataclass, field
from pathlib import Path


def other_chuang_processes() -> list:
    """除了自己，系统里还有哪些「窗」进程（查"是不是有残留进程"）。"""
    out = []
    me = os.getpid()
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return out
    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == me:
            continue
        try:
            argv = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        argv = [a.decode("utf-8", "replace") for a in argv if a]
        # 只看真正的启动命令（python3 /usr/bin/chuang、chuang-gui…），
        # 别把 "gsettings set … sky-a.png" 这种子进程也算进来
        if any(os.path.basename(a) in ("chuang", "chuang-gui") for a in argv[:2]):
            out.append((pid, " ".join(argv[:3])))
    return out


@dataclass
class Facts:
    """一份诊断要用到的事实（窗口在调用前抓一次，抓完就与界面无关了）。"""

    version: str = ""
    pid: int = 0
    instance_held: bool = False               # 单实例锁还在不在自己手上
    other_processes: list = field(default_factory=list)   # [(pid, 命令)]
    installed_deb: str = ""                   # dpkg 里装的版本
    tray_available: bool = False
    config: object = None                     # config.Config

    # 壁纸这一摊
    now: float = 0.0
    shown_uri: str = ""
    shown_slot: int = -1
    slot_mtimes: tuple = (0.0, 0.0)
    slot_names: tuple = ("sky-a.png", "sky-b.png")
    stale_slot: int | None = None             # 桌面那张比另一张旧时，该顶上来的槽位
    last_wallpaper_at: float = 0.0            # 最近一次尝试换图的时刻（0 = 还没有过）
    last_wallpaper_ok: bool | None = None
    last_wallpaper_msg: str = ""
    last_flip_at: float = 0.0                 # 最近一次真的换了文件名（URI）
    flip_every: float = 300.0
    tick_errors: int = 0
    last_tick_error: str = ""

    # ---- 排版的零件 ---------------------------------------------------
    @property
    def slot(self) -> str:
        """桌面此刻挂着的槽位文件名（挂的不是我们的图就返回空串）。"""
        if 0 <= self.shown_slot < len(self.slot_names):
            return self.slot_names[self.shown_slot]
        return ""

    def other_slot_name(self) -> str:
        if 0 <= self.shown_slot < len(self.slot_names):
            return self.slot_names[1 - self.shown_slot]
        return ""


def environment_report(f: Facts) -> str:
    """给 issue 用的一小段环境信息（纯本地读取，不外发）。"""
    lines = [f"- 版本：{f.version}"]
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        lines.append(f"- GTK：{Gtk.get_major_version()}.{Gtk.get_minor_version()}."
                     f"{Gtk.get_micro_version()}")
        info = gi.Repository.get_default().require("Adw", "1")
        lines.append(f"- libadwaita：{info.get_version()}")
    except Exception:
        pass
    lines.append(f"- 会话：{os.environ.get('XDG_SESSION_TYPE', '?')} / "
                 f"{os.environ.get('XDG_CURRENT_DESKTOP', '?')}")
    try:
        text = Path("/etc/os-release").read_text(encoding="utf-8")
        pretty = next((l.split("=", 1)[1].strip().strip('"')
                       for l in text.splitlines()
                       if l.startswith("PRETTY_NAME=")), "")
        if pretty:
            lines.append(f"- 系统：{pretty}")
    except OSError:
        pass
    wallpaper_auto = bool(getattr(f.config, "wallpaper_auto", False))
    lines.append(f"- 托盘：{'可用' if f.tray_available else '不可用'}"
                 f"；壁纸跟随：{'开' if wallpaper_auto else '关'}")
    return "\n".join(lines)


def wallpaper_report(f: Facts) -> str:
    """「壁纸诊断…」里那段可以整段复制的文字。

    这类毛病是"有时候"发生的，光靠转述很难查；发生的那一刻点一下这里，
    把内容贴出来就够了。所以这里把"谁持有锁、桌面挂着哪张、心跳兜了几次"
    一次摊开。
    """
    now = f.now or _t.time()
    lines = [f"窗 · Chuang {f.version} 壁纸诊断",
             _t.strftime("%Y-%m-%d %H:%M:%S", _t.localtime(now)), "─" * 34]
    lines.append(f"本进程 pid={f.pid}，单实例锁："
                 f"{'持有' if f.instance_held else '未持有'}")
    lines.append(f"系统里其它「窗」进程：{len(f.other_processes)} 个"
                 + ("（" + "; ".join(f"{p}: {c}" for p, c in f.other_processes) + "）"
                    if f.other_processes else ""))
    lines.append(f"代码版本 {f.version}；dpkg 里装的是 "
                 f"{f.installed_deb or '（不是 .deb 安装）'}")
    interval = getattr(f.config, "wallpaper_interval", "?")
    wallpaper_auto = bool(getattr(f.config, "wallpaper_auto", False))
    lines.append(f"壁纸跟随：{'开' if wallpaper_auto else '关'}"
                 f"（间隔 {interval} 秒）；"
                 f"托盘：{'可用' if f.tray_available else '不可用'}")
    lines.append("")

    if f.shown_slot < 0:
        lines.append(f"桌面此刻挂的不是「窗」画的图：{f.shown_uri or '（读不到）'}")
    else:
        lines.append(f"桌面此刻挂的是：{f.slot}"
                     f"（{now - f.slot_mtimes[f.shown_slot]:.1f} 秒前写的）")
        lines.append(f"另一张 {f.other_slot_name()}："
                     f"{now - f.slot_mtimes[1 - f.shown_slot]:.1f} 秒前写的")
        if f.stale_slot is None:
            lines.append("桌面显示的是不是最新那张：是")
        else:
            lines.append(f"桌面显示的是不是最新那张：**不是**（应该顶上 "
                         f"{f.slot_names[f.stale_slot]}）")
    lines.append("")
    if f.last_wallpaper_at:
        lines.append(f"最近一次换图：{now - f.last_wallpaper_at:.0f} 秒前 → "
                     f"{'成功' if f.last_wallpaper_ok else '失败'}"
                     f"（{f.last_wallpaper_msg}）")
    else:
        lines.append("最近一次换图：本次启动以来还没有过")
    lines.append(f"上次真正换过文件名（URI）：{now - f.last_flip_at:.0f} 秒前"
                 f"（常态是就地更新正在显示的那张，每 {int(f.flip_every)} 秒兜底换一次）")
    lines.append(f"心跳：本进程共兜住 {f.tick_errors} 次异常"
                 + (f"；最近一次：{f.last_tick_error}" if f.last_tick_error else ""))
    lines.append("")
    lines.append("（如果上面写着「桌面显示的不是最新那张」，那就是桌面没接受新图；"
                 "如果「最近一次换图」停在很久以前，说明心跳停了。）")
    return "\n".join(lines)
