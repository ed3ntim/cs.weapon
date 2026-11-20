# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Special shaders (rain, frost): UV density rules, material insert/remove, object sync."""

import os
import tempfile
import unittest

import bpy

from cs_weapon import utils
from cs_weapon.library import registry
from cs_weapon.nodes import special_shader


def _quad(size_m, uv_size):
    mesh = bpy.data.meshes.new("quad")
    s = size_m
    mesh.from_pydata([(0, 0, 0), (s, 0, 0), (s, s, 0), (0, s, 0)], [], [(0, 1, 2, 3)])
    uv = mesh.uv_layers.new()
    for loop, co in zip(uv.data, ((0, 0), (uv_size, 0), (uv_size, uv_size), (0, uv_size))):
        loop.uv = co
    return mesh


class TileTests(unittest.TestCase):
    def test_coarse_uv_tiles_three_times(self):
        # 1 m^2 = 1550 in^2 with UV area 1: 0.00065 per in^2 < 0.002
        mesh = _quad(1.0, 1.0)
        self.addCleanup(bpy.data.meshes.remove, mesh)
        self.assertEqual(special_shader.tile_factors(mesh), [3.0])

    def test_dense_uv_keeps_scale(self):
        # 1 cm^2 = 0.155 in^2 with UV area 0.25: 1.6 per in^2
        mesh = _quad(0.01, 0.5)
        self.addCleanup(bpy.data.meshes.remove, mesh)
        self.assertEqual(special_shader.tile_factors(mesh), [1.0])

    def test_attribute(self):
        mesh = _quad(1.0, 1.0)
        self.addCleanup(bpy.data.meshes.remove, mesh)
        attr = special_shader.ensure_tile_attribute(mesh)
        self.assertEqual(attr.domain, "FACE")
        self.assertEqual(attr.data[0].value, 3.0)
        self.assertEqual(special_shader.ensure_tile_attribute(mesh).name, attr.name)
        self.assertEqual(len([a for a in mesh.attributes if a.name == special_shader.TILE_ATTRIBUTE]), 1)


class FrostScaleTests(unittest.TestCase):
    def test_scale_is_power_of_two_of_uv_density(self):
        # 1 m (39.37 in) with UV 1: 0.0254 UV per inch, 0.25 / 0.0254 = 9.8 -> 8
        mesh = _quad(1.0, 1.0)
        self.addCleanup(bpy.data.meshes.remove, mesh)
        self.assertEqual(special_shader.frost_scales(mesh), [8.0])

    def test_dense_uv_small_scale(self):
        # 1 cm (0.39 in) with UV 0.5: 1.27 UV per inch, 0.197 -> 0.25
        mesh = _quad(0.01, 0.5)
        self.addCleanup(bpy.data.meshes.remove, mesh)
        self.assertEqual(special_shader.frost_scales(mesh), [0.25])

    def test_attribute(self):
        mesh = _quad(1.0, 1.0)
        self.addCleanup(bpy.data.meshes.remove, mesh)
        attr = special_shader.ensure_scale_attribute(mesh)
        self.assertEqual((attr.domain, attr.data[0].value), ("FACE", 8.0))
        self.assertEqual(special_shader.ensure_scale_attribute(mesh).name, attr.name)


class RainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.droplets = os.path.join(cls.tmp.name, "water_droplets.png")
        img = bpy.data.images.new("droplets src", 8, 8, alpha=True)
        img.pixels = [0.5, 0.5, 0.3, 1.0] * 64
        img.filepath_raw = cls.droplets
        img.file_format = "PNG"
        img.save()
        bpy.data.images.remove(img)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)
        registry.refresh(bpy.context)
        registry.get_catalog().special_shaders = {"rain": {"droplets": self.droplets},
                                                  "frost": {"frost": self.droplets}}

    def tearDown(self):
        registry.get_catalog().special_shaders = {}

    def _spawn(self, sid="demo_rifle:90001"):
        self.assertEqual(bpy.ops.cs_weapon.spawn(skin_id=sid), {"FINISHED"})
        return bpy.context.active_object

    def _bsdf_sources(self, mat):
        tree = special_shader._tree(mat)
        bsdf = special_shader._bsdf(tree)
        return {name: (bsdf.inputs[name].links[0].from_node.name if bsdf.inputs[name].is_linked else None)
                for name in special_shader.BSDF_INPUTS}

    def test_group_builds(self):
        tree = special_shader.rain_group(special_shader.droplets_image())
        self.assertEqual(tree.get(special_shader.VERSION_KEY), special_shader.RAIN_VERSION)
        outs = {n for n in tree.nodes if n.bl_idname == "NodeGroupOutput"}
        self.assertEqual(len(outs), 1)
        out = outs.pop()
        for name, _typ in special_shader.OUTPUTS:
            self.assertTrue(out.inputs[name].is_linked, name)
        self.assertIs(tree.nodes["droplets"].image, bpy.data.images[special_shader.DROPLETS_IMAGE])
        self.assertIs(special_shader.rain_group(), tree, "cached by version")

    def test_insert_and_remove_restore_bsdf(self):
        obj = self._spawn()
        mat = obj.material_slots[0].material
        before = self._bsdf_sources(mat)
        self.assertIsNotNone(special_shader.insert(mat))
        during = self._bsdf_sources(mat)
        self.assertEqual(set(during.values()), {special_shader.NODE})
        self.assertEqual(special_shader.insert(mat).name, special_shader.NODE)
        self.assertEqual(sum(n.name.startswith(special_shader.NODE) and n.bl_idname == "ShaderNodeGroup"
                             for n in special_shader._tree(mat).nodes), 1, "only once")
        self.assertTrue(special_shader.remove(mat))
        self.assertEqual(self._bsdf_sources(mat), before)
        self.assertFalse(special_shader.has_effect(mat))

    def test_object_sync_and_values(self):
        obj = self._spawn()
        mat = obj.material_slots[0].material
        s = obj.cs_weapon
        s.rain_wetness = 0.7
        s.special_shader = special_shader.RAIN
        self.assertTrue(special_shader.has_effect(mat))
        node = special_shader._tree(mat).nodes[special_shader.NODE]
        self.assertAlmostEqual(node.inputs["Wetness"].default_value, 0.7, places=5)
        self.assertIn(special_shader.TILE_ATTRIBUTE, obj.data.attributes)
        s.rain_time = 3.0
        self.assertAlmostEqual(node.inputs["Time"].default_value, 3.0, places=5)
        s.rain_animate = True
        tree = special_shader._tree(mat)
        self.assertEqual(len(tree.animation_data.drivers), 1)
        s.rain_animate = False
        self.assertEqual(len(tree.animation_data.drivers), 0)
        # a seed change keeps the effect
        s.seed = 5
        self.assertTrue(special_shader.has_effect(mat))
        s.special_shader = special_shader.NONE
        self.assertFalse(special_shader.has_effect(mat))

    def test_reskin_keeps_effect(self):
        from cs_weapon.ops import spawn
        obj = self._spawn()
        obj.cs_weapon.special_shader = special_shader.RAIN
        spawn.reskin(obj, registry.get_catalog().get_skin("demo_rifle:90002"))
        self.assertTrue(special_shader.has_effect(obj.material_slots[0].material))

    def test_vanilla(self):
        obj = self._spawn("demo_rifle:0")
        obj.cs_weapon.special_shader = special_shader.RAIN
        self.assertTrue(special_shader.has_effect(obj.material_slots[0].material))

    def test_without_texture_nothing_changes(self):
        registry.get_catalog().special_shaders = {}
        obj = self._spawn()
        obj.cs_weapon.special_shader = special_shader.RAIN
        self.assertFalse(special_shader.has_effect(obj.material_slots[0].material))
        self.assertTrue(utils.is_weapon(obj))


class FrostTests(RainTests):
    """Frost on the same demo items (the rain tests run again as well)."""

    def test_frost_group_builds(self):
        tree = special_shader.frost_group(special_shader.effect_image(special_shader.FROST))
        self.assertEqual(tree.get(special_shader.FROST_VERSION_KEY), special_shader.FROST_VERSION)
        out = next(n for n in tree.nodes if n.bl_idname == "NodeGroupOutput")
        for name, _typ in special_shader.FROST_OUTPUTS:
            self.assertTrue(out.inputs[name].is_linked, name)
        self.assertEqual({tree.nodes[n].image.name for n in ("frost_a", "frost_b")}, {special_shader.FROST_IMAGE})
        self.assertEqual(special_shader.frost_group().name, tree.name, "cached by version")

    def test_frost_insert_and_remove_restore_bsdf(self):
        obj = self._spawn()
        mat = obj.material_slots[0].material
        tree = special_shader._tree(mat)
        bsdf = special_shader._bsdf(tree)
        before = {n: (bsdf.inputs[n].links[0].from_socket.name, bsdf.inputs[n].links[0].from_node.name)
                  if bsdf.inputs[n].is_linked else bsdf.inputs[n].default_value
                  for n in special_shader.FROST_BSDF_INPUTS if n != "Base Color"}
        node = special_shader.insert(mat, effect=special_shader.FROST)
        self.assertEqual({bsdf.inputs[n].links[0].from_node.name for n in special_shader.FROST_BSDF_INPUTS},
                         {special_shader.FROST_NODE})
        self.assertTrue(special_shader.has_effect(mat, special_shader.FROST))
        self.assertFalse(special_shader.has_effect(mat, special_shader.RAIN))
        self.assertTrue(special_shader.remove(mat))
        after = {n: (bsdf.inputs[n].links[0].from_socket.name, bsdf.inputs[n].links[0].from_node.name)
                 if bsdf.inputs[n].is_linked else bsdf.inputs[n].default_value
                 for n in special_shader.FROST_BSDF_INPUTS if n != "Base Color"}
        self.assertEqual(after, before)
        self.assertFalse(any(n.name.startswith(special_shader.FROST_NODE) for n in tree.nodes))
        del node

    def test_switch_effects(self):
        obj = self._spawn()
        mat = obj.material_slots[0].material
        s = obj.cs_weapon
        s.frost_amount = 0.4
        s.frost_melt = 0.2
        s.special_shader = special_shader.FROST
        node = special_shader._tree(mat).nodes[special_shader.FROST_NODE]
        self.assertAlmostEqual(node.inputs["Frost"].default_value, 0.4, places=5)
        self.assertAlmostEqual(node.inputs["Melt"].default_value, 0.2, places=5)
        self.assertIn(special_shader.SCALE_ATTRIBUTE, obj.data.attributes)
        s.special_shader = special_shader.RAIN
        self.assertTrue(special_shader.has_effect(mat, special_shader.RAIN))
        self.assertFalse(special_shader.has_effect(mat, special_shader.FROST))
        s.special_shader = special_shader.FROST
        self.assertFalse(special_shader.has_effect(mat, special_shader.RAIN))
        s.special_shader = special_shader.NONE
        self.assertFalse(special_shader.has_effect(mat))

    def test_frost_without_texture(self):
        registry.get_catalog().special_shaders = {"rain": {"droplets": self.droplets}}
        obj = self._spawn()
        obj.cs_weapon.special_shader = special_shader.FROST
        self.assertFalse(special_shader.has_effect(obj.material_slots[0].material))


if __name__ == "__main__":
    unittest.main()
