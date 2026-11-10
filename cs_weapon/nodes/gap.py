# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Sticker gaps: filler faces that only carry stickers.

Some models (the AK-47's handguard) have filler faces across a gap with
the material ``weapons/models/shared/materials/sticker_gaps``. In the game
it is csgo_weapon with F_TRANSLUCENT and F_STICKERS, colour alpha 0: the
paint is invisible there, a sticker over the gap is drawn across it.

Here the filler faces get the weapon material as well. A face attribute
(ATTRIBUTE, 1 on filler faces) swaps the weapon shader for a Transparent
BSDF before the sticker layer, so stickers still draw on top::

    weapon shader ─┬─ GAP_MIX ── (sticker layer) ── Material Output
    Transparent ───┘   factor: attribute ATTRIBUTE
"""

MATERIAL = "sticker_gaps"
ATTRIBUTE = "cs_sticker_gap"
NODE_PREFIX = "GAP"


def _base_name(name):
    head, _, tail = name.rpartition(".")
    return head if head and tail.isdigit() else name


def is_gap_material(mat):
    return mat is not None and _base_name(mat.name) == MATERIAL


def gap_slots(mesh):
    """Material indices of the filler faces in a model mesh."""
    return {i for i, m in enumerate(mesh.materials) if is_gap_material(m)}


def ensure_attribute(mesh):
    """Face attribute ATTRIBUTE: 1 on filler faces. Returns True if the
    mesh has filler faces."""
    slots = gap_slots(mesh)
    attr = mesh.attributes.get(ATTRIBUTE)
    if not slots:
        if attr is not None:
            mesh.attributes.remove(attr)
        return False
    if attr is None or attr.domain != "FACE" or attr.data_type != "FLOAT":
        if attr is not None:
            mesh.attributes.remove(attr)
        attr = mesh.attributes.new(ATTRIBUTE, "FLOAT", "FACE")
    attr.data.foreach_set("value", [1.0 if p.material_index in slots else 0.0 for p in mesh.polygons])
    return True


def _output(tree):
    outs = [n for n in tree.nodes if n.type == "OUTPUT_MATERIAL"]
    return next((n for n in outs if n.is_active_output), outs[0] if outs else None)


def has_nodes(mat):
    tree = mat.node_tree if mat is not None else None
    return tree is not None and tree.nodes.get(NODE_PREFIX + "_MIX") is not None


def ensure_nodes(mat):
    """Insert the gap mix between the shader and the output (once). Must
    run before the sticker layer is built, which starts from whatever
    feeds the output."""
    tree = mat.node_tree if mat is not None else None
    if tree is None or has_nodes(mat):
        return
    out = _output(tree)
    if out is None or not out.inputs["Surface"].is_linked:
        return
    shader = out.inputs["Surface"].links[0].from_socket
    nodes, links = tree.nodes, tree.links
    x, y = out.location.x, out.location.y
    out.location.x = x + 250
    attr = nodes.new("ShaderNodeAttribute")
    attr.name = attr.label = NODE_PREFIX + "_ATTR"
    attr.attribute_type = "GEOMETRY"
    attr.attribute_name = ATTRIBUTE
    attr.location = (x - 300, y + 300)
    clear = nodes.new("ShaderNodeBsdfTransparent")
    clear.name = clear.label = NODE_PREFIX + "_CLEAR"
    clear.location = (x - 300, y - 150)
    mix = nodes.new("ShaderNodeMixShader")
    mix.name = NODE_PREFIX + "_MIX"
    mix.label = "Sticker Gaps (stickers only)"
    mix.location = (x, y)
    links.new(attr.outputs["Fac"], mix.inputs[0])
    links.new(shader, mix.inputs[1])
    links.new(clear.outputs[0], mix.inputs[2])
    links.new(mix.outputs[0], out.inputs["Surface"])

