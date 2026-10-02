#!/usr/bin/env python3
"""Build a B1 DXT5 atlas and 0x1C glyph maps from a selected TrueType font."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import shutil
import statistics
import struct
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

try:
    from .font_corpus import (
        FontCorpus,
        codepoint_list,
        collect_characters,
        validate_glyphs,
        write_corpus_artifacts,
    )
    from .font_metrics import center_middle_dot
except ImportError:  # Direct execution: python tools/fonts/build_b1_corpus_font.py
    from font_corpus import (
        FontCorpus,
        codepoint_list,
        collect_characters,
        validate_glyphs,
        write_corpus_artifacts,
    )
    from font_metrics import center_middle_dot

TABLE_OFFSET = 0xD7F990
ORIGINAL_RECORD_COUNT = 2565
RECORD_SIZE = 0x1C
FONT_SIZE = 22
DDS_HEADER_SIZE = 128
DXT5_BLOCK_SIZE = 16
MAX_D3D11_TEXTURE_DIMENSION = 16384
def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def display_path(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root))
    except ValueError:
        return str(path.resolve())


def align4(value: int) -> int:
    return (value + 3) & ~3


def packed_utf8(character: str) -> int:
    encoded = character.encode("utf-8")[::-1]
    if len(encoded) > 4:
        raise ValueError(f"U+{ord(character):04X} does not fit the B1 key")
    return int.from_bytes(encoded.ljust(4, b"\0"), "little")


def unpacked_character(key: int) -> str | None:
    encoded = key.to_bytes(4, "little").rstrip(b"\0")[::-1]
    try:
        character = encoded.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return character if len(character) == 1 else None


def next_power_of_two(value: int) -> int:
    return 1 << (value - 1).bit_length()


def required_bottom(character_count: int, atlas_width: int, start_y: int, record_size: tuple[int, int]) -> int:
    cell_width, cell_height = (align4(value) + 4 for value in record_size)
    columns = atlas_width // cell_width
    if not columns:
        raise ValueError("Glyph cell is wider than the atlas")
    return start_y + ((character_count + columns - 1) // columns) * cell_height


def copy_source_bc3_blocks(source: bytes, destination: bytearray, source_size: tuple[int, int], destination_size: tuple[int, int]) -> int:
    """Copy the source atlas into the destination's top-left without recompressing a BC3 block."""
    source_width, source_height = source_size
    destination_width, destination_height = destination_size
    if destination_width < source_width or destination_height < source_height:
        raise ValueError("destination cannot be smaller than the source atlas")
    if any(value % 4 for value in (*source_size, *destination_size)):
        raise ValueError("BC3 dimensions must be multiples of four")
    source_row_size = (source_width // 4) * DXT5_BLOCK_SIZE
    destination_row_size = (destination_width // 4) * DXT5_BLOCK_SIZE
    for block_y in range(source_height // 4):
        source_offset = DDS_HEADER_SIZE + block_y * source_row_size
        destination_offset = DDS_HEADER_SIZE + block_y * destination_row_size
        destination[destination_offset : destination_offset + source_row_size] = source[
            source_offset : source_offset + source_row_size
        ]
    return source_height // 4 * source_width // 4


def build(args: argparse.Namespace) -> dict:
    root = args.game_root.resolve()
    text_root = args.text_root.resolve() if args.text_root and args.text_root.exists() else None
    translation_table = args.translation_table
    translation_tables = (translation_table.resolve(),) if translation_table else ()
    source_dir = args.source_dir.resolve()
    source_dds = source_dir / "000.dds"
    output = args.output.resolve()
    work_dir = output / "mainfonthd720_0"
    corpus = collect_characters(
        text_root,
        translation_tables=translation_tables,
        source_kind="public exact-source translation table",
        target_language=args.target_language,
    )
    corpus_characters = corpus.characters
    occurrences = corpus.visible_occurrences
    input_files = corpus.files
    snapshot_hash = corpus.snapshot_sha256

    dll_path = args.dll.resolve()
    dll = dll_path.read_bytes()
    original_table = dll[TABLE_OFFSET : TABLE_OFFSET + ORIGINAL_RECORD_COUNT * RECORD_SIZE]
    original_records = [original_table[index : index + RECORD_SIZE] for index in range(0, len(original_table), RECORD_SIZE)]
    original_keys = [struct.unpack_from("<I", record)[0] for record in original_records]
    original_decoded = [struct.unpack("<IHHHHiiii", record) for record in original_records]
    reference_rows = [
        record
        for record in original_decoded
        if (character := unpacked_character(record[0])) is not None
        and 0x3041 <= ord(character) <= 0x30FF
        and record[3] > 10
        and record[4] > 10
    ]
    if not reference_rows:
        raise ValueError("Could not determine original B1 kana glyph dimensions")
    reference_metrics = {
        "sample_count": len(reference_rows),
        "width": round(statistics.median(record[3] for record in reference_rows)),
        "height": round(statistics.median(record[4] for record in reference_rows)),
        "left": round(statistics.median(record[5] for record in reference_rows)),
        "top": round(statistics.median(record[6] for record in reference_rows)),
        "advance": round(statistics.median(record[7] for record in reference_rows)),
    }
    existing_hangul = {
        character
        for key in original_keys
        if (character := unpacked_character(key)) is not None and 0xAC00 <= ord(character) <= 0xD7A3
    }
    existing_same_key = sorted(set(corpus_characters) & existing_hangul)
    characters_file = getattr(args, "characters_file", None)
    if characters_file is None:
        characters = corpus_characters
    else:
        raw_characters = characters_file.read_text(encoding="utf-8")
        if raw_characters.endswith("\n"):
            raw_characters = raw_characters[:-1]
        characters = list(raw_characters)
        if any(character in "\r\n" for character in characters) or len(characters) != len(set(characters)):
            raise ValueError("characters file contains a line break or duplicate character")
        characters.sort(key=packed_utf8)

    source_bytes = source_dds.read_bytes()
    source_paths = {
        "dll": dll_path,
        "dds": source_dds,
        "g1t_json": source_dir / "g1t.json",
    }
    source_g1t = source_dir.parent / f"{source_dir.name}.g1t"
    if source_g1t.is_file():
        source_paths["g1t"] = source_g1t
    source_hashes_before = {name: file_sha256(path) for name, path in source_paths.items()}
    if source_bytes[:4] != b"DDS " or source_bytes[84:88] != b"DXT5":
        raise ValueError("B1 source must be a legacy-header DXT5 DDS")
    with Image.open(io.BytesIO(source_bytes)) as opened:
        source_image = opened.convert("RGBA")
    if source_image.size != (2048, 1024):
        raise ValueError(f"unexpected B1 source size: {source_image.size}")
    font = ImageFont.truetype(str(args.font), FONT_SIZE)
    source_alpha = source_image.getchannel("A")
    source_ink_rows = []
    for record in reference_rows:
        crop = source_alpha.crop((record[1], record[2], record[1] + record[3], record[2] + record[4]))
        bounds = crop.point(lambda value: 255 if value > 127 else 0).getbbox()
        if bounds:
            source_ink_rows.append((bounds[2] - bounds[0], bounds[3] - bounds[1], bounds[0], bounds[1]))
    if not source_ink_rows:
        raise ValueError("Could not determine original B1 kana ink bounds")
    source_ink = {
        "width": statistics.median(row[0] for row in source_ink_rows),
        "height": statistics.median(row[1] for row in source_ink_rows),
        "x": round(statistics.median(row[2] for row in source_ink_rows)),
        "y": round(statistics.median(row[3] for row in source_ink_rows)),
    }
    font_glyph_validation = validate_glyphs(font, characters)
    if characters:
        if args.atlas_width < source_image.width or args.atlas_height <= source_image.height:
            raise ValueError("atlas must contain the source atlas in its top-left")
        if args.atlas_width % 4 or args.atlas_height % 4:
            raise ValueError("atlas dimensions must be block aligned")
        if args.atlas_width > MAX_D3D11_TEXTURE_DIMENSION or args.atlas_height > MAX_D3D11_TEXTURE_DIMENSION:
            raise ValueError("atlas dimensions exceed the D3D11 2D texture limit")
        minimum_height = next_power_of_two(required_bottom(
            len(characters), args.atlas_width, source_image.height,
            (reference_metrics["width"], reference_metrics["height"]),
        ))
        atlas_size = (args.atlas_width, max(args.atlas_height, minimum_height))
    else:
        minimum_height = source_image.height
        atlas_size = source_image.size
    if atlas_size[1] > MAX_D3D11_TEXTURE_DIMENSION:
        raise ValueError("required atlas height exceeds the D3D11 2D texture limit")
    atlas = Image.new("RGBA", atlas_size, (255, 255, 255, 0))
    atlas.paste(source_image, (0, 0))
    alpha = atlas.getchannel("A")
    alpha_draw = ImageDraw.Draw(alpha)
    records = []
    cursor_x = 0
    cursor_y = source_image.height
    row_height = 0
    for character in characters:
        bbox = font.getbbox(character)
        left, top, right, bottom = bbox
        width = right - left
        height = bottom - top
        if width <= 0 or height <= 0:
            raise ValueError(f"empty glyph: U+{ord(character):04X}")
        is_hangul = 0xAC00 <= ord(character) <= 0xD7A3
        if is_hangul and (width > reference_metrics["width"] or height > reference_metrics["height"]):
            raise ValueError(f"U+{ord(character):04X} glyph exceeds the original glyph cell")
        record_width = reference_metrics["width"] if is_hangul else width
        record_height = reference_metrics["height"] if is_hangul else height
        cell_width = align4(record_width) + 4
        cell_height = align4(record_height) + 4
        if cursor_x + cell_width > atlas.width:
            cursor_x = 0
            cursor_y += row_height
            row_height = 0
        if cursor_y + cell_height > atlas.height:
            raise ValueError(
                f"{len(records)} of {len(characters)} glyphs fit; increase --atlas-height above {atlas.height}"
            )
        y_padding = min(source_ink["y"], record_height - height)
        alpha_draw.text((cursor_x - left, cursor_y + y_padding - top), character, font=font, fill=255)
        record = {
            "character": character,
            "codepoint": f"U+{ord(character):04X}",
            "utf8_packed": packed_utf8(character),
            "utf8_packed_hex": f"0x{packed_utf8(character):08X}",
            "x": cursor_x,
            "y": cursor_y,
            "width": record_width,
            "height": record_height,
            "left": reference_metrics["left"] if is_hangul else left,
            "top": reference_metrics["top"] - 1 if is_hangul else top,
            "advance": 23 if is_hangul else round(font.getlength(character)),
            "bottom": 0,
            "occurrences_in_current_ko_json": occurrences[character],
        }
        records.append(record)
        cursor_x += cell_width
        row_height = max(row_height, cell_height)
    used_bottom = cursor_y + row_height
    atlas.putalpha(alpha)
    hangul_records = [record for record in records if 0xAC00 <= ord(record["character"]) <= 0xD7A3]
    generated_median_height = statistics.median(record["height"] for record in hangul_records) if hangul_records else 0
    if hangul_records and generated_median_height != reference_metrics["height"]:
        raise ValueError("B1 replacement glyph height differs from the original reference")

    encoded = io.BytesIO()
    atlas.save(encoded, format="DDS", pixel_format="DXT5")
    pillow_bytes = encoded.getvalue()
    expected_length = DDS_HEADER_SIZE + atlas.width * atlas.height
    if len(pillow_bytes) != expected_length or pillow_bytes[84:88] != b"DXT5":
        raise ValueError("unexpected DXT5 encoder result")
    encoded_bytes = bytearray(source_bytes[:DDS_HEADER_SIZE] + pillow_bytes[DDS_HEADER_SIZE:])
    struct.pack_into("<I", encoded_bytes, 12, atlas.height)
    struct.pack_into("<I", encoded_bytes, 16, atlas.width)
    struct.pack_into("<I", encoded_bytes, 20, atlas.width * atlas.height)
    struct.pack_into("<I", encoded_bytes, 28, 1)
    source_payload_size = source_image.width * source_image.height
    if len(source_bytes) != DDS_HEADER_SIZE + source_payload_size:
        raise ValueError("unexpected B1 source DDS length")
    copied_source_blocks = copy_source_bc3_blocks(
        source_bytes,
        encoded_bytes,
        source_image.size,
        atlas.size,
    )
    source_dds_byte_identical = bytes(encoded_bytes) == source_bytes
    if not characters and not source_dds_byte_identical:
        raise ValueError("zero-character B1 output must be byte-identical to the source DDS")

    work_dir.mkdir(parents=True, exist_ok=True)
    corpus_manifest = write_corpus_artifacts(corpus, text_root, output)
    added_characters_path = output / "added-characters.txt"
    added_characters_path.write_text("".join(characters) + ("\n" if characters else ""), encoding="utf-8")
    output_dds = work_dir / "000.dds"
    output_dds.write_bytes(encoded_bytes)
    shutil.copy2(source_dir / "g1t.json", work_dir / "g1t.json")
    with Image.open(output_dds) as opened:
        decoded = opened.convert("RGBA")
    if decoded.size != atlas.size:
        raise ValueError(f"encoded size mismatch: {decoded.size}")
    if ImageChops.difference(decoded.crop((0, 0, 2048, 1024)), source_image).getbbox() is not None:
        raise ValueError("decoded original half changed")
    destination_row_size = (atlas.width // 4) * DXT5_BLOCK_SIZE
    source_row_size = (source_image.width // 4) * DXT5_BLOCK_SIZE
    original_blocks_identical = all(
        encoded_bytes[
            DDS_HEADER_SIZE + block_y * destination_row_size :
            DDS_HEADER_SIZE + block_y * destination_row_size + source_row_size
        ]
        == source_bytes[
            DDS_HEADER_SIZE + block_y * source_row_size :
            DDS_HEADER_SIZE + (block_y + 1) * source_row_size
        ]
        for block_y in range(source_image.height // 4)
    )
    if not original_blocks_identical:
        raise ValueError("source compressed blocks were not preserved")
    decoded_alpha = decoded.getchannel("A")
    outside = []
    empty_after_compression = []
    decoded_signatures: dict[bytes, list[str]] = {}
    generated_ink_rows = []
    for record in records:
        right = record["x"] + record["width"]
        bottom = record["y"] + record["height"]
        codepoint = record["codepoint"]
        if record["x"] < 0 or record["y"] < 0 or right > decoded.width or bottom > decoded.height:
            outside.append(codepoint)
            continue
        crop = decoded_alpha.crop((record["x"], record["y"], right, bottom))
        bounds = crop.point(lambda value: 255 if value > 127 else 0).getbbox()
        if bounds is None:
            empty_after_compression.append(codepoint)
        elif 0xAC00 <= ord(record["character"]) <= 0xD7A3:
            generated_ink_rows.append((bounds[2] - bounds[0], bounds[3] - bounds[1], bounds[0], bounds[1]))
        signature = hashlib.sha256(
            struct.pack("<HH", crop.width, crop.height) + crop.tobytes()
        ).digest()
        decoded_signatures.setdefault(signature, []).append(codepoint)
    duplicate_decoded_crops = [
        codepoints for codepoints in decoded_signatures.values() if len(codepoints) > 1
    ]
    if outside or empty_after_compression or duplicate_decoded_crops:
        raise ValueError(
            f"decoded glyph validation failed: outside={outside}, empty={empty_after_compression}, "
            f"duplicate_crops={duplicate_decoded_crops}"
        )
    generated_ink = {
        "width": statistics.median(row[0] for row in generated_ink_rows) if generated_ink_rows else 0,
        "height": statistics.median(row[1] for row in generated_ink_rows) if generated_ink_rows else 0,
        "x": statistics.median(row[2] for row in generated_ink_rows) if generated_ink_rows else 0,
        "y": statistics.median(row[3] for row in generated_ink_rows) if generated_ink_rows else 0,
    }
    if hangul_records and any(abs(generated_ink[name] - source_ink[name]) > 1 for name in generated_ink):
        raise ValueError(f"B1 replacement glyph ink bounds or position differ from the original reference: original={source_ink}, generated={generated_ink}")
    decoded_glyph_validation = {
        "checked_count": len(records),
        "channel": "A",
        "outside_atlas": 0,
        "empty_after_texture_compression": 0,
        "duplicate_crop_groups": 0,
        "distinct_crop_sha256_count": len(decoded_signatures),
        "passed": True,
    }

    center_y = reference_metrics["top"] + source_ink["y"] + source_ink["height"] / 2
    for record in records:
        center_middle_dot(record, decoded_alpha, center_y)

    preview = Image.alpha_composite(Image.new("RGBA", decoded.size, (32, 36, 48, 255)), decoded)
    preview.save(output / "atlas-preview-on-dark.png", optimize=True)
    if used_bottom > source_image.height:
        korean_preview = preview.crop((0, source_image.height, preview.width, used_bottom))
    else:
        korean_preview = Image.new("RGBA", (preview.width, 1), (32, 36, 48, 255))
    korean_preview.save(output / "korean-region-preview-on-dark.png", optimize=True)
    decoded.save(output / "atlas-preview-transparent.png", optimize=True)
    records_by_key = sorted(records, key=lambda record: record["utf8_packed"])
    mapped_characters = [record["character"] for record in records]
    missing_from_mapping = sorted(set(characters) - set(mapped_characters))
    extra_in_mapping = sorted(set(mapped_characters) - set(characters))
    duplicate_mapping_characters = len(mapped_characters) - len(set(mapped_characters))
    if missing_from_mapping or extra_in_mapping or duplicate_mapping_characters:
        raise ValueError(
            "mapping does not exactly match the selected characters: "
            f"missing=[{codepoint_list(list(map(ord, missing_from_mapping)))}], "
            f"extra=[{codepoint_list(list(map(ord, extra_in_mapping)))}], "
            f"duplicates={duplicate_mapping_characters}"
        )
    extension_bytes = b"".join(
        struct.pack(
            "<IHHHHiiii",
            record["utf8_packed"],
            record["x"],
            record["y"],
            record["width"],
            record["height"],
            record["left"],
            record["top"],
            record["advance"],
            record["bottom"],
        )
        for record in records_by_key
    )
    extension_keys = [record["utf8_packed"] for record in records_by_key]
    compatibility_record = None
    if not extension_bytes:
        compatibility_key = packed_utf8("?")
        try:
            compatibility_index = original_keys.index(compatibility_key)
        except ValueError as error:
            raise ValueError("Original B1 glyph table has no ? record for an empty extension table") from error
        extension_bytes = original_records[compatibility_index]
        extension_keys = [compatibility_key]
        compatibility_record = "U+003F copied byte-for-byte from the original table"
    extension_path = output / "ko-glyph-table-0x1c.bin"
    extension_path.write_bytes(extension_bytes)

    duplicates = sorted(set(original_keys) & set(extension_keys))
    source_hashes_after = {name: file_sha256(path) for name, path in source_paths.items()}
    source_files_unchanged = {
        name: source_hashes_before[name] == source_hashes_after[name]
        for name in source_paths
    }
    if not all(source_files_unchanged.values()):
        changed = [name for name, unchanged in source_files_unchanged.items() if not unchanged]
        raise RuntimeError(f"B1 source files changed during font generation: {changed}")

    mapping = {
        "schema_version": 1,
        "purpose": "B1 current translated-message extension-first records; original records remain separate and byte-identical",
        "record_layout": "<IHHHHiiii (0x1C bytes)",
        "sort_order": "unsigned packed UTF-8 key ascending; search this table before the original table",
        "records": records_by_key,
    }
    mapping_path = output / "mapping.json"
    mapping_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    mapping_csv_path = output / "mapping.csv"
    with mapping_csv_path.open("w", encoding="utf-8", newline="") as stream:
        fieldnames = [
            "character", "codepoint", "utf8_packed", "utf8_packed_hex",
            "x", "y", "width", "height", "left", "top", "advance",
            "bottom", "occurrences_in_current_ko_json",
        ]
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records_by_key)

    result = {
        "status": (
            "generated font assets and mapping only; B1.dll was not modified" if characters else
            "source-identical assets; translation inputs need no added characters"
        ),
        "generator": display_path(Path(__file__), root),
        "inputs": {
            "text_root": display_path(text_root, root) if text_root else None,
            "translation_table": display_path(translation_table, root) if translation_table else None,
            "json_file_count": len(input_files),
            "json_snapshot_sha256": snapshot_hash,
            "message_snapshot_sha256": corpus.message_snapshot_sha256,
            "message_count": corpus.message_count,
            "field_name": corpus.field_name,
            "field_kind": corpus.field_kind,
            "source_kind": corpus.source_kind,
            "target_language": args.target_language,
            "character_set": "printable characters from selected translation values",
            "source_dds": display_path(source_dds, root),
            "source_dds_sha256": sha256(source_bytes),
            "font": display_path(args.font, root),
            "font_sha256": file_sha256(args.font),
            "font_size_px": FONT_SIZE,
            "font_distribution": {"display_name": args.font.stem},
            "font_glyph_validation": font_glyph_validation,
            "source_files": {
                name: {
                    "path": display_path(path, root),
                    "sha256_before": source_hashes_before[name],
                    "sha256_after": source_hashes_after[name],
                    "unchanged_during_build": source_files_unchanged[name],
                }
                for name, path in source_paths.items()
            },
        },
        "atlas": {
            "format": "DXT5/BC3",
            "old_size": [2048, 1024],
            "new_size": list(atlas.size),
            "generated_start_y": source_image.height,
            "korean_start_y": source_image.height,
            "used_bottom_exclusive": used_bottom,
            "free_rows_after_korean": atlas.height - used_bottom,
            "minimum_power_of_two_height_for_this_mapping": next_power_of_two(used_bottom),
            "recommended_dimensions_for_this_mapping": [
                source_image.width,
                next_power_of_two(used_bottom),
            ],
            "original_compressed_blocks_byte_identical": original_blocks_identical,
            "original_compressed_block_count": copied_source_blocks,
            "original_payload_is_unchanged_prefix": atlas.width == source_image.width,
            "decoded_original_half_pixel_identical": True,
            "source_and_output_dds_byte_identical": source_dds_byte_identical,
            "dds": display_path(output_dds, root),
            "dds_sha256": file_sha256(output_dds),
        },
        "glyphs": {
            "corpus_characters": len(corpus_characters),
            "corpus_complete_hangul": sum(0xAC00 <= ord(character) <= 0xD7A3 for character in corpus_characters),
            "existing_coverage_reused": 0,
            "existing_same_key_overridden": len(existing_same_key),
            "new_required": len(records),
            "runtime_record_count": len(extension_keys),
            "compatibility_record": compatibility_record,
            "uncovered": len(set(characters) - set(mapped_characters)),
            "coverage_first": f"U+{ord(characters[0]):04X}" if characters else None,
            "coverage_last": f"U+{ord(characters[-1]):04X}" if characters else None,
            "coverage_missing": 0,
            "coverage_extra": 0,
            "coverage_duplicates": duplicate_mapping_characters,
            "total_occurrences": sum(occurrences.values()),
            "original_record_count": ORIGINAL_RECORD_COUNT,
            "duplicate_keys_against_original": len(duplicates),
            "all_original_records_preserved": True,
            "font_metric_calibration": {
                "reference": reference_metrics,
                "reference_ink": source_ink,
                "generated_ink": generated_ink,
                "generated_median_width": statistics.median(record["width"] for record in records) if records else 0,
                "generated_median_height": generated_median_height,
                "generated_median_left": statistics.median(record["left"] for record in records) if records else 0,
                "generated_median_top": statistics.median(record["top"] for record in records) if records else 0,
                "generated_median_advance": statistics.median(record["advance"] for record in records) if records else 0,
            },
            "decoded_glyph_validation": decoded_glyph_validation,
            "extension_table": display_path(extension_path, root),
            "extension_table_sha256": file_sha256(extension_path),
            "mapping_json": display_path(mapping_path, root),
            "mapping_json_sha256": file_sha256(mapping_path),
            "mapping_csv": display_path(mapping_csv_path, root),
            "mapping_csv_sha256": file_sha256(mapping_csv_path),
            "characters": display_path(output / "characters.txt", root),
            "characters_sha256": corpus_manifest["characters_sha256"],
            "added_characters": display_path(added_characters_path, root),
            "added_characters_sha256": file_sha256(added_characters_path),
            "corpus_manifest": display_path(output / "corpus.json", root),
        },
        "activation_requirement": {
            "glyph_lookup": (
                "ASI must search the generated extension table first, then fall back to the untouched original table."
                if characters else "No extension records are needed for the current corpus."
            ),
            "runtime_dimensions": (
                "B1.dll font descriptor at RVA 0xD80D60 (preferred VA 0x180D80D60; "
                "runtime module_base+0xD80D60) stores uint16 width/height. ASI must change "
                f"2048x1024 to {atlas.width}x{atlas.height}; changing only the G1T/DDS header is insufficient."
                if atlas.size != source_image.size else
                "The DDS remains 2048x1024, so the original B1 font descriptor does not need a size update."
            ),
            "source_executable_modified": False,
        },
    }
    current_snapshot = collect_characters(
        text_root,
        translation_tables=translation_tables,
        source_kind="public exact-source translation table",
        target_language=args.target_language,
    )
    corpus_unchanged = (
        current_snapshot.snapshot_sha256 == corpus.snapshot_sha256
        and current_snapshot.message_snapshot_sha256 == corpus.message_snapshot_sha256
    )
    if not corpus_unchanged:
        marker = {
            "game": "B1",
            "status": "invalid; do not use files in this directory",
            "reason": "translation files changed during B1 font generation",
        }
        (output / "BUILD_INVALID.json").write_text(
            json.dumps(marker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        raise RuntimeError(marker["reason"])
    summary_path = output / "summary.json"
    summary_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "BUILD_INVALID.json").unlink(missing_ok=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--game-root", type=Path, required=True)
    parser.add_argument("--dll", type=Path, required=True)
    parser.add_argument("--text-root", type=Path)
    parser.add_argument("--translation-table", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--font", type=Path, required=True)
    parser.add_argument("--characters-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target-language", required=True)
    parser.add_argument("--atlas-width", type=int, default=2048)
    parser.add_argument("--atlas-height", type=int, default=2048)
    args = parser.parse_args()
    print(json.dumps(build(args), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
