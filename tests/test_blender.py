# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Blender tests: registration, library, spawning the demo, live seed."""

import os
import unittest

import bpy
from mathutils import Vector

from cs_weapon import utils
from cs_weapon.library import registry
from cs_weapon.nodes import material
from cs_weapon.ops import duplicates, modules


class StickerLayerTests(unittest.TestCase):
    def test_preset_rotation_adds_to_item_rotation(self):
        from types import SimpleNamespace
        from cs_weapon.ops import sticker_ops
        item = SimpleNamespace(offset_x=0.1, offset_y=0.0, size=1.0, rotation=90.0, wear=0.0)
        v = sticker_ops._values(item, (0.2, -0.3, 6.4, 0.05))
        self.assertAlmostEqual(v["rotation"], 0.3)
        self.assertAlmostEqual(v["offset"][0], 0.3)
        self.assertEqual(sticker_ops._stored([0.2, -0.3, 6.4]), (0.2, -0.3, 6.4, 0.0))  # before 0.7.6
        self.assertIsNone(sticker_ops._stored([1.0]))

    def test_shared_slots_get_own_layers(self):
        from cs_weapon.ops import weapon_ops
        st = [{"slot": 2}, {"slot": 1}, {"slot": 2}, {"slot": 4}, {"slot": 2}, {"slot": 0}]
        # shared slots: layers in link order (draw order), a sixth has none
        self.assertEqual(weapon_ops.sticker_layers(st), [0, 1, 2, 3, 4, None])
        # the test link of #22: the second sticker on slot 2 draws on top
        self.assertEqual(weapon_ops.sticker_layers([{"slot": 2}, {"slot": 1}, {"slot": 2}, {"slot": 4}]),
                         [0, 1, 2, 3])

    def test_slots_in_order_keep_their_slot(self):
        from cs_weapon.ops import weapon_ops
        self.assertEqual(weapon_ops.sticker_layers([{"slot": 0}, {"slot": 2}, {"slot": 3}]), [0, 2, 3])
        # listed out of slot order: link order wins
        self.assertEqual(weapon_ops.sticker_layers([{"slot": 3}, {"slot": 1}]), [0, 1])


class GlitterTests(unittest.TestCase):
    def _graph(self):
        from cs_weapon.nodes.expr import Graph
        mat = bpy.data.materials.new("glitter test")
        mat.use_nodes = True
        self.addCleanup(bpy.data.materials.remove, mat)
        return mat.node_tree, Graph(mat.node_tree)

    def test_flake_normal_decode(self):
        from cs_weapon.nodes import paint
        _nt, g = self._graph()
        flat = paint._hemi_oct(g, 128 / 255, 128 / 255).const
        for got, want in zip(flat, (0.0, 0.0, 1.0)):
            self.assertAlmostEqual(got, want, places=3)
        tilted = paint._hemi_oct(g, 0.75, 0.5).const  # +x and +y lean
        self.assertGreater(tilted[0], 0.2)
        self.assertGreater(tilted[1], 0.2)

    def test_glitter_nodes(self):
        import os
        import tempfile
        from cs_weapon.nodes import paint
        nt, g = self._graph()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "flakes.png")
            img = bpy.data.images.new("flakes", 4, 4, alpha=True)
            img.filepath_raw = path
            img.file_format = "PNG"
            img.save()
            bpy.data.images.remove(img)
            mask = g.value("mask", 1.0)
            rgb = [g.value(c, 0.5) for c in "rgb"]
            final = {"F_GLITTER": 1, "g_fGlitterIntensity": 0.8, "g_fGlitterScale": 25.0,
                     "g_fGlitterRainbowSpread": 1.0, "g_fGlitterRainbowBalance": -0.5,
                     "g_flUvScale1": 1.0, "g_tGlitterNormal": path}
            self.assertIsNone(paint.glitter(g, {**final, "g_fGlitterIntensity": 0.0}, rgb, 0.5, 0.0, mask))
            self.assertIsNone(paint.glitter(g, {**final, "F_GLITTER": 0}, rgb, 0.5, 0.0, mask))
            self.assertIsNone(paint.glitter(g, {**final, "g_tGlitterNormal": ""}, rgb, 0.5, 0.0, mask))
            out = paint.glitter(g, final, rgb, 0.5, 0.0, mask)
            self.assertIsNotNone(out)
            albedo, rough, metal, normal, added = out
            self.assertEqual(len(albedo), 3)
            sink = nt.nodes.new("ShaderNodeBsdfPrincipled")
            g.feed(sink.inputs["Base Color"], g.vec(*albedo))
            g.feed(sink.inputs["Roughness"], rough)
            g.feed(sink.inputs["Metallic"], metal)
            g.feed(sink.inputs["Normal"], normal)
            g.feed(sink.inputs["Emission Color"], added)
            g.flush()
            texs = [n for n in nt.nodes if n.bl_idname == "ShaderNodeTexImage"]
            self.assertEqual(len(texs), 1)
            self.assertEqual(texs[0].interpolation, "Closest")
            self.assertTrue(any(n.bl_idname == "ShaderNodeVectorMath" and n.operation == "REFLECT"
                                for n in nt.nodes))
            for name in ("Base Color", "Roughness", "Metallic", "Normal", "Emission Color"):
                self.assertTrue(sink.inputs[name].is_linked, name)


class PatternAddressTests(unittest.TestCase):
    def test_per_axis_address(self):
        from cs_weapon.nodes import paint
        from cs_weapon.nodes.expr import Graph
        mat = bpy.data.materials.new("address test")
        mat.use_nodes = True
        self.addCleanup(bpy.data.materials.remove, mat)
        g = Graph(mat.node_tree)
        same = g.wrap((0.2, 0.3, 0.0))
        self.assertEqual(paint.pattern_address(g, same, "EXTEND", "EXTEND"), ("EXTEND", same))
        # Blaze: clamp horizontally, repeat vertically; the clamp keeps a
        # margin so REPEAT filtering (also on coarse mips) never reaches the far edge
        ext, uv = paint.pattern_address(g, g.wrap((1.5, 1.3, 0.0)), "EXTEND", "REPEAT", size=(1024, 512))
        self.assertEqual(ext, "REPEAT")
        x, y, _z = uv.const
        self.assertAlmostEqual(x, 1.0 - paint.EDGE_TEXELS / 1024, places=6)
        self.assertAlmostEqual(y, 1.3, places=6)
        _ext, uv = paint.pattern_address(g, g.wrap((-0.4, 0.5, 0.0)), "EXTEND", "REPEAT", size=(1024, 512))
        self.assertAlmostEqual(uv.const[0], paint.EDGE_TEXELS / 1024, places=6)
        _ext, uv = paint.pattern_address(g, g.wrap((0.2, 1.25, 0.0)), "REPEAT", "MIRROR", size=(1024, 512))
        self.assertAlmostEqual(uv.const[1], 0.75, places=6)  # mirrored second tile


class BlenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        registry.refresh(bpy.context)

    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        registry.refresh(bpy.context)

    def test_library_list(self):
        state = bpy.context.window_manager.cs_weapon
        self.assertEqual(len(state.items), 3)  # two skins + vanilla
        state.search = "fade"
        self.assertEqual(len(state.items), 1)
        state.search = ""
        state.filter_weapon = "demo_rifle"
        self.assertEqual(len(state.items), 3)
        state.filter_type = "rifle"
        self.assertEqual(state.filter_weapon, "ALL", "type change resets the weapon filter")

    def _spawn(self, sid):
        res = bpy.ops.cs_weapon.spawn(skin_id=sid)
        self.assertEqual(res, {"FINISHED"})
        return bpy.context.active_object

    def test_spawn_both_demo_skins(self):
        a = self._spawn("demo_rifle:90001")
        b = self._spawn("demo_rifle:90002")
        self.assertTrue(utils.is_weapon(a) and utils.is_weapon(b))
        self.assertIs(a.data, b.data, "template mesh is shared")
        self.assertIsNot(a.material_slots[0].material, b.material_slots[0].material)
        coll = utils.weapon_collection(bpy.context)
        self.assertIn(a, list(coll.objects))
        self.assertGreater(len(a.data.polygons), 20)

    def test_spawn_vanilla(self):
        obj = self._spawn("demo_rifle:0")
        mat = obj.material_slots[0].material
        self.assertEqual(material.spec_of(mat)["kind"], "vanilla")
        self.assertTrue(any(n.type == "TEX_IMAGE" and n.image for n in mat.node_tree.nodes))
        obj.cs_weapon.seed = 12  # no live values, must not fail
        self.assertTrue(material.apply_seed(mat, 12))

    def test_second_spawn_copies_cached_material(self):
        a = self._spawn("demo_rifle:90001")
        b = self._spawn("demo_rifle:90001")
        ma, mb = a.material_slots[0].material, b.material_slots[0].material
        self.assertIsNot(ma, mb)
        self.assertEqual(len(ma.node_tree.nodes), len(mb.node_tree.nodes))
        self.assertEqual(material.spec_of(mb)["skin"], "demo_rifle:90001")

    def test_duplicate_gets_own_material(self):
        a = self._spawn("demo_rifle:90001")
        b = a.copy()  # what Shift+D does with an object-linked slot
        utils.weapon_collection(bpy.context).objects.link(b)
        self.assertIs(a.material_slots[0].material, b.material_slots[0].material)
        self.assertEqual(duplicates.make_unique(), [b])
        ma, mb = a.material_slots[0].material, b.material_slots[0].material
        self.assertIsNot(ma, mb)
        before = ma.node_tree.nodes["cs_x0_t"].outputs[0].default_value
        b.cs_weapon.seed = 661
        self.assertEqual(ma.node_tree.nodes["cs_x0_t"].outputs[0].default_value, before)
        self.assertEqual(duplicates.make_unique(), [])

    def test_apply_skin_keeps_object_and_pattern(self):
        obj = self._spawn("demo_rifle:90001")
        obj.cs_weapon.seed = 321
        obj.location = (1.0, 2.0, 3.0)
        old = obj.material_slots[0].material
        name = old.name
        res = bpy.ops.cs_weapon.apply_skin(skin_id="demo_rifle:90002")
        self.assertEqual(res, {"FINISHED"})
        self.assertEqual(obj[utils.PROP_SKIN], "demo_rifle:90002")
        self.assertEqual(obj.cs_weapon.seed, 321)
        self.assertEqual(tuple(obj.location), (1.0, 2.0, 3.0))
        self.assertEqual(material.spec_of(obj.material_slots[0].material)["style"], 6)
        self.assertNotIn(name, bpy.data.materials, "unused weapon material is removed")
        self.assertTrue(obj.name.startswith("Demo Rifle |"))
        bpy.ops.cs_weapon.apply_skin(skin_id="demo_rifle:0")
        self.assertEqual(material.spec_of(obj.material_slots[0].material)["kind"], "vanilla")
        self.assertTrue(obj.name.startswith("Demo Rifle (Vanilla)"))

    def test_attachment_axes(self):
        class FakeWeapon:
            def attachment_matrix(self, name, legacy=False):
                # Source: 10 inches forward, 2 left, 1 up, yaw 90 degrees
                return [0, -1, 0, 10, 1, 0, 0, 2, 0, 0, 1, 1, 0, 0, 0, 1]

        m = modules.attachment_local(FakeWeapon(), "stattrak")
        t = m.translation
        # forward -> -Y, left -> +X, up -> +Z, inches -> metres
        self.assertAlmostEqual(t.x, 2 * 0.0254)
        self.assertAlmostEqual(t.y, -10 * 0.0254)
        self.assertAlmostEqual(t.z, 1 * 0.0254)
        # the module's forward (Source x = Blender -Y) turns with yaw 90 to
        # Source +y = Blender +X
        fwd = m.to_3x3() @ Vector((0.0, -1.0, 0.0))
        self.assertAlmostEqual(fwd.x, 1.0)
        self.assertAlmostEqual(fwd.y, 0.0)

    def test_modules_without_library_are_a_no_op(self):
        obj = self._spawn("demo_rifle:90001")
        obj.cs_weapon.stattrak = True
        obj.cs_weapon.stattrak_count = 42
        obj.cs_weapon.nametag = "test"
        self.assertEqual(len(obj.children), 0, "demo library has no modules")
        self.assertEqual(modules.available(), set())

    def test_import_inspect_link(self):
        from cs_weapon.library import inspect_link
        link = inspect_link.link(defindex=0, paintindex=90001, paintseed=661, paintwear=0.2,
                                 stattrak=7, nametag="demo")
        res = bpy.ops.cs_weapon.import_inspect(link=link)
        self.assertEqual(res, {"FINISHED"})
        obj = bpy.context.active_object
        self.assertEqual(obj[utils.PROP_SKIN], "demo_rifle:90001")
        self.assertEqual(obj.cs_weapon.seed, 661)
        self.assertAlmostEqual(obj.cs_weapon.wear, 0.2, places=5)
        self.assertTrue(obj.cs_weapon.stattrak)
        self.assertEqual(obj.cs_weapon.stattrak_count, 7)
        self.assertEqual(obj.cs_weapon.nametag, "demo")
        with self.assertRaises(RuntimeError):  # reported as error: not in the library
            bpy.ops.cs_weapon.import_inspect(link=inspect_link.link(defindex=0, paintindex=12345))

    def _sticker_library(self, d):
        import json
        import os
        for name, rgba in (("red", [1.0, 0.0, 0.0, 1.0]), ("blue", [0.0, 0.0, 1.0, 1.0])):
            img = bpy.data.images.new("stk", 8, 8, alpha=True)
            img.pixels = rgba * 64
            img.filepath_raw = os.path.join(d, f"{name}.png")
            img.file_format = "PNG"
            img.save()
        with open(os.path.join(d, "catalog.json"), "w", encoding="utf-8") as fh:
            json.dump({"stickers": [
                {"id": "cs2_77_red", "name": "Red", "finish": "PAPER", "textures": {"base": "red.png"}},
                {"id": "cs2_78_blue", "name": "Blue", "finish": "PAPER", "textures": {"base": "blue.png"}},
                {"id": "cs2_79_holo", "name": "Holo", "finish": "HOLO", "textures": {"base": "blue.png"}},
            ]}, fh)
        from cs_weapon.library import sticker_registry
        sticker_registry._library.load(d)
        sticker_registry._loaded = True
        return sticker_registry

    def test_stickers_in_material(self):
        import tempfile
        from cs_weapon.nodes import sticker_layer
        from cs_weapon.ops import sticker_ops
        with tempfile.TemporaryDirectory() as d:
            reg = self._sticker_library(d)
            obj = self._spawn("demo_rifle:90001")
            self.assertEqual(sorted(sticker_ops.slot_presets(obj)), [0, 1])
            state = bpy.context.window_manager.cs_weapon
            state.sticker_id = "cs2_77_red"
            self.assertEqual(bpy.ops.cs_weapon.put_sticker(slot=1), {"FINISHED"})
            self.assertEqual(bpy.ops.cs_weapon.put_sticker(slot=3), {"CANCELLED"})  # no such slot
            mat = obj.material_slots[0].material
            nodes = mat.node_tree.nodes
            mix = nodes[sticker_layer.node_name(1, "MIX")]
            out = next(n for n in nodes if n.type == "OUTPUT_MATERIAL")
            self.assertEqual(out.inputs["Surface"].links[0].from_node.name, mix.name)
            self.assertAlmostEqual(nodes[sticker_layer.node_name(1, "SCALE")].outputs[0].default_value, 4.0)
            item = sticker_ops.entry_for(obj, 1)
            item.size = 2.0
            item.rotation = 90.0
            item.offset_x = 0.05
            self.assertAlmostEqual(nodes[sticker_layer.node_name(1, "SCALE")].outputs[0].default_value, 2.0)
            self.assertAlmostEqual(nodes[sticker_layer.node_name(1, "ROT")].outputs[0].default_value, 0.25)
            self.assertAlmostEqual(nodes[sticker_layer.node_name(1, "OX")].outputs[0].default_value, 0.25)
            # a second weapon copies the cached material, without the first one's stickers
            other = self._spawn("demo_rifle:90001")
            self.assertFalse(sticker_layer.has_layer(other.material_slots[0].material))
            # reskin keeps the stickers
            bpy.context.view_layer.objects.active = obj
            obj.select_set(True)
            other.select_set(False)
            bpy.context.window_manager.cs_weapon.index = 1
            self.assertTrue(sticker_layer.has_layer(obj.material_slots[0].material))
            from cs_weapon.ops import spawn
            spawn.reskin(obj, registry.get_catalog().get_skin("demo_rifle:90002"))
            self.assertTrue(sticker_layer.has_layer(obj.material_slots[0].material))
            self.assertEqual(bpy.ops.cs_weapon.remove_sticker(slot=1), {"FINISHED"})
            mat = obj.material_slots[0].material
            self.assertFalse(sticker_layer.has_layer(mat))
            out = next(n for n in mat.node_tree.nodes if n.type == "OUTPUT_MATERIAL")
            self.assertTrue(out.inputs["Surface"].is_linked)
            reg.invalidate()

    def test_same_structure_updates_in_place(self):
        import tempfile
        from cs_weapon.nodes import sticker_layer
        from cs_weapon.ops import sticker_ops
        with tempfile.TemporaryDirectory() as d:
            reg = self._sticker_library(d)
            obj = self._spawn("demo_rifle:90001")
            mat = obj.material_slots[0].material
            nodes = mat.node_tree.nodes

            def group_ptr():
                return nodes[sticker_layer.node_name(0, "STICKER")].as_pointer()

            sticker_ops.put(obj, 0, reg.get_sticker("cs2_77_red"))
            sticker_ops.sync(obj)
            before = group_ptr()
            # another paper sticker: no new nodes (EEVEE would recompile), new image
            sticker_ops.put(obj, 0, reg.get_sticker("cs2_78_blue"))
            sticker_ops.sync(obj)
            self.assertEqual(group_ptr(), before)
            tex = nodes[sticker_layer.node_name(0, "TEX_BASE")]
            self.assertTrue(tex.image.filepath.endswith("blue.png"))
            self.assertIn("Blue", nodes[sticker_layer.node_name(0, "STICKER")].label)
            # another finish needs other nodes
            sticker_ops.put(obj, 0, reg.get_sticker("cs2_79_holo"))
            sticker_ops.sync(obj)
            self.assertIn("Holo", nodes[sticker_layer.node_name(0, "STICKER")].label)
            sticker_ops.remove(obj, 0)
            self.assertNotIn(sticker_layer.PROP_LAYOUT, mat)
            reg.invalidate()

    def test_move_sticker_keeps_position(self):
        import tempfile
        from cs_weapon.ops import sticker_ops
        with tempfile.TemporaryDirectory() as d:
            reg = self._sticker_library(d)
            obj = self._spawn("demo_rifle:90001")
            sticker_ops.put(obj, 0, reg.get_sticker("cs2_77_red"))
            sticker_ops.put(obj, 1, reg.get_sticker("cs2_77_red"), anchor=0)
            sticker_ops.entry_for(obj, 0).name = "bottom"
            sticker_ops.entry_for(obj, 1).name = "top"
            sticker_ops.sync(obj)
            self.assertEqual(bpy.ops.cs_weapon.move_sticker(slot=0, step=1), {"FINISHED"})
            self.assertEqual(sticker_ops.entry_for(obj, 0).name, "top")
            self.assertEqual(sticker_ops.entry_for(obj, 1).name, "bottom")
            self.assertEqual([sticker_ops.anchor_of(sticker_ops.entry_for(obj, i)) for i in (0, 1)], [0, 0])
            self.assertEqual(sticker_ops.entry_for(obj, 1).anchor, 0)
            self.assertEqual(bpy.ops.cs_weapon.move_sticker(slot=0, step=-1), {"CANCELLED"})
            # into an empty layer: the sticker moves, its position stays
            self.assertEqual(bpy.ops.cs_weapon.move_sticker(slot=1, step=1), {"FINISHED"})
            self.assertIsNone(sticker_ops.entry_for(obj, 1))
            self.assertEqual(sticker_ops.anchor_of(sticker_ops.entry_for(obj, 2)), 0)
            reg.invalidate()

    def test_sticker_mesh_follows_uv1(self):
        from cs_weapon.library import stickers as ST
        from cs_weapon.ops import sticker_mesh
        bpy.ops.mesh.primitive_grid_add(x_subdivisions=8, y_subdivisions=8, size=1.0)
        target = bpy.context.active_object
        target.data.uv_layers[0].name = "UVMap.001"  # grid UV covers 0..1
        sticker = ST.Sticker(id="s", name="S")
        entry = {"slot": 0, "sticker": sticker, "offset": (0.1, 0.0), "scale": 2.0, "rotation": 0.0}
        mesh = sticker_mesh.build_mesh(target.data, "UVMap.001", entry, grid=8)
        xs = [v.co.x for v in mesh.vertices]
        ys = [v.co.y for v in mesh.vertices]
        # sticker spans 1/scale = half the UV range, centred at u = 0.5 + 0.1
        self.assertAlmostEqual(min(xs), -0.15, places=4)
        self.assertAlmostEqual(max(xs), 0.35, places=4)
        self.assertAlmostEqual(max(ys) - min(ys), 0.5, places=4)
        self.assertTrue(all(abs(v.co.z - sticker_mesh.LIFT) < 1e-6 for v in mesh.vertices))
        self.assertEqual(len(mesh.polygons), 64)
        # the mesh's own UV has v up like the image: top edge of the sticker at the top
        layer = mesh.uv_layers["UVMap"].data
        top = max(mesh.vertices, key=lambda v: v.co.y).index
        top_v = [layer[i].uv.y for i, loop in enumerate(mesh.loops) if loop.vertex_index == top]
        self.assertAlmostEqual(max(top_v), 1.0)
        # quarter turn: still the same square area
        entry["rotation"] = 0.25
        mesh = sticker_mesh.build_mesh(target.data, "UVMap.001", entry, grid=8)
        xs = [v.co.x for v in mesh.vertices]
        self.assertAlmostEqual(max(xs) - min(xs), 0.5, places=4)
        self.assertIsNone(sticker_mesh.build_mesh(target.data, "missing", entry))

    def test_sticker_mode_switch(self):
        import tempfile
        from cs_weapon.nodes import sticker_layer
        from cs_weapon.ops import sticker_mesh, sticker_ops
        with tempfile.TemporaryDirectory() as d:
            reg = self._sticker_library(d)
            obj = self._spawn("demo_rifle:90001")
            obj.data.uv_layers.new(name="UVMap.001")
            sticker_ops.put(obj, 0, reg.get_sticker("cs2_77_red"))
            mat = obj.material_slots[0].material
            self.assertTrue(sticker_layer.has_layer(mat))
            obj.cs_weapon.sticker_mode = "MESH"
            self.assertFalse(sticker_layer.has_layer(mat))
            kids = sticker_mesh.children(obj)
            self.assertEqual(len(kids), 1)
            self.assertTrue(kids[0].active_material.get(utils.PROP_STICKER_MATERIAL))
            sticker_ops.entry_for(obj, 0).wear = 0.4  # rebuilds the mesh
            self.assertEqual(len(sticker_mesh.children(obj)), 1)
            obj.cs_weapon.sticker_mode = "MATERIAL"
            self.assertEqual(sticker_mesh.children(obj), [])
            self.assertTrue(sticker_layer.has_layer(mat))
            reg.invalidate()

    def test_charm_without_cs_charm_or_attachment(self):
        from cs_weapon.ops import charm_link
        obj = self._spawn("demo_rifle:90001")
        self.assertIsNone(charm_link.keychain_matrix(obj))  # demo has no keychain attachment
        if not charm_link.charm_addon_available():
            with self.assertRaises(RuntimeError):
                bpy.ops.cs_weapon.add_charm()

    def test_sticker_gap_faces_show_only_stickers(self):
        import tempfile
        from cs_weapon.nodes import gap, sticker_layer
        from cs_weapon.ops import bake, spawn, sticker_ops
        with tempfile.TemporaryDirectory() as d:
            reg = self._sticker_library(d)
            obj = self._spawn("demo_rifle:90001")
            # the demo model gets filler faces like the AK's handguard
            me = obj.data
            me.materials.append(bpy.data.materials.new("sticker_gaps.001"))
            me.polygons[0].material_index = 1
            spawn.reskin(obj, registry.get_catalog().get_skin("demo_rifle:90002"))
            mat = obj.material_slots[0].material
            self.assertIs(obj.material_slots[1].material, mat, "filler faces get the weapon material")
            values = [0.0] * len(me.polygons)
            me.attributes[gap.ATTRIBUTE].data.foreach_get("value", values)
            self.assertEqual(values[0], 1.0)
            self.assertEqual(sum(values), 1.0)
            nodes = mat.node_tree.nodes
            out = next(n for n in nodes if n.type == "OUTPUT_MATERIAL")
            self.assertEqual(out.inputs["Surface"].links[0].from_node.name, "GAP_MIX")
            # stickers draw on top of the gap mix
            sticker_ops.put(obj, 0, reg.get_sticker("cs2_77_red"))
            sticker_ops.sync(obj)
            mix = nodes[sticker_layer.node_name(0, "MIX")]
            self.assertEqual(out.inputs["Surface"].links[0].from_node, mix)
            self.assertEqual(mix.inputs[1].links[0].from_node.name, "GAP_MIX")
            sticker_ops.remove(obj, 0)
            self.assertEqual(out.inputs["Surface"].links[0].from_node.name, "GAP_MIX")
            # bake swaps and restores both slots
            images = bake.bake(bpy.context, obj, size=16, channels=("COLOR",))
            baked = bake.baked_material(obj.name, images, me.uv_layers[0].name)
            bake.use_baked(obj, baked)
            self.assertEqual([s.material for s in obj.material_slots], [baked, baked])
            self.assertTrue(gap.has_nodes(baked))
            self.assertTrue(bake.restore_live(obj))
            self.assertEqual([s.material for s in obj.material_slots], [mat, mat])
            reg.invalidate()

    def test_bake_skin(self):
        from cs_weapon.ops import bake
        obj = self._spawn("demo_rifle:90001")
        live = obj.material_slots[0].material
        groups = len(bpy.data.node_groups)
        images = bake.bake(bpy.context, obj, size=32)
        self.assertEqual(sorted(images), ["COLOR", "METALLIC", "NORMAL", "ROUGHNESS"])
        self.assertEqual(len(bpy.data.node_groups), groups, "temporary group copies are removed")
        self.assertIs(obj.material_slots[0].material, live)
        px = list(images["COLOR"].pixels)
        self.assertGreater(max(px[0::4]) - min(px[0::4]), 0.01, "color bake is not flat")
        self.assertEqual(images["COLOR"].colorspace_settings.name, "sRGB")
        mat = bake.baked_material(obj.name, images, obj.data.uv_layers[0].name)
        bake.use_baked(obj, mat)
        self.assertIs(obj.material_slots[0].material, mat)
        self.assertTrue(live.use_fake_user)
        self.assertTrue(bake.restore_live(obj))
        self.assertIs(obj.material_slots[0].material, live)
        self.assertNotIn(bake.PROP_LIVE, obj)

    def test_inspect_link_slot_without_preset(self):
        import tempfile
        from cs_weapon.library import inspect_link
        from cs_weapon.nodes import sticker_layer
        from cs_weapon.ops import sticker_ops
        with tempfile.TemporaryDirectory() as d:
            reg = self._sticker_library(d)
            link = inspect_link.link(defindex=0, paintindex=90001, paintseed=1, stickers=[
                {"slot": 0, "id": 77}, {"slot": 4, "id": 78, "offset_x": -0.2}])
            self.assertEqual(bpy.ops.cs_weapon.import_inspect(link=link), {"FINISHED"})
            obj = bpy.context.active_object
            presets = sticker_ops.slot_presets(obj)
            item = sticker_ops.entry_for(obj, 4)  # slots in order keep their layer
            self.assertEqual(sticker_ops.anchor_of(item), 4)
            # stand-in: the first slot's preset, and the layer is drawn
            self.assertEqual(sticker_ops.preset_for(item, presets), presets[min(presets)])
            nodes = obj.material_slots[0].material.node_tree.nodes
            self.assertIn(sticker_layer.node_name(4, "MIX"), nodes)
            reg.invalidate()

    def test_inspect_link_places_stickers(self):
        import tempfile
        from cs_weapon.library import inspect_link
        from cs_weapon.ops import sticker_ops
        with tempfile.TemporaryDirectory() as d:
            reg = self._sticker_library(d)
            link = inspect_link.link(defindex=0, paintindex=90001, paintseed=1,
                                     stickers=[{"slot": 0, "id": 77, "wear": 0.3, "rotation": 45.0},
                                               {"slot": 1, "id": 12345}])
            self.assertEqual(bpy.ops.cs_weapon.import_inspect(link=link), {"FINISHED"})
            obj = bpy.context.active_object
            item = sticker_ops.entry_for(obj, 0)
            self.assertEqual(item.name, "Red")
            self.assertAlmostEqual(item.wear, 0.3, places=5)
            self.assertAlmostEqual(item.rotation, 45.0, places=4)
            self.assertIsNone(sticker_ops.entry_for(obj, 1))
            reg.invalidate()

    def _rig_glb(self, folder):
        """The demo rifle with a skeleton (weapon, bolt) and a clip moving
        the bolt, written like the exporter's <model>.rig.glb."""
        import os
        weapon = registry.get_catalog().weapons["demo_rifle"]
        bpy.ops.import_scene.gltf(filepath=weapon.model_path())
        body = next(o for o in bpy.context.scene.objects if o.type == "MESH")
        body.name = "demo_rifle.body"
        arm = bpy.data.objects.new("demo_rifle", bpy.data.armatures.new("demo_rifle"))
        bpy.context.scene.collection.objects.link(arm)
        bpy.context.view_layer.objects.active = arm
        bpy.ops.object.mode_set(mode="EDIT")
        root = arm.data.edit_bones.new("weapon")
        root.head, root.tail = (0, 0, 0), (0, 0.1, 0)
        bolt = arm.data.edit_bones.new("bolt")
        bolt.head, bolt.tail, bolt.parent = (0, 0, 0), (0, 0.05, 0), root
        bpy.ops.object.mode_set(mode="OBJECT")
        top = max(v.co.z for v in body.data.vertices)
        groups = {n: body.vertex_groups.new(name=n) for n in ("weapon", "bolt")}
        for v in body.data.vertices:  # the top vertices are the bolt
            groups["bolt" if v.co.z >= top - 1e-6 else "weapon"].add([v.index], 1.0, "REPLACE")
        body.parent = arm
        body.modifiers.new("Armature", "ARMATURE").object = arm
        arm.animation_data_create()
        act = bpy.data.actions.new("reload")
        arm.animation_data.action = act
        pb = arm.pose.bones["bolt"]
        for frame, y in ((1, 0.0), (10, 0.2)):
            pb.location = (0.0, y, 0.0)
            pb.keyframe_insert("location", frame=frame)
        # like the game's clips: the root bone sits off its rest pose
        root = arm.pose.bones["weapon"]
        for frame in (1, 10):
            root.location = (0.0, 0.0, 0.05)
            root.keyframe_insert("location", frame=frame)
        path = os.path.join(folder, "demo_rifle.rig.glb")
        bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", export_skins=True,
                                  export_animations=True, export_animation_mode="ACTIONS")
        bpy.ops.wm.read_homefile(use_empty=True)
        for a in list(bpy.data.actions):
            bpy.data.actions.remove(a)
        return path

    def test_spawn_as_armature(self):
        import tempfile
        from cs_weapon.ops import spawn
        cat = registry.get_catalog()
        weapon = cat.weapons["demo_rifle"]
        with tempfile.TemporaryDirectory() as d:
            weapon.rig = self._rig_glb(d)
            try:
                self.assertTrue(weapon.has_rig())
                state = bpy.context.window_manager.cs_weapon
                state.rigged = True
                obj = self._spawn("demo_rifle:90001")
                arm = spawn.rig_of(obj)
                self.assertIsNotNone(arm, "weapon object under its armature")
                self.assertEqual({b.name for b in arm.data.bones}, {"weapon", "bolt"})
                self.assertEqual(obj.modifiers[0].type, "ARMATURE")
                self.assertIs(obj.modifiers[0].object, arm)
                self.assertIn(utils.PROP_SPEC, obj.material_slots[0].material)
                clips = spawn.rig_actions(weapon)
                self.assertEqual([a.name for a in clips], ["Demo Rifle | reload"])
                self.assertTrue(clips[0].use_fake_user)
                # the clip moves the bolt vertices
                dg = bpy.context.evaluated_depsgraph_get()
                rest = [v.co.copy() for v in obj.evaluated_get(dg).data.vertices]
                arm.animation_data_create().action = clips[0]
                if hasattr(arm.animation_data, "action_slot") and clips[0].slots:
                    arm.animation_data.action_slot = clips[0].slots[0]
                bpy.context.scene.frame_set(10)
                dg = bpy.context.evaluated_depsgraph_get()
                moved = [v.co for v in obj.evaluated_get(dg).data.vertices]
                self.assertGreater(max((a - b).length for a, b in zip(rest, moved)), 0.1)
                shifts = sorted((a - b).length for a, b in zip(rest, moved))
                self.assertAlmostEqual(shifts[0], 0.05, places=4, msg="whole weapon: root bone offset")
                # a rigid child follows the root bone through the constraint
                child = bpy.data.objects.new("probe", None)
                bpy.context.scene.collection.objects.link(child)
                child.parent = obj
                child.location = (0.1, 0.2, 0.3)
                spawn.follow_rig(obj, child)
                bpy.context.view_layer.update()
                pb = arm.pose.bones["weapon"]
                want = arm.matrix_world @ pb.matrix @ pb.bone.matrix_local.inverted()                     @ arm.matrix_world.inverted() @ obj.matrix_world @ child.matrix_basis
                self.assertLess((child.matrix_world.translation - want.translation).length, 1e-5)
                self.assertGreater((child.matrix_world.translation - obj.matrix_world @ child.location).length, 0.04)
                # same place as the plain weapon in rest pose
                arm.animation_data.action = None
                state.rigged = False
                plain = self._spawn("demo_rifle:90001")
                self.assertIsNone(spawn.rig_of(plain))
                self.assertTrue(all(abs(a - b) < 1e-5 for ra, rb in zip(obj.matrix_world, plain.matrix_world)
                                    for a, b in zip(ra, rb)))
                # a new skin keeps the rig; a second rigged spawn shares the armature data
                spawn.reskin(obj, cat.get_skin("demo_rifle:90002"))
                self.assertIs(spawn.rig_of(obj), arm)
                state.rigged = True
                other = self._spawn("demo_rifle:90002")
                self.assertIs(spawn.rig_of(other).data, arm.data)
                self.assertEqual(len(spawn.rig_actions(weapon)), 1, "clips imported once")
            finally:
                weapon.rig = ""
                bpy.context.window_manager.cs_weapon.rigged = False

    def test_api(self):
        import tempfile
        from mathutils import Matrix
        from cs_weapon import api
        from cs_weapon.ops import spawn
        self.assertEqual(api.API_VERSION, 1)
        self.assertTrue(api.library_ready())
        self.assertIn("demo_rifle:90001", [s[0] for s in api.skins("demo_rifle")])
        self.assertEqual(api.skins("demo_rifle", "knife"), [])
        info = api.skin_info("demo_rifle:90001")
        self.assertEqual((info["weapon"], info["glove"], info["rig"], info["rig_path"]), ("demo_rifle", False, False, ""))
        self.assertTrue(info["model_path"].endswith(".glb") and os.path.isfile(info["model_path"]))
        self.assertTrue(info["icon"] == "" or os.path.isfile(info["icon"]))
        self.assertIsNone(api.skin_info("nope:1"))
        self.assertIsNone(api.spawn(bpy.context, "nope:1"))
        seen = []
        api.register_spawn_handler(seen.append)
        api.register_spawn_handler(lambda w: 1 / 0)  # a broken partner does not break spawning
        try:
            plain = api.spawn(bpy.context, "demo_rifle:90001", seed=5, wear=0.2)
        finally:
            api._spawn_handlers.clear()
        self.assertEqual(seen, [plain])
        self.assertEqual(plain.cs_weapon.seed, 5)
        self.assertEqual(api.weapons(), [plain])
        self.assertIsNone(api.armature(plain))
        self.assertEqual(api.clips(plain), {})
        self.assertIsNone(api.weapon_of(None))
        # an attachment on the bolt: it follows the clip
        weapon = registry.get_catalog().weapons["demo_rifle"]
        source = Matrix.Translation((10.0, 0.0, 20.0))  # Source inches, model space
        weapon.attachments["muzzle_flash"] = {"bone": "bolt", "matrix": [v for r in source for v in r]}
        with tempfile.TemporaryDirectory() as d:
            weapon.rig = self._rig_glb(d)
            try:
                plain = api.spawn(bpy.context, "demo_rifle:90001")
                obj = api.spawn(bpy.context, "demo_rifle:90001", rigged=True)
                arm = api.armature(obj)
                self.assertIs(api.weapon_of(arm), obj)
                self.assertEqual(api.bones(obj), ["weapon", "bolt"])
                self.assertEqual(list(api.clips(obj)), ["reload"])
                self.assertEqual(api.attachments(obj), ["muzzle_flash"])
                rest = api.attachment_matrix(obj, "muzzle_flash")
                expect = api.attachment_matrix(plain, "muzzle_flash")
                self.assertLess((rest.translation - expect.translation).length, 1e-5)
                self.assertIsNotNone(api.set_clip(obj, "reload"))
                bpy.context.scene.frame_set(10)
                moved = api.attachment_matrix(obj, "muzzle_flash")
                self.assertAlmostEqual((moved.translation - rest.translation).length, (0.2 ** 2 + 0.05 ** 2) ** 0.5,
                                       places=4, msg="bolt clip plus root bone offset")
                self.assertIsNone(api.set_clip(obj, "no_such_clip"))
                api.rest_pose(obj)
                bpy.context.scene.frame_set(10)
                self.assertLess((api.attachment_matrix(obj, "muzzle_flash").translation
                                 - rest.translation).length, 1e-5)
                self.assertIs(spawn.rig_of(obj), arm)
            finally:
                weapon.rig = ""
                del weapon.attachments["muzzle_flash"]

    def test_seed_updates_transform(self):
        obj = self._spawn("demo_rifle:90001")
        nodes = obj.material_slots[0].material.node_tree.nodes
        before = nodes["cs_x0_t"].outputs[0].default_value
        obj.cs_weapon.seed = 661
        after = nodes["cs_x0_t"].outputs[0].default_value
        self.assertNotAlmostEqual(before, after)
        # matrix = scaled rotation, scale 1 for the demo weapon
        u, v = nodes["cs_x0_u"].outputs[0].default_value, nodes["cs_x0_v"].outputs[0].default_value
        self.assertAlmostEqual(u * u + v * v, 1.0, places=4)

    def test_spec_survives_without_library(self):
        obj = self._spawn("demo_rifle:90002")
        mat = obj.material_slots[0].material
        spec = material.spec_of(mat)
        self.assertEqual(spec["style"], 6)
        self.assertTrue(material.apply_seed(mat, 412))

    def test_render_smoke(self):
        obj = self._spawn("demo_rifle:90001")
        scene = bpy.context.scene
        cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
        scene.collection.objects.link(cam)
        cam.location = (1.5, 0, 0.1)
        cam.rotation_euler = (1.5708, 0, 1.5708)
        scene.camera = cam
        engines = scene.render.bl_rna.properties["engine"].enum_items.keys()
        scene.render.engine = "BLENDER_EEVEE" if "BLENDER_EEVEE" in engines else "BLENDER_EEVEE_NEXT"  # 4.5
        scene.render.resolution_x = scene.render.resolution_y = 64
        scene.render.filepath = bpy.app.tempdir + "cs_weapon_smoke.png"
        bpy.ops.render.render(write_still=True)
        self.assertTrue(obj.visible_get())


if __name__ == "__main__":
    unittest.main()
