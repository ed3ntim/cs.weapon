# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Glove material from the game's glove composite shader.

Gloves use their own composite shader (``csgo_customglove``): up to four
textile layers (detail, grunge, damage textures per layer, picked by the
model's layer mask) plus an optional pattern. One program writes, by
``g_nOutputMode``:

- 0: albedo (linear),
- 1: normal (hemi-octahedral x/y) and roughness (z),
- 2: (0, metalness, cloth),
- 3: ambient occlusion, 4: anisotropic gloss (not used here).

Outputs 1 and 2 end with an sRGB to linear conversion because the targets
are sRGB textures: the stored value is the one before it, so it is undone
here like for the weapon shader (paint.py).

Each hand is its own material: the procedure decides per material by its
attributes (``left`` on the left glove, ``seed_offset`` on the right one),
which flips the pattern (``g_bFlipFixup``) and which seed rolls the
texture transforms (``g_nRandomSeed`` left, ``g_nRandomSeedAlt`` right).

Live inputs (Value nodes): wear (``cs_wear``), the seed dependent pattern
and grunge transforms per pass (``cs_p<k>_<uniform>_<i>``) and the flip
factor per pass (``cs_p<k>_flip``), computed by ``live_values`` from the
spec stored on the material. With the flip live, both hands usually have
the same node tree: the second hand is a copy of the first (``for_hand``).
"""

import math
import os

from ..library import pattern, procedure, seed
from . import glsl, udim
from .expr import Graph
from .paint import WEAR, PaintError, _image, _linear_to_srgb, _num, shader_index

KIND = "glove"
# generated texture -> output mode; the AO and anisotropy passes are left out
PASSES = (("g_tColor", 0), ("g_tNormal", 1), ("g_tMetalness", 2))
NORMAL_TEXTURES = ("g_tNormal", "g_tDetailNormal", "g_tDamageNormal")
# live transforms: uniform -> (scale, rotation, offset, rotation sign
# without / with flip fixup, scale used)
XFORMS = {
    "g_vPatternTexCoordXform": ("g_fPatternTexCoordScale", "g_fPatternTexCoordRotation",
                                "g_fPatternTexCoordOffset", 1.0, -1.0, True),
    "g_vPatternTexRotationXform": ("g_fPatternTexCoordScale", "g_fPatternTexCoordRotation",
                                   None, -1.0, 1.0, False),
    "g_vGrungeTexCoordXform": ("g_fGrungeTexCoordScale", "g_fGrungeTexCoordRotation",
                               "g_fGrungeTexCoordOffset", 1.0, 1.0, True),
    "g_vGrungeTexRotationXform": ("g_fGrungeTexCoordScale", "g_fGrungeTexCoordRotation",
                                  None, -1.0, -1.0, False),
}
# the newer combos (S_BACKWARDS_COMPATIBILITY) take their texture
# coordinates from the vertex shader: uniform -> (rotation, offset, scale,
# center); scale None = 1, center None = (0.5, 0.5)
VS_XFORMS = {
    "g_vPatternTexCoordXform": ("g_fPatternTexCoordRotation", "g_vPatternTexCoordOffset",
                                "g_vPatternTexCoordScale", "g_vPatternTexCoordCenter"),
    "g_vDamageTexCoordXform": ("g_fDamageTexCoordRotation", "g_vDamageTexCoordOffset", None, None),
    "g_vGrimeTexCoordXform": ("g_fGrimeTexCoordRotation", "g_vGrimeTexCoordOffset", None, None),
}
VS_OFFSETS = tuple(f"g_vUvOffset{i}" for i in range(1, 5))
VS_PARAMS = {n for v in VS_XFORMS.values() for n in v if n} | set(VS_OFFSETS)
XFORM_PARAMS = ({n for v in XFORMS.values() for n in v[:3] if n} | VS_PARAMS
                | {"g_bFlipFixup", "g_fWearExponent"})


def _srgb_to_linear(x):
    x = float(x)
    return x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4


def _levels(x, y, z):
    return (x, -math.log2(max(1e-4, 1.0 - y)), 2.0 - z)


def seed_for(seed_var, seed_value):
    """Seed of a roll mutator. ``g_nRandomSeedAlt`` (the glove material with
    ``seed_offset``) is the pattern seed plus one: checked against the
    community's per-hand seed lists of Sport Gloves | Ultra Violent (see
    CONTEXT.md)."""
    return int(seed_value) + (1 if seed_var.endswith("Alt") else 0)


def hand_roll(kit, attributes):
    """(seed variable, roll list) of a paint kit for a glove material."""
    weapon = {"attributes": attributes}
    for entry in getattr(kit, "rolls", None) or []:
        cond = entry.get("if")
        if not cond or procedure.condition(cond, weapon):
            return entry.get("seed", "g_nRandomSeed"), entry.get("roll", [])
    return "g_nRandomSeed", list(kit.roll or [])


def xform_values(params):
    """Shader transform uniforms (4-tuples) of a pass from its parameters
    (after rolling)."""
    flip = bool(_num(params.get("g_bFlipFixup", 0)))
    out = {}
    for name, (s_name, r_name, o_name, sign, flip_sign, scaled) in XFORMS.items():
        rot = _num(params.get(r_name, 0.0)) * (flip_sign if flip else sign)
        off = params.get(o_name, (0.0, 0.0)) if o_name else (0.0, 0.0)
        off = (float(off[0]), float(off[1])) if isinstance(off, (list, tuple)) else (float(off), 0.0)
        scale = _num(params.get(s_name, 1.0), 1.0) if scaled else 1.0
        x0, x1 = pattern.xform(rot, off, scale)
        out[name + "0"], out[name + "1"] = x0, x1
    return out


def _vec2(v, default):
    if isinstance(v, (list, tuple)) and len(v) >= 2:
        return float(v[0]), float(v[1])
    if isinstance(v, (int, float)):
        return float(v), float(v)
    return default


def vs_values(params):
    """Vertex shader uniforms of the newer combos (4-tuples) after rolling,
    as the shader's material expressions compute them: rotation in
    degrees around the center, then scale and offset."""
    out = {}
    for name, (r_name, o_name, s_name, c_name) in VS_XFORMS.items():
        r = math.radians(_num(params.get(r_name, 0.0)))
        c, s = math.cos(r), math.sin(r)
        ox, oy = _vec2(params.get(o_name), (0.0, 0.0))
        sx, sy = _vec2(params.get(s_name), (1.0, 1.0)) if s_name else (1.0, 1.0)
        cx, cy = _vec2(params.get(c_name), (0.5, 0.5)) if c_name else (0.5, 0.5)
        out[name + "0"] = (c * sx, -s * sy, 0.0, cx + ox + s * cy - c * cx)
        out[name + "1"] = (s * sx, c * sy, 0.0, cy + oy - (s * cx + c * cy))
    for name in VS_OFFSETS:
        x, y = _vec2(params.get(name), (0.0, 0.0))
        out[name] = (x, y, 0.0, 0.0)
    return out


def live_values(spec, seed_value, wear_value):
    """Values of the live Value nodes of a glove material."""
    rolled = seed.roll_kit(seed_for(spec.get("seed_var", ""), seed_value), spec.get("roll", []))
    out = {WEAR: wear_value}
    for k, base in enumerate(spec.get("passes", [])):
        params = {**base, **rolled}
        flip = _num(base.get("g_bFlipFixup", 0))
        out[f"cs_p{k}_flip"] = -1.0 if flip else 1.0
        out[f"cs_p{k}_flipb"] = 1.0 if flip else 0.0
        values = xform_values(params)
        values.update({f"vs_{n}": v for n, v in vs_values(params).items()})
        for name, val in values.items():
            for i, x in enumerate(val):
                out[f"cs_p{k}_{name}_{i}"] = x
    return out


# luminance weights of the engine's colour matrices
LUMA = (0.2125, 0.7154, 0.0721)


# 4x4 matrices for row vectors (v' = v * M), as nested lists
def _mmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def _chain(*ms):
    out = ms[0]
    for m in ms[1:]:
        out = _mmul(out, m)
    return out


def _scale(x, y=None, z=None):
    y = x if y is None else y
    z = x if z is None else z
    return [[x, 0, 0, 0], [0, y, 0, 0], [0, 0, z, 0], [0, 0, 0, 1]]


def _move(x, y, z):
    return [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [x, y, z, 1]]


def _transpose(m):
    return [list(r) for r in zip(*m)]


def _axis_angle(axis, angle):
    x, y, z = axis
    c, s = math.cos(angle), math.sin(angle)
    return [[x * x + c * (1 - x * x), x * y - c * x * y + s * z, x * z - c * x * z - s * y, 0],
            [x * y - c * x * y - s * z, y * y + c * (1 - y * y), y * z - c * y * z + s * x, 0],
            [x * z - c * x * z + s * y, y * z - c * y * z - s * x, z * z + c * (1 - z * z), 0],
            [0, 0, 0, 1]]


def _lum_frame():
    """Unit luminance axis and the rotation taking it onto z."""
    n = math.sqrt(sum(w * w for w in LUMA))
    lum = tuple(w / n for w in LUMA)
    cross = (lum[1], -lum[0], 0.0)  # lum x (0, 0, 1)
    ln = math.hypot(cross[0], cross[1])
    rot = _axis_angle((cross[0] / ln, cross[1] / ln, 0.0), math.atan2(ln, lum[2]))
    return lum, rot


def _apply(m, v):
    return [sum(v[i] * m[i][j] for i in range(4)) for j in range(4)]


def color_correct(contrast, saturation, brightness, pivot):
    """The engine's MatrixColorCorrect2 (row vectors): contrast around the
    pivot (the texture's average colour), brightness as a factor,
    saturation around the luminance axis."""
    lum, rot = _lum_frame()
    neg = [-x for x in pivot]
    return _chain(_move(*neg), _scale(contrast), _move(*pivot), _scale(brightness),
                  _scale(*lum), rot, _scale(saturation, saturation, 1.0), _transpose(rot),
                  _scale(*(1.0 / x for x in lum)))


def color_tint(rgb, strength=1.0):
    """The engine's MatrixColorTint2 (row vectors): moves colours towards
    the tint's hue by its saturation, keeping the luminance."""
    hi, lo = max(rgb), min(rgb)
    sat = 0.0 if hi == 0 else (hi - lo) / hi
    lum, rot = _lum_frame()
    frame = _chain(_scale(*lum), rot)
    gray = _apply(frame, list(rgb) + [1.0])
    desat, keep = 1.0 - sat, 1.0 - sat * sat * strength
    return _chain(frame, _move(-gray[0], -gray[1], 0.0), _scale(desat, desat, keep),
                  _move(gray[0], gray[1], (1.0 - keep) * gray[2]), _transpose(rot),
                  _scale(*(1.0 / x for x in lum)))


def color_matrix(tint, contrast, saturation, brightness, average):
    """mat4 (flat, column after column, applied as ``vec4(rgb, 1) * m``)
    of ``MatrixMultiply(MatrixColorTint2(tint, 1), MatrixColorCorrect2(
    float3(contrast, saturation, brightness), TextureAverageColor(tex)))``:
    colour correction first, then the tint. The formulas follow
    Source2Viewer's VfxEval implementation (MIT)."""
    tint = [float(x) for x in (list(tint) + [1.0] * 3)[:3]]
    m = _chain(color_correct(contrast, saturation, brightness, [float(x) for x in average]),
               color_tint(tint))
    cols = []
    for j in range(4):
        cols += [m[i][j] for i in range(4)]
    return tuple(float(round(x, 9)) for x in cols)


def color_matrices(p, table, average):
    """Colour matrix uniforms from the shader index's ``color_matrices``
    ({uniform: [tint, contrast, saturation, brightness, texture]}).
    ``average(texture path)`` -> linear average rgb."""
    out = {}
    for name, (tint, con, sat, bri, tex) in (table or {}).items():
        path = p.get(tex)
        avg = average(path) if isinstance(path, str) and path else (0.5, 0.5, 0.5)
        out[name] = color_matrix(p.get(tint, (1.0, 1.0, 1.0)), _num(p.get(con, 1.0), 1.0),
                                 _num(p.get(sat, 1.0), 1.0), _num(p.get(bri, 1.0), 1.0), avg)
    return out


def uniforms(params, defaults=None, index=None, average=None):
    """Constant uniforms of a pass with the derived ones (the shader's
    material expressions, see CONTEXT.md). Colour parameters stay as the
    vmat has them (sRGB): unlike the weapon shader, the glove shader
    converts its colours to linear itself."""
    index = index or {}
    p = dict(defaults or {})
    p.update({k: v for k, v in params.items()
              if not k.startswith(("F_", "g_t")) and isinstance(v, (bool, int, float, list, tuple))})
    u = {}
    for k, v in p.items():
        if isinstance(v, (list, tuple)):
            # the shader's type decides the size: a vmat vec4 for a vec2
            # uniform keeps its first components
            d = (defaults or {}).get(k)
            n = len(d) if isinstance(d, (list, tuple)) else len(v)
            u[k] = tuple(float(x) for x in v[:n])
        else:
            u[k] = float(v)
    texture_params = {k: v for k, v in params.items() if k.startswith("g_t")}
    u.update(color_matrices({**p, **texture_params}, index.get("color_matrices"),
                            average or (lambda _path: (0.5, 0.5, 0.5))))
    for i in range(1, 5):
        u[f"g_fDetailBlackPointCompensation{i}"] = 1.0 - _num(p.get(f"g_fDetailBlackPoint{i}", 0.0))
    u["g_fPaintShadowPower"] = max(0.01, _num(p.get("g_fPaintThickness", 0.0)) * 0.15)
    u["g_nPatternReplaceIndex"] = _num(p.get("g_nPatternReplaceIndex", 1), 1.0) - 1.0
    u["g_nOutputMode"] = float(procedure.feature(params, "F_OUTPUT_MODE"))
    u["g_bPattern"] = float(procedure.feature(params, "F_PATTERN"))
    # the tint id colours of the newer combos are used as they are, unlike
    # g_vColorTintN, which the shader converts itself: the engine passes
    # them linear
    for i in range(1, 9):
        c = u.get(f"g_vId{i}Color")
        if isinstance(c, tuple) and len(c) >= 3:
            u[f"g_vId{i}Color"] = tuple(_srgb_to_linear(x) for x in c[:3]) + c[3:]
    u["g_vTextileAlbedoLevels"] = _levels(0.045, 0.6, 1.0)
    u["g_vMetallicTextileAlbedoLevels"] = _levels(0.08, 0.6, 1.08)
    return u


_UV_ONLY = {}


def _uv_only(path):
    """True if a program reads only the UV (input 0). The newer combos
    also read per-layer UVs, normal and tangent from the composite's
    vertex shader (vs_program)."""
    if path not in _UV_ONLY:
        with open(path, encoding="utf-8") as fh:
            head = fh.read(4096)
        _UV_ONLY[path] = set(glsl.header(head)["inputs"]) <= {0}
    return _UV_ONLY[path]


def vs_program(index, params, folder):
    """Path of the vertex shader combo for a pass, or None."""
    vs = index.get("vs")
    if not vs:
        return None
    cid = procedure.combo_id(params, vs.get("combo_index", {}), vs.get("combo_max", {}))
    path = os.path.join(folder, f"{vs.get('prefix', 'cgvs')}_{cid}.glsl")
    return path if cid in vs.get("combos", []) and os.path.isfile(path) else None


def programs(skin, attributes):
    """[(params, glsl path)] per pass for one glove material, or None."""
    kit = skin.paint_kit
    path = getattr(kit, "shaders", "")
    index = shader_index(path) if path else None
    if not kit.passes or index is None or index.get("shader") != "csgo_customglove":
        return None
    folder = os.path.dirname(path)
    prefix = index.get("prefix", "cg")
    weapon = {"attributes": attributes, "target": {}, "composite_inputs": {}}
    out = []
    for target, mode in PASSES:
        ops = kit.passes.get(target)
        if ops is None:
            return None
        params = procedure.resolve(ops, weapon)
        cid = procedure.combo_id(params, index.get("combo_index", {}), index.get("combo_max", {}))
        glsl_path = os.path.join(folder, f"{prefix}_{cid}.glsl")
        if not os.path.isfile(glsl_path):
            return None
        if not _uv_only(glsl_path) and vs_program(index, params, folder) is None:
            return None
        out.append((params, glsl_path))
    return out


def make_spec(skin, attributes, progs):
    seed_var, roll = hand_roll(skin.paint_kit, attributes)
    passes = [{k: v for k, v in params.items() if k in XFORM_PARAMS} for params, _p in progs]
    return {"kind": KIND, "skin": skin.id, "hand": "left" if attributes.get("left") else "right",
            "seed_var": seed_var, "roll": roll, "passes": passes}


def same_tree(progs_a, progs_b):
    """True if two hands' programs only differ in live values, so one
    material can be a copy of the other."""
    if len(progs_a) != len(progs_b):
        return False
    for (pa, path_a), (pb, path_b) in zip(progs_a, progs_b):
        if path_a != path_b:
            return False
        live = XFORM_PARAMS - {"g_fWearExponent"}  # the exponent is a constant in the tree
        ka = {k: v for k, v in pa.items() if k not in live}
        kb = {k: v for k, v in pb.items() if k not in live}
        if ka != kb:
            return False
    return True


def for_hand(mat, skin, attributes, progs):
    """Turn a copy of the other hand's material into this hand's (same
    node tree, see same_tree). Returns the spec."""
    return make_spec(skin, attributes, progs)


_AVERAGE = {}


def texture_average(path, srgb=True):
    """Linear average colour of a texture file (TextureAverageColor)."""
    key = (path, srgb)
    if key not in _AVERAGE:
        img = _image(path, srgb)
        avg = (0.5, 0.5, 0.5)
        if img is not None and img.size[0] > 0:
            import numpy as np
            px = np.empty(img.size[0] * img.size[1] * img.channels, dtype=np.float32)
            img.pixels.foreach_get(px)
            rgb = px.reshape(-1, img.channels)[:, :3]
            if srgb and not img.is_float:
                rgb = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
            avg = tuple(float(x) for x in rgb.mean(axis=0))
        _AVERAGE[key] = avg
    return _AVERAGE[key]


def _hemi_oct(g, rgb):
    """Decoded RGB normal (as Source2Viewer writes it) back to the
    hemi-octahedral encoding the shader samples: returns (a, b)."""
    x, y, z = (c * 2.0 - 1.0 for c in g.sep(rgb))
    s = g.max(g.abs(x) + g.abs(y) + g.abs(z), 1e-4)
    px, py = x / s, y / s
    return (px + py) * 0.5 + 0.5, (px - py) * 0.5 + 0.5


def vertex_outputs(g, index, params, path, texcoord, k, ps_meta):
    """Pixel shader inputs (name -> values) from the vertex shader run on
    the UV; its seed dependent uniforms are live values of pass k."""
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    meta = glsl.header(src)
    consts, main = glsl.parse_main(src)
    vs = index.get("vs", {})
    u = uniforms(params, vs.get("defaults"), {})
    u["g_bFlipFixup"] = [g.value(f"cs_p{k}_flipb", 0.0)]
    for name in VS_XFORMS:
        for r in "01":
            u[name + r] = [g.value(f"cs_p{k}_vs_{name}{r}_{i}") for i in range(4)]
    for name in VS_OFFSETS:
        u[name] = [g.value(f"cs_p{k}_vs_{name}_{i}") for i in range(4)]
    ins = {meta["inputs"][loc]: list(texcoord) for loc in meta["inputs"]}
    env = glsl.Machine(g, u, ins, None).run(consts, main)
    out = {}
    for loc, name in ps_meta["inputs"].items():
        vs_name = meta["outputs"].get(loc)
        if vs_name is None or vs_name not in env:
            raise glsl.GLSLError(f"vertex shader does not write output {loc}")
        out[name] = env[vs_name]
    return out


RAW_NORMALS_FOLDER = "paints_raw"  # tools/export_cs2_weapons.py RAW_NORMALS_GROUP


def is_raw_normal(path):
    """True for normal textures exported without Source2Viewer's decoding
    (#39): the shader reads them as they are. Older libraries hold decoded
    RGB normals, which g_tNormal & co. encode back to hemi-octahedral."""
    parts = os.path.normpath(path).replace("\\", "/").split("/")
    return RAW_NORMALS_FOLDER in parts[:-1]


def _textures(progs, sources):
    """{(path, srgb)} of the image files the passes' samplers point at."""
    out = set()
    for (params, _path), src in zip(progs, sources):
        for name, srgb in glsl.header(src)["textures"].items():
            path = params.get(name)
            if isinstance(path, str) and path and os.path.isfile(path):
                out.add((path, srgb))
    return out


def _image_size(path, srgb):
    img = _image(path, srgb)
    return tuple(img.size) if img is not None else (0, 0)


def build_nodes(mat, skin, attributes, progs=None):
    """Fill a glove material. Returns the spec."""
    progs = progs or programs(skin, attributes)
    if progs is None:
        raise PaintError(f"no glove shader for {skin.id}")
    index = shader_index(skin.paint_kit.shaders) or {}
    spec = make_spec(skin, attributes, progs)
    from . import group
    nt = group.tree_for(mat, skin.name)
    g = Graph(nt, origin=(-4000.0, 0.0), defer=True)
    wear = g.value(WEAR, 0.1)
    uv = g.socket(g.node("ShaderNodeUVMap", 0).outputs[0], "v", 0)
    u_raw, v_raw, _ = g.sep(uv)
    texcoord = [u_raw, 1.0 - v_raw]  # source convention, v from the top; not wrapped
    cache = {}
    results = []
    folder = os.path.dirname(skin.paint_kit.shaders)
    sources = []
    for params, path in progs:
        with open(path, encoding="utf-8") as fh:
            sources.append(fh.read())
    tiles = udim.plan(_textures(progs, sources), _image_size)
    for k, (params, path) in enumerate(progs):
        src = sources[k]
        meta = glsl.header(src)
        consts, main = glsl.parse_main(src)

        def sample(name, uvvec, params=params, meta=meta):
            tex_path = params.get(name)
            tex_path = tex_path if isinstance(tex_path, str) else ""
            key = (name, tex_path, uvvec.key)
            if key in cache:
                return cache[key]
            srgb = meta["textures"].get(name, False)
            tile = tiles.get((tex_path, srgb))
            img = _image(tex_path, srgb) if tile is None else udim.image(tile[0])
            if img is None:
                res = (g.wrap((0.0, 0.0, 0.0)), g.wrap(0.0), (0.0, 0.0))
            else:
                rgb, alpha = g.texture(img, uvvec if tile is None else udim.tile_uv(g, uvvec, tile[1]))
                if name.startswith(NORMAL_TEXTURES) and not is_raw_normal(tex_path):
                    a, b = _hemi_oct(g, rgb)
                    rgb = g.vec(a, b, 0.0)
                res = (rgb, alpha, (0.0, 1.0))
            cache[key] = res
            return res

        u_const = uniforms(params, index.get("defaults"), index,
                           texture_average)
        u_const["g_fFlipFixup"] = [g.value(f"cs_p{k}_flip", 1.0)]
        u_const["g_fWearProgress"] = [g.pow(g.max(wear, 0.0), _num(params.get("g_fWearExponent", 1.0), 1.0))]
        for name in XFORMS:
            for r in "01":
                u_const[name + r] = [g.value(f"cs_p{k}_{name}{r}_{i}") for i in range(4)]
        inputs = {meta["inputs"][0]: texcoord} if 0 in meta["inputs"] else {}
        if not _uv_only(path):
            inputs = vertex_outputs(g, index, params, vs_program(index, params, folder), texcoord, k, meta)

        def size(name, params=params, meta=meta):
            path = params.get(name)
            img = _image(path, meta["textures"].get(name, False)) if isinstance(path, str) else None
            return tuple(img.size) if img is not None and img.size[0] else (1, 1)

        env = glsl.Machine(g, u_const, inputs, sample, size).run(consts, main)
        out = env.get(meta["outputs"].get(0, ""))
        if out is None:
            raise glsl.GLSLError("shader output not written")
        results.append(out)

    color, normal, metal = results
    bsdf = g.node("ShaderNodeBsdfPrincipled", 60)
    base = g.vec(*color[:3])
    g.feed(bsdf.inputs["Base Color"], base)
    a, b = _linear_to_srgb(g, normal[0]), _linear_to_srgb(g, normal[1])
    g.feed(bsdf.inputs["Roughness"], _linear_to_srgb(g, normal[2]))
    g.feed(bsdf.inputs["Metallic"], _linear_to_srgb(g, metal[1]))
    cloth = _linear_to_srgb(g, metal[2])
    final = procedure.resolve(skin.paint_kit.final, {"attributes": attributes})
    if procedure.feature(final, "F_CLOTH_SHADING"):
        g.feed(bsdf.inputs["Sheen Weight"], g.min(g.max(cloth, 0.0), 1.0))
        bsdf.inputs["Sheen Roughness"].default_value = 0.5
        # cloth sheen takes the albedo's colour; a white sheen turns
        # full-cloth suede white at grazing angles
        g.feed(bsdf.inputs["Sheen Tint"], base)
    # hemi-octahedral decode: x = a + b - 1, y = a - b, z = 1 - |x| - |y|
    nx, ny = a + b - 1.0, a - b
    nz = g.max(1.0 - g.abs(nx) - g.abs(ny), 0.0)
    ln = g.sqrt(nx * nx + ny * ny + nz * nz)
    nm = g.node("ShaderNodeNormalMap", 59)
    g.feed(nm.inputs["Color"], g.vec(nx / ln * 0.5 + 0.5, ny / ln * 0.5 + 0.5, nz / ln * 0.5 + 0.5))
    nt.links.new(nm.outputs[0], bsdf.inputs["Normal"])
    g.flush()
    group.finish(nt, bsdf.outputs[0], (g.origin[0] + 62 * g.COLUMN, 0.0))
    return spec
