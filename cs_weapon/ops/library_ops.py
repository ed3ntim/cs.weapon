# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Library operators: refresh, open the user folder."""

import bpy

from .. import utils


class CSWEAPON_OT_refresh_library(bpy.types.Operator):
    bl_idname = "cs_weapon.refresh_library"
    bl_label = "Refresh Library"
    bl_description = "Reload the weapon libraries (core, bundled and user folder)"

    def execute(self, context):
        from ..library import registry
        cat = registry.refresh(context)
        self.report({"INFO"}, f"{len(cat.skins)} skins, {len(cat.weapons)} weapons, "
                              f"{len(cat.paint_kits)} paint kits")
        return {"FINISHED"}


class CSWEAPON_OT_open_user_folder(bpy.types.Operator):
    bl_idname = "cs_weapon.open_user_folder"
    bl_label = "Open Library Folder"
    bl_description = "Open the user weapon library folder in the file browser"

    @classmethod
    def poll(cls, context):
        return bool(utils.user_library_dir())

    def execute(self, context):
        bpy.ops.wm.path_open(filepath=utils.user_library_dir())
        return {"FINISHED"}


classes = (CSWEAPON_OT_refresh_library, CSWEAPON_OT_open_user_folder)
