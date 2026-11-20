# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Special shaders on weapons, knives and gloves (optional).

The game can add an extra shader to the weapon in the player's hands. CS2 does not
use extra materials for it: the weapon shader (``csgo_weapon``, gloves
``csgo_character``) reads per-object values the game sets while the
player stands in the open, and textures and strengths from the map.

Rain (de_train): ``info_map_parameters`` sets ``envrainstrength``,
``envwetnesscoverage`` (both 1.0), the shader gets
``g_flRainExposureToSkyWetness`` (how wet the item is) and
``g_flRainExposureLocalTimer`` (seconds) per object. Decompiled
(csgo_weapon combo 0, the same block in csgo_character)::

    W     = ToSkyWetness * coverage
    T     = droplets(uv0 * 2.5 * (3 if uv density < 0.002 per in^2 else 1))
            rg: drop normal (y flipped), b: phase, a: drop mask
    drop  = sat((W/4 - fract(T.b + timer * 0.1 * speed^2)) * 5 / (coverage + 0.001))
            * sat((N.z + 0.75) * 4)                       (not under faces)
    rr    = sat((roughness - 0.75) * 4)                    (cloth, rubber)
    dark  = sat(drop * T.a + sat(W) / 2) * (rr * 0.75 + 0.25) * sat(W)
    color = mix(color, pow(color, 1.6) * 0.6, dark)
    rough = mix(rough, 0.1, ao * sat(drop * (1 - rr) * T.a * 4
            + (cos((T.b + timer * 0.2) * 2 pi) / 2 + 0.5) * sat(W) * 0.2 + W * 0.4))
    N_spec = mix(normalize(mix(N_vertex, N_mapped, 1 + dark * 1.5)),
                 drop normal, drop * (1 - rr))

Blender has one normal per Principled BSDF: the specular normal is used
(the wet look comes from it; the game's diffuse normal moves only by a
quarter towards the inverted drop normal). Left out: the albedo UV shift
under drops (0.02 UV at most) and the extra diffuse light on drops.

The effect is a node group (``RAIN_GROUP``) between the skin and the
Principled BSDF: base colour, roughness and normal go through it. Wetness,
coverage, speed and time are inputs of the group node in the material, so
changing them does not rebuild anything.

Frost (de_cbble, ``info_map_parameters`` ``frostenabled``): per object
``g_vFrostState`` (x: frost grown so far, y: frost the item grows to,
z: melt water, w: settled state) and, on weapons only,
``g_vFrostHeatPosOsAndAmount`` (a heat source in object space melting the
frost around it). Texture ``materials/dev/frost.vtex_c`` (512 px; z:
noise, xy: crystal normal, w: sparkle mask), sampled twice: at
uv * S / 8 + 0.37 and, rotated by 37 degrees, at uv * S, where S is the
power of two closest to 0.25 / (UV units per inch) (a face attribute
here, the shader computes it from screen derivatives). Decompiled
(csgo_weapon combo 0, the same block in csgo_character without the heat
source)::

    R     = sat((roughness - 0.75) * 4)                  (cloth, rubber)
    P     = sat(n1 * 0.7 + (1 - metal) * 0.3)
    grow  = sat((1 - x/y - P * 0.6) * 2.5)               (x < y: growing)
    F     = y * (1 - grow)                                (frost thickness)
    H     = mix(sat(mix(Q, Q * n2, 0.6) + (n1 - 0.5) * 0.3),
                sat(Q * mix(1, n1, 0.2) * mix(0.7, 1, U)), R) - 1
            Q, U: from AO^16 / AO^2 and the normal map's deviation
    T     = mix(sat((H + F*.46) * mix(8, 2.5, R)), max(.., sat((H + y*.46) * 2.5)), R)
    frost = sat(T * crystals * fresnel * detail * ...)
    color = mix(albedo, (0.8, 0.86, 0.92), frost); spec -> 0.02
    rough = mix(rough, mix(0.4, 0.12, grow), T * detail * ..)
    N     = bump(N_mapped, frost * mix(0.01, 0.05, R) in) + crystal normal

Left out: the heat source (needs a position on the weapon), the parallax
offset of the second sample (0.01 UV at most), the mip bias that makes
growing frost blurry, the luminance detail term of the skin (a constant
``Detail`` input here) and the extra translucent light on thick frost.
Metallic is lowered with the frost (the game's albedo is diffuse only).
"""

import math
import os

import bpy

from . import group as G
from .expr import Graph

NONE, RAIN, FROST = "NONE", "RAIN", "FROST"
EFFECTS = (RAIN, FROST)
CATALOG_KEYS = {RAIN: "rain", FROST: "frost"}  # effect -> key in the catalog's "special_shaders"
TEXTURE_KEYS = {RAIN: "droplets", FROST: "frost"}  # texture key inside that entry

RAIN_GROUP = "CS2 Rain Shader"
RAIN_VERSION = 1
VERSION_KEY = "cs_weapon_rain_shader_version"
DROPLETS_IMAGE = "cs2_water_droplets"

NODE = "cs_special_rain"            # group node in the material (or paint group)
NODE_AO = "cs_special_rain_ao"
NODE_UV = "cs_special_rain_uv"
NODE_TILE = "cs_special_rain_tile"
NODE_GEO = "cs_special_rain_geo"
TILE_ATTRIBUTE = "cs_rain_tile"  # face attribute: 3 on faces with low UV density
TILE_THRESHOLD = 0.002           # UV area per square inch, from the shader
INCH = 0.0254

FROST_GROUP = "CS2 Frost Shader"
FROST_VERSION = 2  # 2: melt water limited to the frost there is
FROST_VERSION_KEY = "cs_weapon_frost_shader_version"
FROST_IMAGE = "cs2_frost"
FROST_NODE = "cs_special_frost"
SCALE_ATTRIBUTE = "cs_frost_scale"  # face attribute: frost texture scale S
FROST_COLOR = (0.8, 0.86, 0.92)
ROT = (0.798635542392730712890625, 0.6018149852752685546875)  # cos, sin of the second sample

# group inputs: (name, socket type, default, min, max)
INPUTS = (
    ("Color", "NodeSocketColor", (0.5, 0.5, 0.5, 1.0), None, None),
    ("Roughness", "NodeSocketFloat", 0.5, 0.0, 1.0),
    ("Normal", "NodeSocketVector", (0.0, 0.0, 0.0), None, None),
    ("AO", "NodeSocketFloat", 1.0, 0.0, 1.0),
    ("Wetness", "NodeSocketFloat", 1.0, 0.0, 4.0),
    ("Coverage", "NodeSocketFloat", 1.0, 0.0, 1.0),
    ("Time", "NodeSocketFloat", 0.0, None, None),
    ("Speed", "NodeSocketFloat", 1.0, 0.0, 10.0),
    ("UV", "NodeSocketVector", (0.0, 0.0, 0.0), None, None),
    ("Tile", "NodeSocketFloat", 1.0, 1.0, 3.0),
)
OUTPUTS = (("Color", "NodeSocketColor"), ("Roughness", "NodeSocketFloat"), ("Normal", "NodeSocketVector"))
LIVE = ("Wetness", "Coverage", "Time", "Speed")
BSDF_INPUTS = ("Base Color", "Roughness", "Normal")

FROST_INPUTS = (
    ("Color", "NodeSocketColor", (0.5, 0.5, 0.5, 1.0), None, None),
    ("Roughness", "NodeSocketFloat", 0.5, 0.0, 1.0),
    ("Metallic", "NodeSocketFloat", 0.0, 0.0, 1.0),
    ("Normal", "NodeSocketVector", (0.0, 0.0, 0.0), None, None),
    ("AO", "NodeSocketFloat", 1.0, 0.0, 1.0),
    ("Frost", "NodeSocketFloat", 1.0, 0.0, 1.0),
    ("Limit", "NodeSocketFloat", 1.0, 0.0, 1.0),
    ("Melt", "NodeSocketFloat", 0.0, 0.0, 1.0),
    ("Detail", "NodeSocketFloat", 0.5, 0.0, 1.8),
    ("UV", "NodeSocketVector", (0.0, 0.0, 0.0), None, None),
    ("Scale", "NodeSocketFloat", 4.0, 0.0, 1024.0),
)
FROST_OUTPUTS = OUTPUTS + (("Metallic", "NodeSocketFloat"),)
FROST_BSDF_INPUTS = BSDF_INPUTS + ("Metallic",)

# per effect: group node name, BSDF inputs it intercepts, face attribute
# and the group input it feeds (with the value used where it is missing)
EFFECT = {
    RAIN: {"node": NODE, "label": "Rain", "bsdf": BSDF_INPUTS,
           "attr": TILE_ATTRIBUTE, "attr_input": "Tile", "attr_min": 1.0},
    FROST: {"node": FROST_NODE, "label": "Frost", "bsdf": FROST_BSDF_INPUTS,
            "attr": SCALE_ATTRIBUTE, "attr_input": "Scale", "attr_min": 1.0 / 64.0},
}
GROUP_INPUT = {"Base Color": "Color", "Roughness": "Roughness", "Normal": "Normal", "Metallic": "Metallic"}


class MapFxError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Library
# ---------------------------------------------------------------------------

def texture_path(effect):
    """The effect's texture in the loaded libraries ("" if none has one)."""
    from ..library import registry
    cat = registry.get_catalog()
    entry = (getattr(cat, "special_shaders", {}) or {}).get(CATALOG_KEYS.get(effect, ""), {})
    return entry.get(TEXTURE_KEYS.get(effect, ""), "") if isinstance(entry, dict) else ""


def droplets_path():
    """The droplet texture of the loaded libraries ("" if none has one)."""
    return texture_path(RAIN)


def available(effect):
    if effect in EFFECTS:
        path = texture_path(effect)
        return bool(path) and os.path.isfile(path)
    return effect == NONE


def effect_image(effect, path=None):
    """The effect's texture as a data image (loaded once)."""
    return droplets_image(path) if effect == RAIN else _data_image(FROST_IMAGE, path or texture_path(effect))


def droplets_image(path=None):
    return _data_image(DROPLETS_IMAGE, path or droplets_path())


def _data_image(name, path):
    img = bpy.data.images.get(name)
    if img is None:
        if not path or not os.path.isfile(path):
            return None
        img = bpy.data.images.load(path, check_existing=False)
        img.name = name
    elif path and os.path.isfile(path) and bpy.path.abspath(img.filepath) != path:
        img.filepath = path
        img.reload()
    img.colorspace_settings.name = "Non-Color"  # normals, phase, masks: data
    img.alpha_mode = "CHANNEL_PACKED"
    return img


# ---------------------------------------------------------------------------
# Rain group
# ---------------------------------------------------------------------------

def rain_group(image=None):
    """The rain node group (created or updated to RAIN_VERSION)."""
    tree = bpy.data.node_groups.get(RAIN_GROUP)
    if tree is not None and tree.get(VERSION_KEY) == RAIN_VERSION:
        tex = tree.nodes.get("droplets")
        if tex is not None and image is not None and tex.image is not image:
            tex.image = image
        return tree
    if tree is not None:
        tree.name = f"{RAIN_GROUP} (v{tree.get(VERSION_KEY, 0)})"
    tree = bpy.data.node_groups.new(RAIN_GROUP, "ShaderNodeTree")
    tree[VERSION_KEY] = RAIN_VERSION
    iface = tree.interface
    for name, typ, default, lo, hi in INPUTS:
        s = iface.new_socket(name, in_out="INPUT", socket_type=typ)
        s.default_value = default
        if lo is not None:
            s.min_value, s.max_value = lo, hi
    for name, typ in OUTPUTS:
        iface.new_socket(name, in_out="OUTPUT", socket_type=typ)
    _build_rain(tree, image if image is not None else droplets_image())
    return tree


def _build_rain(tree, image):
    nodes, links = tree.nodes, tree.links
    gin = nodes.new("NodeGroupInput")
    gin.location = (-2400, 0)
    g = Graph(tree, origin=(-2200.0, 0.0))
    inp = {s.name: g.socket(s, "v" if s.type in ("RGBA", "VECTOR") else "f", 0) for s in gin.outputs if s.name}
    geo = g.node("ShaderNodeNewGeometry", 0)
    n_vertex = g.socket(geo.outputs["Normal"], "v", 0)

    # droplet texture, Source UV (v from the top) tiled 2.5 x Tile
    u, v, _ = g.sep(inp["UV"])
    k = inp["Tile"] * 2.5
    su, sv = u * k, (1.0 - v) * k
    tex = g.node("ShaderNodeTexImage", 2)
    tex.name = tex.label = "droplets"
    tex.image = image
    tex.extension = "REPEAT"
    tex.interpolation = "Linear"
    g.feed(tex.inputs[0], g.vec(su, 1.0 - sv, 0.0))
    r, gg, phase = g.sep(g.socket(tex.outputs["Color"], "v", 3))
    mask = g.socket(tex.outputs["Alpha"], "f", 3)
    nx, ny = r * 2.0 - 1.0, gg * 2.0 - 1.0

    W = inp["Wetness"] * inp["Coverage"]
    W1 = g.clamp(W)
    t = inp["Time"]
    speed = inp["Speed"]
    cycle = g.fract(phase + t * 0.1 * speed * speed)
    _, _, nz = g.sep(n_vertex)
    drop = g.clamp((W * 0.25 - cycle) * 5.0 / (inp["Coverage"] + 0.001)) * g.clamp((nz + 0.75) * 4.0)

    rough_in = inp["Roughness"]
    rr = g.clamp((rough_in - 0.75) * 4.0)
    dark = g.clamp(drop * mask + W1 * 0.5) * (rr * 0.75 + 0.25) * W1
    dropf = drop * g.clamp(1.0 - rr)

    # colour: mix(c, pow(c, 1.6) * 0.6, dark)
    wet = [g.pow(g.max(c, 0.0), 1.6) * 0.6 for c in g.sep(inp["Color"])]
    color = g.mix(inp["Color"], g.vec(*wet), dark)

    shimmer = (g.op("COSINE", (phase + t * 0.2) * (2.0 * math.pi)) * 0.5 + 0.5) * W1 * 0.2
    rough = g.mix(rough_in, 0.1, inp["AO"] * g.clamp(dropf * mask * 4.0 + shimmer + W * 0.4))

    # drop normal: decoded like the weapon normal maps (Normal Map node)
    nzd = g.sqrt(g.max(1.0 - g.clamp(nx * nx + ny * ny), 0.0))
    nmap = g.node("ShaderNodeNormalMap", 8)
    nmap.space = "TANGENT"
    g.feed(nmap.inputs["Color"], g.vec(r, gg, nzd * 0.5 + 0.5))
    n_drop = g.socket(nmap.outputs[0], "v", 9)
    n_mapped = inp["Normal"]
    exaggerated = g.normalize(g.mix(n_vertex, n_mapped, dark * 1.5 + 1.0))
    normal = g.normalize(g.mix(exaggerated, n_drop, dropf))

    gout = nodes.new("NodeGroupOutput")
    gout.location = (g.origin[0] + 14 * g.COLUMN, 0)
    g.feed(gout.inputs["Color"], color)
    g.feed(gout.inputs["Roughness"], rough)
    g.feed(gout.inputs["Normal"], normal)
    for n in nodes:  # keep the frame tidy: inputs left, outputs right
        if n.bl_idname == "NodeGroupInput":
            n.location = (g.origin[0] - 300, 0)


# ---------------------------------------------------------------------------
# Frost group
# ---------------------------------------------------------------------------

def frost_group(image=None):
    """The frost node group (created or updated to FROST_VERSION)."""
    tree = bpy.data.node_groups.get(FROST_GROUP)
    if tree is not None and tree.get(FROST_VERSION_KEY) == FROST_VERSION:
        for name in ("frost_a", "frost_b"):
            tex = tree.nodes.get(name)
            if tex is not None and image is not None and tex.image is not image:
                tex.image = image
        return tree
    if tree is not None:
        tree.name = f"{FROST_GROUP} (v{tree.get(FROST_VERSION_KEY, 0)})"
    tree = bpy.data.node_groups.new(FROST_GROUP, "ShaderNodeTree")
    tree[FROST_VERSION_KEY] = FROST_VERSION
    iface = tree.interface
    for name, typ, default, lo, hi in FROST_INPUTS:
        s = iface.new_socket(name, in_out="INPUT", socket_type=typ)
        s.default_value = default
        if lo is not None:
            s.min_value, s.max_value = lo, hi
    for name, typ in FROST_OUTPUTS:
        iface.new_socket(name, in_out="OUTPUT", socket_type=typ)
    _build_frost(tree, image if image is not None else effect_image(FROST))
    return tree


def _sample(g, img, name, su, sv, depth):
    """Sample ``img`` at Source UV (su, sv) (v from the top): (rgb, alpha)."""
    tex = g.node("ShaderNodeTexImage", depth)
    tex.name = tex.label = name
    tex.image = img
    tex.extension = "REPEAT"
    tex.interpolation = "Linear"
    g.feed(tex.inputs[0], g.vec(su, 1.0 - sv, 0.0))
    return g.socket(tex.outputs["Color"], "v", depth + 1), g.socket(tex.outputs["Alpha"], "f", depth + 1)


def _tangent_normal(g, x, y, depth):
    """A tangent-space normal (x, y) of the game's frame, decoded like the
    weapon normal maps (Normal Map node)."""
    nz = g.sqrt(g.max(1.0 - g.clamp(x * x + y * y), 0.0))
    inv = 1.0 / g.max(g.sqrt(x * x + y * y + nz * nz), 1e-6)
    nmap = g.node("ShaderNodeNormalMap", depth)
    nmap.space = "TANGENT"
    g.feed(nmap.inputs["Color"], g.vec(x * inv * 0.5 + 0.5, y * inv * 0.5 + 0.5, nz * inv * 0.5 + 0.5))
    return g.socket(nmap.outputs[0], "v", depth + 1)


def _build_frost(tree, img):
    nodes = tree.nodes
    gin = nodes.new("NodeGroupInput")
    g = Graph(tree, origin=(-2600.0, 0.0))
    inp = {s.name: g.socket(s, "v" if s.type in ("RGBA", "VECTOR") else "f", 0) for s in gin.outputs if s.name}
    geo = g.node("ShaderNodeNewGeometry", 0)
    n_vertex = g.socket(geo.outputs["Normal"], "v", 0)
    view = g.socket(geo.outputs["Incoming"], "v", 0)  # towards the camera
    n_mapped = inp["Normal"]
    rough_in, metal, ao = inp["Roughness"], inp["Metallic"], inp["AO"]
    X = g.min(inp["Frost"], inp["Limit"])
    Y = inp["Limit"]
    Z = inp["Melt"] * X  # melt water needs frost to melt

    # two samples: uv * S / 8 + 0.37, and uv * S rotated
    u, v, _ = g.sep(inp["UV"])
    v = 1.0 - v  # Source UV
    S = inp["Scale"]
    a_rgb, _a_alpha = _sample(g, img, "frost_a", u * S * 0.125 + 0.37, v * S * 0.125 + 0.37, 2)
    n1 = a_rgb.z
    cs, sn = ROT
    pu, pv = u * S, v * S
    b_rgb, b_alpha = _sample(g, img, "frost_b", pu * cs - pv * sn, pu * sn + pv * cs, 2)
    bx, by, n2 = g.sep(b_rgb)
    cx, cy = bx * 2.0 - 1.0, by * 2.0 - 1.0
    dx, dy = cx * cs + cy * sn, cy * cs - cx * sn  # crystal normal, rotated back

    R = g.clamp((rough_in - 0.75) * 4.0)
    Rn = 1.0 - R
    P = g.clamp(n1 * 0.7 + (1.0 - metal) * 0.3)
    # x < y: still growing; else settled (melt water z as is, w = 0)
    growing = g.op("LESS_THAN", X, Y)
    G1 = g.clamp((g.clamp(1.0 - X / g.max(Y, 1e-4)) - P * 0.6) * 2.5)
    F1 = Y * (1.0 - G1)
    M1 = (1.0 - g.clamp(((1.0 - g.clamp(Z / g.max(Y - X, 0.001))) - g.clamp(P + R * 0.4) * 0.6) * 2.5)) * (Y - F1)
    F = g.mix(X, F1, growing)
    Mw = g.mix(Z, M1, growing)
    K = g.clamp(G1 * 3.0) * growing
    E = g.clamp(1.0 - F / g.max(Y, 1e-4))
    Mf = g.clamp(Mw / g.max(Y - F, 0.001))

    D = g.clamp(g.sqrt(g.dot(n_mapped - n_vertex, n_mapped - n_vertex)))
    _, _, nvz = g.sep(n_vertex)
    ao16 = g.pow(g.max(ao, 0.0), 16.0)
    Q = g.clamp(g.mix(1.0, D, 0.4) * ao16 * g.mix(1.0, g.clamp(nvz), 0.2) + 0.2)
    U = ao * ao * (1.0 - D)
    H = g.mix(g.clamp(g.mix(Q, Q * n2, 0.6) + (n1 - 0.5) * 0.3),
              g.clamp(Q * g.mix(1.0, n1, 0.2) * g.mix(0.7, 1.0, U)), R) - 1.0
    a1 = H + F * 0.46
    a2 = H + Y * 0.46
    L7 = g.clamp(a1 * g.mix(8.0, 2.5, R))
    T = g.mix(L7, g.max(L7, g.clamp(a2 * 2.5)), R)
    B = g.clamp(a1 * 2.5)
    J = g.clamp(g.clamp(a2 * 4.0) - g.clamp(a1 * 4.0))
    V = g.mix(0.8, 1.0, metal) * 0.5 + inp["Detail"]
    Tv = T * V
    S5 = g.smoothstep(0.0, 0.1, E)
    C = g.max(J, Tv * K) * Mf * V * S5  # melting crystals: wet, glossy

    rough1 = g.mix(rough_in, 0.08, C * (1.0 - R * 0.6))
    w9 = g.clamp((n2 - 1.0 + Mf) * 6.0) * C * Rn * S5
    rough2 = g.mix(rough1, 0.05, w9)

    dlen = g.sqrt(dx * dx + dy * dy)
    fres = 0.75 + g.pow(1.0 - g.clamp(g.dot(n_mapped, view)), 3.0)
    frost = g.clamp(T * g.mix(g.mix(0.02, 0.0, R), 1.0, g.max(B * B, g.mix(g.clamp(dlen * 3.0), b_alpha, R)))
                    * g.mix(0.3, 2.7 * g.mix(0.5, 1.0, U), R) * fres
                    * g.mix(1.0, g.clamp(b_alpha * 0.5 + 0.5 - E), R) * V
                    * g.mix(1.0, 0.35, K * Rn))

    # normal: melting crystals, then the frost relief as a bump, then flakes
    n_crystal = _tangent_normal(g, dx * 2.5, dy * 2.5, 6)
    n_wet = g.normalize(g.mix(g.normalize(g.mix(n_mapped, n_vertex, C * 0.5 * Rn)), n_crystal, w9))
    bump = g.node("ShaderNodeBump", 9)
    bump.inputs["Strength"].default_value = 1.0
    g.feed(bump.inputs["Height"], frost)
    g.feed(bump.inputs["Distance"], g.mix(0.01, 0.05, R) * INCH)
    g.feed(bump.inputs["Normal"], n_wet)
    n_bump = g.socket(bump.outputs[0], "v", 10)
    n_flake = _tangent_normal(g, dx, dy, 6)
    normal = g.normalize(g.mix(n_bump, n_flake, Tv * (1.0 - R * 0.9)))

    base = inp["Color"] * g.mix(1.0, g.mix(0.8, 0.55, R), C)
    color = g.mix(base, g.vec(*FROST_COLOR), frost)
    rough = g.mix(rough2, g.mix(0.4, 0.12, K), Tv * g.mix(0.5, 1.0, B))
    metallic = metal * (1.0 - frost)

    gout = nodes.new("NodeGroupOutput")
    gout.location = (g.origin[0] + 16 * g.COLUMN, 0)
    g.feed(gout.inputs["Color"], color)
    g.feed(gout.inputs["Roughness"], rough)
    g.feed(gout.inputs["Normal"], normal)
    g.feed(gout.inputs["Metallic"], metallic)
    gin.location = (g.origin[0] - 300, 0)


# ---------------------------------------------------------------------------
# Material
# ---------------------------------------------------------------------------

def _tree(mat):
    """The tree holding the material's Principled BSDF (paint group or the
    material itself)."""
    for tree in (G.group_tree(mat), mat.node_tree if mat is not None else None):
        if tree is not None and any(n.bl_idname == "ShaderNodeBsdfPrincipled" for n in tree.nodes):
            return tree
    return None


def _bsdf(tree):
    return next((n for n in tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled"), None)


def has_effect(mat, effect=None):
    """Whether ``mat`` has ``effect`` (any special shader for None)."""
    tree = _tree(mat) if mat is not None and mat.node_tree else None
    if tree is None:
        return False
    effects = EFFECTS if effect is None else (effect,)
    return any(tree.nodes.get(EFFECT[e]["node"]) is not None for e in effects)


def _ao_path(mat):
    """The weapon's own AO texture (g_tAmbientOcclusion) for the material."""
    from ..library import registry
    from . import material as M
    spec = M.spec_of(mat) or {}
    skin = registry.get_catalog().get_skin(spec.get("skin", "")) if spec.get("skin") else None
    if skin is None or skin.weapon.is_glove:
        return ""
    var = skin.weapon.variant(bool(spec.get("legacy")))
    path = var.target.get("g_tAmbientOcclusion") or var.textures.get("vanilla_ambientocclusion") or ""
    return path if isinstance(path, str) and os.path.isfile(path) else ""


def _group(effect, img):
    return rain_group(img) if effect == RAIN else frost_group(img)


def insert(mat, image=None, effect=RAIN):
    """Put the effect's group between the skin and the BSDF (once). Returns
    the group node, or None for a material without a Principled BSDF."""
    tree = _tree(mat)
    if tree is None:
        return None
    spec = EFFECT[effect]
    name = spec["node"]
    node = tree.nodes.get(name)
    if node is not None:
        return node
    bsdf = _bsdf(tree)
    nodes, links = tree.nodes, tree.links
    x, y = bsdf.location.x, bsdf.location.y
    node = nodes.new("ShaderNodeGroup")
    node.name = name
    node.label = f"Special Shader: {spec['label']}"
    node.node_tree = _group(effect, image or effect_image(effect))
    node.location = (x - 260, y + 200)
    node.width = 200
    for bsdf_name in spec["bsdf"]:
        sock, dst = bsdf.inputs[bsdf_name], node.inputs[GROUP_INPUT[bsdf_name]]
        if sock.is_linked:
            links.new(sock.links[0].from_socket, dst)
        elif bsdf_name != "Normal":
            dv = sock.default_value
            dst.default_value = tuple(dv) if bsdf_name == "Base Color" else dv
    if not bsdf.inputs["Normal"].is_linked:
        geo = nodes.new("ShaderNodeNewGeometry")
        geo.name = geo.label = name + "_geo"
        geo.location = (x - 520, y - 300)
        links.new(geo.outputs["Normal"], node.inputs["Normal"])
    for bsdf_name in spec["bsdf"]:
        links.new(node.outputs[GROUP_INPUT[bsdf_name]], bsdf.inputs[bsdf_name])

    uv = nodes.new("ShaderNodeUVMap")
    uv.name = uv.label = name + "_uv"
    uv.location = (x - 780, y + 100)
    links.new(uv.outputs[0], node.inputs["UV"])
    attr = nodes.new("ShaderNodeAttribute")
    attr.name = attr.label = name + "_tile"
    attr.attribute_type = "GEOMETRY"
    attr.attribute_name = spec["attr"]
    attr.location = (x - 780, y - 50)
    mx = nodes.new("ShaderNodeMath")  # a missing attribute reads 0
    mx.operation = "MAXIMUM"
    mx.name = mx.label = name + "_tile_max"
    mx.inputs[1].default_value = spec["attr_min"]
    mx.location = (x - 560, y - 50)
    links.new(attr.outputs["Fac"], mx.inputs[0])
    links.new(mx.outputs[0], node.inputs[spec["attr_input"]])
    ao_path = _ao_path(mat)
    if ao_path:
        ao = nodes.new("ShaderNodeTexImage")
        ao.name = ao.label = name + "_ao"
        img = bpy.data.images.load(ao_path, check_existing=True)
        img.colorspace_settings.name = "Non-Color"
        ao.image = img
        ao.location = (x - 560, y + 300)
        links.new(uv.outputs[0], ao.inputs[0])
        links.new(ao.outputs["Color"], node.inputs["AO"])
    return node


AUX = ("_ao", "_uv", "_tile", "_tile_max", "_geo")


def remove(mat, effect=None):
    """Take the effect's group out again (every special shader for None);
    the BSDF gets its inputs back. True if something was removed."""
    tree = _tree(mat) if mat is not None and mat.node_tree else None
    if tree is None:
        return False
    done = False
    for e in (EFFECTS if effect is None else (effect,)):
        done = _remove(tree, e) or done
    return done


def _remove(tree, effect):
    spec = EFFECT[effect]
    name = spec["node"]
    node = tree.nodes.get(name)
    if node is None:
        return False
    bsdf = _bsdf(tree)
    links = tree.links
    for bsdf_name in spec["bsdf"]:
        sock = node.inputs[GROUP_INPUT[bsdf_name]]
        src = sock.links[0].from_socket if sock.is_linked else None
        if src is not None and src.node.name != name + "_geo":
            links.new(src, bsdf.inputs[bsdf_name])
        else:
            for link in list(bsdf.inputs[bsdf_name].links):
                links.remove(link)
            if bsdf_name != "Normal":
                dv = sock.default_value
                bsdf.inputs[bsdf_name].default_value = tuple(dv) if bsdf_name == "Base Color" else dv
    if tree.animation_data is not None:
        for fc in list(tree.animation_data.drivers):
            if fc.data_path.startswith(f'nodes["{name}"]'):
                tree.animation_data.drivers.remove(fc)
    for n_name in (name,) + tuple(name + sfx for sfx in AUX):
        n = tree.nodes.get(n_name)
        if n is not None:
            tree.nodes.remove(n)
    return True


def set_frost(mat, frost=None, melt=None):
    """Live inputs of the frost group."""
    tree = _tree(mat) if mat is not None and mat.node_tree else None
    node = tree.nodes.get(FROST_NODE) if tree is not None else None
    if node is None:
        return False
    for name, value in (("Frost", frost), ("Melt", melt)):
        if value is not None:
            node.inputs[name].default_value = float(value)
    return True


def set_values(mat, wetness=None, coverage=None, time=None, speed=None, animate=None):
    """Live inputs of the rain group; ``animate`` drives Time from the
    frame (seconds at the scene's frame rate)."""
    tree = _tree(mat) if mat is not None and mat.node_tree else None
    node = tree.nodes.get(NODE) if tree is not None else None
    if node is None:
        return False
    for name, value in (("Wetness", wetness), ("Coverage", coverage), ("Speed", speed)):
        if value is not None:
            node.inputs[name].default_value = float(value)
    path = f'nodes["{NODE}"].inputs[{list(node.inputs).index(node.inputs["Time"])}].default_value'
    driven = tree.animation_data is not None and tree.animation_data.drivers.find(path) is not None
    if animate and not driven:
        fc = tree.driver_add(path)
        drv = fc.driver
        drv.type = "SCRIPTED"
        var = drv.variables.new()
        var.name = "fps"
        var.type = "CONTEXT_PROP"
        var.targets[0].context_property = "ACTIVE_SCENE"
        var.targets[0].data_path = "render.fps"
        drv.expression = f"frame / max(fps, 1) + {float(time or 0.0)!r}"
    elif animate and driven:
        fc = tree.animation_data.drivers.find(path)
        fc.driver.expression = f"frame / max(fps, 1) + {float(time or 0.0)!r}"
    elif not animate and driven:
        tree.driver_remove(path)
    if not animate and time is not None:
        node.inputs["Time"].default_value = float(time)
    return True


# ---------------------------------------------------------------------------
# Mesh
# ---------------------------------------------------------------------------

def tile_factors(mesh):
    """Per face: 3 where the first UV map is coarse (UV area per square
    inch below TILE_THRESHOLD, the shader's screen-space test), else 1."""
    uv = mesh.uv_layers[0] if mesh.uv_layers else None
    out = []
    for poly in mesh.polygons:
        area_in = poly.area / (INCH * INCH)
        if uv is None or area_in <= 0.0:
            out.append(1.0)
            continue
        pts = [uv.data[i].uv for i in poly.loop_indices]
        a = 0.0
        for i in range(len(pts)):
            p, q = pts[i], pts[(i + 1) % len(pts)]
            a += p.x * q.y - q.x * p.y
        out.append(3.0 if abs(a) * 0.5 / area_in < TILE_THRESHOLD else 1.0)
    return out


def ensure_tile_attribute(mesh):
    attr = mesh.attributes.get(TILE_ATTRIBUTE)
    if attr is not None and attr.domain == "FACE" and attr.data_type == "FLOAT" \
            and len(attr.data) == len(mesh.polygons):
        return attr
    if attr is not None:
        mesh.attributes.remove(attr)
    attr = mesh.attributes.new(TILE_ATTRIBUTE, "FLOAT", "FACE")
    attr.data.foreach_set("value", tile_factors(mesh))
    return attr


def _uv_density(mesh):
    """Per face: UV units per inch (sqrt of UV area / area), None without
    UVs or area."""
    uv = mesh.uv_layers[0] if mesh.uv_layers else None
    out = []
    for poly in mesh.polygons:
        area_in = poly.area / (INCH * INCH)
        if uv is None or area_in <= 0.0:
            out.append(None)
            continue
        pts = [uv.data[i].uv for i in poly.loop_indices]
        a = 0.0
        for i in range(len(pts)):
            p, q = pts[i], pts[(i + 1) % len(pts)]
            a += p.x * q.y - q.x * p.y
        out.append(math.sqrt(abs(a) * 0.5 / area_in) if a else None)
    return out


def frost_scales(mesh):
    """Per face: the frost texture scale S, the power of two closest to
    0.25 / (UV units per inch) (exp2(round(log2(..))) in the shader)."""
    out = []
    for d in _uv_density(mesh):
        if not d:
            out.append(4.0)
            continue
        out.append(2.0 ** math.floor(math.log2(0.25 / max(d, 1e-6)) + 0.5))
    return out


def ensure_scale_attribute(mesh):
    attr = mesh.attributes.get(SCALE_ATTRIBUTE)
    if attr is not None and attr.domain == "FACE" and attr.data_type == "FLOAT" \
            and len(attr.data) == len(mesh.polygons):
        return attr
    if attr is not None:
        mesh.attributes.remove(attr)
    attr = mesh.attributes.new(SCALE_ATTRIBUTE, "FLOAT", "FACE")
    attr.data.foreach_set("value", frost_scales(mesh))
    return attr


# ---------------------------------------------------------------------------
# Object
# ---------------------------------------------------------------------------

def _materials(obj):
    from .. import utils
    seen = []
    for slot in obj.material_slots:
        m = slot.material
        if m is not None and utils.PROP_SPEC in m and m not in seen:
            seen.append(m)
    return seen


def sync(obj):
    """Apply the object's special shader settings to its materials."""
    from .. import utils
    if not utils.is_weapon(obj):
        return
    s = obj.cs_weapon
    effect = s.special_shader
    if effect != NONE and not available(effect):
        effect = NONE
    img = effect_image(effect) if effect != NONE else None
    if obj.type == "MESH":
        if effect == RAIN:
            ensure_tile_attribute(obj.data)
        elif effect == FROST:
            ensure_scale_attribute(obj.data)
    for mat in _materials(obj):
        for other in EFFECTS:
            if other != effect:
                remove(mat, other)
        if effect == NONE or insert(mat, img, effect) is None:
            continue
        if effect == RAIN:
            set_values(mat, s.rain_wetness, 1.0, s.rain_time, s.rain_speed, s.rain_animate)
        else:
            set_frost(mat, s.frost_amount, s.frost_melt)
