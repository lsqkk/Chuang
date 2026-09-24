"""后台线程 → 主线程的那一跳：**优先级必须高于帧时钟**，否则满载时会饿死。

后台线程里算完的东西要回到主线程（GTK 只能在主线程碰），惯例是
`GLib.idle_add(cb)`。问题在 GLib 的规矩：**空闲源只在一件优先级更高的事都没有
时才被调用**，而 `idle_add` 的默认优先级是 `G_PRIORITY_DEFAULT_IDLE`(200)——
帧时钟重绘（约 16 ms）、心跳（25 ms）、各处定时器全排在它前面。机器一忙，主循环
里永远有事可做，那个 idle 就一直轮不到：

* 天气抓回来了却送不到画面上（窗口写着"未联网"，其实数据早就在线程里躺着）；
* 菜单弹层关掉之后，焦点永远收不回画面（那两个都是 `idle_add`）。

2026-09-25 的 CI 就是这么红的，本机用 8 个满载进程也能稳定复现
（`tests/gui_smoke.py` 里的天气与焦点两条探针同时失败）。

所以统一走这里的 `to_main()`：同样是 idle，只把优先级提到 `G_PRIORITY_DEFAULT`，
让出去的只有真正的"高优先级"源，不再被定时器与重绘挡住。
**只支持位置参数**——PyGObject 的 `idle_add` 会把关键字参数当源选项吃掉
（`cb(a, b=2, priority=...)` 里的 `b=2` 根本传不到回调里）。
"""

from __future__ import annotations

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib  # noqa: E402


def to_main(func, *args):
    """把 `func(*args)` 排到主线程去跑，返回 GLib 的 source id。"""
    return GLib.idle_add(func, *args, priority=GLib.PRIORITY_DEFAULT)
