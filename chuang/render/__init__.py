"""天空绘制：全部由 Cairo 逐帧画出，没有一张位图素材。

这个包原来是**一个 3235 行的 `render.py`**。功能一直在长（天色、日月星、
云雨雪、城市、街道、窗台与盆栽、长卷、信息卡、提示条），到 1.2.0 已经长到
"想改一处得先翻半天"的程度，于是按**画面里的层**拆开：

| 模块 | 管什么 |
|---|---|
| `paint.py` | 最底层：纵向排版、颜色/几何小工具、字体与文字、矢量小图标 |
| `state.py` | 画笔旁边的纯数据：`UIState` / `FactRow` / `CacheSlot` |
| `weatherfx.py` | 云的精灵图与天气粒子（云、雨、雪、闪电、飞机）的状态 |
| `core.py` | 每一层都要用的：镜头、环境光、天际线数据、缓存失效 |
| `sky.py` | 天空：天色、星星、日月、云、飞机，以及下在窗外的雨雪与闪电 |
| `ground.py` | 地面：路面、街上的人车、天际线、暗角与玻璃反光 |
| `sill.py` | 窗台与那盆植物（窗台进缓存，植物每帧现画——它会摇） |
| `ribbon.py` | 窗底那条「今日天色」长卷 |
| `card.py` | 信息卡的内容、文案与排版 |
| `cardpaint.py` | 信息卡的画法（一笔一笔，最长的那一段） |
| `notify.py` | 浮在最上面的两条：提示条与"正在预览" |
| `painter.py` | 组装成 `SkyPainter`：一帧的顺序与这一帧的参数 |

每一层都是一个**混入类**（`SkyLayer` / `GroundLayer` / …），共用同一支画笔的
状态（`self.ui`、`self.fx` 与那五个离屏缓存槽位）——拆的是文件，不是状态，
所以 `SkyPainter` 的对外接口一个都没变：`draw()` / `draw_chip()` /
`invalidate_location()` 与那些事实行的接口都是原来那套。

改哪一层就进哪个模块；"一帧的顺序"在 `painter.py` 的 `draw()` 里，
**顺序是内容的一部分**（雨要下在窗外、影子要落在窗台上）。
"""

from __future__ import annotations

# 对外仍然只认这个包：`from chuang.render import SkyPainter`（tools 与 tests
# 都是这么用的）。下面的名字就是原来 `render.py` 的公开门面。
from .card import CardLayer
from .cardpaint import CardPaintLayer
from .core import PainterCore
from .ground import GroundLayer
from .notify import NotifyLayer
from .paint import (ALT_GROUND, ALT_TOP, GROUND_TOP, GROUND_Y, HORIZON_Y,
                    RIBBON_Y, ROAD_BOTTOM, SILL_TOP, SILL_Y, TAU, clamp,
                    draw_icon, draw_text, draw_text_bl, font, icon_ink_y,
                    icon_tint, ink_baseline, lerp, noise_tile, rgba, rgba01,
                    rounded_rect, scene_height, text_ascent, text_ink, wrap_cjk,
                    x_for_az, y_for_alt)
from .painter import SkyPainter
from .ribbon import RibbonLayer
from .sill import SillLayer
from .sky import SkyLayer
from .state import CacheSlot, FactRow, UIState
from .weatherfx import Sprites, WeatherFX

__all__ = [
    "SkyPainter", "PainterCore", "SkyLayer", "GroundLayer", "SillLayer",
    "RibbonLayer", "CardLayer", "CardPaintLayer", "NotifyLayer",
    "UIState", "FactRow", "CacheSlot", "Sprites", "WeatherFX",
    "scene_height", "clamp", "lerp", "rgba", "rgba01", "noise_tile",
    "x_for_az", "y_for_alt", "font", "draw_text", "draw_text_bl",
    "text_ascent", "text_ink", "ink_baseline", "icon_ink_y", "icon_tint",
    "rounded_rect", "wrap_cjk", "draw_icon",
    "TAU", "HORIZON_Y", "GROUND_Y", "SILL_Y", "SILL_TOP", "RIBBON_Y",
    "GROUND_TOP", "ROAD_BOTTOM", "ALT_TOP", "ALT_GROUND",
]
