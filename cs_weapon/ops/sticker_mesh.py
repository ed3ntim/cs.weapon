# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Stickers as own meshes on the weapon's sticker slots (option; the
default draws them in the weapon material, nodes/sticker_layer.py).

The mesh is built from the weapon's second UV map with the game's slot
transform run backwards: a grid over the sticker (0..1) goes to UV1

    p   = rotate(q - 0.5, -rotation * 2 pi)           q: sticker uv, v down
    uv1 = p / scale + 0.5 + offset                    uv1: v down (Source)

and each grid point to the surface point with that UV1 (barycentric in
the UV1 triangle), lifted a little along the normal. So the mesh lies
exactly where the in-material sticker would be and follows the surface.
Grid cells that leave the UV1 islands or cross a seam are dropped.

Sticker meshes are children of the weapon (PROP_STICKER_MESH = slot), in
the weapon mesh's coordinates, with their own material."""

import bpy
from mathutils import Vector

from .. import utils
from ..nodes import sticker_layer

PROP_STICKER_MESH = "cs_weapon_sticker_mesh"
GRID = 24
LIFT = 0.00025  # meters along the normal, above the paint
SEAM_FACTOR = 4.0  # an edge longer than this times the median edge crosses a seam


def children(weapon_obj):
    return [c for c in weapon_obj.children if PROP_STICKER_MESH in c]


def remove_all(weapon_obj):
    for c in children(weapon_obj):
        mesh, mat = c.data, c.active_material
        bpy.data.objects.remove(c)
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
        if mat is not None and mat.users == 0:
            bpy.data.materials.remove(mat)


def sticker_to_uv1(s, t, offset, scale, rotation):
    """Sticker image coordinate (v up) -> UV1 in Blender (v up)."""
    import math
    qx, qy = s - 0.5, (1.0 - t) - 0.5
    a = -rotation * 2.0 * math.pi
    c, n = math.cos(a), math.sin(a)
    px, py = qx * c - qy * n, qx * n + qy * c
    sx = px / scale + 0.5 + offset[0]
    sy = py / scale + 0.5 + offset[1]
    return sx, 1.0 - sy


class _UVLookup:
    """Surface point (and normal) of a UV1 coordinate, among the triangles
    whose UV1 bounding box overlaps a given region."""

    def __init__(self, mesh, uv_name, lo, hi):
        mesh.calc_loop_triangles()
        uvs = mesh.uv_layers[uv_name].data
        verts = mesh.vertices
        self.tris = []
        for tri in mesh.loop_triangles:
            a, b, c = (uvs[i].uv.copy() for i in tri.loops)
            if (max(a.x, b.x, c.x) < lo[0] or min(a.x, b.x, c.x) > hi[0]
                    or max(a.y, b.y, c.y) < lo[1] or min(a.y, b.y, c.y) > hi[1]):
                continue
            e1, e2 = b - a, c - a
            det = e1.x * e2.y - e1.y * e2.x
            if abs(det) < 1e-14:
                continue
            p = [verts[i].co.copy() for i in tri.vertices]
            nrm = [verts[i].normal.copy() for i in tri.vertices]
            self.tris.append((a, e1, e2, det, p, nrm, tri.normal.copy()))

    def find(self, u, v):
        for a, e1, e2, det, p, nrm, face_n in self.tris:
            qx, qy = u - a.x, v - a.y
            s = (qx * e2.y - qy * e2.x) / det
            t = (e1.x * qy - e1.y * qx) / det
            if s >= -1e-6 and t >= -1e-6 and s + t <= 1.0 + 1e-6:
                w0 = 1.0 - s - t
                co = p[0] * w0 + p[1] * s + p[2] * t
                n = nrm[0] * w0 + nrm[1] * s + nrm[2] * t
                if n.length < 1e-6:
                    n = face_n
                return co, n.normalized()
        return None


def build_mesh(weapon_mesh, uv_name, entry, grid=GRID, lift=LIFT):
    """Mesh data of one sticker, or None if the slot hits no surface."""
    if uv_name not in weapon_mesh.uv_layers:
        return None
    off, scale, rot = entry["offset"], entry["scale"], entry.get("rotation", 0.0)
    corners = [sticker_to_uv1(s, t, off, scale, rot) for s in (0.0, 1.0) for t in (0.0, 1.0)]
    lo = (min(c[0] for c in corners), min(c[1] for c in corners))
    hi = (max(c[0] for c in corners), max(c[1] for c in corners))
    look = _UVLookup(weapon_mesh, uv_name, lo, hi)
    if not look.tris:
        return None
    n = grid + 1
    points = {}
    for j in range(n):
        for i in range(n):
            s, t = i / grid, j / grid
            hit = look.find(*sticker_to_uv1(s, t, off, scale, rot))
            if hit is not None:
                points[i, j] = (hit[0] + hit[1] * lift, (s, t))
    cells = []
    for j in range(grid):
        for i in range(grid):
            quad = ((i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1))
            if all(q in points for q in quad):
                cells.append(quad)
    if not cells:
        return None
    lengths = sorted((points[q[0]][0] - points[q[1]][0]).length for q in cells)
    limit = lengths[len(lengths) // 2] * SEAM_FACTOR
    keep = [q for q in cells
            if all((points[q[k]][0] - points[q[(k + 1) % 4]][0]).length <= limit for k in range(4))]
    if not keep:
        return None
    index, coords, uvs = {}, [], []
    for q in keep:
        for key in q:
            if key not in index:
                index[key] = len(coords)
                coords.append(points[key][0])
                uvs.append(points[key][1])
    mesh = bpy.data.meshes.new(f"Sticker {entry['slot'] + 1}")
    mesh.from_pydata([tuple(c) for c in coords], [], [tuple(index[k] for k in q) for q in keep])
    layer = mesh.uv_layers.new(name="UVMap")
    for poly in mesh.polygons:
        for li in poly.loop_indices:
            layer.data[li].uv = uvs[mesh.loops[li].vertex_index]
    for poly in mesh.polygons:
        poly.use_smooth = True
    mesh.validate()
    return mesh


def sync(weapon_obj, entries, uv_name):
    """Replace the weapon's sticker meshes with one per entry. Returns warnings."""
    remove_all(weapon_obj)
    warnings = []
    colls = list(weapon_obj.users_collection) or [bpy.context.scene.collection]
    for entry in entries:
        mesh = build_mesh(weapon_obj.data, uv_name, entry)
        if mesh is None:
            warnings.append(f"{entry['sticker'].name}: slot {entry['slot'] + 1} is not on the surface")
            continue
        obj = bpy.data.objects.new(f"{weapon_obj.name} Sticker {entry['slot'] + 1}", mesh)
        obj[PROP_STICKER_MESH] = int(entry["slot"])
        for coll in colls:
            coll.objects.link(obj)
        obj.parent = weapon_obj
        obj.matrix_parent_inverse.identity()
        obj.matrix_basis.identity()
        from . import spawn
        spawn.follow_rig(weapon_obj, obj)
        mat = bpy.data.materials.new(f"Sticker {entry['sticker'].name}")
        mat[utils.PROP_STICKER_MATERIAL] = True
        warnings += sticker_layer.build_mesh_material(mat, entry)
        mesh.materials.append(mat)
    return warnings


def set_wear(weapon_obj, slot, wear):
    for c in children(weapon_obj):
        if c[PROP_STICKER_MESH] == slot and c.active_material is not None:
            sticker_layer.set_values(c.active_material, slot, wear=wear)


def surface_center(weapon_obj, uv_name, entry):
    """Weapon-mesh point at the slot center (tests, placement checks)."""
    if uv_name not in weapon_obj.data.uv_layers:
        return None
    off = entry["offset"]
    u, v = 0.5 + off[0], 1.0 - (0.5 + off[1])
    look = _UVLookup(weapon_obj.data, uv_name, (u - 1e-4, v - 1e-4), (u + 1e-4, v + 1e-4))
    hit = look.find(u, v)
    return Vector(hit[0]) if hit else None
