"""把这扇窗此刻的样子存成一张 PNG。

菜单 → 「把这扇窗存成图片…」。这和 `tools/snapshot.py` 是同一件事的两个入口：
那个是开发时在终端里跑（还能指定任意时刻与天气），这个给用户点一下。

三件刻意的取舍：

* **画的就是你现在看到的那一帧**：同一个尺寸、同一份场景、同一张信息卡与长卷
  （连"正在预览"那条提示也在）。不做额外美化、也不偷偷放大——图上就是窗上
  那一刻，尺寸写进提示里。
* 存在 `~/Pictures`（认 `XDG_PICTURES_DIR`），文件名是当地时间的年月日与时刻。
  **同名的绝不覆盖**，往后面加 -2 / -3（用户的"上一张"不该被悄悄顶掉）。
* 只写一个 PNG（cairo 自带，不引第三方库）。写不出去就把原因说出来。

画的时候**没有任何网络**：这一帧用的是手上那份场景（天气没联网时就是纯天文）。
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import cairo

from .scene import facing_azimuth


def pictures_dir() -> Path:
    """图片放哪儿：先认 `XDG_PICTURES_DIR`，不然就是 `~/Pictures`。"""
    value = os.environ.get("XDG_PICTURES_DIR")
    return Path(value) if value else Path.home() / "Pictures"


def default_path(when: datetime, directory: Path | None = None) -> Path:
    """这一帧该叫什么名字（**已经存在的名字往后顺延**，不覆盖）。"""
    directory = directory or pictures_dir()
    stem = f"chuang-{when:%Y-%m-%d-%H%M}"
    path = directory / f"{stem}.png"
    n = 2
    while path.exists():
        path = directory / f"{stem}-{n}.png"
        n += 1
    return path


def render_frame(painter, scene, w: int, h: int, azimuth: float | None = None):
    """把这一帧画进一张离屏图并返回它（和窗口里画的是同一条路）。"""
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, int(w), int(h))
    cr = cairo.Context(surf)
    if azimuth is None:
        azimuth = facing_azimuth(scene.lat)
    painter.draw(cr, w, h, scene, azimuth)
    painter.draw_chip(cr, w, h)          # 预览时右上角那条提示也照画
    return surf


def save_png(painter, scene, w: int, h: int, path: Path) -> Path:
    """画一帧写进 `path`（父目录不存在就建），返回真正写下去的那个路径。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    render_frame(painter, scene, w, h).write_to_png(str(path))
    return path
