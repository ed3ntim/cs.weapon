# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Weapon library: weapons, paint kits and skins from ``catalog.json``.

Pure Python (no bpy), unit tested in tests/test_library.py.

A library folder holds ``catalog.json`` plus the files it references
(paths relative to the folder). Several libraries are merged: the core
demo set, a bundled personal set and the user's export. Later libraries
override earlier ones with the same id.

catalog.json, version 2 (version 1 lacks the procedure fields)::

    {
      "version": 2,
      "weapons": [{
        "id": "weapon_ak47", "def_index": 7, "name": "AK-47", "type": "rifle",
        "model": "models/weapon_rif_ak47.glb",
        "hd": {"mesh": "body_hd", "uv_scale": 0.772, "weapon_length": 37.287,
               "separate_channels": true,
               "textures": {"color": "...", "masks": "...", "cavity": "...", ...},
               "composite_inputs": {"g_flUvScale1": 0.772, "g_tMasks": "...", ...},
               "target": {"g_tMetalness": "...", ...}},
        "legacy": {"mesh": "body_legacy", "uv_scale": 0.549, "weapon_length": 37.746,
                   "textures": {...}},
        "attachments": {"stattrak": {"origin": [x, y, z], "angles": [p, y, r],
                                     "bone": "weapon_offset"}, ...}
      }],
      "paint_kits": [{
        "id": 44, "name": "aq_oiled", "display": "Case Hardened",
        "style": 8, "wear_min": 0.0, "wear_max": 1.0, "legacy": true,
        "passes": {"g_tColor": [op, ...], "g_tMetalness": [op, ...]},
        "final": [op, ...],
        "roll": [["g_vPatternTexCoordOffset", 2, [[0, 0], [1, 1]], [0, 0]], ...]
      }],
      "skins": [{"weapon": "weapon_ak47", "paint_kit": 44, "icon": "..."}],
      "shaders": "shaders/index.json",
      "modules": {
        "stattrak": {"model": "modules/stattrak_module.glb",
                     "materials": {"stattrak_module": {"g_tColor": "...", ...},
                                   "stattrak_module_display": {...}}},
        "stattrak_knife": {...},
        "nametag": {..., "text": {"position": [0.04, 0.038],
                                  "color": [241, 241, 241], "font": "Arial Narrow"}}
      }
    }

Gloves are weapons of type ``gloves`` with a ``glove`` entry instead of
``hd``/``legacy`` (both hands are one mesh with one material per hand)::

    {"id": "sporty_gloves", "def_index": 5030, "name": "Sport Gloves", "type": "gloves",
     "model": "gloves/sporty_gloves/glove_sporty.glb",
     "glove": {"mesh": "viewmodel", "arm": "bare_arm_133",
               "materials": {"glove_sporty_left": {"attributes": {"left": "1"},
                                                   "textures": {"g_tColor": "...", ...}},
                             "glove_sporty_right": {"attributes": {"seed_offset": "1"}, ...}}}}

Their paint kits carry ``"glove": true`` and use the glove composite
shader (``glove_shaders``: index of csgo_customglove); ``rolls`` lists the
roll mutators with their condition (the right hand rolls with another
seed). Gloves have no vanilla skin.

Attachments carry ``matrix``: the attachment in model space (Source
inches, row-major 4x4) composed with its bone chain.

Ops and the roll list come from the paint kit's composite material (see
tools/compmat.py and library/procedure.py). Texture values (keys starting
with ``g_t``) are library paths and made absolute on load.

Version 1 paint kits carry ``params``, ``textures`` and ``ranges``
instead; they get the simple material (nodes/material.py).
"""

import json
import os

CATALOG_FILE = "catalog.json"
CATALOG_VERSION = 2

# Paint kit id of the unpainted default finish. Every weapon gets a vanilla
# skin; it is not part of catalog.json.
VANILLA_ID = 0
# Sticker slots of the weapon shader (g_tSticker0..4)
STICKER_SLOTS = 5

# items_game "style" numbers (F_PAINT_STYLE = style - 1), 0 = vanilla
STYLES = {
    0: "Vanilla",
    1: "Solid Color",
    2: "Hydrographic",
    3: "Spray-Paint",
    4: "Anodized",
    5: "Anodized Multicolored",
    6: "Anodized Airbrushed",
    7: "Custom Paint Job",
    8: "Patina",
    9: "Gunsmith",
}

WEAPON_TYPES = ("pistol", "smg", "rifle", "sniper", "shotgun", "machinegun", "knife", "gloves", "other")


def _texture_paths(values, root):
    """Copy of a parameter dict with texture paths made absolute."""
    return {k: os.path.join(root, v) if k.startswith("g_t") and isinstance(v, str) and v else v
            for k, v in (values or {}).items()}


def _ops(ops, root):
    out = []
    for op in ops or []:
        if "set" in op:
            op = {**op, "set": _texture_paths(op["set"], root)}
        out.append(op)
    return out


def _op_textures(ops):
    return [v for op in ops for k, v in op.get("set", {}).items() if k.startswith("g_t") and isinstance(v, str)]


class Report:
    def __init__(self):
        self.warnings = []
        self.libraries = []

    def warn(self, msg):
        self.warnings.append(msg)


class Variant:
    """One mesh of a weapon model (HD or legacy UV layout) with its
    composite inputs."""

    def __init__(self, data, root, legacy):
        self.legacy = legacy
        self.mesh = data.get("mesh", "")
        self.uv_scale = float(data.get("uv_scale", 1.0))
        self.weapon_length = float(data.get("weapon_length", 36.0))
        self.separate_channels = bool(data.get("separate_channels", False))
        self.textures = {k: os.path.join(root, v) for k, v in data.get("textures", {}).items() if v}
        self.composite_inputs = _texture_paths(data.get("composite_inputs"), root)
        self.target = _texture_paths(data.get("target"), root)
        self.sticker_markup = list(data.get("sticker_markup") or [])

    def sticker_slots(self):
        """Sticker slots of this mesh: [{"slot", "offset": (x, y), "scale",
        "rotation", "deprecated"}]. rotation is the slot's own tilt in turns
        (the shader's g_flSticker<k>Rotation); the item's rotation adds to it. The game maps a sticker through the second UV map
        (TEXCOORD_1, v from the top): sticker uv = rotate((uv1 - 0.5 -
        offset) * scale) + 0.5, so the slot center is uv1 = 0.5 + offset and
        the sticker spans 1 / scale.

        Source: the model's StickerMarkup (CS2's slots, often with a fifth
        one; a moved slot keeps its old entry as "deprecated"), exported
        into ``sticker_markup``. Older libraries without it fall back to
        the weapon material (g_vSticker<k>Offset/Scale, the old values)."""
        if self.sticker_markup:
            out = []
            for m in self.sticker_markup:
                slot, scale, off = int(m.get("slot", -1)), float(m.get("scale", 0.0)), m.get("offset") or ()
                if 0 <= slot < STICKER_SLOTS and scale > 0.0 and len(off) >= 2:
                    out.append({"slot": slot, "offset": (float(off[0]), float(off[1])), "scale": scale,
                                "rotation": float(m.get("rotation", 0.0)),
                                "deprecated": bool(m.get("deprecated"))})
            return sorted(out, key=lambda s: s["slot"])
        out = []
        for k in range(STICKER_SLOTS):
            off = self.target.get(f"g_vSticker{k}Offset")
            scale = self.target.get(f"g_vSticker{k}Scale")
            if not isinstance(off, (list, tuple)) or not isinstance(scale, (list, tuple)) or len(off) < 2:
                continue
            s = abs(float(scale[0])) if scale else 0.0
            if s <= 0.0 or (len(scale) > 1 and float(scale[1]) == 0.0):
                continue
            rot = self.target.get(f"g_flSticker{k}Rotation", 0.0)
            rot = float(rot[0] if isinstance(rot, (list, tuple)) and rot else rot or 0.0)
            out.append({"slot": k, "offset": (float(off[0]), float(off[1])), "scale": s, "rotation": rot,
                        "deprecated": False})
        return out


class GloveInfo:
    """Mesh and the two hand materials of a glove model."""

    def __init__(self, data, root):
        self.mesh = data.get("mesh", "")
        self.arm = data.get("arm", "")
        self.materials = {}
        for name, m in (data.get("materials") or {}).items():
            self.materials[name] = {
                "attributes": {k: str(v) for k, v in (m.get("attributes") or {}).items()},
                "textures": _texture_paths(m.get("textures"), root),
            }

    def hand(self, name):
        """"left" or "right" for a material name, "" for others."""
        m = self.materials.get(name)
        if m is None:
            return ""
        return "left" if m["attributes"].get("left") == "1" else "right"


class Module:
    """StatTrak or name tag module: model and materials (paths absolute)."""

    def __init__(self, key, data, root):
        self.key = key
        model = data.get("model", "")
        self.model = os.path.join(root, model) if model else ""
        self.materials = {name: _texture_paths(values, root)
                          for name, values in (data.get("materials") or {}).items()}
        self.text = dict(data.get("text") or {})

    def missing_files(self):
        files = [self.model] + [v for m in self.materials.values() for k, v in m.items()
                                if k.startswith("g_t") and isinstance(v, str)]
        return [f for f in files if not os.path.isfile(f)]


class Weapon:
    def __init__(self, data, root):
        self.root = root
        self.id = str(data["id"])
        self.def_index = int(data.get("def_index", 0))
        self.name = data.get("name", self.id)
        self.type = data.get("type", "other")
        self.model = data.get("model", "")
        self.rig = data.get("rig", "")  # same model with skeleton and animations
        self.hd = Variant(data.get("hd", {}), root, False)
        self.legacy = Variant(data["legacy"], root, True) if data.get("legacy") else None
        self.attachments = dict(data.get("attachments", {}))
        self.glove = GloveInfo(data["glove"], root) if data.get("glove") else None

    @property
    def is_glove(self):
        return self.glove is not None

    def model_path(self):
        return os.path.join(self.root, self.model) if self.model else ""

    def rig_path(self):
        return os.path.join(self.root, self.rig) if self.rig else ""

    def has_rig(self, isfile=os.path.isfile):
        return bool(self.rig) and isfile(self.rig_path())

    def variant(self, legacy=False):
        """The legacy variant if asked for and present, else HD."""
        return self.legacy if legacy and self.legacy is not None else self.hd

    def has_legacy(self):
        return self.legacy is not None

    @property
    def is_knife(self):
        return self.type == "knife"

    def attachment_matrix(self, name, legacy=False):
        """Model-space matrix (16 floats, Source inches) of an attachment,
        the ``_legacy`` one for the legacy mesh if present, else None."""
        for key in ((f"{name}_legacy", name) if legacy else (name,)):
            att = self.attachments.get(key)
            if isinstance(att, dict) and len(att.get("matrix") or []) == 16:
                return [float(v) for v in att["matrix"]]
        return None

    def missing_files(self, isfile=os.path.isfile):
        files = [self.model_path(), *self.hd.textures.values()]
        if self.glove is not None:
            files += [v for m in self.glove.materials.values() for v in m["textures"].values()
                      if isinstance(v, str)]
        if self.legacy is not None:
            files += self.legacy.textures.values()
        return [f for f in files if f and not isfile(f)]


# Doppler and Gamma Doppler: the game names every phase and gem paint kit
# the same; the kit's code name tells them apart (am_doppler_phase2_b,
# am_ruby_marbleized, am_emerald_marbleized_glock ...)
PHASES = (("phase1", "Phase 1"), ("phase2", "Phase 2"), ("phase3", "Phase 3"), ("phase4", "Phase 4"),
          ("ruby", "Ruby"), ("sapphire", "Sapphire"), ("blackpearl", "Black Pearl"),
          ("emerald", "Emerald"))


def phase_of(code_name):
    """Phase or gem of a Doppler-type paint kit ("" otherwise)."""
    code = code_name.lower()
    if not code.startswith("am_") or ("doppler" not in code and "marbleized" not in code):
        return ""
    return next((label for key, label in PHASES if key in code), "")


class PaintKit:
    def __init__(self, data, root, shaders=""):
        self.root = root
        self.shaders = shaders  # absolute path of the shader index ("" if none)
        self.id = int(data["id"])
        self.name = data.get("name", str(self.id))
        self.display = data.get("display", self.name)
        self.phase = phase_of(self.name)
        self.style = int(data.get("style", 1))
        self.wear_min = float(data.get("wear_min", 0.0))
        self.wear_max = float(data.get("wear_max", 1.0))
        self.legacy = bool(data.get("legacy", False))
        self.params = dict(data.get("params", {}))
        self.textures = dict(data.get("textures", {}))
        self.ranges = {k: (v[0], v[1]) for k, v in data.get("ranges", {}).items()}
        self.passes = {k: _ops(v, root) for k, v in data.get("passes", {}).items()}
        self.final = _ops(data.get("final"), root)
        self.roll = [list(r) for r in data.get("roll", [])]
        self.rolls = [dict(r) for r in data.get("rolls", [])]
        self.glove = bool(data.get("glove", False))

    @property
    def is_vanilla(self):
        return self.id == VANILLA_ID

    @property
    def style_name(self):
        return STYLES.get(self.style, f"Style {self.style}")

    def texture_paths(self):
        return {k: os.path.join(self.root, v) for k, v in self.textures.items() if v}

    def missing_files(self, isfile=os.path.isfile):
        files = list(self.texture_paths().values())
        for ops in list(self.passes.values()) + [self.final]:
            files += _op_textures(ops)
        return sorted({f for f in files if not isfile(f)})

    def seed_bounds(self):
        """Bounds for seed.roll_paint: {name: (mins, maxs)}."""
        return dict(self.ranges)


class Skin:
    def __init__(self, data, root, weapon, paint_kit):
        self.weapon = weapon
        self.paint_kit = paint_kit
        icon = data.get("icon", "")
        self.icon = os.path.join(root, icon) if icon else ""

    @property
    def id(self):
        return skin_id(self.weapon.id, self.paint_kit.id)

    @property
    def is_vanilla(self):
        return self.paint_kit.is_vanilla

    @property
    def name(self):
        if self.is_vanilla:
            return f"{self.weapon.name} (Vanilla)"
        kit = self.paint_kit
        return f"{self.weapon.name} | {kit.display}" + (f" ({kit.phase})" if kit.phase else "")

    def missing_files(self):
        if self.is_vanilla:
            return self.weapon.missing_files()
        return self.weapon.missing_files() + self.paint_kit.missing_files()


def skin_id(weapon_id, paint_kit_id):
    return f"{weapon_id}:{paint_kit_id}"


class Catalog:
    def __init__(self):
        self.weapons = {}
        self.paint_kits = {}
        self.skins = {}
        self.modules = {}
        self.report = Report()
        self._incomplete = {}

    def load(self, *dirs):
        """Load and merge libraries (empty or missing folders are skipped)."""
        self.weapons, self.paint_kits, self.skins, self.modules = {}, {}, {}, {}
        self.report = Report()
        self._incomplete = {}
        for d in dirs:
            if d and os.path.isfile(os.path.join(d, CATALOG_FILE)):
                self._load_one(d)
        return self

    def incomplete(self, skin):
        """Whether files of a skin are missing. Cached per load: the list
        asks for every skin on each search, and checking the files of all
        skins takes seconds (refresh the library after adding files)."""
        weapon = self._incomplete.get(("w", skin.weapon.id))
        if weapon is None:
            weapon = self._incomplete[("w", skin.weapon.id)] = bool(skin.weapon.missing_files(self._isfile))
        if weapon or skin.is_vanilla:
            return weapon
        kit = self._incomplete.get(("k", skin.paint_kit.id))
        if kit is None:
            kit = self._incomplete[("k", skin.paint_kit.id)] = bool(skin.paint_kit.missing_files(self._isfile))
        return kit

    def _isfile(self, path):
        found = self._incomplete.get(path)  # paint kits share many textures
        if found is None:
            found = self._incomplete[path] = os.path.isfile(path)
        return found

    def _load_one(self, root):
        path = os.path.join(root, CATALOG_FILE)
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            self.report.warn(f"{path}: {exc}")
            return
        if int(data.get("version", 0)) > CATALOG_VERSION:
            self.report.warn(f"{path}: catalog version {data.get('version')} is newer than supported")
        self.report.libraries.append(root)
        shaders = os.path.join(root, data["shaders"]) if data.get("shaders") else ""
        glove_shaders = os.path.join(root, data["glove_shaders"]) if data.get("glove_shaders") else ""
        for key, mod in (data.get("modules") or {}).items():
            if isinstance(mod, dict):
                self.modules[key] = Module(key, mod, root)
        for w in data.get("weapons", []):
            try:
                weapon = Weapon(w, root)
            except (KeyError, ValueError, TypeError) as exc:
                self.report.warn(f"{path}: bad weapon entry {w!r:.60}: {exc}")
                continue
            self.weapons[weapon.id] = weapon
        for p in data.get("paint_kits", []):
            try:
                kit = PaintKit(p, root, glove_shaders if p.get("glove") else shaders)
            except (KeyError, ValueError, TypeError) as exc:
                self.report.warn(f"{path}: bad paint kit entry {p!r:.60}: {exc}")
                continue
            self.paint_kits[kit.id] = kit
        for s in data.get("skins", []):
            weapon = self.weapons.get(str(s.get("weapon", "")))
            kit = self.paint_kits.get(int(s.get("paint_kit", -1)))
            if weapon is None or kit is None:
                self.report.warn(f"{path}: skin {s.get('weapon')}:{s.get('paint_kit')} "
                                 "refers to an unknown weapon or paint kit")
                continue
            skin = Skin(s, root, weapon, kit)
            self.skins[skin.id] = skin
        vanilla = self.paint_kits.setdefault(VANILLA_ID, PaintKit(
            {"id": VANILLA_ID, "name": "vanilla", "display": "Vanilla", "style": 0}, root))
        for wid in {str(w.get("id", "")) for w in data.get("weapons", [])}:
            weapon = self.weapons.get(wid)
            if weapon is not None and not weapon.is_glove:
                skin = Skin({}, root, weapon, vanilla)
                self.skins[skin.id] = skin

    def get_skin(self, sid):
        return self.skins.get(sid)

    def weapon_by_def_index(self, def_index):
        for w in self.weapons.values():
            if w.def_index == def_index:
                return w
        return None

    def skin_for_item(self, def_index, paint_index):
        """Skin of an item definition and paint kit (0 = vanilla), or None."""
        weapon = self.weapon_by_def_index(def_index)
        if weapon is None:
            return None
        return self.skins.get(skin_id(weapon.id, paint_index))

    def weapon_types(self):
        present = {w.type for w in self.weapons.values()}
        return [t for t in WEAPON_TYPES if t in present]

    def styles(self):
        return sorted({k.style for k in self.paint_kits.values()})

    def weapon_list(self, weapon_type=""):
        """[(id, name)] of the weapons (of one type), sorted by name."""
        ws = [w for w in self.weapons.values() if not weapon_type or w.type == weapon_type]
        return sorted(((w.id, w.name) for w in ws), key=lambda x: x[1].lower())

    def filter(self, search="", weapon_type="", style=None, weapon_id=""):
        """Skins matching all given filters, sorted by weapon then skin name
        (vanilla first). ``search`` matches words in weapon name, skin name
        or paint kit name."""
        words = search.lower().split()
        out = []
        for skin in self.skins.values():
            if weapon_type and skin.weapon.type != weapon_type:
                continue
            if weapon_id and skin.weapon.id != weapon_id:
                continue
            if style is not None and skin.paint_kit.style != style:
                continue
            kit = skin.paint_kit
            hay = f"{skin.weapon.name} {kit.display} {kit.phase} {kit.name}".lower()
            if all(w in hay for w in words):
                out.append(skin)
        order = {label: i for i, (_k, label) in enumerate(PHASES)}
        out.sort(key=lambda s: (s.weapon.name.lower(), not s.is_vanilla, s.paint_kit.display.lower(),
                                order.get(s.paint_kit.phase, -1)))
        return out
