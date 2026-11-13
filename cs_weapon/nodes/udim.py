# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Several textures as tiles of one UDIM image, for materials that would
use more images than EEVEE has samplers (#38).

EEVEE compiles a material with at most 32 samplers, two of them its own.
Driver Gloves | Plum Quill reads 32 different images, Wave Chaser 34:
the shader fails and the gloves render pink. A UDIM image is one texture
array on the GPU and costs one sampler however many tiles it has. So when
a material would go over MAX_IMAGES, images of the same size and colour
space become tiles of one UDIM image (copies of the files, named
``<key>.<1001+i>.png``, in ``_udim`` next to the first file), and each
sample reads its tile: tile column/row plus the fraction of the UV, which
gives the REPEAT wrap inside the tile. Materials under the limit are left
as they are.
"""

import hashlib
import os
import shutil

import bpy

# Images a material may use before tiles are formed (EEVEE: 32 samplers,
# 2 for the engine; some room for what Blender adds).
MAX_IMAGES = 26
ROW = 10  # UDIM tiles per row (1001..1010, 1011..)


def plan(textures, size_of, limit=MAX_IMAGES):
    """Which textures become tiles. ``textures``: iterable of (path, srgb);
    ``size_of(path, srgb)`` -> (width, height). Returns {(path, srgb): (group,
    tile index)} with group = (size, srgb, sorted paths), or {} when the
    material stays under ``limit``. The largest groups are tiled first,
    only as many as needed."""
    textures = sorted(set(textures))
    count = len(textures)
    if count <= limit:
        return {}
    groups = {}
    for path, srgb in textures:
        groups.setdefault((tuple(size_of(path, srgb)), srgb), []).append(path)
    out = {}
    for (size, srgb), paths in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if count <= limit:
            break
        if len(paths) < 2:
            continue
        group = (size, srgb, tuple(paths))
        for i, path in enumerate(paths):
            out[(path, srgb)] = (group, i)
        count -= len(paths) - 1
    return out


def tile_offset(index):
    """(column, row) of tile ``index`` (0 = 1001) in UV space."""
    return index % ROW, index // ROW


def tile_uv(g, uv, index):
    """UV of tile ``index`` for a REPEAT sample at ``uv`` (Graph value)."""
    col, row = tile_offset(index)
    u, v, w = g.sep(uv)
    return g.vec(g.fract(u) + col, g.fract(v) + row, w)


def _key(group):
    size, srgb, paths = group
    text = "|".join(os.path.normcase(os.path.abspath(p)) for p in paths)
    return hashlib.sha1(f"{size}|{srgb}|{text}".encode("utf-8")).hexdigest()[:16]


def _folder(first):
    folder = os.path.join(os.path.dirname(first), "_udim")
    try:
        os.makedirs(folder, exist_ok=True)
        return folder
    except OSError:  # read-only library: Blender's temp folder
        folder = os.path.join(bpy.app.tempdir or os.path.dirname(first), "cs_weapon_udim")
        os.makedirs(folder, exist_ok=True)
        return folder


def image(group):
    """The UDIM image of a tile group (built once, reused)."""
    _size, srgb, paths = group
    key = _key(group)
    name = f"cs_weapon tiles {key}{'' if srgb else ' (data)'}"
    img = bpy.data.images.get(name)
    if img is not None and img.source == "TILED" and len(img.tiles) == len(paths):
        return img
    folder = _folder(paths[0])
    for i, path in enumerate(paths):
        dst = os.path.join(folder, f"{key}.{1001 + i}.png")
        if not os.path.isfile(dst) or os.path.getsize(dst) != os.path.getsize(path):
            shutil.copyfile(path, dst)
    if img is not None:
        bpy.data.images.remove(img)
    img = bpy.data.images.load(os.path.join(folder, f"{key}.1001.png"))
    img.source = "TILED"  # finds the other tiles, path becomes <UDIM>
    img.name = name
    img.colorspace_settings.name = "sRGB" if srgb else "Non-Color"
    img.alpha_mode = "CHANNEL_PACKED"
    return img
