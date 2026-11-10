# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Generate the demo library for the tests (self-made, no game assets):

    blender -b --factory-startup --python tools/generate_demo.py

Writes tests/fixtures/demo_library/ (test fixture, not shipped): a simple low-poly rifle (glb), its base
colour and paint mask textures, two procedural patterns and catalog.json
with two demo skins (a patina and an airbrushed fade).
"""

import json
import math
import os
import sys

import bmesh
import bpy
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "tests", "fixtures", "demo_library")
TEX = 512
PAINT_U = 0.75  # UV u < 0.75: painted metal, right strip: wooden parts


def save_png(path, rgba):
    h, w = rgba.shape[:2]
    img = bpy.data.images.new(os.path.basename(path), w, h, alpha=True)
    img.pixels.foreach_set(np.flipud(rgba).astype(np.float32).ravel())
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


def value_noise(size, cells, rng):
    """Tileable smooth value noise in 0..1."""
    grid = rng.random((cells, cells))
    x = np.linspace(0, cells, size, endpoint=False)
    i0 = np.floor(x).astype(int)
    f = x - i0
    f = f * f * (3 - 2 * f)
    i1 = (i0 + 1) % cells
    a = grid[np.ix_(i0, i0)]
    b = grid[np.ix_(i0, i1)]
    c = grid[np.ix_(i1, i0)]
    d = grid[np.ix_(i1, i1)]
    fy, fx = f[:, None], f[None, :]
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


def textures():
    rng = np.random.default_rng(7)
    os.makedirs(os.path.join(OUT, "textures"), exist_ok=True)
    u = np.linspace(0, 1, TEX, endpoint=False)[None, :].repeat(TEX, 0)
    grain = value_noise(TEX, 64, rng) * 0.08
    metal = np.stack([0.42 + grain, 0.42 + grain, 0.44 + grain, np.ones_like(grain)], -1)
    wood_n = value_noise(TEX, 8, rng)[:, :1].repeat(TEX, 1) * 0.15
    wood = np.stack([0.45 + wood_n, 0.22 + wood_n * 0.5, 0.12 + wood_n * 0.3, np.ones_like(wood_n)], -1)
    is_paint = (u < PAINT_U)[..., None]
    save_png(os.path.join(OUT, "textures", "demo_rifle_color.png"), np.where(is_paint, metal, wood))
    mask = np.zeros((TEX, TEX, 4))
    mask[..., 0] = is_paint[..., 0]
    mask[..., 3] = 1.0
    save_png(os.path.join(OUT, "textures", "demo_rifle_masks.png"), mask)

    # Patina pattern: blotches of blue on gold, like a heat-treated finish
    n = value_noise(TEX, 6, rng) * 0.6 + value_noise(TEX, 16, rng) * 0.3 + value_noise(TEX, 48, rng) * 0.1
    t = np.clip((n - 0.45) * 5.0, 0, 1)[..., None]
    gold, blue = np.array([1.0, 0.82, 0.45, 1.0]), np.array([0.35, 0.55, 1.0, 1.0])
    edge = np.exp(-((n - 0.45) * 18) ** 2)[..., None] * np.array([0.25, -0.1, 0.15, 0.0])
    save_png(os.path.join(OUT, "textures", "demo_patina.png"), np.clip(gold * (1 - t) + blue * t + edge, 0, 1))

    # Fade pattern: diagonal gradient yellow -> pink -> violet
    y = np.linspace(0, 1, TEX)[:, None].repeat(TEX, 1)
    g = (u + y) / 2
    stops = [(0.0, (1.0, 0.85, 0.2)), (0.5, (1.0, 0.2, 0.5)), (1.0, (0.35, 0.15, 0.9))]
    col = np.zeros((TEX, TEX, 4))
    col[..., 3] = 1
    for k in range(3):
        col[..., k] = np.interp(g, [s[0] for s in stops], [s[1][k] for s in stops])
    save_png(os.path.join(OUT, "textures", "demo_fade.png"), col)


def _box(bm, size, loc, rot=0.0):
    geom = bmesh.ops.create_cube(bm, size=1.0)
    verts = geom["verts"]
    bmesh.ops.scale(bm, vec=size, verts=verts)
    if rot:
        bmesh.ops.rotate(bm, verts=verts, cent=(0, 0, 0),
                         matrix=__import__("mathutils").Matrix.Rotation(rot, 3, "X"))
    bmesh.ops.translate(bm, vec=loc, verts=verts)
    return verts


def _cyl(bm, radius, depth, loc):
    from mathutils import Matrix
    geom = bmesh.ops.create_cone(bm, cap_ends=True, segments=12, radius1=radius, radius2=radius, depth=depth,
                                 matrix=Matrix.Rotation(math.pi / 2, 4, "X"))
    bmesh.ops.translate(bm, vec=loc, verts=geom["verts"])
    return geom["verts"]


def part(name, builder, region):
    """Build a mesh part, unwrap it into its UV region (u0, v0, u1, v1)."""
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    builder(bm)
    bm.to_mesh(me)
    bm.free()
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.02)
    bpy.ops.object.mode_set(mode="OBJECT")
    u0, v0, u1, v1 = region
    for loop in me.uv_layers.active.data:
        loop.uv = (u0 + loop.uv.x * (u1 - u0), v0 + loop.uv.y * (v1 - v0))
    return obj


def model():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    # Units: metres, rifle along +Y like the CS2 export, about 0.9 m long.
    metal = part("metal", lambda bm: (
        _box(bm, (0.04, 0.34, 0.07), (0, 0.02, 0.0)),        # receiver
        _cyl(bm, 0.012, 0.36, (0, -0.33, 0.015)),            # barrel
        _box(bm, (0.03, 0.06, 0.03), (0, -0.48, 0.04)),      # front sight block
        _box(bm, (0.028, 0.07, 0.17), (0, -0.07, -0.11), 0.35),  # magazine
    ), (0.0, 0.0, PAINT_U, 1.0))
    wood = part("wood", lambda bm: (
        _box(bm, (0.035, 0.26, 0.08), (0, 0.32, -0.02)),     # stock
        _box(bm, (0.03, 0.05, 0.11), (0, 0.13, -0.09), -0.3),  # grip
        _box(bm, (0.045, 0.16, 0.045), (0, -0.2, 0.0)),      # handguard
    ), (PAINT_U + 0.01, 0.0, 1.0, 1.0))
    bpy.ops.object.select_all(action="DESELECT")
    metal.select_set(True)
    wood.select_set(True)
    bpy.context.view_layer.objects.active = metal
    bpy.ops.object.join()
    metal.name = metal.data.name = "body"
    bpy.ops.object.shade_flat()
    os.makedirs(os.path.join(OUT, "models"), exist_ok=True)
    bpy.ops.export_scene.gltf(filepath=os.path.join(OUT, "models", "demo_rifle.glb"),
                              export_format="GLB", use_selection=True, export_materials="NONE")


def catalog():
    data = {
        "version": 1,
        "weapons": [{
            "id": "demo_rifle", "def_index": 0, "name": "Demo Rifle", "type": "rifle",
            "model": "models/demo_rifle.glb",
            "hd": {"mesh": "body", "uv_scale": 1.0, "weapon_length": 36.0,
                   "textures": {"color": "textures/demo_rifle_color.png",
                                "masks": "textures/demo_rifle_masks.png"},
                   # two sticker slots for the tests (no second UV map in the demo)
                   "target": {"g_vSticker0Offset": [0.0, 0.0], "g_vSticker0Scale": [2.0, 2.0],
                              "g_vSticker1Offset": [0.2, 0.1], "g_vSticker1Scale": [4.0, 4.0]}},
        }],
        "paint_kits": [
            {"id": 90001, "name": "demo_patina", "display": "Demo Patina", "style": 8,
             "params": {"g_flColorBrightness": 1.6, "g_flPaintRoughness": 0.4},
             "textures": {"pattern": "textures/demo_patina.png"},
             "ranges": {"g_flPatternTexCoordRotation": [0, 360],
                        "g_vPatternTexCoordOffset": [[0, 0], [1, 1]]}},
            {"id": 90002, "name": "demo_fade", "display": "Demo Fade", "style": 6, "wear_max": 0.08,
             "params": {"g_flColorBrightness": 2.0, "g_flPaintRoughness": 0.35},
             "textures": {"pattern": "textures/demo_fade.png"},
             "ranges": {"g_flPatternTexCoordRotation": [-55, -65]}},
        ],
        "skins": [{"weapon": "demo_rifle", "paint_kit": 90001}, {"weapon": "demo_rifle", "paint_kit": 90002}],
    }
    with open(os.path.join(OUT, "catalog.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


if __name__ == "__main__":
    model()
    textures()
    catalog()
    print("demo library written to", OUT)
    sys.exit(0)
