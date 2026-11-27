"""Export identifier-named PNG files from Barotrauma XML sprite declarations."""

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
from struct import pack
import sys
from tempfile import NamedTemporaryFile
from urllib.parse import quote
from xml.etree import ElementTree as ET

from PIL import Image, ImageChops, ImageColor


PRIMARY_TAGS = ("inventoryicon", "icon", "sprite", "portrait", "mapicon", "backgroundsprite")
FOLDER_NAMES = {
    "afflictionhusk": "Afflictions",
    "guiimage": "GUIImages",
    "internaldamage": "Afflictions",
    "traitormission": "TraitorMissions",
}
COLLECTION_TAGS = {"afflictions", "items", "jobs", "missions", "orders", "structures", "talents"}
DEFAULT_GUI_COLORS = {
    "buffcolormedium": "110,168,118,255",
    "debuffcolormedium": "155,55,55,255",
    "green": "154,213,163,255",
}
WHITE = (255, 255, 255, 255)
WINDOWS_DEVICES = {"CON", "PRN", "AUX", "NUL"} | {
    f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(10)
}


@dataclass(frozen=True)
class Prefab:
    identifier: str
    kind: str
    folder: str
    path: Path
    element: ET.Element
    is_item: bool


@dataclass(frozen=True)
class Sprite:
    tag: str
    depth: int
    texture: str
    rect: str | None
    sheet_index: str | None
    sheet_size: str | None
    is_icon: bool
    color: str | None
    colors: str | None


@dataclass(frozen=True)
class ExportResult:
    exported: int
    skipped: int
    padded: int
    errors: int
    output: Path

    def summary(self):
        return (
            f"Exported {self.exported} PNG file(s) to {self.output}. "
            f"Skipped: {self.skipped}. Padded: {self.padded}. Errors: {self.errors}."
        )


def name(value):
    return value.rsplit("}", 1)[-1].lower()


def folder_name(tag):
    tag = tag.rsplit("}", 1)[-1]
    known = FOLDER_NAMES.get(tag.lower())
    if known is not None:
        return known
    suffix = "" if tag.lower().endswith("s") else "s"
    return tag[:1].upper() + tag[1:] + suffix


def ascii_filename(value):
    quoted = quote(value, safe="")
    if quoted in {".", ".."} or quoted.split(".", 1)[0].upper() in WINDOWS_DEVICES:
        return f"%{ord(quoted[0]):02X}{quoted[1:]}"
    return quoted.rstrip(".") + "%2E" * (len(quoted) - len(quoted.rstrip(".")))


def expand_variant(base, replacement):
    """Merge the sprite-relevant XML structure of an item variant."""
    result = deepcopy(base)
    result.attrib.update(replacement.attrib)
    by_tag = defaultdict(list)
    for child in result:
        by_tag[child.tag].append(child)
    cleared_tags = {child.tag for clear in replacement.findall("clearall") for child in clear}
    occurrences = Counter()
    for index, child in enumerate(replacement):
        tag = child.tag
        if tag == "clear":
            result[:] = deepcopy(list(replacement)[index + 1 :])
            return result
        if tag == "clearall":
            continue
        occurrence = occurrences[tag]
        occurrences[tag] += 1
        if occurrence >= len(by_tag[tag]):
            result.append(deepcopy(child))
            continue
        original = by_tag[tag][occurrence]
        position = list(result).index(original)
        if tag in cleared_tags or (not child.attrib and not len(child)):
            result.remove(original)
        else:
            result[position] = expand_variant(original, child)
    for child in list(result):
        if child.tag in cleared_tags and child in by_tag[child.tag]:
            result.remove(child)
    return result


def owned_sprites(element, depth=0, in_icon=False):
    attrs = element.attrib
    texture = attrs.get("texture", "").strip()
    rect = attrs.get("sourcerect")
    index = attrs.get("sheetindex")
    tag = element.tag
    is_icon = in_icon or "icon" in tag or tag in {"symbol", "radiationsymbol"}
    if texture and (
        rect is not None or index is not None or depth == 0
        or is_icon or tag in PRIMARY_TAGS or tag.endswith("sprite")
    ):
        yield Sprite(
            tag, depth, texture, rect, index, attrs.get("sheetelementsize"), is_icon,
            attrs.get("color"), attrs.get("iconcolors") or attrs.get("colors"),
        )
    for child in element:
        if not child.get("identifier", "").strip() and child.tag not in {"override", "clear", "clearall"}:
            yield from owned_sprites(child, depth + 1, is_icon)


def sprite_priority(sprite):
    return (
        sprite.depth > 1,
        PRIMARY_TAGS.index(sprite.tag) if sprite.tag in PRIMARY_TAGS else len(PRIMARY_TAGS),
        sprite.depth,
    )


def read_package_folders(manifest):
    folders = {}
    if manifest.is_file():
        for entry in ET.parse(manifest).getroot():
            path = entry.get("file", "").replace("\\", "/")
            prefix, _, relative = path.partition("/")
            if prefix.lower() == "content" and relative.lower().endswith(".xml"):
                folders[relative.lower()] = folder_name(entry.tag)
    return folders


def item_definitions(element, registered_item_file):
    if element.tag in {"items", "override"}:
        for child in element:
            yield from item_definitions(child, registered_item_file)
    elif (registered_item_file or element.tag == "item") and element.get("identifier", "").strip():
        yield element


def read_prefabs(content, report):
    prefabs = []
    errors = 0
    gui_colors = DEFAULT_GUI_COLORS.copy()
    gui_style = content / "UI" / "style.xml"
    manifest = content / "ContentPackages" / "Vanilla.xml"
    try:
        folders = read_package_folders(manifest)
    except (ET.ParseError, OSError) as error:
        report(f"Error: {manifest}: {error}")
        folders = {}
        errors += 1
    for path in sorted(content.rglob("*.xml"), key=lambda path: path.as_posix().lower()):
        if path == manifest:
            continue
        try:
            root = ET.parse(path).getroot()
        except (ET.ParseError, OSError) as error:
            report(f"Error: {path}: {error}")
            errors += 1
            continue
        original_tags = {element: element.tag for element in root.iter()}
        for element in original_tags:
            element.tag = name(element.tag)
            element.attrib = {name(key): value for key, value in element.attrib.items()}
        if path == gui_style:
            gui_colors.update((child.tag, child.get("color")) for child in root if child.get("color"))
        folder = folders.get(path.relative_to(content).as_posix().lower())
        definitions = set(item_definitions(root, folder == "Items"))
        if folder is None and root.tag in COLLECTION_TAGS:
            folder = folder_name(original_tags[root])
        for element, original_tag in original_tags.items():
            identifier = element.get("identifier", "").strip()
            if identifier:
                is_item = element in definitions
                destination = "Items" if is_item else (folder or folder_name(original_tag))
                prefabs.append(Prefab(identifier, element.tag, destination, path, element, is_item))
    return prefabs, gui_colors, errors


def resolve_item(prefab, items, chain=()):
    parent_id = prefab.element.get("variantof", "").strip().casefold()
    if not parent_id:
        return prefab.element, (prefab.path.parent,)
    identifier = prefab.identifier.casefold()
    if identifier in chain:
        raise ValueError(f"item variant cycle: {' -> '.join((*chain, identifier))}")
    parent = items.get(parent_id)
    if parent is None:
        raise ValueError(f"missing base item {parent_id!r}")
    base, directories = resolve_item(parent, items, (*chain, identifier))
    directories = (parent.path.parent, prefab.path.parent, *directories)
    return expand_variant(base, prefab.element), tuple(dict.fromkeys(directories))


def numbers(value, count, attribute, convert):
    try:
        values = tuple(convert(part.strip()) for part in value.split(","))
        if len(values) == count:
            return values
    except (ValueError, OverflowError):
        pass
    raise ValueError(f"invalid {attribute}: {value!r}; expected {count} numbers")


def crop_box(sprite, size):
    if sprite.sheet_index is not None:
        column, row = numbers(sprite.sheet_index, 2, "sheetindex", int)
        width, height = numbers(sprite.sheet_size or "", 2, "sheetelementsize", int)
        x, y = column * width, row * height
    elif sprite.rect:
        x, y, width, height = numbers(sprite.rect, 4, "sourcerect", lambda value: int(float(value)))
        if width == height == 0:
            return 0, 0, *size
    else:
        return 0, 0, *size
    if min(width, height) <= 0:
        raise ValueError(f"empty rectangle {(x, y, width, height)}")
    if x >= size[0] or y >= size[1] or x + width <= 0 or y + height <= 0:
        raise ValueError(f"rectangle {(x, y, width, height)} does not overlap texture size {size}")
    if Image.MAX_IMAGE_PIXELS is not None and width * height > Image.MAX_IMAGE_PIXELS:
        raise ValueError(f"rectangle {(x, y, width, height)} exceeds the image pixel limit")
    return x, y, x + width, y + height


def parse_color(value, gui_colors):
    value = value.strip().lower()
    if value.startswith("gui."):
        value = gui_colors.get(value[4:], "white").strip().lower()
    if value in {"transparent", "transparentblack"}:
        return 0, 0, 0, 0
    if "," not in value:
        return ImageColor.getcolor(value, "RGBA")
    channels = tuple(float(channel.strip()) for channel in value.split(","))
    if len(channels) not in (3, 4) or not all(isfinite(channel) for channel in channels):
        raise ValueError(f"invalid icon color: {value!r}")
    scale = 255 if max(channels) <= 1 else 1
    color = tuple(int(min(255, max(0, channel * scale))) for channel in channels)
    return (*color, 255) if len(color) == 3 else color


def icon_tint(prefab, element, sprite, primary, gui_colors):
    attrs = element.attrib
    item_sprite = prefab.folder == "Items" and primary and sprite.tag == "sprite"
    command_sprite = prefab.folder in {"Orders", "Missions", "TraitorMissions"} and sprite.tag == "sprite"
    if not (sprite.is_icon or item_sprite or command_sprite):
        return WHITE

    if prefab.folder == "Talents":
        override = attrs.get("coloroverride", "").strip()
        if override:
            color = parse_color(override, gui_colors)
            if color != (0, 0, 0, 0):
                return color
        return parse_color("gui.green", gui_colors)

    palette = sprite.colors or attrs.get("iconcolors")
    if palette:
        stops = palette.split(";")
        low = parse_color(stops[(len(stops) - 1) // 2], gui_colors)
        high = parse_color(stops[len(stops) // 2], gui_colors)
        return tuple((low_channel + high_channel) // 2 for low_channel, high_channel in zip(low, high))
    if prefab.folder == "Afflictions" and sprite.tag == "icon":
        style = "buffcolormedium" if attrs.get("isbuff", "").lower() == "true" else "debuffcolormedium"
        return parse_color(gui_colors[style], gui_colors)

    color = None
    if sprite.tag == "inventoryicon":
        color = attrs.get("inventoryiconcolor")
    elif item_sprite:
        color = attrs.get("spritecolor")
    elif prefab.folder == "Jobs":
        color = attrs.get("uicolor")
    color = color or sprite.color or attrs.get("iconcolor") or attrs.get("color")
    if not color and prefab.folder == "Factions":
        main_icon = element.find("icon")
        if main_icon is not None:
            color = main_icon.get("color")
    return parse_color(color, gui_colors) if color else WHITE


def load_texture(path):
    with Image.open(path) as image:
        return image.convert("RGBA")


def texture_path(content, directories, texture):
    texture = texture.replace("\\", "/")
    path = Path(texture)
    prefix, slash, tail = texture.partition("/")
    if path.is_absolute():
        candidates = (path,)
    elif slash and prefix.lower() in {"content", "%moddir%"}:
        candidates = (content / tail,)
    else:
        candidates = (*(directory / path for directory in directories), content / path)
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate.is_relative_to(content) and candidate.is_file():
            return candidate
    raise ValueError(f"texture not found inside the content root: {texture!r}")


def render_sprite(texture, box, tint):
    image = texture.crop(box)
    if tint == WHITE:
        return image
    with image, Image.new("RGBA", image.size, tint) as color_layer:
        return ImageChops.multiply(image, color_layer)


def write_atomically(target, write):
    temporary = NamedTemporaryFile(dir=target.parent, suffix=".tmp", delete=False)
    temporary_path = Path(temporary.name)
    try:
        with temporary:
            result = write(temporary)
        temporary_path.replace(target)
        return result
    finally:
        temporary_path.unlink(missing_ok=True)


def read_export_data(path, report):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("expected an export record")
        return data
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        report(f"Warning: could not read export data from {path}: {error}")
        return {}


def recorded_path(data, key):
    value = data.get(key)
    return Path(value) if isinstance(value, str) and value.strip() else None


def file_hash(stream):
    digest = sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def matches_export(image_hash, target, previous):
    if not isinstance(previous, dict) or previous.get("rgba_sha256") != image_hash:
        return False
    if not previous.get("sha256") or not previous.get("exported_at"):
        return False
    try:
        with target.open("rb") as existing:
            return file_hash(existing) == previous["sha256"]
    except FileNotFoundError:
        return False


def save_sprite(image, target):
    def write_png(stream):
        image.save(stream, format="PNG")
        stream.seek(0)
        return file_hash(stream)

    target.parent.mkdir(parents=True, exist_ok=True)
    return write_atomically(target, write_png)


def unique_target(output, category, stem, used):
    folder = output / ascii_filename(category)
    target = folder / f"{stem}.png"
    number = 2
    while str(target).casefold() in used:
        target = folder / f"{stem}_{number}.png"
        number += 1
    used.add(str(target).casefold())
    return target


def export_sprites(content, output, *, identifiers=(), kinds=(), tags=(), all_sprites=False, report=print, log=None):
    content = content.resolve()
    output = output.resolve()
    if not content.is_dir():
        raise ValueError(f"Content directory does not exist: {content}")
    if output.is_relative_to(content):
        raise ValueError("Choose an output folder outside the input Content folder.")
    if output.exists() and not output.is_dir():
        raise ValueError(f"Output path is not a directory: {output}")

    data_path = Path.cwd() / "data"
    data = read_export_data(data_path, report)
    images = data.get("images")
    if recorded_path(data, "output_path") != output or not isinstance(images, dict):
        images = {}
    find_texture = lru_cache(maxsize=4096)(texture_path)
    open_texture = lru_cache(maxsize=16)(load_texture)

    identifiers = {value.casefold() for value in identifiers}
    kinds = {value.lower() for value in kinds}
    tags = {value.lower() for value in tags}
    if log is not None:
        log(f"Scanning XML files in {content}")
    prefabs, gui_colors, errors = read_prefabs(content, report)
    items = {
        prefab.identifier.casefold(): prefab for prefab in prefabs
        if prefab.kind == "item" and (
            prefab.element.get("variantof") or any(child.tag in {"sprite", "inventoryicon"} for child in prefab.element)
        )
    }
    items.update((prefab.identifier.casefold(), prefab) for prefab in prefabs if prefab.is_item)
    primary = []
    additional = []
    for prefab in prefabs:
        if identifiers and prefab.identifier.casefold() not in identifiers:
            continue
        if kinds and prefab.kind not in kinds and prefab.folder.lower() not in kinds:
            continue
        try:
            element, directories = prefab.element, (prefab.path.parent,)
            if (prefab.is_item or prefab.kind == "item") and element.get("variantof"):
                element, directories = resolve_item(prefab, items)
            sprites = sorted(
                (sprite for sprite in owned_sprites(element) if not tags or sprite.tag in tags),
                key=sprite_priority,
            )
        except ValueError as error:
            report(f"Error: {prefab.path} [{prefab.identifier}]: {error}")
            errors += 1
            continue
        if sprites:
            primary.append((prefab, element, directories, sprites[0]))
            if all_sprites:
                additional.extend((prefab, element, directories, sprite) for sprite in sprites[1:])
            elif prefab.folder == "Items" and sprites[0].tag == "inventoryicon" and sprites[0].depth == 1:
                item_sprite = next(
                    (sprite for sprite in sprites[1:] if sprite.tag == "sprite" and sprite.depth == 1),
                    None,
                )
                if item_sprite is not None:
                    additional.append((prefab, element, directories, item_sprite))

    exported = 0
    skipped = 0
    padded = 0
    processed = 0
    total = len(primary) + len(additional)
    used = set()
    matched = set()
    for entries, suffix in ((primary, False), (additional, True)):
        for prefab, element, directories, sprite in entries:
            matched.add(prefab.identifier.casefold())
            stem = ascii_filename(prefab.identifier)
            if suffix:
                stem += f"_{ascii_filename(sprite.tag)}"
            target = unique_target(output, prefab.folder, stem, used)
            relative = target.relative_to(output).as_posix()
            processed += 1
            if log is not None:
                log(f"[{processed}/{total}] Processing {relative}")
            try:
                texture = open_texture(find_texture(content, directories, sprite.texture))
                box = crop_box(sprite, texture.size)
                tint = icon_tint(prefab, element, sprite, not suffix, gui_colors)
                with render_sprite(texture, box, tint) as image:
                    digest = sha256(pack(">II", *image.size))
                    digest.update(image.tobytes())
                    image_hash = digest.hexdigest()
                    if matches_export(image_hash, target, images.get(relative)):
                        skipped += 1
                        if log is not None:
                            log(f"[{processed}/{total}] Skipped unchanged {relative}")
                    else:
                        png_hash = save_sprite(image, target)
                        images[relative] = {
                            "sha256": png_hash,
                            "rgba_sha256": image_hash,
                            "exported_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                        }
                        exported += 1
                left, top, right, bottom = box
                if min(left, top) < 0 or right > texture.width or bottom > texture.height:
                    padded += 1
            except (OSError, ValueError, Image.DecompressionBombError) as error:
                report(f"Error: {prefab.path} [{prefab.identifier}/{sprite.tag}]: {error}")
                errors += 1
    for identifier in sorted(identifiers - matched):
        report(f"Error: no matching sprite for identifier {identifier!r}")
        errors += 1
    if not exported and not skipped and not errors:
        report("Error: no matching sprites with an identifier and a texture.")
        errors += 1
    try:
        data = {"content_path": str(content), "output_path": str(output), "images": images}
        encoded = (json.dumps(data, ensure_ascii=True, indent=2) + "\n").encode("utf-8")
        write_atomically(data_path, lambda stream: stream.write(encoded))
    except OSError as error:
        report(f"Error: could not write export data to {data_path}: {error}")
        errors += 1
    return ExportResult(exported, skipped, padded, errors, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gui", action="store_true", help="open the folder selection window (also the default with no arguments)")
    parser.add_argument("--content", type=Path, help="game Content directory (uses saved path if omitted)")
    parser.add_argument("--output", type=Path, help="output directory (uses saved path if omitted)")
    parser.add_argument("--identifier", action="append", help="export this identifier (repeatable)")
    parser.add_argument("--kind", action="append", help="owner XML tag or output category, for example Items (repeatable)")
    parser.add_argument("--sprite-tag", action="append", help="sprite XML tag, for example Sprite (repeatable)")
    parser.add_argument("--all-sprites", action="store_true", help="include additional sprites for each identifier")
    args = parser.parse_args()

    def report(message):
        print(message, file=sys.stderr)

    content = args.content
    output = args.output
    if content is None or output is None:
        data = read_export_data(Path.cwd() / "data", report)
        content = content or recorded_path(data, "content_path")
        output = output or recorded_path(data, "output_path")
    if args.gui or len(sys.argv) == 1:
        from sprite_window import show_window

        show_window(content, output, args.all_sprites, export_sprites)
        return 0
    missing = [flag for flag, value in (("--content", content), ("--output", output)) if value is None]
    if missing:
        parser.error(f"missing paths in data; provide {' and '.join(missing)}")
    try:
        result = export_sprites(
            content,
            output,
            identifiers=args.identifier or (),
            kinds=args.kind or (),
            tags=args.sprite_tag or (),
            all_sprites=args.all_sprites,
            report=report,
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(result.summary())
    return int(result.errors > 0)


if __name__ == "__main__":
    sys.exit(main())
