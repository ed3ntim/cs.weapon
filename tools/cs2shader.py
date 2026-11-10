# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Extract the paint composite shader from the local CS2 installation.

The add-on evaluates the game's own ``csgo_customweapon`` pixel shader
(see cs_weapon/nodes/glsl.py). This module writes the static combos the
library needs as GLSL with readable parameter names:

1. Source2Viewer-CLI exports ``csgo_customweapon_vulkan_50_ps.vcs`` from
   ``shaders_vulkan_dir.vpk`` and dumps its metadata (KV3 text),
2. the vcs holds one zstd frame per static combo, compressed with a
   dictionary that Source2Viewer-CLI carries (found by its header),
3. each frame holds SPIR-V, decompiled to GLSL with the spirv-cross
   library that ships next to Source2Viewer-CLI,
4. uniform members and textures get the names from the metadata.

Output in ``<library>/shaders/``: ``cw_<combo>.glsl`` and ``index.json``
(combo index values, parameter defaults). Needs the Python package
``zstandard`` (``pip install zstandard``).
"""

import ctypes
import glob
import json
import os
import re
import struct
import subprocess
import sys

WEAPON_SHADER = ("csgo_customweapon", "cw")  # (shader, file prefix)
GLOVE_SHADER = ("csgo_customglove", "cg")


def ps_vcs(shader, stage="ps"):
    """Path of a shader stage (``ps`` pixel, ``vs`` vertex) in the VPK."""
    return f"shaders/vfx/{shader}_vulkan_50_{stage}.vcs"
SHADER_VPK = "shaders_vulkan_dir.vpk"
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
DICT_MAGIC = bytes.fromhex("37a430ec")
SPIRV_MAGIC = b"\x03\x02\x23\x07"
DICT_SIZE = 0x10000
TEXTURE_SLOT_BASE = 30  # GLSL binding = texture slot + 30
GLSL_TYPES = {1: "float", 2: "vec2", 3: "vec3", 4: "vec4", 5: "int", 9: "int"}


class ShaderError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# zstd with Valve's dictionaries
# ---------------------------------------------------------------------------

def _zstd():
    try:
        import zstandard
    except ImportError:
        raise ShaderError("the shader export needs the Python package 'zstandard': "
                          f"{sys.executable} -m pip install zstandard") from None
    return zstandard


def frame_dict_id(data, i):
    """Dictionary id of the zstd frame at ``data[i]`` (0 = none)."""
    fhd = data[i + 4]
    flag = fhd & 3
    if not flag:
        return 0
    p = i + 5 + (0 if (fhd >> 5) & 1 else 1)
    return int.from_bytes(data[p:p + (1, 2, 4)[flag - 1]], "little")


def find_dictionaries(blob, ids):
    """{id: dictionary bytes} for the dictionary ids found in ``blob``
    (a binary that embeds them, e.g. Source2Viewer-CLI)."""
    out = {}
    for did in ids:
        i = blob.find(DICT_MAGIC + struct.pack("<I", did))
        if i >= 0:
            out[did] = blob[i:i + DICT_SIZE]
    return out


def frames(data):
    """Offsets of all zstd frames in ``data``."""
    out, i = [], data.find(ZSTD_MAGIC)
    while i >= 0:
        out.append(i)
        i = data.find(ZSTD_MAGIC, i + 1)
    return out


def spirv_blobs(vcs, dictionaries):
    """SPIR-V of every static combo in file order."""
    zstandard = _zstd()
    dicts = {k: zstandard.ZstdCompressionDict(v) for k, v in dictionaries.items()}
    out = []
    for i in frames(vcs):
        did = frame_dict_id(vcs, i)
        if did and did not in dicts:
            continue
        try:
            dctx = zstandard.ZstdDecompressor(dict_data=dicts[did]) if did else zstandard.ZstdDecompressor()
            data = dctx.decompressobj().decompress(vcs[i:])
        except zstandard.ZstdError:
            continue
        if data[8:12] == SPIRV_MAGIC:
            n = int.from_bytes(data[4:8], "little")
            out.append(data[8:8 + n])
    return out


# ---------------------------------------------------------------------------
# spirv-cross
# ---------------------------------------------------------------------------

class SpirvCross:
    NAMES = ("spirv-cross.dll", "libspirv-cross-c-shared.so", "libspirv-cross-c-shared.dylib",
             "libspirv-cross.so", "libspirv-cross.dylib")
    # spvc_compiler_option values (spirv_cross_c.h)
    GLSL_VERSION, GLSL_ES, GLSL_VULKAN, GLSL_PLAIN_UNIFORMS = 0x2000008, 0x2000009, 0x200000A, 0x2000023

    def __init__(self, folder):
        path = next((os.path.join(folder, n) for n in self.NAMES if os.path.isfile(os.path.join(folder, n))), None)
        if path is None:
            raise ShaderError(f"spirv-cross library not found next to Source2Viewer-CLI in {folder}")
        lib = ctypes.CDLL(path)
        P = ctypes.c_void_p
        sig = {
            "spvc_context_create": (ctypes.c_int, [ctypes.POINTER(P)]),
            "spvc_context_parse_spirv": (ctypes.c_int, [P, P, ctypes.c_size_t, ctypes.POINTER(P)]),
            "spvc_context_create_compiler": (ctypes.c_int, [P, ctypes.c_int, P, ctypes.c_int, ctypes.POINTER(P)]),
            "spvc_compiler_create_compiler_options": (ctypes.c_int, [P, ctypes.POINTER(P)]),
            "spvc_compiler_options_set_uint": (ctypes.c_int, [P, ctypes.c_int, ctypes.c_uint]),
            "spvc_compiler_install_compiler_options": (ctypes.c_int, [P, P]),
            "spvc_compiler_compile": (ctypes.c_int, [P, ctypes.POINTER(ctypes.c_char_p)]),
            "spvc_context_get_last_error_string": (ctypes.c_char_p, [P]),
            "spvc_context_destroy": (None, [P]),
        }
        for name, (res, args) in sig.items():
            f = getattr(lib, name)
            f.restype, f.argtypes = res, args
        self.lib, self.P = lib, P

    def glsl(self, spv):
        lib, P = self.lib, self.P
        ctx = P()
        lib.spvc_context_create(ctypes.byref(ctx))
        try:
            ir = P()
            buf = ctypes.create_string_buffer(spv, len(spv))
            if lib.spvc_context_parse_spirv(ctx, ctypes.cast(buf, P), len(spv) // 4, ctypes.byref(ir)):
                raise ShaderError(lib.spvc_context_get_last_error_string(ctx).decode())
            comp = P()
            lib.spvc_context_create_compiler(ctx, 1, ir, 1, ctypes.byref(comp))  # GLSL, take ownership
            opts = P()
            lib.spvc_compiler_create_compiler_options(comp, ctypes.byref(opts))
            for k, v in ((self.GLSL_VERSION, 460), (self.GLSL_ES, 0), (self.GLSL_VULKAN, 1),
                         (self.GLSL_PLAIN_UNIFORMS, 1)):
                lib.spvc_compiler_options_set_uint(opts, k, v)
            lib.spvc_compiler_install_compiler_options(comp, opts)
            out = ctypes.c_char_p()
            if lib.spvc_compiler_compile(comp, ctypes.byref(out)):
                raise ShaderError(lib.spvc_context_get_last_error_string(ctx).decode())
            return out.value.decode()
        finally:
            lib.spvc_context_destroy(ctx)


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------

def _material_block(src, main, count):
    """Members and instance name of the uniform block holding the material
    constants: the one with ``count`` members (all, or those main reads).
    The paint shader has a single block; the weapon shader has several and
    keeps texture and sampler indices (bindless) in the material block."""
    for struct, inst in re.findall(r"uniform (_\d+) (_\d+);", src):
        m = re.search(r"struct %s\n\{(.*?)\};" % struct, src, re.S)
        if m is None:
            continue
        members = re.findall(r"(\w+) (_m\d+);", m.group(1))
        if len(members) != count:  # members the code never reads are not listed
            members = [mm for mm in members if re.search(r"\b%s\.%s\b" % (inst, mm[1]), main)]
        if len(members) == count:
            return members, inst
    return None, None


def name_glsl(src, combo_id, combo_data, variables, combo_array, shader="csgo_customweapon"):
    """Insert parameter and texture names into decompiled GLSL and prepend
    the header the add-on reads (glsl.header)."""
    allv = combo_data["m_allVars"]
    arr = allv["m_indexAndRegisterOffsetArray"]
    rs, ce = allv["m_nFirstRenderStateElement"], allv["m_nFirstConstantElement"]
    textures = {}
    for v in arr[rs:ce]:
        hi, lo = v >> 16, v & 0xFFFF
        var = variables[hi & 0xFFF] if hi >> 12 == 1 and (hi & 0xFFF) < len(variables) else None
        if var is not None and var["m_szName"].startswith("g_t"):
            textures[lo + TEXTURE_SLOT_BASE] = (var["m_szName"], int(var.get("m_nTypeSpecificBits", 0)) & 1)
    consts = sorted(((v & 0xFFFF), variables[v >> 16]["m_szName"]) for v in arr[ce:])
    main = src[src.index("void main()"):]
    members, ubo = _material_block(src, main, len(consts))
    if members is None:
        raise ShaderError(f"combo {combo_id}: no uniform block with {len(consts)} members")
    vtypes = {v["m_szName"]: v["m_type"] for v in variables}
    for (typ, mm), (_off, name) in zip(members, consts):
        if GLSL_TYPES.get(vtypes.get(name), typ) != typ:
            raise ShaderError(f"combo {combo_id}: {name} is not a {typ}")
        src = re.sub(r"\b%s\.%s\b" % (ubo, mm), name, src)
    head = [f"// {shader} static combo {combo_id}",
            "// combos " + " ".join(f"{c['m_szName']}={(combo_id // c['m_nComboIndexValue']) % (c['m_nMax'] + 1)}"
                                    for c in combo_array)]
    for binding, (name, srgb) in sorted(textures.items()):
        t = re.search(r"binding = %d\) uniform texture2D (_\d+);" % binding, src)
        if t:
            src = re.sub(r"\b%s\b" % t.group(1), name, src)
            head.append(f"// texture {name} srgb={srgb}")
    for loc, name in re.findall(r"layout\(location = (\d+)\) in vec[234] (_\d+);", src):
        head.append(f"// input {loc} {name}")
    for loc, name in re.findall(r"layout\(location = (\d+)\) out vec[234] (_\d+);", src):
        head.append(f"// output {loc} {name}")
    # values spirv-cross leaves undefined (OpUndef): any value is valid, 0 here
    undef = [f"const {typ} {name} = {typ}(0.0);"
             for typ, name in re.findall(r"^(float|vec[234]) (_\d+);$", src[:src.index("void main()")], re.M)]
    src = re.sub(r"sampler2D\((\w+), _\d+\)", r"\1", src)
    arrays = re.findall(r"^const .*?;$", src, re.M)
    return "\n".join(head + arrays + undef) + "\n" + src[src.index("void main()"):]


def murmur2(data, seed=0x31415926):
    """MurmurHash2 as Source 2 uses it for names (lower-case, this seed)."""
    m, h, i = 0x5BD1E995, (seed ^ len(data)) & 0xFFFFFFFF, 0
    while len(data) - i >= 4:
        k = (struct.unpack_from("<I", data, i)[0] * m) & 0xFFFFFFFF
        k = ((k ^ (k >> 24)) * m) & 0xFFFFFFFF
        h = ((h * m) & 0xFFFFFFFF) ^ k
        i += 4
    rest = data[i:]
    if len(rest) >= 3:
        h ^= rest[2] << 16
    if len(rest) >= 2:
        h ^= rest[1] << 8
    if rest:
        h = ((h ^ rest[0]) * m) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * m) & 0xFFFFFFFF
    return h ^ (h >> 15)


# material expression of the glove colour matrices:
# MatrixMultiply(MatrixColorTint2(tint, 1),
#                MatrixColorCorrect2(float3(contrast, saturation, brightness),
#                                    TextureAverageColor(texture)))
_VAR, _PUSH, _CALL, _END = 0x1D, 0x07, 0x06, 0x00
_COLOR_MATRIX = ("v", (_PUSH, 1.0), (_CALL, 54), "v", "v", "v", (_CALL, 25), "v", (_CALL, 40),
                 (_CALL, 48), (_CALL, 46))


def _expr_ops(code):
    ops, i = [], 0
    while i < len(code) and code[i] != _END:
        op = code[i]
        if op == _VAR:
            ops.append(("v", struct.unpack_from("<I", code, i + 1)[0]))
            i += 5
        elif op == _PUSH:
            ops.append((_PUSH, struct.unpack_from("<f", code, i + 1)[0]))
            i += 5
        elif op == _CALL:
            ops.append((_CALL, code[i + 1]))
            i += 3
        else:
            return None
    return ops


def color_matrices(variables):
    """{matrix uniform: [tint, contrast, saturation, brightness, texture]}
    of the uniforms computed by the colour matrix expression above."""
    names = {murmur2(v["m_szName"].lower().encode()): v["m_szName"] for v in variables}
    out = {}
    for v in variables:
        code = v.get("m_pCompiledExpression")
        if v.get("m_type") != 19 or not isinstance(code, (bytes, bytearray)):
            continue
        ops = _expr_ops(code)
        if ops is None or len(ops) != len(_COLOR_MATRIX):
            continue
        args = []
        for (kind, val), want in zip(ops, _COLOR_MATRIX):
            if want == "v" and kind == "v" and val in names:
                args.append(names[val])
            elif want != (kind, val):
                break
        else:
            out[v["m_szName"]] = args
    return out


def colors(variables):
    """Parameters the material editor shows as colours (sRGB in the vmat)."""
    return sorted({v["m_szName"] for v in variables if v.get("m_uiType") == 2})


def defaults(variables):
    out = {}
    for v in variables:
        name = v["m_szName"]
        if name in out or name.startswith(("g_t", "Texture")) or not name.startswith(("g_", "b", "n")):
            continue
        d = v.get("m_flDefault", v.get("m_intDefault"))
        if d is None:
            continue
        n = {1: 1, 2: 2, 3: 3, 4: 4}.get(v.get("m_type"), 1)
        vals = [float(x) for x in d[:n]]
        out[name] = vals[0] if n == 1 else vals
    return out


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

class ShaderExport:
    """Loads the pixel shader once; ``write(combo_ids, folder)`` writes the
    requested combos."""

    def __init__(self, s2v_exe, game_dir, work_dir, kv3, threads=1, shader=WEAPON_SHADER, stage="ps"):
        self.kv3 = kv3
        self.shader, self.prefix = shader
        self.stage = stage
        if stage != "ps":
            self.prefix += stage
        vcs_name = ps_vcs(self.shader, stage)
        vpk = os.path.join(game_dir, SHADER_VPK)
        if not os.path.isfile(vpk):
            raise ShaderError(f"{SHADER_VPK} not found in {game_dir}")
        os.makedirs(work_dir, exist_ok=True)
        subprocess.run([s2v_exe, "-i", vpk, "-o", work_dir, "-f", vcs_name], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=work_dir)
        vcs_path = os.path.join(work_dir, *vcs_name.split("/"))
        if not os.path.isfile(vcs_path):
            raise ShaderError(f"could not export {self.shader}")
        dump = vcs_path + ".txt"
        subprocess.run([s2v_exe, "-i", vcs_path, "-d", "-o", dump], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=work_dir)
        self.doc = kv3.load(dump)
        with open(vcs_path, "rb") as fh:
            vcs = fh.read()
        ids = {frame_dict_id(vcs, i) for i in frames(vcs)} - {0}
        with open(s2v_exe, "rb") as fh:
            dicts = find_dictionaries(fh.read(), ids)
        if ids - set(dicts):
            raise ShaderError("Source2Viewer-CLI does not carry the shader dictionary "
                              f"{', '.join(hex(i) for i in sorted(ids - set(dicts)))}; update it")
        self.blobs = spirv_blobs(vcs, dicts)
        self.ids = list(self.doc["m_staticComboIDs"])
        # combos can share a program (m_nByteCodeDataIdx); older files map 1:1
        data = self.doc["m_staticComboData"]
        self.program = [int(d.get("m_nByteCodeDataIdx", k)) for k, d in enumerate(data)]
        programs = len(self.doc.get("m_byteCodeData") or self.ids)
        if len(self.blobs) != programs or any(not 0 <= i < len(self.blobs) for i in self.program):
            raise ShaderError(f"{len(self.blobs)} shader programs for {len(self.ids)} combos")
        self.cross = SpirvCross(os.path.dirname(os.path.abspath(s2v_exe)))
        self.source = {"size": len(vcs), "combos": len(self.ids)}

    def combo_index(self):
        return {c["m_szName"]: c["m_nComboIndexValue"] for c in self.doc["m_staticComboArray"]}

    def combo_max(self):
        return {c["m_szName"]: c["m_nMax"] for c in self.doc["m_staticComboArray"]}

    def write(self, combo_ids, folder):
        variables = self.doc["m_variableDescriptionArray"]
        written, missing = self.write_programs(combo_ids, folder)
        index = {"shader": self.shader, "prefix": self.prefix, "source": self.source,
                 "combo_index": self.combo_index(), "combo_max": self.combo_max(),
                 "defaults": defaults(variables), "colors": colors(variables),
                 "color_matrices": color_matrices(variables), "combos": written}
        with open(os.path.join(folder, "index.json"), "w", encoding="utf-8", newline="\n") as fh:
            json.dump(index, fh, indent=1)
        return written, missing

    def stage_index(self, written):
        """Index entry of a further stage (the vertex shader) for the main index."""
        return {"prefix": self.prefix, "combo_index": self.combo_index(), "combo_max": self.combo_max(),
                "defaults": defaults(self.doc["m_variableDescriptionArray"]), "combos": written}

    def write_programs(self, combo_ids, folder):
        """Named GLSL of the combos into folder, stale ones removed."""
        os.makedirs(folder, exist_ok=True)
        variables = self.doc["m_variableDescriptionArray"]
        written, missing = [], []
        for cid in sorted(set(combo_ids)):
            if cid not in self.ids:
                missing.append(cid)
                continue
            k = self.ids.index(cid)
            src = self.cross.glsl(self.blobs[self.program[k]])
            named = name_glsl(src, cid, self.doc["m_staticComboData"][k], variables,
                              self.doc["m_staticComboArray"], self.shader)
            with open(os.path.join(folder, f"{self.prefix}_{cid}.glsl"), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(named)
            written.append(cid)
        for old in glob.glob(os.path.join(folder, f"{self.prefix}_*.glsl")):
            m = re.search(self.prefix + r"_(\d+)\.glsl$", old)
            if m and int(m.group(1)) not in written:
                os.remove(old)
        return written, missing
