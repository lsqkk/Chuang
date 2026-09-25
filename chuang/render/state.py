
"""画笔旁边的纯数据：界面状态、一行事实、离屏缓存的槽位。

三样都不画东西，只用来说明"这一刻该画什么"与"这张离屏图还算不算数"。
`UIState` 的字段名与 `config.SCENE_SWITCHES`、菜单里的动作名是同一份，改名字要
三处一起改（见 config.py 的注释）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import cairo



# --------------------------------------------------------------------------
# 主画笔
# --------------------------------------------------------------------------

class UIState:
    """界面状态：信息面板、长卷游标、预览、提示。"""

    def __init__(self) -> None:
        self.show_info = True
        self.info_compact = False       # 信息卡精简模式（只剩时间与那句话）
        # 卡片上画不画"能点的东西"（收起箭头 / 刷新按钮 / 每行右边那颗小箭头）。
        # 桌面壁纸上的那张卡没有鼠标，画成可点的样子只会误导人（见 wallpaper.py）。
        self.info_buttons = True
        self.info_rows: list = []       # 这一帧画出来的"事实"（FactRow 列表）
        self.info_rects: list = []      # 信息卡上可点的方块 (x, y, w, h, kind, when)
        self.info_hover = -1            # 鼠标停在哪一块上（-1 = 没有）
        self.info_hover_dt = None       # 悬停在"日弧"上时指到的时刻
        self.show_ribbon = True
        self.ribbon: list[tuple] = []
        self.ribbon_info: list[str] = []   # 长卷每一格的那句天气（悬停时显示）
        self.ribbon_key = None
        self.ribbon_surface: cairo.ImageSurface | None = None
        self.ribbon_rect = (0.0, 0.0, 0.0, 0.0)
        self.preview_dt = None          # 正在预览的时刻
        self.preview_started = None     # 进入预览那一刻的墙钟（让人车在预览里也继续走）
        self.hover_dt = None
        self.dragging = False
        self.chip_rect = (0.0, 0.0, 0.0, 0.0)
        self.toast = ""
        self.toast_icon = "info"        # 提示条左边那枚小图标
        self.toast_until = 0.0
        self.toast_span = 0.0           # 这条提示一共停留几秒（画那条细线用）
        self.toast_rect = (0.0, 0.0, 0.0, 0.0)
        self.toast_detail = ""          # 非空时：点提示条可以看/复制完整内容
        # 此刻是不是正在问一次真实天气（窗口的心跳把它从 weather.busy 抄下来）。
        # 卡片底下那行字与右下角那枚刷新图标都看它——按下 R 就不再是"没回声"。
        self.weather_busy = False
        self.hint_shown = False
        # 画布底下被系统面板 / dock 挡住的像素数（桌面壁纸渲染时才非 0）。
        # 长卷与它的时刻标签得让开这一条，否则正好被面板压住（见 _draw_ribbon）。
        self.bottom_inset = 0.0
        # ---- 窗外画什么（菜单 → 场景；字段名与 config.SCENE_SWITCHES 一致）----
        # 全部默认为真：什么都不动的时候，画面和 1.1.12 一模一样。
        self.show_people = True
        self.show_traffic = True
        self.show_trees = True
        self.show_lamps = True
        self.show_planes = True
        self.show_skyline = True
        self.show_clouds = True
        self.show_stars = True
        self.show_weatherfx = True
        self.show_plant = True
        self.plant_sway = True


@dataclass
class FactRow:
    """「此刻的事实」里的一行：图标 + 标题 + 主值 + 副值 + 点它做什么。

    action 有三种：""（只是看看）、"open"（跳到 when 那一刻去预览）、
    "detail"（摊开这条背后的完整数据）。窗口那边照 action 决定点下去干什么，
    画的地方只管把方块记进 ui.info_rects。
    """

    icon: str
    label: str
    value: str
    note: str = ""
    action: str = ""
    when: datetime | None = None


class CacheSlot:
    """一张"按参数缓存"的离屏图：key + surface。

    这个文件里有 5 张这样的图（天空底色、云、城市、暗角、窗台）。以前每处都
    自己写一遍

        if self._x_surf is None or self._x_key != key or 尺寸对不上:

    ——而"手工拼 key"正是这里最容易出错的地方：漏一个参数，就会出现"换了城市/
    换了天气，画面还是上一张"。1.1.8 修的那次（城市种子没进 key）就是这条路
    的产物。把判断收进这个二十行的小类之后：**要加参数就往 key 里加**，
    判断逻辑只有一处，换地方也不用再抄一遍。
    """

    __slots__ = ("key", "surf")

    def __init__(self) -> None:
        self.key = None
        self.surf: cairo.ImageSurface | None = None

    def stale(self, key, w: int, h: int) -> bool:
        """该重画了吗？（没画过 / key 变了 / 尺寸变了）"""
        return (self.surf is None or self.key != key
                or self.surf.get_width() != int(w)
                or self.surf.get_height() != int(h))

    def store(self, key, surf: cairo.ImageSurface) -> cairo.ImageSurface:
        self.key, self.surf = key, surf
        return surf

    def invalidate(self) -> None:
        self.key, self.surf = None, None
