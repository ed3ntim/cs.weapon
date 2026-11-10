# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 ed3n
"""Build the installable zip: dist/cs_weapon-<version>.zip

The zip holds a single top-level folder ``cs_weapon/`` with source code,
manifest, LICENSE.md and COMMERCIAL.md only (no tests, tools, demo fixture or repo
docs). That layout installs both as an extension (Blender detects the
manifest in the sub-folder) and as a legacy add-on.

    python tools/build_zip.py
    python tools/build_zip.py --with-weapons C:/cs2_weapons   # personal full build
    python tools/build_zip.py --with-weapons C:/cs2_weapons --suffix " - all skins fullres"
    python tools/build_zip.py --with-weapons C:/cs2_weapons_2048 --suffix " - all skins 2048px"

``--with-weapons`` bakes a library made by tools/export_cs2_weapons.py
(or shrunk by tools/shrink_library.py) into cs_weapon/weapons/bundled/
-> dist/cs_weapon-<version><suffix>.zip (default suffix "-full"). That
zip contains Valve game assets: never on GitHub (tools/publish.py refuses
it); the "all skins" zips (2048px and fullres) are shared by ed3n
through ed3n.gallery only.
"""

import argparse
import json
import os
import re
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "cs_weapon")
DIST = os.path.join(ROOT, "dist")

EXCLUDE_DIRS = {"__pycache__", ".pytest_cache"}
EXCLUDE_EXT = {".pyc", ".pyo", ".blend1", ".orig", ".bak"}
LICENSE_FILES = ("LICENSE.md", "COMMERCIAL.md")


def version():
    with open(os.path.join(PKG, "blender_manifest.toml"), encoding="utf-8") as fh:
        m = re.search(r'^version\s*=\s*"([^"]+)"', fh.read(), re.M)
    if not m:
        raise SystemExit("version not found in blender_manifest.toml")
    return m.group(1)


BUNDLED_ARC = "cs_weapon/weapons/bundled"


def _add_bundled(zf, library):
    """Add the whole library folder (catalog.json, models, textures, icons;
    working folders starting with "_" or "." are skipped). PNG / GLB are
    stored, they are compressed already. Returns the number of files added."""
    with open(os.path.join(library, "catalog.json"), encoding="utf-8") as fh:
        catalog = json.load(fh)
    files = []
    for folder, dirs, names in os.walk(library):
        dirs[:] = sorted(d for d in dirs if not d.startswith(("_", ".")))
        files += [os.path.join(folder, n) for n in sorted(names)]
    referenced = {w.get("model", "") for w in catalog.get("weapons", [])}
    missing = [m for m in referenced if m and not os.path.isfile(os.path.join(library, m))]
    if missing:
        raise SystemExit(f"{len(missing)} models listed in catalog.json are missing, e.g. {missing[0]}")
    for i, path in enumerate(files, 1):
        rel = os.path.relpath(path, library).replace(os.sep, "/")
        compress = zipfile.ZIP_DEFLATED if rel.endswith(".json") else zipfile.ZIP_STORED
        zf.write(path, f"{BUNDLED_ARC}/{rel}", compress_type=compress)
        if i % 500 == 0:
            print(f"  {i}/{len(files)} weapon files")
    print(f"  bundled {len(catalog.get("skins", []))} skins ({len(files)} files)")
    return len(files)


def main(with_weapons="", suffix="-full"):
    os.makedirs(DIST, exist_ok=True)
    suffix = suffix if with_weapons else ""
    out = os.path.join(DIST, f"cs_weapon-{version()}{suffix}.zip")
    if with_weapons and not os.path.isfile(os.path.join(with_weapons, "catalog.json")):
        raise SystemExit(f"no catalog.json in {with_weapons} (run tools/export_cs2_weapons.py first)")
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9, allowZip64=True) as zf:
        # License files travel next to the manifest.
        for name in LICENSE_FILES:
            zf.write(os.path.join(ROOT, name), f"cs_weapon/{name}")
        for folder, dirs, files in os.walk(PKG):
            dirs[:] = sorted(d for d in dirs if d not in EXCLUDE_DIRS
                             and os.path.join(folder, d) != os.path.join(PKG, "weapons", "bundled"))
            for name in sorted(files):
                if os.path.splitext(name)[1].lower() in EXCLUDE_EXT:
                    continue
                path = os.path.join(folder, name)
                zf.write(path, os.path.relpath(path, ROOT).replace(os.sep, "/"))
                count += 1
        if with_weapons:
            count += _add_bundled(zf, with_weapons)
    if with_weapons:
        print("NOTE: this zip contains Valve game assets. Never upload it to GitHub.")
    print(f"{out}: {count + len(LICENSE_FILES)} files, {os.path.getsize(out) / 1024:.0f} KiB")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build the installable add-on zip.")
    ap.add_argument("--with-weapons", default="", metavar="LIBRARY",
                    help="bake a library from export_cs2_weapons.py into a zip with weapons")
    ap.add_argument("--suffix", default="-full", help="name suffix of the zip with weapons (default -full)")
    args = ap.parse_args()
    main(args.with_weapons, args.suffix)
