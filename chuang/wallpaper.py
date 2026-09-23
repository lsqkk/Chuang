"""把此刻的天空写成桌面壁纸。

思路：用和窗口里完全一样的绘制代码，离屏渲染一张与屏幕同尺寸的 PNG，
再交给桌面环境设为壁纸。GNOME 对同一个文件路径未必会重新加载，
所以在两个文件名之间来回写（a/b 交替）来触发刷新。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import cairo

CACHE = Path.home() / ".cache" / "chuang"
SLOTS = (CACHE / "sky-a.png", CACHE / "sky-b.png")
FRAME_DIR = CACHE / "frames"
DAY_XML = CACHE / "sky-day.xml"
TRANSITION_S = 30.0        # 帧间过渡秒数（静态时长 = 一天/帧数 - 过渡）


def slot_uri(index: int) -> str:
    return "file://" + quote(str(SLOTS[index]))


def is_our_uri(uri: str) -> bool:
    """这个 URI 是不是我们自己画出来的（壁纸槽位或动态壁纸 XML）。"""
    if not uri:
        return False
    from urllib.parse import unquote
    path = unquote(uri[len("file://"):]) if uri.startswith("file://") else uri
    try:
        resolved = Path(path).resolve()
    except OSError:
        return False
    return resolved in {p.resolve() for p in (*SLOTS, DAY_XML)}


def screen_size() -> tuple[int, int]:
    """当前主显示器的分辨率；拿不到就退回 1920×1080。"""
    try:
        import gi
        gi.require_version("Gdk", "4.0")
        from gi.repository import Gdk
        display = Gdk.Display.get_default()
        if display is not None:
            monitors = display.get_monitors()
            if monitors and monitors.get_n_items() > 0:
                geo = monitors.get_item(0).get_geometry()
                if geo.width > 320 and geo.height > 240:
                    return int(geo.width), int(geo.height)
    except Exception:
        pass
    return 1920, 1080


def render(path: Path, painter, scene, az0: float, size: tuple[int, int]) -> Path:
    """把一帧天空画进 PNG。

    画不画信息卡与长卷，由 painter.ui.show_info / show_ribbon 决定，
    所以"壁纸上包含此刻的事实"这类选项直接生效。
    """
    w, h = size
    w, h = max(320, int(w)), max(240, int(h))
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surf)
    painter.draw(cr, w, h, scene, az0)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.png")
    surf.write_to_png(str(tmp))
    tmp.replace(path)          # 原子替换，避免桌面读到半张图
    return path


# --------------------------------------------------------------------------
# 与桌面环境打交道
# --------------------------------------------------------------------------

def current_uris() -> tuple[str, str]:
    """读当前壁纸（浅色 / 深色），用于「还原」。"""
    gsettings = shutil.which("gsettings")
    if not gsettings:
        return "", ""
    out = []
    for key in ("picture-uri", "picture-uri-dark"):
        rc, data = _gsettings("get", "org.gnome.desktop.background", key)
        out.append(data.strip().strip("'") if rc == 0 else "")
    return out[0], out[1]


def _gsettings(*args) -> tuple[int, str]:
    """跑一次 gsettings，返回 (返回码, stdout)。

    **一定要带超时**：dconf 偶尔会卡住（会话总线忙、服务刚重启），没超时的
    subprocess.run 会把 GTK 主循环一起拖死——窗口不响应、壁纸也就停在那一刻。
    """
    gsettings = shutil.which("gsettings")
    if not gsettings:
        return 1, ""
    try:
        r = subprocess.run([gsettings, *args], capture_output=True, text=True,
                           timeout=5)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return r.returncode, (r.stdout or "").strip()


def shown_key() -> str:
    """GNOME 此刻真正显示的是哪个键——深色模式看的是 picture-uri-dark。"""
    rc, scheme = _gsettings("get", "org.gnome.desktop.interface", "color-scheme")
    return "picture-uri-dark" if (rc == 0 and "dark" in scheme) else "picture-uri"


def shown_uri() -> str:
    """桌面此刻真正显示的那张壁纸。

    GNOME 在深色模式下用的是 picture-uri-dark，浅色模式下用 picture-uri；
    只盯着其中一个会误判"现在挂着的是哪张"，进而把新图写进正在显示的那个
    文件里——URI 没变，GNOME 不会重读，桌面就停在一张旧图上。
    """
    light, dark = current_uris()
    if not dark or dark == light:
        return light
    return dark if shown_key() == "picture-uri-dark" else light


def set_wallpaper(path: Path) -> tuple[bool, str]:
    return apply_uri("file://" + quote(str(path)))


def apply_uri(uri: str) -> tuple[bool, str]:
    """把任意壁纸 URI（png 或 GNOME 动态壁纸 xml）交给桌面环境。

    写完一定**回读校验**：桌面没接受就把两个键都重设一遍再验一次。
    否则会出现"程序以为换了、桌面还挂着上一张"——表现就是壁纸文件在更新，
    但桌面上那张（连左上角的时间）一直是旧的。
    """
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP", "")
               + os.environ.get("DESKTOP_SESSION", "")).lower()
    tried = []

    if shutil.which("gsettings"):
        schemas = []
        if "cinnamon" in desktop:
            schemas.append("org.cinnamon.desktop.background")
        schemas.append("org.gnome.desktop.background")
        for schema in schemas:
            if _gsettings("get", schema, "picture-uri")[0] != 0:
                tried.append(schema)
                continue
            for attempt in (0, 1):
                _set_background(schema, uri)
                if _background_effective(schema, uri):
                    return True, "已设为桌面壁纸"
                if attempt == 0:
                    time.sleep(0.3)      # dconf 偶尔慢一拍，重设一次再看
            tried.append(schema)

    if "mate" in desktop and shutil.which("gsettings"):
        try:
            subprocess.run(["gsettings", "set", "org.mate.background",
                            "picture-filename", uri.replace("file://", "")],
                           capture_output=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass
        return True, "已设为桌面壁纸"

    plasma = shutil.which("plasma-apply-wallpaperimage")
    if plasma:
        try:
            r = subprocess.run([plasma, uri.replace("file://", "")],
                               capture_output=True, text=True, timeout=15)
            rc = r.returncode
        except (OSError, subprocess.SubprocessError):
            rc = 1
        if rc == 0:
            return True, "已设为桌面壁纸"
        tried.append("plasma")

    return False, ("这个桌面环境没接受这次换图"
                   + (f"（试过：{'、'.join(tried)}）" if tried else ""))


def _set_background(schema: str, uri: str) -> None:
    """把壁纸写进去（GNOME 要连深色模式的键一起写）。"""
    _gsettings("set", schema, "picture-uri", uri)
    if schema.startswith("org.gnome"):
        if _gsettings("get", schema, "picture-uri-dark")[0] == 0:
            _gsettings("set", schema, "picture-uri-dark", uri)
        _gsettings("set", schema, "picture-options", "zoom")


def _background_effective(schema: str, uri: str) -> bool:
    """回读：桌面此刻真正生效的那个键，是不是我们要的那张图。"""
    key = shown_key() if schema.startswith("org.gnome") else "picture-uri"
    rc, data = _gsettings("get", schema, key)
    if rc != 0:                       # 没有这个键（老 GNOME）就退回浅色键
        rc, data = _gsettings("get", schema, "picture-uri")
        if rc != 0:
            return False
    return uri.split("file://")[-1] in data


def slot_mtimes() -> tuple[float, float]:
    out = []
    for path in SLOTS:
        try:
            out.append(path.stat().st_mtime)
        except OSError:
            out.append(0.0)
    return (out[0], out[1])


def shown_slot() -> int:
    """桌面此刻挂着的是哪个槽位；挂的不是我们的图就返回 -1。"""
    current = shown_uri()
    for i in range(len(SLOTS)):
        if current == slot_uri(i):
            return i
    return -1


def stale_shown_slot(tolerance: float = 3.0):
    """桌面挂着我们的图，却比另一张还旧 → 上一次换图没生效。

    返回"该顶上来的那个槽位"，没有这种情况就返回 None。
    """
    shown = shown_slot()
    if shown < 0:
        return None
    other = 1 - shown
    m = slot_mtimes()
    if m[other] > m[shown] + tolerance:
        return other
    return None


def restore(light: str, dark: str) -> tuple[bool, str]:
    """还原成原来那张壁纸。"""
    if not light:
        return False, "没有记录到你原来的壁纸"
    ok, msg = apply_uri(light)
    if ok and dark and dark != light:
        if shutil.which("gsettings"):
            _gsettings("set", "org.gnome.desktop.background",
                       "picture-uri-dark", dark)
    return ok, "已还原成你原来的壁纸" if ok else msg


def write_day_xml(frames: list[Path], start: datetime, out: Path = DAY_XML) -> Path:
    """生成 GNOME 的定时动态壁纸 XML：每帧 25 分钟 + 5 分钟过渡，正好一天。"""
    n = len(frames)
    static_s = 86400.0 / n - TRANSITION_S
    parts = ["<background>", "  <starttime>",
             f"    <year>{start.year}</year>", f"    <month>{start.month:02d}</month>",
             f"    <day>{start.day:02d}</day>", "    <hour>00</hour>",
             "    <minute>00</minute>", "    <second>00</second>",
             "  </starttime>"]
    for i, frame in enumerate(frames):
        nxt = frames[(i + 1) % n]
        parts.append(f"  <static>\n    <duration>{static_s:.1f}</duration>\n"
                     f"    <file>{frame}</file>\n  </static>")
        parts.append(f"  <transition>\n    <duration>{TRANSITION_S:.1f}</duration>\n"
                     f"    <from>{frame}</from>\n    <to>{nxt}</to>\n  </transition>")
    parts.append("</background>\n")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts), encoding="utf-8")
    return out


# --------------------------------------------------------------------------
# 后台渲染：壁纸的绘制放在线程里，别卡住窗口
# --------------------------------------------------------------------------

class Worker:
    """自己的引擎与画笔，独立线程渲染，完成后用 GLib.idle_add 回调。"""

    def __init__(self, lat: float, lon: float, tz: str, label: str, seed: int = 31):
        from .render import SkyPainter
        from .scene import SkyEngine
        self.engine = SkyEngine()
        self.engine.set_location(lat, lon, tz)
        self.painter = SkyPainter(seed=seed)
        self.label = label
        self.lat, self.lon, self.tz = lat, lon, tz
        self._busy = False
        self._lock = threading.Lock()

    def location(self, lat: float, lon: float, tz: str, label: str):
        self.lat, self.lon, self.tz, self.label = lat, lon, tz, label
        self.engine.set_location(lat, lon, tz)

    @property
    def busy(self) -> bool:
        return self._busy

    def _az0(self) -> float:
        return 180.0 if self.lat >= 0 else 0.0

    def render_now(self, when: datetime, weather, show_info: bool, show_ribbon: bool,
                   size: tuple[int, int], slot: int, done) -> None:
        """渲染"此刻"的一张壁纸。done(ok, message, slot) 在主线程被调用。"""
        if not self._lock.acquire(blocking=False):
            return
        self._busy = True

        def work():
            from gi.repository import GLib
            try:
                self.painter.ui.show_info = show_info
                self.painter.ui.show_ribbon = show_ribbon
                if show_ribbon:
                    day = when.replace(hour=0, minute=0, second=0, microsecond=0)
                    self.painter.ui.ribbon = self.engine.ribbon(day, weather)
                    self.painter.ui.ribbon_surface = None
                scene = self.engine.build(when, weather, preview=False,
                                          location_label=self.label)
                # 槽位由主线程决定（它知道桌面此刻显示的是哪一张），这里只负责画
                # 注意：不能在这里给 slot 赋值，否则它就成了局部变量（闭包捕获不到）
                path = SLOTS[slot]
                render(path, self.painter, scene, self._az0(), size)
                ok, msg = set_wallpaper(path)
                GLib.idle_add(done, ok, msg, slot)
            except Exception as exc:
                GLib.idle_add(done, False, f"渲染壁纸失败：{exc}", slot)
            finally:
                self._busy = False
                self._lock.release()

        threading.Thread(target=work, daemon=True, name="chuang-wallpaper").start()

    def render_day(self, day: datetime, weather, show_info: bool, show_ribbon: bool,
                   size: tuple[int, int], frames: int, progress, done) -> None:
        """渲染一整天的 48 帧并生成动态壁纸 XML。"""
        if not self._lock.acquire(blocking=False):
            return
        self._busy = True

        def work():
            from gi.repository import GLib
            try:
                # 先清掉上一次的帧文件：它们有几十 MB，而且重新生成后旧帧
                # 只会让 GNOME 的 XML 指向混在一起的新旧两张图
                for old in FRAME_DIR.glob("frame-*.png"):
                    try:
                        old.unlink()
                    except OSError:
                        pass
                self.painter.ui.show_info = show_info
                self.painter.ui.show_ribbon = show_ribbon
                if show_ribbon:
                    self.painter.ui.ribbon = self.engine.ribbon(day, weather)
                    self.painter.ui.ribbon_surface = None
                step = 1440 // frames
                paths = []
                base = day.replace(hour=0, minute=0, second=0, microsecond=0)
                for i in range(frames):
                    when = base + timedelta(minutes=i * step)
                    scene = self.engine.build(when, weather, preview=False,
                                              location_label=self.label)
                    p = FRAME_DIR / f"frame-{i:02d}.png"
                    render(p, self.painter, scene, self._az0(), size)
                    paths.append(p)
                    if i % 4 == 0 or i == frames - 1:
                        GLib.idle_add(progress, i + 1, frames)
                xml = write_day_xml(paths, day)
                ok, msg = apply_uri("file://" + quote(str(xml)))
                GLib.idle_add(done, ok, msg)
            except Exception as exc:
                GLib.idle_add(done, False, f"生成动态壁纸失败：{exc}")
            finally:
                self._busy = False
                self._lock.release()

        threading.Thread(target=work, daemon=True, name="chuang-wallpaper-day").start()


def next_slot(current_uri: str, last_slot: int) -> tuple[int, Path]:
    """下一个要写的槽位。

    以"桌面此刻真正显示的是哪一张"为准：只有写到另一张，URI 才会真的变化，
    GNOME 才会重新读文件。如果桌面挂的压根不是我们的图（用户自己换了壁纸、
    或挂着动态壁纸 XML），就退回按上次用的槽位交替。
    """
    current = -1
    for i in range(len(SLOTS)):
        if current_uri == slot_uri(i):
            current = i
            break
    index = (1 - current) if current >= 0 else (1 - (int(last_slot or 0) % 2))
    return index, SLOTS[index]


def set_wallpaper(path: Path) -> tuple[bool, str]:
    """按桌面环境设置壁纸。返回 (是否成功, 说明)。"""
    return apply_uri("file://" + quote(str(path)))
