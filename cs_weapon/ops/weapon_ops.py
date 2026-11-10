# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Spawn and edit operators."""

import random

import bpy
from bpy.props import StringProperty

from .. import utils
from ..library import catalog as C
from . import spawn


class CSWEAPON_OT_spawn(bpy.types.Operator):
    bl_idname = "cs_weapon.spawn"
    bl_label = "Spawn Weapon"
    bl_description = "Spawn the selected skin at the 3D cursor"
    bl_options = {"REGISTER", "UNDO"}

    skin_id: StringProperty(options={"SKIP_SAVE"}, description="Skin to spawn (default: list selection)")

    def execute(self, context):
        from ..library import registry
        skin = registry.get_catalog().get_skin(self.skin_id) if self.skin_id else registry.selected_skin(context)
        if skin is None:
            self.report({"WARNING"}, "No skin selected")
            return {"CANCELLED"}
        missing = skin.missing_files()
        if skin.weapon.model_path() in missing:
            self.report({"ERROR"}, f"Model missing: {skin.weapon.model_path()}")
            return {"CANCELLED"}
        state = context.window_manager.cs_weapon
        try:
            spawn.spawn(context, skin, state.seed, state.wear, context.scene.cursor.location.copy(),
                        rigged=state.rigged and skin.weapon.has_rig())
        except RuntimeError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if missing:
            self.report({"WARNING"}, f"{len(missing)} texture(s) missing, e.g. {missing[0]}")
        return {"FINISHED"}


class CSWEAPON_OT_apply_skin(bpy.types.Operator):
    bl_idname = "cs_weapon.apply_skin"
    bl_label = "Apply to Selected"
    bl_description = ("Give the selected weapons the skin selected in the library. "
                      "The mesh (weapon, HD/legacy) is swapped when needed, the pattern stays")
    bl_options = {"REGISTER", "UNDO"}

    skin_id: StringProperty(options={"SKIP_SAVE"}, description="Skin to apply (default: list selection)")

    @classmethod
    def poll(cls, context):
        return any(utils.is_weapon(o) for o in context.selected_objects)

    def execute(self, context):
        from ..library import registry
        skin = registry.get_catalog().get_skin(self.skin_id) if self.skin_id else registry.selected_skin(context)
        if skin is None:
            self.report({"WARNING"}, "No skin selected")
            return {"CANCELLED"}
        if skin.weapon.model_path() in skin.missing_files():
            self.report({"ERROR"}, f"Model missing: {skin.weapon.model_path()}")
            return {"CANCELLED"}
        done = 0
        for obj in context.selected_objects:
            if not utils.is_weapon(obj):
                continue
            try:
                spawn.reskin(obj, skin)
            except RuntimeError as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
            done += 1
        self.report({"INFO"}, f"{skin.name} applied to {done} weapon(s)")
        return {"FINISHED"}


def sticker_layers(stickers, count=C.STICKER_SLOTS):
    """Layer per inspect-link sticker. The layers follow the order of the
    link, which is the game's draw order: the last sticker covers the
    earlier ones, as the highest layer covers the others (checked by ed3n
    in the game with the test link of #22). Stickers listed by ascending slot keep their slot
    (same order, no "at slot" notes); freely placed ones can share a slot
    and take the layer of their position in the link. None past the last
    layer."""
    slots = [int(st.get("slot", 0)) for st in stickers]
    if all(0 <= s < count for s in slots) and all(a < b for a, b in zip(slots, slots[1:])):
        return slots
    return [i if i < count else None for i in range(len(stickers))]


def _place_stickers(obj, stickers):
    """Stickers of an inspect link into the weapon's slots, matched to the
    sticker library by sticker kit id. Returns (placed, not in the sticker
    library, without a position on this weapon)."""
    if not stickers:
        return 0, 0, 0
    from ..library import sticker_registry
    from . import sticker_ops
    lib = sticker_registry.get_library()
    presets = sticker_ops.slot_presets(obj)
    placed = missing = no_slot = 0
    stand_in = False
    for st, layer in zip(stickers, sticker_layers(stickers)):
        sticker = lib.by_kit_id(st.get("id", 0))
        anchor = int(st.get("slot", 0))
        if sticker is None:
            missing += 1
            continue
        if layer is None:
            no_slot += 1
            continue
        fallback = None
        if anchor not in presets:
            fallback = sticker_ops.free_preset(presets)
            if fallback is None:
                no_slot += 1
                continue
        sticker_ops.put(obj, layer, sticker, wear=min(max(st.get("wear", 0.0), 0.0), 1.0),
                        rotation=st.get("rotation", 0.0), anchor=anchor)
        entry = sticker_ops.entry_for(obj, layer)
        if fallback is not None:
            entry[sticker_ops.PROP_FREE_PRESET] = list(fallback)
        # Free placement: offset_x/y shift the sticker from its slot's preset
        # position (the stickers of a test link have y near 0 while the
        # presets sit at y = -0.33, so they are not absolute)
        entry["offset_x"] = min(max(float(st.get("offset_x", 0.0)), -1.0), 1.0)
        entry["offset_y"] = min(max(float(st.get("offset_y", 0.0)), -1.0), 1.0)
        if st.get("scale"):
            entry["size"] = min(max(float(st["scale"]), 0.1), 4.0)
        sticker_ops.update_values(obj)
        placed += 1
        stand_in = stand_in or fallback is not None
    if stand_in:  # put() built the layer before the stand-in preset was set
        sticker_ops.sync(obj)
    return placed, missing, no_slot


class CSWEAPON_OT_import_inspect(bpy.types.Operator):
    bl_idname = "cs_weapon.import_inspect"
    bl_label = "Import Inspect Link"
    bl_description = ("Spawn the item of a CS2 preview link (csgo_econ_action_preview with hex data) "
                      "with its pattern, float, StatTrak, name tag and stickers")
    bl_options = {"REGISTER", "UNDO"}

    link: StringProperty(name="Link", description="Inspect/preview link, console command or hex data")

    def invoke(self, context, event):
        if self.link.strip():  # from the panel's link field
            return self.execute(context)
        return context.window_manager.invoke_props_dialog(self, width=520)

    def execute(self, context):
        from ..library import inspect_link, registry
        try:
            item = inspect_link.parse_item(self.link)
        except (inspect_link.LinkError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        cat = registry.get_catalog()
        skin = cat.skin_for_item(item["defindex"], item["paintindex"])
        if skin is None:
            self.report({"ERROR"}, f"Item {item['defindex']} with paint kit {item['paintindex']} "
                                   "is not in the library")
            return {"CANCELLED"}
        if skin.weapon.model_path() in skin.missing_files():
            self.report({"ERROR"}, f"Model missing: {skin.weapon.model_path()}")
            return {"CANCELLED"}
        kit = skin.paint_kit
        wear = min(max(item["paintwear"], kit.wear_min), kit.wear_max)
        try:
            obj = spawn.spawn(context, skin, min(item["paintseed"], 1000), wear,
                              context.scene.cursor.location.copy(), rigged=context.window_manager.cs_weapon.rigged)
        except RuntimeError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        props = obj.cs_weapon
        if item["stattrak"] is not None:
            props.stattrak_count = min(item["stattrak"], 999999)
            props.stattrak = True
        if item["nametag"]:
            props.nametag = item["nametag"][:20]
        placed, missing, no_slot = _place_stickers(obj, item["stickers"])
        note = f", {placed} sticker(s)" if placed else ""
        problem = bool(missing or no_slot)
        if missing:
            note += f", {missing} sticker(s) not in the sticker library"
        if no_slot:
            note += f", {no_slot} sticker(s) on a slot this weapon has no position for"
        if item["keychains"]:
            from . import charm_link
            weapon = obj
            for kc in item["keychains"][:1]:  # one charm attachment per weapon
                error = charm_link.hang_from_link(context, weapon, kc)
                note += f", {error}" if error else ", charm"
                problem = problem or bool(error)
            context.view_layer.objects.active = weapon
        if not item["checksum_ok"]:
            note += ", checksum mismatch"
        self.report({"WARNING" if problem else "INFO"},
                    f"{skin.name}, pattern {item['paintseed']}, float {item['paintwear']:.6f}{note}")
        return {"FINISHED"}


class CSWEAPON_OT_random_seed(bpy.types.Operator):
    bl_idname = "cs_weapon.random_seed"
    bl_label = "Random Pattern"
    bl_description = "Give the selected weapons a random pattern template"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return any(utils.is_weapon(o) for o in context.selected_objects)

    def execute(self, context):
        for obj in context.selected_objects:
            if utils.is_weapon(obj):
                obj.cs_weapon.seed = random.randint(0, 1000)
        return {"FINISHED"}


class CSWEAPON_OT_pick_skin(bpy.types.Operator):
    """Select this skin"""
    bl_idname = "cs_weapon.pick_skin"
    bl_label = "Pick Skin"
    bl_options = {"INTERNAL"}

    index: bpy.props.IntProperty(min=0)

    @classmethod
    def description(cls, context, properties):
        items = context.window_manager.cs_weapon.items
        return items[properties.index].name if properties.index < len(items) else cls.__doc__

    def execute(self, context):
        state = context.window_manager.cs_weapon
        if self.index >= len(state.items):
            return {"CANCELLED"}
        state.index = self.index
        return {"FINISHED"}


class CSWEAPON_OT_grid_page(bpy.types.Operator):
    """Show the previous or next page of skins"""
    bl_idname = "cs_weapon.grid_page"
    bl_label = "Grid Page"
    bl_options = {"INTERNAL"}

    step: bpy.props.IntProperty(default=1)

    def execute(self, context):
        from .. import properties as P
        state = context.window_manager.cs_weapon
        pages = max(1, -(-len(state.items) // P.GRID_PAGE))
        state.grid_page = min(max(state.grid_page + self.step, 0), pages - 1)
        return {"FINISHED"}


classes = (CSWEAPON_OT_spawn, CSWEAPON_OT_apply_skin, CSWEAPON_OT_import_inspect, CSWEAPON_OT_random_seed,
           CSWEAPON_OT_pick_skin, CSWEAPON_OT_grid_page)
