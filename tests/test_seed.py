# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Pure-Python tests for seed rolls and inspect links (no Blender needed).

Run: python -m unittest discover -s tests -p "test_seed.py"
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "cs_weapon", "library"))

import inspect_link  # noqa: E402
import pattern  # noqa: E402
import seed  # noqa: E402


class SeedTests(unittest.TestCase):
    def test_deterministic(self):
        a = seed.UniformRandomStream(661)
        b = seed.UniformRandomStream(661)
        self.assertEqual([a.next_int() for _ in range(50)], [b.next_int() for _ in range(50)])

    def test_seeds_differ(self):
        firsts = {seed.UniformRandomStream(s).next_int() for s in range(1001)}
        self.assertGreater(len(firsts), 990)

    def test_zero_equals_one(self):
        # ran1 maps idum 0 to 1, so seed 0 and seed 1 give the same stream
        self.assertEqual(seed.UniformRandomStream(0).next_int(), seed.UniformRandomStream(1).next_int())

    def test_float_range(self):
        rng = seed.UniformRandomStream(42)
        vals = [rng.random_float(-55.0, -65.0) for _ in range(2000)]
        self.assertTrue(all(-65.0 <= v <= -55.0 for v in vals))
        rng = seed.UniformRandomStream(42)
        vals = [rng.random_float() for _ in range(5000)]
        self.assertTrue(all(0.0 <= v < 1.0 for v in vals))
        self.assertAlmostEqual(sum(vals) / len(vals), 0.5, delta=0.02)

    def test_roll_components(self):
        out = seed.roll(661, [("rot", 0.0, 360.0), ("off", (0.0, 0.0), (1.0, 1.0))])
        self.assertEqual(len(out["off"]), 2)
        rng = seed.UniformRandomStream(661)
        self.assertEqual(out["rot"], rng.random_float(0.0, 360.0))
        self.assertEqual(out["off"][0], rng.random_float(0.0, 1.0))


FADE = {"g_flPatternTexCoordRotation": (-55.0, -65.0)}
CASE_HARDENED = {
    "g_flPatternTexCoordRotation": (0.0, 360.0),
    "g_vPatternTexCoordOffset": ((0.0, 0.0), (1.0, 1.0)),
}


class RollPaintTests(unittest.TestCase):
    def test_fade_skips_offset_rolls(self):
        # In-game (#1): Fade without offset bounds still consumes two rolls,
        # rotation is the third value. Karambit Fade 412 sits at the range end.
        rot = seed.roll_paint(412, FADE)["g_flPatternTexCoordRotation"]
        self.assertAlmostEqual(rot, -64.995, places=2)
        self.assertEqual(seed.roll_paint(412, FADE)["g_vPatternTexCoordOffset"], (0.0, 0.0))

    def test_fade_ordering_matches_screenshots(self):
        # AWP Fade: seed 500 shows most blue, seed 1000 least, seed 0 between
        rot = {s: seed.roll_paint(s, FADE)["g_flPatternTexCoordRotation"] for s in (0, 500, 1000)}
        self.assertGreater(rot[500], rot[0])
        self.assertGreater(rot[0], rot[1000])

    def test_case_hardened_order(self):
        out = seed.roll_paint(661, CASE_HARDENED)
        rng = seed.UniformRandomStream(661)
        ox, oy = rng.random_float(), rng.random_float()
        self.assertEqual(out["g_vPatternTexCoordOffset"], (ox, oy))
        self.assertEqual(out["g_flPatternTexCoordRotation"], rng.random_float(0.0, 360.0))

    def test_shared_wear_rolls_same_for_all_kits(self):
        a = seed.roll_paint(661, CASE_HARDENED)
        b = seed.roll_paint(661, FADE)
        for name in ("g_flWearTexCoordScale", "g_vWearTexCoordOffset", "g_flGrungeTexCoordRotation"):
            self.assertEqual(a[name], b[name])
        self.assertTrue(1.6 <= a["g_flWearTexCoordScale"] <= 1.8)


class PatternXformTests(unittest.TestCase):
    # Translation (mod 1) measured from in-game screenshots of AK-47 Case
    # Hardened, legacy model, g_flUvScale1 0.549 (#1).
    MEASURED = {0: (0.441, 0.047), 1000: (0.145, 0.355), 151: (0.121, 0.875),
                29: (0.137, 0.293), 661: (0.180, 0.453)}

    def test_translation_matches_screenshots(self):
        for sd, (tx, ty) in self.MEASURED.items():
            r = seed.roll_paint(sd, CASE_HARDENED)
            x0, x1 = pattern.xform(r["g_flPatternTexCoordRotation"], r["g_vPatternTexCoordOffset"],
                                   factor=pattern.size_factor(uv_scale=0.549))
            for got, want in ((x0[3], tx), (x1[3], ty)):
                d = (got - want + 0.5) % 1.0 - 0.5
                self.assertLess(abs(d), 0.01, f"seed {sd}")

    def test_matrix_is_scaled_rotation(self):
        x0, x1 = pattern.xform(90.0, (0.0, 0.0), factor=0.5)
        self.assertAlmostEqual(x0[0], 0.0, places=6)
        self.assertAlmostEqual(x0[1], -0.5, places=6)
        self.assertAlmostEqual(x1[0], 0.5, places=6)

    def test_rounding(self):
        self.assertEqual(pattern.round2(105.373), 105.37)
        self.assertEqual(pattern.round2(0.3163), 0.32)
        self.assertEqual(pattern.size_factor(uv_scale=0.772, weapon_length=37.746), 0.772)
        self.assertEqual(pattern.size_factor(uv_scale=0.772, ignore_size=True), 1.0)


class InspectLinkTests(unittest.TestCase):
    def test_free_placed_stickers_and_charm(self):
        link = inspect_link.link(defindex=4, paintindex=1119, stickers=[
            {"slot": 2, "id": 2769, "offset_x": 0.23, "offset_y": -0.003},
            {"slot": 2, "id": 2718, "offset_x": 0.18}],
            keychains=[{"slot": 0, "id": 36, "pattern": 1234, "offset_x": 1.5}])
        item = inspect_link.parse_item(link)
        self.assertEqual([s["slot"] for s in item["stickers"]], [2, 2])
        self.assertAlmostEqual(item["stickers"][0]["offset_x"], 0.23, places=6)
        self.assertEqual(item["keychains"][0]["id"], 36)
        self.assertEqual(item["keychains"][0]["pattern"], 1234)
        self.assertTrue(item["checksum_ok"])

    def test_sticker_slab_keeps_its_sticker(self):
        # Link from #22: Sticker Slab (charm 37) wrapping mousesports Katowice 2014 (kit 65).
        item = inspect_link.parse_item(
            "F7E73C737D3748F6EFF0D76DF5DFF2C7FECF07721207F4B710F2BFF7A712B195F3FFF7E7B695F3FFF6E7B695F3"
            "FFF5E7B695F3FFF4E7B69FAD87FF55F6E2FFF7E7D2CAE98E66B7B213102DC9BA280376C997B6E61FC591")
        self.assertEqual(item["keychains"][0]["id"], 37)
        self.assertEqual(item["keychains"][0]["sticker"], 65)
        link = inspect_link.link(defindex=7, paintindex=0, keychains=[{"slot": 0, "id": 37, "sticker": 5000}])
        self.assertEqual(inspect_link.parse_item(link)["keychains"][0]["sticker"], 5000)
        plain = inspect_link.link(defindex=7, paintindex=0, keychains=[{"slot": 0, "id": 12}])
        self.assertNotIn("sticker", inspect_link.parse_item(plain)["keychains"][0])

    def test_roundtrip(self):
        hexstr = inspect_link.encode(defindex=7, paintindex=44, paintseed=661,
                                     paintwear=0.1234, stattrak=1337, nametag="cs.weapon")
        fields, ok = inspect_link.decode(hexstr)
        self.assertTrue(ok)
        self.assertEqual(fields[inspect_link.F_DEFINDEX], 7)
        self.assertEqual(fields[inspect_link.F_PAINTINDEX], 44)
        self.assertEqual(fields[inspect_link.F_PAINTSEED], 661)
        self.assertEqual(fields[inspect_link.F_PAINTWEAR], inspect_link.float_bits(0.1234))
        self.assertEqual(fields[inspect_link.F_KILLEATERVALUE], 1337)
        self.assertEqual(fields[inspect_link.F_CUSTOMNAME], b"cs.weapon")
        self.assertTrue(hexstr.startswith("00"))

    def test_parse_item_round_trip(self):
        hexstr = inspect_link.encode(defindex=507, paintindex=38, paintseed=412, paintwear=0.0312,
                                     stattrak=42, nametag="ed3n",
                                     stickers=[{"slot": 0, "id": 4682, "wear": 0.25},
                                               {"slot": 2, "id": 5000, "rotation": 45.0}],
                                     keychains=[{"slot": 0, "id": 12, "pattern": 0}])
        for text in (hexstr, inspect_link.STEAM_URL + hexstr,
                     "csgo_econ_action_preview " + hexstr.lower()):
            item = inspect_link.parse_item(text)
            self.assertTrue(item["checksum_ok"])
            self.assertEqual((item["defindex"], item["paintindex"], item["paintseed"]), (507, 38, 412))
            self.assertAlmostEqual(item["paintwear"], 0.0312, places=6)
            self.assertEqual(item["stattrak"], 42)
            self.assertEqual(item["nametag"], "ed3n")
            self.assertEqual([st["id"] for st in item["stickers"]], [4682, 5000])
            self.assertAlmostEqual(item["stickers"][0]["wear"], 0.25)
            self.assertAlmostEqual(item["stickers"][1]["rotation"], 45.0)
            self.assertEqual(item["keychains"][0]["id"], 12)

    def test_parse_item_without_stattrak_and_masked(self):
        hexstr = inspect_link.encode(defindex=7, paintindex=0)
        item = inspect_link.parse_item(hexstr)
        self.assertIsNone(item["stattrak"])
        self.assertEqual(item["stickers"], [])
        # a non-zero first byte masks every byte with XOR
        data = bytes.fromhex(hexstr)
        masked = bytes(b ^ 0xA5 for b in data).hex()
        self.assertEqual(inspect_link.parse_item(masked)["defindex"], 7)

    def test_inventory_links_need_steam(self):
        with self.assertRaises(inspect_link.LinkError):
            inspect_link.parse_item("steam://rungame/730/76561202255233023/+csgo_econ_action_preview"
                                    "%20S76561198000000000A123456789D987654321")
        with self.assertRaises(inspect_link.LinkError):
            inspect_link.parse_item("hello")


if __name__ == "__main__":
    unittest.main()
