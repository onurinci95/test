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
    "version": (1, 5, 0),
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

# For Position = Middle: header method to wrap, whether it is a staticmethod
# taking (layout, context), and whether our tabs go before or after it.
MIDDLE_HOOKS = {
    # Tool settings | spacer | <tabs> spacer | Options
    "VIEW3D_TOOL_HEADER": ("draw_mode_settings", False, "BEFORE"),
    # Menus | spacer | Orientation/Pivot/Snap <tabs> | spacer | Shading
    "VIEW3D_HEADER": ("draw_xform_template", True, "AFTER"),
    # Menus, Workspaces | spacer <tabs> spacer
    "TOPBAR": ("draw_left", False, "AFTER"),
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
_attached_hook = None  # name of the wrapped method when Position = Middle
# Workspace -> preset switching is tracked here, not in properties, because
# it is detected while drawing, where properties can't be written.
_last_workspace = None
_workspace_preset = None  # name of the preset bound to the current workspace
# Tab (and preset showing it) whose edit menu is open. Menus can't take
# arguments, so the tab is remembered when its menu is drawn.
_menu_tab = (None, "")


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
        global _menu_tab
        layout = self.layout
        category = _slot_categories.get(index)
        prefs = _prefs()
        preset = _effective_preset(prefs, context) if prefs else None
        _menu_tab = (category, preset.name if preset else "")
        header = layout.row()
        header.label(text=category)
        header.menu("TOPTABS_MT_tab_edit", text="", icon="DOWNARROW_HLT")
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
    _draw_tabs(self.layout, context)


def _draw_tabs(layout, context, middle=None):
    """Draw the tab buttons. `middle` is the hook's side ("BEFORE"/"AFTER")."""
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

    preset = _effective_preset(prefs, context)
    tabs = preset.tabs if preset is not None else prefs.tabs

    if middle == "AFTER" or (middle is None and prefs.align_right):
        layout.separator_spacer()
    if preset is not None and prefs.show_preset_menu and len(prefs.presets) > 1:
        layout.menu(
            "TOPTABS_MT_presets", text=preset.name,
            icon="WORKSPACE" if preset.name == _workspace_preset else "PRESET",
        )
    style = prefs.button_style
    row = layout.row(align=True)
    for category, label, visible, icon in _ordered_tabs(prefs, tabs):
        if not visible:
            continue
        if not any(_poll(cls, context) for cls in _category_panels.get(category, ())):
            continue
        if style == "TEXT" or not icon:
            icon = "NONE"
        elif style == "ICON":
            label = ""
        if use_sidebar:
            op = row.operator(
                "toptabs.open_sidebar_tab", text=label, icon=icon,
                depress=category == active,
            )
            op.category = category
        else:
            row.popover(panel=_proxies[category], text=label, icon=icon)
    row.operator("toptabs.refresh", text="", icon="FILE_REFRESH", emboss=False)
    if middle is not None:
        layout.separator_spacer()


def _our_wrapper(header, name):
    """Return our wrapper if `header.name` currently is one, else None."""
    try:
        attr = inspect.getattr_static(header, name)
    except AttributeError:
        return None
    func = getattr(attr, "__func__", attr)
    return func if getattr(func, "_toptabs_orig", None) is not None else None


def _wrap_middle(header, location):
    name, is_static, side = MIDDLE_HOOKS[location]
    orig = inspect.getattr_static(header, name)
    orig_func = getattr(orig, "__func__", orig)

    if is_static:
        def wrapper(layout, context):
            if side == "BEFORE":
                _draw_tabs(layout, context, middle=side)
            orig_func(layout, context)
            if side == "AFTER":
                _draw_tabs(layout, context, middle=side)
    else:
        def wrapper(self, context):
            if side == "BEFORE":
                _draw_tabs(self.layout, context, middle=side)
            orig_func(self, context)
            if side == "AFTER":
                _draw_tabs(self.layout, context, middle=side)

    wrapper._toptabs_orig = orig
    setattr(header, name, staticmethod(wrapper) if is_static else wrapper)


def _attach_header(location, position="END"):
    global _attached_header, _attached_hook
    _detach_header()
    header = getattr(bpy.types, HEADER_TYPES[location], None)
    if header is None:
        return
    hook = MIDDLE_HOOKS.get(location)
    if position == "MIDDLE" and hook and hasattr(header, hook[0]):
        _wrap_middle(header, location)
        _attached_hook = hook[0]
    elif position == "START":
        header.prepend(_draw_header)
    else:
        # END, or MIDDLE on a header that doesn't have the expected method.
        header.append(_draw_header)
    _attached_header = header


def _detach_header():
    global _attached_header, _attached_hook
    if _attached_header is not None:
        try:
            _attached_header.remove(_draw_header)
        except Exception:
            pass
        if _attached_hook:
            wrapper = _our_wrapper(_attached_header, _attached_hook)
            if wrapper is not None:
                setattr(_attached_header, _attached_hook, wrapper._toptabs_orig)
        _attached_header = None
        _attached_hook = None


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
    if header is _attached_header:
        if _attached_hook:
            if _our_wrapper(header, _attached_hook) is not None:
                return False
        else:
            draw_funcs = getattr(getattr(header, "draw", None), "_draw_funcs", None) or ()
            if _draw_header in draw_funcs:
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


def _edit_preset(prefs):
    """The preset selected in the preferences (and in the header menu)."""
    if 0 <= prefs.preset_index < len(prefs.presets):
        return prefs.presets[prefs.preset_index]
    return prefs.presets[0] if len(prefs.presets) else None


def _edit_tabs(prefs):
    preset = _edit_preset(prefs)
    return preset.tabs if preset is not None else prefs.tabs


def _effective_preset(prefs, context):
    """Preset shown in the header: the one bound to the current workspace,
    picked when the user switches to that workspace, else the selected one."""
    global _last_workspace, _workspace_preset
    workspace = getattr(context, "workspace", None)
    name = workspace.name if workspace else None
    if name != _last_workspace:
        _last_workspace = name
        bound = next((p for p in prefs.presets if name and p.workspace == name), None)
        _workspace_preset = bound.name if bound else None
    if _workspace_preset:
        bound = prefs.presets.get(_workspace_preset)
        if bound is not None:
            return bound
    return _edit_preset(prefs)


def _ordered_tabs(prefs, tabs):
    """Detected tabs as (category, label, visible, icon), in the user's order.

    Tabs the user hasn't seen in the list yet go last and are visible, unless
    they are in the legacy comma separated "Hidden Tabs" string.
    """
    result = []
    seen = set()
    for item in tabs:
        if item.name in _proxies and item.name not in seen:
            seen.add(item.name)
            result.append((item.name, item.label.strip() or item.name, item.visible, _safe_icon(item.icon, "")))
    legacy_hidden = _excluded_categories(prefs)
    for name in _proxies:
        if name not in seen:
            result.append((name, name, name.lower() not in legacy_hidden, ""))
    return result


def _sync_tabs():
    """Add newly detected tabs to the preferences list.

    Not called from draw callbacks, where writing properties is not allowed.
    """
    prefs = _prefs()
    if prefs is None:
        return
    legacy_hidden = _excluded_categories(prefs)

    if not len(prefs.presets):
        # First run, or upgrade from <= 1.3.0: the old single list becomes
        # the "Default" preset.
        preset = prefs.presets.add()
        preset.name = "Default"
        _copy_tabs(prefs.tabs, preset.tabs)
        prefs.preset_index = 0

    for preset in prefs.presets:
        known = {item.name for item in preset.tabs}
        for name in _proxies:
            if name not in known:
                item = preset.tabs.add()
                item.name = name
                item.visible = name.lower() not in legacy_hidden
        if legacy_hidden:
            for item in preset.tabs:
                if item.name.lower() in legacy_hidden:
                    item.visible = False
    if prefs.excluded_categories:
        # Migrated into the lists above.
        prefs.excluded_categories = ""


def _copy_tabs(source, target):
    target.clear()
    for src in source:
        item = target.add()
        item.name = src.name
        item.visible = src.visible
        item.label = src.label


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

    @classmethod
    def description(cls, _context, properties):
        return f"Open '{properties.category}' in the sidebar (N panel). Click again to close it.\nRight-click to edit this tab"

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
        owner = _edit_preset(prefs) or prefs
        tabs = owner.tabs
        index = owner.tabs_index
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
            owner.tabs_index = target
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
        for item in _edit_tabs(_prefs()):
            item.visible = self.visible
        _redraw_all()
        return {"FINISHED"}


class TOPTABS_OT_sort_tabs(bpy.types.Operator):
    bl_idname = "toptabs.sort_tabs"
    bl_label = "Sort Tabs A-Z"
    bl_description = "Sort the tab list alphabetically"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        tabs = _edit_tabs(_prefs())
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
        for owner in list(prefs.presets) + [prefs]:
            for index in reversed(range(len(owner.tabs))):
                if owner.tabs[index].name not in _proxies:
                    owner.tabs.remove(index)
            owner.tabs_index = min(owner.tabs_index, max(0, len(owner.tabs) - 1))
        return {"FINISHED"}


def _unique_preset_name(prefs, base):
    names = {p.name for p in prefs.presets}
    if base not in names:
        return base
    i = 2
    while f"{base} {i}" in names:
        i += 1
    return f"{base} {i}"


class TOPTABS_OT_preset_add(bpy.types.Operator):
    bl_idname = "toptabs.preset_add"
    bl_label = "Add Preset"
    bl_description = "Add a new preset, starting as a copy of the selected one"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        prefs = _prefs()
        source = _edit_preset(prefs)
        preset = prefs.presets.add()
        preset.name = _unique_preset_name(prefs, source.name if source else "Preset")
        _copy_tabs(source.tabs if source else prefs.tabs, preset.tabs)
        prefs.preset_index = len(prefs.presets) - 1
        _sync_tabs()
        _redraw_all()
        return {"FINISHED"}


class TOPTABS_OT_preset_remove(bpy.types.Operator):
    bl_idname = "toptabs.preset_remove"
    bl_label = "Remove Preset"
    bl_description = "Remove the selected preset"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        prefs = _prefs()
        return prefs is not None and len(prefs.presets) > 1

    def execute(self, context):
        prefs = _prefs()
        prefs.presets.remove(prefs.preset_index)
        prefs.preset_index = min(prefs.preset_index, len(prefs.presets) - 1)
        _redraw_all()
        return {"FINISHED"}


class TOPTABS_OT_preset_activate(bpy.types.Operator):
    bl_idname = "toptabs.preset_activate"
    bl_label = "Switch Preset"
    bl_description = "Show this preset's tabs in the header"
    bl_options = {"INTERNAL"}

    index: IntProperty()

    def execute(self, context):
        global _workspace_preset
        prefs = _prefs()
        if not 0 <= self.index < len(prefs.presets):
            return {"CANCELLED"}
        prefs.preset_index = self.index
        # A manual choice wins until the user switches workspace again.
        _workspace_preset = None
        _redraw_all()
        return {"FINISHED"}


class TOPTABS_MT_presets(bpy.types.Menu):
    bl_label = "Tab Presets"

    def draw(self, context):
        layout = self.layout
        prefs = _prefs()
        current = _effective_preset(prefs, context)
        for index, preset in enumerate(prefs.presets):
            text = preset.name + (f"   ({preset.workspace})" if preset.workspace else "")
            layout.operator(
                "toptabs.preset_activate", text=text,
                icon="CHECKMARK" if preset == current else "BLANK1",
            ).index = index
        layout.separator()
        layout.operator("preferences.addon_show", text="Edit Presets...", icon="PREFERENCES").module = ADDON_ID


class TOPTABS_UL_presets(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "name", text="", emboss=False, icon="PRESET")
        row.prop_search(item, "workspace", bpy.data, "workspaces", text="", icon="WORKSPACE")


def _tab_owner(preset_name):
    prefs = _prefs()
    if preset_name:
        preset = prefs.presets.get(preset_name)
        if preset is not None:
            return preset
    return _edit_preset(prefs) or prefs


def _find_tab(owner, category):
    for index, item in enumerate(owner.tabs):
        if item.name == category:
            return index, item
    item = owner.tabs.add()
    item.name = category
    return len(owner.tabs) - 1, item


class _TabOperator:
    bl_options = {"INTERNAL"}

    category: StringProperty()
    preset: StringProperty()


class TOPTABS_OT_tab_hide(_TabOperator, bpy.types.Operator):
    bl_idname = "toptabs.tab_hide"
    bl_label = "Hide Tab"
    bl_description = "Hide this tab from the header (it stays in the sidebar)"

    def execute(self, context):
        _find_tab(_tab_owner(self.preset), self.category)[1].visible = False
        _redraw_all()
        return {"FINISHED"}


class TOPTABS_OT_tab_show(_TabOperator, bpy.types.Operator):
    bl_idname = "toptabs.tab_show"
    bl_label = "Show Tab"
    bl_description = "Show this tab in the header again"

    def execute(self, context):
        _find_tab(_tab_owner(self.preset), self.category)[1].visible = True
        _redraw_all()
        return {"FINISHED"}


class TOPTABS_OT_tab_move(_TabOperator, bpy.types.Operator):
    bl_idname = "toptabs.tab_move"
    bl_label = "Move Tab"
    bl_description = "Move this tab in the header"

    direction: EnumProperty(items=[
        ("FIRST", "First", ""), ("LEFT", "Left", ""), ("RIGHT", "Right", ""), ("LAST", "Last", ""),
    ])

    def execute(self, context):
        owner = _tab_owner(self.preset)
        index, _item = _find_tab(owner, self.category)
        # Move among the buttons actually shown, skipping hidden/unloaded ones.
        shown = [i for i, t in enumerate(owner.tabs) if t.visible and t.name in _proxies]
        if index not in shown:
            return {"CANCELLED"}
        pos = shown.index(index)
        target_pos = {
            "FIRST": 0, "LEFT": pos - 1, "RIGHT": pos + 1, "LAST": len(shown) - 1,
        }[self.direction]
        target_pos = max(0, min(len(shown) - 1, target_pos))
        if target_pos != pos:
            owner.tabs.move(index, shown[target_pos])
            _redraw_all()
        return {"FINISHED"}


class TOPTABS_OT_tab_rename(_TabOperator, bpy.types.Operator):
    bl_idname = "toptabs.tab_rename"
    bl_label = "Rename Tab"
    bl_description = "Change the name shown on this tab's button"

    label: StringProperty(name="Name", description="Leave empty to use the tab's own name")

    def invoke(self, context, event):
        item = _find_tab(_tab_owner(self.preset), self.category)[1]
        self.label = item.label or self.category
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "label")
        layout.label(text=f"Sidebar tab: {self.category}", icon="INFO")

    def execute(self, context):
        label = self.label.strip()
        item = _find_tab(_tab_owner(self.preset), self.category)[1]
        item.label = "" if label in ("", self.category) else label
        _redraw_all()
        return {"FINISHED"}


_icon_items = []
_valid_icons = set()


def _safe_icon(name, fallback="NONE"):
    """`name` if Blender knows this icon, else `fallback` (an unknown icon
    name raises an error while drawing and would break the whole header)."""
    if not name:
        return fallback
    if not _valid_icons:
        _valid_icons.update(
            bpy.types.UILayout.bl_rna.functions["prop"].parameters["icon"].enum_items.keys()
        )
    return name if name in _valid_icons else fallback


def _icon_enum_items(_self, _context):
    # Kept in a module level list: Blender needs enum item strings to outlive
    # the callback.
    if not _icon_items:
        icons = bpy.types.UILayout.bl_rna.functions["prop"].parameters["icon"].enum_items
        _icon_items.append(("NONE", "No Icon", "Don't show an icon", "BLANK1", 0))
        for number, icon in enumerate(icons, start=1):
            if icon.identifier != "NONE":
                _icon_items.append((icon.identifier, icon.identifier.replace("_", " ").title(), "", icon.identifier, number))
    return _icon_items


class TOPTABS_OT_tab_set_icon(_TabOperator, bpy.types.Operator):
    bl_idname = "toptabs.tab_set_icon"
    bl_label = "Set Tab Icon"
    bl_description = "Pick an icon for this tab's button"
    bl_property = "icon"

    icon: EnumProperty(items=_icon_enum_items)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        item = _find_tab(_tab_owner(self.preset), self.category)[1]
        item.icon = "" if self.icon == "NONE" else self.icon
        _redraw_all()
        return {"FINISHED"}


class TOPTABS_OT_tab_toggle_preset(_TabOperator, bpy.types.Operator):
    bl_idname = "toptabs.tab_toggle_preset"
    bl_label = "Show in Preset"
    bl_description = "Show or hide this tab in the given preset"

    def execute(self, context):
        item = _find_tab(_tab_owner(self.preset), self.category)[1]
        item.visible = not item.visible
        _redraw_all()
        return {"FINISHED"}


def _set_tab_props(op, category, preset):
    op.category = category
    op.preset = preset
    return op


def _draw_tab_edit(layout, category, preset):
    """Items of the edit menu for one tab (right-click / popover ⌄ menu)."""
    global _menu_tab
    _menu_tab = (category, preset)
    layout.label(text=f"Tab: {category}" + (f"  ·  Preset: {preset}" if preset else ""), icon="MENU_PANEL")
    layout.separator()
    _set_tab_props(layout.operator("toptabs.tab_rename", text="Rename...", icon="SORTALPHA"), category, preset)
    _set_tab_props(layout.operator("toptabs.tab_set_icon", text="Set Icon...", icon="IMAGE_DATA"), category, preset)
    layout.separator()
    for direction, text, icon in (
        ("LEFT", "Move Left", "TRIA_LEFT"), ("RIGHT", "Move Right", "TRIA_RIGHT"),
        ("FIRST", "Move to Start", "TRIA_LEFT_BAR"), ("LAST", "Move to End", "TRIA_RIGHT_BAR"),
    ):
        _set_tab_props(layout.operator("toptabs.tab_move", text=text, icon=icon), category, preset).direction = direction
    layout.separator()
    _set_tab_props(layout.operator("toptabs.tab_hide", text="Hide", icon="HIDE_ON"), category, preset)
    prefs = _prefs()
    if len(prefs.presets) > 1:
        layout.menu("TOPTABS_MT_tab_presets", icon="PRESET")
    layout.menu("TOPTABS_MT_hidden_tabs", icon="HIDE_OFF")
    layout.separator()
    layout.operator("preferences.addon_show", text="Edit Tabs...", icon="PREFERENCES").module = ADDON_ID


class TOPTABS_MT_tab_edit(bpy.types.Menu):
    bl_label = "Edit Tab"

    def draw(self, context):
        category, preset = _menu_tab
        if category:
            _draw_tab_edit(self.layout, category, preset)


class TOPTABS_MT_tab_presets(bpy.types.Menu):
    bl_label = "Show in Preset"

    def draw(self, context):
        category, _preset = _menu_tab
        for preset in _prefs().presets:
            item = next((t for t in preset.tabs if t.name == category), None)
            shown = item is None or item.visible
            _set_tab_props(
                self.layout.operator(
                    "toptabs.tab_toggle_preset", text=preset.name,
                    icon="CHECKBOX_HLT" if shown else "CHECKBOX_DEHLT",
                ),
                category, preset.name,
            )


class TOPTABS_MT_hidden_tabs(bpy.types.Menu):
    bl_label = "Show Hidden Tabs"

    def draw(self, context):
        _category, preset = _menu_tab
        owner = _tab_owner(preset)
        hidden = [t for t in owner.tabs if not t.visible and t.name in _proxies]
        if not hidden:
            self.layout.label(text="No hidden tabs")
        for item in hidden:
            _set_tab_props(
                self.layout.operator("toptabs.tab_show", text=item.label or item.name, icon=_safe_icon(item.icon)),
                item.name, preset,
            )


def _button_context_menu(self, context):
    """Right-click on a tab button in the header."""
    op = getattr(context, "button_operator", None)
    if op is None or getattr(op.bl_rna, "identifier", "") != "TOPTABS_OT_open_sidebar_tab":
        return
    prefs = _prefs()
    preset = _effective_preset(prefs, context) if prefs else None
    layout = self.layout
    layout.separator()
    _draw_tab_edit(layout, op.category, preset.name if preset else "")


class TOPTABS_UL_tabs(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        loaded = item.name in _proxies
        row = layout.row(align=True)
        row.prop(
            item, "visible", text="", emboss=False,
            icon="HIDE_OFF" if item.visible else "HIDE_ON",
        )
        op = row.operator("toptabs.tab_set_icon", text="", icon=_safe_icon(item.icon, "BLANK1"), emboss=False)
        op.category = item.name
        op.preset = data.name if isinstance(data, TOPTABS_PG_preset) else ""
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
    icon: StringProperty(
        name="Icon", description="Blender icon shown on the button (empty: no icon)",
        default="", update=_on_draw_update,
    )


class TOPTABS_PG_preset(bpy.types.PropertyGroup):
    # `name` (inherited) is the preset name.
    workspace: StringProperty(
        name="Workspace",
        description="Switch to this preset automatically when this workspace is opened",
        default="", update=_on_draw_update,
    )
    tabs: bpy.props.CollectionProperty(type=TOPTABS_PG_tab)
    tabs_index: IntProperty(default=0)


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
            ("MIDDLE", "Middle", "Centered in the free space of the header"),
            ("END", "End", "After the header's own buttons and other add-ons (right side)"),
        ],
        default="MIDDLE",
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
    # Single list used before presets existed (<= 1.3.0); migrated to "Default".
    tabs: bpy.props.CollectionProperty(type=TOPTABS_PG_tab)
    tabs_index: IntProperty(default=0)
    presets: bpy.props.CollectionProperty(type=TOPTABS_PG_preset)
    preset_index: IntProperty(default=0, update=_on_draw_update)
    button_style: EnumProperty(
        name="Button Style",
        items=[
            ("TEXT", "Text", "Only the tab name"),
            ("ICON_TEXT", "Icon + Text", "Icon (if set) and name"),
            ("ICON", "Icon Only", "Only the icon; tabs without an icon show their name"),
        ],
        default="ICON_TEXT",
        update=_on_draw_update,
    )
    show_preset_menu: BoolProperty(
        name="Preset Menu in Header",
        description="Show a menu to switch presets next to the tabs (when there is more than one preset)",
        default=True, update=_on_draw_update,
    )

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        col = layout.column()
        col.prop(self, "enabled")
        col.prop(self, "location")
        col.prop(self, "position")
        col.prop(self, "click_action")
        col.prop(self, "button_style")
        if self.location == "VIEW3D_TOOL_HEADER":
            col.label(text="If the row is hidden: Viewport > View > Tool Settings", icon="INFO")
        if self.location == "TOPBAR":
            col.label(text="Some panels may not work outside the 3D Viewport", icon="ERROR")
        if self.position != "MIDDLE":
            col.prop(self, "align_right")
        col.prop(self, "include_builtin")
        col.prop(self, "popover_width")
        col.prop(self, "show_preset_menu")

        box = layout.box()
        box.use_property_split = False
        box.label(text="Presets  (optionally bound to a workspace)", icon="PRESET")
        row = box.row()
        row.template_list(
            "TOPTABS_UL_presets", "", self, "presets", self, "preset_index",
            rows=3,
        )
        side = row.column(align=True)
        side.operator("toptabs.preset_add", text="", icon="ADD")
        side.operator("toptabs.preset_remove", text="", icon="REMOVE")

        preset = _edit_preset(self)
        owner = preset or self
        box = layout.box()
        box.use_property_split = False
        header = box.row()
        title = f"Tabs in '{preset.name}'" if preset else "Tabs"
        header.label(text=f"{title}: order, visibility and custom names", icon="MENU_PANEL")
        header.operator("toptabs.refresh", text="", icon="FILE_REFRESH", emboss=False)

        row = box.row()
        row.template_list(
            "TOPTABS_UL_tabs", "", owner, "tabs", owner, "tabs_index",
            rows=max(6, min(len(owner.tabs), 14)),
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

        if not owner.tabs:
            box.label(text="No tabs yet. They appear here a moment after Blender starts.", icon="INFO")
        else:
            box.label(text="Eye: show/hide  ·  Icon: click to pick  ·  Right field: custom name  ·  Arrows: reorder", icon="QUESTION")
            box.label(text="Tip: right-click a tab in the header to rename, move, hide or set its icon", icon="MOUSE_RMB")


classes = (
    TOPTABS_OT_refresh,
    TOPTABS_OT_open_sidebar_tab,
    TOPTABS_OT_move_tab,
    TOPTABS_OT_set_all_visible,
    TOPTABS_OT_sort_tabs,
    TOPTABS_OT_remove_missing,
    TOPTABS_OT_preset_add,
    TOPTABS_OT_preset_remove,
    TOPTABS_OT_preset_activate,
    TOPTABS_MT_presets,
    TOPTABS_OT_tab_hide,
    TOPTABS_OT_tab_show,
    TOPTABS_OT_tab_move,
    TOPTABS_OT_tab_rename,
    TOPTABS_OT_tab_set_icon,
    TOPTABS_OT_tab_toggle_preset,
    TOPTABS_MT_tab_edit,
    TOPTABS_MT_tab_presets,
    TOPTABS_MT_hidden_tabs,
    TOPTABS_UL_tabs,
    TOPTABS_UL_presets,
    TOPTABS_PG_tab,
    TOPTABS_PG_preset,
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
    bpy.types.UI_MT_button_context_menu.append(_button_context_menu)
    # Delay the first scan so add-ons enabled after this one are picked up too.
    bpy.app.timers.register(_refresh_timer, first_interval=0.5, persistent=True)


def unregister():
    global _signature
    if bpy.app.timers.is_registered(_refresh_timer):
        bpy.app.timers.unregister(_refresh_timer)
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    try:
        bpy.types.UI_MT_button_context_menu.remove(_button_context_menu)
    except Exception:
        pass
    _detach_header()
    _proxies.clear()
    _slot_categories.clear()
    _unregister_slots()
    _category_panels.clear()
    _children.clear()
    _signature = None
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
