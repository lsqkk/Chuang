"""单实例：同一时刻只允许一扇「窗」在写壁纸。

应用本身靠 D-Bus 保单例（同名的第二个实例会被 GApplication 转发给正在跑的那
个），但"两个写入方各自往 sky-a / sky-b 里交替写"正是壁纸来回闪的根源之一
（一张是"现在"，另一张还是上次那张旧图）。所以再加一把**进程间的文件锁**兜底：
拿不到锁就把已有的实例叫到前台，然后自己退场。

锁跟着进程走：进程一退出内核就自动释放（锁文件留着也没关系）。句柄必须存在
模块级变量里——被回收就等于解锁。
"""

from __future__ import annotations

import time as _time
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import APP_ID

# 单实例锁：同一时刻只允许一个「窗」在写壁纸，避免两个进程互相覆盖
INSTANCE_LOCK = Path.home() / ".cache" / "chuang" / "instance.lock"

_HANDLE = None          # 拿到的那把锁（别让 GC 收走，收走就等于解锁）


def held() -> bool:
    """这把锁此刻是不是还在自己手上（诊断文本里要写出来）。"""
    return _HANDLE is not None


def claim() -> bool:
    """拿这把锁；拿不到就把已有实例叫到前台并返回 False。"""
    import fcntl
    global _HANDLE
    deadline = _time.monotonic() + 1.5
    while True:
        try:
            INSTANCE_LOCK.parent.mkdir(parents=True, exist_ok=True)
            handle = open(INSTANCE_LOCK, "w")
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _HANDLE = handle
            return True
        except OSError:
            # 同一个会话里已经有一扇窗：把它叫到前台，自己退场
            if poke_running():
                return False
            # 锁被"别的会话/正在退出的进程"占着：等一下再试；实在等不到就
            # 照常启动（总比登录后什么都没有强，此时本会话里确实没有第二个）
            if _time.monotonic() >= deadline:
                return True
            _time.sleep(0.15)


def poke_running() -> bool:
    """本会话里如果已经有「窗」在跑，就把它叫到前台。成功返回 True。"""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        path = "/" + APP_ID.replace(".", "/")
        bus.call_sync(APP_ID, path, "org.freedesktop.Application", "Activate",
                      GLib.Variant("(a{sv})", ({},)), None,
                      Gio.DBusCallFlags.NONE, 1500, None)
        return True
    except Exception:
        return False
