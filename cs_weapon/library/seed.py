# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Pattern seed -> rolled material variables.

CS2 composite materials (``.vcompmat``) roll every loose variable that has
float bounds from ``g_nRandomSeed`` (mutator
``COMP_MAT_PROPERTY_MUTATOR_RANDOM_ROLL_INPUT_VARIABLES``). The generator
is assumed to be the same as in CS:GO: Valve's uniform random stream, a
shuffled minimal-standard (Park-Miller) generator as published as ``ran1``
in Numerical Recipes in C, 2nd edition. Implemented here from that
description.

The roll order is listed in ``_shared_paint_generic.vcompmat`` and
implemented in ``roll_paint`` (verified against in-game screenshots, #1).
``roll`` is the low-level helper: variables in the order given, one roll
per component.
"""

import struct

_IA = 16807
_IM = 2147483647
_IQ = 127773
_IR = 2836
_NTAB = 32
_NDIV = 1 + (_IM - 1) // _NTAB
_AM = 1.0 / _IM
_RNMX = 1.0 - 1.2e-7


def _f32(x):
    """Round to 32-bit float, like the C++ code does."""
    return struct.unpack("<f", struct.pack("<f", x))[0]


class UniformRandomStream:
    def __init__(self, seed=0):
        self.set_seed(seed)

    def set_seed(self, seed):
        seed = int(seed)
        self._idum = seed if seed < 0 else -seed
        self._iy = 0
        self._iv = [0] * _NTAB

    @staticmethod
    def _step(idum):
        k = idum // _IQ
        idum = _IA * (idum - k * _IQ) - _IR * k
        if idum < 0:
            idum += _IM
        return idum

    def next_int(self):
        if self._idum <= 0 or not self._iy:
            self._idum = 1 if -self._idum < 1 else -self._idum
            for j in range(_NTAB + 7, -1, -1):
                self._idum = self._step(self._idum)
                if j < _NTAB:
                    self._iv[j] = self._idum
            self._iy = self._iv[0]
        self._idum = self._step(self._idum)
        j = self._iy // _NDIV
        if j >= _NTAB or j < 0:
            j = (j % _NTAB) & 0x7FFFFFFF
        self._iy = self._iv[j]
        self._iv[j] = self._idum
        return self._iy

    def random_float(self, low=0.0, high=1.0):
        fl = _f32(_AM * self.next_int())
        if fl > _RNMX:
            fl = _f32(_RNMX)
        return _f32(_f32(fl * _f32(high - low)) + low)

    def random_int(self, low, high):
        """Inclusive integer range, as in Valve's RandomInt."""
        span = high - low + 1
        if span <= 1:
            return low
        max_acceptable = 0x7FFFFFFF - ((0x7FFFFFFF + 1) % span)
        while True:
            n = self.next_int()
            if n <= max_acceptable:
                return low + n % span


def roll(seed, variables):
    """Roll bounded variables for a seed.

    ``variables``: list of ``(name, mins, maxs)`` with ``mins``/``maxs`` as
    float or sequence (one entry per component). Returns ``{name: value}``
    with a float or a tuple per variable.
    """
    rng = UniformRandomStream(seed)
    out = {}
    for name, mins, maxs in variables:
        if isinstance(mins, (int, float)):
            out[name] = rng.random_float(mins, maxs)
        else:
            out[name] = tuple(rng.random_float(a, b) for a, b in zip(mins, maxs))
    return out


# Roll order from _shared_paint_generic.vcompmat
# (m_vecRandomRollInputVars_InputVarsToRoll): (name, components, default
# mins, default maxs). Every listed component consumes one roll, even when
# the paint kit gives the variable no bounds (verified in-game with Fade
# seeds, #1). Unbounded components keep their default value.
ROLL_ORDER = [
    ("g_vPatternTexCoordOffset", 2, None, None),
    ("g_flPatternTexCoordRotation", 1, None, None),
    ("g_flWearTexCoordScale", 1, (1.6,), (1.8,)),
    ("g_vWearTexCoordOffset", 2, (0.0, 0.0), (1.0, 1.0)),
    ("g_flWearTexCoordRotation", 1, (0.0,), (360.0,)),
    ("g_flGrungeTexCoordScale", 1, (1.6,), (1.8,)),
    ("g_vGrungeTexCoordOffset", 2, (0.0, 0.0), (1.0, 1.0)),
    ("g_flGrungeTexCoordRotation", 1, (0.0,), (360.0,)),
]

DEFAULTS = {
    "g_vPatternTexCoordOffset": (0.0, 0.0),
    "g_flPatternTexCoordRotation": (0.0,),
}


def roll_paint(seed, bounds=None):
    """Roll all paint variables for a pattern seed.

    ``bounds``: paint kit overrides from its vcompmat,
    ``{name: (mins, maxs)}`` with float or tuple per component. Returns
    ``{name: float or tuple}`` for every variable in ``ROLL_ORDER``.
    """
    bounds = bounds or {}
    rng = UniformRandomStream(seed)
    out = {}
    for name, n, dmin, dmax in ROLL_ORDER:
        mins, maxs = bounds.get(name, (dmin, dmax))
        if isinstance(mins, (int, float)):
            mins, maxs = (mins,), (maxs,)
        default = DEFAULTS.get(name, (0.0,) * n)
        vals = []
        for i in range(n):
            v = rng.random_float()  # consumed in any case
            if mins is not None:
                lo, hi = mins[i], maxs[i]
                v = _f32(_f32(v * _f32(hi - lo)) + lo)
            else:
                v = default[i]
            vals.append(v)
        out[name] = vals[0] if n == 1 else tuple(vals)
    return out


def roll_kit(seed, roll):
    """Roll a paint kit's variables for a pattern seed.

    ``roll``: the kit's roll list from the catalog, in roll order:
    ``[name, components, bounds, default]`` with bounds ``[min, max]``
    (lists per component for vectors) or None. Every component consumes
    one roll; unbounded ones keep ``default`` (None: not returned).
    Returns ``{name: float or tuple}``.
    """
    rng = UniformRandomStream(seed)
    out = {}
    for name, n, bounds, default in roll:
        n = int(n)
        if bounds is not None:
            mins, maxs = bounds
            if isinstance(mins, (int, float)):
                mins, maxs = [mins], [maxs]
        if isinstance(default, (int, float)):
            default = [default]
        vals = []
        for i in range(n):
            v = rng.random_float()  # consumed in any case
            if bounds is not None and i < len(mins):
                lo, hi = mins[i], maxs[i]
                v = _f32(_f32(v * _f32(hi - lo)) + lo)
            elif default is not None and i < len(default):
                v = float(default[i])
            else:
                v = None
            vals.append(v)
        if None in vals:
            continue
        out[name] = vals[0] if n == 1 else tuple(vals)
    return out
