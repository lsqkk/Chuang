
"""天空这一层：天色、星星、太阳、月亮、云、飞机，以及下在窗外的那场雨雪。

雨雪（`_draw_precip` / `_draw_flash`）也在这里，因为它们是"天上的事"；但**画的
次序**由 `painter.draw()` 决定——排在窗台、玻璃反光与盆栽**之前**（雨是下在窗外
的，见 AGENTS.md §3.6）。天色与云各自有离屏缓存（`self._sky` / `self._cloud`），
键里带了光与尺寸。
"""

from __future__ import annotations

import math

import cairo

from ..palette import mix
from ..scene import Scene
from .core import PainterCore
from .paint import (ALT_TOP, GROUND_TOP, HORIZON_Y, SILL_Y, TAU, clamp, lerp,
                    noise_tile, scene_height, x_for_az, y_for_alt)


class SkyLayer(PainterCore):

    # ------------------------------------------------------------------
    # 天空
    # ------------------------------------------------------------------
    def _draw_sky(self, cr, w, h, scene: Scene, glow_x, sun_y):
        key = (int(w), int(h), round(scene.sun_alt * 3), round(scene.sun_az * 1.5),
               round((scene.cloud if scene.has_weather else 0) / 5),
               scene.fog, scene.mood.key)
        if self._sky.stale(key, w, h):
            self._sky.store(key, self._render_sky(int(w), int(h), scene, glow_x, sun_y))
        cr.set_source_surface(self._sky.surf, 0, 0)
        cr.paint()

    def _render_sky(self, w: int, h: int, scene: Scene, glow_x, sun_y):
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(surf)
        mu = scene.mood
        horizon_y = HORIZON_Y * h
        grad = cairo.LinearGradient(0, 0, 0, horizon_y)
        grad.add_color_stop_rgb(0.00, *[c / 255 for c in mu.zenith])
        z2 = mix(mu.zenith, mu.horizon, 0.12)
        grad.add_color_stop_rgb(0.44, *[c / 255 for c in z2])
        z3 = mix(mu.zenith, mu.horizon, 0.30)
        grad.add_color_stop_rgb(0.68, *[c / 255 for c in z3])
        z4 = mix(mu.zenith, mu.horizon, 0.58)
        grad.add_color_stop_rgb(0.88, *[c / 255 for c in z4])
        grad.add_color_stop_rgb(1.00, *[c / 255 for c in mu.horizon])
        cr.set_source(grad)
        cr.rectangle(0, 0, w, horizon_y + 1)
        cr.fill()

        # 地平线以下（被剪影和窗台挡住之前）先铺一层暗色
        below = cairo.LinearGradient(0, horizon_y, 0, h)
        below.add_color_stop_rgb(0, *[c / 255 for c in mu.horizon])
        dark = mix(mu.horizon, (4, 5, 10), 0.93)
        dark = tuple(c * (0.35 + 0.55 * clamp(mu.ambient, 0, 1)) for c in dark)
        below.add_color_stop_rgb(0.35, *[c / 255 for c in dark])
        below.add_color_stop_rgb(1, 0.015, 0.016, 0.025)
        cr.set_source(below)
        cr.rectangle(0, horizon_y, w, h - horizon_y)
        cr.fill()

        # 太阳一方的辉光
        strength = mu.glow_strength * (1.0 - 0.7 * clamp(scene.cloud / 100.0, 0, 1))
        if strength > 0.01:
            radius = max(w, h) * lerp(0.80, 0.42, clamp(strength, 0, 1))
            gx = glow_x
            gy = clamp(sun_y, -0.1 * h, horizon_y + 0.02 * h)
            rad = cairo.RadialGradient(gx, gy, 0.0, gx, gy, radius)
            gc = [c / 255 for c in mu.glow_color]
            rad.add_color_stop_rgba(0.00, gc[0], gc[1], gc[2], 0.46 * strength)
            rad.add_color_stop_rgba(0.24, gc[0], gc[1], gc[2], 0.22 * strength)
            rad.add_color_stop_rgba(0.58, gc[0], gc[1], gc[2], 0.07 * strength)
            rad.add_color_stop_rgba(1.00, gc[0], gc[1], gc[2], 0.0)
            cr.save()
            cr.set_operator(cairo.OPERATOR_ADD)
            cr.set_source(rad)
            cr.rectangle(0, 0, w, horizon_y + 1)
            cr.fill()
            cr.restore()

        # 贴近地平线的一层空气雾
        haze_h = h * 0.16
        haze = cairo.LinearGradient(0, horizon_y - haze_h, 0, horizon_y)
        hz = mix(mu.horizon, mu.glow_color, 0.35)
        haze.add_color_stop_rgba(0, hz[0] / 255, hz[1] / 255, hz[2] / 255, 0.0)
        haze.add_color_stop_rgba(1, hz[0] / 255, hz[1] / 255, hz[2] / 255, 0.22)
        cr.set_source(haze)
        cr.rectangle(0, horizon_y - haze_h, w, haze_h)
        cr.fill()

        if scene.has_weather and scene.fog:
            cr.set_source_rgba(*[c / 255 for c in mix(mu.horizon, (200, 205, 210), 0.5)], 0.42)
            cr.rectangle(0, 0, w, h)
            cr.fill()

        # 阴天：整片天空被云幕盖住，太阳只剩下一个隐约的亮斑
        cover = clamp(scene.cloud / 100.0, 0, 1) if scene.has_weather else 0.0
        if cover > 0.55:
            veil = (cover - 0.55) / 0.45
            if scene.sun_alt > 12:
                tone = (200, 207, 216)
            elif scene.sun_alt > 1:
                tone = (146, 136, 132)
            elif scene.sun_alt > -8:
                tone = (84, 76, 90)
            else:
                tone = (28, 32, 44)
            vg = cairo.LinearGradient(0, 0, 0, horizon_y)
            vg.add_color_stop_rgba(0, tone[0] / 255, tone[1] / 255, tone[2] / 255,
                                   0.93 * veil)
            vg.add_color_stop_rgba(0.7, tone[0] / 255, tone[1] / 255, tone[2] / 255,
                                   0.90 * veil)
            vg.add_color_stop_rgba(1, tone[0] / 255, tone[1] / 255, tone[2] / 255,
                                   0.78 * veil)
            cr.set_source(vg)
            cr.rectangle(0, 0, w, horizon_y)
            cr.fill()

        # 最后抖一层极淡的噪声：把渐变在 8 位色深上的色带打散（见 noise_tile）。
        # 夜色越深、渐变越长，色带越明显，所以这一笔整片天空都要盖。
        pat = cairo.SurfacePattern(noise_tile())
        pat.set_extend(cairo.EXTEND_REPEAT)
        cr.set_source(pat)
        cr.rectangle(0, 0, w, h)
        cr.fill()
        return surf

    # ------------------------------------------------------------------
    def _draw_stars(self, cr, w, h, scene: Scene, az0, fov):
        mu = scene.mood
        if mu.star_alpha <= 0.02 or not scene.stars.points:
            return
        if scene.has_weather and scene.cloud > 78:      # 厚云下星星本来也看不见
            return
        haze = 1.0 - 0.75 * clamp(scene.cloud / 100.0, 0, 1) if scene.has_weather else 1.0
        alpha_base = mu.star_alpha * haze
        if alpha_base <= 0.02:
            return
        t = self._lit_phase
        cr.save()
        for az, alt, mag, rgb, phase in scene.stars.points:
            x, d = x_for_az(az, az0, fov, w)
            if d < -fov / 2 - 2 or d > fov / 2 + 2:
                continue
            y = y_for_alt(alt, h)
            flux = 10 ** (-0.4 * mag)
            base = clamp(flux / 0.265, 0.0, 1.0) ** 0.42
            a = clamp(0.08 + 0.92 * base, 0, 1) * alpha_base
            if a <= 0.012:
                continue
            tw = 1.0 + 0.28 * math.sin(t * 2.7 + phase) * (1.0 - alt / 90.0)
            a = clamp(a * tw, 0, 1)
            r = 0.6 + 1.35 * base
            cr.set_source_rgba(rgb[0], rgb[1], rgb[2], a)
            cr.arc(x, y, r, 0, TAU)
            cr.fill()
            if base > 0.85:
                g = cairo.RadialGradient(x, y, 0, x, y, r * 4.2)
                g.add_color_stop_rgba(0, rgb[0], rgb[1], rgb[2], a * 0.34)
                g.add_color_stop_rgba(1, rgb[0], rgb[1], rgb[2], 0)
                cr.set_source(g)
                cr.arc(x, y, r * 4.2, 0, TAU)
                cr.fill()
        cr.restore()

    # ------------------------------------------------------------------
    def _draw_sun(self, cr, w, h, scene: Scene, sun_x, sun_d, fov, direct):
        if sun_d < -fov / 2 - 6 or sun_d > fov / 2 + 6:
            return
        if scene.sun_alt < -1.2:
            return
        cover = clamp(scene.cloud / 100.0, 0, 1) if scene.has_weather else 0.0
        vis = clamp(1.0 - 0.92 * cover ** 1.25, 0.05, 1.0)
        x = clamp(sun_x, -0.1 * w, 1.1 * w)
        y = y_for_alt(scene.sun_alt, h)
        r = max(6.0, w * 0.0060)
        # 高度越低，太阳越大越红
        low = clamp(1.0 - scene.sun_alt / 18.0, 0.0, 1.0)
        r *= (1.0 + 0.5 * low)
        warm = mix(scene.mood.glow_color, (255, 246, 226), 0.55 - 0.5 * low)
        cr.save()
        cr.set_operator(cairo.OPERATOR_ADD)
        halo = cairo.RadialGradient(x, y, r * 0.6, x, y, r * 9)
        halo.add_color_stop_rgba(0, warm[0] / 255, warm[1] / 255, warm[2] / 255, 0.34 * vis)
        halo.add_color_stop_rgba(0.22, warm[0] / 255, warm[1] / 255, warm[2] / 255, 0.13 * vis)
        halo.add_color_stop_rgba(1, warm[0] / 255, warm[1] / 255, warm[2] / 255, 0)
        cr.set_source(halo)
        cr.arc(x, y, r * 9, 0, TAU)
        cr.fill()
        disc = cairo.RadialGradient(x, y, 0, x, y, r)
        disc.add_color_stop_rgba(0, 1, 0.99, 0.95,
                                 (0.75 + 0.25 * min(1.0, direct + 0.4)) * vis)
        disc.add_color_stop_rgba(0.7, warm[0] / 255, warm[1] / 255, warm[2] / 255,
                                 0.95 * vis)
        disc.add_color_stop_rgba(1, warm[0] / 255, warm[1] / 255, warm[2] / 255, 0.0)
        cr.set_source(disc)
        cr.arc(x, y, r * 1.15, 0, TAU)
        cr.fill()
        cr.restore()

    # ------------------------------------------------------------------
    def _draw_moon(self, cr, w, h, scene: Scene, az0, fov):
        if scene.moon_alt < -1.0:
            return
        x, d = x_for_az(scene.moon_az, az0, fov, w)
        if d < -fov / 2 - 6 or d > fov / 2 + 6:
            return
        x = clamp(x, -0.1 * w, 1.1 * w)
        y = y_for_alt(scene.moon_alt, h)
        r = max(5.5, w * 0.0068)
        illum = scene.moon_illum
        darkness = clamp((-scene.sun_alt - 1) / 12.0, 0.0, 1.0)
        vis = clamp(0.35 + 0.65 * illum, 0, 1) * clamp((scene.moon_alt + 2) / 10.0, 0, 1)
        vis *= 1.0 - 0.6 * clamp(scene.cloud / 100.0, 0, 1) if scene.has_weather else 1.0

        cr.save()
        if vis > 0.02:
            cr.set_operator(cairo.OPERATOR_ADD)
            halo_r = r * lerp(3.0, 11.0, illum) * (0.5 + 0.5 * darkness)
            halo = cairo.RadialGradient(x, y, r * 0.8, x, y, halo_r)
            halo.add_color_stop_rgba(0, 0.86, 0.90, 1.0, 0.26 * vis)
            halo.add_color_stop_rgba(0.35, 0.80, 0.86, 1.0, 0.10 * vis)
            halo.add_color_stop_rgba(1, 0.80, 0.86, 1.0, 0)
            cr.set_source(halo)
            cr.arc(x, y, halo_r, 0, TAU)
            cr.fill()
            cr.set_operator(cairo.OPERATOR_OVER)

        # 月面：先画满月的圆，再用晨昏线切出暗面
        cr.save()
        cr.translate(x, y)
        face = cairo.RadialGradient(-r * 0.25, -r * 0.25, r * 0.1, 0, 0, r)
        face.add_color_stop_rgb(0, 1.0, 0.98, 0.92)
        face.add_color_stop_rgb(0.75, 0.94, 0.92, 0.86)
        face.add_color_stop_rgb(1, 0.82, 0.82, 0.80)
        cr.set_source(face)
        cr.arc(0, 0, r, 0, TAU)
        cr.fill()

        v = scene.moon_bright
        right = (math.sin(math.radians(az0 + 90)), math.cos(math.radians(az0 + 90)), 0.0)
        sx = v[0] * right[0] + v[1] * right[1] + v[2] * right[2]
        sy = v[2]
        ang = math.atan2(-sy, sx) if (abs(sx) + abs(sy)) > 1e-6 else 0.0
        cr.rotate(ang)
        cos_i = 2.0 * illum - 1.0
        kx = -r * cos_i          # 亮面在 +x 方向：满月时晨昏线退到 -r，新月时鼓到 +r
        K = 0.5523
        cr.new_path()
        cr.arc(0, 0, r, math.pi / 2, 3 * math.pi / 2)
        cr.curve_to(kx * K, -r, kx, -r * K, kx, 0)
        cr.curve_to(kx, r * K, kx * K, r, 0, r)
        cr.close_path()
        cr.set_source_rgba(0.045, 0.055, 0.095, 0.93)
        cr.fill()
        cr.restore()
        cr.restore()

    # ------------------------------------------------------------------
    def _cloud_tint(self, scene: Scene) -> str:
        alt = scene.sun_alt
        if alt > 12:
            return "day"
        if alt > 1:
            return "warm"
        if alt > -7:
            return "dusk"
        return "night"

    def _draw_clouds(self, cr, w, h, scene: Scene, az0, fov):
        if not self.fx.clouds:
            return
        tint = self._cloud_tint(scene)
        sprites = self.sprites.tinted[tint]
        moonlit = clamp(scene.moon_light * 0.35, 0, 0.35)
        # 云画在 1/3 分辨率的离屏层上再放大——既快，边缘也更柔
        s = 0.36
        lw = max(1, int(w * s))
        lh = max(1, int((HORIZON_Y * h + h * 0.06) * s))
        if self._cloud.stale((int(w), int(h)), lw, lh):
            self._cloud.store((int(w), int(h)),
                              cairo.ImageSurface(cairo.FORMAT_ARGB32, lw, lh))
        surf = self._cloud.surf
        lcr = cairo.Context(surf)
        lcr.set_operator(cairo.OPERATOR_SOURCE)
        lcr.set_source_rgba(0, 0, 0, 0)
        lcr.paint()
        lcr.set_operator(cairo.OPERATOR_OVER)
        lcr.scale(s, s)
        lcr.save()
        for c in self.fx.clouds:
            x = c["x"] * w
            y = y_for_alt(c["alt"], h) + math.sin(self._lit_phase * 0.06 + c["wobble"]) * 3
            scale = c["scale"] * (w / 1100.0) * 3.3
            alpha = clamp(c["alpha"], 0, 1)
            if tint == "night":
                alpha *= 0.85
            lcr.save()
            lcr.translate(x, y)
            lcr.scale(scale, scale * 0.58)
            pat = cairo.SurfacePattern(sprites[c["shape"]])
            pat.set_filter(cairo.FILTER_BILINEAR)
            lcr.set_source(pat)
            lcr.paint_with_alpha(alpha)
            lcr.restore()
            if moonlit > 0.02 and tint == "night":
                lcr.save()
                lcr.translate(x, y - 6 * scale)
                lcr.scale(scale * 0.8, scale * 0.45)
                pat = cairo.SurfacePattern(sprites[c["shape"]])
                pat.set_filter(cairo.FILTER_BILINEAR)
                lcr.set_source(pat)
                lcr.paint_with_alpha(moonlit * alpha)
                lcr.restore()
        lcr.restore()
        cr.save()
        cr.translate(0, 0)
        cr.scale(1 / s, 1 / s)
        pat = cairo.SurfacePattern(surf)
        pat.set_filter(cairo.FILTER_BILINEAR)
        cr.set_source(pat)
        cr.rectangle(0, 0, lw, lh)
        cr.fill()
        cr.restore()

    # ------------------------------------------------------------------
    def _draw_plane(self, cr, w, h, scene: Scene):
        p = self.fx.plane
        if p is None:
            return
        x, y = p["x"] * w, y_for_alt(p["alt"], h)
        night = scene.sun_alt < -2
        cr.save()
        if night:
            blink = (math.sin(p["blink"] * 3.0) + 1) * 0.5
            body_a = 0.22 + 0.2 * blink
            cr.set_source_rgba(0.85, 0.88, 0.95, body_a)
            cr.rectangle(x - 5, y - 1.0, 10, 2.0)
            cr.fill()
            if blink > 0.55:
                cr.set_source_rgba(1.0, 0.42, 0.32, 0.55 * blink)
                cr.arc(x + 6, y - 3.2, 1.6, 0, TAU)
                cr.fill()
                g = cairo.RadialGradient(x + 6, y - 3.2, 0, x + 6, y - 3.2, 7)
                g.add_color_stop_rgba(0, 1.0, 0.45, 0.35, 0.35 * blink)
                g.add_color_stop_rgba(1, 1.0, 0.45, 0.35, 0)
                cr.set_source(g)
                cr.arc(x + 6, y - 3.2, 7, 0, TAU)
                cr.fill()
        else:
            cr.set_source_rgba(0.35, 0.38, 0.44, 0.45)
            cr.rectangle(x - 6, y - 1.1, 12, 2.2)
            cr.fill()
            trail = cairo.LinearGradient(x - 60, y, x - 6, y)
            trail.add_color_stop_rgba(0, 1, 1, 1, 0)
            trail.add_color_stop_rgba(1, 1, 1, 1, 0.18 * p["trail"])
            cr.set_source(trail)
            cr.rectangle(x - 60, y - 1.0, 54, 2.0)
            cr.fill()
        cr.restore()

    # ------------------------------------------------------------------
    # 降水与闪电
    # ------------------------------------------------------------------
    def _draw_precip(self, cr, w, h, scene: Scene):
        if not self.fx.drops and not self.fx.flakes:
            return
        mu = scene.mood
        darkness = clamp((-scene.sun_alt) / 12.0, 0.0, 1.0)
        strength = clamp(scene.precip_strength, 0.0, 1.0)
        # 白天雨丝其实比天空亮（迎着光能看见），夜里才发暗——以前一条公式
        # 走到底，白天那点透明度让雨几乎看不见，于是"雨很糙"也看不出层次。
        drop_col = mix((246, 250, 255), mu.horizon, 0.34 * (1.0 - darkness))
        base_a = 0.32 + 0.40 * darkness
        # 雨幕：雨大到一定程度，整幅画面上蒙一层水汽（雨越大越明显）
        if scene.precip_kind == "rain" and strength > 0.30:
            veil = cairo.LinearGradient(0, 0, 0, h)
            vc = mix(mu.horizon, (198, 210, 232), 0.45)
            a = 0.035 + 0.075 * strength
            veil.add_color_stop_rgba(0, vc[0] / 255, vc[1] / 255, vc[2] / 255,
                                     a * 0.55)
            veil.add_color_stop_rgba(0.62, vc[0] / 255, vc[1] / 255, vc[2] / 255, a)
            veil.add_color_stop_rgba(1, vc[0] / 255, vc[1] / 255, vc[2] / 255,
                                     a * 1.25)
            cr.set_source(veil)
            cr.rectangle(0, 0, w, h)
            cr.fill()
        cr.save()
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        wind = scene.wind_speed if scene.has_weather else 0.0
        to_dir = (scene.wind_dir + 180.0) % 360.0
        slant = math.sin(math.radians(((to_dir - 180 + 180) % 360) - 180)) * wind / 26.0
        # 雨丝的长度随雨量走：毛毛雨是 3-8 个像素的小斜线，暴雨才是长划子
        len_scale = h * (0.0045 + 0.019 * strength)
        # 一场大雨一帧有几百根雨丝，一根一次 stroke 是这一摊里最贵的一笔。
        # 把"粗细 + 透明度"量化之后分桶，每桶合成一条路径只 stroke 一次——
        # 几十次调用，画出来看不出差别。
        buckets: dict = {}
        for x, y, ln, _speed, a, thick, phase in self.fx.drops:
            px, py = x * w, y * h
            L = len_scale * ln * (0.62 + 0.38 * a)
            dx = slant * L * 0.8
            # 迎着光的那一侧亮一点，雨丝不那么"平"
            alpha = a * base_a * (0.82 + 0.18 * math.sin(phase))
            key = (round(thick * 4), round(alpha * 10))
            buckets.setdefault(key, []).append((px, py, dx, L))
        for (thick_q, alpha_q), segs in buckets.items():
            cr.set_source_rgba(drop_col[0] / 255, drop_col[1] / 255, drop_col[2] / 255,
                               max(0.03, alpha_q / 10.0))
            cr.set_line_width(max(0.55, (thick_q / 4.0 + 0.15) * w / 1500.0))
            for px, py, dx, L in segs:
                cr.move_to(px, py)
                cr.line_to(px + dx, py + L)
            cr.stroke()
        # 路面上溅起的小涟漪：一圈圈往外散开又淡掉
        if self.fx.splashes:
            hs = scene_height(h)
            # 落在**窗外那条马路**上（窗台与窗框在它前面，由绘制的先后顺序挡掉）
            road_y = (GROUND_TOP + 0.010) * hs
            road_h = max(1.0, (SILL_Y - GROUND_TOP - 0.022) * hs)
            rc = mix(drop_col, (255, 255, 255), 0.35)
            for x, ph, size in self.fx.splashes:
                px = x * w
                py = road_y + ph * road_h
                grow = 0.35 + 1.9 * (1.0 - abs(0.5 - ph) * 2.0)
                rr = max(1.0, size * grow * (0.9 + 0.6 * strength) * w / 480.0)
                fade = clamp(1.0 - ph * 1.15, 0.0, 1.0) ** 1.4
                cr.set_source_rgba(rc[0] / 255, rc[1] / 255, rc[2] / 255,
                                   fade * (0.38 + 0.40 * strength))
                cr.set_line_width(max(0.9, w / 1500.0))
                cr.save()
                cr.translate(px, py)
                cr.scale(1.0, 0.34)             # 俯视的一圈：压扁成路面上的椭圆
                cr.arc(0, 0, rr, 0, TAU)
                cr.restore()
                cr.stroke()
        for x, y, r_, radius, phase, a in self.fx.flakes:
            px, py = x * w, y * h
            rr = max(0.9, radius * w / 1500.0)
            cr.set_source_rgba(0.96, 0.97, 1.0, a * base_a)
            cr.arc(px, py, rr, 0, TAU)
            cr.fill()
        cr.restore()

    def _draw_flash(self, cr, w, h, scene: Scene):
        f = self.fx.flash
        if f <= 0.01:
            return
        v = clamp(f * f, 0, 1)
        cr.set_source_rgba(0.88, 0.92, 1.0, 0.34 * v)
        cr.rectangle(0, 0, w, h)
        cr.fill()
