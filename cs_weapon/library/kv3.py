# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Minimal parser for KeyValues3 text (decompiled .vcompmat, .vmdl).

Objects become dicts, arrays lists, numbers int/float, ``true``/``false``
bool, ``null`` None. Typed strings (``resource_name:"..."``) keep only
the string, binary blobs (``#[ .. ]``) become bytes. Duplicate keys: the
last one wins.
"""

import re

_TOKEN = re.compile(r"""
    (?P<ws>\s+|//[^\n]*|/\*.*?\*/|<!--.*?-->)
  | (?P<str>"(?:[^"\\]|\\.)*")
  | (?P<mstr>\"\"\".*?\"\"\")
  | (?P<blob>\#\[[^\]]*\])
  | (?P<punct>[{}\[\]=,])
  | (?P<typed>[A-Za-z_][A-Za-z0-9_]*:(?="))
  | (?P<word>[^\s{}\[\]=,"]+)
""", re.S | re.X)


class KV3Error(ValueError):
    pass


def _tokens(text):
    pos = 0
    n = len(text)
    while pos < n:
        m = _TOKEN.match(text, pos)
        if not m:
            raise KV3Error(f"unexpected character {text[pos]!r} at {pos}")
        pos = m.end()
        kind = m.lastgroup
        if kind == "ws" or kind == "typed":
            continue
        yield kind, m.group(kind)


def _scalar(kind, tok):
    if kind == "str":
        return bytes(tok[1:-1], "utf-8").decode("unicode_escape") if "\\" in tok else tok[1:-1]
    if kind == "mstr":
        return tok[3:-3]
    if kind == "blob":
        return bytes.fromhex(tok[2:-1].replace("\n", " ").strip() or "")
    if tok == "true":
        return True
    if tok == "false":
        return False
    if tok == "null":
        return None
    try:
        return int(tok)
    except ValueError:
        pass
    try:
        return float(tok)
    except ValueError:
        return tok


def loads(text):
    toks = list(_tokens(text))
    pos = 0

    def value():
        nonlocal pos
        if pos >= len(toks):
            raise KV3Error("unexpected end")
        kind, tok = toks[pos]
        pos += 1
        if tok == "{" and kind == "punct":
            obj = {}
            while True:
                if pos >= len(toks):
                    raise KV3Error("unclosed object")
                k_kind, k = toks[pos]
                if k == "}" and k_kind == "punct":
                    pos += 1
                    return obj
                if k == "," and k_kind == "punct":
                    pos += 1
                    continue
                pos += 1
                key = k[1:-1] if k_kind == "str" else k
                if pos >= len(toks) or toks[pos][1] != "=":
                    raise KV3Error(f"expected '=' after {key!r}")
                pos += 1
                obj[key] = value()
        if tok == "[" and kind == "punct":
            arr = []
            while True:
                if pos >= len(toks):
                    raise KV3Error("unclosed array")
                if toks[pos] == ("punct", "]"):
                    pos += 1
                    return arr
                if toks[pos] == ("punct", ","):
                    pos += 1
                    continue
                arr.append(value())
        if kind == "punct":
            raise KV3Error(f"unexpected {tok!r}")
        return _scalar(kind, tok)

    result = value()
    return result


def load(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return loads(fh.read())


def walk(node, key=None):
    """Yield every dict below ``node`` (depth first), optionally only those
    having ``key``."""
    if isinstance(node, dict):
        if key is None or key in node:
            yield node
        for v in node.values():
            yield from walk(v, key)
    elif isinstance(node, list):
        for v in node:
            yield from walk(v, key)


def loose_variable_ranges(doc):
    """Bounds of rollable loose variables in a composite material:
    ``{name: (mins, maxs)}`` with floats (FLOAT1) or tuples (FLOAT2..4)."""
    out = {}
    for var in walk(doc, "m_strName"):
        if not var.get("m_bHasFloatBounds"):
            continue
        vtype = str(var.get("m_nVariableType", ""))
        n = {"LOOSE_VARIABLE_TYPE_FLOAT1": 1, "LOOSE_VARIABLE_TYPE_FLOAT2": 2,
             "LOOSE_VARIABLE_TYPE_FLOAT3": 3, "LOOSE_VARIABLE_TYPE_FLOAT4": 4}.get(vtype)
        if not n:
            continue
        comps = "XYZW"[:n]
        mins = [float(var.get(f"m_flValueFloat{c}_Min", 0.0)) for c in comps]
        maxs = [float(var.get(f"m_flValueFloat{c}_Max", 0.0)) for c in comps]
        out[var["m_strName"]] = (mins[0], maxs[0]) if n == 1 else (tuple(mins), tuple(maxs))
    return out


def includes(doc):
    """Included composite materials (m_vecCompMatIncludes)."""
    out = []
    for node in walk(doc, "m_vecCompMatIncludes"):
        out += [str(p) for p in node["m_vecCompMatIncludes"]]
    return out


def specific_materials(doc):
    """{alias: vmat path} of CONTAINER_SOURCE_TYPE_SPECIFIC_MATERIAL containers
    and of loose-variable containers holding a resource material (newer
    glove kits name their paint material that way)."""
    out = {}
    for node in walk(doc, "m_strSpecificContainerMaterial"):
        out[node.get("m_strAlias", "")] = str(node["m_strSpecificContainerMaterial"])
    for node in walk(doc, "m_vecLooseVariables"):
        for var in node.get("m_vecLooseVariables") or []:
            if isinstance(var, dict) and var.get("m_strResourceMaterial"):
                out[node.get("m_strAlias", "")] = str(var["m_strResourceMaterial"])
    return out
