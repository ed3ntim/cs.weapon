# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Apply a paint kit's assembly procedure to a weapon.

The exporter evaluates each paint kit's composite material (see
tools/compmat.py) into ops that stay symbolic where the weapon matters.
``resolve`` applies them for one weapon variant and returns the shader
parameters of a pass; ``combo_id`` picks the static shader combo for them.

Pure Python (no bpy), unit tested in tests/test_library.py.
"""

# Generated textures: pass 0 bakes the albedo, pass 1 roughness/metalness.
PASSES = ("g_tColor", "g_tMetalness")


def matches(value, want):
    """Composite material condition VALUE_EQUALS (string compare as in the
    game: numbers and booleans by value)."""
    want = str(want).strip().lower()
    if isinstance(value, bool):
        return value == (want in ("1", "true"))
    if isinstance(value, (int, float)):
        try:
            return float(value) == float(want)
        except ValueError:
            return want == "true" and bool(value)
    return str(value).lower() == want


def condition(cond, weapon):
    """True if a weapon-side condition ``[container, var, value(, negate)]``
    holds (value None: the variable exists)."""
    alias, var, value = cond[:3]
    vs = weapon.get(alias, {})
    if value is None:
        ok = vs.get(var) is not None
    else:
        ok = var in vs and matches(vs[var], value)
    return ok != bool(cond[3] if len(cond) > 3 else False)


def resolve(ops, weapon):
    """Apply ops for a weapon. ``weapon``: {"composite_inputs": {...},
    "target": {...}, "attributes": {...}} with parameter and texture values
    (attributes: of the target material, e.g. ``left`` on gloves)."""
    params = {}
    for op in ops or []:
        if op.get("weapon"):
            params.update(weapon.get("composite_inputs", {}))
            continue
        cond = op.get("if")
        if cond and not condition(cond, weapon):
            continue
        for k, v in op.get("set", {}).items():
            if isinstance(v, dict) and "ref" in v:
                v = weapon.get(v["ref"], {}).get(v["name"])
                if v is None:
                    continue
            params[k] = v
    return params


def feature(params, name, default=0):
    """Integer value of a feature flag (F_...) in resolved parameters."""
    v = params.get(name, default)
    if isinstance(v, (list, tuple)):
        v = v[0] if v else default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def combo_id(params, combo_index, combo_max=None):
    """Static combo id of the paint shader for resolved parameters.
    ``combo_index``: combo name (S_...) -> index value, ``combo_max``:
    combo name -> highest value (both from the exported shader index).
    Each S_X combo follows the feature F_X."""
    combo_max = combo_max or {}
    cid = 0
    for name, index in combo_index.items():
        value = feature(params, "F_" + name[2:]) if name.startswith("S_") else 0
        value = max(0, min(value, int(combo_max.get(name, 1))))
        cid += value * int(index)
    return cid
