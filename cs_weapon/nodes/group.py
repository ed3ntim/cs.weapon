# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""The generated paint and glove nodes live in a node group of their own
(one per material), so the material shows only that group, the output and
the sticker layer: hundreds of math nodes stay out of the way in the
shader editor. Tab into the group to see them.

The group returns the finished shader. Its live Value nodes (pattern
seed, wear, flip) sit inside the group; ``nodes(mat)`` finds them.
A material copy needs its own group (``copy_material``), otherwise two
weapons would share their seed and wear.
"""

import bpy

NODE = "cs_paint"  # name of the group node in the material
SOCKET = "Shader"


def tree_for(mat, label):
    """Empty the material and return the (new) group tree to build into.
    The material gets the group node and its output."""
    nt = mat.node_tree
    old = nt.nodes.get(NODE)
    if old is not None and old.node_tree is not None and old.node_tree.users <= 1:
        remove_tree(old.node_tree)
    nt.nodes.clear()
    tree = bpy.data.node_groups.new(f"{label} (cs.weapon)", "ShaderNodeTree")
    tree.interface.new_socket(SOCKET, in_out="OUTPUT", socket_type="NodeSocketShader")
    node = nt.nodes.new("ShaderNodeGroup")
    node.name = NODE
    node.label = label
    node.node_tree = tree
    node.location = (0.0, 0.0)
    node.width = 240
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (400.0, 0.0)
    nt.links.new(node.outputs[0], out.inputs["Surface"])
    return tree


def remove_tree(tree):
    """Remove a paint group and the nested part groups (``.<name> part N``,
    see ``expr.Graph.flush``) nothing else uses any more."""
    parts = {n.node_tree for n in tree.nodes
             if n.bl_idname == "ShaderNodeGroup" and n.node_tree is not None
             and n.node_tree.name.startswith(".")}
    bpy.data.node_groups.remove(tree)
    for part in parts:
        if part.users == 0:
            remove_tree(part)


def finish(tree, shader, location):
    """Connect the group's shader to its output."""
    out = tree.nodes.new("NodeGroupOutput")
    out.location = location
    tree.links.new(shader, out.inputs[0])


def group_tree(mat):
    node = mat.node_tree.nodes.get(NODE) if mat is not None and mat.node_tree else None
    return node.node_tree if node is not None and node.bl_idname == "ShaderNodeGroup" else None


def find(mat, name):
    """A node of the material or its paint group, by name."""
    node = mat.node_tree.nodes.get(name)
    if node is None:
        tree = group_tree(mat)
        node = tree.nodes.get(name) if tree is not None else None
    return node


def copy_material(mat):
    """Copy of a weapon material with its own paint group."""
    new = mat.copy()
    own_group(new)
    return new


def own_group(mat):
    """Give ``mat`` its own copy of the paint group if it shares it."""
    node = mat.node_tree.nodes.get(NODE) if mat.node_tree else None
    if node is not None and node.node_tree is not None and node.node_tree.users > 1:
        tree = node.node_tree.copy()
        tree.name = f"{mat.name} (cs.weapon)"
        node.node_tree = tree
