# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Composite material procedure: evaluation (tools/compmat.py), applying
it to a weapon (library/procedure.py), data-driven seed rolls and the
GLSL switch statement. Fixtures are hand-written, no game data."""

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
import seed  # noqa: E402


def loose(name, vtype, **kw):
    return {"m_strName": name, "m_nVariableType": f"LOOSE_VARIABLE_TYPE_{vtype}", **kw}


def container(alias, kind, variables=(), material=""):
    return {"m_strAlias": alias, "m_nCompositeMaterialInputContainerSourceType": f"CONTAINER_SOURCE_TYPE_{kind}",
            "m_strSpecificContainerMaterial": material, "m_vecLooseVariables": list(variables)}


def mutator(kind, **kw):
    return {"m_nMutatorCommandType": f"COMP_MAT_PROPERTY_MUTATOR_{kind}", **kw}


def cond(kind, alias, var, value=""):
    return {"m_nMutatorCondition": f"COMP_MAT_MUTATOR_CONDITION_{kind}",
            "m_strMutatorConditionContainerName": alias,
            "m_strMutatorConditionContainerVarName": var,
            "m_strMutatorConditionContainerVarValue": value}


def doc(containers, mutators, includes=()):
    proc = {"m_vecCompositeInputContainers": containers, "m_vecPropertyMutators": mutators,
            "m_vecCompMatIncludes": list(includes)}
    return {"m_Points": [{"m_vecCompositeMaterialAssemblyProcedures": [proc]}]}


SHARED = doc([
    container("grunge_wear", "LOOSE_VARIABLES", [
        loose("g_flWearTexCoordScale", "FLOAT1", m_flValueFloatX=1.6, m_bHasFloatBounds=True,
              m_flValueFloatX_Min=1.6, m_flValueFloatX_Max=1.8),
    ]),
], [
    mutator("INIT"),
    mutator("GENERATE_TEXTURE", m_strGenerateTexture_TargetParam="g_tColor",
            m_strGenerateTexture_InitialContainer="paint",
            m_vecTexGenInstructions=[
                mutator("COPY_MATCHING_KEYS", m_strCopyMatchingKeys_InputContainerSrc="composite_inputs"),
                mutator("COPY_MATCHING_KEYS", m_strCopyMatchingKeys_InputContainerSrc="econ_instance"),
                mutator("COPY_MATCHING_KEYS", m_strCopyMatchingKeys_InputContainerSrc="grunge_wear"),
                mutator("SET_VALUE", m_nSetValue_Value=loose("F_SEPARATE_CHANNEL_INPUTS", "BOOLEAN",
                                                               m_bValueBoolean=True),
                        m_vecConditions=[cond("VALUE_EQUALS", "composite_inputs", "F_SEPARATE_CHANNEL_INPUTS", "1")]),
            ]),
    mutator("GENERATE_TEXTURE", m_strGenerateTexture_TargetParam="g_tMetalness",
            m_strGenerateTexture_InitialContainer="paint",
            m_vecTexGenInstructions=[
                mutator("COPY_MATCHING_KEYS", m_strCopyMatchingKeys_InputContainerSrc="composite_inputs"),
                mutator("COPY_PROPERTY", m_strCopyProperty_InputContainerSrc="target",
                        m_strCopyProperty_InputContainerProperty="g_tMetalness"),
                mutator("SET_VALUE", m_nSetValue_Value=loose("F_ROUGHNESS_MODE", "INTEGER", m_nValueIntX=1)),
            ]),
    mutator("COPY_PROPERTY", m_strCopyProperty_InputContainerSrc="paint",
            m_strCopyProperty_InputContainerProperty="g_tNormalOverride",
            m_strCopyProperty_TargetProperty="g_tNormal"),
    mutator("CONDITIONAL_MUTATORS", m_vecConditionalMutators=[
        mutator("RANDOM_ROLL_INPUT_VARIABLES", m_vecRandomRollInputVars_InputVarsToRoll=[
            "econ_instance.g_vPatternTexCoordOffset.xy",
            "econ_instance.g_flPatternTexCoordRotation.x",
            "grunge_wear.g_flWearTexCoordScale.x",
        ]),
    ]),
])

KIT = doc([
    container("paint", "SPECIFIC_MATERIAL", material="paints/kit.vmat"),
    container("econ_instance", "LOOSE_VARIABLES", [
        loose("g_flPatternTexCoordRotation", "FLOAT1", m_flValueFloatX=-55.0, m_bHasFloatBounds=True,
              m_flValueFloatX_Min=-55.0, m_flValueFloatX_Max=-65.0),
        loose("g_vColor0", "COLOR4", m_cValueColor4=[255, 0, 51, 255]),
    ]),
    container("exposed", "LOOSE_VARIABLES", [
        loose("g_bOverride", "BOOLEAN", m_bValueBoolean=False),
        loose("g_tPaintByNumberMasks", "RESOURCE_TEXTURE", m_strTextureRuntimeResourcePath="paints/m.vtex"),
    ]),
], [], includes=["shared.vcompmat"])

MATERIALS = {"paints/kit.vmat": {"F_PAINT_STYLE": 5, "g_vPatternTexCoordOffset": [-0.7, -0.7, 0, 0],
                                 "g_tPattern": "paints/fade.vtex", "g_tNormalOverride": "paints/n.vtex"}}


def evaluate(kit=KIT):
    return compmat.evaluate(kit, lambda p: SHARED if p == "shared.vcompmat" else None,
                            lambda m: MATERIALS.get(m, {}))


class CompMatTests(unittest.TestCase):
    def test_passes_and_final(self):
        r = evaluate()
        self.assertEqual(set(r["passes"]), {"g_tColor", "g_tMetalness"})
        color = r["passes"]["g_tColor"]
        self.assertEqual(color[0]["set"]["g_tPattern"], "paints/fade.vtex")
        self.assertEqual(color[1], {"weapon": True})
        self.assertEqual(color[2]["set"]["g_vColor0"], [1.0, 0.0, 0.2, 1.0])
        self.assertEqual(color[-1]["if"], ["composite_inputs", "F_SEPARATE_CHANNEL_INPUTS", "1"])
        metal = r["passes"]["g_tMetalness"]
        self.assertEqual(metal[2]["set"]["g_tMetalness"], {"ref": "target", "name": "g_tMetalness"})
        self.assertEqual(r["final"], [{"set": {"g_tNormal": "paints/n.vtex"}}])
        self.assertEqual(compmat.textures(r), {"paints/fade.vtex", "paints/n.vtex"})

    def test_roll_list(self):
        roll = evaluate()["roll"]
        self.assertEqual(roll[0], ["g_vPatternTexCoordOffset", 2, None, None])
        self.assertEqual(roll[1], ["g_flPatternTexCoordRotation", 1, [-55.0, -65.0], -55.0])
        self.assertEqual(roll[2], ["g_flWearTexCoordScale", 1, [1.6, 1.8], 1.6])

    def test_own_mutators_follow_included(self):
        extra = doc(KIT["m_Points"][0]["m_vecCompositeMaterialAssemblyProcedures"][0]["m_vecCompositeInputContainers"],
                    [mutator("SET_VALUE", m_nSetValue_Value=loose("g_flPearlescentScale", "FLOAT1",
                                                                  m_flValueFloatX=0.6))],
                    includes=["shared.vcompmat"])
        r = evaluate(extra)
        self.assertEqual(set(r["passes"]), {"g_tColor", "g_tMetalness"})
        self.assertEqual(r["final"][-1], {"set": {"g_flPearlescentScale": 0.6}})

    def test_conditions(self):
        ev = compmat._Evaluator(compmat.collect(KIT, lambda p: SHARED)[0], lambda m: {})
        self.assertFalse(ev.condition([cond("VALUE_EQUALS", "exposed", "g_bOverride", "1")]))
        self.assertTrue(ev.condition([cond("VALUE_EQUALS", "exposed", "g_bOverride", "0")]))
        self.assertTrue(ev.condition([cond("VALUE_EXISTS", "exposed", "g_tPaintByNumberMasks")]))
        self.assertEqual(ev.condition([cond("VALUE_EXISTS", "target", "g_tX")]), ["target", "g_tX", None])


class ProcedureTests(unittest.TestCase):
    WEAPON = {"composite_inputs": {"F_SEPARATE_CHANNEL_INPUTS": 1, "g_flUvScale1": 0.772, "g_tMasks": "m.png"},
              "target": {"g_tMetalness": "metal.png"}}

    def test_resolve(self):
        r = evaluate()
        color = procedure.resolve(r["passes"]["g_tColor"], self.WEAPON)
        self.assertEqual(color["g_flUvScale1"], 0.772)
        self.assertIs(color["F_SEPARATE_CHANNEL_INPUTS"], True)
        metal = procedure.resolve(r["passes"]["g_tMetalness"], self.WEAPON)
        self.assertEqual(metal["g_tMetalness"], "metal.png")
        self.assertEqual(metal["F_ROUGHNESS_MODE"], 1)
        legacy = procedure.resolve(r["passes"]["g_tColor"], {"composite_inputs": {"F_SEPARATE_CHANNEL_INPUTS": 0}})
        self.assertEqual(legacy["F_SEPARATE_CHANNEL_INPUTS"], 0)

    def test_combo_id(self):
        index = {"S_PAINT_STYLE": 1, "S_SEPARATE_CHANNEL_INPUTS": 288, "S_OVERLAY_TEXTURE": 576}
        cmax = {"S_PAINT_STYLE": 8, "S_SEPARATE_CHANNEL_INPUTS": 1, "S_OVERLAY_TEXTURE": 1}
        params = {"F_PAINT_STYLE": 5, "F_SEPARATE_CHANNEL_INPUTS": True, "F_OVERLAY_TEXTURE": 0}
        self.assertEqual(procedure.combo_id(params, index, cmax), 293)
        self.assertEqual(procedure.combo_id({"F_PAINT_STYLE": 12}, index, cmax), 8)

    def test_matches(self):
        self.assertTrue(procedure.matches(1, "1"))
        self.assertTrue(procedure.matches(True, "true"))
        self.assertFalse(procedure.matches(0.5, "1"))
        self.assertTrue(procedure.matches("A", "a"))


class RollTests(unittest.TestCase):
    def test_roll_kit_matches_roll_paint(self):
        bounds = {"g_vPatternTexCoordOffset": ((0.0, 0.0), (1.0, 1.0)), "g_flPatternTexCoordRotation": (0.0, 360.0)}
        roll = []
        for name, n, dmin, dmax in seed.ROLL_ORDER:
            mins, maxs = bounds.get(name, (dmin, dmax))
            roll.append([name, n, [list(mins), list(maxs)] if n > 1 else [mins[0] if isinstance(mins, tuple) else mins,
                                                                          maxs[0] if isinstance(maxs, tuple) else maxs],
                         None])
        for sd in (0, 1, 661, 1000):
            self.assertEqual(seed.roll_kit(sd, roll), seed.roll_paint(sd, bounds))

    def test_unbounded_keeps_default_and_consumes_roll(self):
        a = seed.roll_kit(5, [["a", 2, None, [-0.7, -0.7]], ["b", 1, [0.0, 1.0], 0.0]])
        b = seed.roll_kit(5, [["x", 2, [[0, 0], [1, 1]], None], ["b", 1, [0.0, 1.0], 0.0]])
        self.assertEqual(a["a"], (-0.7, -0.7))
        self.assertEqual(a["b"], b["b"])
        self.assertNotIn("z", seed.roll_kit(5, [["z", 1, None, None]]))


class CatalogV2Tests(unittest.TestCase):
    def test_paths_made_absolute(self):
        data = {"version": 2, "shaders": "shaders/index.json",
                "weapons": [{"id": "w", "hd": {"mesh": "m", "composite_inputs": {"g_tMasks": "w/m.png", "g_flUvScale1": 0.7},
                                                "target": {"g_tMetalness": "w/metal.png"}}}],
                "paint_kits": [{"id": 1, "passes": {"g_tColor": [{"set": {"g_tPattern": "p/a.png", "F_PAINT_STYLE": 5}},
                                                                  {"weapon": True}]},
                                "final": [{"set": {"g_tNormal": "p/n.png"}}],
                                "roll": [["g_flPatternTexCoordRotation", 1, [0, 360], 0]]}],
                "skins": [{"weapon": "w", "paint_kit": 1}]}
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "catalog.json"), "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            cat = C.Catalog().load(d)
            kit, var = cat.paint_kits[1], cat.weapons["w"].hd
            self.assertEqual(kit.shaders, os.path.join(d, "shaders/index.json"))
            self.assertEqual(kit.passes["g_tColor"][0]["set"]["g_tPattern"], os.path.join(d, "p/a.png"))
            self.assertEqual(kit.passes["g_tColor"][0]["set"]["F_PAINT_STYLE"], 5)
            self.assertEqual(var.composite_inputs["g_tMasks"], os.path.join(d, "w/m.png"))
            self.assertEqual(var.target["g_tMetalness"], os.path.join(d, "w/metal.png"))
            self.assertEqual(len(kit.missing_files()), 2)
            self.assertEqual(kit.roll[0][0], "g_flPatternTexCoordRotation")


if __name__ == "__main__":
    unittest.main()
