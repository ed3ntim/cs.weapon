# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Public API for partner add-ons (cs.anim, cs.agents, cs.blend).

Import it through the add-on module so it works for legacy installs,
extensions and the cs.blend suite alike::

    import sys
    mod = next(m for k, m in sys.modules.items()
               if k == "cs_weapon" or k.endswith(".cs_weapon"))
    api = mod.api
    if api.API_VERSION >= 1:
        weapon = api.spawn(bpy.context, "weapon_ak47:0", rigged=True)
        arm = api.armature(weapon)
        api.set_clip(weapon, "reload")
        muzzle = api.attachment_matrix(weapon, "muzzle_flash")

Stable within an ``API_VERSION``. What it offers:

* Library: ``library_ready``, ``skins`` (ids and names, filter by weapon or
  type), ``skin_info``.
* Finding weapons: ``weapons``, ``weapon_of`` (the weapon object of any of
  its parts: rig, modules, sticker meshes), ``is_weapon``, ``is_glove``,
  ``gloves``.
* Spawning: ``spawn(context, skin_id, rigged=..., seed=..., wear=...,
  location=...)``; ``register_spawn_handler(fn)``: ``fn(weapon)`` runs after
  every spawn (operator, inspect link or API).
* Rig (weapons and gloves spawned with ``rigged=True``): ``armature``, ``bones``,
  ``has_rig``, ``clips`` ({clip name: action}), ``set_clip``, ``rest_pose``.
* Attachments: ``attachments`` (names), ``attachment_matrix`` (world
  matrix, follows the pose of a rigged weapon), e.g. ``muzzle_flash``,
  ``shell_eject``, ``stattrak``, ``nametag``, ``keychain_markup``.

Matrices are in Blender units (metres); attachment axes are the game's
(x forward out of the attachment, z up), converted to Blender space.
Functions return None or empty results for objects that are not weapons
instead of raising.
"""

import bpy

from . import utils

API_VERSION = 1

_spawn_handlers = []


def _catalog():
    from .library import registry
    return registry.get_catalog()


def _skin(obj):
    obj = weapon_of(obj)
    return _catalog().get_skin(obj.get(utils.PROP_SKIN, "")) if obj is not None else None


# ---------------------------------------------------------------------------
# Library
# ---------------------------------------------------------------------------

def library_ready():
    """True when a weapon library with at least one weapon is loaded."""
    return bool(_catalog().weapons)


def skins(weapon_id="", weapon_type=""):
    """[(skin id, display name)] of the library, optionally of one weapon
    (``weapon_ak47``) or type (``rifle``, ``knife``, ``glove`` ...)."""
    out = []
    for skin in _catalog().skins.values():
        if weapon_id and skin.weapon.id != weapon_id:
            continue
        if weapon_type and skin.weapon.type != weapon_type:
            continue
        out.append((skin.id, skin.name))
    return sorted(out, key=lambda s: s[1])


def skin_info(obj_or_id):
    """Dict about a skin (by weapon object or skin id), or None: id, name,
    weapon, weapon_name, def_index, paint_index, type, knife, glove, legacy,
    rig (library has a skeleton for it), icon (inventory image, absolute
    path or ""), model_path, rig_path (glb files of the library, "" if
    there is none)."""
    from .ops import spawn
    skin = _catalog().get_skin(obj_or_id) if isinstance(obj_or_id, str) else _skin(obj_or_id)
    if skin is None:
        return None
    weapon = skin.weapon
    return {
        "id": skin.id, "name": skin.name, "weapon": weapon.id, "weapon_name": weapon.name,
        "def_index": weapon.def_index, "paint_index": skin.paint_kit.id if not skin.is_vanilla else 0,
        "type": weapon.type, "knife": weapon.is_knife, "glove": weapon.is_glove,
        "legacy": spawn.uses_legacy(skin), "rig": weapon.has_rig(),
        "icon": skin.icon, "model_path": weapon.model_path(), "rig_path": weapon.rig_path(),
    }


# ---------------------------------------------------------------------------
# Finding weapons
# ---------------------------------------------------------------------------

def is_weapon(obj):
    return utils.is_weapon(obj)


def weapon_of(obj):
    """The weapon object ``obj`` belongs to (itself, a parent, or for a rig
    armature its weapon child), or None."""
    from .ops import spawn
    if obj is None:
        return None
    if obj.type == "ARMATURE" and spawn.PROP_RIG in obj:
        return next((c for c in obj.children if utils.is_weapon(c)), None)
    while obj is not None:
        if utils.is_weapon(obj):
            return obj
        obj = obj.parent
    return None


def weapons(scene=None, gloves=False):
    """Weapon objects in ``scene`` (default: current); gloves only with
    ``gloves=True``."""
    scene = scene or bpy.context.scene
    return [o for o in scene.objects if utils.is_weapon(o) and (gloves or not is_glove(o))]


def is_glove(obj):
    info = skin_info(obj) if utils.is_weapon(obj) else None
    return bool(info and info["glove"])


def gloves(scene=None):
    """Glove objects in ``scene``. Spawned with ``rigged=True`` (library
    with skeletons) they hang under an armature with the game's arm bones
    (``arm_upper_L``, ``hand_R``, ``finger_index_0_L`` ...) and carry the
    matching vertex groups, so they can be bound to another skeleton with
    the same bone names (an agent); otherwise they are rigid."""
    return [o for o in weapons(scene, gloves=True) if is_glove(o)]


# ---------------------------------------------------------------------------
# Spawning
# ---------------------------------------------------------------------------

def spawn(context, skin_id, rigged=False, seed=0, wear=0.01, location=None):
    """Spawn a skin (``weapon_ak47:44``, vanilla ``weapon_ak47:0``). Returns
    the weapon object, or None if the skin is not in the library.
    ``rigged`` needs a library with skeletons (``skin_info(...)["rig"]``)
    and is ignored otherwise."""
    from .ops import spawn as S
    skin = _catalog().get_skin(skin_id)
    if skin is None:
        return None
    kit = skin.paint_kit
    if not skin.is_vanilla:
        wear = min(max(wear, kit.wear_min), kit.wear_max)
    return S.spawn(context, skin, int(seed), float(wear), location, rigged=rigged)


def register_spawn_handler(fn):
    if fn not in _spawn_handlers:
        _spawn_handlers.append(fn)


def unregister_spawn_handler(fn):
    if fn in _spawn_handlers:
        _spawn_handlers.remove(fn)


def notify_spawned(weapon):
    for fn in list(_spawn_handlers):
        try:
            fn(weapon)
        except Exception as exc:  # a broken partner must not break spawning
            utils.log.warning("cs.weapon: spawn handler %r failed: %s", fn, exc)


# ---------------------------------------------------------------------------
# Rig
# ---------------------------------------------------------------------------

def armature(obj):
    """Armature object of a rigged weapon, or None."""
    from .ops import spawn
    arm = spawn.rig_of(weapon_of(obj))
    if arm is not None and arm.pose is None:  # new objects get their pose on evaluation
        bpy.context.view_layer.update()
    return arm


def has_rig(obj):
    return armature(obj) is not None


def bones(obj):
    """Bone names of a rigged weapon ([] otherwise), parents first."""
    arm = armature(obj)
    return [b.name for b in arm.data.bones] if arm is not None else []


def clips(obj):
    """{clip name: action} of a rigged weapon, e.g. reload, shoot,
    inventory_inspect. Loaded on demand from the library."""
    from .ops import spawn
    skin = _skin(obj)
    if skin is None or not skin.weapon.has_rig():
        return {}
    acts = spawn.rig_actions(skin.weapon)
    if not acts:
        spawn.armature_for(skin.weapon)
        acts = spawn.rig_actions(skin.weapon)
    return {a[spawn.PROP_CLIP]: a for a in acts}


def set_clip(obj, clip):
    """Play a clip (name or action; None clears it) on the weapon's
    armature. Returns the action or None."""
    arm = armature(obj)
    if arm is None:
        return None
    action = clip if isinstance(clip, bpy.types.Action) or clip is None else clips(obj).get(clip)
    if action is None and clip is not None:
        return None
    ad = arm.animation_data_create()
    ad.action = action
    if action is not None and hasattr(ad, "action_slot") and getattr(action, "slots", None):
        if ad.action_slot is None:
            ad.action_slot = action.slots[0]
    return action


def rest_pose(obj):
    """Clear the clip and all pose transforms of a rigged weapon."""
    arm = armature(obj)
    if arm is None:
        return
    if arm.animation_data is not None:
        arm.animation_data.action = None
    for pb in arm.pose.bones:
        pb.location = (0.0, 0.0, 0.0)
        pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        pb.rotation_euler = (0.0, 0.0, 0.0)
        pb.scale = (1.0, 1.0, 1.0)


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------

def attachments(obj):
    """Attachment names of the weapon's model."""
    skin = _skin(obj)
    return sorted(skin.weapon.attachments) if skin is not None else []


def attachment_matrix(obj, name):
    """World matrix of an attachment (``muzzle_flash``, ``shell_eject`` ...)
    for the weapon's current transform and pose, or None. On the legacy
    mesh the ``_legacy`` attachment is used when the model has one."""
    from .ops import modules, spawn
    weapon = weapon_of(obj)
    skin = _skin(weapon)
    if skin is None:
        return None
    local = modules.attachment_local(skin.weapon, name, spawn.uses_legacy(skin))
    if local is None:
        return None
    model = spawn.template_matrix(weapon.data).inverted_safe() @ local  # in the weapon object's space
    arm = armature(weapon)
    att = skin.weapon.attachments.get(name) or {}
    bone_name = att.get("bone", "") if isinstance(att, dict) else ""
    if arm is not None and arm.pose is not None and bone_name in arm.pose.bones:
        # the attachment follows its bone: rest -> posed, in armature space
        in_arm = weapon.matrix_parent_inverse @ weapon.matrix_basis @ model
        pb = arm.pose.bones[bone_name]
        return arm.matrix_world @ pb.matrix @ pb.bone.matrix_local.inverted_safe() @ in_arm
    return weapon.matrix_world @ model

