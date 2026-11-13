# cs.weapon

Blender add-on that spawns CS2 weapons, knives and gloves with any skin:
every paint kit with its pattern seed (0 to 1000) and float (clamped to the
skin's wear range), rendered by the game's own paint shaders, live in
EEVEE and Cycles. StatTrak, name tags, stickers and charms sit where the
game puts them, CS2 inspect links spawn the exact item, and weapons can
come with their skeleton and animations.
Target: Blender 5.2, compatible back to 4.5.

**For the add-on excluding the weapon assets (CS2 export required) [⬇ Download cs_weapon.zip (latest release)](https://github.com/ed3ntim/cs.weapon/releases/latest/download/cs_weapon.zip)**

**For the version including all weapons and skins visit my page [⬇ Download cs_weapon - all skins (2048px or full resolution)](https://ed3n.gallery/apps/cs-weapon/)**

## Installation

1. Download [`cs_weapon.zip`](https://github.com/ed3ntim/cs.weapon/releases/latest/download/cs_weapon.zip)
   (all versions: [Releases](https://github.com/ed3ntim/cs.weapon/releases)).
2. Blender: *Edit > Preferences > Get Extensions > ⌄ > Install from Disk...*
   and pick the zip (drag & drop onto the Blender window works too).
3. Export the weapon library (see [Weapon library](#weapon-library)) and
   set it as *Preferences > Add-ons > cs.weapon > Weapon Library Folder*.
   The all skins version from [ed3n.gallery](https://ed3n.gallery/apps/cs-weapon/)
   brings the library along, skip this step.
4. 3D Viewport sidebar (`N`), tab **cs.weapon**.

To remove the add-on: *Preferences > Add-ons > cs.weapon > Uninstall
cs.weapon* (or Blender's own *⌄ > Uninstall* next to the extension).
Weapons already spawned in your files and the exported library stay as
they are.

## Usage

### Library

- Search by name, filter by type (rifle, knife, gloves ...), weapon and
  paint style. List or grid view with the inventory icons.
- The info box shows style, float range and icon of the selected skin. Set
  **Pattern** (seed) and **Float**; the wear tier (Factory New ...
  Battle-Scarred) is shown next to it.
- **Spawn Weapon** adds it at the 3D cursor. **Apply to Selected** gives
  selected weapons the skin and keeps their object, pattern and float.
- Every weapon also has its vanilla finish.

### Inspect links

Paste a CS2 inspect link with item data (`steam://rungame/...
csgo_econ_action_preview <hex>`, the console command or the bare hex
code) into the field at the top and press **Spawn**: the item comes with
its skin, pattern, float, StatTrak count, name tag, stickers (with
position, rotation and wear) and charm (needs cs.charm). Old links of the
form `S...A...D...` hold no item data and cannot be read.

### Active weapon

- **Pattern** and **Float** stay live: change them at any time, the
  material follows. Duplicates are independent.
- **StatTrak** with a live counter (kills, or the game's ERROR!, USER and
  UNKNWN states) and **Name Tag**, both at the game's attachment points.
- **Stickers**: five slots per weapon as in the game, filled from a
  [cs.sticker](https://github.com/ed3ntim/cs.sticker) library. Per sticker
  wear, rotation, offset and size. *In Material* draws them into the weapon
  material like the game does; *As Meshes* creates sticker meshes, e.g.
  for export.
- **Add Charm** hangs the charm picked in
  [cs.charm](https://github.com/ed3ntim/cs.charm) at the weapon's charm
  point.
- **Bake Skin** bakes color, roughness, metalness and normal (stickers
  included) into textures on a plain Principled material, for export to
  other programs. **Restore Live Material** switches back.

### As Armature

When the library has skeletons, **As Armature** (next to *Spawn Weapon*)
spawns the weapon under its own armature with the game's bones (`weapon`,
`weapon_offset`, `bolt`, `clip`, `cliprelease`, `trigger` ...). The
model's clips (reload, shoot, inspect ...) come along as actions named
`<weapon> | <clip>`. StatTrak, name tag, sticker meshes and charm follow
the rig. Gloves get the game's arm skeleton (`arm_upper_L`, `hand_R`,
fingers), the same bone names as the CS2 agents.

### Materials

The skins run the game's own composite shaders (decompiled from your CS2
installation by the exporter) live per pixel: solid colors, hydrographic,
spray paint with halftone, anodized, custom paint jobs, patina, gunsmith,
case hardening and the glove paints, each with pattern placement and wear
from seed and float. On top: pearlescence, iridescence and glitter from the
weapon shader. Without the exported shader a skin falls back to a simpler
material.

## Weapon library

Valve's models, textures and shaders are copyrighted game assets and are
not part of this repository or the release. `tools/export_cs2_weapons.py`
exports them from **your own** CS2 installation with
[Source2Viewer](https://github.com/ValveResourceFormat/ValveResourceFormat)
into a library the add-on reads. Needs Python 3.9+ with
`pip install zstandard` (for the paint shaders).

```
python tools/export_cs2_weapons.py --out D:/cs2_weapons --download-s2v
```

- CS2 is found through Steam; otherwise pass `--cs2 <path>`.
- `--download-s2v` fetches Source2Viewer-CLI; alternatively `--s2v <path>`.
- `--only weapon_ak47,weapon_knife_karambit` exports only some weapons.
- The full library takes about 15 GB. `--max-texture-size 2048` (needs
  `pip install pillow`) roughly halves that at the cost of close-up detail.
- `--skip-export` rebuilds the library from the existing raw export; with
  `--max-texture-size` it shrinks an existing library.
- `--skip-export --sticker-markup` adds the sticker slots and charm points
  of the models to an existing library, `--skip-export --rig` the
  skeletons and animations (both need CS2 and Source2Viewer).
- Run it again after a CS2 update.
- Libraries exported before cs.weapon 1.0.2 have broken glove normals
  (dark cracks on the gloves at every wear). Fix one in place, without a
  full export: `python tools/fix_glove_normals.py --library <folder with
  catalog.json> --download-s2v` (needs `pip install pillow`).

Optionally bake the library into a personal add-on zip:

```
python tools/build_zip.py --with-weapons D:/cs2_weapons
→ dist/cs_weapon-<version>-full.zip   (for your own use only, do not share)
```

The exported assets are Valve's: for personal use only, do not
redistribute them.

## For add-on developers

`cs_weapon.api` (`API_VERSION`) lets other add-ons list skins, spawn
weapons (also rigged), play clips, read attachment matrices (muzzle,
shell eject, StatTrak ...) and react to every spawn. See the docstring of
[`cs_weapon/api.py`](cs_weapon/api.py).

## Development

Tests run headless in Blender:

```
blender -b --factory-startup --python tests/run_tests.py
```

- Test fixture (self-made demo rifle): `blender -b --factory-startup --python tools/generate_demo.py`
- Release zip: `python tools/build_zip.py`, check with
  `blender --command extension validate dist/cs_weapon-<version>.zip`

## License

[PolyForm Noncommercial 1.0.0](LICENSE.md), Copyright 2026 ed3n.
Free for noncommercial use. Any commercial use (selling the add-on, paid
bundles, paid services) needs a written agreement, see
[COMMERCIAL.md](COMMERCIAL.md). Contact: contact@ed3n.gallery
