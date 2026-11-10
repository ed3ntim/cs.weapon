# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Operators and the logic behind them.

    spawn.py        glTF import, template meshes, weapon objects
    weapon_ops.py   spawn / edit operators
    library_ops.py  refresh, open folder
    duplicates.py   duplicated weapons get their own material
    sticker_ops.py  stickers in the weapon's sticker slots
    sticker_mesh.py stickers as own meshes on the slots (option)
    charm_link.py   charm at the keychain attachment (needs cs.charm)
    bake.py         bake the live material into textures
"""

import bpy

from . import bake, charm_link, duplicates, library_ops, sticker_ops, weapon_ops

_classes = (*library_ops.classes, *weapon_ops.classes, *sticker_ops.classes, *charm_link.classes,
            *bake.classes)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    duplicates.register()


def unregister():
    duplicates.unregister()
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
