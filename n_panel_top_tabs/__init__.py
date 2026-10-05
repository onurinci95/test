# SPDX-License-Identifier: GPL-3.0-or-later
"""
N-Panel Top Tabs

3D Viewport'un sağındaki N panelde (Sidebar) dikey sekmeler halinde duran
eklenti kategorilerini, viewport başlığında yatay bir buton satırı olarak
gösterir. Her buton bir popover açar ve o kategorideki tüm panelleri
(alt panelleriyle birlikte) çizer. Orijinal paneller yerinde kalır; bu
eklenti yalnızca onlara ikinci, daha okunaklı bir erişim yolu ekler.
"""

bl_info = {
    "name": "N-Panel Top Tabs",
    "author": "onurinci95",
    "version": (1, 2, 0),
    "blender": (4, 2, 0),
    "location": "3D Viewport > Header",
    "description": "Show sidebar (N panel) add-on tabs as horizontal header buttons",
    "category": "Interface",
}

import inspect
import types

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty

ADDON_ID = __package__ or __name__
PROXY_PREFIX = "TOPTABS_PT_"
REFRESH_INTERVAL = 2.0
# Popover panels are registered once in register() and reused; refreshing only
# reassigns which category each slot shows. Registering classes later (from a
# timer or load_post at startup) proved unreliable.
MAX_SLOTS = 64

# Blender'ın kendi Python arayüz modülleri (Item/Tool/View vb.)
BUILTIN_MODULE_PREFIXES = ("bl_ui", "bl_operators")

HEADER_TYPES = {
    "VIEW3D_HEADER": "VIEW3D_HT_header",
    "VIEW3D_TOOL_HEADER": "VIEW3D_HT_tool_header",
    "TOPBAR": "TOPBAR_HT_upper_bar",
}

# category -> slot panel idname (in display order)
_proxies = {}
# slot index -> category
_slot_categories = {}
_slot_classes = []
# category -> [top-level panel classes]
_category_panels = {}
# parent idname -> [child panel classes]
_children = {}
_signature = None
_attached_header = None


# -----------------------------------------------------------------------------
# Helpers


def _prefs():
    addon = bpy.context.preferences.addons.get(ADDON_ID)
    return addon.preferences if addon else None


def _panel_idname(cls):
    return getattr(cls, "bl_idname", "") or cls.__name__


def _iter_panel_classes():
    seen = set()
    queue = list(bpy.types.Panel.__subclasses__())
    while queue:
        cls = queue.pop(0)
        if cls in seen:
            continue
        seen.add(cls)
        queue.extend(cls.__subclasses__())
        yield cls


def _is_sidebar_panel(cls):
    return (
        getattr(cls, "bl_space_type", None) == "VIEW_3D"
        and getattr(cls, "bl_region_type", None) == "UI"
        and getattr(cls, "is_registered", False)
        and not cls.__name__.startswith(PROXY_PREFIX)
    )


def _is_builtin(cls):
    return cls.__module__.startswith(BUILTIN_MODULE_PREFIXES)


def _excluded_categories(prefs):
    if prefs is None:
        return set()
    return {c.strip().lower() for c in prefs.excluded_categories.split(",") if c.strip()}


def _collect(prefs):
    include_builtin = prefs.include_builtin if prefs else False
    categories = {}
    children = {}
    for cls in _iter_panel_classes():
        if not _is_sidebar_panel(cls):
            continue
        parent = getattr(cls, "bl_parent_id", "")
        if parent:
            children.setdefault(parent, []).append(cls)
            continue
        if not include_builtin and _is_builtin(cls):
            continue
        category = getattr(cls, "bl_category", "") or "Misc"
        categories.setdefault(category, []).append(cls)

    order_key = lambda c: getattr(c, "bl_order", 0)
    for panels in categories.values():
        panels.sort(key=order_key)
    for panels in children.values():
        panels.sort(key=order_key)
    return categories, children


def _poll(cls, context):
    poll = getattr(cls, "poll", None)
    if poll is None:
        return True
    try:
        return bool(poll(context))
    except Exception:
        return False


# -----------------------------------------------------------------------------
# Drawing other add-ons' panels inside a popover


class _PanelProxy:
    """Stands in for `self` when calling another panel's draw functions.

    Panels can't be instantiated by hand, so this object exposes `layout` and
    forwards everything else (bl_label, helper methods, ...) to the class.
    """

    _defaults = {
        "is_popover": True,
        "use_pin": False,
        "text": "",
        "custom_data": None,
    }

    def __init__(self, cls, layout):
        object.__setattr__(self, "_cls", cls)
        object.__setattr__(self, "layout", layout)

    def __getattr__(self, name):
        cls = object.__getattribute__(self, "_cls")
        try:
            static = inspect.getattr_static(cls, name)
        except AttributeError:
            if name in _PanelProxy._defaults:
                return _PanelProxy._defaults[name]
            raise
        if isinstance(static, (staticmethod, classmethod)):
            return getattr(cls, name)
        if inspect.isfunction(static):
            return types.MethodType(static, self)
        return getattr(cls, name)


def _call(func_name, cls, layout, context):
    func = inspect.getattr_static(cls, func_name, None)
    if not inspect.isfunction(func):
        return
    try:
        func(_PanelProxy(cls, layout), context)
    except Exception as ex:
        layout.label(text=f"{_panel_idname(cls)}: {ex}", icon="ERROR")


def _draw_panel(layout, context, cls, depth=0):
    if depth > 8 or not _poll(cls, context):
        return

    idname = _panel_idname(cls)
    options = getattr(cls, "bl_options", set())

    if "HIDE_HEADER" in options:
        body = layout.column()
    elif hasattr(layout, "panel"):
        header, body = layout.panel(
            f"toptabs_{idname}", default_closed="DEFAULT_CLOSED" in options
        )
        row = header.row(align=True)
        _call("draw_header", cls, row, context)
        row.label(text=getattr(cls, "bl_label", idname))
        _call("draw_header_preset", cls, header.row(align=True), context)
    else:
        body = layout.box()
        body.label(text=getattr(cls, "bl_label", idname))

    if body is None:
        return

    _call("draw", cls, body, context)
    for child in _children.get(idname, ()):
        _draw_panel(body, context, child, depth + 1)


def _make_slot_panel(index, width):
    def draw(self, context):
        layout = self.layout
        category = _slot_categories.get(index)
        drawn = False
        for cls in _category_panels.get(category, ()):
            if _poll(cls, context):
                _draw_panel(layout, context, cls)
                drawn = True
        if not drawn:
            layout.label(text="Nothing to show in this context", icon="INFO")

    idname = f"{PROXY_PREFIX}slot_{index}"
    return type(
        idname,
        (bpy.types.Panel,),
        {
            "bl_idname": idname,
            "bl_label": "Top Tab",
            "bl_space_type": "VIEW_3D",
            "bl_region_type": "HEADER",
            "bl_ui_units_x": width,
            "draw": draw,
        },
    )


def _register_slots(width):
    _unregister_slots()
    for index in range(MAX_SLOTS):
        cls = _make_slot_panel(index, width)
        bpy.utils.register_class(cls)
        _slot_classes.append(cls)


def _unregister_slots():
    for cls in reversed(_slot_classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    _slot_classes.clear()


# -----------------------------------------------------------------------------
# Header


def _draw_header(self, context):
    prefs = _prefs()
    if prefs is None or not prefs.enabled:
        return
    if not _proxies:
        # Scanning is read-only, so it is safe to do from a draw callback.
        try:
            refresh(redraw=False)
        except Exception as ex:
            print(f"[N-Panel Top Tabs] refresh failed: {ex}")
    use_sidebar = prefs.click_action == "SIDEBAR"
    active = None
    if use_sidebar:
        area, region = _find_sidebar(context)
        if area is not None and area.spaces.active.show_region_ui:
            active = _active_category(region)

    layout = self.layout
    if prefs.align_right:
        layout.separator_spacer()
    row = layout.row(align=True)
    for category, label, visible in _ordered_tabs(prefs):
        if not visible:
            continue
        if not any(_poll(cls, context) for cls in _category_panels.get(category, ())):
            continue
        if use_sidebar:
            op = row.operator(
                "toptabs.open_sidebar_tab", text=label, depress=category == active
            )
            op.category = category
        else:
            row.popover(panel=_proxies[category], text=label)
    row.operator("toptabs.refresh", text="", icon="FILE_REFRESH", emboss=False)


def _attach_header(location, position="END"):
    global _attached_header
    _detach_header()
    header = getattr(bpy.types, HEADER_TYPES[location], None)
    if header is not None:
        if position == "START":
            header.prepend(_draw_header)
        else:
            header.append(_draw_header)
        _attached_header = header


def _detach_header():
    global _attached_header
    if _attached_header is not None:
        try:
            _attached_header.remove(_draw_header)
        except Exception:
            pass
        _attached_header = None


def _ensure_attached():
    """Re-attach if another add-on replaced the header class or its draw().

    Some add-ons re-register VIEW3D_HT_tool_header (or monkey-patch its draw
    method) after we load, which silently drops our appended draw function.
    """
    prefs = _prefs()
    location = prefs.location if prefs else "VIEW3D_TOOL_HEADER"
    position = prefs.position if prefs else "END"
    header = getattr(bpy.types, HEADER_TYPES[location], None)
    if header is None:
        return False
    draw_funcs = getattr(getattr(header, "draw", None), "_draw_funcs", None) or ()
    if header is _attached_header and _draw_header in draw_funcs:
        return False
    _attach_header(location, position)
    return True


def _redraw_all():
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return
    for window in wm.windows:
        if window.screen is None:
            continue
        for area in window.screen.areas:
            area.tag_redraw()


# -----------------------------------------------------------------------------
# User's tab list (order, visibility, custom names)


def _ordered_tabs(prefs):
    """Detected tabs as (category, label, visible), in the user's order.

    Tabs the user hasn't seen in the list yet go last and are visible, unless
    they are in the legacy comma separated "Hidden Tabs" string.
    """
    result = []
    seen = set()
    for item in prefs.tabs:
        if item.name in _proxies and item.name not in seen:
            seen.add(item.name)
            result.append((item.name, item.label.strip() or item.name, item.visible))
    legacy_hidden = _excluded_categories(prefs)
    for name in _proxies:
        if name not in seen:
            result.append((name, name, name.lower() not in legacy_hidden))
    return result


def _sync_tabs():
    """Add newly detected tabs to the preferences list.

    Not called from draw callbacks, where writing properties is not allowed.
    """
    prefs = _prefs()
    if prefs is None:
        return
    known = {item.name for item in prefs.tabs}
    legacy_hidden = _excluded_categories(prefs)
    for name in _proxies:
        if name not in known:
            item = prefs.tabs.add()
            item.name = name
            item.visible = name.lower() not in legacy_hidden
    if prefs.excluded_categories:
        # Migrated into the list above.
        for item in prefs.tabs:
            if item.name.lower() in legacy_hidden:
                item.visible = False
        prefs.excluded_categories = ""


# -----------------------------------------------------------------------------
# Refresh (add-ons can be enabled/disabled at any time)


def refresh(force=False, redraw=True):
    global _signature, _category_panels, _children
    prefs = _prefs()
    categories, children = _collect(prefs)

    signature = (
        tuple((cat, tuple(_panel_idname(c) for c in panels)) for cat, panels in categories.items()),
        tuple((p, tuple(_panel_idname(c) for c in kids)) for p, kids in children.items()),
    )
    if not force and signature == _signature and _proxies:
        return False

    names = list(categories)[: len(_slot_classes)]

    _category_panels = categories
    _children = children
    _proxies.clear()
    _slot_categories.clear()
    for index, category in enumerate(names):
        _slot_categories[index] = category
        _proxies[category] = _slot_classes[index].bl_idname
    _signature = signature

    if redraw:
        _redraw_all()
    return True


def _refresh_timer():
    try:
        if _ensure_attached():
            _redraw_all()
        refresh()
        _sync_tabs()
    except Exception as ex:
        print(f"[N-Panel Top Tabs] refresh failed: {ex}")
    return REFRESH_INTERVAL


@bpy.app.handlers.persistent
def _on_load_post(*_args):
    _ensure_attached()
    refresh(force=True)
    _sync_tabs()


# -----------------------------------------------------------------------------
# Operators & preferences


def _find_sidebar(context):
    """Return (area, sidebar region) of the 3D Viewport the header belongs to.

    From the top bar there is no viewport in context, so use the largest one.
    """
    area = context.area
    if area is None or area.type != "VIEW_3D":
        screen = context.screen
        areas = [a for a in screen.areas if a.type == "VIEW_3D"] if screen else []
        area = max(areas, key=lambda a: a.width * a.height, default=None)
    if area is None:
        return None, None
    region = next((r for r in area.regions if r.type == "UI"), None)
    return area, region


def _active_category(region):
    try:
        return region.active_panel_category if region else None
    except Exception:
        return None


def _set_category(region, category):
    try:
        region.active_panel_category = category
        return True
    except Exception:
        return False


class TOPTABS_OT_open_sidebar_tab(bpy.types.Operator):
    bl_idname = "toptabs.open_sidebar_tab"
    bl_label = "Open Sidebar Tab"
    bl_description = "Open this tab in the sidebar (N panel). Click again to close the sidebar"
    bl_options = {"INTERNAL"}

    category: StringProperty()

    def execute(self, context):
        area, region = _find_sidebar(context)
        if area is None or region is None:
            self.report({"WARNING"}, "No 3D Viewport found")
            return {"CANCELLED"}
        space = area.spaces.active

        if space.show_region_ui and _active_category(region) == self.category:
            space.show_region_ui = False
            area.tag_redraw()
            return {"FINISHED"}

        was_hidden = not space.show_region_ui
        space.show_region_ui = True
        if not _set_category(region, self.category):
            # Tab list is only built once the sidebar has been drawn, so retry
            # shortly after it was opened.
            category = self.category

            def retry(attempts=[10]):
                attempts[0] -= 1
                try:
                    if _set_category(region, category) or attempts[0] <= 0:
                        area.tag_redraw()
                        return None
                except ReferenceError:
                    return None
                return 0.05

            bpy.app.timers.register(retry, first_interval=0.01 if was_hidden else 0.05)
        area.tag_redraw()
        return {"FINISHED"}


class TOPTABS_OT_refresh(bpy.types.Operator):
    bl_idname = "toptabs.refresh"
    bl_label = "Refresh Top Tabs"
    bl_description = "Rescan sidebar (N panel) tabs"

    def execute(self, context):
        refresh(force=True)
        _sync_tabs()
        return {"FINISHED"}


class TOPTABS_OT_move_tab(bpy.types.Operator):
    bl_idname = "toptabs.move_tab"
    bl_label = "Move Tab"
    bl_description = "Move the selected tab up or down"
    bl_options = {"INTERNAL"}

    direction: EnumProperty(items=[("UP", "Up", ""), ("DOWN", "Down", ""), ("TOP", "Top", ""), ("BOTTOM", "Bottom", "")])

    def execute(self, context):
        prefs = _prefs()
        tabs = prefs.tabs
        index = prefs.tabs_index
        if not 0 <= index < len(tabs):
            return {"CANCELLED"}
        target = {
            "UP": index - 1,
            "DOWN": index + 1,
            "TOP": 0,
            "BOTTOM": len(tabs) - 1,
        }[self.direction]
        target = max(0, min(len(tabs) - 1, target))
        if target != index:
            tabs.move(index, target)
            prefs.tabs_index = target
            _redraw_all()
        return {"FINISHED"}


class TOPTABS_OT_set_all_visible(bpy.types.Operator):
    bl_idname = "toptabs.set_all_visible"
    bl_label = "Show/Hide All Tabs"
    bl_options = {"INTERNAL"}

    visible: BoolProperty()

    @classmethod
    def description(cls, _context, properties):
        return "Show all tabs" if properties.visible else "Hide all tabs"

    def execute(self, context):
        for item in _prefs().tabs:
            item.visible = self.visible
        _redraw_all()
        return {"FINISHED"}


class TOPTABS_OT_sort_tabs(bpy.types.Operator):
    bl_idname = "toptabs.sort_tabs"
    bl_label = "Sort Tabs A-Z"
    bl_description = "Sort the tab list alphabetically"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        tabs = _prefs().tabs
        names = sorted((item.name for item in tabs), key=str.lower)
        for target, name in enumerate(names):
            current = next(i for i, item in enumerate(tabs) if item.name == name)
            tabs.move(current, target)
        _redraw_all()
        return {"FINISHED"}


class TOPTABS_OT_remove_missing(bpy.types.Operator):
    bl_idname = "toptabs.remove_missing"
    bl_label = "Remove Missing Tabs"
    bl_description = "Remove tabs whose add-on is not loaded from the list"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        prefs = _prefs()
        for index in reversed(range(len(prefs.tabs))):
            if prefs.tabs[index].name not in _proxies:
                prefs.tabs.remove(index)
        prefs.tabs_index = min(prefs.tabs_index, max(0, len(prefs.tabs) - 1))
        return {"FINISHED"}


class TOPTABS_UL_tabs(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        loaded = item.name in _proxies
        row = layout.row(align=True)
        row.prop(
            item, "visible", text="", emboss=False,
            icon="HIDE_OFF" if item.visible else "HIDE_ON",
        )
        name = row.row()
        name.active = loaded and item.visible
        name.label(text=item.name if loaded else f"{item.name}  (not loaded)")
        row.prop(item, "label", text="")

    def filter_items(self, context, data, propname):
        # Keep the list in the user's order; only apply the name filter.
        items = getattr(data, propname)
        flags = [self.bitflag_filter_item] * len(items)
        if self.filter_name:
            needle = self.filter_name.lower()
            flags = [
                self.bitflag_filter_item if needle in item.name.lower() else 0
                for item in items
            ]
        return flags, []


def _on_location_update(self, _context):
    _attach_header(self.location, self.position)
    _redraw_all()


def _on_layout_update(_self, _context):
    refresh(force=True)


def _on_width_update(self, _context):
    _register_slots(self.popover_width)
    refresh(force=True)


def _on_draw_update(_self, _context):
    _redraw_all()


class TOPTABS_PG_tab(bpy.types.PropertyGroup):
    # `name` (inherited) is the sidebar category this entry refers to.
    visible: BoolProperty(
        name="Visible", description="Show this tab in the header", default=True,
        update=_on_draw_update,
    )
    label: StringProperty(
        name="Custom Name", description="Name shown on the button (empty: use the tab's own name)",
        default="", update=_on_draw_update,
    )


class TOPTABS_AP_preferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_ID

    enabled: BoolProperty(
        name="Show Top Tabs", default=True, update=_on_draw_update
    )
    location: EnumProperty(
        name="Location",
        items=[
            ("VIEW3D_HEADER", "Viewport Header", "Main 3D Viewport header (Select / Add / Object row)"),
            ("VIEW3D_TOOL_HEADER", "Tool Settings Header", "3D Viewport tool settings bar (second row, where BlenderKit's search bar is)"),
            ("TOPBAR", "Top Bar", "Window top bar. Panels that rely on the 3D Viewport context may not work here"),
        ],
        default="VIEW3D_TOOL_HEADER",
        update=_on_location_update,
    )
    click_action: EnumProperty(
        name="On Click",
        items=[
            ("POPOVER", "Dropdown", "Show the tab's panels in a dropdown under the button"),
            ("SIDEBAR", "Open in Sidebar", "Open the sidebar (N panel) on this tab; click again to close it"),
        ],
        default="SIDEBAR",
        update=_on_draw_update,
    )
    position: EnumProperty(
        name="Position",
        items=[
            ("START", "Start", "Before the header's own buttons (left side)"),
            ("END", "End", "After the header's own buttons and other add-ons (right side)"),
        ],
        default="END",
        update=_on_location_update,
    )
    align_right: BoolProperty(
        name="Align Right", default=False, update=_on_draw_update
    )
    include_builtin: BoolProperty(
        name="Include Built-in Tabs",
        description="Also show Blender's own sidebar tabs (Item, Tool, View...) where possible",
        default=False,
        update=_on_layout_update,
    )
    popover_width: IntProperty(
        name="Popover Width", default=14, min=8, max=40, update=_on_width_update
    )
    # Legacy (<= 1.1.0) comma separated list; migrated into `tabs`.
    excluded_categories: StringProperty(default="", options={"HIDDEN"})
    tabs: bpy.props.CollectionProperty(type=TOPTABS_PG_tab)
    tabs_index: IntProperty(default=0)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        col = layout.column()
        col.prop(self, "enabled")
        col.prop(self, "location")
        col.prop(self, "position")
        col.prop(self, "click_action")
        if self.location == "VIEW3D_TOOL_HEADER":
            col.label(text="If the row is hidden: Viewport > View > Tool Settings", icon="INFO")
        if self.location == "TOPBAR":
            col.label(text="Some panels may not work outside the 3D Viewport", icon="ERROR")
        col.prop(self, "align_right")
        col.prop(self, "include_builtin")
        col.prop(self, "popover_width")

        box = layout.box()
        box.use_property_split = False
        header = box.row()
        header.label(text="Tabs: order, visibility and custom names", icon="PRESET")
        header.operator("toptabs.refresh", text="", icon="FILE_REFRESH", emboss=False)

        row = box.row()
        row.template_list(
            "TOPTABS_UL_tabs", "", self, "tabs", self, "tabs_index",
            rows=max(6, min(len(self.tabs), 14)),
        )
        side = row.column(align=True)
        side.operator("toptabs.move_tab", text="", icon="TRIA_UP_BAR").direction = "TOP"
        side.operator("toptabs.move_tab", text="", icon="TRIA_UP").direction = "UP"
        side.operator("toptabs.move_tab", text="", icon="TRIA_DOWN").direction = "DOWN"
        side.operator("toptabs.move_tab", text="", icon="TRIA_DOWN_BAR").direction = "BOTTOM"
        side.separator()
        side.operator("toptabs.set_all_visible", text="", icon="HIDE_OFF").visible = True
        side.operator("toptabs.set_all_visible", text="", icon="HIDE_ON").visible = False
        side.separator()
        side.operator("toptabs.sort_tabs", text="", icon="SORTALPHA")
        side.operator("toptabs.remove_missing", text="", icon="TRASH")

        if not self.tabs:
            box.label(text="No tabs yet. They appear here a moment after Blender starts.", icon="INFO")
        else:
            box.label(text="Eye: show/hide  ·  Right field: custom button name  ·  Arrows: reorder", icon="QUESTION")


classes = (
    TOPTABS_OT_refresh,
    TOPTABS_OT_open_sidebar_tab,
    TOPTABS_OT_move_tab,
    TOPTABS_OT_set_all_visible,
    TOPTABS_OT_sort_tabs,
    TOPTABS_OT_remove_missing,
    TOPTABS_UL_tabs,
    TOPTABS_PG_tab,
    TOPTABS_AP_preferences,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    prefs = _prefs()
    _register_slots(prefs.popover_width if prefs else 14)
    if prefs:
        _attach_header(prefs.location, prefs.position)
    else:
        _attach_header("VIEW3D_TOOL_HEADER")
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)
    # Delay the first scan so add-ons enabled after this one are picked up too.
    bpy.app.timers.register(_refresh_timer, first_interval=0.5, persistent=True)


def unregister():
    global _signature
    if bpy.app.timers.is_registered(_refresh_timer):
        bpy.app.timers.unregister(_refresh_timer)
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    _detach_header()
    _proxies.clear()
    _slot_categories.clear()
    _unregister_slots()
    _category_panels.clear()
    _children.clear()
    _signature = None
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
