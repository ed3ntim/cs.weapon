# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Gloves: per-hand conditions and rolls of the composite procedure
(tools/compmat.py, library/procedure.py), glove catalog entries and the
derived glove shader uniforms. Fixtures are hand-written, no game data."""

import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "cs_weapon", "library"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import catalog as C  # noqa: E402
import compmat  # noqa: E402
import procedure  # noqa: E402
from test_procedure import container, doc, loose, mutator  # noqa: E402


def cond(alias, var, value, passes=True):
    c = {"m_nMutatorCondition": "COMP_MAT_MUTATOR_CONDITION_INPUT_CONTAINER_VALUE_EQUALS",
         "m_strMutatorConditionContainerName": alias,
         "m_strMutatorConditionContainerVarName": var,
         "m_strMutatorConditionContainerVarValue": value}
    if not passes:
        c["m_bPassWhenTrue"] = False
    return c


def attr_container(alias, attr):
    c = container(alias, "VARIABLE_FROM_TARGET_ATTR")
    c["m_strAttrNameForVar"] = attr
    return c


ROLLED = ["instance_params.g_fPatternTexCoordOffset.xy", "instance_params.g_fPatternTexCoordRotation.x"]

GLOVE = doc([
    container("econ_instance", "LOOSE_VARIABLES", [loose("g_nRandomSeed", "INTEGER1"),
                                                   loose("g_nRandomSeedAlt", "INTEGER1")]),
    container("paint", "SPECIFIC_MATERIAL", material="gloves/paints/kit.vmat"),
    attr_container("handedness", "left"),
    attr_container("material_seed", "seed_offset"),
    container("instance_params", "LOOSE_VARIABLES", [
        loose("g_fPatternTexCoordOffset", "FLOAT2", m_bHasFloatBounds=True, m_flValueFloatX_Max=1.0,
              m_flValueFloatY_Max=1.0),
        loose("g_fPatternTexCoordRotation", "FLOAT1", m_bHasFloatBounds=True, m_flValueFloatX_Max=360.0),
    ]),
], [
    mutator("RANDOM_ROLL_INPUT_VARIABLES", m_strRandomRollInputVars_SeedInputVar="econ_instance.g_nRandomSeed",
            m_vecRandomRollInputVars_InputVarsToRoll=ROLLED,
            m_vecConditions=[cond("material_seed", "seed_offset", "1", passes=False)]),
    mutator("RANDOM_ROLL_INPUT_VARIABLES", m_strRandomRollInputVars_SeedInputVar="econ_instance.g_nRandomSeedAlt",
            m_vecRandomRollInputVars_InputVarsToRoll=ROLLED,
            m_vecConditions=[cond("material_seed", "seed_offset", "1")]),
    mutator("INIT"),
    mutator("GENERATE_TEXTURE", m_strGenerateTexture_TargetParam="g_tColor",
            m_strGenerateTexture_InitialContainer="paint",
            m_vecTexGenInstructions=[
                mutator("SET_VALUE", m_nSetValue_Value=loose("F_OUTPUT_MODE", "INTEGER1", m_nValueIntX=0)),
                # left hand, paint without flip: flip
                mutator("SET_VALUE", m_nSetValue_Value=loose("g_bFlipFixup", "BOOLEAN", m_bValueBoolean=True),
                        m_vecConditions=[cond("handedness", "left", "1"),
                                         cond("paint", "g_bFlipFixup", "true", passes=False)]),
                # left hand, paint with flip: no flip
                mutator("SET_VALUE", m_nSetValue_Value=loose("g_bFlipFixup", "BOOLEAN", m_bValueBoolean=False),
                        m_vecConditions=[cond("handedness", "left", "1"),
                                         cond("paint", "g_bFlipFixup", "true")]),
            ]),
])

LEFT = {"attributes": {"left": "1"}}
RIGHT = {"attributes": {"seed_offset": "1"}}


def evaluate(flip):
    return compmat.evaluate(GLOVE, lambda p: None,
                            lambda m: {"g_bFlipFixup": flip, "g_tLayerMask": "gloves/mask.vtex"})


class GloveProcedureTests(unittest.TestCase):
    def test_hand_conditions(self):
        for flip in (0, 1):
            ops = evaluate(flip)["passes"]["g_tColor"]
            left, right = procedure.resolve(ops, LEFT), procedure.resolve(ops, RIGHT)
            self.assertEqual(bool(left["g_bFlipFixup"]), not flip)
            self.assertEqual(bool(right["g_bFlipFixup"]), bool(flip))  # the paint's own value
            self.assertEqual(left["g_tLayerMask"], "gloves/mask.vtex")

    def test_attribute_condition_form(self):
        ops = evaluate(0)["passes"]["g_tColor"]
        self.assertEqual(ops[-1]["if"], ["attributes", "left", "1"])

    def test_negated_condition(self):
        self.assertTrue(procedure.condition(["attributes", "seed_offset", "1", True], LEFT))
        self.assertFalse(procedure.condition(["attributes", "seed_offset", "1", True], RIGHT))
        self.assertTrue(procedure.condition(["attributes", "seed_offset", "1"], RIGHT))

    def test_rolls_per_hand(self):
        r = evaluate(0)
        self.assertEqual([x["seed"] for x in r["rolls"]], ["g_nRandomSeed", "g_nRandomSeedAlt"])
        self.assertEqual(r["rolls"][0]["if"], ["attributes", "seed_offset", "1", True])
        self.assertEqual(r["roll"], r["rolls"][0]["roll"])
        self.assertEqual([x[0] for x in r["roll"]], ["g_fPatternTexCoordOffset", "g_fPatternTexCoordRotation"])


RESOURCE_KIT = doc([
    container("paint", "LOOSE_VARIABLES", [loose("m_strResourceMaterial", "RESOURCE_MATERIAL",
                                                 m_strResourceMaterial="items/kit.vmat")]),
    container("paint_compositor", "SPECIFIC_MATERIAL", material="templates/glove_compositor.vmat"),
], [
    mutator("INIT"),
    mutator("GENERATE_TEXTURE", m_strGenerateTexture_TargetParam="g_tColor",
            m_strGenerateTexture_InitialContainer="paint_compositor",
            m_vecTexGenInstructions=[
                mutator("COPY_MATCHING_KEYS", m_strCopyMatchingKeys_InputContainerSrc="paint"),
                mutator("SET_VALUE", m_nSetValue_Value=loose("F_TINT_ID", "INTEGER1", m_nValueIntX=0),
                        m_vecConditions=[cond("paint", "F_TINT_ID", "0")]),
            ]),
])


class ResourceMaterialTests(unittest.TestCase):
    def test_loose_resource_material_is_a_material_container(self):
        mats = {"items/kit.vmat": {"g_fPaintThickness": 0.5, "F_TINT_ID": 0},
                "templates/glove_compositor.vmat": {"g_fPaintThickness": 1.0, "F_TINT_ID": 1}}
        r = compmat.evaluate(RESOURCE_KIT, lambda p: None, lambda m: mats.get(m, {}))
        params = procedure.resolve(r["passes"]["g_tColor"], LEFT)
        self.assertEqual(params["g_fPaintThickness"], 0.5)  # copied from the kit material
        self.assertEqual(params["F_TINT_ID"], 0)  # condition read the kit material
        self.assertNotIn("m_strResourceMaterial", params)

    def test_specific_materials_lists_resource_material(self):
        sys.path.insert(0, os.path.join(ROOT, "cs_weapon", "library"))
        import kv3
        self.assertEqual(kv3.specific_materials(RESOURCE_KIT),
                         {"paint": "items/kit.vmat", "paint_compositor": "templates/glove_compositor.vmat"})


class GloveCatalogTests(unittest.TestCase):
    def test_glove_entries(self):
        data = {"version": 2, "shaders": "shaders/index.json", "glove_shaders": "shaders_glove/index.json",
                "weapons": [
                    {"id": "weapon_x", "hd": {"mesh": "m"}},
                    {"id": "sporty_gloves", "def_index": 5030, "name": "Sport Gloves", "type": "gloves",
                     "model": "g/m.glb", "rig": "g/m.rig.glb",
                     "glove": {"mesh": "viewmodel", "arm": "bare_arm_133", "materials": {
                         "glove_l": {"attributes": {"left": "1"}, "textures": {"g_tColor": "g/c.png"}},
                         "glove_r": {"attributes": {"seed_offset": "1"}, "textures": {}}}}}],
                "paint_kits": [{"id": 1, "passes": {}}, {"id": 10006, "glove": True, "passes": {},
                                                         "rolls": [{"seed": "g_nRandomSeed", "if": None, "roll": []}]}],
                "skins": [{"weapon": "weapon_x", "paint_kit": 1}, {"weapon": "sporty_gloves", "paint_kit": 10006}]}
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "catalog.json"), "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            cat = C.Catalog().load(d)
            g = cat.weapons["sporty_gloves"]
            self.assertTrue(g.is_glove)
            self.assertEqual(g.rig_path(), os.path.join(d, "g/m.rig.glb"))
            self.assertFalse(cat.weapons["weapon_x"].is_glove)
            self.assertEqual(g.glove.hand("glove_l"), "left")
            self.assertEqual(g.glove.hand("glove_r"), "right")
            self.assertEqual(g.glove.materials["glove_l"]["textures"]["g_tColor"], os.path.join(d, "g/c.png"))
            self.assertEqual(cat.paint_kits[10006].shaders, os.path.join(d, "shaders_glove/index.json"))
            self.assertEqual(cat.paint_kits[1].shaders, os.path.join(d, "shaders/index.json"))
            self.assertTrue(cat.paint_kits[10006].glove)
            self.assertEqual(cat.paint_kits[10006].rolls[0]["seed"], "g_nRandomSeed")
            self.assertIn("sporty_gloves:10006", cat.skins)
            self.assertNotIn("sporty_gloves:0", cat.skins)  # no vanilla gloves
            self.assertIn("weapon_x:0", cat.skins)
            self.assertIn("gloves", cat.weapon_types())


if __name__ == "__main__":
    unittest.main()
