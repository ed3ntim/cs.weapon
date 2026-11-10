# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Stickers inside the weapon material, the way the game draws them.

CS2 has no sticker meshes on weapons: csgo_weapon.vfx with S_STICKERS
samples up to five sticker textures (g_tSticker0..4) through the second UV
map (TEXCOORD_1) and lays them over the paint. Per slot (decompiled shader,
combo 512)::

    p  = (uv1 - 0.5 - g_vSticker<k>Offset) * |g_vSticker<k>Scale.x|
    uv = rotate(p, g_flSticker<k>Rotation * 2 pi) + 0.5     (inside 0..1)

uv1 with v from the top (Source), so the slot center is uv1 = 0.5 +
offset and a sticker spans 1 / scale of the UV1 range. The offsets and
scales of the preset slots come from the weapon material
(catalog Variant.sticker_slots); rotation (in turns) and wear come from the
item.

Here every used slot adds, between the weapon shader and the output::

    weapon shader ─┬─ STK<k>_MIX ── ... ── Material Output
    finish group ──┘   factor: STK<k>_WEAR (sticker alpha and wear)

The finish groups are cs.sticker's (sticker_groups.py), as in cs.charm's
Sticker Slab. Position, rotation, scale and wear are Value nodes
(STK<k>_OX, _OY, _SCALE, _ROT, Wear input), so changing them does not
rebuild the material.
"""

import json
import os
import zlib

import bpy

from ..library import stickers as ST
from . import gap
from . import sticker_groups as SG

STICKER_UV_DEFAULT = "UVMap.001"  # TEXCOORD_1 of the glTF import
NODE_PREFIX = "STK"
UV_GROUP = "CS2 Weapon Sticker UV"
UV_GROUP_VERSION = 1
UV_VERSION_KEY = "cs_weapon_sticker_uv_version"


def node_name(slot, part):
    return f"{NODE_PREFIX}{slot}_{part}"


def wear_seed(sticker_id, slot):
    """Stable wear seed per sticker and slot (two copies wear differently)."""
    return (zlib.crc32(f"{sticker_id}:{slot}".encode("utf-8")) % 1000) / 10.0


# ---------------------------------------------------------------------------
# UV group
# ---------------------------------------------------------------------------

def _uv_group():
    """UV1 (Blender, v up) + slot values -> sticker image vector (v up)."""
    tree = bpy.data.node_groups.get(UV_GROUP)
    if tree is not None and tree.get(UV_VERSION_KEY) == UV_GROUP_VERSION:
        return tree
    if tree is not None:
        tree.name = f"{UV_GROUP} (v{tree.get(UV_VERSION_KEY, 0)})"
    tree = bpy.data.node_groups.new(UV_GROUP, "ShaderNodeTree")
    tree[UV_VERSION_KEY] = UV_GROUP_VERSION
    iface = tree.interface
    for name in ("UV",):
        iface.new_socket(name, in_out="INPUT", socket_type="NodeSocketVector")
    for name in ("Offset X", "Offset Y", "Scale", "Rotation"):
        iface.new_socket(name, in_out="INPUT", socket_type="NodeSocketFloat")
    iface.new_socket("Vector", in_out="OUTPUT", socket_type="NodeSocketVector")
    nodes, links = tree.nodes, tree.links
    gin = nodes.new("NodeGroupInput")
    gout = nodes.new("NodeGroupOutput")
    gin.location, gout.location = (-1200, 0), (900, 0)

    def math(op, a, b, x, y):
        n = nodes.new("ShaderNodeMath")
        n.operation = op
        n.location = (x, y)
        for i, v in enumerate((a, b)):
            if isinstance(v, (int, float)):
                n.inputs[i].default_value = float(v)
            else:
                links.new(v, n.inputs[i])
        return n.outputs[0]

    sep = nodes.new("ShaderNodeSeparateXYZ")
    sep.location = (-1000, 0)
    links.new(gin.outputs["UV"], sep.inputs[0])
    # Source v from the top
    sv = math("SUBTRACT", 1.0, sep.outputs["Y"], -800, -100)
    px = math("MULTIPLY", math("SUBTRACT", math("SUBTRACT", sep.outputs["X"], 0.5, -800, 100),
                               gin.outputs["Offset X"], -600, 100), gin.outputs["Scale"], -400, 100)
    py = math("MULTIPLY", math("SUBTRACT", math("SUBTRACT", sv, 0.5, -600, -100),
                               gin.outputs["Offset Y"], -400, -100), gin.outputs["Scale"], -200, -100)
    ang = math("MULTIPLY", gin.outputs["Rotation"], 6.283185307179586, -600, -300)
    c = math("COSINE", ang, 0.0, -400, -300)
    s = math("SINE", ang, 0.0, -400, -450)
    qx = math("ADD", math("SUBTRACT", math("MULTIPLY", px, c, 0, 150), math("MULTIPLY", py, s, 0, 0), 200, 100),
              0.5, 400, 100)
    qy = math("ADD", math("ADD", math("MULTIPLY", px, s, 0, -150), math("MULTIPLY", py, c, 0, -300), 200, -200),
              0.5, 400, -200)
    vy = math("SUBTRACT", 1.0, qy, 600, -200)  # back to v up for the image
    comb = nodes.new("ShaderNodeCombineXYZ")
    comb.location = (750, 0)
    links.new(qx, comb.inputs[0])
    links.new(vy, comb.inputs[1])
    links.new(comb.outputs[0], gout.inputs["Vector"])
    return tree


# ---------------------------------------------------------------------------
# Layer
# ---------------------------------------------------------------------------

def _image(path, data=False):
    if not path or not os.path.isfile(path):
        return None
    try:
        img = bpy.data.images.load(path, check_existing=True)
    except RuntimeError:
        return None
    if data:
        try:
            img.colorspace_settings.name = "Non-Color"
        except TypeError:
            img.colorspace_settings.is_data = True
        img.alpha_mode = "CHANNEL_PACKED"
    return img


def clear(mat):
    """Remove all sticker nodes and reconnect the weapon shader."""
    tree = mat.node_tree if mat is not None else None
    if tree is None:
        return
    out = _output(tree)
    first = None
    for slot in range(8):
        mix = tree.nodes.get(node_name(slot, "MIX"))
        if mix is not None and mix.inputs[1].is_linked:
            src = mix.inputs[1].links[0].from_socket
            if not src.node.name.startswith(NODE_PREFIX):
                first = src
    for node in [n for n in tree.nodes if n.name.startswith(NODE_PREFIX)]:
        tree.nodes.remove(node)
    if PROP_LAYOUT in mat:
        del mat[PROP_LAYOUT]
    if first is not None and out is not None:
        tree.links.new(first, out.inputs["Surface"])


def _output(tree):
    outs = [n for n in tree.nodes if n.type == "OUTPUT_MATERIAL"]
    for n in outs:
        if n.is_active_output:
            return n
    return outs[0] if outs else None


def has_layer(mat):
    tree = mat.node_tree if mat is not None else None
    return tree is not None and any(n.name.startswith(NODE_PREFIX) for n in tree.nodes)


# Structure of the built layer. EEVEE compiles the whole weapon shader again
# whenever the node structure changes (seconds, the weapon shows white
# meanwhile), but not when only images or values change. A layer with the
# same structure is therefore updated in place.
PROP_LAYOUT = "cs_sticker_layout"


def _finish(sticker):
    return sticker.finish if sticker.finish in SG.FINISH_GROUP_KEYS else ST.DEFAULT_FINISH


def _present(sticker):
    return {r: p for r, p in sticker.textures.items() if p and os.path.isfile(p)}


def layout(entries, uv_map):
    """Everything the node structure of a layer depends on."""
    return json.dumps([uv_map] + [
        [int(e["slot"]), _finish(e["sticker"]), sorted(_present(e["sticker"])), sorted(e["sticker"].params)]
        for e in sorted(entries, key=lambda e: e["slot"])])


def _update(mat, entries):
    """Same structure: new images, values and labels only."""
    nodes = mat.node_tree.nodes
    for entry in entries:
        slot, sticker = int(entry["slot"]), entry["sticker"]
        for role, path in _present(sticker).items():
            n = nodes.get(node_name(slot, "TEX_" + role.upper()))
            img = _image(path, data=role in ST.DATA_ROLES)
            if n is not None and img is not None and n.image != img:
                n.image = img
        set_values(mat, slot, entry["offset"], entry["scale"], entry.get("rotation", 0.0), entry.get("wear", 0.0))
        group, wear_node = nodes.get(node_name(slot, "STICKER")), nodes.get(node_name(slot, "WEAR"))
        if group is not None:
            finish = _finish(sticker)
            label = f"Slot {slot + 1}: {sticker.name} ({ST.FINISH_LABELS.get(finish, finish)})"
            if group.label != label:
                group.label = label
        for target in (group, wear_node):
            if target is None:
                continue
            values = {k: v for k, v in sticker.params.items() if k not in ("Wear", "Alpha", "Alpha Clip")}
            if target is wear_node:
                values["Wear Seed"] = wear_seed(sticker.id, slot)
            for key, value in values.items():
                sock = target.inputs.get(key)
                if sock is not None and not sock.is_linked and hasattr(sock, "default_value"):
                    try:
                        if sock.default_value != value:
                            sock.default_value = value
                    except (TypeError, ValueError):
                        pass


def build(mat, entries, uv_map=STICKER_UV_DEFAULT):
    """(Re)build the sticker layer of a weapon material.

    ``entries``: dicts with slot, sticker (ST.Sticker), offset (x, y),
    scale, rotation (turns), wear. Returns warnings."""
    key = layout(entries, uv_map) if entries else None
    if key is not None and mat.get(PROP_LAYOUT) == key and has_layer(mat):
        _update(mat, entries)
        return []
    clear(mat)
    gap.ensure_nodes(mat)  # below the stickers: they draw across the filler faces
    warnings = []
    tree = mat.node_tree
    out = _output(tree)
    if out is None or not out.inputs["Surface"].is_linked:
        return [f"{mat.name}: no material output"] if entries else []
    shader = out.inputs["Surface"].links[0].from_socket
    nodes, links = tree.nodes, tree.links
    x0, y0 = out.location.x, out.location.y - 900
    uv = None
    # mixed in slot order: the highest slot draws on top, like the game (in
    # a link the last sticker covers the earlier ones)
    for i, entry in enumerate(sorted(entries, key=lambda e: e["slot"])):
        if uv is None:
            uv = nodes.new("ShaderNodeUVMap")
            uv.name = uv.label = node_name(0, "UVMAP")
            uv.uv_map = uv_map
            uv.location = (x0 - 2200, y0)
        mix, warn = _slot(tree, entry, uv, shader, (x0 + 300 * i, y0 - 1600 * i))
        warnings += warn
        if mix is not None:
            shader = mix.outputs[0]
    links.new(shader, out.inputs["Surface"])
    if entries and not warnings:
        mat[PROP_LAYOUT] = key
    return warnings


def build_mesh_material(mat, entry):
    """Material of a sticker mesh (sticker_mesh.py): the same sticker nodes
    as in the weapon material, mixed over transparency, through the mesh's
    own 0..1 UV map. Returns warnings."""
    mat.use_nodes = True
    tree = mat.node_tree
    tree.nodes.clear()
    nodes, links = tree.nodes, tree.links
    out = nodes.new("ShaderNodeOutputMaterial")
    out.location = (400, 0)
    clear_bsdf = nodes.new("ShaderNodeBsdfTransparent")
    clear_bsdf.location = (0, 200)
    uv = nodes.new("ShaderNodeUVMap")
    uv.name = uv.label = node_name(entry["slot"], "UVMAP")
    uv.uv_map = "UVMap"
    uv.location = (-1400, 0)
    mix, warnings = _slot(tree, entry, uv, clear_bsdf.outputs[0], (0, 0), vec=uv.outputs[0])
    links.new(mix.outputs[0] if mix is not None else clear_bsdf.outputs[0], out.inputs["Surface"])
    return warnings


def _slot(tree, entry, uv, shader, origin, vec=None):
    """Sticker nodes of one slot mixed over ``shader``. Without ``vec`` the
    sticker vector comes from ``uv`` (UV1) through the game's slot transform."""
    nodes, links = tree.nodes, tree.links
    slot, sticker = int(entry["slot"]), entry["sticker"]
    ox, oy = origin
    warnings = []

    def node(kind, part, x, y, **props):
        n = nodes.new(kind)
        n.name = n.label = node_name(slot, part)
        n.location = (ox + x, oy + y)
        for key, value in props.items():
            setattr(n, key, value)
        return n

    present = _present(sticker)
    for role, path in sticker.textures.items():
        if role not in present:
            warnings.append(f"{sticker.name}: {role} texture not found ({path})")
    base_img = _image(present.get(ST.ROLE_BASE, ""))
    if base_img is None:
        warnings.append(f"{sticker.name}: no base texture, slot {slot + 1} stays empty")
        return None, warnings

    if vec is None:
        xform = node("ShaderNodeGroup", "UV", -1900, 0)
        xform.node_tree = _uv_group()
        links.new(uv.outputs[0], xform.inputs["UV"])
        values = (("Offset X", "OX", entry["offset"][0]), ("Offset Y", "OY", entry["offset"][1]),
                  ("Scale", "SCALE", entry["scale"]), ("Rotation", "ROT", entry.get("rotation", 0.0)))
        for i, (key, part, value) in enumerate(values):
            val = node("ShaderNodeValue", part, -2150, -150 - 110 * i)
            val.outputs[0].default_value = float(value)
            links.new(val.outputs[0], xform.inputs[key])
        vec = xform.outputs["Vector"]

    finish = _finish(sticker)
    group = node("ShaderNodeGroup", "STICKER", -400, 0)
    group.label = f"Slot {slot + 1}: {sticker.name} ({ST.FINISH_LABELS.get(finish, finish)})"
    group.node_tree = SG.ensure_group(finish)
    group.width = 240
    wear_node = node("ShaderNodeGroup", "WEAR", -400, -700)
    wear_node.label = f"Slot {slot + 1} Alpha & Wear"
    wear_node.node_tree = SG.ensure_group("WEAR")
    mix = node("ShaderNodeMixShader", "MIX", 0, 0)
    links.new(shader, mix.inputs[1])
    links.new(group.outputs[0], mix.inputs[2])
    links.new(wear_node.outputs["Alpha"], mix.inputs[0])

    y = [0]

    def tex(role, vector=None, extension="CLIP"):
        img = base_img if role == ST.ROLE_BASE else _image(present.get(role, ""), data=role in ST.DATA_ROLES)
        if img is None:
            return None
        n = node("ShaderNodeTexImage", "TEX_" + role.upper(), -1000, y[0], image=img, extension=extension)
        n.hide = True
        y[0] -= 60
        links.new(vector if vector is not None else vec, n.inputs["Vector"])
        return n

    def wire(src, name, target=group):
        sock = target.inputs.get(name)
        if sock is not None:
            links.new(src, sock)

    def setv(target, name, value):
        sock = target.inputs.get(name)
        if sock is not None and not sock.is_linked and hasattr(sock, "default_value"):
            try:
                sock.default_value = value
            except (TypeError, ValueError):
                pass

    base = tex(ST.ROLE_BASE)
    wire(base.outputs["Color"], "Base Color")
    setv(group, "Alpha", 1.0)      # opaque: the mix factor carries the alpha
    setv(group, "Alpha Clip", 0.0)
    setv(group, "Wear", 0.0)

    wearmask = tex(ST.ROLE_WEARMASK)
    if wearmask is not None:
        # CS2: the sticker shape is wear mask > ~0.8 % (as in cs.sticker).
        shape = node("ShaderNodeMath", "SHAPE", -700, -700, operation="MULTIPLY", use_clamp=True)
        shape.inputs[1].default_value = 128.0
        links.new(wearmask.outputs["Color"], shape.inputs[0])
        wire(shape.outputs[0], "Alpha", wear_node)
        wire(wearmask.outputs["Color"], "Wear Mask", wear_node)
        setv(wear_node, "Use Wear Mask", 1.0)
        scratches = tex(ST.ROLE_SCRATCHES, extension="REPEAT")
        if scratches is not None:
            wire(scratches.outputs["Color"], "Scratches", wear_node)
    else:
        wire(base.outputs["Alpha"], "Alpha", wear_node)
    setv(wear_node, "Alpha Clip", 0.0)
    setv(wear_node, "Wear", float(entry.get("wear", 0.0)))
    setv(wear_node, "Wear Seed", wear_seed(sticker.id, slot))

    normal = tex(ST.ROLE_NORMAL)
    if normal is not None:
        wire(normal.outputs["Color"], "Normal Color")
    rough = tex(ST.ROLE_ROUGHNESS)
    if rough is not None:
        wire(rough.outputs["Color"], "Roughness")
    if finish in ("FOIL", "GOLD"):
        metal = tex(ST.ROLE_METALNESS)
        if metal is not None:
            wire(metal.outputs["Color"], "Metal Mask")
    glow = tex(ST.ROLE_EMISSION)
    if glow is not None:
        wire(glow.outputs["Color"], "Emission Mask")
    if finish == "GLITTER":
        glit = tex(ST.ROLE_GLITTER)
        if glit is not None:
            wire(glit.outputs["Color"], "Glitter Mask")
        if ST.ROLE_FLAKES in present:
            tiling = node("ShaderNodeMapping", "FLAKE_TILING", -1300, y[0])
            tiling.inputs["Scale"].default_value = (8.0, 8.0, 8.0)
            links.new(vec, tiling.inputs["Vector"])
            flakes = tex(ST.ROLE_FLAKES, tiling.outputs["Vector"], extension="REPEAT")
            if flakes is not None:
                wire(flakes.outputs["Color"], "Flake Normal")
                setv(group, "Use Flake Texture", 1.0)
    if finish in ("HOLO", "LENTICULAR") and ST.ROLE_HOLOMASK in present:
        coords = node("ShaderNodeGroup", "HOLO_COORDS", -1300, y[0] - 100)
        coords.node_tree = SG.ensure_group("HOLO_COORDS")
        mask = tex(ST.ROLE_HOLOMASK)
        if mask is not None:
            links.new(mask.outputs["Color"], coords.inputs["Holo Mask"])
            wire(mask.outputs["Color"], "Holo Mask")
            wire(mask.outputs["Alpha"], "Holo Mask Alpha")
        spectrum = tex(ST.ROLE_HOLOSPECTRUM, coords.outputs["Vector"], extension="REPEAT")
        if spectrum is None:
            proc = node("ShaderNodeGroup", "HOLO_SPECTRUM", -1000, y[0] - 100)
            proc.node_tree = SG.ensure_group("HOLO_SPECTRUM")
            links.new(coords.outputs["Vector"], proc.inputs["Vector"])
            spectrum_out = proc.outputs["Color"]
        else:
            spectrum_out = spectrum.outputs["Color"]
        wire(spectrum_out, "Spectrum Color")

    # Catalog params (e.g. Wear Bias) set matching inputs of both groups.
    for key, value in sticker.params.items():
        if key in ("Wear", "Alpha", "Alpha Clip"):
            continue
        setv(group, key, value)
        setv(wear_node, key, value)
    return mix, warnings


def set_values(mat, slot, offset=None, scale=None, rotation=None, wear=None):
    """Live update of one slot (no rebuild)."""
    tree = mat.node_tree if mat is not None else None
    if tree is None:
        return
    for part, value in (("OX", offset[0] if offset else None), ("OY", offset[1] if offset else None),
                        ("SCALE", scale), ("ROT", rotation)):
        n = tree.nodes.get(node_name(slot, part))
        if n is not None and value is not None:
            n.outputs[0].default_value = float(value)
    w = tree.nodes.get(node_name(slot, "WEAR"))
    if w is not None and wear is not None and "Wear" in w.inputs:
        w.inputs["Wear"].default_value = float(wear)
