# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Property groups.

* ``WindowManager.cs_weapon``  runtime library state (list mirror, filters,
  selection) and the settings for the next spawn. Not saved in files.
* ``Object.cs_weapon``         per-weapon state: pattern seed, wear,
  StatTrak, nametag and the stickers in the slots. Update callbacks
  re-apply the material at once.
"""

import bpy
from bpy.props import (
    BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
    IntProperty, PointerProperty, StringProperty,
)

from .library import catalog as C

# Dynamic enum items must stay referenced from Python, otherwise Blender may
# read freed strings. The registry refreshes these lists.
_type_items = [("ALL", "All Types", "")]
_style_items = [("ALL", "All Styles", "")]
_weapon_items = [("ALL", "All Weapons", "")]
_sticker_collection_items = [("ALL", "All Collections", "")]


def set_sticker_collections(collections):
    _sticker_collection_items[:] = [("ALL", "All Collections", "")] + [
        (n, n, f"Only stickers from {n}") for n in collections]


def _sticker_collection_enum(self, context):
    return _sticker_collection_items


def _on_sticker_filter(self, context):
    from .library import sticker_registry
    sticker_registry.on_filter_changed(self)


def _on_sticker_select(self, context):
    from .library import sticker_registry
    sticker_registry.on_selection_changed(self)


def set_weapon_items(weapons):
    _weapon_items[:] = [("ALL", "All Weapons", "")] + [
        (wid, name, f"Only {name} skins") for wid, name in weapons]


def set_filter_items(types, styles):
    _type_items[:] = [("ALL", "All Types", "")] + [
        (t, t.title(), f"Only {t} skins") for t in types]
    _style_items[:] = [("ALL", "All Styles", "")] + [
        (str(s), C.STYLES.get(s, str(s)), f"Only {C.STYLES.get(s, s)} skins") for s in styles]


def _type_enum(self, context):
    return _type_items


def _style_enum(self, context):
    return _style_items


def _weapon_enum(self, context):
    return _weapon_items


def _on_type_change(self, context):
    from .library import registry
    registry.on_type_changed(self)


def _on_filter_change(self, context):
    from .library import registry
    registry.on_filter_changed(self)


def _on_select(self, context):
    from .library import registry
    registry.on_selection_changed(self, context)
    # the grid shows the page of the selected skin
    self["grid_page"] = self.index // GRID_PAGE if self.index >= 0 else 0


# Grid view: icon buttons inside the panel, GRID_PAGE skins per page
# (template_icon_view would open a popup; loading an icon costs time once,
# so only one page is drawn).
GRID_COLUMNS = 3
GRID_PAGE = GRID_COLUMNS * 4


class CSWEAPON_PG_item(bpy.types.PropertyGroup):
    """Mirror of one skin of the catalog, displayed by the UIList."""
    # ``name`` (built in) holds the display name.
    skin_id: StringProperty()
    weapon_type: StringProperty()
    style: IntProperty()
    has_missing: BoolProperty(description="Model or textures are missing")


class CSWEAPON_PG_sticker_item(bpy.types.PropertyGroup):
    """Mirror of one sticker of the sticker library (sticker picker)."""
    sticker_id: StringProperty()
    collection: StringProperty()
    finish: StringProperty()


class CSWEAPON_PG_state(bpy.types.PropertyGroup):
    items: CollectionProperty(type=CSWEAPON_PG_item)
    index: IntProperty(name="Skin", default=0, update=_on_select)

    inspect_link: StringProperty(
        name="Inspect Link",
        description="Paste an inspect/preview link, console command or hex data, then spawn it")
    grid: BoolProperty(name="Grid", default=False, description="Show the skins as a grid of icons")
    grid_page: IntProperty(name="Page", default=0, min=0, description="Page of the skin grid")
    search: StringProperty(
        name="Search", description="Filter by weapon or skin name (all words must match)",
        options={"TEXTEDIT_UPDATE"}, update=_on_filter_change,
    )
    filter_type: EnumProperty(name="Type", items=_type_enum, update=_on_type_change)
    filter_weapon: EnumProperty(name="Weapon", items=_weapon_enum, update=_on_filter_change)
    filter_style: EnumProperty(name="Style", items=_style_enum, update=_on_filter_change)

    # Settings for the next spawn
    seed: IntProperty(name="Pattern", default=0, min=0, max=1000,
                      description="Pattern template (paint seed) of the new weapon")
    wear: FloatProperty(name="Float", default=0.01, min=0.0, max=1.0, precision=6,
                        description="Wear rating of the new weapon")
    rigged: BoolProperty(
        name="As Armature", default=False,
        description="Spawn the weapon with its skeleton (bones weapon, bolt, clip, trigger ...) and "
                    "the game's weapon animations as actions, e.g. for cs.anim. Needs a library "
                    "exported with skeletons (export_cs2_weapons.py --rig)")

    # Sticker picker (cs.sticker library)
    sticker_items: CollectionProperty(type=CSWEAPON_PG_sticker_item)
    sticker_index: IntProperty(name="Sticker", default=-1, update=_on_sticker_select)
    sticker_id: StringProperty(name="Sticker", description="Sticker for the weapon's slots")
    sticker_search: StringProperty(
        name="Search", description="Filter stickers by name, collection, finish or rarity",
        options={"TEXTEDIT_UPDATE"}, update=_on_sticker_filter,
    )
    sticker_collection: EnumProperty(name="Collection", items=_sticker_collection_enum,
                                     update=_on_sticker_filter)


def _on_weapon_change(self, context):
    from .nodes import material
    material.update_object(self.id_data)


def _on_module_change(self, context):
    from .ops import modules
    modules.sync(self.id_data)


def _on_sticker_value(self, context):
    from .ops import sticker_ops
    sticker_ops.update_values(self.id_data)


def _on_sticker_mode(self, context):
    from .ops import sticker_ops
    sticker_ops.sync(self.id_data)


class CSWEAPON_PG_sticker(bpy.types.PropertyGroup):
    """A sticker in one slot of a weapon. The sticker itself is stored as
    JSON (``data``), so the weapon keeps it without the sticker library;
    the preset position comes from the weapon's material."""
    # ``name`` (built in) holds the sticker name.
    slot: IntProperty(min=0, max=C.STICKER_SLOTS - 1)  # layer, one sticker each
    # preset position the offsets refer to (-1: the slot's own); freely
    # placed stickers of an inspect link can share one
    anchor: IntProperty(default=-1, min=-1, max=C.STICKER_SLOTS - 1)
    data: StringProperty()
    wear: FloatProperty(name="Wear", default=0.0, min=0.0, max=1.0, subtype="FACTOR",
                        update=_on_sticker_value, description="Scrapes the sticker off (0 = new)")
    rotation: FloatProperty(name="Rotation", default=0.0, min=-360.0, max=360.0, precision=1,
                            update=_on_sticker_value, description="Rotation in degrees")
    offset_x: FloatProperty(name="Offset X", default=0.0, min=-1.0, max=1.0, precision=4, step=0.1,
                            update=_on_sticker_value,
                            description="Shift from the preset position (sticker UV units)")
    offset_y: FloatProperty(name="Offset Y", default=0.0, min=-1.0, max=1.0, precision=4, step=0.1,
                            update=_on_sticker_value,
                            description="Shift from the preset position (sticker UV units)")
    size: FloatProperty(name="Size", default=1.0, min=0.1, max=4.0, update=_on_sticker_value,
                        description="Size relative to the preset slot")


class CSWEAPON_PG_object(bpy.types.PropertyGroup):
    seed: IntProperty(name="Pattern", default=0, min=0, max=1000, update=_on_weapon_change,
                      description="Pattern template (paint seed)")
    wear: FloatProperty(name="Float", default=0.01, min=0.0, max=1.0, precision=6,
                        update=_on_weapon_change, description="Wear rating")
    stattrak: BoolProperty(name="StatTrak", default=False, update=_on_module_change,
                           description="StatTrak module with kill counter")
    stattrak_mode: EnumProperty(
        name="Display", update=_on_module_change,
        items=[("KILLS", "Kills", "Show the kill count"),
               ("ERROR", "ERROR!", "Show the game's error state"),
               ("USER", "USER", "Show the game's state for another owner"),
               ("UNKNOWN", "UNKNWN", "Show the game's unknown state")],
        description="What the StatTrak display shows (weapons only, knives always show kills)")
    stattrak_count: IntProperty(name="Kills", default=0, min=0, max=999999, update=_on_module_change,
                                description="Confirmed kills shown on the StatTrak display")
    nametag: StringProperty(name="Name Tag", default="", maxlen=20, update=_on_module_change,
                            description="Name tag text (empty: no name tag)")
    stickers: CollectionProperty(type=CSWEAPON_PG_sticker)
    sticker_mode: EnumProperty(
        name="Stickers", update=_on_sticker_mode,
        items=[("MATERIAL", "In Material", "Draw the stickers in the weapon material, like the game"),
               ("MESH", "As Meshes", "Own sticker meshes on the slots (child objects), e.g. for export "
                                    "or to edit them separately")],
        description="How the stickers are put on the weapon")


_classes = (CSWEAPON_PG_item, CSWEAPON_PG_sticker_item, CSWEAPON_PG_state, CSWEAPON_PG_sticker,
            CSWEAPON_PG_object)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.cs_weapon = PointerProperty(type=CSWEAPON_PG_state)
    bpy.types.Object.cs_weapon = PointerProperty(type=CSWEAPON_PG_object)


def unregister():
    del bpy.types.Object.cs_weapon
    del bpy.types.WindowManager.cs_weapon
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
