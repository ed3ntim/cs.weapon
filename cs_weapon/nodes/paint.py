# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Paint material from the game's composite shader.

The composite shader (csgo_customweapon) bakes a skin into albedo and
roughness/metalness textures in texture space. Here the same program is
evaluated live per pixel: its decompiled GLSL (exported per static combo
from the user's installation, see tools/cs2shader.py) runs through
``glsl.Machine`` once per generated texture and feeds a Principled BSDF.

The shader parameters of each pass come from the paint kit's assembly
procedure applied to the weapon variant (library/procedure.py).

Live inputs are Value nodes: the wear amount, the seed-dependent texture
transforms (pattern, wear, grunge, overlay) and further rolled shader
parameters (``cs_roll_<name>_<component>``). ``live_values`` computes them
from the spec stored on the material, so seed and wear work without the
library.
"""

import json
import math
import os

import bpy

from ..library import pattern, procedure, seed
from . import glsl
from .expr import Graph

WEAR = "cs_wear"
XFORMS = ("pattern", "wear", "grunge", "overlay")
XFORM_PARTS = ("x0_u", "x0_v", "x0_t", "x1_u", "x1_v", "x1_t")
# transform -> (scale, rotation, offset) parameter names
XFORM_PARAMS = {
    "pattern": ("g_flPatternTexCoordScale", "g_flPatternTexCoordRotation", "g_vPatternTexCoordOffset"),
    "wear": ("g_flWearTexCoordScale", "g_flWearTexCoordRotation", "g_vWearTexCoordOffset"),
    "grunge": ("g_flGrungeTexCoordScale", "g_flGrungeTexCoordRotation", "g_vGrungeTexCoordOffset"),
    "overlay": ("g_flOverlayTexCoordScale", "g_flOverlayTexCoordRotation", "g_vOverlayTexCoordOffset"),
}
XFORM_NAMES = {n for names in XFORM_PARAMS.values() for n in names}
# Spray-paint halftone: the pattern transform with extra rotation offsets
HALFTONE = {"A": "g_flHalftoneRotationOffsetA", "B": "g_flHalftoneRotationOffsetB",
            "C": "g_flHalftoneRotationOffsetC"}
HALFTONE_DEFAULTS = {"A": 0.0, "B": 4.0, "C": -4.0}

# Shader defaults for parameters a paint kit may leave out (on top of the
# defaults in the exported shader index).
DEFAULTS = {
    "g_fWearSoftness": 0.0, "g_flColorBrightness": 1.0, "g_nColorAdjustmentMode": 1,
    "g_flPaintMetalness": 0.0, "g_flPaintRoughness": 0.6, "g_flPearlescentScale": 0.0,
    "g_vPaintRoughness": (0.6, 0.6, 0.6, 0.6), "g_vPaintMetalness": (0.0, 0.0, 0.0, 0.0),
    "g_vPaintDurability": (0.0, 0.0, 0.0, 0.0), "g_vColor0": (0.5, 0.5, 0.5, 0.0),
    "g_vColor1": (0.59, 0.59, 0.59, 0.0), "g_vColor2": (0.38, 0.38, 0.38, 0.0),
    "g_vColor3": (0.42, 0.42, 0.42, 0.0), "g_bBiasSpray": 0, "g_vSprayBlend": (1.0, 1.0, 0.0, 0.0),
    "g_bHalftoneInCavity": 1, "g_fHalftoneCavityCutoff": 1.0, "g_vHalftonePatternLevels": (0.0, 0.5, 1.0, 0.0),
    "g_vHalftoneThresholds": (0.0, 1.0, 0.0, 0.0), "g_bPearlescentOnMetallicOnly": 0,
    "g_bPearlescenceMaskUsesPatternUVs": 1, "g_bRoughnessUsesPatternUVs": 1,
    "g_bMetalnessUsesPatternUVs": 0, "g_fOverlayBrightness": 1.0, "g_fOverlayDurability": 0.0,
    "g_fOverlayMaterialStrength": 1.0, "g_fOverlayMetalness": 0.0, "g_fOverlayPearlescentMask": 0.0,
    "g_fOverlayRoughness": 0.0, "g_fOverlayStrength": 1.0, "g_flCaseHardeningGeometricInfluence": 1.0,
    "g_flCaseHardeningPatternInfluence": 0.5, "g_flCaseHardeningRampOffset": 0.0,
    "g_vCaseHardening": (0.0, 1.0, 0.0, 0.0), "g_flPatinaAgeStart": 0.0, "g_flPatinaColorize": 1.0,
    "g_flHalftoneRotationOffsetA": 0.0, "g_flHalftoneRotationOffsetB": 4.0,
    "g_flHalftoneRotationOffsetC": -4.0,
}
GLITTER_NODE = "cs_glitter"  # Add Shader of the glitter sparkle
ADDRESS = {0: "REPEAT", 1: "MIRROR", 2: "EXTEND", 3: "CLIP", 4: "MIRROR"}

_index_cache = {}


class PaintError(RuntimeError):
    pass


def srgb_to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _levels(x, y, z):
    return (x, -math.log2(max(1e-4, 1.0 - y)), 2.0 - z)


def _num(v, default=0.0):
    if isinstance(v, (list, tuple)):
        return float(v[0]) if v else default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def shader_index(path):
    """Exported shader index (cached by modification time), None if absent."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    hit = _index_cache.get(path)
    if hit is None or hit[0] != mtime:
        with open(path, encoding="utf-8") as fh:
            hit = (mtime, json.load(fh))
        _index_cache[path] = hit
    return hit[1]


def pass_params(skin, legacy):
    """Resolved shader parameters per pass for a skin on a weapon variant."""
    var = skin.weapon.variant(legacy)
    weapon = {"composite_inputs": var.composite_inputs, "target": var.target}
    return [procedure.resolve(skin.paint_kit.passes.get(t, []), weapon) for t in procedure.PASSES]


def programs(skin, legacy):
    """[(params, glsl path)] per pass, or None if the paint kit has no
    procedure or a shader combo is missing."""
    kit = skin.paint_kit
    if not kit.passes or not kit.shaders:
        return None
    index = shader_index(kit.shaders)
    if index is None:
        return None
    folder = os.path.dirname(kit.shaders)
    out = []
    for params in pass_params(skin, legacy):
        cid = procedure.combo_id(params, index.get("combo_index", {}), index.get("combo_max", {}))
        path = os.path.join(folder, f"cw_{cid}.glsl")
        if not os.path.isfile(path):
            return None
        out.append((params, path))
    return out


def uniforms(params, defaults=None):
    """Constant uniform values of a pass (live ones are added by the
    builder): parameters with the conversions the engine applies."""
    p = dict(defaults or {})
    p.update(DEFAULTS)
    p.update({k: v for k, v in params.items()
              if not k.startswith(("F_", "g_t")) and isinstance(v, (int, float, list, tuple))})
    u = {}
    for k, v in p.items():
        u[k] = tuple(float(x) for x in v) if isinstance(v, (list, tuple)) else float(v)
    for i in range(4):
        c = tuple(p[f"g_vColor{i}"])
        u[f"g_vColor{i}"] = tuple(srgb_to_linear(float(x)) for x in c[:3])
    u["g_vPaintDurability"] = tuple(1.0 - float(x) for x in tuple(p["g_vPaintDurability"]) + (0.0,) * 4)[:4]
    u["g_fOverlayDurability"] = 1.0 - _num(p["g_fOverlayDurability"])
    u["g_vPaintAlbedoLevels"] = _levels(0.045, 0.6, 1.0)
    u["g_vMetallicPaintAlbedoLevels"] = _levels(0.08, 0.6, 1.0)
    u["g_vAlbedoLevels"] = _levels(0.045, 0.4, 1.15)
    u["g_vMetallicAlbedoLevels"] = _levels(0.45, 0.4, 1.08)
    u["g_vDarkMetallicAlbedoLevels"] = _levels(0.1, 0.4, 1.08)
    hl = tuple(p["g_vHalftonePatternLevels"])
    hx, hy, hz = (float(x) for x in hl[:3])
    u["g_vHalftonePatternLevels"] = _levels(-hx, hy, hz)  # the expression negates x
    sb = tuple(p["g_vSprayBlend"])
    u["g_vSprayBiasBlend"] = (1.0 if _num(p["g_bBiasSpray"]) else 0.0, float(sb[0]), float(sb[1]))
    u["g_fColorBoostFactor"] = 63.0
    u["bRoughnessMode"] = float(procedure.feature(params, "F_ROUGHNESS_MODE"))
    u["g_bRoughnessPerColor"] = float(procedure.feature(params, "F_ROUGHNESS_PER_COLOR"))
    u["bSpraypaintHalftone"] = float(procedure.feature(params, "F_SPRAYPAINT_HALFTONE"))
    u["nOverlayBlendMode"] = float(procedure.feature(params, "F_OVERLAY_BLEND_MODE"))
    u["nOverlayMaskMode"] = float(procedure.feature(params, "F_OVERLAY_MASK"))
    return u


def _image(path, srgb):
    if not path or not os.path.isfile(path):
        return None
    name = f"{os.path.basename(path)}{'' if srgb else ' (data)'}"
    img = bpy.data.images.get(name)
    if img is None or bpy.path.abspath(img.filepath) != path:
        img = bpy.data.images.load(path)
        img.name = name
    img.colorspace_settings.name = "sRGB" if srgb else "Non-Color"
    img.alpha_mode = "CHANNEL_PACKED"
    return img


EDGE_TEXELS = 16  # margin of clamped/mirrored pattern coordinates, in texels


def _address_axis(g, x, mode, size):
    """One texture coordinate under a sampler address mode, for an image of
    ``size`` texels sampled with REPEAT. Clamped and mirrored coordinates
    keep ``EDGE_TEXELS`` from the edge: beyond the outer texel centre the
    REPEAT image filters with the opposite edge (Blaze's rear turned
    orange), and the coarser mip levels a small or distant view reads
    have wider texels (the grip turned red-brown at half resolution).
    16 texels cover the mips down to 1/32 of the image."""
    half = min(EDGE_TEXELS / max(int(size), 1), 0.25)
    if mode == "EXTEND" or mode == "CLIP":  # clamp (CLIP's border is rare: clamped too)
        return g.clamp(x, half, 1.0 - half)
    if mode == "MIRROR":
        return g.clamp(1.0 - g.abs(g.fract(x * 0.5) * 2.0 - 1.0), half, 1.0 - half)
    return x


def pattern_address(g, uvvec, horizontal, vertical, size=(1024, 1024)):
    """(image extension, coordinates) for the pattern sampler. Blender's
    image extension applies to both axes; paint kits like Blaze clamp
    horizontally and repeat vertically, so differing modes are applied to
    the coordinates of a REPEAT image of ``size`` (width, height)."""
    if horizontal == vertical:
        return horizontal, uvvec
    x, y, z = g.sep(uvvec)
    return "REPEAT", g.vec(_address_axis(g, x, horizontal, size[0]),
                           _address_axis(g, y, vertical, size[1]), z)


def _uv_inputs(g):
    """Source-space weapon UV (wrapped) and the three transformed UVs."""
    uv = g.socket(g.node("ShaderNodeUVMap", 0).outputs[0], "v", 0)
    u_raw, v_raw, _ = g.sep(uv)
    u = g.fract(u_raw)
    v = g.fract(1.0 - v_raw)
    out = {}
    for key in XFORMS:
        val = {p: g.value(f"cs_{key}_{p}") for p in XFORM_PARTS}
        out[key] = (u * val["x0_u"] + v * val["x0_v"] + val["x0_t"],
                    u * val["x1_u"] + v * val["x1_v"] + val["x1_t"])
    return u, v, out


def overlay_uv(params, u, v, xf):
    """Overlay texture coordinates as the vertex shader picks them."""
    if not procedure.feature(params, "g_bOverlayUsesPatternUVs"):
        return u, v
    if procedure.feature(params, "g_bOverlayUsesUniqueUVs"):
        return xf["overlay"]
    return xf["pattern"]


def _linear_to_srgb(g, x):
    lo = x * 12.92
    hi = 1.055 * g.pow(g.max(x, 0.0), 1.0 / 2.4) - 0.055
    return g.mix(hi, lo, g.op("LESS_THAN", x, 0.0031308))


def make_spec(skin, legacy, params=None):
    """Data the live values need, stored as JSON on the material."""
    params = params if params is not None else pass_params(skin, legacy)[0]
    keep = XFORM_NAMES | set(HALFTONE.values()) | {"g_flUvScale1", "g_bIgnoreWeaponSizeScale"}
    base = {k: v for k, v in params.items() if k in keep and isinstance(v, (int, float, list, tuple))}
    return {"kind": "paint", "skin": skin.id, "legacy": bool(legacy), "base": base,
            "roll": skin.paint_kit.roll}


def live_rolls(spec):
    """Rolled parameters (other than the texture transforms) that are live
    Value nodes: [(name, components)]."""
    return [(r[0], int(r[1])) for r in spec.get("roll", []) if r[0] not in XFORM_NAMES]


def build_nodes(mat, skin, legacy, progs=None):
    """Fill a material with the evaluated paint program. Returns the spec."""
    progs = progs or programs(skin, legacy)
    if progs is None:
        raise PaintError(f"no paint shader for {skin.id}")
    index = shader_index(skin.paint_kit.shaders) or {}
    spec = make_spec(skin, legacy, progs[0][0])
    from . import group
    nt = group.tree_for(mat, skin.name)
    g = Graph(nt, origin=(-4000.0, 0.0), defer=True)
    wear = g.value(WEAR, 0.1)
    u, v, xf = _uv_inputs(g)
    rolls = {}
    for name, n in live_rolls(spec):
        rolls[name] = [g.value(f"cs_roll_{name}_{i}") for i in range(n)]
    cache = {}

    results = []
    for params, path in progs:
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        meta = glsl.header(src)
        consts, main = glsl.parse_main(src)
        in1, in2, in3 = (meta["inputs"].get(i) for i in (1, 2, 3))
        inputs = {}
        if in1:
            inputs[in1] = [u, v, xf["pattern"][0], xf["pattern"][1]]
        if in2:
            inputs[in2] = [xf["wear"][0], xf["wear"][1], xf["grunge"][0], xf["grunge"][1]]
        if in3:
            inputs[in3] = list(overlay_uv(params, u, v, xf))
        address = (ADDRESS.get(procedure.feature(params, "g_nPatternTextureHorizontalSampling"), "REPEAT"),
                   ADDRESS.get(procedure.feature(params, "g_nPatternTextureVerticalSampling"), "REPEAT"))

        def sample(name, uvvec, params=params, meta=meta, address=address):
            tex_path = params.get(name)
            tex_path = tex_path if isinstance(tex_path, str) else ""
            key = (name, tex_path, uvvec.key)
            if key in cache:
                return cache[key]
            img = _image(tex_path, meta["textures"].get(name, False))
            if img is None:
                res = (g.wrap((0.0, 0.0, 0.0)), g.wrap(0.0), (0.0, 0.0))
            else:
                ext = "REPEAT"
                if name == "g_tPattern":
                    ext, uvvec = pattern_address(g, uvvec, *address, size=tuple(img.size))
                rgb, alpha = g.texture(img, uvvec, extension=ext)
                rng = None if tex_path.lower().endswith((".exr", ".hdr")) else (0.0, 1.0)
                res = (rgb, alpha, rng)
            cache[key] = res
            return res

        u_const = uniforms(params, index.get("defaults"))
        u_const["g_flWearAmount"] = [wear]
        for i, key in enumerate(("g_vPatternTexCoordXform0", "g_vPatternTexCoordXform1")):
            r = "x0" if i == 0 else "x1"
            u_const[key] = [g.value(f"cs_pattern_{r}_u"), g.value(f"cs_pattern_{r}_v"), g.wrap(0.0),
                            g.value(f"cs_pattern_{r}_t")]
        for h in HALFTONE:
            for i in (0, 1):
                key = f"g_vPatternTexCoordXformHalftone{h}{i}"
                if key in src:
                    r = f"cs_halftone{h}_x{i}"
                    u_const[key] = [g.value(f"{r}_u"), g.value(f"{r}_v"), g.wrap(0.0), g.value(f"{r}_t")]
        u_const.update(rolls)
        env = glsl.Machine(g, u_const, inputs, sample).run(consts, main)
        out = env.get(meta["outputs"].get(0, ""))
        if out is None:
            raise glsl.GLSLError("shader output not written")
        results.append(out)

    var = skin.weapon.variant(legacy)
    final = {**var.target, **final_params(skin, legacy)}
    bsdf = g.node("ShaderNodeBsdfPrincipled", 60)
    normal = None
    nimg = _image(final.get("g_tNormal") or var.textures.get("vanilla_normal"), False)
    if nimg is not None:
        nm = g.node("ShaderNodeNormalMap", 59)
        tex = g.node("ShaderNodeTexImage", 58)
        tex.image = nimg
        nt.links.new(tex.outputs[0], nm.inputs["Color"])
        nt.links.new(nm.outputs[0], bsdf.inputs["Normal"])
        normal = nm.outputs[0]
    albedo = list(results[0][:3])
    shader = bsdf.outputs[0]
    if len(results) > 1:
        rough = _linear_to_srgb(g, results[1][0])
        lo, hi = (tuple(final.get("g_vMetalnessRemapRange", (0.0, 1.0))) + (0.0, 1.0))[:2]
        metal = g.mix(float(lo), float(hi), _linear_to_srgb(g, results[1][1]))
        mask = _linear_to_srgb(g, results[1][2])
        scale = _num(final.get("g_flPearlescentScale", 0.0))
        if scale:
            albedo = pearlescence(g, albedo, scale, mask, normal)
        sparkle = glitter(g, final, albedo, rough, metal, mask, normal)
        if sparkle is not None:
            albedo, rough, metal, glitter_normal, added = sparkle
            g.feed(bsdf.inputs["Normal"], glitter_normal)
            diffuse = g.node("ShaderNodeBsdfDiffuse", 60)
            g.feed(diffuse.inputs["Color"], added)
            if normal is not None:
                nt.links.new(normal, diffuse.inputs["Normal"])
            add = g.node("ShaderNodeAddShader", 61)
            add.name = add.label = GLITTER_NODE
            nt.links.new(bsdf.outputs[0], add.inputs[0])
            nt.links.new(diffuse.outputs[0], add.inputs[1])
            shader = add.outputs[0]
        g.feed(bsdf.inputs["Roughness"], rough)
        g.feed(bsdf.inputs["Metallic"], metal)
        strength = _num(final.get("g_flIridescentStrength", 0.0))
        if strength > 0.0:
            albedo, tint = iridescence(g, albedo, metal, mask * strength,
                                       _num(final.get("g_flIridescentScale", 1.0), 1.0),
                                       _num(final.get("g_flIridescentHueShift", 0.0)))
            g.feed(bsdf.inputs["Specular Tint"], g.vec(*tint))
    g.feed(bsdf.inputs["Base Color"], g.vec(*albedo))
    g.flush()
    group.finish(nt, shader, (g.origin[0] + 62 * g.COLUMN, 0.0))
    return spec


def build_vanilla(mat, skin, legacy=False):
    """Unpainted default finish: the weapon's own colour, roughness/metal
    (g_tMetalness x/y, metal remapped) and normal map. Returns the spec."""
    var = skin.weapon.variant(legacy)
    target, tex = var.target, var.textures
    nt = mat.node_tree
    nt.nodes.clear()
    g = Graph(nt, origin=(-1200.0, 0.0))
    bsdf = g.node("ShaderNodeBsdfPrincipled", 60)

    def image(key, fallback, srgb, x):
        path = target.get(key) or tex.get(f"vanilla_{fallback}") or tex.get(fallback)
        img = _image(path if isinstance(path, str) else "", srgb)
        if img is None:
            return None
        node = g.node("ShaderNodeTexImage", x)
        node.image = img
        return node

    col = image("g_tColor", "color", True, 40)
    if col is not None:
        nt.links.new(col.outputs[0], bsdf.inputs["Base Color"])
    met = image("g_tMetalness", "metalness", False, 40)
    if met is not None:
        r, m, _ = g.sep(g.socket(met.outputs[0], "v", 41))
        lo, hi = (tuple(target.get("g_vMetalnessRemapRange", (0.0, 1.0))) + (0.0, 1.0))[:2]
        g.feed(bsdf.inputs["Roughness"], r)
        g.feed(bsdf.inputs["Metallic"], g.mix(float(lo), float(hi), m))
    nrm = image("g_tNormal", "normal", False, 40)
    if nrm is not None:
        nm = g.node("ShaderNodeNormalMap", 59)
        nt.links.new(nrm.outputs[0], nm.inputs["Color"])
        nt.links.new(nm.outputs[0], bsdf.inputs["Normal"])
    outn = g.node("ShaderNodeOutputMaterial", 61)
    nt.links.new(bsdf.outputs[0], outn.inputs[0])
    return {"kind": "vanilla", "skin": skin.id, "legacy": bool(legacy)}


def pearlescence(g, rgb, scale, mask, normal=None):
    """The weapon shader's pearlescence: the albedo hue rotates around the
    grey axis by scale * (1 - N.V) * mask radians, blended in by
    saturation^0.125. ``mask``: paint not worn off (roughness pass .z)."""
    lw = g.node("ShaderNodeLayerWeight", 57)
    lw.inputs["Blend"].default_value = 0.5  # Facing = 1 - |N.V|
    if normal is not None:
        g.tree.links.new(normal, lw.inputs["Normal"])
    facing = g.socket(lw.outputs["Facing"], "f", 57)
    angle = facing * mask * scale
    cos_a, sin_a = g.op("COSINE", angle), g.op("SINE", angle)
    r, gr, b = rgb
    k = 1.0 / math.sqrt(3.0)
    axis = (r + gr + b) * (k * k) * (1.0 - cos_a)
    cross = ((b - gr) * k, (r - b) * k, (gr - r) * k)
    rotated = [c * cos_a + x * sin_a + axis for c, x in zip(rgb, cross)]
    hi = g.max(r, g.max(gr, b))
    lo = g.min(r, g.min(gr, b))
    sat = g.op("DIVIDE", hi - lo, g.max(hi, 1e-6))
    t = g.pow(g.max(sat, 0.0), 0.125)
    grey = r * 0.2125 + gr * 0.7154 + b * 0.0721
    return [g.mix(grey, c, t) for c in rotated]


def _hemi_oct(g, x, y):
    """The weapon shader's normal decode (hemi-octahedral, x/y channels)."""
    a = x + y - 1.00392162
    b = x - y
    return g.normalize(g.vec(a, b, 1.0 - g.abs(a) - g.abs(b)))


def glitter(g, final, rgb, rough, metal, mask, normal=None):
    """The weapon shader's glitter (F_GLITTER, csgo_weapon S_GLITTER).

    A flake normal map (g_tGlitterNormal, nearest texel, tiled by
    2.5 * g_fGlitterScale * g_flUvScale1) tilts the normal; sin() of the
    reflected view vector gives sparkles, rainbow coloured by
    g_fGlitterRainbowSpread/-Balance. They light like extra diffuse
    albedo. The flakes also lower roughness, brighten dark albedo, raise
    metalness and bump the shading normal slightly. Everything scales with
    g_fGlitterIntensity and the paint mask (paint not worn off).

    Returns (albedo, roughness, metalness, normal, added diffuse colour)
    or None without glitter."""
    intensity = _num(final.get("g_fGlitterIntensity", 0.0))
    path = final.get("g_tGlitterNormal")
    if intensity <= 0.0 or not procedure.feature(final, "F_GLITTER"):
        return None
    img = _image(path if isinstance(path, str) else "", False)
    if img is None:
        return None
    spread = _num(final.get("g_fGlitterRainbowSpread", 0.0))
    balance = _num(final.get("g_fGlitterRainbowBalance", 0.0))
    tiles = 2.5 * _num(final.get("g_fGlitterScale", 1.0), 1.0) * _num(final.get("g_flUvScale1", 1.0), 1.0)

    amount = mask * min(1.0, intensity)                     # I
    uv_u, uv_v, _ = g.sep(g.socket(g.node("ShaderNodeUVMap", 0).outputs[0], "v", 0))
    flake, w = g.texture(img, g.vec(uv_u * tiles, uv_v * tiles, 0.0), interpolation="Closest")
    fx, fy, _ = g.sep(flake)
    gn = _hemi_oct(g, fx, fy)
    gx, gy, gz = g.sep(gn)
    # tangent -> world: the Normal Map node of the unit flake normal, then
    # scaled by gz as the shader's gn * gn.z
    tangent = g.node("ShaderNodeNormalMap", 57)
    g.feed(tangent.inputs["Color"], g.vec(gx * 0.5 + 0.5, gy * 0.5 + 0.5, gz * 0.5 + 0.5))
    flake_ws = g.socket(tangent.outputs[0], "v", 57) * gz
    geo = g.node("ShaderNodeNewGeometry", 0)
    view = g.socket(geo.outputs["Incoming"], "v", 0)
    base = g.socket(normal, "v", 59) if normal is not None else g.socket(geo.outputs["Normal"], "v", 0)
    n = g.normalize(base + flake_ws)
    refl = g._rec("ShaderNodeVectorMath", [(0, view), (1, n)], [0], "v", operation="REFLECT")[0]
    s = g.vec(*(g.op("SINE", c) for c in g.sep(refl * (12.0 + (5.6 - 12.0) * spread))))
    t = 0.99 + (0.8 - 0.99) * spread
    k = 1.0 / (1.0 - t)
    sx, sy, sz = g.sep(s)
    a = [g.max(c - t, 0.0) * k for c in (sx, sy, sz)]
    white = g.pow(g.lum(g.vec(*(g.clamp(-c) for c in (sx, sy, sz)))), 4.0)
    if balance > 0.0:
        white = white + g.lum(g.vec(*(g.clamp(0.15 - c) for c in (sx, sy, sz)))) * balance
    expo = 4.0 - 3.5 * max(0.0, spread)
    rot = (a[1], a[2], a[0])
    if balance < 0.0:
        rot = [r + g.max(-c - t, 0.0) * (k * -balance) for r, c in zip(rot, (sy, sz, sx))]
    colour = max(0.0, 1.0 - balance) * 4.0
    sparkle = [white + (ac + g.pow(r, expo)) * colour for ac, r in zip(a, rot)]

    tint = g.normalize(g.vec(*(g.max(c, 0.0003) for c in rgb)))
    tx, ty, tz = g.sep(tint)
    facing = g.max(g.dot(view, base), 0.0)
    scale = w * facing * (intensity * mask)
    added = g.vec(*(sp * (0.05 + tc * (1.06 * 0.95)) * scale for sp, tc in zip(sparkle, (tx, ty, tz))))

    flakes = amount * w * g.abs(1.0 - gz)                   # G
    rough = rough * (1.0 - flakes * 0.25)
    boost = g.mix(1.0 + flakes * 2.5, 1.0, g.smoothstep(0.0, 0.8, g.lum(g.vec(*rgb))))
    albedo = [c * boost for c in rgb]
    metal = g.max(metal, flakes * 0.5 * amount)
    bumped = g.normalize(base + flake_ws * (amount * 0.04))
    return albedo, rough, metal, bumped, added


def iridescence(g, rgb, metal, amount, scale, hue_shift):
    """The weapon shader's iridescence: the specular colour F0 =
    mix(0.02, albedo, metal) moves towards a rainbow hue of
    fract((N.V) * scale + hue_shift), keeping its luminance, by
    ``amount`` (strength * paint mask). Returns (base colour, specular
    tint): metals take F0 as base colour, dielectrics as tinted F0."""
    lw = g.node("ShaderNodeLayerWeight", 57)
    lw.inputs["Blend"].default_value = 0.5
    ndotv = 1.0 - g.socket(lw.outputs["Facing"], "f", 57)  # geometric normal, as the shader
    hue = g.fract(ndotv * scale + hue_shift)
    hsv = g.node("ShaderNodeCombineColor", 58)
    hsv.mode = "HSV"
    g.feed(hsv.inputs[0], hue)
    hsv.inputs[1].default_value = 1.0
    hsv.inputs[2].default_value = 1.0
    hr, hg, hb = g.sep(g.socket(hsv.outputs[0], "v", 58))
    f0 = [g.mix(0.02, c, metal) for c in rgb]
    lum = f0[0] * 0.2125 + f0[1] * 0.7154 + f0[2] * 0.0721
    n = [g.max(c, 0.001) for c in (hr, hg, hb)]
    inv = g.op("INVERSE_SQRT", n[0] * n[0] + n[1] * n[1] + n[2] * n[2])
    n = [c * inv for c in n]
    nlum = n[0] * 0.2125 + n[1] * 0.7154 + n[2] * 0.0721
    k = g.min(g.op("DIVIDE", lum, g.max(nlum, 1e-6)), lum * 3.0)
    iri = [g.clamp(g.mix(c, x * k, amount)) for c, x in zip(f0, n)]
    base = [g.mix(c, x, metal) for c, x in zip(rgb, iri)]
    tint = [g.mix(g.clamp(g.op("DIVIDE", x, 0.02)), 1.0, metal) for x in iri]
    return base, tint


def final_params(skin, legacy):
    """Properties the procedure sets on the final weapon material."""
    var = skin.weapon.variant(legacy)
    weapon = {"composite_inputs": var.composite_inputs, "target": var.target}
    return procedure.resolve(skin.paint_kit.final, weapon)


def live_values(spec, seed_value, wear_value):
    """Values of the live Value nodes for a seed and wear."""
    values = dict(spec.get("base", {}))
    values.update(seed.roll_kit(seed_value, spec.get("roll", [])))
    ignore = bool(_num(values.get("g_bIgnoreWeaponSizeScale", 0)))
    factor = pattern.size_factor(_num(values.get("g_flUvScale1", 1.0), 1.0), ignore_size=ignore)
    out = {WEAR: wear_value}
    for key, (s_name, r_name, o_name) in XFORM_PARAMS.items():
        off = values.get(o_name, (0.0, 0.0))
        off = (float(off[0]), float(off[1])) if isinstance(off, (list, tuple)) else (float(off), 0.0)
        x0, x1 = pattern.xform(_num(values.get(r_name, 0.0)), off, _num(values.get(s_name, 1.0), 1.0), factor)
        for part, val in zip(XFORM_PARTS, (x0[0], x0[1], x0[3], x1[0], x1[1], x1[3])):
            out[f"cs_{key}_{part}"] = val
    s_name, r_name, o_name = XFORM_PARAMS["pattern"]
    off = values.get(o_name, (0.0, 0.0))
    off = (float(off[0]), float(off[1])) if isinstance(off, (list, tuple)) else (float(off), 0.0)
    for h, h_name in HALFTONE.items():
        rot = _num(values.get(r_name, 0.0)) + _num(values.get(h_name, HALFTONE_DEFAULTS[h]))
        x0, x1 = pattern.xform(rot, off, _num(values.get(s_name, 1.0), 1.0), factor)
        for part, val in zip(XFORM_PARTS, (x0[0], x0[1], x0[3], x1[0], x1[1], x1[3])):
            out[f"cs_halftone{h}_{part}"] = val
    for name, n in live_rolls(spec):
        val = values.get(name, 0.0)
        val = val if isinstance(val, (list, tuple)) else [val]
        for i in range(n):
            out[f"cs_roll_{name}_{i}"] = float(val[i]) if i < len(val) else 0.0
    return out
