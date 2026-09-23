"""系统托盘（KStatusNotifierItem + DBusMenu），只用 Gio 的 DBus，不依赖额外库。

托盘菜单不是另抄一份，而是直接把应用菜单（Gio.Menu）翻译成 dbusmenu，
所以两边的项目、勾选状态天然一致。
"""

from __future__ import annotations

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

SNI_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"
WATCHER_NAME = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"

# 属性类型必须和 GNOME 的 AppIndicator 扩展一字不差，否则 GDBusProxy 会
# 因为类型不匹配而拒绝整条属性链（WindowId 在扩展里是 i，不是规范文档里的 u）。
SNI_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionMovieName" type="s" access="read"/>
    <!-- 下面这些是 GNOME 的 AppIndicator 扩展自己"追加"进属性列表的项
         （见扩展的 appIndicator.js: _setupProxyPropertyList）。它每次收到
         NewIcon 之类信号都会拿这些名字来 Get；只要有一项答不上来，扩展就会
         抛一个未捕获的 Promise 异常刷满日志。我们照单全收，答空值最省事。 -->
    <property name="IconAccessibleDesc" type="s" access="read"/>
    <property name="AttentionAccessibleDesc" type="s" access="read"/>
    <property name="XAyatanaLabel" type="s" access="read"/>
    <property name="XAyatanaLabelGuide" type="s" access="read"/>
    <property name="XAyatanaOrderingIndex" type="u" access="read"/>
    <method name="ContextMenu">
      <arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/>
    </method>
    <method name="Activate">
      <arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/>
    </method>
    <method name="SecondaryActivate">
      <arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/>
    </method>
    <method name="XAyatanaSecondaryActivate">
      <arg name="timestamp" type="u" direction="in"/>
    </method>
    <method name="Scroll">
      <arg name="delta" type="i" direction="in"/><arg name="orientation" type="s" direction="in"/>
    </method>
    <signal name="NewTitle"/>
    <signal name="NewIcon"/>
    <signal name="NewAttentionIcon"/>
    <signal name="NewOverlayIcon"/>
    <signal name="NewStatus"><arg name="status" type="s"/></signal>
    <signal name="NewIconThemePath"><arg type="s" name="icon_theme_path" direction="out"/></signal>
    <signal name="NewMenu"/>
  </interface>
</node>
"""

MENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <method name="GetLayout">
      <arg type="i" name="parentId" direction="in"/>
      <arg type="i" name="recursionDepth" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="u" name="revision" direction="out"/>
      <arg type="(ia{sv}av)" name="layout" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="a(ia{sv})" name="properties" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="name" direction="in"/>
      <arg type="v" name="value" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="eventId" direction="in"/>
      <arg type="v" name="data" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg type="a(isvu)" name="events" direction="in"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg type="i" name="id" direction="in"/>
      <arg type="b" name="needUpdate" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="ai" name="updatesNeeded" direction="out"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <property name="Version" type="u" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <signal name="ItemsPropertiesUpdated">
      <arg type="a(ia{sv})" name="updatedProps"/>
      <arg type="a(ias)" name="removedProps"/>
    </signal>
    <signal name="LayoutUpdated">
      <arg type="u" name="revision"/>
      <arg type="i" name="parent"/>
    </signal>
  </interface>
</node>
"""


class MenuEntry:
    """托盘菜单里的一项。"""

    __slots__ = ("id", "label", "action", "target", "stateful", "children",
                 "separator", "icon", "action_obj", "handler_id")

    def __init__(self, ident: int, label: str = "", action: str = "",
                 target=None, stateful: bool = False, separator: bool = False,
                 children=None, icon: str = ""):
        self.id = ident
        self.label = label
        self.action = action
        self.target = target
        self.stateful = stateful
        self.separator = separator
        self.children = children or []
        self.icon = icon
        self.action_obj = None
        self.handler_id = 0


class Tray:
    """一个 KStatusNotifierItem，菜单内容来自应用的 Gio.Menu。"""

    def __init__(self, menu_model: Gio.MenuModel, activate_cb, action_lookup,
                 icon_name: str = "chuang", prefix_items=None,
                 activate_action: str = "win.show"):
        self.menu_model = menu_model
        self.activate_cb = activate_cb
        # 图标被点（Activate）时执行哪个动作——和菜单第一项是同一个
        self.activate_name = activate_action
        self.action_lookup = action_lookup     # name -> Gio.Action|None
        self.icon_name = icon_name
        self.prefix_items = prefix_items or []
        self.connection: Gio.DBusConnection | None = None
        self.entries: dict[int, MenuEntry] = {}
        self.root = MenuEntry(0, children=[])
        self._next_id = 1
        self._revision = 1
        self.available = False
        self.reason = ""
        self._sni_reg = 0
        self._menu_reg = 0
        self.pixmaps = self._load_pixmaps(icon_name)

    @staticmethod
    def _load_pixmaps(icon_name: str = "chuang"):
        """图标像素（a(iiay)，网络字节序 ARGB）。

        GNOME 的 AppIndicator 扩展找不到图标名时会退回像素图；
        像素图为空它会抛异常（甚至把扩展搞挂），所以这里必须给出真数据。
        """
        from pathlib import Path

        out = []
        # 装了 .deb / 源码安装时图标在这里；直接跑仓库（开发模式）时在 data/
        candidates = [
            Path(f"/usr/share/icons/hicolor/scalable/apps/{icon_name}.svg"),
            Path(__file__).resolve().parent.parent / "data" / f"{icon_name}.svg",
        ]
        try:
            import gi as _gi
            _gi.require_version("GdkPixbuf", "2.0")
            from gi.repository import GdkPixbuf
            source = next((p for p in candidates if p.exists()), None)
            if source is None:
                raise FileNotFoundError(icon_name)
            for size in (32, 64):
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(source), size, size, True)
                if not pb.get_has_alpha():
                    pb = pb.add_alpha(False, 0, 0, 0)
                w, h = pb.get_width(), pb.get_height()
                stride = pb.get_rowstride()
                data = pb.get_pixels()
                nch = pb.get_n_channels()
                buf = bytearray(w * h * 4)
                for y in range(h):
                    row = y * stride
                    for x in range(w):
                        i = row + x * nch
                        o = (y * w + x) * 4
                        r = data[i] if nch > 0 else 0
                        g = data[i + 1] if nch > 1 else r
                        b = data[i + 2] if nch > 2 else r
                        a = data[i + 3] if nch > 3 else 255
                        buf[o], buf[o + 1], buf[o + 2], buf[o + 3] = a, r, g, b
                out.append((w, h, bytes(buf)))
        except Exception:
            pass
        return out or Tray._fallback_pixmaps()

    @staticmethod
    def _fallback_pixmaps():
        """连图标文件都找不到时的兜底：用 Cairo 画一个。

        宁可丑，也绝不给空像素图——空像素图会让扩展抛异常，进而连累
        用户桌面上所有托盘图标。
        """
        out = []
        try:
            import cairo
            for size in (32, 64):
                surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
                cr = cairo.Context(surf)
                r = size * 0.5
                cr.arc(r, r, r * 0.86, 0, 2 * 3.141592653589793)
                cr.set_source_rgba(0.10, 0.13, 0.22, 0.92)
                cr.fill_preserve()
                cr.set_source_rgba(0.92, 0.95, 1.0, 0.95)
                cr.set_line_width(max(1.0, size * 0.07))
                cr.stroke()
                cr.arc(r, r * 0.92, r * 0.34, 0, 2 * 3.141592653589793)
                cr.set_source_rgba(0.98, 0.96, 0.86, 0.95)
                cr.fill()
                surf.flush()
                # 换成网络字节序的 ARGB（和 GdkPixbuf 那条路保持一致）
                raw = bytes(surf.get_data())
                stride = surf.get_stride()
                buf = bytearray(size * size * 4)
                for y in range(size):
                    for x in range(size):
                        o = y * stride + x * 4
                        d = (y * size + x) * 4
                        b, g, rr, a = raw[o], raw[o + 1], raw[o + 2], raw[o + 3]
                        buf[d], buf[d + 1], buf[d + 2], buf[d + 3] = a, rr, g, b
                out.append((size, size, bytes(buf)))
        except Exception:
            pass
        return out

    # ------------------------------------------------------------------
    def start(self) -> bool:
        try:
            self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception as exc:
            self.reason = f"连不上会话总线：{exc}"
            return False
        if self.connection is None:
            self.reason = "会话总线不可用"
            return False

        # 有没有宿主（GNOME 的 AppIndicator 扩展会注册这个 watcher）
        try:
            has_watcher = self.connection.call_sync(
                WATCHER_NAME, WATCHER_PATH, "org.freedesktop.DBus.Peer",
                "Ping", None, None, Gio.DBusCallFlags.NONE, 800, None)
            has_watcher = has_watcher is not None
        except Exception:
            has_watcher = False
        if not has_watcher:
            self.reason = ("系统托盘宿主没在运行（GNOME 需要 AppIndicator 扩展），"
                           "托盘图标暂时不会出现")
            return False

        self._build_menu()
        sni = Gio.DBusNodeInfo.new_for_xml(SNI_XML).interfaces[0]
        menu = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]
        self._sni_reg = self.connection.register_object(
            SNI_PATH, sni, self._on_sni_call, self._on_sni_property, None)
        self._menu_reg = self.connection.register_object(
            MENU_PATH, menu, self._on_menu_call, self._on_menu_property, None)
        try:
            self.connection.call_sync(
                WATCHER_NAME, WATCHER_PATH, WATCHER_NAME,
                "RegisterStatusNotifierItem",
                GLib.Variant("(s)", (self.connection.get_unique_name(),)),
                None, Gio.DBusCallFlags.NONE, 1500, None)
        except Exception as exc:
            self.reason = f"托盘注册失败：{exc}"
            self.stop()
            return False
        self.available = True
        return True

    def reload(self, menu_model) -> None:
        """换一份菜单模型（例如检测到新版本后多了一项），就地刷新托盘菜单。"""
        self.menu_model = menu_model
        if not self.available or self.connection is None:
            return
        for entry in list(self.entries.values()):
            if entry.action_obj is not None and entry.handler_id:
                try:
                    entry.action_obj.disconnect(entry.handler_id)
                except Exception:
                    pass
                entry.handler_id = 0
        self._build_menu()
        self._revision += 1
        try:
            self.connection.emit_signal(
                None, MENU_PATH, "com.canonical.dbusmenu", "LayoutUpdated",
                GLib.Variant("(ui)", (self._revision, 0)))
        except Exception:
            pass

    def stop(self):
        if self.connection is not None:
            if self._sni_reg:
                self.connection.unregister_object(self._sni_reg)
            if self._menu_reg:
                self.connection.unregister_object(self._menu_reg)
        self._sni_reg = self._menu_reg = 0
        self.available = False

    # ------------------------------------------------------------------
    # 把应用菜单翻译成 dbusmenu
    # ------------------------------------------------------------------
    def _build_menu(self):
        self.entries = {0: self.root}
        head = [self._register(MenuEntry(self._new_id(), label=label, action=action))
                for label, action in self.prefix_items]
        if head:
            head.append(self._register(MenuEntry(self._new_id(), separator=True)))
        self.root.children = head + self._walk(self.menu_model)
        # 监听所有带状态的项，勾选状态变化时通知托盘
        for entry in self.entries.values():
            act = self.action_lookup(entry.action) if entry.action else None
            entry.action_obj = act
            if act is not None and entry.stateful:
                entry.handler_id = act.connect("notify::state",
                                               self._on_action_state, entry)

    def _walk(self, model: Gio.MenuModel) -> list[MenuEntry]:
        out: list[MenuEntry] = []
        n = model.get_n_items()
        for i in range(n):
            label = self._attr(model, i, "label") or ""
            action = self._attr(model, i, "action") or ""
            target = model.get_item_attribute_value(i, "target", None)
            submenu = model.get_item_link(i, Gio.MENU_LINK_SUBMENU)
            section = model.get_item_link(i, Gio.MENU_LINK_SECTION)
            if section is not None:                     # 分组 → 托盘里用分隔线
                if out:
                    out.append(self._register(MenuEntry(self._new_id(),
                                                        separator=True)))
                out.extend(self._walk(section))
                continue
            entry = self._register(MenuEntry(self._new_id(), label=label,
                                             action=action, target=target))
            if action:
                act = self.action_lookup(action)
                entry.action_obj = act
                entry.stateful = bool(act is not None
                                      and act.get_state() is not None
                                      and act.get_parameter_type() is None)
            if submenu is not None:
                entry.children = self._walk(submenu)
            out.append(entry)
        return out

    @staticmethod
    def _attr(model: Gio.MenuModel, index: int, name: str):
        value = model.get_item_attribute_value(index, name,
                                              GLib.VariantType.new("s"))
        return value.get_string() if value is not None else None

    def _new_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def _register(self, entry: MenuEntry) -> MenuEntry:
        self.entries[entry.id] = entry
        return entry

    # ------------------------------------------------------------------
    # SNI 接口
    # ------------------------------------------------------------------
    def _on_sni_call(self, conn, sender, path, iface, method, params, invocation):
        if method in ("Activate", "SecondaryActivate"):
            # 把名字显式传过去：窗口那边的方法签名是 (name, target)，不带参数
            # 调用会抛 TypeError，而 D-Bus 方法处理器抛异常就意味着这次调用
            # 永远等不到回复（图标点了没反应，日志里多一条 traceback）。
            self.activate_cb(self.activate_name)
            invocation.return_value(None)
        elif method == "XAyatanaSecondaryActivate":
            self.activate_cb(self.activate_name)
            invocation.return_value(None)
        elif method == "ContextMenu":
            invocation.return_value(None)
        elif method == "Scroll":
            invocation.return_value(None)
        else:
            invocation.return_value(None)

    def _on_sni_property(self, conn, sender, path, iface, prop):
        values = {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", "chuang"),
            "Title": GLib.Variant("s", "窗"),
            "Status": GLib.Variant("s", "Active"),
            "WindowId": GLib.Variant("i", 0),
            "IconThemePath": GLib.Variant("s", ""),
            # 图标名故意留空：GNOME 的 AppIndicator 扩展一旦"按名字找到图标"，
            # 就会走一条在这台机器上会抛异常的 GdkPixbuf 路径（进而把扩展弄挂）。
            # 只给像素图最稳，系统里其它托盘程序也都是这么做的。
            "IconName": GLib.Variant("s", ""),
            "IconPixmap": GLib.Variant("a(iiay)", self.pixmaps),
            "OverlayIconName": GLib.Variant("s", ""),
            "OverlayIconPixmap": GLib.Variant("a(iiay)", []),
            "AttentionIconName": GLib.Variant("s", ""),
            "AttentionIconPixmap": GLib.Variant("a(iiay)", []),
            "AttentionMovieName": GLib.Variant("s", ""),
            "IconAccessibleDesc": GLib.Variant("s", ""),
            "AttentionAccessibleDesc": GLib.Variant("s", ""),
            "XAyatanaLabel": GLib.Variant("s", ""),
            "XAyatanaLabelGuide": GLib.Variant("s", ""),
            "XAyatanaOrderingIndex": GLib.Variant("u", 0),
            "ItemIsMenu": GLib.Variant("b", False),
            "Menu": GLib.Variant("o", MENU_PATH),
        }
        return values.get(prop)

    # ------------------------------------------------------------------
    # dbusmenu 接口
    # ------------------------------------------------------------------
    @staticmethod
    def _props_of(entry: MenuEntry):
        props = {}
        if entry.separator:
            props["type"] = GLib.Variant("s", "separator")
            props["visible"] = GLib.Variant("b", True)
            return props
        props["label"] = GLib.Variant("s", entry.label)
        props["visible"] = GLib.Variant("b", True)
        act = entry.action_obj
        enabled = True
        if act is not None:
            try:
                enabled = act.get_enabled()
            except Exception:
                enabled = True
        props["enabled"] = GLib.Variant("b", bool(enabled))
        if entry.children:
            props["children-display"] = GLib.Variant("s", "submenu")
        state = act.get_state() if act is not None else None
        if state is not None and entry.target is not None:
            # 单选组（例如"关窗时：问我 / 最小化到托盘 / 直接退出"）
            same = bool(state.equal(entry.target))
            props["toggle-type"] = GLib.Variant("s", "radio")
            props["toggle-state"] = GLib.Variant("i", 1 if same else 0)
        elif (act is not None and entry.stateful and state is not None
              and state.get_type_string() == "b"):
            on = bool(state.get_boolean())
            props["toggle-type"] = GLib.Variant("s", "checkmark")
            props["toggle-state"] = GLib.Variant("i", 1 if on else 0)
        return props

    def _on_menu_call(self, conn, sender, path, iface, method, params, invocation):
        try:
            self._dispatch_menu_call(method, params, invocation)
        except Exception:             # 绝不吞掉请求：出错也回一个中性结果
            import traceback; traceback.print_exc()
            try:
                if method == "GetLayout":
                    invocation.return_value(GLib.Variant.new_tuple(
                        GLib.Variant("u", self._revision),
                        GLib.Variant("(ia{sv}av)", (0, {}, []))))
                elif method == "GetGroupProperties":
                    invocation.return_value(GLib.Variant("(a(ia{sv}))", ([],)))
                elif method == "GetProperty":
                    invocation.return_value(GLib.Variant("(v)", (GLib.Variant("s", ""),)))
                elif method == "EventGroup":
                    invocation.return_value(GLib.Variant("(ai)", ([],)))
                elif method == "AboutToShowGroup":
                    invocation.return_value(GLib.Variant("(aiai)", ([], [])))
                elif method == "AboutToShow":
                    invocation.return_value(GLib.Variant("(b)", (False,)))
                else:
                    invocation.return_value(None)
            except Exception:
                pass

    def _dispatch_menu_call(self, method, params, invocation):
        if method == "GetLayout":
            parent_id = int(params[0])
            depth = int(params[1])
            entry = self.entries.get(parent_id, self.root)
            levels = 99 if depth < 0 else max(0, depth)
            layout = self._node_variant(entry, levels)
            invocation.return_value(GLib.Variant.new_tuple(
                GLib.Variant("u", self._revision), layout))
        elif method == "GetGroupProperties":
            ids = list(params[0]) if params[0] else list(self.entries.keys())
            out = []
            for i in ids:
                entry = self.entries.get(i)
                if entry is None:
                    continue
                out.append((i, self._props_of(entry)))
            invocation.return_value(GLib.Variant("(a(ia{sv}))", (out,)))
        elif method == "GetProperty":
            entry = self.entries.get(params[0])
            props = self._props_of(entry) if entry else {}
            value = props.get(params[1])
            invocation.return_value(GLib.Variant(
                "(v)", (value if value is not None else GLib.Variant("s", ""),)))
        elif method == "Event":
            self._on_event(params[0], params[1], params[2])
            invocation.return_value(None)
        elif method == "EventGroup":
            for item in params[0]:
                self._on_event(item[0], item[1], item[2])
            invocation.return_value(GLib.Variant("(ai)", ([],)))
        elif method == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (False,)))
        elif method == "AboutToShowGroup":
            invocation.return_value(GLib.Variant("(aiai)", ([], [])))
        else:
            invocation.return_value(None)

    def _node_variant(self, entry: MenuEntry, levels: int):
        """把一项及其子树构造成 (ia{sv}av)。

        levels：还能往下展开几层（0 = 不再展开子节点）。
        注意：GLib 的变体构造器不接受"已建好的 Variant"作为非 v 字段，
        所以要在这里一次性把子节点也构造成 Variant 放进 av 数组里。
        """
        kids = []
        if levels > 0:
            kids = [self._node_variant(c, levels - 1) for c in entry.children]
        return GLib.Variant("(ia{sv}av)",
                            (entry.id, self._props_of(entry), kids))

    def _on_menu_property(self, conn, sender, path, iface, prop):
        values = {
            "Version": GLib.Variant("u", 3),
            "Status": GLib.Variant("s", "normal"),
            "TextDirection": GLib.Variant("s", "ltr"),
            "IconThemePath": GLib.Variant("as", []),
        }
        return values.get(prop)

    def _on_event(self, item_id: int, event_id: str, data):
        if event_id != "clicked":
            return
        entry = self.entries.get(item_id)
        if entry is None or not entry.action:
            return
        act = entry.action_obj
        if act is not None and entry.stateful and entry.target is None:
            # 勾选项：不带参数地激活，让动作自己翻转
            self.activate_cb(entry.action, None)
        else:
            self.activate_cb(entry.action, entry.target)

    def _on_action_state(self, action, _pspec, entry: MenuEntry):
        if not self.available or self.connection is None:
            return
        try:
            state = action.get_state()
            updated = []
            for e in self.entries.values():
                if e.action != entry.action:
                    continue
                if e.target is not None:
                    on = bool(state is not None and state.equal(e.target))
                elif state is None or state.get_type_string() != "b":
                    continue
                else:
                    on = bool(state.get_boolean())
                updated.append((e.id, {"toggle-state": GLib.Variant("i", 1 if on else 0)}))
            if not updated:
                return
            self.connection.emit_signal(
                None, MENU_PATH, "com.canonical.dbusmenu", "ItemsPropertiesUpdated",
                GLib.Variant("(a(ia{sv})a(ias))", (updated, [])))
        except Exception:
            pass
