# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Small shared helpers: add-on id, paths, preference access, logging."""

import logging
import os

import bpy

# Package name as Blender sees it ("cs_weapon" for legacy installs,
# "bl_ext.<repo>.cs_weapon" for extensions). Used as AddonPreferences id.
ADDON_ID = __package__
# Name of the module towards a host add-on (cs.blend), whatever the package path.
MODULE_ID = "cs_weapon"

ADDON_DIR = os.path.dirname(os.path.abspath(__file__))
CORE_LIBRARY_DIR = os.path.join(ADDON_DIR, "weapons", "core")
# Only present in personal builds (tools/build_zip.py --with-weapons).
BUNDLED_LIBRARY_DIR = os.path.join(ADDON_DIR, "weapons", "bundled")

# Custom property keys.
PROP_SKIN = "cs_weapon_skin"              # on weapon objects: skin id "weapon:paintkit"
PROP_SPEC = "cs_weapon_spec"              # on weapon materials: JSON copy of the spec
PROP_MODEL = "cs_weapon_model"            # on template meshes: model path + mesh name
PROP_COLLECTION = "cs_weapon_collection"  # on the per-scene weapon collection
PROP_STICKER_MATERIAL = "cs_weapon_sticker"  # on materials of sticker meshes

WEAPON_COLLECTION_NAME = "CS Weapons"

log = logging.getLogger("cs_weapon")


# Host add-on (cs.blend) when registered as its sub-module, else None.
# The host provides settings(module_id) and user_path(module_id, sub, create).
_host = None


def set_host(host):
    global _host
    _host = host


def get_host():
    return _host


def prefs():
    """The settings (preferences.CSWEAPON_PG_settings) or None, e.g. while
    not registered. Standalone they hang off the add-on preferences, in host
    mode the host keeps them."""
    if _host is not None:
        return _host.settings(MODULE_ID)
    addon = bpy.context.preferences.addons.get(ADDON_ID)
    return getattr(addon.preferences, "settings", None) if addon else None


def user_path(sub="", create=True):
    """Folder for the add-on's own user data (caches), or '' if there is
    none (legacy install without host)."""
    try:
        if _host is not None:
            return _host.user_path(MODULE_ID, sub, create=create) or ""
        getter = getattr(bpy.utils, "extension_path_user", None)
        if getter is not None and ADDON_ID.startswith("bl_ext."):
            return getter(ADDON_ID, path=sub, create=create)
    except (ValueError, RuntimeError, OSError):
        pass
    return ""


def user_library_dir():
    """Absolute user library path from the preferences, or '' if unset/invalid."""
    settings = prefs()
    if settings is None or not settings.user_path:
        return ""
    path = bpy.path.abspath(settings.user_path)
    return path if os.path.isdir(path) else ""


def is_weapon(obj):
    return obj is not None and PROP_SKIN in obj


def weapon_collection(context):
    """Get (or create) the collection holding the weapons of this scene."""
    scene = context.scene
    for coll in scene.collection.children_recursive:
        if PROP_COLLECTION in coll:
            return coll
    coll = bpy.data.collections.new(WEAPON_COLLECTION_NAME)
    coll[PROP_COLLECTION] = True
    scene.collection.children.link(coll)
    return coll


def active_weapon(context):
    obj = context.active_object
    return obj if is_weapon(obj) else None


def _sticker_dirs_of(module, settings):
    out = []
    # cs.sticker keeps user_path on its preferences, or (host mode) in settings
    user = getattr(getattr(settings, "settings", settings), "user_path", "") if settings else ""
    if user:
        out.append(bpy.path.abspath(user))
    root = os.path.dirname(os.path.abspath(module.__file__)) if module and module.__file__ else ""
    if root:
        out += [os.path.join(root, "stickers", "bundled"), os.path.join(root, "stickers", "core")]
    return out


def _sticker_addon_dirs():
    """Library folders of an installed cs.sticker: its user folder from the
    preferences, then its bundled and core sets."""
    import sys
    out = []
    for key, addon in bpy.context.preferences.addons.items():
        if key == "cs_sticker" or key.endswith(".cs_sticker"):
            out += _sticker_dirs_of(sys.modules.get(key), getattr(addon, "preferences", None))
    if _host is not None:  # cs.sticker as a sibling module of the same host
        for key, module in list(sys.modules.items()):
            if key.endswith(".cs_sticker") and key not in bpy.context.preferences.addons                     and hasattr(module, "__path__"):
                out += _sticker_dirs_of(module, _host.settings("cs_sticker"))
    return out


def sticker_library_dirs():
    """Sticker library folders, most specific first: the folder from the
    preferences, or (empty) the folders of an installed cs.sticker."""
    settings = prefs()
    path = bpy.path.abspath(settings.sticker_path) if settings is not None and settings.sticker_path else ""
    dirs = [path] if path else _sticker_addon_dirs()
    seen, out = set(), []
    for d in dirs:
        key = os.path.normcase(os.path.abspath(d))
        if key not in seen and os.path.isdir(d):
            seen.add(key)
            out.append(d)
    return out
