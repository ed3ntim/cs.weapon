# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Parser tests for tools/export_cs2_weapons.py and library/kv3.py.

All fixtures are small self-written snippets in the format of the game
files, no game data is used.
"""

import importlib.util
import json
import os
import struct
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("export_cs2_weapons",
                                               os.path.join(ROOT, "tools", "export_cs2_weapons.py"))
X = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(X)
KV3 = X.KV3

VCOMPMAT = """<!-- kv3 encoding:text:version{e21c7f3c-8a33-41c5-9977-a76d3a32aa0d} format:generic:version{7412167c-06e9-4698-aff2-e63eb59037e7} -->
{
	m_vecCompMatIncludes = [ "weapons/paints/legacy/_shared_paint_generic.vcompmat" ]
	m_vecLooseVariables =
	[
		{
			m_strName = "g_flPatternTexCoordRotation"
			m_nVariableType = "LOOSE_VARIABLE_TYPE_FLOAT1"
			m_bHasFloatBounds = true
			m_flValueFloatX = 0.0
			m_flValueFloatX_Min = 0.0
			m_flValueFloatX_Max = 360.0
		},
		{
			m_strName = "g_vPatternTexCoordOffset"
			m_nVariableType = "LOOSE_VARIABLE_TYPE_FLOAT2"
			m_bHasFloatBounds = true
			m_flValueFloatX_Min = 0.0
			m_flValueFloatX_Max = 1.0
			m_flValueFloatY_Min = 0.0
			m_flValueFloatY_Max = 1.0
		},
		{
			m_strName = "g_bIgnoreWeaponSizeScale"
			m_nVariableType = "LOOSE_VARIABLE_TYPE_BOOLEAN"
			m_bValueBoolean = false
		},
	]
	m_vecInputMaterials =
	[
		{
			m_strAlias = "paint"
			m_nContainerSourceType = "CONTAINER_SOURCE_TYPE_SPECIFIC_MATERIAL"
			m_strSpecificContainerMaterial = resource_name:"weapons/paints/legacy/test_kit.vmat"
			m_blob = #[ 01 02 ff ]
		},
	]
}
"""

VMDL = """<!-- kv3 encoding:text:version{e21c7f3c-8a33-41c5-9977-a76d3a32aa0d} format:modeldoc29:version{3cec427c-1b0e-4d48-a90a-0436f33a6041} -->
{
	rootNode =
	{
		_class = "RootNode"
		children =
		[
			{
				_class = "AttachmentList"
				children =
				[
					{
						_class = "Attachment"
						name = "stattrak"
						parent_bone = "weapon_offset"
						relative_origin = [ 1.5, -0.25, 2.0 ]
						relative_angles = [ 0.0, 90.0, 0.0 ]
					},
					{
						_class = "Attachment"
						name = "nametag"
						parent_bone = "weapon_offset"
						relative_origin = [ 3.0, 0.5, 1.0 ]
						relative_angles = [ 0.0, 0.0, 0.0 ]
					},
					{
						_class = "Attachment"
						name = "tip"
						parent_bone = "blade"
						relative_origin = [ 1.0, 0.0, 0.0 ]
						relative_angles = [ 0.0, 0.0, 0.0 ]
					},
				]
			},
			{
				_class = "Skeleton"
				children =
				[
					{
						_class = "Bone"
						name = "weapon"
						origin = [ 2.0, 0.0, 1.0 ]
						angles = [ 0.0, 0.0, 0.0 ]
						children =
						[
							{
								_class = "Bone"
								name = "weapon_offset"
								origin = [ 0.0, 0.0, 0.0 ]
								angles = [ 0.0, 0.0, 0.0 ]
							},
							{
								_class = "Bone"
								name = "blade"
								origin = [ 1.0, 0.0, 0.0 ]
								angles = [ 0.0, 90.0, 0.0 ]
							},
						]
					},
				]
			},
		]
	}
}
"""

ITEMS_GAME = """"items_game"
{
	"prefabs"
	{
		"primary" { "prefab" "weapon_base" }
		"rifle" { "prefab" "primary" }
		"weapon_ak_prefab"
		{
			"prefab"	"rifle"
			"item_name"	"#SFUI_WPNHUD_AK47"
			"model_player"	"weapons/models/ak/weapon_ak.vmdl"
			"paint_data"
			{
				"PaintableMaterial0"
				{
					"Name"	"rif_ak"
					"WeaponLength"	"37.7"
					"UVScale"	"0.55"
				}
			}
		}
		"melee_unusual" { "prefab" "melee" }
		"melee" { }
	}
	"items"
	{
		"7" { "name" "weapon_ak" "prefab" "weapon_ak_prefab" }
		"507"
		{
			"name"	"weapon_knife_test"
			"prefab"	"melee_unusual"
			"item_name"	"#KnifeTest"
			"model_player"	"weapons/models/knife/test.vmdl"
			"paint_data" { "PaintableMaterial0" { "Name" "knife_test" "WeaponLength" "9.8" "UVScale" "0.44" } }
		}
		"42" { "name" "weapon_taser" "prefab" "primary" "model_player" "x.vmdl" }
		"1000" { "name" "sticker_capsule" }
	}
	"paint_kits"
	{
		"44"
		{
			"name"	"aq_oiled"
			"description_tag"	"#PaintKit_aq_oiled_Tag"
			"style"	"8"
			"use_legacy_model"	"1"
			"wear_remap_min"	"0.000000"
			"wear_remap_max"	"1.000000"
		}
		"38" { "name" "aa_fade" "description_tag" "#PaintKit_aa_fade_Tag" "style" "6" "wear_remap_max" "0.08" }
	}
	"paint_kits_rarity" { "aq_oiled" "mythical" "aa_fade" "rare" }
	"client_loot_lists"
	{
		"crate_test" { "[aq_oiled]weapon_ak" "1" "[aa_fade]weapon_knife_test" "1" }
	}
}
"""

TOKENS = """"lang"
{
	"Language"	"english"
	"Tokens"
	{
		"SFUI_WPNHUD_AK47"	"AK-47"
		"KnifeTest"	"Test Knife"
		"PaintKit_aq_oiled_Tag"	"Case Hardened"
		// comment
		"PaintKit_aa_fade_Tag"	"Fade"
	}
}
"""

VMAT = """"Layer0"
{
	"shader"	"csgo_composite_inputs.vfx"
	"F_SEPARATE_CHANNEL_INPUTS"	"1"
	"g_flUvScale1"	"0.772"
	"g_vColorTint"	"[1.000000 0.500000 0.250000 1.000000]"
	"TextureColor1"	"weapons/x_color.png"
	"Compiled Textures"
	{
		"g_tColor"	"weapons/x_color_psd_1234.vtex"
		"g_tMasks"	"weapons\\x_masks_tga_5678.vtex"
	}
	"Attributes"
	{
		"composite_inputs"	"weapons/x_composite_inputs.vmat"
	}
}
"""


def _write(folder, rel, text):
    path = os.path.join(folder, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _glb(path, gltf):
    data = json.dumps(gltf).encode()
    data += b" " * (-len(data) % 4)
    with open(path, "wb") as fh:
        fh.write(b"glTF" + struct.pack("<II", 2, 12 + 8 + len(data)))
        fh.write(struct.pack("<I", len(data)) + b"JSON" + data)


class KV3Tests(unittest.TestCase):
    def test_vcompmat(self):
        doc = KV3.loads(VCOMPMAT)
        self.assertEqual(KV3.includes(doc), ["weapons/paints/legacy/_shared_paint_generic.vcompmat"])
        self.assertEqual(KV3.specific_materials(doc), {"paint": "weapons/paints/legacy/test_kit.vmat"})
        ranges = KV3.loose_variable_ranges(doc)
        self.assertEqual(ranges["g_flPatternTexCoordRotation"], (0.0, 360.0))
        self.assertEqual(ranges["g_vPatternTexCoordOffset"], ((0.0, 0.0), (1.0, 1.0)))
        self.assertNotIn("g_bIgnoreWeaponSizeScale", ranges)
        blob = next(KV3.walk(doc, "m_blob"))["m_blob"]
        self.assertEqual(blob, b"\x01\x02\xff")

    def test_scalars_and_errors(self):
        self.assertEqual(KV3.loads('{ a = true b = null c = -1.5e2 d = "x\\"y" e = [ 1, 2, ] }'),
                         {"a": True, "b": None, "c": -150.0, "d": 'x"y', "e": [1, 2]})
        with self.assertRaises(KV3.KV3Error):
            KV3.loads("{ a = [ 1, 2 }")

    def test_vmdl_attachments(self):
        with tempfile.TemporaryDirectory() as tmp:
            att = X.vmdl_attachments(_write(tmp, "w.vmdl", VMDL))
        self.assertEqual(set(att), {"stattrak", "nametag", "tip"})
        self.assertEqual(att["stattrak"]["origin"], [1.5, -0.25, 2.0])
        self.assertEqual(att["stattrak"]["angles"], [0.0, 90.0, 0.0])
        self.assertEqual(att["nametag"]["bone"], "weapon_offset")
        self.assertEqual(X.vmdl_attachments("does/not/exist.vmdl"), {})

        def translation(m):
            return [m[3], m[7], m[11]]

        # bone chain weapon (2, 0, 1) + attachment, no rotation
        for a, b in zip(translation(att["stattrak"]["matrix"]), [3.5, -0.25, 3.0]):
            self.assertAlmostEqual(a, b)
        # yaw 90 on the blade bone turns its local x into model y
        for a, b in zip(translation(att["tip"]["matrix"]), [3.0, 1.0, 1.0]):
            self.assertAlmostEqual(a, b)
        m = att["stattrak"]["matrix"]
        self.assertAlmostEqual(m[4], 1.0)  # yaw 90: x axis -> +y (column 0)

    def test_angle_matrix(self):
        # pitch positive looks down: forward gets a negative z
        m = X.angle_matrix([30.0, 0.0, 0.0])
        self.assertAlmostEqual(m[2][0], -0.5)
        m = X.angle_matrix([0.0, 0.0, 90.0])  # roll: y axis -> z
        self.assertAlmostEqual(m[2][1], 1.0)
        self.assertEqual(X.nametag_text(None)["font"], "Arial Narrow")


class KeyValuesTests(unittest.TestCase):
    def test_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            tokens = X.load_tokens(_write(tmp, "csgo_english.txt", TOKENS))
        self.assertEqual(X.tr(tokens, "#PaintKit_AQ_Oiled_Tag"), "Case Hardened")
        self.assertEqual(X.tr(tokens, "#missing", "fallback"), "fallback")

    def test_vmat(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec = X.parse_vmat(_write(tmp, "x.vmat", VMAT))
        self.assertEqual(spec["shader"], "csgo_composite_inputs.vfx")
        self.assertEqual(spec["params"]["F_SEPARATE_CHANNEL_INPUTS"], 1)
        self.assertAlmostEqual(spec["params"]["g_flUvScale1"], 0.772)
        self.assertEqual(spec["params"]["g_vColorTint"], [1.0, 0.5, 0.25, 1.0])
        self.assertNotIn("TextureColor1", spec["params"])
        self.assertEqual(spec["compiled"]["g_tMasks"], "weapons/x_masks_tga_5678.vtex")
        self.assertEqual(spec["attributes"]["composite_inputs"], "weapons/x_composite_inputs.vmat")
        self.assertEqual(X.tex_key("g_tAmbientOcclusion"), "ambientocclusion")


class ItemsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp:
            cls.items = X.Items(_write(tmp, "items_game.txt", ITEMS_GAME))
            cls.tokens = X.load_tokens(_write(tmp, "csgo_english.txt", TOKENS))

    def test_prefab_chain(self):
        merged, chain = self.items.resolve(self.items.items["7"])
        self.assertEqual(chain, ["weapon_ak_prefab", "rifle", "primary"])
        self.assertEqual(merged["paint_data"]["PaintableMaterial0"]["Name"], "rif_ak")
        self.assertEqual(X.weapon_type(chain), "rifle")

    def test_weapons(self):
        weapons = X.weapons_from_items(self.items, self.tokens)
        self.assertEqual(set(weapons), {"weapon_ak", "weapon_knife_test"})  # taser: no paint_data
        self.assertEqual(weapons["weapon_ak"]["name"], "AK-47")
        self.assertEqual(weapons["weapon_ak"]["def_index"], 7)
        self.assertEqual(weapons["weapon_knife_test"]["type"], "knife")
        self.assertEqual(weapons["weapon_knife_test"]["paint"]["UVScale"], "0.44")

    def test_paint_kits_and_combos(self):
        self.assertEqual(self.items.paint_kits[44]["style"], "8")
        self.assertEqual(self.items.rarity["aa_fade"], "rare")
        self.assertEqual(self.items.combos, {("weapon_ak", "aq_oiled"), ("weapon_knife_test", "aa_fade")})
        weapons = X.weapons_from_items(self.items, self.tokens)
        listing = [
            X.ICON_DIR_VPK + "weapon_ak_aq_oiled_light_png.vtex_c",
            X.ICON_DIR_VPK + "weapon_ak_aq_oiled_heavy_png.vtex_c",
            X.ICON_DIR_VPK + "weapon_ak_aa_fade_medium_png.vtex_c",  # icon only, no loot list
            X.ICON_DIR_VPK + "weapon_ak_unknown_kit_light_png.vtex_c",
        ]
        combos, icons = X.skin_combos(self.items, weapons, listing)
        self.assertEqual(combos, [("weapon_ak", "aa_fade"), ("weapon_ak", "aq_oiled"),
                                  ("weapon_knife_test", "aa_fade")])
        self.assertEqual(set(icons[("weapon_ak", "aq_oiled")]), {"light", "heavy"})


class StickerMarkupTests(unittest.TestCase):
    DATA = '''m_keyValueText = """<!-- kv3 encoding:text:version{e21c7f3c-8a33-41c5-9977-a76d3a32aa0d} format:generic:version{7412167c-06e9-4698-aff2-e63eb59037e7} -->
{
	StickerMarkup =
	[
		{ Index = 0 Mesh = "body_hd" LegacyModel = false Offset = [ 0.1, -0.3 ] Scale = 6.2 Rotation = 0.0 },
		{ Index = 0 Mesh = "body_legacy" LegacyModel = true Deprecated = true Offset = [ 0.2, -0.3 ] Scale = 5.8 },
		{ Index = 4 Mesh = "body_legacy" LegacyModel = true Offset = [ 0.21, -0.32 ] Scale = 6.4 Rotation = 0.05 },
	]
	prop_data = { base = "Weapon.Small" }
}"""
m_other = 1'''

    def test_parse(self):
        m = X.sticker_markup(self.DATA)
        self.assertEqual([e["slot"] for e in m["hd"]], [0])
        self.assertEqual(m["legacy"][0], {"slot": 0, "offset": [0.2, -0.3], "scale": 5.8, "rotation": 0.0,
                                          "deprecated": True})
        self.assertEqual((m["legacy"][1]["slot"], m["legacy"][1]["rotation"]), (4, 0.05))

    def test_keychain_markup(self):
        data = '''m_keyValueText = """{
	KeychainMarkup =
	[
		{ LegacyModel = true BoneName = "slide" Corners = [ 0.0, 0.5, 2.0, 4.0, 0.5, 2.0, 0.0, 0.5, 1.0, 4.0, 0.5, 1.0 ] },
		{ LegacyModel = true BoneName = "slide" Corners = [ 2.9, 0.4, 2.4, 3.1, 0.4, 2.4, 2.9, 0.6, 2.2, 3.1, 0.6, 2.2 ] DefaultLocation = true },
		{ BoneName = "magazine" Corners = [ 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1 ] },
	]
}"""'''
        m = X.keychain_markup(data)
        self.assertNotIn("hd", m)  # the hd entry has 11 values
        self.assertEqual([a["default"] for a in m["legacy"]], [False, True])
        self.assertEqual(m["legacy"][0]["corners"][1], [4.0, 0.5, 2.0])
        att = X.keychain_attachment(m["legacy"])
        self.assertEqual([round(v, 6) for v in att["origin"]], [3.0, 0.5, 2.3])
        self.assertEqual(att["matrix"][3::4][:3], att["origin"])
        self.assertIsNone(X.keychain_attachment(m["legacy"][:1]))

    def test_no_markup(self):
        self.assertEqual(X.sticker_markup(""), {})
        self.assertEqual(X.sticker_markup('m_keyValueText = """{ prop_data = { } }"""'), {})


class PathTests(unittest.TestCase):
    def test_raw_path(self):
        raw = os.path.join("r")
        self.assertEqual(X.raw_path(raw, "a/b/c.vmat_c", ".vmat"), os.path.join("r", "a", "b", "c.vmat"))
        self.assertEqual(X.raw_path(raw, "a/m.vmdl", ".glb"), os.path.join("r", "a", "m.glb"))
        self.assertEqual(X.raw_path(raw, "scripts/items_game.txt", ".txt"),
                         os.path.join("r", "scripts", "items_game.txt"))

    def test_place_rig(self):
        import types
        with tempfile.TemporaryDirectory() as tmp:
            ex = X.Exporter(types.SimpleNamespace(out=tmp, threads=1))
            model = "agents/models/shared/arms/glove_handwrap/glove_handwrap.vmdl"
            glb = os.path.join(tmp, "gloves", "leather_handwraps", "glove_handwrap.glb")
            self.assertEqual(ex.place_rig(model, glb, "gloves/leather_handwraps"), "")
            rig = X.raw_path(ex.rig_raw(), model, ".glb")
            os.makedirs(os.path.dirname(rig))
            with open(rig, "wb") as fh:
                fh.write(b"glTF")
            self.assertEqual(ex.place_rig(model, glb, "gloves/leather_handwraps"),
                             "gloves/leather_handwraps/glove_handwrap.rig.glb")
            self.assertTrue(os.path.isfile(os.path.join(tmp, "gloves", "leather_handwraps",
                                                        "glove_handwrap.rig.glb")))

    def test_glb_meshes(self):
        gltf = {
            "asset": {"version": "2.0"},
            "materials": [{"name": "a", "extras": {"vmat": {"Name": "weapons\\ak\\ak_hd.vmat"}}},
                          {"name": "b", "extras": {"vmat": {"Name": "weapons/v_models/ak_legacy.vmat"}}}],
            "meshes": [{"primitives": [{"material": 0}]}, {"primitives": [{"material": 1}]}],
            "nodes": [{"name": "root"}, {"name": "weapons\\ak.vmdl_c.body_hd", "mesh": 0},
                      {"name": "weapons\\ak.vmdl_c.body_legacy", "mesh": 1}],
            "images": [{"uri": "ak_color.png"}, {"uri": "../outside.png"}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ak.glb")
            _glb(path, gltf)
            self.assertEqual(X.glb_meshes(path), [("body_hd", "weapons/ak/ak_hd.vmat"),
                                                  ("body_legacy", "weapons/v_models/ak_legacy.vmat")])
            self.assertEqual(X.glb_images(path), ["ak_color.png"])


class TextureSizeTests(unittest.TestCase):
    def test_scaled_size(self):
        self.assertIsNone(X.scaled_size((4096, 4096), 0))
        self.assertIsNone(X.scaled_size((1024, 512), 2048))
        self.assertEqual(X.scaled_size((4096, 4096), 2048), (2048, 2048))
        self.assertEqual(X.scaled_size((4096, 1024), 2048), (2048, 512))

    def test_copy_texture(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("pillow not installed")
        with tempfile.TemporaryDirectory() as tmp:
            src, dst, exr = (os.path.join(tmp, n) for n in ("a.png", "b.png", "c.exr"))
            Image.new("RGBA", (64, 32), (128, 127, 255, 0)).save(src)  # normal map, alpha 0
            X.copy_texture(src, dst, 16)
            with Image.open(dst) as im:
                self.assertEqual((im.size, im.mode), ((16, 8), "RGBA"))
                self.assertEqual(im.getpixel((5, 5)), (128, 127, 255, 0))
            X.copy_texture(src, dst, 0)
            with Image.open(dst) as im:
                self.assertEqual(im.size, (64, 32))
            with open(exr, "wb") as fh:
                fh.write(b"not decoded")
            X.copy_texture(exr, dst + ".exr", 16)
            with open(dst + ".exr", "rb") as fh:
                self.assertEqual(fh.read(), b"not decoded")


if __name__ == "__main__":
    unittest.main()


_shader_spec = importlib.util.spec_from_file_location("cs2shader", os.path.join(ROOT, "tools", "cs2shader.py"))
SH = importlib.util.module_from_spec(_shader_spec)
_shader_spec.loader.exec_module(SH)


class ShaderNamingTests(unittest.TestCase):
    SRC = """struct _1
{
    vec4 _m0[3];
};

struct _2
{
    uint _m0;
    float _m1;
    vec2 _m2;
};

layout(set = 1) uniform _1 _10;
layout(set = 1) uniform _2 _20;

void main()
{
    float a = _20._m1 + _20._m2.x + float(_20._m0) + _10._m0[0].x;
}
"""

    def test_material_block_is_the_one_with_matching_members(self):
        # The weapon shader has several blocks; the first struct is not the material.
        main = self.SRC[self.SRC.index("void main()"):]
        members, inst = SH._material_block(self.SRC, main, 3)
        self.assertEqual(inst, "_20")
        self.assertEqual([m[1] for m in members], ["_m0", "_m1", "_m2"])
        self.assertEqual(SH._material_block(self.SRC, main, 7), (None, None))
