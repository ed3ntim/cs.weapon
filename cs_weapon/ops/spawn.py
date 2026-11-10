# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Spawning: glTF import into template meshes, weapon objects, materials.

Each model/mesh pair is imported once and kept as a template mesh
(utils.PROP_MODEL). Spawned weapons share the mesh data and get their own
material (object-linked slot), so seed and wear stay independent.

Gloves are one mesh with a material per hand (and the bare arm, which
keeps the model's material): both hand slots get their own material.

Rigged weapons and gloves (``rigged=True``, library with <model>.rig.glb): the same
meshes with vertex groups come from the rig glb, the weapon object is
parented to its own armature object (shared armature data per model) and
deformed by an Armature modifier. The model's clips (reload, shoot, ...)
stay as actions "<weapon> | <clip>". The clips also pose the bones
``weapon``/``weapon_offset`` (per clip a fixed offset from the rest pose,
e.g. the inventory pose), so rigid parts (StatTrak, name tag, sticker
meshes, charm) stay children of the weapon object but follow
``weapon_offset`` through an Armature constraint (follow_rig).
"""

import os

import bpy
from mathutils import Matrix

from .. import utils
from ..nodes import gap, material
from . import modules

PROP_MATRIX = "cs_weapon_matrix"  # on template meshes/armatures: world matrix from the import
PROP_RIG = "cs_weapon_rig"        # on rig armature objects and actions: template key of the rig
PROP_CLIP = "cs_weapon_clip"      # on rig actions: clip name in the model (reload, shoot ...)
RIG_CONSTRAINT = "cs.weapon Rig"  # Armature constraint of rigid parts on a rigged weapon
RIG_BONE = "weapon_offset"        # bone of all attachments


def _template_key(model_path, mesh_name):
    return f"{os.path.normcase(os.path.abspath(model_path))}|{mesh_name}"


def _find_template(key):
    for me in bpy.data.meshes:
        if me.get(utils.PROP_MODEL) == key:
            return me
    return None


def _flat(matrix):
    return [v for row in matrix for v in row]


def _import_templates(model_path):
    """Import a glb, keep every mesh as template, remove the import objects.
    An armature (rig glb) is kept as template too, its actions get a fake
    user. Returns {object name: mesh}."""
    before = set(bpy.data.objects)
    actions_before = set(bpy.data.actions)
    bpy.ops.import_scene.gltf(filepath=model_path)
    new = [o for o in bpy.data.objects if o not in before]
    found = {}
    rig_key_ = _template_key(model_path, "")
    for obj in new:
        if obj.type == "MESH":
            me = obj.data
            me[PROP_MATRIX] = _flat(obj.matrix_world)
            found[obj.name] = me
        elif obj.type == "ARMATURE":
            arm = obj.data
            arm[PROP_MATRIX] = _flat(obj.matrix_world)
            arm[utils.PROP_MODEL] = rig_key_
    for act in [a for a in bpy.data.actions if a not in actions_before]:
        act[PROP_RIG] = rig_key_
        act[PROP_CLIP] = act.name
        act.use_fake_user = True
    for obj in new:
        bpy.data.objects.remove(obj, do_unlink=True)
    return found


def _model_path(weapon, rigged):
    return weapon.rig_path() if rigged else weapon.model_path()


def _mesh_for(weapon, legacy, rigged=False):
    """Template mesh of a weapon (HD or legacy), importing on first use."""
    path = _model_path(weapon, rigged)
    suffix = weapon.glove.mesh if weapon.is_glove else (weapon.variant(legacy).mesh or "")
    key = _template_key(path, suffix)
    me = _find_template(key)
    if me is not None:
        if not weapon.is_glove and gap.ATTRIBUTE not in me.attributes:
            gap.ensure_attribute(me)  # template from an older version
        return me
    meshes = _import_templates(path)
    if rigged:
        for act in rig_actions(weapon):
            act.name = f"{weapon.name} | {act[PROP_CLIP]}"
    chosen = None
    for name, mesh in meshes.items():
        mkey = _template_key(path, _suffix_of(name, weapon))
        mesh[utils.PROP_MODEL] = mkey
        if mkey == key:
            chosen = mesh
    if chosen is None and meshes and not suffix:
        chosen = next(iter(meshes.values()))
        chosen[utils.PROP_MODEL] = key
    if chosen is not None and weapon.is_glove:
        _strip_arm(chosen, set(weapon.glove.materials))
    elif chosen is not None:
        gap.ensure_attribute(chosen)
    return chosen


def _strip_arm(mesh, keep):
    """Remove the faces of the bare arm (materials other than the hands)."""
    import bmesh
    slots = {i for i, m in enumerate(mesh.materials) if m is None or _base_name(m.name) not in keep}
    if not slots:
        return
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.material_index in slots], context="FACES")
    loose = [v for v in bm.verts if not v.link_faces]
    bmesh.ops.delete(bm, geom=loose, context="VERTS")
    bm.to_mesh(mesh)
    bm.free()
    for i in sorted(slots, reverse=True):
        mesh.materials.pop(index=i)


def _suffix_of(object_name, weapon):
    for s in (weapon.hd.mesh, weapon.legacy.mesh if weapon.legacy else "",
              weapon.glove.mesh if weapon.is_glove else ""):
        if s and object_name.endswith(s):
            return s
    return object_name


def rig_key(weapon):
    return _template_key(weapon.rig_path(), "") if weapon.rig else ""


def rig_actions(weapon):
    """Actions (clips) of a weapon's rig, imported ones only."""
    key = rig_key(weapon)
    return [a for a in bpy.data.actions if key and a.get(PROP_RIG) == key]


def armature_for(weapon):
    """Template armature data of a weapon's rig, importing on first use."""
    key = rig_key(weapon)
    if not key:
        return None
    for arm in bpy.data.armatures:
        if arm.get(utils.PROP_MODEL) == key:
            return arm
    _mesh_for(weapon, False, rigged=True)
    return next((a for a in bpy.data.armatures if a.get(utils.PROP_MODEL) == key), None)


def rig_of(obj):
    """The rig armature object of a rigged weapon object, or None."""
    parent = obj.parent if obj is not None else None
    if parent is not None and parent.type == "ARMATURE" and PROP_RIG in parent:
        return parent
    return None


def follow_rig(weapon_obj, child):
    """Let a rigid child of the weapon object (module, sticker mesh, charm)
    follow the rig's weapon_offset bone, or drop that for a plain weapon."""
    arm = rig_of(weapon_obj)
    con = child.constraints.get(RIG_CONSTRAINT)
    if arm is None:
        if con is not None:
            child.constraints.remove(con)
        return
    bones = arm.data.bones
    bone = RIG_BONE if RIG_BONE in bones else (bones[0].name if len(bones) else "")
    if con is None:
        con = child.constraints.new("ARMATURE")
        con.name = RIG_CONSTRAINT
    if not con.targets:
        con.targets.new()
    target = con.targets[0]
    target.target = arm
    target.subtarget = bone
    target.weight = 1.0


def follow_rig_children(weapon_obj):
    for child in weapon_obj.children:
        follow_rig(weapon_obj, child)


def _rig(context, weapon, name, mesh, obj):
    """New armature object for a weapon and the weapon object under it."""
    data = armature_for(weapon)
    if data is None:
        raise RuntimeError(f"no skeleton in {weapon.rig_path()}")
    arm = bpy.data.objects.new(f"{name} Rig", data)
    arm[PROP_RIG] = rig_key(weapon)
    arm.matrix_world = template_matrix(data)
    utils.weapon_collection(context).objects.link(arm)
    _attach(obj, arm, mesh)
    follow_rig_children(obj)
    return arm


def _attach(obj, arm, mesh):
    obj.parent = arm
    obj.matrix_parent_inverse = Matrix.Identity(4)
    obj.matrix_basis = template_matrix(arm.data).inverted_safe() @ template_matrix(mesh)
    mod = next((m for m in obj.modifiers if m.type == "ARMATURE"), None)
    if mod is None:
        mod = obj.modifiers.new("Armature", "ARMATURE")
    mod.object = arm


def _unrig(obj):
    """Back to a plain weapon object (the new skin's weapon has no rig)."""
    arm = rig_of(obj)
    world = obj.matrix_world.copy()
    for mod in [m for m in obj.modifiers if m.type == "ARMATURE"]:
        obj.modifiers.remove(mod)
    obj.parent = None
    obj.matrix_world = world
    follow_rig_children(obj)
    if arm is not None and not arm.children:
        bpy.data.objects.remove(arm, do_unlink=True)


def uses_legacy(skin):
    """CS2 paints legacy paint kits on the legacy mesh, if the weapon has one."""
    return bool(skin.paint_kit.legacy and skin.weapon.has_legacy())


def template_matrix(mesh):
    flat = mesh.get(PROP_MATRIX) if mesh is not None else None
    if flat is not None and len(flat) == 16:
        return Matrix([flat[i:i + 4] for i in range(0, 16, 4)])
    return Matrix.Identity(4)


def _skin_mesh(skin, rigged=False):
    legacy = uses_legacy(skin)
    mesh = _mesh_for(skin.weapon, legacy, rigged)
    if mesh is None:
        raise RuntimeError(f"no mesh for {skin.weapon.id} ({'legacy' if legacy else 'hd'}) "
                           f"in {skin.weapon.model_path()}")
    return mesh, legacy


def _set_slot(slot, mat):
    slot.link = "OBJECT"
    old = slot.material
    slot.material = mat
    if old is not None and old is not mat and old.users == 0 and (
            utils.PROP_SPEC in old or old.get("cs_weapon_baked")):
        bpy.data.materials.remove(old)


def _base_name(name):
    """Material name without Blender's .001 suffix."""
    head, _, tail = name.rpartition(".")
    return head if head and tail.isdigit() else name


def _assign_glove(obj, mats):
    """Hand materials into the slots of the template's hand materials."""
    for i, slot in enumerate(obj.material_slots):
        data_mat = obj.data.materials[i] if i < len(obj.data.materials) else None
        mat = mats.get(_base_name(data_mat.name)) if data_mat is not None else None
        if mat is not None:
            _set_slot(slot, mat)
        elif slot.link == "OBJECT":
            _set_slot(slot, None)  # back to the model's own material


def _assign_material(obj, mat):
    """Put the weapon material into the first slot, linked to the object
    (spawned weapons share the template mesh). ``mat`` may be a dict of
    glove materials by model material name."""
    if isinstance(mat, dict):
        _assign_glove(obj, mat)
    else:
        if not obj.material_slots:
            obj.data.materials.append(None)
        _set_slot(obj.material_slots[0], mat)
        gaps = gap.gap_slots(obj.data)
        for i, slot in enumerate(obj.material_slots[1:], 1):
            if i in gaps:  # filler faces: the weapon material shows only the stickers there
                _set_slot(slot, mat)
            elif slot.link == "OBJECT" and slot.material is not None and utils.PROP_SPEC in slot.material:
                _set_slot(slot, None)  # left over from gloves
    # A new skin replaces a baked material: the kept live one is not needed.
    live = bpy.data.materials.get(obj.pop("cs_weapon_live_material", "") or "")
    if live is not None and live is not mat:
        live.use_fake_user = False
        if live.users == 0:
            bpy.data.materials.remove(live)
    # A cached copy may carry another weapon's stickers: rebuild from the object.
    from . import sticker_ops
    sticker_ops.sync(obj)


def _materials(skin, legacy):
    if skin.weapon.is_glove:
        return material.get_glove(skin)
    return material.get(skin, legacy)


def spawn(context, skin, seed_value=0, wear_value=0.01, location=None, rigged=False):
    """Create a weapon object for a skin. Returns the object (with
    ``rigged`` parented to a new armature object, see rig_of)."""
    rigged = rigged and skin.weapon.has_rig()
    mesh, legacy = _skin_mesh(skin, rigged)
    obj = bpy.data.objects.new(skin.name, mesh)
    utils.weapon_collection(context).objects.link(obj)
    if rigged:
        arm = _rig(context, skin.weapon, skin.name, mesh, obj)
        if location is not None:
            arm.location = location
    else:
        obj.matrix_world = template_matrix(mesh)
        if location is not None:
            obj.location = location
    obj[utils.PROP_SKIN] = skin.id
    _assign_material(obj, _materials(skin, legacy))
    obj.cs_weapon["seed"] = seed_value  # set raw, apply once below
    obj.cs_weapon["wear"] = wear_value
    material.update_object(obj)
    for o in context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    from .. import api
    api.notify_spawned(obj)
    return obj


def reskin(obj, skin):
    """Give an existing weapon object another skin. Swaps the mesh when the
    weapon or the mesh variant (HD/legacy) changes and keeps the object's
    own transform relative to the model. Pattern stays, float is clamped
    to the new paint kit's range."""
    old_name = _display_name(obj.get(utils.PROP_SKIN, ""))
    arm = rig_of(obj)
    if arm is not None and not skin.weapon.has_rig():
        _unrig(obj)
        arm = None
    mesh, legacy = _skin_mesh(skin, rigged=arm is not None)
    if arm is not None and arm.get(PROP_RIG) != rig_key(skin.weapon):
        # another weapon: its skeleton; the old clip does not fit
        if arm.animation_data is not None:
            arm.animation_data.action = None
        arm.data = armature_for(skin.weapon)
        arm[PROP_RIG] = rig_key(skin.weapon)
        if old_name and arm.name.startswith(old_name):
            arm.name = skin.name + arm.name[len(old_name):]
        obj.data = mesh
        _attach(obj, arm, mesh)
        follow_rig_children(obj)
        swapped = True
    elif obj.data is not mesh:
        rel = obj.matrix_basis @ template_matrix(obj.data).inverted_safe()
        obj.data = mesh
        obj.matrix_basis = rel @ template_matrix(mesh)
        swapped = True
    else:
        swapped = False
    obj[utils.PROP_SKIN] = skin.id
    _assign_material(obj, _materials(skin, legacy))
    kit = skin.paint_kit
    if not skin.is_vanilla:
        obj.cs_weapon["wear"] = min(max(obj.cs_weapon.wear, kit.wear_min), kit.wear_max)
    if old_name and obj.name.startswith(old_name):
        obj.name = skin.name + obj.name[len(old_name):]
    material.update_object(obj)
    modules.sync(obj, replace=swapped)
    return obj


def _display_name(skin_id):
    if not skin_id:
        return ""
    from ..library import registry
    skin = registry.get_catalog().get_skin(skin_id)
    return skin.name if skin is not None else skin_id
