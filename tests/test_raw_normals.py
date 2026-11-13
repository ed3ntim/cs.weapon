# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Glove normals without Source2Viewer's decoding (#39): which library
textures the add-on encodes back, which ones tools/fix_glove_normals.py
replaces, and the VPK lookup. Fixtures are hand-written, no game data."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import export_cs2_weapons as E  # noqa: E402
import fix_glove_normals as F  # noqa: E402


class RawNormalPathTests(unittest.TestCase):
    def test_raw_folder(self):
        from cs_weapon.nodes import glove
        self.assertTrue(glove.is_raw_normal("C:/lib/paints_raw/suede_normal_tga_1.png"))
        self.assertTrue(glove.is_raw_normal(r"C:\lib\paints_raw\suede_normal_tga_1.png"))
        self.assertFalse(glove.is_raw_normal("C:/lib/paints/suede_normal_tga_1.png"))
        self.assertFalse(glove.is_raw_normal("C:/lib/paints/paints_raw.png"))
        self.assertEqual(glove.RAW_NORMALS_FOLDER, E.RAW_NORMALS_GROUP)

    def test_normal_keys(self):
        for key in ("g_tNormal", "g_tSurfaceNormal2", "g_tSubstrateNormal1", "g_tDamageNormal4", "TextureNormal"):
            self.assertTrue(E.is_normal_key(key), key)
        for key in ("g_tSurface1", "g_tDamage1", "g_tAo"):
            self.assertFalse(E.is_normal_key(key), key)


def _catalog():
    return {
        "weapons": [{"id": "slick_gloves", "glove": {"materials": {
            "glove_slick_right": {"textures": {"TextureNormal": "gloves/slick/glove_slick_normal.png"}}}}},
            {"id": "weapon_ak47"}],
        "paint_kits": [
            {"id": 1, "glove": True,
             "passes": {"g_tColor": [{"set": {"g_tSurfaceNormal1": "paints/suede_normal_a.png",
                                              "g_tSurface1": "paints/suede_color.png",
                                              "g_flScale": 1.0}},
                                     {"weapon": True}],
                        "g_tNormal": [{"set": {"g_tNormal": "paints/glove_half_back_normal.png",
                                               "g_tSurfaceNormal1": "paints/suede_normal_a.png"}}]},
             "final": [{"set": {"g_tDetailNormal1": "paints_raw/already_raw_normal.png"}}]},
            {"id": 2, "passes": {"g_tColor": [{"set": {"g_tNormal": "paints/ak47_normal.png"}}]}},
        ],
    }


class FixToolTests(unittest.TestCase):
    def test_refs_glove_kits_only(self):
        refs = F.glove_normal_refs(_catalog())
        self.assertEqual(sorted(refs), ["paints/glove_half_back_normal.png", "paints/suede_normal_a.png"])
        self.assertEqual(len(refs["paints/suede_normal_a.png"]), 2)  # used by two passes

    def test_refs_are_rewritable(self):
        cat = _catalog()
        for holder, key in F.glove_normal_refs(cat)["paints/suede_normal_a.png"]:
            holder[key] = "paints_raw/suede_normal_a.png"
        passes = cat["paint_kits"][0]["passes"]
        self.assertEqual(passes["g_tColor"][0]["set"]["g_tSurfaceNormal1"], "paints_raw/suede_normal_a.png")
        self.assertEqual(passes["g_tColor"][0]["set"]["g_tSurface1"], "paints/suede_color.png")
        self.assertEqual(F.glove_normal_refs(cat).keys(), {"paints/glove_half_back_normal.png"})

    def test_vpk_paths(self):
        listing = ["items/assets/paintkits/volatile_02/textiles/suede_normal_a.vtex_c",
                   "items/assets/paintkits/volatile_02/textiles/suede_normal_a.vmat_c",
                   "agents/models/shared/arms/glove_slick/glove_half_back_normal.vtex_c",
                   "other/thing.vtex_c"]
        got = F.vpk_paths(listing, ["suede_normal_a", "glove_half_back_normal", "missing_normal"])
        self.assertEqual(got, {"suede_normal_a": listing[0], "glove_half_back_normal": listing[2]})


if __name__ == "__main__":
    unittest.main()
