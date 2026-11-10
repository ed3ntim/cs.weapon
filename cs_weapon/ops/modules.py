# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""StatTrak and name tag modules.

The modules are their own objects, parented to the weapon at the
attachment points of the model (``stattrak``/``nametag``, the ``_legacy``
ones on the legacy mesh). Knives use the knife StatTrak module.

* StatTrak: the display samples the digit atlas like the game's shader
  (F_STATTRAK): slot k = floor(u) of the display UV (1 = highest digit),
  fract(u) lies in atlas cell k - 1, so the cell is shifted onto the digit:
  u' = fract(u) + (digit + 2 - k) / 16. Six digits with leading zeros. The
  count is a Value node (``cs_stattrak``) of the object's display material.
  The weapon atlas also holds the words the game shows in special states
  (ERROR!, USER, UNKNWN, six cells each, rows 64/192/256 px below the
  digits); ``cs_stattrak_mode`` 1-3 shows them instead of the count.
  The knife atlas has digits only.
* Name tag: the game draws the text into the module's colour texture
  (composite DRAW_TEXT at a texture position). Here a text object lies on
  that spot of the plate, mapped through the plate's UVs.

Attachment matrices come from the catalog in Source space (inches); the
glTF models are Source rotated by -90 degrees around Z, in metres.
"""

import math
import os

import bpy
from mathutils import Matrix, Vector

from .. import utils
from ..nodes import paint
from ..nodes.expr import Graph

PROP_MODULE = "cs_weapon_module"        # on module objects: stattrak | nametag | nametag_text
PROP_MODULE_KEY = "cs_weapon_module_key"  # catalog module of the object (stattrak / stattrak_knife)
PROP_MODULE_MODEL = "cs_weapon_module_model"  # on module template meshes
COUNT = "cs_stattrak"
MODE = "cs_stattrak_mode"
# Display modes: (property id, label, atlas row offset in texture units)
MODES = (("KILLS", "Kills", 0.0), ("ERROR", "ERROR!", 64 / 512), ("USER", "USER", 192 / 512),
         ("UNKNOWN", "UNKNWN", 256 / 512))
INCH = 0.0254
CELL = 1.0 / 16.0
DIGITS = 6
DISPLAYS = ("stattrak_module_display", "stattrak_module_display_knife")
SOURCE_TO_BLENDER = Matrix.Rotation(-math.pi / 2.0, 4, "Z")
# Name tag text size in texture units (DRAW_TEXT has no size; chosen so
# 20 narrow characters fit the plate) and the lift above the plate.
TEXT_EM = 0.085
TEXT_LIFT = 0.00002
FONT_FILES = {"Arial Narrow": ("ARIALN.TTF", "arialn.ttf")}


def _catalog():
    from ..library import registry
    return registry.get_catalog()


def attachment_local(weapon, name, legacy=False):
    """Blender matrix of an attachment in the model's space, or None."""
    m = weapon.attachment_matrix(name, legacy)
    if m is None:
        return None
    a = Matrix([m[0:4], m[4:8], m[8:12], m[12:16]])
    a.translation = a.translation * INCH
    return SOURCE_TO_BLENDER @ a @ SOURCE_TO_BLENDER.inverted()


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------

def _is_flat_default(path):
    return "default_normal" in os.path.basename(path or "")


def _pbr(name, values):
    """Principled material from csgo_weapon style textures: g_tColor,
    g_tMetalness (x roughness, y metal), g_tNormal."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    g = Graph(nt, origin=(-1200.0, 0.0))
    bsdf = g.node("ShaderNodeBsdfPrincipled", 60)
    img = paint._image(values.get("g_tColor", ""), True)
    if img is not None:
        rgb, _ = g.texture(img)
        g.feed(bsdf.inputs["Base Color"], rgb)
    _metal_rough(g, bsdf, values)
    _normal(g, nt, bsdf, values.get("g_tNormal", ""))
    out = g.node("ShaderNodeOutputMaterial", 61)
    nt.links.new(bsdf.outputs[0], out.inputs[0])
    return mat


def _metal_rough(g, bsdf, values):
    img = paint._image(values.get("g_tMetalness", ""), False)
    if img is None:
        return
    rgb, _ = g.texture(img)
    r, m, _ = g.sep(rgb)
    lo, hi = (tuple(values.get("g_vMetalnessRemapRange", (0.0, 1.0))) + (0.0, 1.0))[:2]
    g.feed(bsdf.inputs["Roughness"], r)
    g.feed(bsdf.inputs["Metallic"], g.mix(float(lo), float(hi), m))


def _normal(g, nt, bsdf, path, uv=None):
    if not path or _is_flat_default(path):
        return
    img = paint._image(path, False)
    if img is None:
        return
    rgb, _ = g.texture(img, uv)
    nm = g.node("ShaderNodeNormalMap", 59)
    g.feed(nm.inputs["Color"], rgb)
    nt.links.new(nm.outputs[0], bsdf.inputs["Normal"])


def digit_uv(g, u, v, count, mode=None):
    """Atlas UV of the StatTrak display (see module docstring). ``mode``
    0 shows the count, 1-3 the words of MODES."""
    slot = g.floor(u)
    digit = g.floor(g.op("FLOORED_MODULO", g.floor(count / g.pow(10.0, float(DIGITS) - slot)), 10.0))
    au = g.fract(u) + (digit + 2.0 - slot) * CELL
    av = g.fract(v)
    if mode is not None:
        word = g.step(0.5, mode)
        au = g.mix(au, g.fract(u), word)  # words: cell k - 1 holds letter k
        offset = 0.0
        prev = 0.0
        for i, (_id, _label, off) in enumerate(MODES[1:], start=1):
            offset = offset + g.step(i - 0.5, mode) * (off - prev)
            prev = off
        av = av - offset
    return g.vec(au, av, 0.0)


def _display(name, values, knife):
    """StatTrak display: digit atlas, glowing (weapons) or etched (knives)."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    g = Graph(nt, origin=(-1400.0, 0.0))
    count = g.value(COUNT, 0.0)
    mode = None if knife else g.value(MODE, 0.0)
    u, v, _ = g.sep(g.socket(g.node("ShaderNodeUVMap", 0).outputs[0], "v", 0))
    uv = digit_uv(g, u, v, count, mode)
    bsdf = g.node("ShaderNodeBsdfPrincipled", 60)
    img = paint._image(values.get("g_tColor", ""), True)
    rgb = alpha = None
    if img is not None:
        rgb, alpha = g.texture(img, uv, extension="CLIP")
        g.feed(bsdf.inputs["Base Color"], rgb)
    _metal_rough(g, bsdf, values)
    if knife:
        _normal(g, nt, bsdf, values.get("g_tNormal", ""), uv)
        if alpha is not None:
            ref = float(values.get("g_flAlphaTestReference", 0.667))
            g.feed(bsdf.inputs["Alpha"], g.step(ref, alpha))
    elif rgb is not None:
        # F_SELF_ILLUM: albedo * mask * brightness
        glow = float(values.get("g_flSelfIllumBrightness", 1.2)) * 0.9
        g.feed(bsdf.inputs["Emission Color"], rgb)
        bsdf.inputs["Emission Strength"].default_value = glow
    out = g.node("ShaderNodeOutputMaterial", 61)
    nt.links.new(bsdf.outputs[0], out.inputs[0])
    return mat


def _module_material(module, slot_name):
    values = module.materials.get(slot_name)
    if values is None:
        return None
    name = f"cs.weapon {slot_name}"
    if slot_name in DISPLAYS:
        return _display(name, values, slot_name.endswith("_knife"))
    return _pbr(name, values)


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

def module_mesh(module):
    """Template mesh of a module (imported once, materials replaced by
    the add-on's own), or None if the model is missing."""
    if not module.model or not os.path.isfile(module.model):
        return None
    key = os.path.normcase(os.path.abspath(module.model))
    for me in bpy.data.meshes:
        if me.get(PROP_MODULE_MODEL) == key:
            return me
    before_obj, before_mat = set(bpy.data.objects), set(bpy.data.materials)
    bpy.ops.import_scene.gltf(filepath=module.model)
    new = [o for o in bpy.data.objects if o not in before_obj]
    mesh = None
    for obj in new:
        if obj.type == "MESH" and mesh is None:
            mesh = obj.data
            mesh.transform(obj.matrix_world)
    for obj in new:
        bpy.data.objects.remove(obj, do_unlink=True)
    if mesh is None:
        return None
    mesh[PROP_MODULE_MODEL] = key
    mesh.use_fake_user = True  # survives saving while no weapon uses it
    for i, imported in enumerate(list(mesh.materials)):
        base = imported.name.split(".")[0] if imported else ""
        mat = _module_material(module, base)
        if mat is not None:
            mesh.materials[i] = mat
    for mat in set(bpy.data.materials) - before_mat:
        if mat.users == 0:
            bpy.data.materials.remove(mat)
    return mesh


# ---------------------------------------------------------------------------
# Name tag text
# ---------------------------------------------------------------------------

def _uv_frame(mesh, u0, v0):
    """(point, du, dv, normal) on the mesh at texture position (u0, v0)
    (v from the top as in the composite), or None."""
    uv_layer = mesh.uv_layers.active
    if uv_layer is None:
        return None
    target = Vector((u0, 1.0 - v0))
    mesh.calc_loop_triangles()
    best = None
    for tri in mesh.loop_triangles:
        uvs = [uv_layer.data[i].uv.copy() for i in tri.loops]
        a, b, c = uvs
        e1, e2, p = b - a, c - a, target - a
        det = e1.x * e2.y - e1.y * e2.x
        if abs(det) < 1e-12:
            continue
        s = (p.x * e2.y - p.y * e2.x) / det
        t = (e1.x * p.y - e1.y * p.x) / det
        if s < -1e-4 or t < -1e-4 or s + t > 1 + 1e-4:
            continue
        pa, pb, pc = (mesh.vertices[i].co for i in tri.vertices)
        q1, q2 = pb - pa, pc - pa
        # dP/du and dP/dv from the triangle's UV basis
        du = (q1 * e2.y - q2 * e1.y) / det
        dv = (q2 * e1.x - q1 * e2.x) / det
        area = tri.area
        if best is None or area > best[0]:
            best = (area, pa + q1 * s + q2 * t, du, dv, tri.normal.copy())
    return best[1:] if best else None


def _font(name):
    for fname in FONT_FILES.get(name, ()):
        path = os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts", fname)
        if os.path.isfile(path):
            return bpy.data.fonts.load(path, check_existing=True)
    return None  # Blender's built-in font


def text_matrix(mesh, style):
    """Local matrix and size of the name tag text on the plate, or None."""
    pos = style.get("position", (0.04, 0.038))
    frame = _uv_frame(mesh, float(pos[0]), float(pos[1]))
    if frame is None:
        return None
    point, du, dv, normal = frame
    x, y = du.normalized(), dv.normalized()
    z = x.cross(y).normalized()
    if z.dot(normal) < 0:
        normal = -normal
    m = Matrix((x, y, z)).transposed().to_4x4()
    m.translation = point + normal * TEXT_LIFT
    return m, TEXT_EM * dv.length


def _text_material(style):
    name = "cs.weapon nametag text"
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        col = [paint.srgb_to_linear(c / 255.0) for c in style.get("color", (241, 241, 241))]
        if bsdf is not None:
            bsdf.inputs["Base Color"].default_value = (*col, 1.0)
            bsdf.inputs["Roughness"].default_value = 0.6
    return mat


def _make_text(tag_obj, module, text):
    style = module.text
    placed = text_matrix(tag_obj.data, style)
    if placed is None:
        return None
    matrix, size = placed
    curve = bpy.data.curves.new("Name Tag", "FONT")
    curve.body = text
    curve.size = size
    curve.align_x, curve.align_y = "LEFT", "TOP"
    font = _font(style.get("font", ""))
    if font is not None:
        curve.font = font
    curve.materials.append(_text_material(style))
    obj = bpy.data.objects.new("Name Tag Text", curve)
    obj[PROP_MODULE] = "nametag_text"
    for coll in tag_obj.users_collection:
        coll.objects.link(obj)
    obj.parent = tag_obj
    obj.matrix_basis = matrix
    obj.hide_select = True
    return obj


# ---------------------------------------------------------------------------
# Sync with the weapon's properties
# ---------------------------------------------------------------------------

def children(obj, kind):
    return [c for c in obj.children if c.get(PROP_MODULE) == kind]


def _remove(obj):
    for c in list(obj.children_recursive):
        bpy.data.objects.remove(c, do_unlink=True)
    bpy.data.objects.remove(obj, do_unlink=True)


def _place(weapon_obj, child, matrix):
    """Parent a module to the weapon at a model-space matrix."""
    from . import spawn
    child.parent = weapon_obj
    child.matrix_parent_inverse = spawn.template_matrix(weapon_obj.data).inverted_safe()
    child.matrix_basis = matrix
    spawn.follow_rig(weapon_obj, child)


def _own_display(child):
    """Give a StatTrak object its own display material (object-linked)."""
    for slot in child.material_slots:
        mat = slot.material
        if mat is None or COUNT not in mat.node_tree.nodes:
            continue
        if slot.link != "OBJECT":
            slot.link = "OBJECT"
            slot.material = mat.copy()
        elif mat.users > 1:
            slot.material = mat.copy()
        return slot.material
    return None


def _ensure(weapon_obj, kind, key, want, attachment, legacy, replace):
    cat = _catalog()
    module = cat.modules.get(key)
    existing = children(weapon_obj, kind)
    if existing and (not want or replace or existing[0].get(PROP_MODULE_KEY) != key):
        for c in existing:
            _remove(c)
        existing = []
    if not want or module is None:
        return None
    if existing:
        return existing[0]
    skin = cat.get_skin(weapon_obj.get(utils.PROP_SKIN, ""))
    matrix = attachment_local(skin.weapon, attachment, legacy) if skin else None
    mesh = module_mesh(module)
    if matrix is None or mesh is None:
        return None
    child = bpy.data.objects.new("StatTrak" if kind == "stattrak" else "Name Tag", mesh)
    child[PROP_MODULE] = kind
    child[PROP_MODULE_KEY] = key
    for coll in weapon_obj.users_collection:
        coll.objects.link(child)
    _place(weapon_obj, child, matrix)
    return child


def sync(weapon_obj, replace=False):
    """Create, update or remove the modules of a weapon to match its
    StatTrak and name tag properties. ``replace`` re-places them (after
    a mesh swap)."""
    if not utils.is_weapon(weapon_obj):
        return
    cat = _catalog()
    skin = cat.get_skin(weapon_obj.get(utils.PROP_SKIN, ""))
    if skin is None:
        return
    props = weapon_obj.cs_weapon
    legacy = bool(skin.paint_kit.legacy and skin.weapon.has_legacy())
    key = "stattrak_knife" if skin.weapon.is_knife else "stattrak"
    st = _ensure(weapon_obj, "stattrak", key, props.stattrak, "stattrak", legacy, replace)
    if st is not None:
        mat = _own_display(st)
        if mat is not None:
            nodes = mat.node_tree.nodes
            nodes[COUNT].outputs[0].default_value = float(props.stattrak_count)
            if MODE in nodes:
                ids = [m[0] for m in MODES]
                nodes[MODE].outputs[0].default_value = float(ids.index(props.stattrak_mode))
    text = props.nametag.strip()
    tag = _ensure(weapon_obj, "nametag", "nametag", bool(text), "nametag", legacy, replace)
    if tag is not None:
        _sync_text(tag, cat.modules["nametag"], props.nametag)


def _sync_text(tag_obj, module, text):
    found = children(tag_obj, "nametag_text")
    obj = found[0] if found else _make_text(tag_obj, module, text)
    if obj is None:
        return
    if obj.data.users > 1:
        obj.data = obj.data.copy()
    if obj.data.body != text:
        obj.data.body = text


def available():
    """Which modules the loaded libraries provide."""
    return set(_catalog().modules)
