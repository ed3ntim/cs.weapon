# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Blender glue for the catalog: the shared Catalog instance, preview icons
and syncing the WindowManager list mirror."""

import bpy
import bpy.utils.previews
from bpy.app.handlers import persistent

from .. import properties, utils
from . import catalog as C

_catalog = C.Catalog()
_previews = None  # bpy.utils.previews collection, keyed by skin id
_loaded = False

# The UI list mirrors only the filtered result (keeps big libraries fast).
MAX_LIST_ITEMS = 500
_match_count = 0


def get_catalog():
    return _catalog


def refresh(context=None):
    """Reload core + bundled + user libraries and rebuild the UI list."""
    global _loaded
    context = context or bpy.context
    _catalog.load(utils.CORE_LIBRARY_DIR, utils.BUNDLED_LIBRARY_DIR, utils.user_library_dir())
    _loaded = True
    properties.set_filter_items(_catalog.weapon_types(), _catalog.styles())
    properties.set_weapon_items(_catalog.weapon_list())
    if _previews is not None:
        _previews.clear()
    wm = context.window_manager
    if wm is not None:
        sync_items(wm.cs_weapon)
    for msg in _catalog.report.warnings:
        utils.log.warning(msg)
    return _catalog


def _filtered(state):
    settings = utils.prefs()
    include_incomplete = settings.include_incomplete if settings else True
    wtype = "" if state.filter_type == "ALL" else state.filter_type
    style = None if state.filter_style == "ALL" else int(state.filter_style)
    weapon = "" if state.filter_weapon == "ALL" else state.filter_weapon
    for skin in _catalog.filter(state.search, wtype, style, weapon):
        missing = _catalog.incomplete(skin)
        if missing and not include_incomplete:
            continue
        yield skin, missing


def match_count():
    return _match_count


def sync_items(state):
    global _match_count
    selected = selected_id(state)
    state.items.clear()
    _match_count = 0
    for skin, missing in _filtered(state):
        _match_count += 1
        if _match_count > MAX_LIST_ITEMS:
            continue
        item = state.items.add()
        item.name = skin.name
        item.skin_id = skin.id
        item.weapon_type = skin.weapon.type
        item.style = skin.paint_kit.style
        item.has_missing = missing
    for i, item in enumerate(state.items):
        if item.skin_id == selected:
            state["index"] = i  # bypass update callback
            break
    else:
        state["index"] = 0
    state["grid_page"] = state["index"] // properties.GRID_PAGE  # grid follows the selection
    if state.get("filter_type", 0) >= len(properties._type_items):
        state["filter_type"] = 0
    if state.get("filter_style", 0) >= len(properties._style_items):
        state["filter_style"] = 0
    if state.get("filter_weapon", 0) >= len(properties._weapon_items):
        state["filter_weapon"] = 0


def selected_id(state):
    if 0 <= state.index < len(state.items):
        return state.items[state.index].skin_id
    return ""


def selected_skin(context):
    return _catalog.get_skin(selected_id(context.window_manager.cs_weapon))


def on_filter_changed(state):
    sync_items(state)


def on_type_changed(state):
    """The weapon list follows the type filter; reset the weapon choice."""
    wtype = "" if state.filter_type == "ALL" else state.filter_type
    properties.set_weapon_items(_catalog.weapon_list(wtype))
    state["filter_weapon"] = 0
    sync_items(state)


def on_selection_changed(state, context):
    skin = _catalog.get_skin(selected_id(state))
    if skin is not None:
        preview_icon(skin)
        state.wear = max(skin.paint_kit.wear_min, min(state.wear, skin.paint_kit.wear_max))


def preview_icon(skin):
    """icon_id for a skin's inventory image (0 if unavailable)."""
    if _previews is None or skin is None or not skin.icon:
        return 0
    if skin.id not in _previews:
        try:
            preview = _previews.load(skin.id, skin.icon, "IMAGE")
            preview.icon_size[:]  # load now, not on a later redraw
        except (OSError, KeyError, RuntimeError):
            return 0
    return _previews[skin.id].icon_id


@persistent
def _on_load_post(_dummy):
    if _loaded:
        sync_items(bpy.context.window_manager.cs_weapon)
    else:
        refresh(bpy.context)


def _deferred_refresh():
    try:
        refresh(bpy.context)
    except Exception as exc:  # never let a timer raise
        utils.log.error("cs.weapon: library refresh failed: %s", exc)
    return None


def register():
    global _previews
    _previews = bpy.utils.previews.new()
    bpy.app.handlers.load_post.append(_on_load_post)
    # bpy.context is restricted during register(); refresh right after.
    bpy.app.timers.register(_deferred_refresh, first_interval=0.1)


def unregister():
    global _previews
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    if bpy.app.timers.is_registered(_deferred_refresh):
        bpy.app.timers.unregister(_deferred_refresh)
    if _previews is not None:
        bpy.utils.previews.remove(_previews)
        _previews = None
