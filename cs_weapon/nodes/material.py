# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Material per spawned weapon.

Skins from an exported library get the game's paint shader (nodes/paint.py).
Without a shader (core demo library, version 1 catalogs, unsupported
combos) the simple material is used: weapon base texture, paint masks and
the pattern texture with the exact seed transform (library/pattern.py).

The material carries a JSON copy of the spec (utils.PROP_SPEC), so seed and
wear can be changed in saved files without the library.
"""

import json
import os

import bpy

from .. import utils
from ..library import pattern, seed
from . import gap, glove, glsl, group, paint

# Value nodes driven from Python when seed changes
XFORM_KEYS = ("cs_x0_u", "cs_x0_v", "cs_x0_t", "cs_x1_u", "cs_x1_v", "cs_x1_t")


def make_spec(skin, legacy):
    kit = skin.paint_kit
    var = skin.weapon.variant(legacy)
    return {
        "kind": "simple",
        "skin": skin.id,
        "legacy": var.legacy,
        "uv_scale": var.uv_scale,
        "weapon_length": var.weapon_length,
        "style": kit.style,
        "pattern_scale": float(kit.params.get("g_flPatternTexCoordScale", 1.0)),
        "ignore_size": bool(kit.params.get("g_bIgnoreWeaponSizeScale", False)),
        "brightness": float(kit.params.get("g_flColorBrightness", 1.0)),
        "roughness": float(kit.params.get("g_flPaintRoughness", 0.4)),
        "ranges": {k: [list(v[0]) if isinstance(v[0], (list, tuple)) else v[0],
                       list(v[1]) if isinstance(v[1], (list, tuple)) else v[1]]
                   for k, v in kit.ranges.items()},
    }


def _load_image(path, colorspace):
    if not path or not os.path.isfile(path):
        return None
    img = bpy.data.images.load(path, check_existing=True)
    img.colorspace_settings.name = colorspace
    # Textures pack data in alpha; never premultiply colour with it.
    img.alpha_mode = "CHANNEL_PACKED"
    return img


def find_cached(skin, legacy=False):
    """An existing material of the same skin and mesh variant, or None.
    Building a paint material evaluates the whole shader; copying an
    existing one is instant (seed and wear are set afterwards)."""
    legacy = bool(legacy)
    for mat in bpy.data.materials:
        if utils.PROP_SPEC not in mat:
            continue
        spec = spec_of(mat)
        if spec and spec.get("skin") == skin.id and bool(spec.get("legacy")) == legacy:
            return mat
    return None


def get(skin, legacy=False):
    """Material for a newly spawned weapon: a copy of a cached one, or new."""
    cached = find_cached(skin, legacy)
    if cached is not None:
        mat = group.copy_material(cached)
        mat.name = skin.name
        from . import sticker_layer
        if not sticker_layer.has_layer(mat):  # else the sticker rebuild adds it
            gap.ensure_nodes(mat)
        return mat
    return build(skin, legacy)


def build(skin, legacy=False, name=None):
    """Create a new material for a skin."""
    mat = _build(skin, legacy, name)
    gap.ensure_nodes(mat)
    return mat


def _build(skin, legacy, name):
    mat = bpy.data.materials.new(name or skin.name)
    mat.use_nodes = True
    if skin.is_vanilla:
        mat[utils.PROP_SPEC] = json.dumps(paint.build_vanilla(mat, skin, legacy))
        return mat
    progs = paint.programs(skin, legacy)
    if progs is not None:
        try:
            spec = paint.build_nodes(mat, skin, legacy, progs)
        except (glsl.GLSLError, paint.PaintError, OSError) as exc:
            print(f"cs.weapon: paint shader failed for {skin.id}, simple material used: {exc}")
        else:
            mat[utils.PROP_SPEC] = json.dumps(spec)
            return mat
    build_simple(mat, skin, legacy)
    return mat


def _find_glove(skin, hand):
    for mat in bpy.data.materials:
        if utils.PROP_SPEC in mat:
            spec = spec_of(mat)
            if spec and spec.get("kind") == glove.KIND and spec.get("skin") == skin.id and spec.get("hand") == hand:
                return mat
    return None


def get_glove(skin):
    """Materials of a glove skin: {model material name: material}. Empty
    when the library has no glove shader (the model keeps its own)."""
    info = skin.weapon.glove
    out, built = {}, []  # built: (programs, material) of this call
    for name, m in info.materials.items():
        attrs = m["attributes"]
        hand = info.hand(name)
        cached = _find_glove(skin, hand)
        if cached is not None:
            mat = group.copy_material(cached)
            mat.name = f"{skin.name} ({hand})"
            out[name] = mat
            continue
        progs = glove.programs(skin, attrs)
        if progs is None:
            print(f"cs.weapon: no supported glove shader for {skin.id}, the model keeps its own material")
            return {}
        mat = None
        for other_progs, other in built:
            if glove.same_tree(other_progs, progs):
                mat = group.copy_material(other)
                spec = glove.for_hand(mat, skin, attrs, progs)
                break
        if mat is None:
            mat = bpy.data.materials.new(f"{skin.name} ({hand})")
            mat.use_nodes = True
            try:
                spec = glove.build_nodes(mat, skin, attrs, progs)
            except (glsl.GLSLError, paint.PaintError, OSError) as exc:
                print(f"cs.weapon: glove shader failed for {skin.id}: {exc}")
                bpy.data.materials.remove(mat)
                return {}
            built.append((progs, mat))
        mat.name = f"{skin.name} ({hand})"
        mat[utils.PROP_SPEC] = json.dumps(spec)
        out[name] = mat
    return out


def build_simple(mat, skin, legacy=False):
    """Fill a material with the simple pattern material."""
    spec = make_spec(skin, legacy)
    mat[utils.PROP_SPEC] = json.dumps(spec)
    nt = mat.node_tree
    N, L = nt.nodes, nt.links
    N.clear()

    def node(kind, x, y, **kw):
        n = N.new(kind)
        n.location = (x, y)
        for k, v in kw.items():
            setattr(n, k, v)
        return n

    def math_(op, a, b=None, x=0, y=0):
        n = node("ShaderNodeMath", x, y, operation=op)
        for i, v in enumerate((a, b)):
            if v is None:
                continue
            if isinstance(v, (int, float)):
                n.inputs[i].default_value = v
            else:
                L.new(v, n.inputs[i])
        return n.outputs[0]

    tex = skin.weapon.variant(legacy).textures
    kit_tex = skin.paint_kit.texture_paths()

    uv = node("ShaderNodeUVMap", -1600, 0)
    sep = node("ShaderNodeSeparateXYZ", -1400, 0)
    L.new(uv.outputs[0], sep.inputs[0])
    # Source convention: v from the top, UV wrapped into 0..1 first.
    u = math_("FRACT", sep.outputs[0], x=-1200, y=100)
    v = math_("FRACT", math_("SUBTRACT", 1.0, sep.outputs[1], x=-1200, y=-100), x=-1050, y=-100)

    vals = {}
    for i, key in enumerate(XFORM_KEYS):
        n = node("ShaderNodeValue", -1050, -300 - i * 90)
        n.name = n.label = key
        vals[key] = n.outputs[0]
    pu = math_("ADD", math_("ADD", math_("MULTIPLY", u, vals["cs_x0_u"], x=-850, y=200),
                            math_("MULTIPLY", v, vals["cs_x0_v"], x=-850, y=50), x=-700, y=120),
               vals["cs_x0_t"], x=-550, y=120)
    pv = math_("ADD", math_("ADD", math_("MULTIPLY", u, vals["cs_x1_u"], x=-850, y=-150),
                            math_("MULTIPLY", v, vals["cs_x1_v"], x=-850, y=-300), x=-700, y=-220),
               vals["cs_x1_t"], x=-550, y=-220)
    puv = node("ShaderNodeCombineXYZ", -400, 0)
    L.new(pu, puv.inputs[0])
    L.new(math_("SUBTRACT", 1.0, pv, x=-550, y=-380), puv.inputs[1])

    def image(path, colorspace, x, y, vector):
        img = _load_image(path, colorspace)
        n = node("ShaderNodeTexImage", x, y)
        n.image = img
        n.extension = "REPEAT"
        L.new(vector, n.inputs[0])
        return n

    pat = image(kit_tex.get("pattern"), "sRGB", -200, 100, puv.outputs[0])
    base = image(tex.get("color"), "sRGB", -200, 400, uv.outputs[0])
    masks = image(tex.get("masks"), "Non-Color", -200, -250, uv.outputs[0])
    msep = node("ShaderNodeSeparateColor", 50, -250)
    L.new(masks.outputs[0], msep.inputs[0])
    paint_mask = msep.outputs[0] if masks.image else None

    # Preliminary paint: pattern x base x brightness (patina-like); a missing
    # base texture leaves the pattern alone.
    mul = node("ShaderNodeMix", 100, 200, data_type="RGBA", blend_type="MULTIPLY")
    mul.inputs[0].default_value = 1.0 if base.image else 0.0
    L.new(pat.outputs[0], mul.inputs[6])
    L.new(base.outputs[0], mul.inputs[7])
    bright = node("ShaderNodeMix", 300, 200, data_type="RGBA", blend_type="MULTIPLY")
    bright.inputs[0].default_value = 1.0
    L.new(mul.outputs[2], bright.inputs[6])
    b = spec["brightness"]
    bright.inputs[7].default_value = (b, b, b, 1.0)

    sel = node("ShaderNodeMix", 500, 300, data_type="RGBA")
    if paint_mask is not None:
        L.new(paint_mask, sel.inputs[0])
    else:
        sel.inputs[0].default_value = 1.0
    L.new(base.outputs[0], sel.inputs[6])
    L.new(bright.outputs[2], sel.inputs[7])

    bsdf = node("ShaderNodeBsdfPrincipled", 750, 200)
    L.new(sel.outputs[2], bsdf.inputs["Base Color"])
    if paint_mask is not None:
        L.new(paint_mask, bsdf.inputs["Metallic"])
    rough = node("ShaderNodeMix", 500, -100, data_type="FLOAT")
    rough.inputs[2].default_value = 0.6
    rough.inputs[3].default_value = spec["roughness"]
    if paint_mask is not None:
        L.new(paint_mask, rough.inputs[0])
    L.new(rough.outputs[0], bsdf.inputs["Roughness"])
    out = node("ShaderNodeOutputMaterial", 1050, 200)
    L.new(bsdf.outputs[0], out.inputs[0])
    return mat


def spec_of(mat):
    try:
        return json.loads(mat.get(utils.PROP_SPEC, ""))
    except (TypeError, ValueError):
        return None


def apply_seed(mat, seed_value, wear_value=None):
    """Set the seed (and wear) dependent values of a weapon material."""
    spec = spec_of(mat)
    if spec is None or not mat.use_nodes:
        return False
    if spec.get("kind") == "vanilla":
        return True
    if spec.get("kind") in ("paint", glove.KIND):
        live = glove.live_values if spec["kind"] == glove.KIND else paint.live_values
        values = live(spec, seed_value, 0.0 if wear_value is None else wear_value)
        if wear_value is None:
            values.pop(paint.WEAR)
        for key, value in values.items():
            node = group.find(mat, key)
            if node is not None:
                node.outputs[0].default_value = value
        return True
    bounds = {k: (tuple(v[0]) if isinstance(v[0], list) else v[0],
                  tuple(v[1]) if isinstance(v[1], list) else v[1])
              for k, v in spec.get("ranges", {}).items()}
    rolled = seed.roll_paint(seed_value, bounds)
    factor = pattern.size_factor(spec["uv_scale"], spec["weapon_length"], spec["legacy"], spec["ignore_size"])
    x0, x1 = pattern.xform(rolled["g_flPatternTexCoordRotation"], rolled["g_vPatternTexCoordOffset"],
                           spec["pattern_scale"], factor)
    values = dict(zip(XFORM_KEYS, (x0[0], x0[1], x0[3], x1[0], x1[1], x1[3])))
    for key, value in values.items():
        node = mat.node_tree.nodes.get(key)
        if node is not None:
            node.outputs[0].default_value = value
    return True


def update_object(obj):
    """Re-apply seed and wear (later StatTrak, nametag) of a weapon object."""
    if not utils.is_weapon(obj):
        return
    done = set()
    for slot in obj.material_slots:
        if slot.material is not None and utils.PROP_SPEC in slot.material and slot.material not in done:
            done.add(slot.material)
            apply_seed(slot.material, obj.cs_weapon.seed, obj.cs_weapon.wear)
