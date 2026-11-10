# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Duplicated weapons get their own material.

Shift+D (and Alt+D) copies a weapon object together with its object-linked
material slot, so both copies point at the same material and a new pattern
or float on one copy would change the other. After objects were added a
short timer looks for weapon materials used by more than one weapon and
gives the copies their own. The weapon whose name sorts first keeps the
original material. Template meshes stay shared on purpose.
"""

import bpy
from bpy.app.handlers import persistent

from .. import utils
from ..nodes import group, material
from . import modules, sticker_ops

_state = {"objects": -1}


def _weapons():
    return sorted((o for o in bpy.data.objects if utils.is_weapon(o)), key=lambda o: o.name)


def make_unique():
    """Give every weapon its own weapon materials. Returns the changed objects."""
    seen, changed = set(), []
    for obj in _weapons():
        touched = False
        for slot in obj.material_slots:
            mat = slot.material
            if mat is None or utils.PROP_SPEC not in mat:
                continue
            if mat in seen:
                mat = group.copy_material(mat)
                if slot.link == "OBJECT":
                    slot.material = mat
                else:  # material on the (shared) mesh: move it to the object
                    slot.link = "OBJECT"
                    slot.material = mat
                touched = True
            seen.add(mat)
        if touched:
            material.update_object(obj)
            sticker_ops.sync(obj)  # sticker meshes of the copy are its own
            changed.append(obj)
        modules.sync(obj)  # copies lack their modules, shared displays split
    return changed


def _deferred():
    try:
        make_unique()
    except Exception as exc:  # never let a timer raise
        utils.log.error("cs.weapon: duplicate check failed: %s", exc)
    return None


@persistent
def _on_depsgraph(scene, depsgraph):
    count = len(bpy.data.objects)
    if count == _state["objects"]:
        return
    grew = count > _state["objects"] >= 0
    _state["objects"] = count
    # Data must not change inside the depsgraph handler: defer.
    if grew and not bpy.app.timers.is_registered(_deferred):
        bpy.app.timers.register(_deferred, first_interval=0.0)


@persistent
def _on_load(_dummy):
    _state["objects"] = len(bpy.data.objects)


def register():
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)
    bpy.app.handlers.load_post.append(_on_load)


def unregister():
    if _on_depsgraph in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph)
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    if bpy.app.timers.is_registered(_deferred):
        bpy.app.timers.unregister(_deferred)
