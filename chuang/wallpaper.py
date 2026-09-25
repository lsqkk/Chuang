"""把此刻的天空写成桌面壁纸。

思路：用和窗口里完全一样的绘制代码，离屏渲染一张与屏幕同尺寸的 PNG，
再交给桌面环境设为壁纸。

刷新机制（**别照着以前的注释改回去**）：gnome-shell 把解码结果按文件缓存在
`Meta.BackgroundImageCache` 里，并且只为"当前正在显示的那个文件"挂文件监听。
所以常态是**就地重写那张正在显示的图**——内容一变，shell 自己 purge + 重读；
反过来"写另一张再把 URI 切过去"会让它直接拿出那个文件**上一次**的解码结果，
桌面于是显示旧画面（这是 1.1.4 修掉的坑，详见 DESIGN.md §4.1）。
只有"接管桌面"和每隔几分钟的兜底才真的换一次文件名，换完要 touch() 两下。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import cairo

CACHE = Path.home() / ".cache" / "chuang"
SLOTS = (CACHE / "sky-a.png", CACHE / "sky-b.png")
# 动态壁纸的帧目录：两个轮着用。正在显示的那一份在生成期间**一个字节都不动**，
# 画完、XML 写好了才切过去——中途失败最多是"这次没换成"，桌面不会变空。
FRAME_DIRS = (CACHE / "frames-a", CACHE / "frames-b")
LEGACY_FRAME_DIR = CACHE / "frames"      # 1.1.7 及以前用的单一目录名
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
    """当前主显示器的分辨率；拿不到就退回 1920×1080。

    多屏时按**主显示器**的比例作画：GNOME 对每块屏幕用的是同一张图（zoom 裁切），
    所以别的屏幕会按自己的比例裁一下——够用，不是"跨屏拼一张大图"。
    真要做跨屏拼图得按所有显示器的包围盒渲染，代价是每帧的像素数和窗口尺寸
    不再相关（双 4K 就是 4 倍多的绘制量），暂时不值得。
    """
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


def screen_bottom_inset() -> float:
    """主显示器底下被系统面板 / dock 占掉多少像素。

    壁纸是**铺满整屏**的，而常驻的底部 dock 会压在它上面（本机 dash-to-dock
    固定在底部，workarea 比屏幕矮 64 像素）。桌面只在"面板常驻"时才把它算进
    workarea——读到的这个数就是"底下那一条有多高"，长卷照着它让开（见
    render._draw_ribbon 的 bottom_inset）。读不到（没有 Gdk / 面板自动隐藏）
    就返回 0：那就只剩 `scene_height` 里那套按像素估的余量。

    **必须在主线程调用**（Gdk 不是线程安全的），所以它在
    WallpaperController._render_target 里算，再传给渲染线程。
    """
    try:
        import gi
        gi.require_version("Gdk", "4.0")
        from gi.repository import Gdk
        display = Gdk.Display.get_default()
        if display is not None:
            monitors = display.get_monitors()
            if monitors and monitors.get_n_items() > 0:
                mon = monitors.get_item(0)
                geo, wa = mon.get_geometry(), mon.get_workarea()
                if geo.height > 240 and wa.height > 0:
                    bottom = (geo.y + geo.height) - (wa.y + wa.height)
                    if 0 < bottom < geo.height * 0.4:
                        return float(bottom)
    except Exception:
        pass
    return 0.0


def apply_scene_opts(painter, opts: dict | None) -> None:
    """把"窗外画什么"的开关搬到壁纸那份画笔上（在渲染线程里、开画之前调）。

    壁纸有自己的 SkyPainter（渲染跑在后台线程），所以窗口里改了场景开关，
    必须**显式**同步过去——否则会出现"窗口里已经把行人和车收起来了，桌面上
    它们还在走"。只在渲染线程开头改，别在主线程一边画一边改。
    """
    for field, value in (opts or {}).items():
        setattr(painter.ui, field, bool(value))


def render(path: Path, painter, scene, az0: float, size: tuple[int, int]) -> Path:
    """把一帧天空画进 PNG（跑在渲染线程里，只碰自己那份 painter）。

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


def touch(path: Path) -> None:
    """只改一下 mtime（内容不动）。

    gnome-shell 把解码好的壁纸按**文件**缓存（Meta.BackgroundImageCache），
    而且只监听"当前正在显示的那个文件"：只有它收到"这个文件变了"才会
    purge 掉缓存并重读。碰一下 mtime 就是给它的第二次提醒——换过 URI
    之后补这一下，桌面才会显示新内容而不是这个文件上一版的解码结果。
    """
    try:
        os.utime(path, None)
    except OSError:
        pass


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


def current_options() -> str:
    """当前的缩放方式（picture-options：zoom / scaled / centered / wallpaper…）。

    接管壁纸时我们一定要写 zoom（天空必须铺满），所以**必须连这个值一起记下来**，
    否则"还原成原来的壁纸"只能还原图片、还原不了"他原来是用拉伸/居中的"。
    """
    rc, data = _gsettings("get", "org.gnome.desktop.background", "picture-options")
    return data.strip().strip("'") if rc == 0 else ""


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
    """把某个 png/xml 设为壁纸。返回 (是否成功, 说明)。"""
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


def restore(light: str, dark: str, options: str = "") -> tuple[bool, str]:
    """还原成原来那张壁纸（连缩放方式一起还回去）。"""
    if not light:
        return False, "没有记录到你原来的壁纸"
    ok, msg = apply_uri(light)
    if ok and shutil.which("gsettings"):
        if dark and dark != light:
            _gsettings("set", "org.gnome.desktop.background",
                       "picture-uri-dark", dark)
        # 接管时我们把 picture-options 改成了 zoom（sky 必须铺满），
        # 还回去的时候必须一起还——用户可能本来是"拉伸/居中/平铺"。
        if options:
            _gsettings("set", "org.gnome.desktop.background", "picture-options", options)
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


def active_frame_dir() -> Path | None:
    """当前动态壁纸 XML 指的是哪个帧目录（没有就返回 None）。"""
    try:
        text = DAY_XML.read_text(encoding="utf-8")
    except OSError:
        return None
    for d in (*FRAME_DIRS, LEGACY_FRAME_DIR):
        if str(d) in text:
            return d
    return None


def _clear_dir(path: Path) -> None:
    """清空一个**我们自己**的帧目录（只删这个目录本身，不碰别处）。"""
    if path.parent != CACHE:
        return
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)


def _prune_frame_dirs(keep: Path) -> None:
    """删掉不用的帧目录（几十 MB）。只在新的 XML 生效之后才调用。"""
    for d in (*FRAME_DIRS, LEGACY_FRAME_DIR):
        if d != keep and d.parent == CACHE:
            shutil.rmtree(d, ignore_errors=True)


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
        # 换了城市：楼群数据与那一堆离屏缓存（城市/窗台/天空/云）都得重来，
        # 否则桌面上的天空还朝着上一座城市。
        self.painter.invalidate_location()

    @property
    def busy(self) -> bool:
        return self._busy

    def _az0(self) -> float:
        from .scene import facing_azimuth
        return facing_azimuth(self.lat)

    def render_now(self, when: datetime, weather, show_info: bool, show_ribbon: bool,
                   size: tuple[int, int], slot: int, done,
                   adopt: bool = False, weather_off: bool = False,
                   compact: bool = False, inset: float = 0.0,
                   scene_opts: dict | None = None) -> None:
        """渲染"此刻"的一张壁纸。done(ok, message, slot) 在主线程被调用。

        adopt=False（常态）：**就地更新桌面正在显示的那个文件**。gnome-shell
        对它有文件监听，内容一变就丢掉解码缓存重读，画面直接换上新的。
        adopt=True（接管 / 每几分钟兜底一次）：写另一张再把壁纸 URI 切过去，
        切换后要连碰两次 mtime —— 刚切过去的那张，shell 很可能先拿出它**上一次**
        的解码结果（这就是"显示的是这个文件上一版"的来源）。

        inset：屏幕底下被系统面板 / dock 占掉的高度（见 screen_bottom_inset）。
        scene_opts：菜单 → 场景里那几个开关（窗外画什么）。**必须跟窗口里一致**：
        关了行人却只改了窗口那一份，桌面上照样有人走。
        """
        if not self._lock.acquire(blocking=False):
            return
        self._busy = True

        def work():
            from gi.repository import GLib
            # 回主线程的那一跳必须走高优先级的 idle（见 mainloop.py）：
            # 这一句放在线程里，和上面那行 GLib 导入同一个道理（沿用原来的姿势）
            from .mainloop import to_main
            try:
                self.painter.ui.show_info = show_info
                self.painter.ui.info_compact = compact
                self.painter.ui.info_buttons = False      # 桌面上没有鼠标
                self.painter.ui.bottom_inset = float(inset or 0.0)
                self.painter.ui.show_ribbon = show_ribbon
                apply_scene_opts(self.painter, scene_opts)
                if show_ribbon:
                    day = when.replace(hour=0, minute=0, second=0, microsecond=0)
                    self.painter.ui.ribbon = self.engine.ribbon(day, weather)
                    self.painter.ui.ribbon_surface = None
                scene = self.engine.build(when, weather, preview=False,
                                          location_label=self.label,
                                          weather_off=weather_off)
                # 槽位由主线程决定（它知道桌面此刻显示的是哪一张），这里只负责画
                # 注意：不能在这里给 slot 赋值，否则它就成了局部变量（闭包捕获不到）
                path = SLOTS[slot]
                render(path, self.painter, scene, self._az0(), size)
                if adopt:
                    ok, msg = set_wallpaper(path)
                    if ok:
                        for delay in (0.20, 0.70):
                            time.sleep(delay)
                            touch(path)
                else:
                    ok, msg = True, "桌面壁纸已更新"
                    time.sleep(0.60)      # 等 shell 处理内容变化，再补一次提醒
                    touch(path)
                to_main(done, ok, msg, slot)
            except Exception as exc:
                to_main(done, False, f"渲染壁纸失败：{exc}", slot)
            finally:
                self._busy = False
                self._lock.release()

        threading.Thread(target=work, daemon=True, name="chuang-wallpaper").start()

    def render_day(self, day: datetime, weather, show_info: bool, show_ribbon: bool,
                   size: tuple[int, int], frames: int, progress, done,
                   weather_off: bool = False, compact: bool = False,
                   inset: float = 0.0, scene_opts: dict | None = None) -> None:
        """渲染一整天的 48 帧并生成动态壁纸 XML。"""
        if not self._lock.acquire(blocking=False):
            return
        self._busy = True

        def work():
            from gi.repository import GLib
            # 回主线程的那一跳必须走高优先级的 idle（见 mainloop.py）：
            # 这一句放在线程里，和上面那行 GLib 导入同一个道理（沿用原来的姿势）
            from .mainloop import to_main
            try:
                # 画进"另一本相册"：桌面此刻正在看的那个目录一个字节都不动，
                # 于是中途失败最多是这次没换成，绝不会留下一张空桌面。
                active = active_frame_dir()
                stage = next((d for d in FRAME_DIRS if d != active), FRAME_DIRS[0])
                _clear_dir(stage)
                self.painter.ui.show_info = show_info
                self.painter.ui.info_compact = compact
                self.painter.ui.info_buttons = False      # 桌面上没有鼠标
                self.painter.ui.bottom_inset = float(inset or 0.0)
                self.painter.ui.show_ribbon = show_ribbon
                apply_scene_opts(self.painter, scene_opts)
                if show_ribbon:
                    self.painter.ui.ribbon = self.engine.ribbon(day, weather)
                    self.painter.ui.ribbon_surface = None
                step = 1440 // frames
                paths = []
                base = day.replace(hour=0, minute=0, second=0, microsecond=0)
                for i in range(frames):
                    when = base + timedelta(minutes=i * step)
                    scene = self.engine.build(when, weather, preview=False,
                                              location_label=self.label,
                                              weather_off=weather_off)
                    p = stage / f"frame-{i:02d}.png"
                    render(p, self.painter, scene, self._az0(), size)
                    paths.append(p)
                    if i % 4 == 0 or i == frames - 1:
                        to_main(progress, i + 1, frames)
                xml = write_day_xml(paths, day)
                ok, msg = apply_uri("file://" + quote(str(xml)))
                if ok:
                    _prune_frame_dirs(keep=stage)   # 新的生效了才清旧的
                to_main(done, ok, msg)
            except Exception as exc:
                to_main(done, False, f"生成动态壁纸失败：{exc}")
            finally:
                self._busy = False
                self._lock.release()

        threading.Thread(target=work, daemon=True, name="chuang-wallpaper-day").start()
