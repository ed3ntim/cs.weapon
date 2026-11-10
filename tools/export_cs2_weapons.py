# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Export all CS2 weapons, knives and paint kits from your own game
installation into a cs.weapon library (catalog.json + glTF models +
textures + icons), using Source2Viewer-CLI.

    python tools/export_cs2_weapons.py --out C:/cs2_weapons --download-s2v
    python tools/export_cs2_weapons.py --out C:/cs2_weapons --only weapon_ak47,weapon_knife_karambit

Then point the add-on's "Weapon Library Folder" at --out, or build a
personal zip with everything included:

    python tools/build_zip.py --with-weapons C:/cs2_weapons

What it does:
1. reads items, prefabs and paint kits from scripts/items/items_game.txt
   (names from the localization, rarity, legacy flag, wear range) and the
   skin combinations from the generated inventory icons and loot lists,
2. exports every paintable weapon and knife model as .glb with materials
   (both meshes: HD and legacy UV layout) and its decompiled .vmdl
   (StatTrak / name tag / charm attachments), plus <model>.rig.glb with
   skeleton and animations (spawn as armature; add to an existing library
   with --skip-export --rig),
3. decompiles the weapon materials and their composite inputs (masks,
   cavity, AO, position, surface, size parameters for the pattern scale),
4. decompiles every paint kit's composite material (seed ranges) and paint
   material (style, colours, textures) and exports the textures,
5. exports the inventory icons (light / medium / heavy wear) and writes
   catalog.json.

Requirements: CS2 installed via Steam, Source2Viewer-CLI
(https://github.com/ValveResourceFormat/ValveResourceFormat/releases,
asset "cli-windows-x64.zip"; --download-s2v fetches it). Python 3.9+, no
other packages. After a CS2 update simply run it again.

The models and textures are Valve's copyrighted game assets: export them
for your own use only and do not redistribute them.
"""

import argparse
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import types
import urllib.request
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compmat  # noqa: E402
import cs2shader  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_DIR = os.path.join(ROOT, "cs_weapon", "library")

S2V_RELEASES = "https://api.github.com/repos/ValveResourceFormat/ValveResourceFormat/releases/latest"
S2V_ASSET = "cli-windows-x64.zip"
S2V_NAMES = ("Source2Viewer-CLI.exe", "Source2Viewer-CLI")

ITEMS_GAME = "scripts/items/items_game.txt"
ICON_DIR_VPK = "panorama/images/econ/default_generated/"
RAW_DIR = "_raw"
RIG_RAW = "_rig"  # inside RAW_DIR: models with skeleton and animations
RIG_SUFFIX = ".rig.glb"

# StatTrak and name tag modules: model and materials (VPK paths without _c)
MODULE_DIR = "weapons/models/shared"
MODULES = {
    "stattrak": (f"{MODULE_DIR}/stattrak/stattrak_module.vmdl",
                 [f"{MODULE_DIR}/stattrak/materials/stattrak_module.vmat",
                  f"{MODULE_DIR}/stattrak/materials/stattrak_module_display.vmat"]),
    "stattrak_knife": (f"{MODULE_DIR}/stattrak/stattrak_module_knife.vmdl",
                       [f"{MODULE_DIR}/stattrak/materials/stattrak_module_display_knife.vmat"]),
    "nametag": (f"{MODULE_DIR}/nametag/nametag_module.vmdl",
                [f"{MODULE_DIR}/nametag/materials/nametag_default.vmat"]),
}
NAMETAG_VCOMPMAT = f"{MODULE_DIR}/nametag/nametag_default.vcompmat"
BATCH = 80  # file filters per Source2Viewer call (Windows command line limit)
WEAR_ICONS = ("light", "medium", "heavy")
# Paint materials also reference default composite inputs of some weapon:
# those come from the weapon itself, not from the paint kit.
# Gloves: paintable hands (prefab hands_paintable) and their paint kits
GLOVE_PREFAB = "hands_paintable"
GLOVE_PAINTS = "gloves/paints/"
GLOVE_MESH = "viewmodel"  # first-person mesh: both hands and the bare arm

WEAPON_INPUT_KEYS = {"ambientocclusion", "color", "masks", "metalness", "position", "surface"}

# prefab name -> weapon type (first match along the prefab chain wins)
TYPE_PREFABS = (
    ("melee", "knife"), ("sniper_rifle", "sniper"), ("secondary", "pistol"), ("machinegun", "machinegun"),
    ("shotgun", "shotgun"), ("smg", "smg"), ("pistol", "pistol"), ("rifle", "rifle"),
)


# ---------------------------------------------------------------------------
# Library modules (loaded without bpy / the add-on package __init__)
# ---------------------------------------------------------------------------

def _load_library():
    pkg = types.ModuleType("cwlib")
    pkg.__path__ = [LIB_DIR]
    sys.modules["cwlib"] = pkg
    mods = {}
    for name in ("kv3", "catalog", "procedure"):
        spec = importlib.util.spec_from_file_location(f"cwlib.{name}", os.path.join(LIB_DIR, f"{name}.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        mods[name] = mod
    return mods["kv3"], mods["catalog"], mods["procedure"]


KV3, C, PROC = _load_library()


# ---------------------------------------------------------------------------
# Locating CS2 and Source2Viewer
# ---------------------------------------------------------------------------

def _steam_roots():
    roots = []
    if sys.platform == "win32":
        try:
            import winreg
            for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                              (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
                try:
                    with winreg.OpenKey(hive, key) as k:
                        for value in ("SteamPath", "InstallPath"):
                            try:
                                roots.append(winreg.QueryValueEx(k, value)[0])
                            except OSError:
                                pass
                except OSError:
                    pass
        except ImportError:
            pass
        roots += [r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"]
    else:
        home = os.path.expanduser("~")
        roots += [os.path.join(home, ".steam", "steam"), os.path.join(home, ".local", "share", "Steam"),
                  os.path.join(home, "Library", "Application Support", "Steam")]
    return [os.path.normpath(r) for r in roots if r and os.path.isdir(r)]


def find_cs2(explicit=""):
    """Path to game/csgo (the folder holding pak01_dir.vpk)."""
    candidates = []
    if explicit:
        candidates += [explicit, os.path.join(explicit, "game", "csgo")]
    for steam in _steam_roots():
        libraries = [steam]
        vdf = os.path.join(steam, "steamapps", "libraryfolders.vdf")
        if os.path.isfile(vdf):
            with open(vdf, encoding="utf-8", errors="replace") as fh:
                libraries += [p.replace("\\\\", "\\") for p in re.findall(r'"path"\s+"([^"]+)"', fh.read())]
        for lib in libraries:
            candidates.append(os.path.join(lib, "steamapps", "common", "Counter-Strike Global Offensive", "game", "csgo"))
    for c in candidates:
        if os.path.isfile(os.path.join(c, "pak01_dir.vpk")):
            return os.path.normpath(c)
    raise SystemExit("CS2 not found. Pass --cs2 <...>/Counter-Strike Global Offensive")


def download_s2v(dest):
    print("downloading Source2Viewer-CLI (latest release) ...")
    req = urllib.request.Request(S2V_RELEASES, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        release = json.load(resp)
    asset = next((a for a in release.get("assets", []) if a["name"] == S2V_ASSET), None)
    if asset is None:
        raise SystemExit(f"release {release.get('tag_name')} has no {S2V_ASSET}")
    os.makedirs(dest, exist_ok=True)
    zpath = os.path.join(dest, S2V_ASSET)
    urllib.request.urlretrieve(asset["browser_download_url"], zpath)
    with zipfile.ZipFile(zpath) as zf:
        zf.extractall(dest)
    print(f"Source2Viewer-CLI {release.get('tag_name')} -> {dest}")


def find_s2v(explicit, tools_dir, download):
    candidates = [explicit] if explicit else []
    candidates += [os.path.join(tools_dir, n) for n in S2V_NAMES]
    candidates += [shutil.which(n) or "" for n in S2V_NAMES]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    if download:
        download_s2v(tools_dir)
        return find_s2v("", tools_dir, False)
    raise SystemExit("Source2Viewer-CLI not found. Pass --s2v <path> or use --download-s2v")


def run_s2v(exe, vpk, out, filters, extensions=None, decompile=True, threads=8, extra=()):
    """Run Source2Viewer-CLI over ``filters`` (paths or folder prefixes) in batches."""
    filters = sorted(set(filters))
    started, written = time.time(), 0
    for i in range(0, len(filters), BATCH):
        cmd = [exe, "-i", vpk, "-o", out, "-f", ",".join(filters[i:i + BATCH]), "--threads", str(threads)]
        if extensions:
            cmd += ["-e", extensions]
        if decompile:
            cmd.append("-d")
        cmd += list(extra)
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace", cwd=out)
        tail = []
        for line in proc.stdout:
            tail = (tail + [line.rstrip()])[-20:]
            if "Dump written" in line or "written to" in line:
                written += 1
            elif re.search(r"\berror\b|exception", line, re.I):
                print("  " + line.rstrip())
        if proc.wait() != 0:
            print("\n".join("  " + t for t in tail))
            raise SystemExit(f"Source2Viewer-CLI failed (exit {proc.returncode})")
    print(f"  done: {written} files in {time.time() - started:.0f}s")


def vpk_listing(exe, vpk):
    """All file paths in the VPK."""
    proc = subprocess.run([exe, "-i", vpk, "--vpk_list"], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise SystemExit("Source2Viewer-CLI --vpk_list failed")
    return [line.split(" CRC:")[0].strip() for line in proc.stdout.splitlines() if " CRC:" in line]


# ---------------------------------------------------------------------------
# KeyValues (items_game.txt, localization, decompiled .vmat)
# ---------------------------------------------------------------------------

_KV_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])|//[^\n]*|([^\s{}"]+)')


def parse_kv(text):
    """KeyValues1 -> nested lists of (key, value) pairs (keeps duplicates)."""
    stack = [[]]
    key = None
    for m in _KV_TOKEN.finditer(text):
        quoted, brace, bare = m.groups()
        if brace == "{":
            block = []
            stack[-1].append((key, block))
            stack.append(block)
            key = None
        elif brace == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
        elif quoted is not None or bare is not None:
            tok = quoted if quoted is not None else bare
            if key is None:
                key = tok
            else:
                stack[-1].append((key, tok))
                key = None
    return stack[0]


def find_blocks(pairs, name):
    for key, value in pairs:
        if isinstance(value, list):
            if key and key.lower() == name:
                yield value
            else:
                yield from find_blocks(value, name)


def to_dict(block):
    """Nested KV list -> dict (first key wins)."""
    out = {}
    for key, value in block:
        if key is not None and key not in out:
            out[key] = to_dict(value) if isinstance(value, list) else value
    return out


def load_tokens(path):
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        data = parse_kv(fh.read())
    tokens = {}
    for block in find_blocks(data, "tokens"):
        for key, value in block:
            if isinstance(value, str) and key:
                tokens.setdefault(key.lower(), value)
    return tokens


def tr(tokens, ref, fallback=""):
    return tokens.get(str(ref).lstrip("#").lower(), fallback)


def _number(text):
    text = text.strip()
    if text.startswith("[") and text.endswith("]"):
        try:
            return [float(x) for x in text[1:-1].split()]
        except ValueError:
            return None
    try:
        value = float(text)
    except ValueError:
        return None
    return int(value) if value.is_integer() and "." not in text else value


def parse_vmat(path):
    """Decompiled .vmat -> {shader, params, compiled, attributes}."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        data = parse_kv(fh.read())
    layer = next(iter(find_blocks(data, "layer0")), None) or data
    spec = {"shader": "", "params": {}, "compiled": {}, "attributes": {}}
    for key, value in layer:
        if key is None:
            continue
        if isinstance(value, list):
            low = key.lower()
            if low == "compiled textures":
                spec["compiled"] = {k: v.replace("\\", "/") for k, v in value if isinstance(v, str)}
            elif low == "attributes":
                spec["attributes"] = {k: v.replace("\\", "/") for k, v in value if isinstance(v, str)}
            continue
        if key == "shader":
            spec["shader"] = value
        elif key.startswith(("g_", "F_")):
            num = _number(value)
            if num is not None:
                spec["params"][key] = num
    return spec


# ---------------------------------------------------------------------------
# items_game
# ---------------------------------------------------------------------------

class Items:
    def __init__(self, path):
        with open(path, encoding="utf-8", errors="replace") as fh:
            data = parse_kv(fh.read())
        self.items, self.prefabs, self.paint_kits, self.rarity = {}, {}, {}, {}
        for block in find_blocks(data, "items"):
            for key, fields in block:
                if isinstance(fields, list) and key and key.isdigit():
                    self.items[key] = to_dict(fields)
        for block in find_blocks(data, "prefabs"):
            for key, fields in block:
                if isinstance(fields, list) and key:
                    self.prefabs[key] = to_dict(fields)
        for block in find_blocks(data, "paint_kits"):
            for key, fields in block:
                if isinstance(fields, list) and key and key.isdigit():
                    self.paint_kits[int(key)] = to_dict(fields)
        for block in find_blocks(data, "paint_kits_rarity"):
            for key, value in block:
                if isinstance(value, str):
                    self.rarity[key] = value
        # "[paintkit]weapon_x" entries of loot lists and item sets
        self.combos = set()
        combo = re.compile(r"^\[([^\]]+)\](weapon_[a-z0-9_]+)$")
        for name in ("client_loot_lists", "item_sets"):
            for block in find_blocks(data, name):
                for pair in _walk_pairs(block):
                    m = combo.match(pair[0] or "")
                    if m:
                        self.combos.add((m.group(2), m.group(1)))

    def resolve(self, entry, depth=0):
        """Item merged with its prefab chain (own keys win). Also returns
        the list of prefab names along the chain."""
        merged, chain = {}, []
        for name in str(entry.get("prefab", "")).split():
            prefab = self.prefabs.get(name)
            if prefab is None or depth > 16:
                continue
            chain.append(name)
            sub, sub_chain = self.resolve(prefab, depth + 1)
            chain += sub_chain
            _deep_update(merged, sub)
        _deep_update(merged, {k: v for k, v in entry.items() if k != "prefab"})
        return merged, chain


def _walk_pairs(block):
    for key, value in block:
        yield key, value
        if isinstance(value, list):
            yield from _walk_pairs(value)


def _deep_update(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_update(dst[k], v)
        else:
            dst[k] = v


def weapon_type(chain):
    for prefab_part, wtype in TYPE_PREFABS:
        if any(prefab_part in p for p in chain):
            return wtype
    return "other"


def weapons_from_items(items, tokens):
    """Paintable weapons and knives: {item name: info}."""
    out = {}
    for def_index, entry in items.items.items():
        name = entry.get("name", "")
        if not name.startswith("weapon_"):
            continue
        merged, chain = items.resolve(entry)
        paint = (merged.get("paint_data") or {}).get("PaintableMaterial0")
        model = merged.get("model_player", "")
        if not paint or not model:
            continue
        out[name] = {
            "def_index": int(def_index),
            "name": tr(tokens, merged.get("item_name", ""), name),
            "type": weapon_type(chain),
            "model": model.replace("\\", "/"),
            "paint": paint,
            "camera": merged.get("inventory_image_data", {}),
        }
    return out


def gloves_from_items(items, tokens):
    """Paintable gloves: {item name: info}."""
    out = {}
    for def_index, entry in items.items.items():
        merged, chain = items.resolve(entry)
        model = merged.get("model_player", "")
        if GLOVE_PREFAB not in chain or not model:
            continue
        name = entry.get("name", "")
        out[name] = {
            "def_index": int(def_index),
            "name": tr(tokens, merged.get("item_name", ""), name),
            "model": model.replace("\\", "/"),
        }
    return out


def glove_kits(items, listing):
    """{paint kit name: vcompmat path} of the glove paint kits. Newer kits
    name it (``composite_material_path``); for older ones the file is named
    after the kit or after its ``vmt_path`` (.../paints_gloves/<x>.vmat),
    the two differ for some kits."""
    files = {p.lower(): p for p in listing if p.startswith(GLOVE_PAINTS) and p.endswith(".vcompmat_c")}
    out = {}
    for kit in items.paint_kits.values():
        name = kit.get("name", "")
        vmt = str(kit.get("vmt_path", "")).replace("\\", "/")
        comp = str(kit.get("composite_material_path", "")).replace("\\", "/")
        if not name or not (comp.startswith(GLOVE_PAINTS) or "/paints_gloves/" in vmt):
            continue
        cands = [comp + "_c"] if comp else []
        cands.append(f"{GLOVE_PAINTS}{name}.vcompmat_c")
        if vmt:
            cands.append(f"{GLOVE_PAINTS}{os.path.splitext(os.path.basename(vmt))[0]}.vcompmat_c")
        for c in cands:
            if c.lower() in files:
                out[name] = files[c.lower()]
                break
    return out


def glove_combos(gloves, kits, listing):
    """(glove name, kit name) pairs with an inventory icon, plus the icons."""
    names = sorted(gloves, key=len, reverse=True)
    kit_lower = {k.lower(): k for k in kits}
    icons = {}
    pat = re.compile(r"^" + re.escape(ICON_DIR_VPK) + r"(.+)_(light|medium|heavy)_png\.vtex_c$")
    for path in listing:
        m = pat.match(path)
        if not m:
            continue
        stem, wear = m.groups()
        for g in names:
            kit = kit_lower.get(stem[len(g) + 1:].lower()) if stem.startswith(g + "_") else None
            if kit:
                icons.setdefault((g, kit), {})[wear] = path
                break
    return sorted(icons), icons


def glb_materials(glb, mesh_suffix):
    """{material name: vmat path} of the primitives of one mesh node."""
    data = read_glb_json(glb)
    mats = data.get("materials", [])
    out = {}
    for node in data.get("nodes", []):
        if "mesh" not in node or node.get("name", "").rsplit(".", 1)[-1] != mesh_suffix:
            continue
        for prim in data["meshes"][node["mesh"]].get("primitives", []):
            i = prim.get("material")
            if i is not None and i < len(mats):
                vmat = ((mats[i].get("extras") or {}).get("vmat") or {}).get("Name", "")
                out[mats[i].get("name", "")] = vmat.replace("\\", "/")
    return out


def skin_combos(items, weapons, listing):
    """(weapon name, paint kit name) pairs from icons and loot lists, plus
    the icon paths per pair."""
    kit_names = {k.get("name", ""): i for i, k in items.paint_kits.items()}
    weapon_names = sorted(weapons, key=len, reverse=True)
    kit_lower = {name.lower(): name for name in kit_names}  # icon files are lower case
    icons = {}
    pat = re.compile(r"^" + re.escape(ICON_DIR_VPK) + r"(weapon_.+)_(light|medium|heavy)_png\.vtex_c$")
    for path in listing:
        m = pat.match(path)
        if not m:
            continue
        stem, wear = m.groups()
        for w in weapon_names:
            kit = kit_lower.get(stem[len(w) + 1:].lower()) if stem.startswith(w + "_") else None
            if kit:
                icons.setdefault((w, kit), {})[wear] = path
                break
    combos = set(icons) | {(w, k) for w, k in items.combos if w in weapons and k in kit_names}
    return sorted(combos), icons


# ---------------------------------------------------------------------------
# Paths in the raw export
# ---------------------------------------------------------------------------

def raw_path(raw, vpk_path, ext):
    """raw/<vpk path without compiled suffix and extension>.<ext>"""
    stem = vpk_path.replace("\\", "/")
    if stem.endswith("_c"):
        stem = stem[:-2]
    for suffix in (".vmdl", ".vmat", ".vtex", ".vcompmat", ext):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return os.path.join(raw, *stem.split("/")) + ext


RESIZABLE = (".png", ".tga", ".jpg")


def _pillow():
    try:
        from PIL import Image
    except ImportError:
        raise SystemExit("--max-texture-size needs the Python package 'pillow': "
                         f"{sys.executable} -m pip install pillow") from None
    return Image


def scaled_size(size, max_size):
    """(w, h) fitted into max_size, aspect kept; None if no change."""
    w, h = size
    if not max_size or max(w, h) <= max_size:
        return None
    f = max_size / max(w, h)
    return max(1, round(w * f)), max(1, round(h * f))


def copy_texture(src, dst, max_size=0):
    """Copy a texture, downscaled to at most ``max_size`` pixels per side
    (0 = full quality). EXR files are always copied as they are."""
    if max_size and src.lower().endswith(RESIZABLE):
        Image = _pillow()
        with Image.open(src) as im:
            size = scaled_size(im.size, max_size)
            if size is not None:
                if im.mode == "P":
                    im = im.convert("RGBA")
                elif im.mode.startswith("I;16"):
                    im = im.convert("I")
                # Channel by channel: alpha holds data (normal maps have
                # alpha 0), a combined RGBA resize would premultiply it.
                bands = [band.resize(size, Image.LANCZOS) for band in im.split()]
                Image.merge(im.mode, bands).save(dst)
                return
    shutil.copy2(src, dst)


def texture_file(raw, vtex):
    for ext in (".png", ".exr", ".tga", ".jpg"):
        p = raw_path(raw, vtex, ext)
        if os.path.isfile(p):
            return p
    return ""


def read_glb_json(path):
    with open(path, "rb") as fh:
        head = fh.read(20)
        if head[:4] != b"glTF":
            raise ValueError(f"{path}: not a glb")
        n = int.from_bytes(head[12:16], "little")
        return json.loads(fh.read(n))


def glb_meshes(glb):
    """[(mesh suffix, vmat path of its first material)] for every mesh node."""
    data = read_glb_json(glb)
    mats = data.get("materials", [])
    out = []
    for node in data.get("nodes", []):
        if "mesh" not in node:
            continue
        mesh = data["meshes"][node["mesh"]]
        prims = mesh.get("primitives", [])
        mat_idx = prims[0].get("material") if prims else None
        vmat = ""
        if mat_idx is not None and mat_idx < len(mats):
            vmat = ((mats[mat_idx].get("extras") or {}).get("vmat") or {}).get("Name", "")
        out.append((node.get("name", "").rsplit(".", 1)[-1], vmat.replace("\\", "/")))
    return out


def glb_images(glb):
    try:
        data = read_glb_json(glb)
    except ValueError:
        return []
    return [img["uri"] for img in data.get("images", []) if img.get("uri") and "/" not in img["uri"]]


def angle_matrix(angles):
    """3x3 rotation of a Source QAngle [pitch, yaw, roll] in degrees
    (R = Rz(yaw) Ry(pitch) Rx(roll), forward = (cp cy, cp sy, -sp))."""
    p, y, r = (math.radians(float(a)) for a in angles)
    cp, sp, cy, sy, cr, sr = math.cos(p), math.sin(p), math.cos(y), math.sin(y), math.cos(r), math.sin(r)
    return [[cp * cy, sr * sp * cy - cr * sy, cr * sp * cy + sr * sy],
            [cp * sy, sr * sp * sy + cr * cy, cr * sp * sy - sr * cy],
            [-sp, sr * cp, cr * cp]]


def transform(origin, angles):
    """4x4 row-major matrix from an origin and a QAngle."""
    m = angle_matrix(angles)
    return [m[0] + [float(origin[0])], m[1] + [float(origin[1])], m[2] + [float(origin[2])], [0.0, 0.0, 0.0, 1.0]]


def mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def vmdl_bones(doc):
    """{bone: model-space 4x4 matrix} from the Skeleton block (origin and
    angles of a bone are relative to its parent)."""
    out = {}

    def visit(nodes, parent):
        for node in nodes or []:
            if not isinstance(node, dict) or node.get("_class") != "Bone":
                continue
            m = mat_mul(parent, transform(node.get("origin", [0, 0, 0]), node.get("angles", [0, 0, 0])))
            out[node.get("name", "")] = m
            visit(node.get("children"), m)

    identity = transform([0, 0, 0], [0, 0, 0])
    for node in KV3.walk(doc, "_class"):
        if node.get("_class") == "Skeleton":
            visit(node.get("children"), identity)
    return out


def vmdl_attachments(vmdl_file):
    """{name: {origin, angles, bone, matrix}} from a decompiled .vmdl.
    ``matrix`` is the attachment in model space (Source inches, row-major
    4x4), composed with its parent bone chain."""
    try:
        doc = KV3.load(vmdl_file)
    except (OSError, ValueError):
        return {}
    bones = vmdl_bones(doc)
    identity = transform([0, 0, 0], [0, 0, 0])
    out = {}
    for node in KV3.walk(doc, "_class"):
        if node.get("_class") == "Attachment" and node.get("name"):
            origin = node.get("relative_origin", [0, 0, 0])
            angles = node.get("relative_angles", [0, 0, 0])
            bone = node.get("parent_bone", "")
            m = mat_mul(bones.get(bone, identity), transform(origin, angles))
            out[node["name"]] = {
                "origin": origin, "angles": angles, "bone": bone,
                "matrix": [round(v, 6) for row in m for v in row],
            }
    return out


def model_keyvalues(data_text):
    """Keyvalues of a model (Source2Viewer ``-b DATA`` output of a .vmdl_c),
    {} if none. The decompiled .vmdl does not carry them."""
    m = re.search(r'm_keyValueText = """(.*?)"""', data_text or "", re.S)
    if not m:
        return {}
    try:
        doc = KV3.loads(m.group(1))
    except ValueError:
        return {}
    return doc if isinstance(doc, dict) else {}


def sticker_markup(data_text):
    """{"hd": [...], "legacy": [...]} sticker slots from the StickerMarkup of
    a model's keyvalues. Each slot: {"slot", "offset": [x, y], "scale",
    "rotation", "deprecated"}."""
    doc = model_keyvalues(data_text)
    out = {}
    for e in doc.get("StickerMarkup") or []:
        if not isinstance(e, dict) or "Index" not in e or "Offset" not in e:
            continue
        key = "legacy" if e.get("LegacyModel") else "hd"
        out.setdefault(key, []).append({
            "slot": int(e["Index"]),
            "offset": [float(v) for v in e["Offset"][:2]],
            "scale": float(e.get("Scale", 0.0)),
            "rotation": float(e.get("Rotation", 0.0)),
            "deprecated": bool(e.get("Deprecated", False)),
        })
    return out


def keychain_markup(data_text):
    """{"hd": [...], "legacy": [...]} charm areas from the KeychainMarkup of
    a model's keyvalues: quads on the weapon's surface where a charm may
    hang. Each: {"bone", "corners": [[x, y, z] * 4] (model space, Source
    inches), "default": the game's default spot}."""
    out = {}
    for e in model_keyvalues(data_text).get("KeychainMarkup") or []:
        c = e.get("Corners") if isinstance(e, dict) else None
        if not isinstance(c, list) or len(c) != 12:
            continue
        out.setdefault("legacy" if e.get("LegacyModel") else "hd", []).append({
            "bone": str(e.get("BoneName", "")),
            "corners": [[float(v) for v in c[i:i + 3]] for i in range(0, 12, 3)],
            "default": bool(e.get("DefaultLocation", False)),
        })
    return out


def keychain_attachment(areas):
    """Attachment entry (as from vmdl_attachments) at the center of the
    default charm area, None without one. Translation only: the charm
    hangs out of the weapon's left side either way."""
    area = next((a for a in areas or () if a.get("default")), None)
    if area is None:
        return None
    x, y, z = (sum(c[i] for c in area["corners"]) / 4.0 for i in range(3))
    return {"origin": [x, y, z], "angles": [0.0, 0.0, 0.0], "bone": area["bone"], "model_space": True,
            "matrix": [1.0, 0.0, 0.0, x, 0.0, 1.0, 0.0, y, 0.0, 0.0, 1.0, z, 0.0, 0.0, 0.0, 1.0]}


def model_data_text(exe, vmdl_c):
    proc = subprocess.run([exe, "-i", vmdl_c, "-b", "DATA"], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    return proc.stdout if proc.returncode == 0 else ""


def nametag_text(vcompmat_doc):
    """Text style of the name tag composite (DRAW_TEXT), or defaults."""
    style = {"position": [0.04, 0.038], "color": [241, 241, 241], "font": "Arial Narrow"}
    for node in KV3.walk(vcompmat_doc, "m_nMutatorCommandType") if vcompmat_doc else []:
        if node.get("m_nMutatorCommandType") == "COMP_MAT_PROPERTY_MUTATOR_DRAW_TEXT":
            style["position"] = list(node.get("m_vecDrawText_Position", style["position"]))
            style["color"] = list(node.get("m_colDrawText_Color", style["color"]))[:3]
            style["font"] = node.get("m_strDrawText_Font", style["font"])
    return style


def tex_key(param):
    """g_tAmbientOcclusion -> ambientocclusion"""
    return param[3:].lower() if param.startswith("g_t") else param.lower()


# ---------------------------------------------------------------------------
# Export steps
# ---------------------------------------------------------------------------

class Exporter:
    def __init__(self, args):
        self.args = args
        self.out = os.path.abspath(args.out)
        self.raw = os.path.join(self.out, RAW_DIR)
        os.makedirs(self.raw, exist_ok=True)
        self.exe = self.vpk = self.game = None
        self.warnings = []
        self._procs = {}

    def s2v(self, filters, **kw):
        if filters:
            run_s2v(self.exe, self.vpk, self.raw, filters, threads=self.args.threads, **kw)

    def weapon_variants(self, wname, info):
        """[(legacy flag, mesh suffix, weapon vmat path)] of a model."""
        glb = raw_path(self.raw, info["model"], ".glb")
        if not os.path.isfile(glb):
            return []
        out = []
        for suffix, vmat in glb_meshes(glb):
            if not vmat or "sticker" in vmat:
                continue
            legacy = "legacy" in suffix or "/v_models/" in vmat
            out.append((legacy, suffix, vmat))
        return out

    def load_vcompmat(self, path):
        f = raw_path(self.raw, path, ".vcompmat")
        return KV3.load(f) if os.path.isfile(f) else None

    def material_vars(self, vmat_path, all_textures=False):
        """Parameters and textures (VPK paths) of a paint material; the
        default weapon inputs it references are left out (glove paints
        keep all: their surface and normal textures are paint inputs)."""
        f = raw_path(self.raw, vmat_path, ".vmat")
        if not os.path.isfile(f):
            return {}
        spec = parse_vmat(f)
        tex = {k: v for k, v in spec["compiled"].items()
               if all_textures or tex_key(k) not in WEAPON_INPUT_KEYS}
        return {**spec["params"], **tex}

    def procedure(self, vcomp, glove=False):
        """Evaluated assembly procedure of a paint kit's vcompmat (cached),
        None if it is missing or unsupported."""
        if vcomp not in self._procs:
            doc = self.load_vcompmat(vcomp)
            result = None
            mvars = (lambda v: self.material_vars(v, True)) if glove else self.material_vars
            if doc is not None:
                try:
                    result = compmat.evaluate(doc, self.load_vcompmat, mvars)
                except compmat.CompMatError as exc:
                    self.warnings.append(f"{vcomp}: {exc}")
            self._procs[vcomp] = result
        return self._procs[vcomp]

    def export_includes(self, comps):
        """Export the vcompmats included by ``comps`` (recursively)."""
        done = set()
        todo = set(comps)
        while todo:
            done |= todo
            new = set()
            for c in todo:
                doc = self.load_vcompmat(c)
                for inc in KV3.includes(doc) if doc is not None else []:
                    path = inc.replace("\\", "/") + ("" if inc.endswith("_c") else "_c")
                    if path not in done:
                        new.add(path)
            self.s2v(new, extensions="vcompmat_c")
            todo = new

    def export_modules(self):
        models = {m + "_c" for m, _v in MODULES.values()}
        self.s2v(models, extensions="vmdl_c",
                 extra=("--gltf_export_format", "glb", "--gltf_export_materials"))
        vmats = {v + "_c" for _m, vs in MODULES.values() for v in vs}
        self.s2v(vmats | {NAMETAG_VCOMPMAT + "_c"}, extensions="vmat_c,vcompmat_c")
        tex = set()
        for v in vmats:
            f = raw_path(self.raw, v, ".vmat")
            if os.path.isfile(f):
                tex.update(t + "_c" for t in parse_vmat(f)["compiled"].values())
        self.s2v(tex, extensions="vtex_c")

    def glove_materials(self, info):
        glb = raw_path(self.raw, info["model"], ".glb")
        return glb_materials(glb, GLOVE_MESH) if os.path.isfile(glb) else {}

    def export_gloves(self, gloves, gkits, gcombos, gicons):
        """Raw export of glove models, materials, paint kits and icons."""
        if not gloves:
            return
        models = {g["model"] + "_c" for g in gloves.values()}
        print(f"gloves: {len(models)} models, {len(gcombos)} skins")
        self.s2v(models, extensions="vmdl_c",
                 extra=("--gltf_export_format", "glb", "--gltf_export_materials"))
        self.s2v(models, extensions="vmdl_c")
        vmats = {v + "_c" for info in gloves.values() for v in self.glove_materials(info).values() if v}
        self.s2v(vmats, extensions="vmat_c")
        comps = {gkits[k] for _g, k in gcombos if k in gkits}
        self.s2v(comps, extensions="vcompmat_c")
        self.export_includes(comps)
        paint_vmats = set()
        for c in comps:
            doc = self.load_vcompmat(c)
            if doc is not None:
                paint_vmats.update(path + "_c" for path in KV3.specific_materials(doc).values())
        self.s2v(paint_vmats, extensions="vmat_c")
        tex = set()
        for v in vmats | paint_vmats:
            f = raw_path(self.raw, v, ".vmat")
            if os.path.isfile(f):
                tex.update(t + "_c" for t in parse_vmat(f)["compiled"].values())
        for c in comps:
            proc = self.procedure(c, glove=True)
            if proc is not None:
                tex.update(t + "_c" for t in compmat.textures(proc))
        tex |= {p for pair in gcombos for p in gicons.get(pair, {}).values()}
        self.s2v(tex, extensions="vtex_c")

    def build_gloves(self, place, library_ops):
        """Catalog entries of the gloves: (weapons, paint kits, skins)."""
        gloves, gkits, gcombos, gicons = getattr(self, "glove_data", ({}, {}, [], {}))
        raw, items = self.raw, self.items
        kit_by_name = {k.get("name", ""): i for i, k in items.paint_kits.items()}
        entries, kits, skins = [], {}, []
        for gname, info in sorted(gloves.items()):
            glb = raw_path(raw, info["model"], ".glb")
            if not os.path.isfile(glb):
                self.warnings.append(f"{gname}: model not exported ({info['model']})")
                continue
            group = f"gloves/{gname}"
            for uri in glb_images(glb):
                src = os.path.join(os.path.dirname(glb), uri)
                if os.path.isfile(src):
                    place(src, group)
            materials, arm = {}, ""
            for mname, vmat in self.glove_materials(info).items():
                f = raw_path(raw, vmat, ".vmat") if vmat else ""
                spec = parse_vmat(f) if f and os.path.isfile(f) else None
                attrs = (spec or {}).get("attributes", {})
                if not spec or str(attrs.get("composite_target", "")).lower() != "true":
                    arm = arm or mname
                    continue
                textures = {}
                for param, vtex in spec["compiled"].items():
                    src = texture_file(raw, vtex)
                    if src:
                        textures[param] = place(src, group)
                materials[mname] = {"attributes": {k: str(v) for k, v in attrs.items()
                                                   if k in ("left", "seed_offset")},
                                    "textures": textures}
            if len(materials) != 2:
                self.warnings.append(f"{gname}: {len(materials)} glove materials, skipped")
                continue
            entry = {"id": gname, "def_index": info["def_index"], "name": info["name"],
                     "type": "gloves", "model": place(glb, group),
                     "glove": {"mesh": GLOVE_MESH, "arm": arm, "materials": materials}}
            rig = self.place_rig(info["model"], glb, group)
            if rig:
                entry["rig"] = rig
            entries.append(entry)
        ids = {e["id"] for e in entries}
        for gname, kname in gcombos:
            kid = kit_by_name.get(kname)
            if gname not in ids or kid is None:
                continue
            if kid not in kits:
                raw_kit = items.paint_kits.get(kid, {})
                proc = self.procedure(gkits[kname], glove=True)
                if proc is None or not proc["passes"]:
                    self.warnings.append(f"glove paint kit {kname}: no composite material")
                    kits[kid] = None
                    continue
                kits[kid] = {
                    "id": kid, "name": kname, "glove": True,
                    "display": tr(self.tokens, raw_kit.get("description_tag", ""), kname),
                    "style": int(raw_kit.get("style", 0) or 0),
                    "wear_min": float(raw_kit.get("wear_remap_min", 0.06)),
                    "wear_max": float(raw_kit.get("wear_remap_max", 0.8)),
                    "rarity": items.rarity.get(kname, ""),
                    "passes": {t: library_ops(ops, kname) for t, ops in proc["passes"].items()},
                    "final": library_ops(proc["final"], kname),
                    "roll": proc["roll"], "rolls": proc["rolls"],
                    "vcompmat": gkits[kname],
                }
            if kits[kid] is None:
                continue
            skin = {"weapon": gname, "paint_kit": kid}
            for wear, path in gicons.get((gname, kname), {}).items():
                src = texture_file(raw, path)
                if src:
                    skin["icon" if wear == "light" else f"icon_{wear}"] = place(src, "icons")
            skins.append(skin)
        return entries, [k for k in kits.values() if k], skins

    def export_glove_shaders(self, kits):
        """Glove composite shader combos into <out>/shaders_glove."""
        if not kits:
            return ""
        folder = os.path.join(self.out, "shaders_glove")
        try:
            game = self.game or find_cs2(self.args.cs2)
            exe = self.exe or find_s2v(self.args.s2v, os.path.join(self.out, "_tools"), self.args.download_s2v)
            sh = cs2shader.ShaderExport(exe, game, os.path.join(self.raw, "_shader_glove"), KV3,
                                        shader=cs2shader.GLOVE_SHADER)
        except (cs2shader.ShaderError, OSError, subprocess.CalledProcessError, SystemExit) as exc:
            self.warnings.append(f"glove shader not exported: {exc}")
            return ""
        index, cmax = sh.combo_index(), sh.combo_max()
        ids = set()
        for kit in kits:
            for hand in ({"left": "1"}, {"seed_offset": "1"}):
                weapon = {"composite_inputs": {}, "target": {}, "attributes": hand}
                for ops in kit["passes"].values():
                    ids.add(PROC.combo_id(PROC.resolve(ops, weapon), index, cmax))
        written, missing = sh.write(ids, folder)
        for cid in missing:
            self.warnings.append(f"glove shader combo {cid} not in the game's shader")
        print(f"  {len(written)} glove shader combos")
        # vertex shader: the newer combos read its per-layer UVs
        try:
            vs = cs2shader.ShaderExport(exe, game, os.path.join(self.raw, "_shader_glove_vs"), KV3,
                                        shader=cs2shader.GLOVE_SHADER, stage="vs")
            vs_written, _missing = vs.write_programs(vs.ids, folder)
            path = os.path.join(folder, "index.json")
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            data["vs"] = vs.stage_index(vs_written)
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(data, fh, indent=1)
        except (cs2shader.ShaderError, OSError, subprocess.CalledProcessError) as exc:
            self.warnings.append(f"glove vertex shader not exported: {exc}")
        return "shaders_glove/index.json"

    def run(self):
        args = self.args
        loc_file = f"resource/csgo_{args.language}.txt"
        items_file = raw_path(self.raw, ITEMS_GAME, ".txt")
        listing_file = os.path.join(self.raw, "vpk_list.txt")
        if not args.skip_export:
            game = self.game = find_cs2(args.cs2)
            self.vpk = os.path.join(game, "pak01_dir.vpk")
            self.exe = find_s2v(args.s2v, os.path.join(self.out, "_tools"), args.download_s2v)
            print(f"CS2: {game}\nSource2Viewer: {self.exe}")
            print("1/6 metadata")
            run_s2v(self.exe, self.vpk, self.raw, [ITEMS_GAME, loc_file], decompile=False, threads=1)
            with open(listing_file, "w", encoding="utf-8") as fh:
                fh.write("\n".join(vpk_listing(self.exe, self.vpk)))
        if not os.path.isfile(items_file) or not os.path.isfile(listing_file):
            raise SystemExit("raw metadata missing: run without --skip-export")
        with open(listing_file, encoding="utf-8") as fh:
            listing = fh.read().splitlines()
        items = self.items = Items(items_file)
        loc_path = raw_path(self.raw, loc_file, ".txt")
        tokens = self.tokens = load_tokens(loc_path) if os.path.isfile(loc_path) else {}
        weapons = weapons_from_items(items, tokens)
        if args.only:
            keep = {w.strip() for w in args.only.split(",")}
            weapons = {k: v for k, v in weapons.items() if k in keep}
        combos, icons = skin_combos(items, weapons, listing)
        gloves = gloves_from_items(items, tokens)
        if args.only:
            gloves = {k: v for k, v in gloves.items() if k in keep}
        gkits = glove_kits(items, listing)
        gcombos, gicons = glove_combos(gloves, gkits, listing)
        self.glove_data = (gloves, gkits, gcombos, gicons)
        print(f"{len(gloves)} paintable gloves, {len({k for _g, k in gcombos})} glove paint kits")
        kit_by_name = {k.get("name", ""): i for i, k in items.paint_kits.items()}
        kits_used = sorted({k for _w, k in combos})
        print(f"{len(weapons)} paintable weapons, {len(kits_used)} paint kits, {len(combos)} skins")

        vcompmats = {}
        for path in listing:
            if path.startswith("weapons/paints/") and path.endswith(".vcompmat_c"):
                # paint kit names and file names differ in case (cu_bizon_Curse)
                vcompmats.setdefault(os.path.basename(path)[:-len(".vcompmat_c")].lower(), path)

        if not args.skip_export:
            models = {w["model"] + "_c" for w in weapons.values()}
            print(f"2/6 models ({len(models)}, glTF + decompiled vmdl)")
            self.s2v(models, extensions="vmdl_c",
                     extra=("--gltf_export_format", "glb", "--gltf_export_materials"))
            self.s2v(models, extensions="vmdl_c")
            self.export_sticker_markup(weapons)
            self.export_rigs(weapons)
            vmats = set()
            for wname, info in weapons.items():
                vmats.update(v + "_c" for _l, _s, v in self.weapon_variants(wname, info))
            print(f"3/6 weapon materials ({len(vmats)}) and composite inputs")
            self.s2v(vmats, extensions="vmat_c")
            comp = set()
            for v in vmats:
                f = raw_path(self.raw, v, ".vmat")
                if os.path.isfile(f):
                    ci = parse_vmat(f)["attributes"].get("composite_inputs", "")
                    if ci:
                        comp.add(ci + "_c")
            self.s2v(comp, extensions="vmat_c")
            tex = set()
            for v in vmats | comp:
                f = raw_path(self.raw, v, ".vmat")
                if os.path.isfile(f):
                    tex.update(t + "_c" for k, t in parse_vmat(f)["compiled"].items() if "Sticker" not in k)
            self.s2v(tex, extensions="vtex_c")

            print(f"4/6 paint kits ({len(kits_used)}: composite + paint materials)")
            comps = {vcompmats[k.lower()] for k in kits_used if k.lower() in vcompmats}
            self.s2v(comps, extensions="vcompmat_c")
            self.export_includes(comps)
            paint_vmats = set()
            for k in kits_used:
                f = raw_path(self.raw, vcompmats[k.lower()], ".vcompmat") if k.lower() in vcompmats else ""
                if f and os.path.isfile(f):
                    for path in KV3.specific_materials(KV3.load(f)).values():
                        paint_vmats.add(path + "_c")
            self.s2v(paint_vmats, extensions="vmat_c")
            tex = set()
            for k in kits_used:
                proc = self.procedure(vcompmats[k.lower()]) if k.lower() in vcompmats else None
                if proc is not None:
                    tex.update(t + "_c" for t in compmat.textures(proc))
            print(f"5/6 paint textures ({len(tex)})")
            self.s2v(tex, extensions="vtex_c")
            icon_files = {p for (w, k) in combos for p in icons.get((w, k), {}).values()}
            print(f"6/6 inventory icons ({len(icon_files)}) and StatTrak/name tag modules")
            self.s2v(icon_files, extensions="vtex_c")
            self.export_modules()
            self.export_gloves(gloves, gkits, gcombos, gicons)

        elif args.sticker_markup or args.rig:
            game = self.game = find_cs2(args.cs2)
            self.vpk = os.path.join(game, "pak01_dir.vpk")
            self.exe = find_s2v(args.s2v, os.path.join(self.out, "_tools"), args.download_s2v)
            if args.sticker_markup:
                self.export_sticker_markup(weapons)
            if args.rig:
                self.export_rigs(weapons)

        self.build(weapons, items, tokens, combos, icons, kit_by_name, vcompmats)

    def export_sticker_markup(self, weapons):
        """The models' StickerMarkup (CS2's sticker slots) and KeychainMarkup
        (charm areas) as <raw>/<model>.sticker_markup.json and
        .keychain_markup.json; needs the compiled .vmdl_c."""
        models = {w["model"] + "_c" for w in weapons.values()}
        print(f"  sticker slots of {len(models)} models")
        self.s2v(models, extensions="vmdl_c", decompile=False)
        for model in sorted(models):
            vmdl_c = os.path.join(self.raw, model)
            if not os.path.isfile(vmdl_c):
                continue
            text = model_data_text(self.exe, vmdl_c)
            for ext, parse in ((".sticker_markup.json", sticker_markup), (".keychain_markup.json", keychain_markup)):
                with open(raw_path(self.raw, model[:-2], ext), "w", encoding="utf-8") as fh:
                    json.dump(parse(text), fh, indent=1)

    def rig_raw(self):
        """Raw folder of the rigged models: Source2Viewer names its output
        after the VPK path, the same as the static glb."""
        return os.path.join(self.raw, RIG_RAW)

    def export_rigs(self, weapons):
        """The models once more with skeleton and animations (bones weapon,
        weapon_offset, bolt, clip, ...; clips reload, shoot, ...), for
        spawning as an armature (cs.anim). Gloves too: their arm skeleton
        (arm_upper_L, hand_R, finger_..., as on the agents). Materials only
        for their names."""
        gloves = getattr(self, "glove_data", ({},))[0]
        models = {w["model"] + "_c" for w in list(weapons.values()) + list(gloves.values())}
        print(f"  rigs of {len(models)} models (skeleton and animations)")
        os.makedirs(self.rig_raw(), exist_ok=True)
        run_s2v(self.exe, self.vpk, self.rig_raw(), models, extensions="vmdl_c", threads=self.args.threads,
                extra=("--gltf_export_format", "glb", "--gltf_export_materials", "--gltf_export_animations"))

    def place_rig(self, model, glb, group):
        """Copy the rig glb of a model next to its static glb
        (<stem>.rig.glb). Returns its library path or ""."""
        rig = raw_path(self.rig_raw(), model, ".glb")
        if not os.path.isfile(rig):
            return ""
        stem = os.path.splitext(os.path.basename(glb))[0]
        dst = os.path.join(self.out, group, stem + RIG_SUFFIX)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(rig, dst)
        return os.path.relpath(dst, self.out).replace(os.sep, "/")

    def sticker_markup_of(self, model, ext=".sticker_markup.json"):
        """Cached StickerMarkup (or KeychainMarkup) of a model ({} if not
        exported)."""
        try:
            with open(raw_path(self.raw, model, ext), encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    # -----------------------------------------------------------------------

    def build(self, weapons, items, tokens, combos, icons, kit_by_name, vcompmats):
        out, raw = self.out, self.raw
        for name in os.listdir(out):
            path = os.path.join(out, name)
            if os.path.isdir(path) and not name.startswith("_"):
                shutil.rmtree(path)
        copied = {}

        def place(src, group):
            key = os.path.normcase(os.path.abspath(src))
            if key not in copied:
                dst = os.path.join(out, group, os.path.basename(src))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if group == "icons" or not self.args.max_texture_size:
                    shutil.copy2(src, dst)
                else:
                    copy_texture(src, dst, self.args.max_texture_size)
                copied[key] = os.path.relpath(dst, out).replace(os.sep, "/")
            return copied[key]

        def textures_of(vmat_path, group, prefix="", skip=("sticker", "glitter", "holo", "sfxmask")):
            """(textures by short key, parsed vmat, parameters and textures
            by full name) of a material, textures copied into ``group``."""
            f = raw_path(raw, vmat_path, ".vmat")
            if not os.path.isfile(f):
                return {}, {}, {}
            spec = parse_vmat(f)
            tex, full = {}, dict(spec["params"])
            for param, vtex in spec["compiled"].items():
                key = tex_key(param)
                if any(s in key for s in skip):
                    continue
                src = texture_file(raw, vtex)
                if src:
                    tex[prefix + key] = full[param] = place(src, group)
                else:
                    self.warnings.append(f"{vmat_path}: texture not exported ({vtex})")
            return tex, spec, full

        def library_ops(ops, where):
            """Ops with VPK texture paths replaced by library paths."""
            out = []
            for op in ops:
                if "set" in op:
                    values = {}
                    for k, v in op["set"].items():
                        if v is None:
                            continue
                        if k.startswith("g_t") and isinstance(v, str):
                            src = texture_file(raw, v)
                            if not src:
                                self.warnings.append(f"{where}: texture not exported ({v})")
                                continue
                            v = place(src, "paints")
                        values[k] = v
                    op = {**op, "set": values}
                out.append(op)
            return out

        cat_weapons = []
        for wname, info in sorted(weapons.items()):
            glb = raw_path(raw, info["model"], ".glb")
            if not os.path.isfile(glb):
                self.warnings.append(f"{wname}: model not exported ({info['model']})")
                continue
            group = f"weapons/{wname}"
            entry = {"id": wname, "def_index": info["def_index"], "name": info["name"], "type": info["type"]}
            for uri in glb_images(glb):
                src = os.path.join(os.path.dirname(glb), uri)
                if os.path.isfile(src):
                    place(src, group)
            entry["model"] = place(glb, group)
            rig = self.place_rig(info["model"], glb, group)
            if rig:
                entry["rig"] = rig
            for legacy, suffix, vmat in self.weapon_variants(wname, info):
                vanilla, vspec, target = textures_of(vmat, group, "vanilla_")
                ci = (vspec or {}).get("attributes", {}).get("composite_inputs", "") if vspec else ""
                comp, cspec, inputs = textures_of(ci, group) if ci else ({}, {}, {})
                params = (cspec or {}).get("params", {})
                paint = info["paint"]
                variant = {
                    "mesh": suffix,
                    "vmat": vmat,
                    "uv_scale": float(params.get("g_flUvScale1", paint.get("UVScale", 1.0))),
                    "weapon_length": float(params.get("g_flWeaponLength1", paint.get("WeaponLength", 36.0))),
                    "separate_channels": bool(params.get("F_SEPARATE_CHANNEL_INPUTS", 0)),
                    "textures": {**vanilla, **comp},
                    "composite_inputs": inputs,
                    "target": target,
                }
                markup = self.sticker_markup_of(info["model"]).get("legacy" if legacy else "hd")
                if markup:
                    variant["sticker_markup"] = markup
                if not ci:  # not paintable (C4 display, dual berettas holster)
                    continue
                key = "legacy" if legacy else "hd"
                if key in entry:
                    self.warnings.append(f"{wname}: second {key} mesh {suffix} ignored")
                    continue
                entry[key] = variant
            if "hd" not in entry and "legacy" in entry:
                entry["hd"] = entry.pop("legacy")
            if "hd" not in entry:
                self.warnings.append(f"{wname}: no paintable mesh, skipped")
                continue
            vmdl_file = raw_path(raw, info["model"], ".vmdl")
            entry["attachments"] = vmdl_attachments(vmdl_file)
            # the game's default charm spot; cs.weapon prefers it over the
            # older keychain attachment
            areas = self.sticker_markup_of(info["model"], ".keychain_markup.json")
            for key, suffix in (("hd", ""), ("legacy", "_legacy")):
                att = keychain_attachment(areas.get(key))
                if att is not None:
                    entry["attachments"]["keychain_markup" + suffix] = att
            entry["camera"] = info["camera"]
            cat_weapons.append(entry)

        modules = {}
        for key, (model, vmats) in MODULES.items():
            glb = raw_path(raw, model, ".glb")
            if not os.path.isfile(glb):
                self.warnings.append(f"module {key}: model not exported ({model})")
                continue
            entry = {"model": place(glb, "modules"), "materials": {}}
            for vmat in vmats:
                tex, spec, full = textures_of(vmat, "modules", skip=())
                if spec:
                    entry["materials"][os.path.basename(vmat)[:-len(".vmat")]] = full
            if key == "nametag":
                entry["text"] = nametag_text(self.load_vcompmat(NAMETAG_VCOMPMAT))
            modules[key] = entry

        cat_kits, kit_ids = [], set()
        for kname in sorted({k for _w, k in combos}):
            kid = kit_by_name.get(kname)
            raw_kit = items.paint_kits.get(kid, {})
            vcomp = vcompmats.get(kname.lower())
            proc = self.procedure(vcomp) if vcomp else None
            if proc is None or not proc["passes"]:
                self.warnings.append(f"paint kit {kname}: no composite material")
                continue
            passes = {t: library_ops(ops, kname) for t, ops in proc["passes"].items()}
            first = PROC.resolve(passes.get(PROC.PASSES[0], []), {})
            kit = {
                "id": kid, "name": kname,
                "display": tr(tokens, raw_kit.get("description_tag", ""), kname),
                "style": int(raw_kit.get("style", PROC.feature(first, "F_PAINT_STYLE") + 1)),
                "wear_min": float(raw_kit.get("wear_remap_min", 0.0)),
                "wear_max": float(raw_kit.get("wear_remap_max", 1.0)),
                "legacy": raw_kit.get("use_legacy_model") == "1",
                "rarity": items.rarity.get(kname, ""),
                "icon_seed": int(raw_kit.get("seed", 0) or 0),
                "passes": passes,
                "final": library_ops(proc["final"], kname),
                "roll": proc["roll"],
                "vcompmat": vcomp,
            }
            cat_kits.append(kit)
            kit_ids.add(kid)

        weapon_ids = {w["id"] for w in cat_weapons}
        cat_skins = []
        for wname, kname in combos:
            kid = kit_by_name.get(kname)
            if wname not in weapon_ids or kid not in kit_ids:
                continue
            skin = {"weapon": wname, "paint_kit": kid}
            for wear, path in icons.get((wname, kname), {}).items():
                src = texture_file(raw, path)
                if src:
                    skin["icon" if wear == "light" else f"icon_{wear}"] = place(src, "icons")
            cat_skins.append(skin)

        shaders = self.export_shaders(cat_weapons, cat_kits, cat_skins)
        g_weapons, g_kits, g_skins = self.build_gloves(place, library_ops)
        glove_shaders = self.export_glove_shaders(g_kits)
        cat_weapons += g_weapons
        cat_kits += g_kits
        cat_skins += g_skins

        catalog = {
            "version": C.CATALOG_VERSION,
            "generator": "cs.weapon tools/export_cs2_weapons.py",
            "note": "Valve game assets exported from a local CS2 install. Personal use only.",
            "weapons": cat_weapons,
            "paint_kits": cat_kits,
            "skins": cat_skins,
            "shaders": shaders,
            "glove_shaders": glove_shaders,
            "modules": modules,
        }
        with open(os.path.join(out, C.CATALOG_FILE), "w", encoding="utf-8", newline="\n") as fh:
            json.dump(catalog, fh, indent=1, ensure_ascii=False)
        self.report(catalog)

    def export_shaders(self, cat_weapons, cat_kits, cat_skins):
        """Write the paint shader combos the skins need into <out>/shaders.
        Returns the index path relative to the library ("" on failure)."""
        folder = os.path.join(self.out, "shaders")
        try:
            game = self.game or find_cs2(self.args.cs2)
            exe = self.exe or find_s2v(self.args.s2v, os.path.join(self.out, "_tools"), self.args.download_s2v)
            print("paint shader")
            sh = cs2shader.ShaderExport(exe, game, os.path.join(self.raw, "_shader"), KV3)
        except (cs2shader.ShaderError, OSError, subprocess.CalledProcessError, SystemExit) as exc:
            self.warnings.append(f"paint shader not exported: {exc}")
            return ""
        index, cmax = sh.combo_index(), sh.combo_max()
        weapons = {w["id"]: w for w in cat_weapons}
        kits = {k["id"]: k for k in cat_kits}
        ids = set()
        for skin in cat_skins:
            w, kit = weapons[skin["weapon"]], kits[skin["paint_kit"]]
            var = w["legacy"] if kit["legacy"] and "legacy" in w else w["hd"]
            weapon = {"composite_inputs": var["composite_inputs"], "target": var["target"]}
            for target in PROC.PASSES:
                ids.add(PROC.combo_id(PROC.resolve(kit["passes"].get(target, []), weapon), index, cmax))
        written, missing = sh.write(ids, folder)
        for cid in missing:
            self.warnings.append(f"paint shader combo {cid} not in the game's shader")
        print(f"  {len(written)} shader combos")
        return "shaders/index.json"

    def report(self, catalog):
        for w in self.warnings[:200]:
            print("  warning: " + w)
        if len(self.warnings) > 200:
            print(f"  ... {len(self.warnings) - 200} more warnings")
        size = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(self.out)
                   for f in fs if not os.path.relpath(d, self.out).startswith("_"))
        by_type = {}
        for w in catalog["weapons"]:
            by_type[w["type"]] = by_type.get(w["type"], 0) + 1
        legacy = sum(1 for w in catalog["weapons"] if "legacy" in w)
        with_icon = sum(1 for s in catalog["skins"] if "icon" in s)
        print(f"\n{len(catalog['weapons'])} weapons ({', '.join(f'{k} {v}' for k, v in sorted(by_type.items()))}),"
              f" {legacy} with legacy mesh")
        print(f"{len(catalog['paint_kits'])} paint kits, {len(catalog['skins'])} skins ({with_icon} with icon)")
        print(f"-> {os.path.join(self.out, C.CATALOG_FILE)}, library {size / 2**30:.1f} GiB")
        print("Reminder: these are Valve's assets. Personal use only, do not redistribute.")


def main():
    ap = argparse.ArgumentParser(description="Export CS2 weapons and skins into a cs.weapon library.")
    ap.add_argument("--out", required=True, help="library folder to create/update")
    ap.add_argument("--cs2", default="", help="CS2 install folder (auto-detected via Steam)")
    ap.add_argument("--s2v", default="", help="path to Source2Viewer-CLI(.exe)")
    ap.add_argument("--download-s2v", action="store_true", help="download Source2Viewer-CLI if not found")
    ap.add_argument("--language", default="english", help="localization for names (default english)")
    ap.add_argument("--only", default="", help="comma separated weapon item names, e.g. weapon_ak47")
    ap.add_argument("--threads", type=int, default=max(2, (os.cpu_count() or 4) - 1))
    ap.add_argument("--skip-export", action="store_true", help="reuse an existing <out>/_raw export")
    ap.add_argument("--sticker-markup", action="store_true",
                    help="with --skip-export: read the sticker slots and charm areas of the models again (needs CS2)")
    ap.add_argument("--rig", action="store_true",
                    help="with --skip-export: export the models with skeleton and animations "
                         "(<model>.rig.glb, spawn as armature) into an existing library (needs CS2)")
    ap.add_argument("--max-texture-size", type=int, default=0, metavar="PX",
                    help="downscale weapon and paint textures to at most PX per side, e.g. 2048 "
                         "(default 0: full quality; needs pillow). Combine with --skip-export "
                         "to shrink an existing library without exporting again.")
    Exporter(ap.parse_args()).run()


if __name__ == "__main__":
    main()
