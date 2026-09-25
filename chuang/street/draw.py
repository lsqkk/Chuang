
"""一帧的顺序：谁该出现、谁在谁前面。

`street.draw()` 由 `render/ground.py` 调用，画在"地面"那一层里。天上有飞机经过时
不归这里管（那是 `render/sky.py` 的事）；这里只管路面上的树、路灯、人、车，
以及它们脚下那些影子。
"""

from __future__ import annotations

from .actors import Actor, ZONE_BOTTOM, ZONE_TOP, _on_road, _visibility, activity
from .people import _cyclist, _ped
from .props import _lamps, _trees
from .vehicles import _bus, _car



def draw(cr, w: float, h: float, scene, actors: list[Actor], t: float,
         light, base_col, lamps=(), trees=(), people: bool = True,
         traffic: bool = True, trees_on: bool = True, lamps_on: bool = True) -> None:
    """把人和车画在地面带上（在剪影之上、窗台之下）。

    `people` / `traffic` / `trees_on` / `lamps_on` 是菜单 → 场景里的那几开关：
    关掉行人就只剩车，关掉行道树就只剩路灯……各自独立，画面的其余部分不受影响。
    （骑车的人算在"行人"里，机动车算在"车辆"里。）
    """
    if trees_on:
        _trees(cr, w, h, scene, light, trees, t)
    if lamps_on:
        _lamps(cr, w, h, scene, light, lamps)
    if not actors or not (people or traffic):
        return
    rain = (scene.has_weather and scene.precip_kind == "rain"
            and scene.precip_strength > 0.15)
    dens = activity(scene.when, scene)
    for a in sorted(actors, key=lambda a: a.depth):
        if a.kind in ("ped", "cyclist") and not people:
            continue                      # 关掉行人时，骑车的也一起关
        if a.kind in ("car", "bus") and not traffic:
            continue
        vis = _visibility(a, scene)
        if vis <= 0.03:
            continue
        if not _on_road(a, t, dens.get(a.kind, 0.6)):
            continue
        x = ((a.phase + a.speed * t) % 1.3 - 0.15) * w
        y = (ZONE_TOP + (ZONE_BOTTOM - ZONE_TOP) * a.depth) * h
        s = h / 760.0
        if a.kind == "ped":
            _ped(cr, x, y, s, w, a, vis, scene, light, base_col, t, rain)
        elif a.kind == "cyclist":
            _cyclist(cr, x, y, s, w, a, vis, scene, light, base_col, t)
        elif a.kind == "car":
            _car(cr, x, y, s, w, a, vis, scene, light, rain)
        else:
            _bus(cr, x, y, s, w, a, vis, scene, light)
