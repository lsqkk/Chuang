"""「关于」那一组：关于窗、作者主页、问题反馈。

从 `app.py` 里搬出来的——窗口那边只留一句转发（`win._act_about`）。

问题反馈那条路刻意**预填**了版本号与系统环境（`diagnostics.py` 生成的纯文本，
只有版本、系统、托盘、壁纸状态这些），**不含位置、也不含任何个人数据**，
用户看得见、也能自己删。打开浏览器一律走 `win._open_url()`，它负责在打不开
的时候把地址原样说出来（那时用户还能自己复制）。
"""

from __future__ import annotations

from urllib.parse import quote, urlencode

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from . import __version__
from . import update as upmod


def show_about(win) -> None:
    """关于窗：版本（有新版本时一并写出来）+ 这段话为什么是这样。"""
    version_text = __version__
    rel = win.updater.available_release
    if rel is not None:
        version_text = f"{__version__}（有新版本 {rel.tag}）"
    about = Gtk.AboutDialog(
        transient_for=win, modal=True,
        program_name="窗 · Chuang", version=version_text,
        logo_icon_name="chuang",
        comments="把你头顶此刻真实的天空，搬到桌面的一扇窗里。\n\n"
                 "太阳、月亮、星星的位置由本地天文算法计算，"
                 "云、雨、雪来自 Open-Meteo 的真实天气。\n"
                 "没有任何内容离开这台电脑。",
        website=upmod.AUTHOR_URL,
        website_label=f"{upmod.AUTHOR} · github.com/lsqkk",
        authors=[f"{upmod.AUTHOR}（lsqkk）"],
        copyright="© 2026 蓝色奇夸克",
        license_type=Gtk.License.MIT_X11)
    # 「有问题去这里说」的直达入口（GTK 4.6 才有的属性，取不到就算了）
    try:
        about.set_issue_url(upmod.ISSUES_URL)
    except Exception:
        pass
    about.present()


def open_author(win) -> None:
    win._open_url(upmod.AUTHOR_URL, "已经在浏览器里打开了作者的主页")


def report_issue(win) -> None:
    """打开"新建 issue"页并预填环境：用户只需要写"发生了什么"那两句。"""
    title = f"[Bug] {__version__} · "
    body = (
        "### 发生了什么\n\n（请把这句换成你看到的现象）\n\n"
        "### 怎么复现\n\n1. \n2. \n\n"
        "### 环境（自动填好，可删）\n\n" + win._diagnostics() + "\n"
    )
    url = upmod.NEW_ISSUE_URL + "?" + urlencode(
        {"title": title, "body": body}, quote_via=quote)
    win._open_url(url, "已经在浏览器里打开反馈页；把上面两句写清楚就好")
