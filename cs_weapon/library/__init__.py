# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Weapon database and the math behind skins.

    catalog.py       weapons, paint kits, skins; loading libraries, filter
    seed.py          pattern seed -> rolled paint variables (Valve RNG)
    pattern.py       pattern texture coordinate transform
    wear.py          float tiers and clamping
    inspect_link.py  CS2 preview links (in-game reference views)
    stickers.py      stickers of a cs.sticker library (weapon sticker slots)
    registry.py      Blender glue: shared catalog, UI list mirror, previews
    sticker_registry.py  the same for the sticker library

Everything except the registries is pure Python and unit tested without
Blender.
"""


def register():
    from . import registry, sticker_registry
    registry.register()
    sticker_registry.register()


def unregister():
    from . import registry, sticker_registry
    sticker_registry.unregister()
    registry.unregister()
