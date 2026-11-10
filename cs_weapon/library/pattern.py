# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Pattern texture coordinate transform of csgo_customweapon.

The shader derives two float4 parameters (``g_vPatternTexCoordXform0/1``)
from rotation, scale and offset of the paint kit. Reconstructed from the
behaviour of the compiled material expressions and verified against
in-game screenshots (#1):

- rotation, scale and offset are rounded to 0.01 first;
- scale = ``g_flPatternTexCoordScale`` times the weapon size factor
  ``g_flUvScale1`` (from the mesh's composite inputs), unless
  ``g_bIgnoreWeaponSizeScale``. Wear and grunge use the same transform
  with their own (seed rolled) scale, rotation and offset;
- the translation contains a quirk: the y component of the rotated
  centre uses the already rotated x value instead of the input. It has to
  be reproduced, otherwise patterns are shifted by up to ~0.3.

The weapon UV is wrapped into 0..1 before the transform (also verified
in-game: legacy meshes have UVs outside 0..1).

Pattern UV (source convention, origin top-left):
    p.x = X0[0] * u + X0[1] * v + X0[3]
    p.y = X1[0] * u + X1[1] * v + X1[3]
"""

import math


def round2(x):
    return math.floor((x + 0.005) * 100.0) / 100.0


def size_factor(uv_scale=1.0, weapon_length=None, legacy=None, ignore_size=False):
    """Weapon size factor of the texture transforms: ``g_flUvScale1`` of the
    mesh's composite inputs (HD and legacy alike), 1 when the paint kit
    ignores the weapon size. ``weapon_length`` and ``legacy`` are accepted
    for compatibility but unused: the shader also carries a
    ``g_flWeaponLength1 / 36`` variant, the vertex shader does not bind it."""
    if ignore_size:
        return 1.0
    return uv_scale


def xform(rotation, offset, scale=1.0, factor=1.0):
    """Return ``(X0, X1)`` as 4-tuples like the shader parameters."""
    rot = round2(rotation)
    s = round2(scale * factor)
    ox, oy = round2(offset[0]), round2(offset[1])
    a = rot * math.pi / 180.0
    c, sn = math.cos(a), math.sin(a)
    half = 0.5 / (s if s > 0 else 1.0)
    cn, sn_n = math.cos(-a), math.sin(-a)
    cx = half * cn - half * sn_n
    cy = cx * sn_n + half * cn  # quirk: uses cx, not half
    x0 = (c * s, -sn * s, 0.0, s * c * cx - s * sn * cy + ox - 0.5)
    x1 = (sn * s, c * s, 0.0, s * sn * cx + s * c * cy + oy - 0.5)
    return x0, x1


def apply(x0, x1, u, v):
    """Pattern UV for a weapon UV (wrapped into 0..1 first)."""
    u, v = u % 1.0, v % 1.0
    return (x0[0] * u + x0[1] * v + x0[3], x1[0] * u + x1[1] * v + x1[3])
