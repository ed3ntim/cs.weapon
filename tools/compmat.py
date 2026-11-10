# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Evaluate a composite material (.vcompmat) assembly procedure.

A paint kit's vcompmat describes how the game builds the skinned weapon
material: input containers (loose variables, the paint material, the
weapon's composite inputs, the weapon material itself) and property
mutators that generate the albedo and roughness textures with the
composite shader and set properties of the final material.

``evaluate`` turns that into plain data for the catalog. Everything that
depends on the weapon stays symbolic, so one result serves every weapon:

    {"passes": {"g_tColor": [op, ...], "g_tMetalness": [op, ...]},
     "final": [op, ...], "roll": [[var, n, bounds, default], ...], ...}

with ops applied in order by the add-on:

    {"set": {name: value, ...}, "if": [container, var, value]}   # "if" optional
    {"weapon": true}        # copy the weapon's composite inputs here

Values are numbers, lists, booleans, texture paths (VPK paths of .vtex,
keys starting with ``g_t``) or ``{"ref": container, "name": var}`` for a
property of a weapon-side container (``target``: the weapon material).
Conditions left in ops refer to weapon-side containers; all others are
decided here. Containers read from an attribute of the target material
(``CONTAINER_SOURCE_TYPE_VARIABLE_FROM_TARGET_ATTR``, e.g. ``left`` and
``seed_offset`` of the glove materials) become conditions on the
``attributes`` container: ``["attributes", attr, value]``. A fourth
element ``true`` negates the condition (``m_bPassWhenTrue = false``).

``roll`` lists the variables the pattern seed rolls, in roll order: name,
component count, float bounds ([min, max], lists per component for
vectors; None when unbounded: the roll is consumed, the value stays) and
the value before rolling. ``rolls`` holds every roll mutator:
``{"seed": "g_nRandomSeed", "if": condition or None, "roll": [...]}``
(gloves roll the right hand with ``g_nRandomSeedAlt``); ``roll`` is the
first of them. The add-on applies the ops with
cs_weapon/library/procedure.resolve.
"""

WEAPON_CONTAINERS = {"composite_inputs", "target", "target_instance"}
ATTR_CONTAINER = "attributes"
TARGET_ATTR_TYPE = "CONTAINER_SOURCE_TYPE_VARIABLE_FROM_TARGET_ATTR"
VAR_TYPES = {
    "LOOSE_VARIABLE_TYPE_FLOAT1": 1, "LOOSE_VARIABLE_TYPE_FLOAT2": 2,
    "LOOSE_VARIABLE_TYPE_FLOAT3": 3, "LOOSE_VARIABLE_TYPE_FLOAT4": 4,
}


class CompMatError(ValueError):
    pass


def loose_value(var):
    """Value of a loose variable as plain data (None if unsupported)."""
    vtype = str(var.get("m_nVariableType", ""))
    if vtype in VAR_TYPES:
        vals = [float(var.get(f"m_flValueFloat{c}", 0.0)) for c in "XYZW"[:VAR_TYPES[vtype]]]
        return vals[0] if len(vals) == 1 else vals
    if vtype == "LOOSE_VARIABLE_TYPE_COLOR4":
        c = [float(x) / 255.0 for x in var.get("m_cValueColor4", [255, 255, 255])]
        return (c + [1.0] * 4)[:4]
    if vtype == "LOOSE_VARIABLE_TYPE_BOOLEAN":
        return bool(var.get("m_bValueBoolean", False))
    if vtype.startswith("LOOSE_VARIABLE_TYPE_INTEGER"):
        return int(var.get("m_nValueIntX", 0))
    if vtype == "LOOSE_VARIABLE_TYPE_RESOURCE_TEXTURE":
        path = var.get("m_strTextureRuntimeResourcePath") or ""
        return str(path).replace("\\", "/") or None
    return None


def _bounds(var):
    if not var.get("m_bHasFloatBounds"):
        return None
    n = VAR_TYPES.get(str(var.get("m_nVariableType", "")))
    if not n:
        return None
    lo = [float(var.get(f"m_flValueFloat{c}_Min", 0.0)) for c in "XYZW"[:n]]
    hi = [float(var.get(f"m_flValueFloat{c}_Max", 0.0)) for c in "XYZW"[:n]]
    return [lo[0], hi[0]] if n == 1 else [lo, hi]


def _procedures(doc):
    for point in doc.get("m_Points", []) or []:
        for proc in point.get("m_vecCompositeMaterialAssemblyProcedures", []) or []:
            yield proc


def collect(doc, load, depth=0):
    """Containers (alias -> dict) and mutators of a vcompmat including its
    includes. ``load(path)`` returns a parsed included vcompmat. The
    including file wins: same-alias containers merge their variables,
    its own mutators run after the included ones (one kit only adds a
    SET_VALUE to the shared procedure)."""
    if depth > 8:
        raise CompMatError("include depth")
    containers, mutators = {}, []
    for proc in _procedures(doc):
        for inc in proc.get("m_vecCompMatIncludes", []) or []:
            sub = load(str(inc))
            if sub is not None:
                c2, m2 = collect(sub, load, depth + 1)
                _merge_containers(containers, c2)
                mutators = mutators + m2
        own = {}
        for c in proc.get("m_vecCompositeInputContainers", []) or []:
            alias = str(c.get("m_strAlias", ""))
            entry = {
                "type": str(c.get("m_nCompositeMaterialInputContainerSourceType", "")),
                "material": str(c.get("m_strSpecificContainerMaterial", "") or "").replace("\\", "/"),
                "attr": str(c.get("m_strAttrName", "") or c.get("m_strAttrNameForVar", "") or ""),
                "vars": {}, "bounds": {},
            }
            for var in c.get("m_vecLooseVariables", []) or []:
                name = str(var.get("m_strName", ""))
                if var.get("m_nVariableType") == "LOOSE_VARIABLE_TYPE_RESOURCE_MATERIAL":
                    # the container stands for that material, like SPECIFIC_MATERIAL
                    entry["material"] = str(var.get("m_strResourceMaterial", "") or "").replace("\\", "/")
                    continue
                if not name:
                    continue
                entry["vars"][name] = loose_value(var)
                b = _bounds(var)
                if b is not None:
                    entry["bounds"][name] = b
            own[alias] = entry
        _merge_containers(containers, own)
        mutators = mutators + list(proc.get("m_vecPropertyMutators") or [])
    return containers, mutators


def _merge_containers(dst, src):
    for alias, c in src.items():
        if alias in dst:
            old = dst[alias]
            merged = dict(c)
            merged["vars"] = {**old["vars"], **c["vars"]}
            merged["bounds"] = {**old["bounds"], **c["bounds"]}
            if not c["material"]:
                merged["material"] = old["material"]
            if not c["type"]:
                merged["type"] = old["type"]
            dst[alias] = merged
        else:
            dst[alias] = c


def _matches(value, want):  # same as cs_weapon/library/procedure.matches
    want = str(want).strip().lower()
    if isinstance(value, bool):
        return value == (want in ("1", "true"))
    if isinstance(value, (int, float)):
        try:
            return float(value) == float(want)
        except ValueError:
            return want in ("true",) and bool(value)
    return str(value).lower() == want


class _Evaluator:
    def __init__(self, containers, material_vars):
        self.c = containers
        self.material_vars = material_vars  # vmat path -> {name: value}

    def vars_of(self, alias):
        c = self.c.get(alias)
        if c is None:
            return None
        if c["material"]:
            return {**self.material_vars(c["material"]), **c["vars"]}
        return c["vars"]

    def condition(self, conds):
        """True, False, or the condition list for the add-on to decide."""
        pending = []
        for cond in conds or []:
            kind = str(cond.get("m_nMutatorCondition", ""))
            alias = str(cond.get("m_strMutatorConditionContainerName", ""))
            var = str(cond.get("m_strMutatorConditionContainerVarName", ""))
            value = str(cond.get("m_strMutatorConditionContainerVarValue", ""))
            negate = cond.get("m_bPassWhenTrue", True) is False
            if (self.c.get(alias) or {}).get("type") == TARGET_ATTR_TYPE:
                alias, var = ATTR_CONTAINER, self.c[alias]["attr"] or var
            if alias in WEAPON_CONTAINERS or alias == ATTR_CONTAINER:
                entry = [alias, var, None if kind.endswith("VALUE_EXISTS") else value]
                pending.append(entry + [True] if negate else entry)
                continue
            vs = self.vars_of(alias)
            if kind.endswith("VALUE_EXISTS"):
                ok = vs is not None and vs.get(var) is not None
            elif kind.endswith("VALUE_EQUALS"):
                ok = vs is not None and var in vs and _matches(vs[var], value)
            else:
                raise CompMatError(f"condition {kind}")
            if negate:
                ok = not ok
            if not ok:
                return False
        if len(pending) > 1:
            raise CompMatError("more than one weapon-side condition")
        return pending[0] if pending else True

    def op_set(self, ops, values, cond):
        if cond is False or not values:
            return
        op = {"set": values}
        if cond is not True:
            op["if"] = cond
        ops.append(op)

    def copy_property(self, ops, m, cond):
        src = str(m.get("m_strCopyProperty_InputContainerSrc", ""))
        prop = str(m.get("m_strCopyProperty_InputContainerProperty", ""))
        target = str(m.get("m_strCopyProperty_TargetProperty", "")) or prop
        if src in WEAPON_CONTAINERS:
            self.op_set(ops, {target: {"ref": src, "name": prop}}, cond)
            return
        vs = self.vars_of(src) or {}
        if vs.get(prop) is not None:
            self.op_set(ops, {target: vs[prop]}, cond)

    def instructions(self, ops, items):
        for m in items or []:
            kind = str(m.get("m_nMutatorCommandType", ""))
            cond = self.condition(m.get("m_vecConditions"))
            if kind.endswith("COPY_MATCHING_KEYS"):
                src = str(m.get("m_strCopyMatchingKeys_InputContainerSrc", ""))
                if cond is False:
                    continue
                if src == "composite_inputs":
                    if cond is not True:
                        raise CompMatError("conditional composite inputs")
                    ops.append({"weapon": True})
                elif src in WEAPON_CONTAINERS:
                    raise CompMatError(f"copy matching keys from {src}")
                else:
                    vs = {k: v for k, v in (self.vars_of(src) or {}).items() if v is not None}
                    self.op_set(ops, vs, cond)
            elif kind.endswith("COPY_PROPERTY"):
                self.copy_property(ops, m, cond)
            elif kind.endswith("SET_VALUE"):
                v = m.get("m_nSetValue_Value") or {}
                name = str(v.get("m_strName", ""))
                if name:
                    self.op_set(ops, {name: loose_value(v)}, cond)
            else:
                raise CompMatError(f"instruction {kind}")


def evaluate(doc, load, material_vars):
    """Evaluate a parsed vcompmat. ``load(path)`` loads includes,
    ``material_vars(vmat_path)`` returns a material's parameters and
    textures as {name: value}."""
    containers, mutators = collect(doc, load)
    ev = _Evaluator(containers, material_vars)
    out = {"passes": {}, "final": [], "rolled": [], "roll": [], "rolls": [], "ranges": {}, "paint": ""}
    rolls = []  # (seed variable, condition, variables)
    paint = containers.get("paint")
    if paint is not None:
        out["paint"] = paint["material"]
    for c in containers.values():
        out["ranges"].update(c["bounds"])
    for m in mutators:
        kind = str(m.get("m_nMutatorCommandType", ""))
        cond = ev.condition(m.get("m_vecConditions"))
        if kind.endswith("CONDITIONAL_MUTATORS"):
            for sub in m.get("m_vecConditionalMutators", []) or []:
                if str(sub.get("m_nMutatorCommandType", "")).endswith("RANDOM_ROLL_INPUT_VARIABLES"):
                    rolls.append((sub, True))
                else:
                    raise CompMatError(f"conditional mutator {sub.get('m_nMutatorCommandType')}")
        elif kind.endswith("RANDOM_ROLL_INPUT_VARIABLES"):
            if cond is not False:
                rolls.append((m, cond))
        elif kind.endswith("_INIT"):
            continue  # final material starts as the weapon material
        elif kind.endswith("GENERATE_TEXTURE"):
            if cond is False:
                continue
            target = str(m.get("m_strGenerateTexture_TargetParam", ""))
            initial = str(m.get("m_strGenerateTexture_InitialContainer", ""))
            ops = []
            base = {k: v for k, v in (ev.vars_of(initial) or {}).items() if v is not None}
            ev.op_set(ops, base, True)
            ev.instructions(ops, m.get("m_vecTexGenInstructions"))
            out["passes"][target] = ops
        elif kind.endswith("COPY_PROPERTY"):
            ev.copy_property(out["final"], m, cond)
        elif kind.endswith("SET_VALUE"):
            v = m.get("m_nSetValue_Value") or {}
            if v.get("m_strName"):
                ev.op_set(out["final"], {str(v["m_strName"]): loose_value(v)}, cond)
        elif kind.endswith("COPY_MATCHING_KEYS"):
            src = str(m.get("m_strCopyMatchingKeys_InputContainerSrc", ""))
            vs = {k: v for k, v in (ev.vars_of(src) or {}).items() if v is not None}
            ev.op_set(out["final"], vs, cond)
        else:
            raise CompMatError(f"mutator {kind}")
    # a kit that rolls again unconditionally replaces the earlier rolls
    kept = []
    for m, cond in rolls:
        if cond is True:
            kept = []
        kept.append((m, cond))
    for m, cond in kept:
        names = [str(x) for x in m.get("m_vecRandomRollInputVars_InputVarsToRoll", [])]
        seed = str(m.get("m_strRandomRollInputVars_SeedInputVar", "") or "econ_instance.g_nRandomSeed")
        out["rolls"].append({"seed": seed.split(".")[-1], "if": None if cond is True else cond,
                             "roll": [roll_entry(r, containers, ev) for r in names]})
    if kept:
        out["rolled"] = [str(x) for x in kept[0][0].get("m_vecRandomRollInputVars_InputVarsToRoll", [])]
        out["roll"] = out["rolls"][0]["roll"]
    return out


def roll_entry(spec, containers, ev):
    """'econ_instance.g_vPatternTexCoordOffset.xy' -> [var, n, bounds, default]"""
    parts = spec.split(".")
    if len(parts) < 2:
        raise CompMatError(f"roll variable {spec}")
    alias, var = parts[0], parts[1]
    n = len(parts[2]) if len(parts) > 2 else 1
    bounds = (containers.get(alias) or {}).get("bounds", {}).get(var)
    default = (ev.vars_of(alias) or {}).get(var)
    if isinstance(default, (list, tuple)):
        default = list(default[:n]) if n > 1 else default[0]
    elif isinstance(default, bool) or not isinstance(default, (int, float)):
        default = None
    return [var, n, bounds, default]


def textures(result):
    """All texture paths (VPK) referenced by an evaluated procedure."""
    out = set()
    for ops in list(result["passes"].values()) + [result["final"]]:
        for op in ops:
            for k, v in op.get("set", {}).items():
                if k.startswith("g_t") and isinstance(v, str) and v:
                    out.add(v)
    return out
