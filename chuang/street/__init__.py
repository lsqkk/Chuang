"""街上的行人与车辆。

位置不是"逐帧推进"的状态，而是**时间的函数**：x = f(相位 + 速度 × 当天秒数)。
好处有三个：窗口里自然而然地走起来；动态壁纸的每一帧都能算出对应的位置，
于是帧与帧之间过渡时人会真的在移动；同一时刻永远画成同一个样子。

1.1.4 之前这里只有"长条车身 + 两个露在外面的圆轱辘"的车和"火柴人"的行人，
而且人和车都不投影。现在：

  · 车轮压在车身的轮拱里——只有贴着地面的那一段露出来，不是骑在圆圈上；
  · 车有轿车 / 掀背 / SUV / 厢式 / 出租 / 公交几种轮廓，白天车窗映天、
    夜里车灯会在地上洒一片光；
  · 人是"走"的：腿有膝盖、手臂反相摆动、有躯干和头，雨天撑伞，还可以牵着狗；
  · 每个人、每辆车都在路面上投下影子，方向与长短由太阳方位角与高度角决定。

1.2.0 拆成了包（原来是一个 1182 行的 `street.py`，是全项目最长的一个文件）：

| 模块 | 管什么 |
|---|---|
| `actors.py` | 谁在街上：`Actor`、名单（`roster`）、一天里各时段的密度（`activity`） |
| `people.py` | 行人：走路、撑伞、牵狗、骑自行车 |
| `vehicles.py` | 轿车 / 掀背 / SUV / 厢式 / 出租 / 公交（白天映天、夜里亮灯） |
| `props.py` | 街边不动的那几样：行道树（按季节换色）与路灯 |
| `shadows.py` | 人、车、树投在路面上的影子（同一个太阳） |
| `draw.py` | 一帧的顺序：谁该出现、谁被挡住、谁在谁前面 |

对外的名字（`street.roster` / `street.trees` / `street.draw`）一个都没变——
`render/ground.py` 与 `tests/test_scene_options.py` 都照旧。
"""

from __future__ import annotations

from .actors import Actor
from .actors import roster
from .actors import activity
from .props import trees
from .draw import draw
