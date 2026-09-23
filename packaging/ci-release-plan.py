#!/usr/bin/env python3
"""决定这次 CI 运行要不要发版、发哪个 tag。

给 GitHub Actions 用（`.github/workflows/release.yml`），也可以在本地跑着玩：

    python3 packaging/ci-release-plan.py --event push --ref refs/heads/master \
        --tags "v1.0.0 v1.1.6"

规则（三条互相兜底，平时改代码不会误发版）：

  * 推 tag（`v1.1.7`）  → 发；但 tag 名必须与 `chuang/__init__.py` 的版本号一致
  * 推 master           → 只有版本号比现有所有 tag 都新时才发
  * 手动触发            → 一定发（用来补传、重发、修 Release 说明）

输出写成 `key=value`（写进 `$GITHUB_OUTPUT`，本地跑就打屏）：

    version=1.1.7
    tag=v1.1.7
    release=true
    reason=版本号从 1.1.6 升到 1.1.7
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
INIT = ROOT / "chuang" / "__init__.py"


def read_version() -> str:
    """版本号只有一处：chuang/__init__.py 的 __version__。"""
    m = re.search(r'__version__\s*=\s*"([^"]+)"', INIT.read_text(encoding="utf-8"))
    if not m:
        sys.exit(f"{INIT} 里找不到 __version__")
    return m.group(1)


def parse_version(text: str) -> tuple:
    """把 'v1.2.3' / '1.2' / '1.2.3-beta.1' 变成可比较的元组 (1, 2, 3)。"""
    text = (text or "").strip().lstrip("vV")
    text = re.split(r"[-+ ]", text)[0]
    parts = []
    for chunk in text.split("."):
        m = re.match(r"(\d+)", chunk)
        if not m:
            break
        parts.append(int(m.group(1)))
    return tuple(parts)


def local_tags() -> list:
    """本地已有的版本 tag（CI 里 checkout 要带 fetch-depth: 0）。"""
    try:
        r = subprocess.run(["git", "tag", "--list"], cwd=ROOT,
                           capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return []
    if r.returncode != 0:
        return []
    return [t.strip() for t in r.stdout.splitlines() if t.strip()]


def decide(event: str, ref: str, want_tag: str, tags: list, version: str) -> dict:
    """返回 {'version','tag','release','reason'}；发不了就抛出 SystemExit(1)。"""
    tag = f"v{version}"

    if event == "workflow_dispatch":
        picked = (want_tag or "").strip() or tag
        if picked != tag:
            sys.exit(f"::error::手动指定的 tag {picked} 与版本号 {version} 对不上"
                     f"（应该发 {tag}，或者先把 chuang/__init__.py 改成对应版本）")
        return {"version": version, "tag": picked, "release": "true",
                "reason": f"手动触发：发布 {picked}"}

    if ref.startswith("refs/tags/"):
        pushed = ref[len("refs/tags/"):]
        if pushed != tag:
            sys.exit(f"::error::推上来的 tag {pushed} 与 chuang/__init__.py 里的版本号 "
                     f"{version} 对不上——把版本号改成 {pushed.lstrip('v')} 再重推")
        return {"version": version, "tag": pushed, "release": "true",
                "reason": f"推送了 tag {pushed}"}

    # 分支推送（master）：只有版本号真的涨了才发
    newest = max(tags, key=parse_version) if tags else ""
    if tag in tags:
        return {"version": version, "tag": tag, "release": "false",
                "reason": f"{tag} 已经发布过了，这次只是普通提交，不发版"}
    if parse_version(tag) <= parse_version(newest):
        return {"version": version, "tag": tag, "release": "false",
                "reason": f"版本号 {version} 不比 {newest} 新，不发版（改版本号才会发）"}
    return {"version": version, "tag": tag, "release": "true",
            "reason": f"版本号 {newest or '（第一个 tag）'} → {tag}，开始发版"}


def main() -> int:
    ap = argparse.ArgumentParser(description="算出版本、tag 与要不要发版")
    ap.add_argument("--event", default=os.environ.get("GITHUB_EVENT_NAME", "push"),
                    help="GitHub Actions 的事件名（默认读环境变量）")
    ap.add_argument("--ref", default=os.environ.get("GITHUB_REF", "refs/heads/master"),
                    help="GITHUB_REF，用来区分推分支还是推 tag")
    ap.add_argument("--want-tag", default=os.environ.get("INPUT_TAG", ""),
                    help="手动触发时指定的 tag")
    ap.add_argument("--tags", default="",
                    help="现有 tag，空格分隔；不填就自己 git tag --list")
    args = ap.parse_args()

    tags = args.tags.split() if args.tags else local_tags()
    plan = decide(args.event, args.ref, args.want_tag, tags, read_version())

    out = os.environ.get("GITHUB_OUTPUT")
    text = "".join(f"{k}={v}\n" for k, v in plan.items())
    print(text, end="")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
