# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Pure-Python tests for the catalog and wear helpers (no Blender needed)."""

import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "cs_weapon", "library"))

import catalog as C  # noqa: E402
import stickers as ST  # noqa: E402
import wear  # noqa: E402

CORE = os.path.join(ROOT, "tests", "fixtures", "demo_library")


class CoreLibraryTests(unittest.TestCase):
    def setUp(self):
        self.cat = C.Catalog().load(CORE)

    def test_core_loads_complete(self):
        self.assertEqual(self.cat.report.warnings, [])
        self.assertEqual(len(self.cat.skins), 3)  # two skins + vanilla
        for skin in self.cat.skins.values():
            self.assertEqual(skin.missing_files(), [], skin.id)

    def test_filter(self):
        self.assertEqual(len(self.cat.filter("fade")), 1)
        self.assertEqual(len(self.cat.filter("demo rifle")), 3)
        self.assertEqual(len(self.cat.filter(style=8)), 1)
        self.assertEqual(len(self.cat.filter(style=0)), 1)
        self.assertEqual(len(self.cat.filter("vanilla")), 1)
        self.assertEqual(len(self.cat.filter(weapon_type="knife")), 0)
        self.assertEqual(self.cat.weapon_types(), ["rifle"])
        self.assertEqual(len(self.cat.filter(weapon_id="demo_rifle")), 3)
        self.assertEqual(len(self.cat.filter(weapon_id="other")), 0)
        self.assertTrue(self.cat.filter("demo rifle")[0].is_vanilla, "vanilla sorts first")
        self.assertEqual(self.cat.weapon_list(), [("demo_rifle", "Demo Rifle")])
        self.assertEqual(self.cat.weapon_list("knife"), [])

    def test_incomplete_cached(self):
        skin = self.cat.filter("fade")[0]
        self.assertFalse(self.cat.incomplete(skin))
        skin.paint_kit.passes = {"x": [{"set": {"g_tPattern": "nope/missing.png"}}]}
        self.assertFalse(self.cat.incomplete(skin), "cached until the library loads again")
        self.cat._incomplete.clear()
        self.assertTrue(self.cat.incomplete(skin))

    def test_vanilla_skin_per_weapon(self):
        for weapon in self.cat.weapons.values():
            skin = self.cat.get_skin(C.skin_id(weapon.id, C.VANILLA_ID))
            self.assertIsNotNone(skin, weapon.id)
            self.assertTrue(skin.is_vanilla)
            self.assertEqual(skin.name, f"{weapon.name} (Vanilla)")
            self.assertEqual(skin.paint_kit.style_name, "Vanilla")

    def test_ranges_for_seed(self):
        kit = self.cat.paint_kits[90002]
        self.assertEqual(kit.seed_bounds()["g_flPatternTexCoordRotation"], (-55, -65))
        self.assertEqual(kit.style_name, "Anodized Airbrushed")


class MergeTests(unittest.TestCase):
    def _lib(self, d, data):
        with open(os.path.join(d, "catalog.json"), "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def test_later_library_overrides_and_bad_entries_warn(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            self._lib(a, {"version": 1, "weapons": [{"id": "w", "name": "Old"}],
                          "paint_kits": [{"id": 1}], "skins": [{"weapon": "w", "paint_kit": 1}]})
            self._lib(b, {"version": 1, "weapons": [{"id": "w", "name": "New"}, {"name": "no id"}],
                          "skins": [{"weapon": "x", "paint_kit": 1}]})
            cat = C.Catalog().load(a, b, "", os.path.join(a, "missing"))
            self.assertEqual(cat.weapons["w"].name, "New")
            self.assertEqual(len(cat.report.warnings), 2)
            self.assertEqual(len(cat.skins), 2)  # w:1 and the vanilla w:0
            self.assertIs(cat.get_skin("w:0").weapon, cat.weapons["w"])


class StickerTests(unittest.TestCase):
    def test_demo_slots(self):
        var = C.Catalog().load(CORE).weapons["demo_rifle"].hd
        slots = var.sticker_slots()
        self.assertEqual([s["slot"] for s in slots], [0, 1])
        self.assertEqual(slots[1]["offset"], (0.2, 0.1))
        self.assertEqual(slots[1]["scale"], 4.0)

    def test_slots_skip_empty_and_negative_scale(self):
        var = C.Variant({"target": {"g_vSticker0Offset": [0.1, -0.4], "g_vSticker0Scale": [-14.1, 14.1],
                                    "g_vSticker1Offset": [0.0, 0.0], "g_vSticker1Scale": [0.0, 0.0],
                                    "g_vSticker2Scale": [5.0, 5.0]}}, "", False)
        self.assertEqual(var.sticker_slots(), [{"slot": 0, "offset": (0.1, -0.4), "scale": 14.1,
                                                "rotation": 0.0, "deprecated": False}])

    def test_slot_rotation(self):
        var = C.Variant({"target": {"g_vSticker3Offset": [0.233, -0.137], "g_vSticker3Scale": [6.35, 6.35],
                                    "g_flSticker3Rotation": 0.05}}, "", False)
        self.assertEqual(var.sticker_slots()[0]["rotation"], 0.05)
        var = C.Variant({"sticker_markup": [{"slot": 3, "offset": [0.2, -0.1], "scale": 6.4, "rotation": 0.063}]},
                        "", False)
        self.assertEqual(var.sticker_slots()[0]["rotation"], 0.063)

    def test_markup_wins_over_material(self):
        var = C.Variant({"target": {"g_vSticker0Offset": [0.1, -0.4], "g_vSticker0Scale": [5.8, 5.8]},
                         "sticker_markup": [
                             {"slot": 4, "offset": [0.21, -0.32], "scale": 6.4},
                             {"slot": 0, "offset": [0.211, -0.319], "scale": 5.8, "deprecated": True},
                             {"slot": 7, "offset": [0.0, 0.0], "scale": 6.4},
                             {"slot": 1, "offset": [0.0, 0.0], "scale": 0.0}]}, "", False)
        slots = var.sticker_slots()
        self.assertEqual([s["slot"] for s in slots], [0, 4])
        self.assertTrue(slots[0]["deprecated"])
        self.assertEqual((slots[1]["offset"], slots[1]["scale"], slots[1]["rotation"]), ((0.21, -0.32), 6.4, 0.0))

    def test_library_kit_ids_and_filter(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "catalog.json"), "w", encoding="utf-8") as fh:
                json.dump({"stickers": [
                    {"id": "cs2_343_eslkatowice2015_titan_holo", "name": "Titan (Holo)",
                     "collection": "Katowice 2015", "finish": "HOLO",
                     "textures": {"base": "t/titan.png"}, "tags": ["x", "mythical"]},
                    {"id": "user_x", "name": "Own | Event 2020", "textures": {"base": "own.png"},
                     "kit_id": 9001},
                    {"textures": {"base": "x.png"}}]}, fh)
            lib = ST.StickerLibrary().load(d)
            self.assertEqual(len(lib), 2)
            titan = lib.by_kit_id(343)
            self.assertEqual((titan.name, titan.event, titan.rarity), ("Titan (Holo)", "Katowice 2015", "mythical"))
            self.assertEqual(titan.full_name, "Titan (Holo) | Katowice 2015")
            self.assertTrue(titan.base_path.endswith(os.path.join("t", "titan.png")))
            own = lib.by_kit_id(9001)
            self.assertEqual((own.name, own.event, own.finish), ("Own", "Event 2020", "PAPER"))
            self.assertEqual([s.id for s in lib.filter("titan holo")], [titan.id])
            copy = ST.Sticker.from_dict(titan.to_dict())
            self.assertEqual((copy.id, copy.kit_id, copy.event, copy.textures), (titan.id, 343, titan.event, titan.textures))


class WearTests(unittest.TestCase):
    def test_tiers(self):
        self.assertEqual(wear.tier(0.0)[0], "FN")
        self.assertEqual(wear.tier(0.0699)[0], "FN")
        self.assertEqual(wear.tier(0.07)[0], "MW")
        self.assertEqual(wear.tier(0.38)[0], "WW")
        self.assertEqual(wear.tier(0.45)[0], "BS")
        self.assertEqual(wear.tier(1.0)[0], "BS")

    def test_clamp_and_midpoint(self):
        self.assertEqual(wear.clamp(0.9, 0.0, 0.08), 0.08)
        self.assertAlmostEqual(wear.tier_midpoint("FT"), 0.265)
        self.assertEqual(wear.tier_midpoint("BS", 0.0, 0.08), 0.08)



class DopplerPhaseTests(unittest.TestCase):
    def test_phases_and_gems(self):
        cases = {"am_doppler_phase2_b": "Phase 2", "am_ruby_marbleized": "Ruby",
                 "am_blackpearl_marbleized_b": "Black Pearl", "am_gamma_doppler_phase4_glock": "Phase 4",
                 "am_emerald_marbleized_glock": "Emerald", "am_doppler_phase1_widow": "Phase 1",
                 "am_marble_fade": "", "cu_ak47_phase2": "", "aq_oiled": ""}
        for code, label in cases.items():
            self.assertEqual(C.phase_of(code), label, code)

    def test_skin_name_and_search(self):
        root = tempfile.mkdtemp()
        kit = C.PaintKit({"id": 416, "name": "am_sapphire_marbleized", "display": "Doppler"}, root)
        self.assertEqual(kit.phase, "Sapphire")


if __name__ == "__main__":
    unittest.main()
