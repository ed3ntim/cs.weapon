# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""UILists for the skin library and the sticker picker."""

import bpy

from ..library import catalog as C
from ..library import registry, sticker_registry
from ..library import stickers as ST


class CSWEAPON_UL_library(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        skin = registry.get_catalog().get_skin(item.skin_id)
        icon_id = registry.preview_icon(skin) if skin else 0
        row = layout.row(align=True)
        if icon_id:
            row.label(text=item.name, icon_value=icon_id)
        else:
            row.label(text=item.name, icon="ERROR" if item.has_missing else "MOD_ARRAY")
        sub = row.row(align=True)
        sub.alignment = "RIGHT"
        sub.label(text=C.STYLES.get(item.style, ""))
        if item.has_missing:
            sub.label(text="", icon="ERROR")

    def filter_items(self, context, data, propname):
        # Filtering happens in registry.sync_items; keep the given order.
        return [], []


class CSWEAPON_UL_stickers(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        sticker = sticker_registry.get_sticker(item.sticker_id)
        icon_id = sticker_registry.preview_icon(sticker)
        row = layout.row(align=True)
        if icon_id:
            row.label(text=item.name, icon_value=icon_id)
        else:
            row.label(text=item.name, icon="IMAGE_DATA")
        sub = row.row(align=True)
        sub.alignment = "RIGHT"
        sub.label(text=ST.FINISH_LABELS.get(item.finish, item.finish.title()))

    def filter_items(self, context, data, propname):
        return [], []


classes = (CSWEAPON_UL_library, CSWEAPON_UL_stickers)
