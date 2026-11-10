# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""GLSL subset parser and interpreter (nodes/glsl.py) on hand-written
snippets; runs inside Blender."""

import unittest

import bpy

from cs_weapon.nodes import glsl
from cs_weapon.nodes.expr import Graph

SRC = """// input 1 _uv
// output 0 _out
const float _tab[3] = float[](1.0, 2.0, 4.0);
void main()
{
    float _a;
    SPIRV_CROSS_BRANCH
    switch (nMode)
    {
        case 0:
        {
            _a = 10.0;
            break;
        }
        case 1:
        case 2:
        {
            _a = _tab[2] + 1.0;
            break;
        }
        default:
        {
            _a = -1.0;
            break;
        }
    }
    float _b = 0.0;
    for (int _i = 0; _i < 3; _i++)
    {
        _b += _tab[_i];
    }
    _out = vec4(_a, _b, _uv.x > 2.0 ? 1.0 : 0.0, 1.0);
}
"""


class GLSLTests(unittest.TestCase):
    def run_src(self, mode):
        mat = bpy.data.materials.new("glsl_test")
        mat.use_nodes = True
        g = Graph(mat.node_tree)
        consts, main = glsl.parse_main(SRC)
        meta = glsl.header(SRC)
        uv = [g.wrap(0.5), g.wrap(0.5)]
        env = glsl.Machine(g, {"nMode": float(mode)}, {"_uv": uv}, None).run(consts, main)
        out = env[meta["outputs"][0]]
        self.assertTrue(all(v.is_const for v in out))
        return [v.const for v in out]

    def test_switch_and_loop_fold_to_constants(self):
        self.assertEqual(self.run_src(0), [10.0, 7.0, 0.0, 1.0])
        self.assertEqual(self.run_src(2), [5.0, 7.0, 0.0, 1.0])
        self.assertEqual(self.run_src(7), [-1.0, 7.0, 0.0, 1.0])

    def test_fall_through_rejected(self):
        bad = "void main()\n{\n switch (n)\n {\n case 0:\n { _a = 1.0; }\n case 1:\n { _a = 2.0; break; }\n }\n}\n"
        with self.assertRaises(glsl.GLSLError):
            glsl.parse_main(bad)

    def test_header(self):
        meta = glsl.header("// texture g_tColor srgb=1\n// input 3 _ov\n// combos S_PAINT_STYLE=5\nvoid main(){}")
        self.assertEqual(meta["textures"], {"g_tColor": True})
        self.assertEqual(meta["inputs"], {3: "_ov"})
        self.assertEqual(meta["combos"], {"S_PAINT_STYLE": 5})


SRC2 = """// input 0 _uv
// output 0 _out
void main()
{
    float _hist[3] = float[](0.0, 0.0, 0.0);
    for (int _i = 0; _i < 3; _i++)
    {
        if (float(_i) == floor(_uv.x * 3.0))
        {
            _hist[_i] += 1.0;
        }
    }
    vec3 _c;
    do
    {
        if (_uv.y > 0.5)
        {
            _c = vec3(1.0);
            break;
        }
        else
        {
            _c = vec3(0.0);
            break;
        }
        break; // unreachable workaround
    } while(false);
    float _d = dFdx(_uv.x);
    _out = vec4(_hist[1] + _d, _c.x, 0.0, 1.0);
}
"""


class GLSLPerPixelTests(unittest.TestCase):
    def test_array_element_and_breaking_branches(self):
        mat = bpy.data.materials.new("glsl_test2")
        mat.use_nodes = True
        mat.node_tree.nodes.clear()
        g = Graph(mat.node_tree)
        u, v = g.value("u"), g.value("v")
        consts, main = glsl.parse_main(SRC2)
        env = glsl.Machine(g, {}, {"_uv": [u, v]}, None).run(consts, main)
        out = env["_out"]
        self.assertFalse(out[0].is_const)  # histogram bin from a per-pixel compare
        self.assertFalse(out[1].is_const)  # branch merged with mix
        self.assertEqual(out[2].const, 0.0)


SRC_MAT = """// output 0 _out
void main()
{
    mat4 _m = mat4((g_mA[0] * _w) + (g_mB[0] * _k), (g_mA[1] * _w) + (g_mB[1] * _k),
                   (g_mA[2] * _w) + (g_mB[2] * _k), (g_mA[3] * _w) + (g_mB[3] * _k));
    vec4 _c = vec4(0.5, 0.25, 1.0, 1.0);
    _out = vec4((_c * _m).xyz, (_m * _c).x);
}
"""

# columns: out.r = 2 r + 0.1, out.g = g, out.b = b, out.w = w
MAT_A = (2.0, 0.0, 0.0, 0.1, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)
MAT_B = tuple(1.0 if i % 5 == 0 else 0.0 for i in range(16))  # identity


SRC_ARR = """// output 0 _out
void main()
{
    vec3 _p[3] = vec3[](vec3(1.0, 0.0, 0.0), vec3(0.0, 2.0, 0.0), vec3(0.0, 0.0, 3.0));
    int _t[2][3];
    for (int _i = 0; _i < 3; _i++)
    {
        _t[1][_i] = int(_i == 2);
    }
    _out = vec4(_p[1].y, _p[int(2.7)].z, float(_t[1][2]), float(_t[0][2] + _t[1][0]));
}
"""


class GLSLArrayTests(unittest.TestCase):
    def test_local_array_initializer_and_2d_array(self):
        mat = bpy.data.materials.new("glsl_arr")
        mat.use_nodes = True
        g = Graph(mat.node_tree)
        consts, main = glsl.parse_main(SRC_ARR)
        env = glsl.Machine(g, {}, {}, None).run(consts, main)
        self.assertEqual([v.const for v in env["_out"]], [2.0, 3.0, 1.0, 0.0])


SRC_SEQ = """// input 0 _uv
// output 0 _out
void main()
{
    float _a;
    float _b = 1.0;
    do
    {
        if (_uv.x > 0.5)
        {
            _a = 2.0;
            break;
        }
        if (nOn != 0)
        {
            _b = 3.0;
        }
        _a = 5.0;
    } while(false);
    _out = vec4(_a, _b, 0.0, 1.0);
}
"""


class GLSLDoWhileFalseTests(unittest.TestCase):
    def run_at(self, x):
        mat = bpy.data.materials.new("glsl_seq")
        mat.use_nodes = True
        g = Graph(mat.node_tree)
        consts, main = glsl.parse_main(SRC_SEQ)
        uv = [g.wrap(x), g.wrap(0.0)] if x is not None else [g.value("u"), g.value("v")]
        return glsl.Machine(g, {"nOn": 1.0}, {"_uv": uv}, None).run(consts, main)["_out"]

    def test_early_break_skips_the_rest(self):
        self.assertEqual([v.const for v in self.run_at(0.9)], [2.0, 1.0, 0.0, 1.0])
        self.assertEqual([v.const for v in self.run_at(0.1)], [5.0, 3.0, 0.0, 1.0])

    def test_early_break_on_a_per_pixel_condition(self):
        out = self.run_at(None)
        self.assertFalse(out[0].is_const)  # 2 or 5, merged with mix
        self.assertFalse(out[1].is_const)  # the rest only runs on one side


class GLSLMatrixTests(unittest.TestCase):
    def test_mat4_columns_and_products(self):
        mat = bpy.data.materials.new("glsl_mat")
        mat.use_nodes = True
        g = Graph(mat.node_tree)
        consts, main = glsl.parse_main(SRC_MAT)
        env = glsl.Machine(g, {"g_mA": MAT_A, "g_mB": MAT_B, "_w": 1.0, "_k": 0.0}, {}, None).run(consts, main)
        out = [v.const for v in env["_out"]]
        self.assertAlmostEqual(out[0], 1.1)  # row vector: dot with column 0
        self.assertEqual(out[1:3], [0.25, 1.0])
        self.assertAlmostEqual(out[3], 0.5 * 2.0)  # matrix * vector: first row (2, 0, 0, 0)

    def test_color_matrix_identity_and_tint(self):
        from cs_weapon.nodes import glove
        for k, v in zip(glove.color_matrix((1, 1, 1), 1.0, 1.0, 1.0, (0.3, 0.3, 0.3)), MAT_B):
            self.assertAlmostEqual(k, v, places=6)
        # a grey tint has no saturation: no change, whatever its brightness
        for k, v in zip(glove.color_matrix((0.2, 0.2, 0.2), 1.0, 1.0, 1.0, (0.3, 0.3, 0.3)), MAT_B):
            self.assertAlmostEqual(k, v, places=6)

        def apply(m, c):
            return [sum(c[i] * m[4 * j + i] for i in range(4)) for j in range(4)]
        out = apply(glove.color_matrix((1, 1, 1), 1.0, 0.0, 2.0, (0.3, 0.3, 0.3)), (0.2, 0.4, 0.6, 1.0))
        self.assertAlmostEqual(out[0], out[1], places=6)  # desaturated
        self.assertAlmostEqual(out[1], out[2], places=6)
        self.assertAlmostEqual(out[3], 1.0)
        # a dark green tint turns grey green, it does not darken it to black
        out = apply(glove.color_matrix((0.035, 0.075, 0.035), 1.0, 1.0, 1.0, (0.3, 0.3, 0.3)), (0.3, 0.3, 0.3, 1.0))
        self.assertGreater(out[1], out[0])
        self.assertGreater(out[0], 0.15)


class GloveUniformTests(unittest.TestCase):
    def test_vector_trimmed_to_shader_size(self):
        from cs_weapon.nodes import glove
        u = glove.uniforms({"g_vA": [0.1, 0.2, 0.0, 0.0], "g_vB": [1.0, 2.0, 3.0]},
                           {"g_vA": [0.0, 0.0], "g_vB": 0.0})
        self.assertEqual(u["g_vA"], (0.1, 0.2))
        self.assertEqual(u["g_vB"], (1.0, 2.0, 3.0))  # scalar default: keep

    def test_tint_id_colors_linear(self):
        from cs_weapon.nodes import glove
        u = glove.uniforms({"g_vId1Color": [0.5, 0.0, 1.0, 0.25], "g_vColorTint1": [0.5, 0.5, 0.5]})
        self.assertAlmostEqual(u["g_vId1Color"][0], 0.214, places=3)
        self.assertEqual(u["g_vId1Color"][1:], (0.0, 1.0, 0.25))  # alpha untouched
        self.assertEqual(u["g_vColorTint1"], (0.5, 0.5, 0.5))  # converted by the shader

    def test_vs_values(self):
        from cs_weapon.nodes import glove
        v = glove.vs_values({"g_vPatternTexCoordOffset": [0.25, 0.5], "g_vUvOffset3": [0.84, 0.65]})
        self.assertEqual(v["g_vPatternTexCoordXform0"], (1.0, -0.0, 0.0, 0.25))
        self.assertEqual(v["g_vPatternTexCoordXform1"], (0.0, 1.0, 0.0, 0.5))
        self.assertEqual(v["g_vUvOffset3"], (0.84, 0.65, 0.0, 0.0))
        r = glove.vs_values({"g_fPatternTexCoordRotation": 90.0})["g_vPatternTexCoordXform0"]
        # rotation about the centre (0.5, 0.5): (0.5, 0.5) stays put
        self.assertAlmostEqual(r[0] * 0.5 + r[1] * 0.5 + r[3], 0.5)


class GraphTests(unittest.TestCase):
    def test_nodes_only_for_fed_values_and_shared(self):
        mat = bpy.data.materials.new("graph_test")
        mat.use_nodes = True
        nt = mat.node_tree
        nt.nodes.clear()
        g = Graph(nt)
        x = g.value("x")
        a = g.sqrt(x * 2.0 + 1.0)
        b = g.sqrt(x * 2.0 + 1.0)  # same expression
        g.sqrt(x * 5.0)  # never used
        self.assertEqual(a.key, b.key)
        self.assertEqual(len(nt.nodes), 1)  # only the Value node so far
        out = nt.nodes.new("ShaderNodeMath")
        g.feed(out.inputs[0], a)
        g.feed(out.inputs[1], b)
        self.assertEqual(len(nt.nodes), 5)  # value, multiply, add, sqrt, out
        sx, sy, _sz = g.sep(g.vec(x, a, 0.0))
        self.assertEqual(sx.key, x.key)  # sep(vec(..)) folds


def _eval(sock, tree, outer=None):
    """Value at an output socket of Value/Math nodes, through group sockets
    (``outer``: the group node and its tree, for the Group Input)."""
    node = sock.node
    if node.bl_idname == "ShaderNodeValue":
        return sock.default_value
    if node.bl_idname == "NodeGroupInput":
        gnode, gtree = outer[-1]
        return _input(gnode.inputs[list(node.outputs).index(sock)], gtree, outer[:-1])
    if node.bl_idname == "ShaderNodeGroup":
        inner = node.node_tree
        gout = next(n for n in inner.nodes if n.bl_idname == "NodeGroupOutput")
        return _input(gout.inputs[list(node.outputs).index(sock)], inner, (outer or []) + [(node, tree)])
    a, b = (_input(node.inputs[i], tree, outer) for i in (0, 1))
    return {"ADD": a + b, "MULTIPLY": a * b, "SQRT": a ** 0.5}[node.operation]


def _input(sock, tree, outer):
    links = [lk for lk in tree.links if lk.to_socket == sock]
    return _eval(links[0].from_socket, tree, outer) if links else sock.default_value


class ChunkedGraphTests(unittest.TestCase):
    def _build(self, chunk):
        tree = bpy.data.node_groups.new("chunk_test", "ShaderNodeTree")
        g = Graph(tree, defer=True)
        g.CHUNK = chunk
        x = g.value("x", 0.5)
        v = x
        for i in range(30):
            v = g.sqrt(v * float(i + 2) + x)
        out = g.node("ShaderNodeMath", 99)
        out.operation = "ADD"
        g.feed(out.inputs[0], v)
        g.feed(out.inputs[1], v * 3.0)
        g.flush()
        return tree, out

    def test_chunks_compute_like_one_tree(self):
        flat, fout = self._build(1000)
        parts, pout = self._build(8)
        self.assertFalse(any(n.bl_idname == "ShaderNodeGroup" for n in flat.nodes))
        groups = [n for n in parts.nodes if n.bl_idname == "ShaderNodeGroup"]
        self.assertGreater(len(groups), 5)
        self.assertLess(len(parts.nodes), 20)
        self.assertTrue(all(len(n.node_tree.nodes) <= 8 + 2 for n in groups))
        want = _eval(fout.outputs[0], flat)
        self.assertAlmostEqual(_eval(pout.outputs[0], parts), want, places=5)

    def test_rebuild_removes_part_groups(self):
        from cs_weapon.nodes import group
        mat = bpy.data.materials.new("chunk_mat")
        mat.use_nodes = True
        before = set(bpy.data.node_groups)
        for _round in range(2):
            tree = group.tree_for(mat, "Chunk Test")
            g = Graph(tree, defer=True)
            g.CHUNK = 4
            x = g.value("x", 0.5)
            v = x
            for i in range(12):
                v = g.sqrt(v * float(i + 2) + x)
            out = g.node("ShaderNodeMath", 99)
            g.feed(out.inputs[0], v)
            g.flush()
            group.finish(tree, g.node("ShaderNodeEmission", 100).outputs[0], (0.0, 0.0))
        new = set(bpy.data.node_groups) - before
        self.assertEqual(len([t for t in new if not t.name.startswith(".")]), 1)
        self.assertTrue(all(t.users > 0 for t in new))


if __name__ == "__main__":
    unittest.main()
