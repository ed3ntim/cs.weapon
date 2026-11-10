# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""CS2 preview (inspect) links for arbitrary items.

CS2 accepts ``csgo_econ_action_preview <HEX>`` where HEX is a zero byte,
the protobuf ``CEconItemPreviewDataBlock`` and a 4-byte checksum. Used to
create in-game reference views for any weapon, paint kit, seed and wear.
Field numbers follow the public protobuf definition from the game files.
"""

import binascii
import re
import struct
import urllib.parse

STEAM_URL = "steam://rungame/730/76561202255233023/+csgo_econ_action_preview%20"

# CEconItemPreviewDataBlock field numbers
F_DEFINDEX = 3
F_PAINTINDEX = 4
F_RARITY = 5
F_QUALITY = 6
F_PAINTWEAR = 7
F_PAINTSEED = 8
F_KILLEATERSCORETYPE = 9
F_KILLEATERVALUE = 10
F_CUSTOMNAME = 11
F_STICKERS = 12
F_KEYCHAINS = 20
REPEATED = {F_STICKERS, F_KEYCHAINS}

# Sticker message (stickers and keychains)
S_SLOT, S_ID, S_WEAR, S_SCALE, S_ROTATION, S_TINT, S_OFFSET_X, S_OFFSET_Y, S_OFFSET_Z, S_PATTERN = range(1, 11)
S_WRAPPED_STICKER = 12  # keychains: sticker kit id inside a Sticker Slab
QUALITY_STATTRAK = 9


def _varint(n):
    n &= 0xFFFFFFFFFFFFFFFF
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _field_varint(num, value):
    return _varint(num << 3) + _varint(value)


def _field_bytes(num, data):
    return _varint((num << 3) | 2) + _varint(len(data)) + data


def float_bits(value):
    return struct.unpack("<I", struct.pack("<f", value))[0]


def _sticker_bytes(st):
    out = _field_varint(S_SLOT, int(st.get("slot", 0))) + _field_varint(S_ID, int(st["id"]))
    for key, num in (("wear", S_WEAR), ("scale", S_SCALE), ("rotation", S_ROTATION),
                     ("offset_x", S_OFFSET_X), ("offset_y", S_OFFSET_Y), ("offset_z", S_OFFSET_Z)):
        if key in st:
            out += _varint((num << 3) | 5) + struct.pack("<f", float(st[key]))
    if "pattern" in st:
        out += _field_varint(S_PATTERN, int(st["pattern"]))
    if st.get("sticker"):
        out += _field_varint(S_WRAPPED_STICKER, int(st["sticker"]))
    return out


def encode(defindex, paintindex, paintseed=0, paintwear=0.0, rarity=0,
           quality=4, stattrak=None, nametag="", stickers=(), keychains=()):
    proto = bytearray()
    proto += _field_varint(F_DEFINDEX, defindex)
    proto += _field_varint(F_PAINTINDEX, paintindex)
    if rarity:
        proto += _field_varint(F_RARITY, rarity)
    proto += _field_varint(F_QUALITY, 9 if stattrak is not None and quality == 4 else quality)
    proto += _field_varint(F_PAINTWEAR, float_bits(paintwear))
    proto += _field_varint(F_PAINTSEED, paintseed)
    if stattrak is not None:
        proto += _field_varint(F_KILLEATERSCORETYPE, 0)
        proto += _field_varint(F_KILLEATERVALUE, stattrak)
    if nametag:
        proto += _field_bytes(F_CUSTOMNAME, nametag.encode("utf-8"))
    for st in stickers:
        proto += _field_bytes(F_STICKERS, _sticker_bytes(st))
    for st in keychains:
        proto += _field_bytes(F_KEYCHAINS, _sticker_bytes(st))
    buf = b"\x00" + bytes(proto)
    crc = binascii.crc32(buf) & 0xFFFFFFFF
    xcrc = ((crc & 0xFFFF) ^ (len(proto) * crc)) & 0xFFFFFFFF
    return (buf + struct.pack(">I", xcrc)).hex().upper()


def decode(hexstr):
    """Decode a preview hex string into ``{field_number: value}`` and a
    checksum flag. A non-zero first byte is an XOR mask over all bytes."""
    data = bytes.fromhex(hexstr)
    if data and data[0]:
        key = data[0]
        data = bytes(b ^ key for b in data)
    proto, check = data[1:-4], data[-4:]
    crc = binascii.crc32(data[:-4]) & 0xFFFFFFFF
    ok = struct.pack(">I", ((crc & 0xFFFF) ^ (len(proto) * crc)) & 0xFFFFFFFF) == check
    return _fields(proto, REPEATED), ok


def _fields(proto, repeated=()):
    """Protobuf wire fields: {number: value}, lists for ``repeated``."""
    fields, i = {}, 0
    while i < len(proto):
        key, i = _read_varint(proto, i)
        num, wt = key >> 3, key & 7
        if wt == 0:
            val, i = _read_varint(proto, i)
        elif wt == 2:
            n, i = _read_varint(proto, i)
            val, i = proto[i:i + n], i + n
        elif wt == 5:
            val, i = proto[i:i + 4], i + 4
        else:
            raise ValueError(f"unsupported wire type {wt}")
        if num in repeated:
            fields.setdefault(num, []).append(val)
        else:
            fields[num] = val
    return fields


def _read_varint(buf, i):
    shift = result = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7


def link(**kwargs):
    return STEAM_URL + encode(**kwargs)


def _float(v):
    """float field: fixed32 bytes or a varint holding the bit pattern."""
    if isinstance(v, (bytes, bytearray)):
        return struct.unpack("<f", bytes(v))[0]
    return struct.unpack("<f", struct.pack("<I", int(v) & 0xFFFFFFFF))[0]


def _sticker(raw):
    f = _fields(bytes(raw))
    out = {"slot": int(f.get(S_SLOT, 0)), "id": int(f.get(S_ID, 0))}
    for key, num in (("wear", S_WEAR), ("scale", S_SCALE), ("rotation", S_ROTATION),
                     ("offset_x", S_OFFSET_X), ("offset_y", S_OFFSET_Y), ("offset_z", S_OFFSET_Z)):
        if num in f:
            out[key] = _float(f[num])
    if S_PATTERN in f:
        out["pattern"] = int(f[S_PATTERN])
    if S_WRAPPED_STICKER in f:
        out["sticker"] = int(f[S_WRAPPED_STICKER])
    return out


class LinkError(ValueError):
    pass


def extract_hex(text):
    """Hex payload of a preview link, a console command or plain hex."""
    text = urllib.parse.unquote((text or "").strip())
    m = re.search(r"csgo_econ_action_preview\s+([0-9A-Fa-f]+)", text)
    if m:
        return m.group(1)
    if re.fullmatch(r"[0-9A-Fa-f]{12,}", text):
        return text
    if re.search(r"[SM]\d+A\d+D\d+", text):
        raise LinkError("This inspect link points to an item in a Steam inventory or market "
                        "listing; it can only be resolved through Steam. Use a self-contained "
                        "preview link (csgo_econ_action_preview with hex data).")
    raise LinkError("No inspect link data found")


def parse_item(text):
    """Decode an inspect/preview link into a plain item dict:
    defindex, paintindex, paintseed, paintwear, stattrak (kills or None),
    nametag, stickers, keychains, checksum_ok."""
    fields, ok = decode(extract_hex(text))
    if F_DEFINDEX not in fields:
        raise LinkError("Link data has no item definition")
    quality = int(fields.get(F_QUALITY, 0))
    has_kills = F_KILLEATERVALUE in fields or F_KILLEATERSCORETYPE in fields
    name = fields.get(F_CUSTOMNAME, b"")
    return {
        "defindex": int(fields[F_DEFINDEX]),
        "paintindex": int(fields.get(F_PAINTINDEX, 0)),
        "paintseed": int(fields.get(F_PAINTSEED, 0)),
        "paintwear": _float(fields[F_PAINTWEAR]) if F_PAINTWEAR in fields else 0.0,
        "quality": quality,
        "stattrak": int(fields.get(F_KILLEATERVALUE, 0)) if has_kills or quality == QUALITY_STATTRAK else None,
        "nametag": bytes(name).decode("utf-8", "replace") if isinstance(name, (bytes, bytearray)) else "",
        "stickers": [_sticker(s) for s in fields.get(F_STICKERS, [])],
        "keychains": [_sticker(s) for s in fields.get(F_KEYCHAINS, [])],
        "checksum_ok": ok,
    }
