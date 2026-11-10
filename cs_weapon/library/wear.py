# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Wear (float) tiers and the paint kit remap."""

# (upper bound exclusive, short, long)
TIERS = (
    (0.07, "FN", "Factory New"),
    (0.15, "MW", "Minimal Wear"),
    (0.38, "FT", "Field-Tested"),
    (0.45, "WW", "Well-Worn"),
    (1.01, "BS", "Battle-Scarred"),
)


def tier(value):
    """Return (short, long) for a float value."""
    for upper, short, name in TIERS:
        if value < upper:
            return short, name
    return TIERS[-1][1], TIERS[-1][2]


def clamp(value, wear_min=0.0, wear_max=1.0):
    """Floats a paint kit can actually have."""
    return min(max(value, wear_min), wear_max)


def tier_midpoint(short, wear_min=0.0, wear_max=1.0):
    """A representative float for a tier, inside the paint kit's range."""
    lower = 0.0
    for upper, s, _name in TIERS:
        if s == short:
            return clamp((lower + min(upper, 1.0)) / 2.0, wear_min, wear_max)
        lower = upper
    raise KeyError(short)
