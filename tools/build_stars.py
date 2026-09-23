#!/usr/bin/env python3
"""把 d3-celestial 的星表裁剪成「窗」需要的精简星表。

用法:
    python3 tools/build_stars.py /tmp/stars6.json

输入为 d3-celestial 的 stars.6.json（GeoJSON，坐标 = [赤经°, 赤纬°]）。
输出 chuang/data/stars.json：[[ra, dec, mag, bv], ...]，按亮度排序。
星等上限 5.0，约 1600 颗——足够在真正的暗夜里铺满天空，又不会拖慢绘制。
"""

import json
import sys
from pathlib import Path

MAG_LIMIT = 5.0
OUT = Path(__file__).resolve().parent.parent / "chuang" / "data" / "stars.json"


def main() -> None:
    src = json.load(open(sys.argv[1], encoding="utf-8"))
    rows = []
    for feat in src["features"]:
        try:
            mag = float(feat["properties"]["mag"])
        except (KeyError, TypeError, ValueError):
            continue
        if mag > MAG_LIMIT:
            continue
        ra, dec = (float(c) for c in feat["geometry"]["coordinates"][:2])
        try:
            bv = float(feat["properties"].get("bv"))
        except (TypeError, ValueError):
            bv = 0.6
        rows.append([round(ra % 360.0, 4), round(dec, 4), round(mag, 2), round(bv, 3)])
    rows.sort(key=lambda r: r[2])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rows, separators=(",", ":")), encoding="utf-8")
    print(f"写入 {OUT} ：{len(rows)} 颗星（星等 ≤ {MAG_LIMIT}）")


if __name__ == "__main__":
    main()
