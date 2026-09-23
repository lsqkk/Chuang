"""检查更新：问一次 GitHub Releases，看看有没有新版本。

只用标准库（urllib），不引入任何依赖；也不会发送任何身份信息——
只是一次普通的 HTTPS GET，用户可以在菜单里关掉。
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

REPO = "lsqkk/Chuang"
REPO_URL = f"https://github.com/{REPO}"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_URL = f"{REPO_URL}/releases"
ISSUES_URL = f"{REPO_URL}/issues"
NEW_ISSUE_URL = f"{REPO_URL}/issues/new"
# 作者（也用在「关于窗」里，只有一个地方要改）
AUTHOR = "蓝色奇夸克"
AUTHOR_URL = "https://github.com/lsqkk"
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
    sums_url: str = ""          # SHA256SUMS 校验文件（CI 打包时生成）
    sums_name: str = ""
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
        if name == "SHA256SUMS" and not rel.sums_url:
            rel.sums_url, rel.sums_name = url, name
    return rel


def parse_sha256sums(text: str) -> dict[str, str]:
    """解析 `sha256sum` 的输出：`<64 位十六进制>  <文件名>`。坏行直接跳过。"""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        digest, name = parts[0].lower(), parts[1].strip().lstrip("*")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            continue
        out[Path(name).name] = digest
    return out


def sha256_file(path) -> str:
    """算文件的 SHA256（约几 MB 的 .deb，秒级）。"""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


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


def installed_deb_version(package: str = "chuang") -> str:
    """本机 .deb 里装的版本号（没装或没有 dpkg 就返回空串）。"""
    try:
        r = subprocess.run(["dpkg-query", "-W", "-f=${Version}", package],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""
