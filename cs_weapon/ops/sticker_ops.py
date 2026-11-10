# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Stickers in the weapon's sticker slots (in the weapon material, as in
the game; nodes/sticker_layer.py).

The slots of a weapon come from its material in the catalog
(Variant.sticker_slots), separate for the HD and the legacy mesh. A
weapon object keeps its stickers in ``obj.cs_weapon.stickers``; the slot
presets used last are stored with each entry (PROP_PRESET), so a saved
file keeps its stickers without the weapon library."""

import json

import bpy
from bpy.props import IntProperty

from .. import utils
from ..library import catalog as C
from ..library import registry, sticker_registry
from ..library import stickers as ST
from ..nodes import sticker_layer
from . import sticker_mesh
from .spawn import uses_legacy

PROP_PRESET = "cs_preset"  # on sticker entries: [offset x, offset y, scale, rotation]


def _stored(value):
    """A preset stored on an entry (before 0.7.6 without rotation)."""
    if value is None or len(value) not in (3, 4):
        return None
    preset = tuple(float(v) for v in value)
    return preset if len(preset) == 4 else preset + (0.0,)


def slot_presets(obj):
    """{slot: (offset x, offset y, scale, rotation in turns)} of the weapon's
    current mesh, from the catalog, else from the presets stored on the
    entries."""
    skin = registry.get_catalog().get_skin(obj.get(utils.PROP_SKIN, "")) if obj is not None else None
    if skin is not None:
        var = skin.weapon.variant(uses_legacy(skin))
        return {s["slot"]: (s["offset"][0], s["offset"][1], s["scale"], s.get("rotation", 0.0))
                for s in var.sticker_slots()}
    out = {}
    for item in obj.cs_weapon.stickers:
        preset = _stored(item.get(PROP_PRESET))
        if preset is not None:
            out[anchor_of(item)] = preset
    return out


# A link can place a sticker relative to a slot the weapon has no preset
# for (the test Glock of #22: slot 5, shown by the game). Such stickers
# take the preset of the first slot, stored on the entry: compared with the
# game screenshot of the test link (#25) that origin matches within a few
# hundredths, x 0 or the median of the slots did not.
PROP_FREE_PRESET = "cs_free_preset"


def free_preset(presets):
    return presets[min(presets)] if presets else None


def preset_for(item, presets):
    """Preset position of a sticker entry, or None."""
    preset = presets.get(anchor_of(item))
    if preset is None:
        preset = _stored(item.get(PROP_FREE_PRESET))
    return preset


def anchor_of(item):
    """Preset slot whose position the sticker's offsets refer to."""
    return item.slot if item.anchor < 0 else item.anchor


def entry_for(obj, slot):
    for item in obj.cs_weapon.stickers:
        if item.slot == slot:
            return item
    return None


def sticker_of(item):
    try:
        return ST.Sticker.from_dict(json.loads(item.data))
    except (TypeError, ValueError):
        return None


def _values(item, preset):
    """Shader values of an entry: the item's offsets, size and rotation
    (degrees) on top of the slot preset, as the game does; the preset's own
    tilt (slot index 3 of the legacy Glock leans by 0.05 turns) stays."""
    ox, oy, scale, rot = preset
    return {"offset": (ox + item.offset_x, oy + item.offset_y),
            "scale": scale / max(item.size, 1e-3),
            "rotation": rot + item.rotation / 360.0,
            "wear": item.wear}


def _weapon_materials(obj):
    return [s.material for s in obj.material_slots
            if s.material is not None and utils.PROP_SPEC in s.material and s.material.use_nodes]


def _uv_map(obj):
    layers = obj.data.uv_layers if obj.type == "MESH" else ()
    return layers[1].name if len(layers) > 1 else sticker_layer.STICKER_UV_DEFAULT


def sync(obj):
    """Rebuild the sticker layer of the weapon's materials. Returns warnings."""
    if not utils.is_weapon(obj):
        return []
    presets = slot_presets(obj)
    entries, warnings = [], []
    for item in obj.cs_weapon.stickers:
        preset = preset_for(item, presets)
        if preset is None:
            warnings.append(f"{item.name}: this weapon has no sticker slot {anchor_of(item) + 1}")
            continue
        item[PROP_PRESET] = list(preset)
        sticker = sticker_of(item)
        if sticker is None:
            continue
        entries.append(dict(_values(item, preset), slot=item.slot, sticker=sticker))
    as_mesh = obj.cs_weapon.sticker_mode == "MESH"
    for mat in _weapon_materials(obj):
        if (entries and not as_mesh) or sticker_layer.has_layer(mat):
            warnings += sticker_layer.build(mat, [] if as_mesh else entries, _uv_map(obj))
    if as_mesh:
        warnings += sticker_mesh.sync(obj, entries, _uv_map(obj))
    elif sticker_mesh.children(obj):
        sticker_mesh.remove_all(obj)
    return warnings


def update_values(obj):
    """Live update of position, size, rotation and wear (no rebuild)."""
    if not utils.is_weapon(obj):
        return
    if obj.cs_weapon.sticker_mode == "MESH":
        sync(obj)  # position and size change the mesh
        return
    presets = slot_presets(obj)
    for item in obj.cs_weapon.stickers:
        preset = preset_for(item, presets)
        if preset is None:
            continue
        v = _values(item, preset)
        for mat in _weapon_materials(obj):
            sticker_layer.set_values(mat, item.slot, v["offset"], v["scale"], v["rotation"], v["wear"])


def put(obj, slot, sticker, wear=0.0, rotation=0.0, anchor=-1):
    """Put ``sticker`` (ST.Sticker) into layer ``slot``, positioned at the
    preset ``anchor`` (-1: the slot's own). Returns warnings."""
    item = entry_for(obj, slot)
    if item is None:
        item = obj.cs_weapon.stickers.add()
        item.slot = slot
        item["anchor"] = -1
    if anchor >= 0:  # else a replaced sticker keeps its position
        item["anchor"] = -1 if anchor == slot else int(anchor)
    item.name = sticker.full_name
    item.data = json.dumps(sticker.to_dict())
    item["wear"] = float(wear)
    item["rotation"] = float(rotation)
    return sync(obj)


def remove(obj, slot):
    coll = obj.cs_weapon.stickers
    for i, item in enumerate(coll):
        if item.slot == slot:
            coll.remove(i)
            break
    return sync(obj)


def report_warnings(op, warnings):
    for msg in warnings[:5]:
        op.report({"WARNING"}, msg)
    if len(warnings) > 5:
        op.report({"WARNING"}, f"... and {len(warnings) - 5} more (see console)")
    for msg in warnings:
        utils.log.warning(msg)


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

class CSWEAPON_OT_refresh_stickers(bpy.types.Operator):
    """Load (or reload) the sticker library"""
    bl_idname = "cs_weapon.refresh_stickers"
    bl_label = "Refresh Stickers"

    def execute(self, context):
        lib = sticker_registry.refresh(context)
        if not len(lib):
            self.report({"WARNING"}, "No stickers found: set the Sticker Library in the add-on preferences "
                                     "or install cs.sticker")
        else:
            self.report({"INFO"}, f"{len(lib)} stickers")
        return {"FINISHED"}


class CSWEAPON_OT_put_sticker(bpy.types.Operator):
    """Put the sticker picked in the Stickers panel into this slot of the selected weapons"""
    bl_idname = "cs_weapon.put_sticker"
    bl_label = "Put Sticker in Slot"
    bl_options = {"REGISTER", "UNDO"}

    slot: IntProperty(name="Slot", min=0, max=4)

    @classmethod
    def poll(cls, context):
        return utils.active_weapon(context) is not None

    def execute(self, context):
        sticker = sticker_registry.selected_sticker(context) if sticker_registry.is_loaded() else None
        if sticker is None:
            self.report({"ERROR"}, "Pick a sticker in the Stickers panel first")
            return {"CANCELLED"}
        targets = [o for o in context.selected_objects if utils.is_weapon(o)]
        active = utils.active_weapon(context)
        if active not in targets:
            targets.append(active)
        warnings, count = [], 0
        for obj in targets:
            item = entry_for(obj, self.slot)
            if self.slot in slot_presets(obj) or (item is not None and anchor_of(item) in slot_presets(obj)):
                warnings += put(obj, self.slot, sticker)
                count += 1
        report_warnings(self, warnings)
        if not count:
            self.report({"WARNING"}, f"No selected weapon has sticker slot {self.slot + 1}")
            return {"CANCELLED"}
        if not warnings:
            self.report({"INFO"}, f"{sticker.name} in slot {self.slot + 1} of {count} weapon(s)")
        return {"FINISHED"}


class CSWEAPON_OT_remove_sticker(bpy.types.Operator):
    """Remove the sticker from this slot"""
    bl_idname = "cs_weapon.remove_sticker"
    bl_label = "Remove Sticker"
    bl_options = {"REGISTER", "UNDO"}

    slot: IntProperty(name="Slot", min=0, max=4)

    @classmethod
    def poll(cls, context):
        return utils.active_weapon(context) is not None

    def execute(self, context):
        report_warnings(self, remove(utils.active_weapon(context), self.slot))
        return {"FINISHED"}


def move(obj, slot, step):
    """Swap the sticker of layer ``slot`` with layer ``slot + step`` (or
    move it there if that one is empty). Both keep their position: only
    the draw order changes, a higher layer draws on top. Returns warnings
    or None if there is no such layer."""
    other = slot + step
    if not 0 <= other < C.STICKER_SLOTS:
        return None
    moving = [entry for entry in (entry_for(obj, slot), entry_for(obj, other)) if entry is not None]
    if not moving:
        return None
    for entry in moving:
        anchor = anchor_of(entry)
        entry.slot = other if entry.slot == slot else slot
        entry["anchor"] = -1 if anchor == entry.slot else anchor
    return sync(obj)


class CSWEAPON_OT_move_sticker(bpy.types.Operator):
    """Swap this sticker with the one of the slot above or below. The
    position stays, only the draw order changes: higher slots draw on top"""
    bl_idname = "cs_weapon.move_sticker"
    bl_label = "Move Sticker"
    bl_options = {"REGISTER", "UNDO"}

    slot: IntProperty(name="Slot", min=0, max=C.STICKER_SLOTS - 1)
    step: IntProperty(name="Step", default=1, min=-1, max=1)

    @classmethod
    def poll(cls, context):
        return utils.active_weapon(context) is not None

    def execute(self, context):
        warnings = move(utils.active_weapon(context), self.slot, self.step)
        if warnings is None:
            return {"CANCELLED"}
        report_warnings(self, warnings)
        return {"FINISHED"}


class CSWEAPON_OT_show_sticker(bpy.types.Operator):
    """Select this slot's sticker in the sticker picker"""
    bl_idname = "cs_weapon.show_sticker"
    bl_label = "Show Sticker in Picker"

    slot: IntProperty(name="Slot", min=0, max=4)

    def execute(self, context):
        obj = utils.active_weapon(context)
        item = entry_for(obj, self.slot) if obj else None
        sticker = sticker_of(item) if item else None
        if sticker is None:
            return {"CANCELLED"}
        state = context.window_manager.cs_weapon
        state.sticker_search = ""
        state.sticker_collection = "ALL"
        state.sticker_id = sticker.id
        sticker_registry.sync_items(state)
        if sticker_registry.get_sticker(sticker.id) is None:
            self.report({"WARNING"}, f"{sticker.name} is not in the current sticker library")
            return {"CANCELLED"}
        return {"FINISHED"}


classes = (CSWEAPON_OT_refresh_stickers, CSWEAPON_OT_put_sticker, CSWEAPON_OT_remove_sticker,
           CSWEAPON_OT_move_sticker, CSWEAPON_OT_show_sticker)
