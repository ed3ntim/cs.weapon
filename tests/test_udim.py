# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""UDIM tiles for materials with more images than EEVEE has samplers
(#38, Driver Gloves | Plum Quill and Wave Chaser rendered pink): which
textures become tiles, the tile UV, and the tiled image built from copies
of the files."""

import os
import tempfile
import unittest

import bpy

from cs_weapon.nodes import udim


def _sizes(table):
    return lambda path, srgb: table[path]


class PlanTests(unittest.TestCase):
    def test_under_the_limit_unchanged(self):
        tex = [(f"t{i}.png", False) for i in range(5)]
        self.assertEqual(udim.plan(tex, _sizes({p: (512, 512) for p, _ in tex}), limit=5), {})

    def test_groups_by_size_and_colour_space(self):
        sizes = {"a.png": (512, 512), "b.png": (512, 512), "c.png": (512, 512),
                 "d.png": (2048, 2048), "e.png": (512, 512), "f.png": (1024, 1024)}
        tex = [("a.png", False), ("b.png", False), ("c.png", False), ("d.png", False),
               ("e.png", True), ("f.png", True)]
        out = udim.plan(tex, _sizes(sizes), limit=4)
        # the three 512 data images become one; colour images and other sizes stay
        self.assertEqual(set(out), {("a.png", False), ("b.png", False), ("c.png", False)})
        group = out[("a.png", False)][0]
        self.assertEqual(group, ((512, 512), False, ("a.png", "b.png", "c.png")))
        self.assertEqual(sorted(i for _g, i in out.values()), [0, 1, 2])

    def test_only_as_many_groups_as_needed(self):
        sizes = {f"x{i}.png": (512, 512) for i in range(6)}
        sizes.update({f"y{i}.png": (256, 256) for i in range(3)})
        tex = [(p, False) for p in sizes]
        out = udim.plan(tex, _sizes(sizes), limit=8)  # 9 images: the largest group is enough
        self.assertEqual({p for p, _s in out}, {f"x{i}.png" for i in range(6)})
        out = udim.plan(tex, _sizes(sizes), limit=2)  # both groups
        self.assertEqual(len(out), 9)

    def test_same_file_as_colour_and_data_counts_twice(self):
        sizes = {"a.png": (8, 8), "b.png": (8, 8)}
        tex = [("a.png", True), ("a.png", False), ("b.png", True)]
        out = udim.plan(tex, _sizes(sizes), limit=2)
        self.assertEqual(set(out), {("a.png", True), ("b.png", True)})


class TileTests(unittest.TestCase):
    def test_tile_offset(self):
        self.assertEqual(udim.tile_offset(0), (0, 0))
        self.assertEqual(udim.tile_offset(9), (9, 0))
        self.assertEqual(udim.tile_offset(12), (2, 1))

    def test_tile_uv_wraps_inside_the_tile(self):
        from cs_weapon.nodes.expr import Graph
        mat = bpy.data.materials.new("udim test")
        mat.use_nodes = True
        self.addCleanup(bpy.data.materials.remove, mat)
        g = Graph(mat.node_tree)
        got = udim.tile_uv(g, g.wrap((3.25, -0.5, 0.0)), 12).const
        for a, b in zip(got, (2.25, 1.5, 0.0)):
            self.assertAlmostEqual(a, b)


class ImageTests(unittest.TestCase):
    def test_tiled_image_from_files(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        paths = []
        for i, color in enumerate(((1, 0, 0, 1), (0, 1, 0, 1), (0, 0, 1, 1))):
            img = bpy.data.images.new(f"src{i}", 4, 4)
            img.pixels.foreach_set([c for _ in range(16) for c in color])
            img.filepath_raw = os.path.join(tmp.name, f"src{i}.png")
            img.file_format = "PNG"
            img.save()
            bpy.data.images.remove(img)
            paths.append(img_path := os.path.join(tmp.name, f"src{i}.png"))
            self.assertTrue(os.path.isfile(img_path))
        group = ((4, 4), False, tuple(paths))
        img = udim.image(group)
        self.addCleanup(lambda: bpy.data.images.remove(img))
        self.assertEqual(img.source, "TILED")
        self.assertEqual([t.number for t in img.tiles], [1001, 1002, 1003])
        self.assertEqual(img.colorspace_settings.name, "Non-Color")
        self.assertTrue(os.path.isdir(os.path.join(tmp.name, "_udim")))
        self.assertIs(udim.image(group), img, "reused")


if __name__ == "__main__":
    unittest.main()
