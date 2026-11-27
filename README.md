# Barotrauma Sprite Exporter

## Open the exporter

Use Python 3.10 or later with Tkinter for the Windows folder selection window.
Install Pillow once from the Wikitools folder.

```powershell
python -m pip install -r requirements.txt
```

1. Double-click `ExportSprites.cmd` or run `python export_sprites.py`.
2. Browse to the game's `Content` folder and choose an output folder outside it.
3. Click **Export Sprites**.

The log lists each sprite as it is processed and shows the export summary.
The window loads each saved path from `data` and leaves a field blank if its
path has no record. The exporter creates the output folders during export.

## Output

- The selected output folder is the `BackportFolder`. Main images are saved
  by resource type, for example `Items/idcard.png`, `Afflictions/beatcop.png`,
  and `Talents/beatcop.png`. The Vanilla package manifest supplies the resource
  types when available. Otherwise the XML collection or owner tag supplies them.
- The owner is the nearest XML element with an `identifier`. At the same level,
  inventory icons and icons take precedence over regular sprites. Item variants
  combine inherited and overridden sprite definitions and are saved in `Items`
  with their own identifiers, for example `Items/idcardfakesootman.png`.
- Icons use the midpoint of a defined color gradient or their configured single
  color. Afflictions without a custom gradient use the GUI style's medium buff
  or debuff color. Icons with no configured tint keep their source colors.
- Talent icons use the unlocked color from the talent menu. The GUI style's
  green is used unless the talent defines a color override.
- Enable **Include additional sprites** to also export other matched images.
  Their names include the sprite tag, for example `Items/idcard_sprite.png`.
  Filename collisions receive a numeric suffix. Filenames are ASCII.
- The exporter supports `sourcerect` and `sheetindex` with `sheetelementsize`.
  It uses the whole texture if the declaration supplies no crop. Rectangles
  that overlap a texture edge preserve their dimensions with transparent
  padding. The result lists the number of padded images.
- The exporter crops the source pixels from the base XML declarations. Runtime
  rendering effects, language overrides, and assets without an explicit XML
  identifier are outside this export.

## Incremental exports

The working directory contains `data`. The launcher uses the Wikitools folder
as its working directory. The record stores the latest Content and output
paths and each tracked PNG's SHA-256 hash, rendered image hash, and UTC export
time. Previous records are kept for the same output folder during filtered
exports.

Each export reads the record at startup and writes it once after the batch
finishes. A PNG with matching rendered pixels and matching on-disk bytes is
skipped without encoding or writing it again. Its original export time is
preserved. Changed, missing, and externally modified PNGs are written again.

## Command line

```powershell
python export_sprites.py --content "D:\Games\Barotrauma\Content" --output "E:\BackportFolder"
python export_sprites.py --kind Items --identifier idcard --all-sprites
python export_sprites.py --kind Structures --sprite-tag Sprite --output "E:\Wikitools\structure-sprites"
```

An omitted `--content` or `--output` uses the corresponding saved path in
`data`. Supply the option when its path has no record.

`--kind` accepts XML owner tags and output category names. `--kind`,
`--identifier`, and `--sprite-tag` can be repeated. A nonzero exit code signals
an error. The exporter reports individual failures and continues with the rest
of the batch.

## Source references

- [Sprite XML parsing](https://github.com/FakeFishGames/Barotrauma/blob/7cd536166c022cdac4ac7f7792bef4ed68a18552/Barotrauma/BarotraumaShared/SharedSource/Sprite/Sprite.cs)
- [Item prefab and texture paths](https://github.com/FakeFishGames/Barotrauma/blob/7cd536166c022cdac4ac7f7792bef4ed68a18552/Barotrauma/BarotraumaShared/SharedSource/Items/ItemPrefab.cs)
- [Item inventory icons](https://github.com/FakeFishGames/Barotrauma/blob/7cd536166c022cdac4ac7f7792bef4ed68a18552/Barotrauma/BarotraumaClient/ClientSource/Items/ItemPrefab.cs)
- [Variant XML inheritance](https://github.com/FakeFishGames/Barotrauma/blob/7cd536166c022cdac4ac7f7792bef4ed68a18552/Barotrauma/BarotraumaShared/SharedSource/Prefabs/IImplementsVariants.cs)
- [Affliction icon palettes and default colors](https://github.com/FakeFishGames/Barotrauma/blob/7cd536166c022cdac4ac7f7792bef4ed68a18552/Barotrauma/BarotraumaClient/ClientSource/Characters/Health/CharacterHealth.cs)
