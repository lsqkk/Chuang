
"""地面这一层：楼前那条路（远人行道 → 马路 → 近人行道）、街上的人车、天际线剪影，
以及压在整幅画上的暗角与玻璃反光。

建筑投在路面上的影子由 `city.ground_shadow` 按真实的太阳方位与高度角画（每栋楼一个
斜的平行四边形，见 AGENTS.md §3.6）；街上的人车交给 `street/`，它们用的是**正在
走动的墙钟**——拖时间轴时天色停住、人车照走，不会冻成一张照片。
"""

from __future__ import annotations

import cairo

from .. import city as _city
from .. import street
from ..palette import mix, mix_rgb, shade
from ..scene import Scene
from .core import PainterCore
from .paint import (GROUND_TOP, HORIZON_Y, ROAD_BOTTOM, SILL_Y, TAU, clamp,
                    x_for_az)


class GroundLayer(PainterCore):

    # ------------------------------------------------------------------
    # 地面：楼前的人行道、马路、建筑投在路面上的影子
    # ------------------------------------------------------------------
    def _draw_ground(self, cr, w, h, scene: Scene, az0, fov, light) -> None:
        """把楼底下那条路面画出来：远人行道 → 马路 → 近人行道。"""
        layers = self._layers(scene)
        mu = scene.mood
        amb = clamp(light.ambient, 0.0, 1.0)
        y_kerb_far = GROUND_TOP * h
        y_road_top = (GROUND_TOP + 0.006) * h
        y_road_bot = ROAD_BOTTOM * h
        y_kerb_near = SILL_Y * h
        horizon01 = tuple(c / 255 for c in mu.horizon)

        # 路面：整体是被天光点亮的中性灰，夜里退成很暗的蓝灰
        road_day = mix_rgb((0.23, 0.23, 0.235), horizon01, 0.20)
        road_night = mix_rgb((0.045, 0.048, 0.062), horizon01, 0.32)
        road = mix_rgb(road_night, road_day, amb ** 1.35)
        g = cairo.LinearGradient(0, y_road_top, 0, y_kerb_near)
        g.add_color_stop_rgb(0, *[c * 1.14 for c in road])
        g.add_color_stop_rgb(0.5, *road)
        g.add_color_stop_rgb(1, *[c * 0.86 for c in road])
        cr.set_source(g)
        cr.rectangle(0, y_road_top, w, y_kerb_near - y_road_top)
        cr.fill()

        # 阳光斜着铺在路面上：太阳那一侧更暖更亮
        if light.direct > 0.02:
            sun_x = x_for_az(scene.sun_az, az0, fov, w)[0]
            cx = clamp(sun_x, -0.4 * w, 1.4 * w)
            rg = cairo.RadialGradient(cx, y_road_top, 0, cx, y_road_top,
                                      max(w * 0.75, h * 0.5))
            gc = mu.glow_color
            a = 0.11 * light.direct
            rg.add_color_stop_rgba(0, gc[0] / 255, gc[1] / 255, gc[2] / 255, a)
            rg.add_color_stop_rgba(0.55, gc[0] / 255, gc[1] / 255, gc[2] / 255, a * 0.35)
            rg.add_color_stop_rgba(1, gc[0] / 255, gc[1] / 255, gc[2] / 255, 0)
            cr.save()
            cr.set_operator(cairo.OPERATOR_ADD)
            cr.set_source(rg)
            cr.rectangle(0, y_road_top, w, y_kerb_near - y_road_top)
            cr.fill()
            cr.restore()

        # 建筑投在路面上的影子（太阳低就长、云厚就淡）
        ground_shadow_h = y_kerb_near - y_road_top
        _city.ground_shadow(cr, w, h, layers, light, y_road_top, ground_shadow_h)

        # 马路靠近楼的那一条暗（楼把光挡住了）
        ao = cairo.LinearGradient(0, y_road_top, 0, y_road_top + h * 0.016)
        ao.add_color_stop_rgba(0, 0, 0, 0, 0.34 + 0.14 * amb)
        ao.add_color_stop_rgba(1, 0, 0, 0, 0)
        cr.set_source(ao)
        cr.rectangle(0, y_road_top, w, h * 0.016)
        cr.fill()

        # 远侧人行道（楼根那一小条，被天光照得比路面亮）
        kerb_day = mix_rgb((0.36, 0.35, 0.34), horizon01, 0.28)
        kerb_night = mix_rgb((0.075, 0.080, 0.098), horizon01, 0.38)
        kerb = mix_rgb(kerb_night, kerb_day, amb ** 1.3)
        cr.set_source_rgb(*[clamp(c, 0, 1) for c in kerb])
        cr.rectangle(0, y_kerb_far, w, y_road_top - y_kerb_far)
        cr.fill()
        cr.set_source_rgba(0, 0, 0, 0.28)
        cr.rectangle(0, y_road_top - max(1.0, h * 0.0015), w, max(1.0, h * 0.0015))
        cr.fill()

        # 近侧人行道（窗下这一条，行人在上面走）
        kerb2 = shade(kerb, 1.10)
        cr.set_source_rgb(*[clamp(c, 0, 1) for c in kerb2])
        cr.rectangle(0, y_road_bot, w, y_kerb_near - y_road_bot)
        cr.fill()
        cr.set_source_rgba(1, 1, 1, 0.10 + 0.10 * amb)
        cr.rectangle(0, y_road_bot, w, max(1.0, h * 0.0016))
        cr.fill()
        cr.set_source_rgba(0, 0, 0, 0.20)
        cr.rectangle(0, y_road_bot + max(1.0, h * 0.0016), w, max(1.0, h * 0.0016))
        cr.fill()

        # 车道中心那一点点标线：只在光够的时候露出来
        if amb > 0.25:
            y_mid = (y_road_top + y_road_bot) / 2
            dash = max(6.0, w * 0.012)
            x = -dash
            cr.set_source_rgba(1.0, 0.96, 0.86, 0.10 + 0.16 * amb)
            while x < w:
                cr.rectangle(x, y_mid, dash * 0.55, max(1.0, h * 0.0016))
                x += dash * 2.1
            cr.fill()

        # 雨天的路面反光
        if scene.has_weather and scene.precip_kind == "rain" and scene.precip_strength > 0.15:
            wet = cairo.LinearGradient(0, y_road_top, 0, y_kerb_near)
            tone = mix(mu.horizon, (255, 255, 255), 0.2)
            wet.add_color_stop_rgba(0, tone[0] / 255, tone[1] / 255, tone[2] / 255,
                                    0.16 * scene.precip_strength)
            wet.add_color_stop_rgba(1, tone[0] / 255, tone[1] / 255, tone[2] / 255,
                                    0.06 * scene.precip_strength)
            cr.set_source(wet)
            cr.rectangle(0, y_road_top, w, y_kerb_near - y_road_top)
            cr.fill()

    def _draw_street(self, cr, w, h, scene: Scene, light):
        """地平线之下、窗台之前的那条街上的人与车。"""
        if self._street_roster is None:
            self._street_roster = street.roster(self.fx.seed * 7 + 13)
        if self._street_trees is None:
            self._street_trees = street.trees(self.fx.seed * 5 + 3)
        ui = self.ui
        near_col = self._colors(scene)
        # 时间取"正在走动的墙钟"，而不是每分钟才重建一次的 scene.when，
        # 否则人与车会像定格一样一分钟跳一次（云是用墙钟画的，所以一直很顺）。
        # 时间旅行（预览）时以预览那一刻为起点，再加上之后真正走过的秒数：
        # 天色停在被预览的时刻，但街上的人车照常走，不会冻成一张照片。
        when = scene.when
        if self.clock is not None:
            try:
                now = self.clock()
            except Exception:
                now = None
            if now is not None:
                started = ui.preview_started if ui.preview_dt is not None else None
                if scene.preview and started is not None:
                    when = scene.when + (now - started)
                else:
                    when = now
        t = (when.hour * 3600 + when.minute * 60 + when.second
             + getattr(when, "microsecond", 0) / 1e6)
        street.draw(cr, w, h, scene, self._street_roster, t, light, near_col,
                    self._layers(scene).lamps if self.ui.show_lamps else (),
                    self._street_trees or (),
                    people=self.ui.show_people, traffic=self.ui.show_traffic,
                    trees_on=self.ui.show_trees)

    def _draw_skyline(self, cr, w, h, scene: Scene, light) -> None:
        layers = self._layers(scene)
        mu = scene.mood
        horizon_y = HORIZON_Y * h
        # 城市是整帧里最贵的一块（每帧上千条路径），而它只随光变化——
        # 把光照量化成很细的档位缓存下来，太阳走过一格才重画一次。
        # key 里必须带上城市种子：光没变、城市变了的时候，一样得重画。
        key = (int(w), int(h), self._city_seed(scene),
               round(light.sun_alt * 4), round(light.rel_az * 4),
               round(light.ambient * 60), round(light.direct * 60),
               round(light.night * 40), round(light.moon * 30),
               round(light.lit_frac * 60))
        if self._city.stale(key, w, h):
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, int(w), int(h))
            _city.draw(cairo.Context(surf), w, h, layers, light, horizon_y)
            self._city.store(key, surf)
        cr.set_source_surface(self._city.surf, 0, 0)
        cr.paint()
        # 雾：把远处的屋顶糊掉
        if scene.fog:
            top = horizon_y - h * 0.20
            fog = cairo.LinearGradient(0, top, 0, horizon_y + h * 0.01)
            tone = mix(mu.horizon, (210, 214, 218), 0.45)
            fog.add_color_stop_rgba(0, tone[0] / 255, tone[1] / 255, tone[2] / 255, 0.0)
            fog.add_color_stop_rgba(0.65, tone[0] / 255, tone[1] / 255, tone[2] / 255, 0.34)
            fog.add_color_stop_rgba(1, tone[0] / 255, tone[1] / 255, tone[2] / 255, 0.52)
            cr.set_source(fog)
            cr.rectangle(0, top, w, h * 0.21)
            cr.fill()

    # ------------------------------------------------------------------
    def _draw_vignette(self, cr, w, h, scene: Scene):
        key = (int(w), int(h))
        if self._overlay.stale(key, w, h):
            self._overlay.store(key, self._render_overlay(int(w), int(h)))
        cr.set_source_surface(self._overlay.surf, 0, 0)
        cr.paint()

    def _render_overlay(self, w: int, h: int):
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(surf)
        top = cairo.LinearGradient(0, 0, 0, h * 0.09)
        top.add_color_stop_rgba(0, 0, 0, 0, 0.26)
        top.add_color_stop_rgba(1, 0, 0, 0, 0)
        cr.set_source(top)
        cr.rectangle(0, 0, w, h * 0.09)
        cr.fill()
        side_w = w * 0.05
        for x0, x1 in ((0.0, side_w), (w, w - side_w)):
            g = cairo.LinearGradient(x0, 0, x1, 0)
            g.add_color_stop_rgba(0, 0, 0, 0, 0.18)
            g.add_color_stop_rgba(1, 0, 0, 0, 0)
            cr.set_source(g)
            cr.rectangle(min(x0, x1), 0, side_w, h)
            cr.fill()
        vg = cairo.RadialGradient(w / 2, h * 0.55, min(w, h) * 0.35,
                                  w / 2, h * 0.55, max(w, h) * 0.78)
        vg.add_color_stop_rgba(0, 0, 0, 0, 0)
        vg.add_color_stop_rgba(1, 0, 0, 0, 0.20)
        cr.set_source(vg)
        cr.rectangle(0, 0, w, h)
        cr.fill()
        # 玻璃反光
        cr.save()
        cr.translate(0, -h * 0.1)
        cr.rotate(-0.16)
        g = cairo.LinearGradient(w * 0.02, 0, w * 0.55, 0)
        g.add_color_stop_rgba(0, 1, 1, 1, 0.0)
        g.add_color_stop_rgba(0.45, 1, 1, 1, 0.035)
        g.add_color_stop_rgba(0.62, 1, 1, 1, 0.055)
        g.add_color_stop_rgba(1, 1, 1, 1, 0.0)
        cr.set_source(g)
        cr.rectangle(0, 0, w * 1.2, h * 1.4)
        cr.fill()
        cr.restore()
        return surf
