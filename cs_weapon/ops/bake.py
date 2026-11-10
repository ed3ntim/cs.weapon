# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Bake the live weapon material (paint, wear, pattern, stickers) into
textures on the first UV map, e.g. for export to other programs.

Cycles has no bake pass for metalness and its color passes depend on
lighting, so color, roughness and metalness are baked as emission: a
temporary copy of the material (and of every node group in it) has each
Principled BSDF replaced by an Emission of the wanted input. Mix Shaders
then mix the values exactly like the shaders, so stickers in the material
end up in the textures too. The normal map is Cycles' NORMAL pass of the
original material (tangent space).

The result is a plain Principled material ("<name> (Baked)") with the
images; it can replace the live material on the object (the live one is
kept with a fake user and comes back with "Restore Live Material")."""

import os

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from .. import utils
from ..nodes import gap

PROP_LIVE = "cs_weapon_live_material"  # on objects with a baked material: live material name
PROP_BAKED = "cs_weapon_baked"         # on baked materials
NODE_TARGET = "CS_BAKE_TARGET"

# channel: (Principled input, colorspace, file suffix)
CHANNELS = {
    "COLOR": ("Base Color", "sRGB", "color"),
    "ROUGHNESS": ("Roughness", "Non-Color", "roughness"),
    "METALLIC": ("Metallic", "Non-Color", "metallic"),
    "NORMAL": (None, "Non-Color", "normal"),
}


# ---------------------------------------------------------------------------
# Emission copies
# ---------------------------------------------------------------------------

def _emit_principled(tree, input_name):
    """Replace every Principled BSDF of ``tree`` by an Emission of one input."""
    for p in [n for n in tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled"]:
        e = tree.nodes.new("ShaderNodeEmission")
        e.location = p.location
        e.inputs["Strength"].default_value = 1.0
        src = p.inputs.get(input_name)
        if src is not None and src.is_linked:
            tree.links.new(src.links[0].from_socket, e.inputs["Color"])
        elif src is not None:
            v = src.default_value
            e.inputs["Color"].default_value = (tuple(v)[:3] + (1.0,)) if hasattr(v, "__len__") else (v, v, v, 1.0)
        for link in list(p.outputs[0].links):
            tree.links.new(e.outputs[0], link.to_socket)
        tree.nodes.remove(p)


def _emission_copy(mat, input_name):
    """Copy of ``mat`` emitting one Principled input; returns (material,
    copied node groups) for cleanup."""
    copy = mat.copy()
    groups = {}

    def convert(tree):
        _emit_principled(tree, input_name)
        for node in tree.nodes:
            if node.type == "GROUP" and node.node_tree is not None:
                src = node.node_tree
                if src.name not in groups:
                    groups[src.name] = src.copy()
                    convert(groups[src.name])
                node.node_tree = groups[src.name]

    convert(copy.node_tree)
    return copy, list(groups.values())


# ---------------------------------------------------------------------------
# Bake
# ---------------------------------------------------------------------------

def _target(mat, image):
    nodes = mat.node_tree.nodes
    node = nodes.get(NODE_TARGET) or nodes.new("ShaderNodeTexImage")
    node.name = NODE_TARGET
    node.image = image
    nodes.active = node
    node.select = True
    return node


def _weapon_slot(obj):
    for slot in obj.material_slots:
        if slot.material is not None and utils.PROP_SPEC in slot.material:
            return slot
    return None


def bake(context, obj, size=2048, channels=("COLOR", "ROUGHNESS", "METALLIC", "NORMAL"), margin=8):
    """Bake the weapon's live material. Returns {channel: image}."""
    slot = _weapon_slot(obj)
    if slot is None:
        raise RuntimeError("The weapon has no live cs.weapon material")
    live = slot.material
    scene = context.scene
    mesh = obj.data
    saved = {
        "engine": scene.render.engine,
        "samples": scene.cycles.samples,
        "uv": mesh.uv_layers.active_index,
        "selected": [o for o in context.selected_objects],
        "active": context.view_layer.objects.active,
    }
    images = {}
    try:
        scene.render.engine = "CYCLES"
        scene.cycles.samples = 1
        bake_settings = scene.render.bake
        bake_settings.margin = margin
        bake_settings.use_selected_to_active = False
        bake_settings.target = "IMAGE_TEXTURES"
        if mesh.uv_layers:
            mesh.uv_layers.active_index = 0
        for o in context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        for channel in channels:
            input_name, colorspace, suffix = CHANNELS[channel]
            name = f"{obj.name} {suffix}"
            old = bpy.data.images.get(name)
            if old is not None and old.users == 0:
                bpy.data.images.remove(old)
            img = bpy.data.images.new(name, size, size, alpha=False, float_buffer=False)
            img.colorspace_settings.name = colorspace
            if channel == "NORMAL":
                _target(live, img)
                try:
                    bpy.ops.object.bake(type="NORMAL", normal_space="TANGENT")
                finally:
                    live.node_tree.nodes.remove(live.node_tree.nodes[NODE_TARGET])
            else:
                temp, groups = _emission_copy(live, input_name)
                _target(temp, img)
                slots = [s for s in obj.material_slots if s.material == live]  # with sticker gap slots
                for s in slots:
                    s.material = temp
                try:
                    bpy.ops.object.bake(type="EMIT")
                finally:
                    for s in slots:
                        s.material = live
                    bpy.data.materials.remove(temp)
                    for g in groups:
                        bpy.data.node_groups.remove(g)
            images[channel] = img
    finally:
        scene.render.engine = saved["engine"]
        scene.cycles.samples = saved["samples"]
        if mesh.uv_layers:
            mesh.uv_layers.active_index = saved["uv"]
        obj.select_set(False)
        for o in saved["selected"]:
            if o.name in bpy.data.objects:
                o.select_set(True)
        context.view_layer.objects.active = saved["active"]
    return images


def save_images(images, folder):
    """Write the baked images as PNG into ``folder`` (and point them there)."""
    os.makedirs(folder, exist_ok=True)
    for img in images.values():
        path = os.path.join(folder, bpy.path.clean_name(img.name) + ".png")
        img.filepath_raw = path
        img.file_format = "PNG"
        img.save()
    return folder


def baked_material(name, images, uv_map):
    """Principled material from baked images."""
    mat = bpy.data.materials.new(f"{name} (Baked)")
    mat[PROP_BAKED] = True
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = next(n for n in nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    uv = nodes.new("ShaderNodeUVMap")
    uv.uv_map = uv_map
    uv.location = (-1000, 0)
    y = 300
    for channel, socket in (("COLOR", "Base Color"), ("ROUGHNESS", "Roughness"), ("METALLIC", "Metallic"),
                            ("NORMAL", None)):
        img = images.get(channel)
        if img is None:
            continue
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = img
        tex.location = (-700, y)
        y -= 300
        links.new(uv.outputs[0], tex.inputs["Vector"])
        if socket:
            links.new(tex.outputs["Color"], bsdf.inputs[socket])
        else:
            nm = nodes.new("ShaderNodeNormalMap")
            nm.uv_map = uv_map
            nm.location = (-300, y)
            links.new(tex.outputs["Color"], nm.inputs["Color"])
            links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
    return mat


def use_baked(obj, mat):
    slot = _weapon_slot(obj)
    if slot is None:
        return
    live = slot.material
    live.use_fake_user = True
    obj[PROP_LIVE] = live.name
    for s in obj.material_slots:
        if s.material == live:  # also the sticker gap slots
            s.link = "OBJECT"
            s.material = mat
    gap.ensure_nodes(mat)


def restore_live(obj):
    live = bpy.data.materials.get(obj.get(PROP_LIVE, ""))
    if live is None:
        return False
    baked = None
    for slot in obj.material_slots:
        if slot.material is not None and slot.material.get(PROP_BAKED):
            baked = slot.material
            slot.material = live
    if baked is not None and baked.users == 0:
        bpy.data.materials.remove(baked)
    live.use_fake_user = False
    del obj[PROP_LIVE]
    return True


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

class CSWEAPON_OT_bake(bpy.types.Operator):
    """Bake the active weapon's skin (paint, wear, stickers in the material) into textures"""
    bl_idname = "cs_weapon.bake"
    bl_label = "Bake Skin"
    bl_options = {"REGISTER", "UNDO"}

    size: EnumProperty(name="Size", default="2048", items=[
        ("1024", "1024", ""), ("2048", "2048", ""), ("4096", "4096", "Like the game")])
    normal: BoolProperty(name="Normal Map", default=True)
    apply: BoolProperty(name="Use Baked Material", default=True,
                        description="Put the baked material on the weapon (the live one can be restored)")
    folder: StringProperty(name="Folder", subtype="DIR_PATH", default="//textures",
                           description="Where the PNGs are written (relative to the .blend file); "
                                       "empty or unsaved file: packed into the .blend")

    @classmethod
    def poll(cls, context):
        obj = utils.active_weapon(context)
        return context.mode == "OBJECT" and obj is not None and _weapon_slot(obj) is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        obj = utils.active_weapon(context)
        channels = ("COLOR", "ROUGHNESS", "METALLIC") + (("NORMAL",) if self.normal else ())
        try:
            images = bake(context, obj, int(self.size), channels)
        except RuntimeError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        where = "packed"
        if self.folder and bpy.data.filepath:
            where = save_images(images, bpy.path.abspath(self.folder))
        else:
            for img in images.values():
                img.pack()
        mat = baked_material(obj.name, images, obj.data.uv_layers[0].name if obj.data.uv_layers else "UVMap")
        if self.apply:
            use_baked(obj, mat)
        self.report({"INFO"}, f"Baked {len(images)} textures ({self.size} px, {where})")
        return {"FINISHED"}


class CSWEAPON_OT_restore_live(bpy.types.Operator):
    """Put the live (editable) material back on the weapon instead of the baked one"""
    bl_idname = "cs_weapon.restore_live"
    bl_label = "Restore Live Material"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and PROP_LIVE in obj

    def execute(self, context):
        if not restore_live(context.active_object):
            self.report({"ERROR"}, "The live material is gone")
            return {"CANCELLED"}
        return {"FINISHED"}


classes = (CSWEAPON_OT_bake, CSWEAPON_OT_restore_live)
