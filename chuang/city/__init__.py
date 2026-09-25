"""地平线上的那片城市：远山、远景楼群、近景屋顶。

1.1.4 之前这里是"一排深浅不一的矩形"：所有楼长得一模一样、白天是一块块没有
受光面的色块、黄昏一律压成纯黑的剪影，也看不见影子。现在把城市拆成三层——

  · 远山      空气糊掉的山脊，只有一条起伏的轮廓；
  · 远景楼群  更远的一圈楼，颜色被天空洗淡，夜里只有零星几盏灯；
  · 近景建筑  有屋顶构件（水箱、楼梯间、天线、烟囱、坡屋顶）、有窗格、
               有受光面与背光面，彼此之间还会互相遮挡。

形状由"城市名 + 经纬度"做种子生成，所以同一座城市永远长成同一片屋顶；颜色与
光影全部由太阳（夜里是月亮）的高度角与方位角、直射光与环境光算出来——太阳偏
左，受光面就在左边、影子就往右倒；太阳低，影子就长；云厚，影子就淡。

1.2.0 拆成了两个模块（原来 `city.py` 有 875 行，是"生成"与"绘制"两件事）：
`model.py` 长出这座城（`Light` / `Building` / `Layers` / `generate` / `wall_color`），
`draw.py` 把它画到画布上（含楼与楼之间的互相遮挡、路面上的影子）。
对外的名字照旧：`render/` 那边一直用的是 `_city.generate` / `_city.draw` /
`_city.ground_shadow` / `_city.Light`。
"""

from __future__ import annotations

from .model import Light
from .model import Building
from .model import Layers
from .model import generate
from .model import wall_color
from .draw import draw
from .draw import ground_shadow
