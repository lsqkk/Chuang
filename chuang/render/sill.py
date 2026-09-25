
"""窗台与那盆植物。

窗台（石头的台面、木框、太阳的光斑）是这一摊里最贵的一块，进离屏缓存；盆栽
**每帧现画**，因为叶子会随风摇（1.1.13 把植物从 `_paint_sill` 里搬了出来）。
影子按真正的投影算：株高 → 窗台平面上的 (横向, 纵向)，永远背光、越长越淡，画在
1/4 分辨率的离屏图上再放大回来（Cairo 没有模糊，缩小再双线性放大就是一次便宜的
高斯）。改这里之前先看 `tests/test_scene_options.py` 里那四条。
"""

from __future__ import annotations

import math
import time as _time

import cairo

from ..palette import mix_rgb, shade
from ..scene import Scene
from .core import PainterCore
from .paint import SILL_TOP, SILL_Y, TAU, clamp, lerp


class SillLayer(PainterCore):

    # ------------------------------------------------------------------
    # 窗台：受光、光斑（离屏缓存）；盆栽单独在 _draw_plant_layer 里现画
    # ------------------------------------------------------------------
    def _draw_sill(self, cr, w, h, scene: Scene, az0, fov, sun_x, direct,
                   hs=None):
        amb = self._ambient(scene)
        hs = hs or h      # 窗台的排版高度（底下那条留给系统面板）
        # 窗台 + 那盆植物也是静态的（只随光与太阳位置变），同样缓存。
        key = (int(w), int(h), int(hs), round(amb * 60), round(direct * 60),
               round(sun_x / 3.0), round(scene.sun_alt * 4),
               round(scene.sun_az * 4), round(fov))
        if not self._sill.stale(key, w, h):
            cr.set_source_surface(self._sill.surf, 0, 0)
            cr.paint()
            return
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, int(w), int(h))
        c2 = cairo.Context(surf)
        self._paint_sill(c2, w, h, scene, az0, fov, sun_x, direct, hs)
        self._sill.store(key, surf)
        cr.set_source_surface(surf, 0, 0)
        cr.paint()

    def _paint_sill(self, cr, w, h, scene: Scene, az0, fov, sun_x, direct,
                    hs=None):
        mu = scene.mood
        amb = self._ambient(scene)
        hs = hs or h
        y0 = SILL_Y * hs
        sill_h = hs - y0            # 露出来的台面（"景色"里的那一段）
        sh = h - y0                 # 一直到窗口最底下（含留给面板的余量）
        horizon01 = tuple(c / 255 for c in mu.horizon)

        # 窗框的下框：一条横在街与窗台之间的木框，把"窗外"和"窗里"分开
        rail_h = hs * 0.012
        frame = mix_rgb((0.20, 0.185, 0.175), horizon01, 0.16)
        frame = shade(frame, 0.26 + 0.66 * amb)
        day_tone = mix_rgb((0.54, 0.52, 0.49), horizon01, 0.22)
        night_tone = mix_rgb((0.045, 0.05, 0.08), horizon01, 0.40)
        lit = mix_rgb(night_tone, day_tone, amb ** 1.7)
        rg = cairo.LinearGradient(0, y0, 0, y0 + rail_h)
        rg.add_color_stop_rgb(0, *[c * 1.35 for c in frame])
        rg.add_color_stop_rgb(0.18, *[c * 1.10 for c in frame])
        rg.add_color_stop_rgb(0.55, *frame)
        rg.add_color_stop_rgb(1, *[c * 0.72 for c in frame])
        cr.set_source(rg)
        cr.rectangle(0, y0, w, rail_h)
        cr.fill()
        cr.set_source_rgba(0, 0, 0, 0.28)
        cr.rectangle(0, y0 + rail_h, w, max(1.0, hs * 0.0035))
        cr.fill()
        y0 = y0 + rail_h + max(1.0, hs * 0.0035)
        sill_h -= (y0 - SILL_Y * hs)
        sh = h - y0

        g = cairo.LinearGradient(0, y0, 0, h)
        g.add_color_stop_rgb(0, *[c * 1.10 for c in lit])
        g.add_color_stop_rgb(0.30, *lit)
        g.add_color_stop_rgb(1, *[c * 0.66 for c in lit])
        cr.set_source(g)
        cr.rectangle(0, y0, w, sh)
        cr.fill()
        # 窗框投在窗台上的阴影
        shadow = cairo.LinearGradient(0, y0, 0, y0 + sill_h * 0.55)
        shadow.add_color_stop_rgba(0, 0, 0, 0, 0.30)
        shadow.add_color_stop_rgba(0.45, 0, 0, 0, 0.10)
        shadow.add_color_stop_rgba(1, 0, 0, 0, 0)
        cr.set_source(shadow)
        cr.rectangle(0, y0, w, sill_h * 0.55)
        cr.fill()
        cr.set_source_rgba(1, 1, 1, 0.04 + 0.10 * amb)
        cr.rectangle(0, y0, w, 1.2)
        cr.fill()

        # 窗台是块石头：拉一点细纹与几道浅浅的石缝，免得是一整片平色
        grain_h = min(sh, hs * 0.10)
        cr.save()
        cr.rectangle(0, y0, w, grain_h)
        cr.clip()
        for i in range(26):
            frac = (i * 0.041) % 1.0
            seed = (i * 7919) % 997 / 997.0
            yy = y0 + grain_h * frac
            cr.set_source_rgba(0, 0, 0, 0.03 + 0.05 * seed)
            cr.rectangle(0, yy, w, 0.8 + 0.8 * seed)
            cr.fill()
        for i in range(4):
            xx = w * ((i + 0.5) / 4.0) + (i % 2) * w * 0.03
            cr.set_source_rgba(0, 0, 0, 0.05)
            cr.rectangle(xx, y0, max(0.8, w * 0.0012), grain_h)
            cr.fill()
        cr.restore()

        # 窗台上的光斑：太阳越低，光斑越宽越斜
        if direct > 0.02:
            d_rel = ((scene.sun_az - az0 + 180) % 360) - 180
            if abs(d_rel) < fov / 2 + 30:
                px = clamp(sun_x, -0.15 * w, 1.15 * w)
                low = clamp(1.0 - scene.sun_alt / 35.0, 0.0, 1.0)
                rx = w * lerp(0.20, 0.46, low)
                ry = sill_h * lerp(0.55, 1.25, low)
                py = y0 + sill_h * 0.42
                cr.save()
                cr.set_operator(cairo.OPERATOR_ADD)
                spot = cairo.RadialGradient(px, py, 0, px, py, max(rx, ry))
                gc = mu.glow_color
                a = 0.30 * direct
                spot.add_color_stop_rgba(0, gc[0] / 255, gc[1] / 255, gc[2] / 255, a)
                spot.add_color_stop_rgba(0.45, gc[0] / 255, gc[1] / 255, gc[2] / 255, a * 0.42)
                spot.add_color_stop_rgba(1, gc[0] / 255, gc[1] / 255, gc[2] / 255, 0)
                cr.save()
                cr.translate(px, py)
                cr.scale(rx / max(rx, ry), ry / max(rx, ry))
                cr.translate(-px, -py)
                cr.set_source(spot)
                cr.rectangle(0, y0, w, sh)
                cr.fill()
                cr.restore()
                cr.restore()

        # 台面纹理
        cr.set_source_rgba(0, 0, 0, 0.045)
        for i in range(1, 5):
            yy = y0 + sill_h * (i / 5.2)
            cr.rectangle(0, yy, w, 1)
            cr.fill()
        # 盆栽**不在这里画**：它会随风摇，得每帧现画（见 _draw_plant_layer）。
        # 窗台这一层才是贵的那块（石头纹理 + 光斑 + 木框），继续离屏缓存。

    def _plant_shapes(self, cr, x, y, scale, green=(0.20, 0.40, 0.26), flat=None):
        """在 (x, y) 画一盆小植物：y 是花盆底，scale 由窗口高度决定。

        flat 不为空时整株用同一个颜色填（画影子用）。
        """
        pot_w = 54.0 * scale
        pot_h = 30.0 * scale
        rim_w = pot_w * 1.14
        rim_h = 8.0 * scale
        top = y - pot_h

        # 花盆：上宽下窄，带一圈盆沿
        cr.new_path()
        cr.move_to(x - rim_w / 2, top - rim_h)
        cr.line_to(x + rim_w / 2, top - rim_h)
        cr.line_to(x + rim_w / 2, top)
        cr.line_to(x + pot_w / 2, top)
        cr.line_to(x + pot_w * 0.40, y)
        cr.line_to(x - pot_w * 0.40, y)
        cr.line_to(x - pot_w / 2, top)
        cr.line_to(x - rim_w / 2, top)
        cr.close_path()
        if flat is None:
            cr.save()
            cr.clip_preserve()
            pot = cairo.LinearGradient(x - rim_w / 2, 0, x + rim_w / 2, 0)
            pot.add_color_stop_rgb(0, 0.34, 0.25, 0.20)
            pot.add_color_stop_rgb(0.36, 0.56, 0.42, 0.33)
            pot.add_color_stop_rgb(0.66, 0.48, 0.35, 0.28)
            pot.add_color_stop_rgb(1, 0.27, 0.20, 0.17)
            cr.set_source(pot)
            cr.paint()
            # 盆沿下面的那一道暗，盆就有了厚度
            sh = cairo.LinearGradient(0, top, 0, top + rim_h * 0.9)
            sh.add_color_stop_rgba(0, 0, 0, 0, 0.30)
            sh.add_color_stop_rgba(1, 0, 0, 0, 0)
            cr.set_source(sh)
            cr.paint()
            # 盆身的竖向高光
            hl = cairo.LinearGradient(x - rim_w * 0.30, 0, x - rim_w * 0.02, 0)
            hl.add_color_stop_rgba(0, 1, 1, 1, 0)
            hl.add_color_stop_rgba(0.6, 1, 1, 1, 0.10)
            hl.add_color_stop_rgba(1, 1, 1, 1, 0)
            cr.set_source(hl)
            cr.paint()
            cr.restore()
            # 盆沿：上面一条亮线，下面一条暗线
            cr.set_source_rgba(1.0, 0.96, 0.90, 0.26)
            cr.rectangle(x - rim_w / 2, top - rim_h, rim_w, max(0.8, rim_h * 0.22))
            cr.fill()
            cr.set_source_rgba(0, 0, 0, 0.22)
            cr.rectangle(x - rim_w / 2, top - rim_h * 0.16, rim_w,
                         max(0.8, rim_h * 0.18))
            cr.fill()
            # 盆里的土
            soil = cairo.LinearGradient(x - rim_w / 2, 0, x + rim_w / 2, 0)
            soil.add_color_stop_rgb(0, 0.15, 0.11, 0.09)
            soil.add_color_stop_rgb(0.5, 0.26, 0.19, 0.14)
            soil.add_color_stop_rgb(1, 0.13, 0.10, 0.08)
            cr.set_source(soil)
            cr.rectangle(x - rim_w * 0.44, top - rim_h * 0.88,
                         rim_w * 0.88, max(1.2, rim_h * 0.34))
            cr.fill()
            for i, (fx, fs) in enumerate(((-0.24, 0.9), (0.04, 0.6), (0.26, 0.8))):
                cr.set_source_rgba(0.45, 0.40, 0.34, 0.55)
                cr.arc(x + rim_w * fx, top - rim_h * 0.68,
                       max(0.5, 1.3 * scale * fs), 0, TAU)
                cr.fill()
        else:
            cr.set_source_rgba(*flat)
            cr.fill_preserve()

        # 叶丛：一根主干 + 几根分枝，叶子大小、朝向都不一样
        base_y = top - rim_h * 0.55
        cr.save()
        cr.translate(x, base_y)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        trunk = 15.0 * scale
        if flat is None:                       # 露出土面的那一小段主干
            cr.set_source_rgba(*[min(1.0, c * 0.55) for c in green], 0.95)
            cr.set_line_width(max(1.0, 3.4 * scale))
            cr.move_to(0, 0)
            cr.line_to(0.6 * scale, -trunk)
            cr.stroke()
        # (角度, 柄长, 叶长, 叶宽, 明暗, 冷暖) —— 角度 -π/2 是正上方
        leaves = (
            (-2.86, 26, 20, 8, 0.74, -0.5), (-0.30, 24, 19, 8, 0.72, -0.5),
            (-2.52, 30, 26, 11, 0.82, -0.3), (-0.68, 28, 25, 10, 0.80, -0.3),
            (-2.16, 34, 32, 15, 0.90, 0.2), (-1.02, 32, 30, 14, 0.88, 0.2),
            (-1.86, 38, 40, 19, 0.98, 0.0), (-1.28, 36, 38, 18, 0.96, 0.0),
            (-2.40, 22, 34, 13, 0.86, -0.2), (-0.80, 20, 32, 12, 0.84, -0.2),
            (-1.57, 30, 30, 13, 0.92, 0.35), (-1.57, 42, 46, 20, 1.04, 0.0),
            (-1.10, 40, 26, 16, 0.94, 0.3), (-2.04, 42, 25, 15, 0.92, 0.3),
        )
        for i, (ang, stem_px, L, Wd, tone, hue) in enumerate(leaves):
            stem = stem_px * scale
            length = L * scale
            width = Wd * scale
            droop = 0.16 if i % 3 == 0 else 0.05
            leaf_col = green
            if abs(hue) > 1e-6:
                tgt = (0.42, 0.46, 0.20) if hue > 0 else (0.16, 0.34, 0.30)
                leaf_col = mix_rgb(green, tgt, abs(hue) * 0.5)
            bx = math.cos(ang) * stem
            by = -trunk + math.sin(ang) * stem + ((i % 3) - 1) * 2.2 * scale
            if flat is None:                     # 叶柄
                cr.set_source_rgba(*[min(1.0, c * 0.66) for c in leaf_col], 0.95)
                cr.set_line_width(max(0.8, 1.8 * scale))
                cr.move_to(0, -trunk)
                cr.line_to(bx, by)
                cr.stroke()
            cr.save()
            cr.translate(bx, by)
            cr.rotate(ang)
            if flat is not None:
                cr.set_source_rgba(*flat)
            cr.new_path()
            cr.move_to(0, 0)
            cr.curve_to(length * 0.26, -width * 0.64,
                        length * 0.76, -width * 0.40,
                        length * 0.99, droop * length * 0.9)
            cr.curve_to(length * 0.74, width * 0.42,
                        length * 0.26, width * 0.64, 0, 0)
            cr.close_path()
            if flat is None:
                lg = cairo.LinearGradient(0, 0, length, 0)
                c0 = shade(leaf_col, 0.58 * tone)
                c1 = shade(leaf_col, 1.24 * tone)
                lg.add_color_stop_rgb(0, *[min(1.0, c) for c in c0])
                lg.add_color_stop_rgb(0.55, *[min(1.0, c) for c in shade(leaf_col, tone)])
                lg.add_color_stop_rgb(1, *[min(1.0, c) for c in c1])
                cr.set_source(lg)
                cr.fill_preserve()
                # 叶脉
                cr.set_source_rgba(*[min(1.0, c * 1.3) for c in leaf_col], 0.28)
                cr.set_line_width(max(0.5, 0.9 * scale))
                cr.move_to(length * 0.06, 0)
                cr.line_to(length * 0.90, -droop * length * 0.7)
                cr.stroke()
            else:
                cr.fill()
            cr.restore()
        cr.restore()

    def _draw_plant_layer(self, cr, w, h, scene: Scene, az0, fov, direct, hs):
        """盆栽单独一层：**每帧现画**，所以它摇得起来。

        以前它与窗台一起烤在同一张离屏缓存里（那张图只随光变化），于是
        "随风摇曳"根本无从谈起。窗台才是真正贵的那块（石头纹理、光斑、木框），
        所以拆开来各归各的：窗台照旧缓存，植物每帧现画（十几条叶子路径）。
        传进来的 h 是**景色高度**（hs），和窗台排版用的是同一个高度。
        """
        if not self.ui.show_plant:
            return
        self._draw_plant(cr, w, h, scene, az0, direct)

    def _plant_sway(self, scene: Scene, az0: float) -> float:
        """这盆植物此刻弯成什么样：每往上一个像素，横向偏多少像素。

        风向是"来向"，所以风吹的方向是 +180°；屏幕上的横向分量取相对方位角的
        正弦（窗朝向 az0）。风越大弯得越多，另外叠一个很慢的摆动——一点风都没有
        的时候叶子也还在轻轻抖，不然窗台看着是死的。
        """
        t = _time.time()
        wave = (math.sin(t * 0.85 + 0.7) * 0.62 + math.sin(t * 2.30 + 2.1) * 0.38)
        if not (scene.has_weather and self.ui.plant_sway):
            # 没有风的数据（或用户关掉了摇曳）：只留一点点"呼吸"的抖动
            return 0.008 * wave
        wind = clamp(scene.wind_speed / 24.0, 0.0, 1.0) ** 0.85
        if wind <= 0.01:
            return 0.010 * wave
        rel = math.radians(((scene.wind_dir + 180.0 - az0 + 180.0) % 360.0) - 180.0)
        return math.sin(rel) * 0.17 * wind * (0.62 + 0.38 * wave) + 0.02 * wind * wave

    @staticmethod
    def _plant_shadow_geom(scene: Scene, az0: float, plant_h: float):
        """盆栽投在窗台上的那团影子：(横移, 下移, 浓度系数)，都按株高算好。

        光源在窗外很远的地方（太阳挂在城市的天空里），影子就落在**窗台这个
        水平面**上、朝观察者这一侧（屏幕下方）拉长；太阳偏东/偏西时再往那一侧
        的背光面斜过去。以前的写法只有一个横向的斜切：影子既没有向下落，
        太阳偏西时还会被推到画面外（x 偏移上千像素），看上去就是用户说的
        "影子朝上 / 干脆没有影子"。

        长度按 1/tan(高度角) 算，但**屏幕上要收着画**：真按物理比例来，低太阳
        下那团影子会长到横穿整扇窗（窗台在画面里只有百来像素深）。所以这里等比
        收进"半个株宽的横向、大半株高的纵向"，并且影子越长越淡。
        """
        d_rel = ((scene.sun_az - az0 + 180) % 360) - 180
        alt = max(scene.sun_alt, 5.0)
        ratio = clamp(1.0 / math.tan(math.radians(alt)), 0.20, 2.6)
        dx = -math.sin(math.radians(d_rel)) * ratio * 0.90 * plant_h
        dy = math.cos(math.radians(d_rel)) * ratio * 0.62 * plant_h
        scale = min(1.0, 0.62 * plant_h / max(1e-6, abs(dx)),
                    0.85 * plant_h / max(1e-6, dy))
        fade = clamp(1.15 - 0.42 * ratio, 0.42, 1.0)
        return dx * scale, dy * scale, fade

    def _draw_plant(self, cr, w, h, scene: Scene, az0, direct):
        mu = scene.mood
        x = 0.115 * w
        y = 0.918 * h
        s = clamp(h / 700.0, 0.72, 1.7)
        plant_h = 104.0 * s
        sway = self._plant_sway(scene, az0)
        dx, dy, fade = self._plant_shadow_geom(scene, az0, plant_h)
        kx, ky = dx / plant_h, dy / plant_h

        # 环境遮光（花盆底下那圈）：贴着盆底，影子才有"落"在台面上的感觉
        cr.save()
        soft = cairo.RadialGradient(x, y, 0, x, y, 46 * s)
        soft.add_color_stop_rgba(0, 0, 0, 0, 0.34)
        soft.add_color_stop_rgba(1, 0, 0, 0, 0)
        cr.set_source(soft)
        cr.arc(x, y, 46 * s, 0, TAU)
        cr.fill()
        cr.restore()

        # 真实的影子：方向与长短由太阳方位角、高度角决定（见 _plant_shadow_geom）
        if direct > 0.03:
            # 影子画在 1/4 分辨率的离屏图上再放大回去：Cairo 没有模糊，
            # 但"缩小再双线性放大"就是一次廉价的高斯模糊——植物的影子本来就
            # 是软的（叶子之间全是缝），画成硬边的剪影会像水里的倒影。
            ss = 0.25
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32,
                                      max(2, int(w * ss) + 2), max(2, int(h * ss) + 2))
            scr = cairo.Context(surf)
            scr.scale(ss, ss)
            shadow_col = mix_rgb((0.09, 0.08, 0.10),
                                 tuple(c / 255 for c in mu.horizon), 0.35)
            for k, a in ((0.94, 0.40), (1.02, 0.28), (1.12, 0.18), (1.24, 0.10)):
                # 投影：株高 u → 台面上的 (kx·u, ky·u)。纵向是"镜像"的——
                # 叶子长在盆上方，影子落在盆前方；横向再叠上随风的那一点弯。
                scr.save()
                scr.transform(cairo.Matrix(
                    xx=1.0, yx=0.0,
                    xy=-(kx + sway) * k, yy=-ky * k,
                    x0=(kx + sway) * k * y, y0=(1.0 + ky * k) * y))
                self._plant_shapes(scr, x, y, s,
                                   flat=(shadow_col[0], shadow_col[1], shadow_col[2],
                                         a * fade * direct))
                scr.restore()
            # 影子只落在窗台上（窗框之下那一片），别糊到街上/花盆上面去
            cr.save()
            cr.rectangle(0, SILL_TOP * h, w, h - SILL_TOP * h)
            cr.clip()
            cr.scale(1.0 / ss, 1.0 / ss)
            pat = cairo.SurfacePattern(surf)
            pat.set_filter(cairo.FILTER_GOOD)
            cr.set_source(pat)
            cr.paint()
            cr.restore()

        # 本体：绕盆底（有叶子的那一段）做一个轻微的斜切——风往哪边吹就往哪边弯
        cr.save()
        if abs(sway) > 1e-4:
            cr.transform(cairo.Matrix(xx=1.0, yx=0.0, xy=-sway, yy=1.0,
                                      x0=sway * y, y0=0.0))
        self._draw_plant_body(cr, w, h, x, y, s, scene, az0)
        cr.restore()

    def _draw_plant_body(self, cr, w, h, x, y, s, scene: Scene, az0):
        mu = scene.mood
        amb = self._ambient(scene)
        green = mix_rgb((0.20, 0.40, 0.26), tuple(c / 255 for c in mu.horizon),
                        0.45 * (1.0 - amb))
        green = shade(green, 0.34 + 0.66 * amb)
        cr.save()
        self._plant_shapes(cr, x, y, s, green)
        cr.restore()
        # 受光面：朝太阳那一侧的叶子亮一点
        d_rel = ((scene.sun_az - az0 + 180) % 360) - 180
        light = clamp(mu.ambient * 0.9, 0.0, 1.0)
        if light > 0.05:
            cr.save()
            cr.translate(x, y)
            gl = cairo.RadialGradient(
                math.sin(math.radians(d_rel)) * 34 * s,
                -58 * s, 0, 0, -58 * s, 120 * s)
            gc = mu.glow_color
            gl.add_color_stop_rgba(0, gc[0] / 255, gc[1] / 255, gc[2] / 255, 0.16 * light)
            gl.add_color_stop_rgba(1, gc[0] / 255, gc[1] / 255, gc[2] / 255, 0)
            cr.set_source(gl)
            cr.arc(0, -58 * s, 120 * s, 0, TAU)
            cr.fill()
            cr.restore()
