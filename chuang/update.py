"""检查更新：问一次 GitHub Releases，看看有没有新版本。

只用标准库（urllib），不引入任何依赖；也不会发送任何身份信息——
只是一次普通的 HTTPS GET，用户可以在菜单里关掉。
"""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

REPO = "lsqkk/Chuang"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{REPO}/releases"
UA = "Chuang-UpdateCheck (+https://github.com/lsqkk/Chuang)"
TIMEOUT = 8.0
CHECK_INTERVAL = 24 * 3600          # 自动检查的间隔：一天一次


@dataclass
class Release:
    tag: str = ""
    name: str = ""
    url: str = ""
    notes: str = ""
    published: str = ""
    deb_url: str = ""
    deb_name: str = ""
    version: tuple = ()
    assets: list = field(default_factory=list)

    @property
    def version_text(self) -> str:
        return ".".join(str(n) for n in self.version) if self.version else self.tag


def parse_version(text: str) -> tuple:
    """把 'v1.2.3'、'1.2'、'1.2.3-beta.1' 变成可比较的元组 (1, 2, 3)。"""
    if not text:
        return ()
    text = text.strip().lstrip("vV")
    text = re.split(r"[-+ ]", text)[0]          # 预发布后缀先不参与比较
    parts = []
    for chunk in text.split("."):
        m = re.match(r"(\d+)", chunk)
        if not m:
            break
        parts.append(int(m.group(1)))
    return tuple(parts)


def is_newer(remote: tuple, local: tuple) -> bool:
    """远端版本是否比本地新（长度不同时短的补 0）。"""
    if not remote:
        return False
    n = max(len(remote), len(local))
    r = tuple(remote) + (0,) * (n - len(remote))
    l = tuple(local) + (0,) * (n - len(local))
    return r > l


def fetch_latest(timeout: float = TIMEOUT) -> Optional[Release]:
    """取最新 release；网络或接口出错时返回 None（不抛异常）。"""
    req = urllib.request.Request(
        API_LATEST,
        headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict) or "tag_name" not in data:
        return None
    if data.get("draft") or data.get("prerelease"):
        return None

    rel = Release(
        tag=data.get("tag_name") or "",
        name=data.get("name") or data.get("tag_name") or "",
        url=data.get("html_url") or RELEASES_URL,
        notes=data.get("body") or "",
        published=data.get("published_at") or "",
        version=parse_version(data.get("tag_name") or ""),
    )
    for asset in data.get("assets") or []:
        name = asset.get("name") or ""
        url = asset.get("browser_download_url") or ""
        rel.assets.append({"name": name, "url": url, "size": asset.get("size") or 0})
        if name.endswith(".deb") and not rel.deb_url:
            rel.deb_url, rel.deb_name = url, name
    return rel


def check(local_version: str, timeout: float = TIMEOUT) -> tuple:
    """返回 (有新版本?, Release 或 None, 说明)。

    给命令行用；图形界面里走 fetch_latest + is_newer，方便分别处理网络错误。
    """
    release = fetch_latest(timeout)
    if release is None:
        return False, None, "检查更新失败（网络或 GitHub 接口不可用）"
    if is_newer(release.version, parse_version(local_version)):
        return True, release, f"有新版本 {release.tag}（当前 {local_version}）"
    return False, release, f"已是最新版本 {local_version}"
