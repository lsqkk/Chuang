#!/usr/bin/env python3
"""从 CHANGELOG.md 里抠出某个版本的段落，当作 GitHub Release 的说明。

    python3 packaging/release-notes.py 1.1.7 > notes.md

版本号只在 `chuang/__init__.py` 一处，所以这里只需要给版本号；
找不到正好那一段时，退而求其次用 `## [Unreleased]` 的段落（并在 stderr 提一句）；
两者都没有就什么都不输出、退出码 2，交给调用方决定怎么办。
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"

HEADING = re.compile(r"^##\s+(?:\[(?P<bracket>[^\]]+)\]|(?P<plain>\S+))\s*(?P<rest>.*)$")


def sections(text: str) -> dict:
    """{'1.1.7': '正文…', 'Unreleased': '正文…'}"""
    out, key, buf = {}, None, []
    for line in text.splitlines():
        m = HEADING.match(line)
        if m:
            if key is not None:
                out[key] = "\n".join(buf).strip()
            raw = m.group("bracket") or m.group("plain") or ""
            key = raw.strip().lstrip("vV")
            buf = []
        elif key is not None:
            buf.append(line)
    if key is not None:
        out[key] = "\n".join(buf).strip()
    return out


def main() -> int:
    if len(sys.argv) < 2:
        sys.exit(__doc__.strip())
    version = sys.argv[1].strip().lstrip("vV")
    text = CHANGELOG.read_text(encoding="utf-8")
    found = sections(text)

    body = found.get(version, "")
    if body:
        print(body)
        return 0

    fallback = found.get("Unreleased", "")
    if fallback:
        print(fallback)
        print(f"（CHANGELOG.md 里还没有 {version} 的段落，这里先用 [Unreleased] 的内容）",
              file=sys.stderr)
        return 0

    print(f"CHANGELOG.md 里找不到 {version} 的段落", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
