# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Replace the decoded glove normal textures of an existing library with
undecoded ones (#39), without a full export.

Libraries exported before #39 hold the glove normals as Source2Viewer
decoded them: it reads the AG normals of the glove paints as
hemi-octahedral RG, writes RGB and drops alpha (normal X). The glove
shader reads them as they are, so the gloves got tilted normals (dark
cracks at every float). This tool exports those textures again with
``--texture_decode_flags none`` into ``paints_raw/`` (same size as the
library copy) and points catalog.json at them. Weapons are not touched.

    python tools/fix_glove_normals.py --library <folder with catalog.json>
    python tools/fix_glove_normals.py --library ... --dry-run

Source2Viewer-CLI and CS2 are found like export_cs2_weapons.py does
(--s2v, --download-s2v, --cs2).
"""

import argparse
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import export_cs2_weapons as E  # noqa: E402


def glove_normal_refs(catalog):
    """{library path: [(container dict, key)]} of the glove paint kits'
    normal textures."""
    refs = {}

    def add(holder, key):
        value = holder.get(key)
        if isinstance(value, str) and E.is_normal_key(key) and not value.startswith(E.RAW_NORMALS_GROUP + "/"):
            refs.setdefault(value, []).append((holder, key))

    for kit in catalog.get("paint_kits", []):
        if not kit.get("glove"):
            continue
        for ops in list(kit.get("passes", {}).values()) + [kit.get("final", [])]:
            for op in ops:
                for key in list((op.get("set") or {})):
                    add(op["set"], key)
    # The glove model materials' own textures are not read by any shader
    # (file check only) and stay as they are.
    return refs


def vpk_paths(listing, names):
    """{library file stem: VPK vtex_c path} for the stems found."""
    wanted = set(names)
    out = {}
    for path in listing:
        if not path.endswith(".vtex_c"):
            continue
        stem = os.path.basename(path)[:-len(".vtex_c")]
        if stem in wanted:
            out.setdefault(stem, path)
    return out


def copy_same_size(src, dst, size):
    """Copy ``src`` to ``dst``, scaled channel by channel to ``size``."""
    from PIL import Image
    with Image.open(src) as im:
        if im.size == tuple(size):
            shutil.copy2(src, dst)
            return
        bands = [band.resize(tuple(size), Image.LANCZOS) for band in im.split()]
        Image.merge(im.mode, bands).save(dst)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--library", required=True, help="library folder (catalog.json)")
    ap.add_argument("--s2v", default="")
    ap.add_argument("--download-s2v", action="store_true")
    ap.add_argument("--cs2", default="")
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    cat_path = os.path.join(args.library, "catalog.json")
    with open(cat_path, encoding="utf-8") as fh:
        catalog = json.load(fh)
    refs = glove_normal_refs(catalog)
    print(f"{len(refs)} glove normal textures to replace")
    if not refs:
        return 0
    stems = {os.path.splitext(os.path.basename(p))[0]: p for p in refs}
    if args.dry_run:
        for p in sorted(refs):
            print("  " + p)
        return 0

    from PIL import Image
    game = E.find_cs2(args.cs2)
    vpk = os.path.join(game, "pak01_dir.vpk")
    exe = E.find_s2v(args.s2v, os.path.join(args.library, "_tools"), args.download_s2v)
    found = vpk_paths(E.vpk_listing(exe, vpk), stems)
    missing = sorted(set(stems) - set(found))
    for stem in missing:
        print(f"  not in the VPK, kept: {stems[stem]}")
    with tempfile.TemporaryDirectory() as tmp:
        E.run_s2v(exe, vpk, tmp, list(found.values()), extensions="vtex_c", threads=args.threads,
                  extra=("--texture_decode_flags", "none"))
        out_dir = os.path.join(args.library, E.RAW_NORMALS_GROUP)
        os.makedirs(out_dir, exist_ok=True)
        done = 0
        for stem, vtex in sorted(found.items()):
            old = stems[stem]
            src = E.texture_file(tmp, vtex)
            if not src:
                print(f"  not exported, kept: {old}")
                continue
            with Image.open(os.path.join(args.library, old)) as im:
                size = im.size
            new = f"{E.RAW_NORMALS_GROUP}/{os.path.basename(src)}"
            copy_same_size(src, os.path.join(args.library, new), size)
            for holder, key in refs[old]:
                holder[key] = new
            done += 1
    backup = os.path.join(args.library, "_backup", "catalog.json")  # "_" folders stay out of zips
    os.makedirs(os.path.dirname(backup), exist_ok=True)
    shutil.copy2(cat_path, backup)
    with open(cat_path, "w", encoding="utf-8") as fh:
        json.dump(catalog, fh, indent=1, ensure_ascii=False)
    print(f"{done} replaced, catalog.json updated (backup: {backup})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
