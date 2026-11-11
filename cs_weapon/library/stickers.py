# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Stickers for the weapon sticker slots, read from a cs.sticker library.

Pure Python (no bpy). Same reader as cs.charm's Sticker Slab: only the
``catalog.json`` that cs.sticker's exporter writes
(tools/export_cs2_stickers.py) or that ships with cs.sticker's core set
is read, cs_sticker code is never imported::

    {"stickers": [{
        "id": "cs2_343_eslkatowice2015_titan_holo", "name": "Titan (Holo)",
        "collection": "Katowice 2015", "finish": "HOLO",
        "textures": {"base": "textures/...png", "holomask": "...", ...},
        "params": {"Wear Bias": 0.5},
        "tags": ["eslkatowice2015/titan_holo", "mythical"],
        # newer catalogs (cs.sticker-dev#10), optional:
        "rarity": "mythical", "event": "Katowice 2015", "kit_id": 343
    }]}

The sticker kit id (the ``id`` of a sticker in an inspect link) is
``kit_id`` or the number in ``cs2_<kit id>_<material>``. Texture paths are
relative to the catalog file.
"""

import json
import os
import re
from dataclasses import dataclass, field

CATALOG_FILE = "catalog.json"

FINISHES = (
    ("PAPER", "Paper"),
    ("GLITTER", "Glitter"),
    ("HOLO", "Holo"),
    ("FOIL", "Foil"),
    ("GOLD", "Gold"),
    ("LENTICULAR", "Lenticular"),
    ("EMBROIDERED", "Embroidered"),
)
FINISH_LABELS = dict(FINISHES)
DEFAULT_FINISH = "PAPER"

# Texture roles of cs.sticker catalogs (cs_sticker/library/finishes.py).
ROLE_BASE = "base"
ROLE_NORMAL = "normal"
ROLE_HOLOMASK = "holomask"
ROLE_HOLOSPECTRUM = "holospectrum"
ROLE_GLITTER = "glitter"
ROLE_ROUGHNESS = "roughness"
ROLE_METALNESS = "metalness"
ROLE_EMISSION = "emission"
ROLE_WEARMASK = "wearmask"
ROLE_SCRATCHES = "scratches"
ROLE_FLAKES = "flakes"
ROLES = (ROLE_BASE, ROLE_NORMAL, ROLE_HOLOMASK, ROLE_HOLOSPECTRUM, ROLE_GLITTER, ROLE_ROUGHNESS,
         ROLE_METALNESS, ROLE_EMISSION, ROLE_WEARMASK, ROLE_SCRATCHES, ROLE_FLAKES)
DATA_ROLES = frozenset(ROLES) - {ROLE_BASE, ROLE_HOLOSPECTRUM}

# CS2 wear masks: 0 outside the shape, inside > 0. Some masks (Katowice
# 2019, cs.sticker #14) have 4/255 outside. Shape = mask > floor + ~0.4 %.
MASK_SHAPE_GAIN = 128.0
MASK_FLOOR_MAX = 8.0 / 255.0

RARITIES = ("common", "uncommon", "rare", "mythical", "legendary", "ancient", "contraband")
RARITY_ALIASES = {"immortal": "contraband", "default": "common"}

_KIT_ID = re.compile(r"^cs2_(\d+)_")
_FINISH_SUFFIX = re.compile(r"\s*\(([^()]+)\)\s*$")


def normalize_rarity(value):
    value = (value or "").strip().lower()
    return RARITY_ALIASES.get(value, value)


def mask_floor(red):
    """Background level of a wear mask (as in cs.sticker): median of the four
    4x4 corners of the red channel (2D numpy array), 0 above MASK_FLOOR_MAX."""
    import numpy as np
    k = 4
    corners = np.concatenate([red[:k, :k].ravel(), red[:k, -k:].ravel(),
                              red[-k:, :k].ravel(), red[-k:, -k:].ravel()])
    floor = float(np.median(corners)) if corners.size else 0.0
    return floor if 0.0 < floor <= MASK_FLOOR_MAX else 0.0


def _param(value):
    """Catalog param usable as a group input: a number or a color (3-4 numbers)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    return (isinstance(value, (list, tuple)) and len(value) in (3, 4)
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value))


def make_id(*parts):
    text = "_".join(p for p in parts if p)
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "sticker"


def kit_id_from(entry_id):
    """Sticker kit id from a cs.sticker export id ("cs2_343_..."), else 0."""
    m = _KIT_ID.match(entry_id or "")
    return int(m.group(1)) if m else 0


@dataclass
class Sticker:
    id: str
    name: str                                     # "Titan (Holo)"
    collection: str = ""
    finish: str = DEFAULT_FINISH
    rarity: str = ""
    event: str = ""
    kit_id: int = 0                               # sticker kit id of the game, 0 = unknown
    textures: dict = field(default_factory=dict)  # role -> absolute path
    params: dict = field(default_factory=dict)    # finish group inputs, e.g. {"Wear Bias": 0.6}
    tags: tuple = ()
    origin: str = ""

    @property
    def finish_label(self):
        return FINISH_LABELS.get(self.finish, self.finish.title())

    @property
    def base_path(self):
        return self.textures.get(ROLE_BASE, "")

    @property
    def full_name(self):
        return f"{self.name} | {self.event}" if self.event else self.name

    def search_text(self):
        return " ".join((self.name, self.collection, self.event, self.finish, self.finish_label,
                         self.rarity, " ".join(self.tags))).lower()

    def to_dict(self):
        """JSON-able copy, stored on weapon objects so their stickers keep
        working (wear, rebuild) without the library."""
        return {"id": self.id, "name": self.name, "collection": self.collection,
                "finish": self.finish, "rarity": self.rarity, "event": self.event,
                "kit_id": self.kit_id, "textures": dict(self.textures), "params": dict(self.params)}

    @classmethod
    def from_dict(cls, data):
        return cls(id=str(data.get("id", "")), name=str(data.get("name", "")),
                   collection=str(data.get("collection", "")),
                   finish=str(data.get("finish") or DEFAULT_FINISH).upper(),
                   rarity=normalize_rarity(data.get("rarity")), event=str(data.get("event", "")),
                   kit_id=_int(data.get("kit_id")) or kit_id_from(str(data.get("id", ""))),
                   textures=dict(data.get("textures") or {}), params=dict(data.get("params") or {}))


def _int(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _abspath(base_dir, path):
    path = str(path).replace("\\", "/")
    return os.path.normpath(path if os.path.isabs(path) else os.path.join(base_dir, path))


def _rarity_from_tags(tags):
    known = set(RARITIES) | set(RARITY_ALIASES)
    for tag in reversed(tags):
        if tag.lower() in known:
            return normalize_rarity(tag)
    return ""


def sticker_from_entry(entry, base_dir, origin=""):
    """Sticker from one catalog entry, or None if it is unusable."""
    if not isinstance(entry, dict):
        return None
    name = str(entry.get("name") or "").strip()
    textures_in = entry.get("textures") or {}
    if not name or not isinstance(textures_in, dict):
        return None
    collection = str(entry.get("collection") or "").strip()
    event = str(entry.get("event") or "").strip()
    if " | " in name:
        name, tail = (p.strip() for p in name.rsplit(" | ", 1))
        event = event or tail
    if not event and "event" not in entry and re.search(r"\b(19|20)\d\d\b", collection):
        event = collection
    tags = tuple(str(t) for t in entry.get("tags") or () if t)
    finish = str(entry.get("finish") or "").upper()
    if finish not in FINISH_LABELS:
        m = _FINISH_SUFFIX.search(name)
        guess = m.group(1).upper() if m else ""
        finish = guess if guess in FINISH_LABELS else DEFAULT_FINISH
    textures = {str(role).lower(): _abspath(base_dir, rel)
                for role, rel in textures_in.items() if rel and str(role).lower() in ROLES}
    sticker_id = str(entry.get("id") or make_id(collection, name, finish))
    return Sticker(
        id=sticker_id,
        name=name,
        collection=collection or "User",
        finish=finish,
        rarity=normalize_rarity(entry.get("rarity")) or _rarity_from_tags(tags),
        event=event,
        kit_id=_int(entry.get("kit_id")) or kit_id_from(sticker_id),
        textures=textures,
        params={k: (list(v) if isinstance(v, (list, tuple)) else v)
                for k, v in (entry.get("params") or {}).items() if _param(v)},
        tags=tags,
        origin=origin,
    )


def load_catalog_file(path, warnings=None):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        if warnings is not None:
            warnings.append(f"{path}: {exc}")
        return []
    entries = data.get("stickers") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        if warnings is not None:
            warnings.append(f"{path}: no 'stickers' list")
        return []
    base_dir = os.path.dirname(os.path.abspath(path))
    return [s for s in (sticker_from_entry(e, base_dir, path) for e in entries) if s is not None]


def catalog_files(root):
    """catalog.json in ``root`` and its first-level sub-folders."""
    if not root or not os.path.isdir(root):
        return []
    found = [os.path.join(root, CATALOG_FILE)]
    try:
        found += [os.path.join(root, d, CATALOG_FILE) for d in sorted(os.listdir(root))
                  if os.path.isdir(os.path.join(root, d))]
    except OSError:
        return []
    return [p for p in found if os.path.isfile(p)]


class StickerLibrary:
    """All stickers of one or more library folders."""

    def __init__(self):
        self.stickers = []
        self.roots = []
        self.warnings = []
        self._by_id = {}
        self._by_kit = {}

    def load(self, *roots):
        self.warnings = []
        self.roots = [r for r in roots if r]
        by_id = {}
        for root in self.roots:
            for path in catalog_files(root):
                for sticker in load_catalog_file(path, self.warnings):
                    by_id.setdefault(sticker.id, sticker)  # earlier roots win
        self.stickers = sorted(by_id.values(), key=lambda s: (s.collection.lower(), s.name.lower()))
        self._by_id = {s.id: s for s in self.stickers}
        self._by_kit = {}
        for s in self.stickers:
            if s.kit_id:
                self._by_kit.setdefault(s.kit_id, s)
        return self

    def __len__(self):
        return len(self.stickers)

    def get(self, sticker_id):
        return self._by_id.get(sticker_id)

    def by_kit_id(self, kit_id):
        """Sticker of a sticker kit id (inspect links), or None."""
        return self._by_kit.get(int(kit_id)) if kit_id else None

    def collections(self):
        return sorted({s.collection for s in self.stickers}, key=str.lower)

    def filter(self, search="", collection=""):
        """Stickers matching all criteria; ``search`` words must all appear
        in name, collection, event, finish, rarity or tags."""
        words = search.lower().split()
        for sticker in self.stickers:
            if collection and sticker.collection != collection:
                continue
            if words:
                text = sticker.search_text()
                if not all(w in text for w in words):
                    continue
            yield sticker
