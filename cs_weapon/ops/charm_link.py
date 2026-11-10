# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Charm at the weapon's keychain attachment, through an installed cs.charm.

cs.charm owns the charms (library, materials, patterns, hanging and
physics). cs.weapon only knows where the game hangs a charm: the default
spot of the model's KeychainMarkup (exported as attachment
``keychain_markup``/``keychain_markup_legacy``), else the older
``keychain`` attachment (``keychain_legacy`` on the legacy mesh). It puts the 3D cursor there, facing out of the weapon's left side
(the side the game shows), and runs cs.charm's "Manual (at Cursor)" with
the weapon as target, so the charm hangs from the weapon like one placed
by hand in cs.charm. The charm is picked in cs.charm's Library panel.
"""

import bpy
from mathutils import Matrix, Vector

from .. import utils
from ..library import registry
from . import modules
from .spawn import template_matrix, uses_legacy

ATTACHMENTS = ("keychain_markup", "keychain")  # preferred first


def charm_addon_available():
    try:
        bpy.ops.cs_charm.spawn_at_cursor.get_rna_type()
    except (AttributeError, KeyError):
        return False
    return True


def keychain_matrix(obj):
    """World matrix for the charm cursor (location at the attachment, -Y out
    of the weapon's left side, Z up), or None without attachment."""
    skin = registry.get_catalog().get_skin(obj.get(utils.PROP_SKIN, "")) if obj is not None else None
    if skin is None:
        return None
    legacy = uses_legacy(skin)
    local = next((m for m in (modules.attachment_local(skin.weapon, name, legacy) for name in ATTACHMENTS)
                  if m is not None), None)
    if local is None:
        return None
    world = obj.matrix_world @ template_matrix(obj.data).inverted_safe() @ local
    rot = obj.matrix_world.to_3x3()
    out = (rot @ Vector((1.0, 0.0, 0.0))).normalized()  # weapon's left side (Source +y)
    up = (rot @ Vector((0.0, 0.0, 1.0))).normalized()
    y = -out
    x = y.cross(up).normalized()
    z = x.cross(y).normalized()
    m = Matrix((x, y, z)).transposed().to_4x4()
    m.translation = world.translation
    return m


def _charm_module(name):
    """A module of the installed cs.charm (legacy add-on or extension)."""
    import sys
    for key, mod in list(sys.modules.items()):
        if key == f"cs_charm.{name}" or key.endswith(f".cs_charm.{name}"):
            return mod
    return None


def hang(context, weapon):
    """Hang cs.charm's selected charm at the weapon's keychain attachment.
    Returns (charm object or None, error text)."""
    if not charm_addon_available():
        return None, "Charms need the cs.charm add-on"
    matrix = keychain_matrix(weapon)
    if matrix is None:
        return None, "This weapon has no charm attachment"
    cursor = context.scene.cursor
    saved = cursor.matrix.copy()
    for obj in context.selected_objects:
        obj.select_set(False)
    weapon.select_set(True)
    context.view_layer.objects.active = weapon
    cursor.matrix = matrix
    try:
        result = bpy.ops.cs_charm.spawn_at_cursor()
    except RuntimeError as exc:
        return None, str(exc)
    finally:
        cursor.matrix = saved
    if "FINISHED" not in result:
        return None, "cs.charm could not spawn the charm"
    charm = context.view_layer.objects.active
    if charm is not None and hasattr(charm, "cs_charm") and hasattr(charm.cs_charm, "rest_on_surface"):
        # On a weapon the charm hangs freely from its spot, as in the game;
        # resting on the surface (cs.charm's default for props) props it up
        # on the slide or receiver.
        charm.cs_charm.rest_on_surface = False
    if charm is not None and charm.parent is weapon:
        from .spawn import follow_rig
        follow_rig(weapon, charm)  # rigged weapon: the charm follows weapon_offset
    return charm, ""


def hang_from_link(context, weapon, keychain):
    """Hang the charm of an inspect link (keychain dict: id = charm
    definition index, pattern, sticker = kit id inside a Sticker Slab) at
    the weapon. Returns an error text or ""."""
    reg = _charm_module("library.registry")
    if reg is None or not charm_addon_available():
        return "charm not placed: needs the cs.charm add-on"
    def_index = int(keychain.get("id", 0))
    charm = next((c for c in reg.get_catalog().charms if c.def_index == def_index), None)
    if charm is None:
        return f"charm {def_index} not in the cs.charm library"
    state = context.window_manager.cs_charm
    for attempt in range(2):
        index = next((i for i, it in enumerate(state.items) if it.charm_id == charm.id), None)
        if index is not None:
            break
        state.search = ""  # not in the filtered list: clear the filters once
        state.filter_collection = "ALL"
        state.filter_rarity = "ALL"
    if index is None:
        return f"charm {charm.id} not in the cs.charm list"
    state.index = index
    note = ""
    picked = state.sticker_id
    if keychain.get("sticker"):
        # Sticker Slab: cs.charm fills new slabs with the picked sticker.
        sticker = _slab_sticker(int(keychain["sticker"]))
        if sticker is not None:
            state.sticker_id = sticker.id
        else:
            note = f"charm without its sticker (kit {keychain['sticker']} not in the cs.charm sticker library)"
    try:
        obj, error = hang(context, weapon)
    finally:
        state.sticker_id = picked
    if obj is None:
        return f"charm not placed: {error}"
    if "pattern" in keychain and hasattr(obj, "cs_charm"):
        obj.cs_charm.pattern = max(0, int(keychain["pattern"]))
    return note


def _slab_sticker(kit_id):
    """cs.charm's sticker with the game's sticker kit id, or None."""
    reg = _charm_module("library.sticker_registry")
    if reg is None:
        return None
    try:
        lib = reg.get_library()
    except (AttributeError, RuntimeError):
        return None
    return next((s for s in lib.stickers if s.kit_id == kit_id), None)


class CSWEAPON_OT_add_charm(bpy.types.Operator):
    """Hang the charm picked in cs.charm at the active weapon's keychain attachment"""
    bl_idname = "cs_weapon.add_charm"
    bl_label = "Add Charm"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and utils.active_weapon(context) is not None

    def execute(self, context):
        obj, error = hang(context, utils.active_weapon(context))
        if obj is None:
            self.report({"ERROR"}, error)
            return {"CANCELLED"}
        return {"FINISHED"}


classes = (CSWEAPON_OT_add_charm,)
