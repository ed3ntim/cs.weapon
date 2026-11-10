# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""3D Viewport sidebar panels (tab "cs.weapon"): Library, Active Weapon
with its sticker slots and the sticker picker."""

import bpy

from .. import utils
from ..library import catalog as C
from ..library import registry, sticker_registry, wear

TAB = "cs.weapon"


class _Panel:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = TAB


def _draw_grid(layout, state):
    """Skins of the filtered list as icon buttons, one page at a time."""
    from .. import properties as P
    catalog = registry.get_catalog()
    pages = max(1, -(-len(state.items) // P.GRID_PAGE))
    page = min(state.grid_page, pages - 1)
    first = page * P.GRID_PAGE
    grid = layout.grid_flow(row_major=True, columns=P.GRID_COLUMNS, even_columns=True, even_rows=True,
                            align=True)
    for i in range(first, min(first + P.GRID_PAGE, len(state.items))):
        item = state.items[i]
        icon_id = registry.preview_icon(catalog.get_skin(item.skin_id))
        cell = grid.column(align=True)
        box = cell.box()
        if icon_id:
            box.template_icon(icon_value=icon_id, scale=4.0)
        else:
            box.label(text="", icon="ERROR" if item.has_missing else "MOD_ARRAY")
        name = item.name.split(" | ", 1)[-1]
        op = cell.operator("cs_weapon.pick_skin", text=name, depress=(i == state.index))
        op.index = i
    row = layout.row(align=True)
    sub = row.row(align=True)
    sub.enabled = page > 0
    sub.operator("cs_weapon.grid_page", text="", icon="TRIA_LEFT").step = -1
    row.label(text=f"Page {page + 1} / {pages}")
    sub = row.row(align=True)
    sub.enabled = page < pages - 1
    sub.operator("cs_weapon.grid_page", text="", icon="TRIA_RIGHT").step = 1


class CSWEAPON_PT_library(_Panel, bpy.types.Panel):
    bl_label = "Library"
    bl_idname = "CSWEAPON_PT_library"

    def draw(self, context):
        layout = self.layout
        state = context.window_manager.cs_weapon
        catalog = registry.get_catalog()
        row = layout.row(align=True)
        row.prop(state, "inspect_link", text="", icon="LINKED", placeholder="Inspect link")
        sub = row.row(align=True)
        sub.enabled = bool(state.inspect_link.strip())
        sub.operator("cs_weapon.import_inspect", text="Spawn", icon="IMPORT").link = state.inspect_link
        row = layout.row(align=True)
        row.prop(state, "search", text="", icon="VIEWZOOM")
        row.prop(state, "grid", text="", icon="IMGDISPLAY" if not state.grid else "LONGDISPLAY")
        row = layout.row(align=True)
        row.prop(state, "filter_type", text="")
        row.prop(state, "filter_weapon", text="")
        row.prop(state, "filter_style", text="")
        if state.grid:
            _draw_grid(layout, state)
        else:
            layout.template_list("CSWEAPON_UL_library", "", state, "items", state, "index", rows=8)
        row = layout.row()
        total, shown = registry.match_count(), len(state.items)
        text = f"{total} of {len(catalog.skins)} skins"
        if shown < total:
            text += f" (first {shown})"
        row.label(text=text)
        row.operator("cs_weapon.refresh_library", text="", icon="FILE_REFRESH")

        skin = registry.selected_skin(context)
        if skin is None:
            if not catalog.skins:
                layout.label(text="No skins: set a library folder in the preferences", icon="INFO")
            return
        box = layout.box()
        col = box.column(align=True)
        col.label(text=skin.name, icon="MOD_ARRAY")
        kit = skin.paint_kit
        if skin.is_vanilla:
            col.label(text="Default finish, no pattern or wear")
        else:
            col.label(text=f"{kit.style_name}, float {kit.wear_min:.2f} - {kit.wear_max:.2f}")
        icon_id = registry.preview_icon(skin)
        if icon_id:
            box.template_icon(icon_value=icon_id, scale=6.0)
        if not skin.is_vanilla:
            col = box.column(align=True)
            col.prop(state, "seed")
            row = col.row(align=True)
            row.prop(state, "wear")
            row.label(text=wear.tier(state.wear)[0])
        if skin.weapon.rig:
            box.prop(state, "rigged", icon="ARMATURE_DATA")
        row = box.row(align=True)
        row.operator("cs_weapon.spawn", icon="ADD")
        row.operator("cs_weapon.apply_skin", icon="BRUSH_DATA")


class CSWEAPON_PT_active(_Panel, bpy.types.Panel):
    bl_label = "Active Weapon"
    bl_idname = "CSWEAPON_PT_active"

    @classmethod
    def poll(cls, context):
        return utils.active_weapon(context) is not None

    def draw(self, context):
        layout = self.layout
        obj = utils.active_weapon(context)
        props = obj.cs_weapon
        skin = registry.get_catalog().get_skin(obj.get(utils.PROP_SKIN, ""))
        layout.label(text=skin.name if skin else obj.get(utils.PROP_SKIN, ""), icon="MOD_ARRAY")
        if skin is None or not skin.is_vanilla:
            col = layout.column(align=True)
            row = col.row(align=True)
            row.prop(props, "seed")
            row.operator("cs_weapon.random_seed", text="", icon="FILE_REFRESH")
            row = col.row(align=True)
            row.prop(props, "wear")
            row.label(text=wear.tier(props.wear)[0])
        if skin is not None and skin.weapon.is_glove:
            return  # no StatTrak, name tag, stickers, charm or bake on gloves
        col = layout.column(align=True)
        row = col.row(align=True)
        row.prop(props, "stattrak")
        sub = row.row(align=True)
        sub.enabled = props.stattrak
        sub.prop(props, "stattrak_count", text="")
        if props.stattrak and not (skin and skin.weapon.is_knife):
            col.prop(props, "stattrak_mode")
        col.prop(props, "nametag")
        row = layout.row(align=True)
        if obj.get("cs_weapon_live_material"):
            row.operator("cs_weapon.restore_live", icon="LOOP_BACK")
        else:
            row.operator("cs_weapon.bake", icon="RENDER_STILL")
        from ..ops import charm_link
        if charm_link.keychain_matrix(obj) is not None:
            row = layout.row()
            row.enabled = charm_link.charm_addon_available()
            row.operator("cs_weapon.add_charm", icon="LINKED")
            if not row.enabled:
                layout.label(text="Charms need the cs.charm add-on", icon="INFO")
        from ..ops import modules
        if not modules.available():
            layout.label(text="StatTrak and name tag need an exported library", icon="INFO")


class CSWEAPON_PT_stickers(_Panel, bpy.types.Panel):
    bl_label = "Stickers"
    bl_idname = "CSWEAPON_PT_stickers"
    bl_parent_id = "CSWEAPON_PT_active"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        obj = utils.active_weapon(context)
        skin = registry.get_catalog().get_skin(obj.get(utils.PROP_SKIN, "")) if obj is not None else None
        return obj is not None and not (skin is not None and skin.weapon.is_glove)

    def draw(self, context):
        from ..ops import sticker_ops
        layout = self.layout
        obj = utils.active_weapon(context)
        presets = sticker_ops.slot_presets(obj)
        if not presets:
            layout.label(text="This weapon has no sticker slots", icon="INFO")
            return
        layout.prop(obj.cs_weapon, "sticker_mode", expand=True)
        state = context.window_manager.cs_weapon
        picked = sticker_registry.selected_sticker(context) if sticker_registry.is_loaded() else None
        for slot in sorted(set(presets) | {s.slot for s in obj.cs_weapon.stickers}):
            item = sticker_ops.entry_for(obj, slot)
            box = layout.box()
            row = box.row(align=True)
            where = ""
            if item is not None and sticker_ops.anchor_of(item) != slot:
                where = f" (at slot {sticker_ops.anchor_of(item) + 1})"
            text = f"{slot + 1}: {item.name if item else 'empty'}{where}"
            icon_id = sticker_registry.preview_icon(sticker_ops.sticker_of(item)) if item else 0
            if icon_id:
                row.label(text=text, icon_value=icon_id)
            else:
                row.label(text=text, icon="IMAGE_DATA" if item else "DOT")
            sub = row.row(align=True)
            sub.enabled = picked is not None
            sub.operator("cs_weapon.put_sticker", text="", icon="IMPORT").slot = slot
            if item is not None:
                for icon, step in (("TRIA_UP", -1), ("TRIA_DOWN", 1)):
                    sub = row.row(align=True)
                    sub.enabled = 0 <= slot + step < C.STICKER_SLOTS
                    op = sub.operator("cs_weapon.move_sticker", text="", icon=icon)
                    op.slot, op.step = slot, step
                row.operator("cs_weapon.show_sticker", text="", icon="VIEWZOOM").slot = slot
                row.operator("cs_weapon.remove_sticker", text="", icon="X").slot = slot
                col = box.column(align=True)
                col.prop(item, "wear")
                col.prop(item, "rotation")
                row = col.row(align=True)
                row.prop(item, "offset_x", text="X")
                row.prop(item, "offset_y", text="Y")
                col.prop(item, "size")

        box = layout.box()
        box.label(text="Picker", icon="IMAGE_DATA")
        if not sticker_registry.is_loaded():
            box.label(text=sticker_registry.status_text())
            box.operator("cs_weapon.refresh_stickers", text="Load Sticker Library", icon="FILE_REFRESH")
            return
        box.prop(state, "sticker_search", text="", icon="VIEWZOOM")
        box.prop(state, "sticker_collection", text="")
        box.template_list("CSWEAPON_UL_stickers", "", state, "sticker_items", state, "sticker_index", rows=6)
        row = box.row()
        total, shown = sticker_registry.match_count(), len(state.sticker_items)
        text = f"{total} stickers" + (f" (first {shown})" if shown < total else "")
        row.label(text=text)
        row.operator("cs_weapon.refresh_stickers", text="", icon="FILE_REFRESH")
        if picked is not None:
            icon_id = sticker_registry.preview_icon(picked)
            if icon_id:
                box.template_icon(icon_value=icon_id, scale=4.0)
            box.label(text=f"Picked: {picked.full_name}")


classes = (CSWEAPON_PT_library, CSWEAPON_PT_active, CSWEAPON_PT_stickers)
