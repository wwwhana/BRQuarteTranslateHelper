#!/usr/bin/env python3
"""Build corpus-scoped font assets for BLUE REFLECTION Quartet."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
import statistics
import struct
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

from PIL import Image, ImageFont

try:
    from . import build_b1_corpus_font, build_b1_menu_font
    from .font_corpus import FontCorpus, collect_characters, inspect_glyphs, write_corpus_artifacts
    from .font_metrics import center_middle_dot, sdf_glyph_offsets
except ImportError:  # Direct execution: python tools/fonts/build_font_assets.py
    import build_b1_corpus_font
    import build_b1_menu_font
    from font_corpus import FontCorpus, collect_characters, inspect_glyphs, write_corpus_artifacts
    from font_metrics import center_middle_dot, sdf_glyph_offsets


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ROOT = PROJECT_ROOT
SOURCE_ROOT = PROJECT_ROOT / "build/font-sources"
TRANSLATIONS = PROJECT_ROOT / "translations"
FONT = Path()
MSDFGEN = Path()
GUST_G1T = Path()
OUTPUT_ROOT = PROJECT_ROOT / "build/font-assets"
RUNTIME_ROOT = PROJECT_ROOT / "build/font-runtime"
TARGET_LANGUAGE = None
INVALID_MARKER = "BUILD_INVALID.json"
RECORD_48 = struct.Struct("<II8d")
DDS_DX10_HEADER_SIZE = 148
SDF_PIXEL_RANGE = 4
GLYPH_ALIASES = {
    "➇": "⑧",
    "É": "E",
    "˖": "+",
    "▿": "▽",
    "◝": "(",
    "◜": ")",
    "✧": "☆",
    "—": "―",
    "〇": "○",
    "〜": "～",
    "−": "－",
    "ー": "―",
    "ｰ": "-",
}
GLYPH_SUBSTITUTIONS = {
    # Some fonts omit the Japanese middle dot. Draw the supported dot while
    # keeping the original key and full-width spacing in the generated record.
    "・": "·",
}


GAME_CONFIGS = {
    "B0": {
        "source_kind": "B0 Japanese-slot combined string table Text attributes (XML)",
        "dll": "B0.dll",
        "lookup_rva": "0x577050",
        "shared_source_game": "B4",
        "shared_source_reason": (
            "B0.dll selects the B4 resource root and no B0-specific dynamic-font G1T exists; "
            "the B0 title screen b0_title.g1t is a separate image asset."
        ),
        "shared_source_evidence": {
            "b0_font_path_builder_rva": "0x575D20",
            "font_prefix_rva": "0x11EB018",
            "font_prefix_reference_rva": "0x575D8D",
            "font_filename_selector_rva": "0x576920",
            "open_call_rvas": ["0x575E0F", "0x576288"],
            "requested_runtime_path": "Font/<selected main-font filename>",
            "resolved_member_example": "\\data\\b4\\x64\\res_jp\\font\\mainfont00_hd1080_0.g1t",
            "b0_dynamic_font_member_found": False,
            "title_asset_separate": "ui/b0_title.g1t is a BC7 title-screen image, not a dynamic glyph atlas",
        },
        "slots": {
            "main00": {"name": "mainfont00_hd1080_0", "offset": 0x11EA4A0, "count": 1821, "safe_y": 844, "size": 32, "render_size": 28, "descriptor_rva": 0x11EB040},
            "main01": {"name": "mainfont01_hd1080_0", "offset": 0x120A4D0, "count": 2675, "safe_y": 844, "size": 36, "render_size": 35, "descriptor_rva": 0x11EB058},
            "main02": {"name": "mainfont02_hd1080_0", "offset": 0x1239530, "count": 2675, "safe_y": 888, "size": 36, "render_size": 35, "descriptor_rva": 0x11EB070},
        },
    },
    "B1": {
        "source_kind": "gust_tools JSON-like document (hex numeric literals permitted)",
        "dll": "B1.dll",
        "lookup_rva": "0x2869D0",
        "slots": {
            "main": {"offset": 0xD7F990, "count": 2565, "size": 24, "render_size": 22, "record_size": 0x1C},
        },
    },
    "B2": {
        "source_kind": "gust_tools JSON-like document (hex numeric literals permitted)",
        "dll": "B2.dll",
        "lookup_rva": "0x5692E0",
        "shared_source_game": "B2",
        "slots": {
            "main00": {"name": "mainfont00_hd1080_0", "offset": 0x13CCEF0, "count": 1821, "safe_y": 788, "size": 32, "render_size": 31, "descriptor_rva": 0x13CE890},
            "main01": {"name": "mainfont01_hd1080_0", "offset": 0x13ECF20, "count": 2675, "safe_y": 788, "size": 36, "render_size": 34, "descriptor_rva": 0x13CE8A8},
            "main02": {"name": "mainfont02_hd1080_0", "offset": 0x141BF80, "count": 2675, "safe_y": 820, "size": 36, "render_size": 34, "descriptor_rva": 0x13CE8C0},
        },
    },
    "B3": {
        "source_kind": "gust_tools JSON-like document (hex numeric literals permitted)",
        "dll": "B3.dll",
        "lookup_rva": "0x56BDA0",
        "shared_source_game": "B3",
        "slots": {
            "main00": {"name": "mainfont00_hd1080_0", "offset": 0x13E9C90, "count": 1821, "safe_y": 800, "size": 32, "render_size": 31, "descriptor_rva": 0x13EAC30},
            "main01": {"name": "mainfont01_hd1080_0", "offset": 0x1409CC0, "count": 2675, "safe_y": 800, "size": 36, "render_size": 35, "descriptor_rva": 0x13EAC48},
            "main02": {"name": "mainfont02_hd1080_0", "offset": 0x1438D20, "count": 2675, "safe_y": 824, "size": 36, "render_size": 34, "descriptor_rva": 0x13EAC60},
        },
    },
    "B4": {
        "source_kind": "gust_tools JSON-like document (hex numeric literals permitted)",
        "dll": "B4.dll",
        "lookup_rva": "0x571A70",
        "shared_source_game": "B4",
        "slots": {
            "main00": {"name": "mainfont00_hd1080_0", "offset": 0x1456DC0, "count": 1821, "safe_y": 844, "size": 32, "render_size": 28, "descriptor_rva": 0x1458360},
            "main01": {"name": "mainfont01_hd1080_0", "offset": 0x1476DF0, "count": 2675, "safe_y": 844, "size": 36, "render_size": 35, "descriptor_rva": 0x1458378},
            "main02": {"name": "mainfont02_hd1080_0", "offset": 0x14A5E50, "count": 2675, "safe_y": 888, "size": 36, "render_size": 35, "descriptor_rva": 0x1458390},
        },
    },
}


def collect_game_hangul(game: str):
    config = GAME_CONFIGS[game]
    return collect_characters(
        None,
        translation_tables=(TRANSLATIONS / f"{game}.json",),
        source_kind="public exact-source translation table",
        target_language=TARGET_LANGUAGE,
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def project_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def mark_invalid(game: str, reason: str) -> None:
    output = output_directory(game)
    output.mkdir(parents=True, exist_ok=True)
    marker = {
        "game": game,
        "status": "invalid; do not use files in this directory",
        "reason": reason,
    }
    (output / INVALID_MARKER).write_text(
        json.dumps(marker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def build_game(game: str) -> dict:
    try:
        result = build_b1() if game == "B1" else build_48(game)
        if game == "B1":
            result["menu_font"] = build_b1_menu_font.generate(
                game_root=ROOT,
                source_root=SOURCE_ROOT,
                translations_root=TRANSLATIONS,
                font=FONT,
                output=output_directory("B1") / "menu-g1n",
                target_language=TARGET_LANGUAGE,
            )
            (output_directory("B1") / "summary.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
    except Exception as error:
        mark_invalid(game, str(error))
        raise
    (output_directory(game) / INVALID_MARKER).unlink(missing_ok=True)
    return result


def packed_utf8(character: str) -> int:
    encoded = character.encode("utf-8")[::-1]
    if len(encoded) > 4:
        raise ValueError(f"U+{ord(character):04X} does not fit the font key")
    return int.from_bytes(encoded.ljust(4, b"\0"), "little")


def unpacked_character(key: int) -> str | None:
    encoded = key.to_bytes(4, "little").rstrip(b"\0")[::-1]
    try:
        character = encoded.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return character if len(character) == 1 else None


def ink_metrics(image: Image.Image, boxes: list[tuple[float, ...]]) -> dict[str, float]:
    rows = []
    for x, y, width, height, left, top in boxes:
        crop = image.crop((round(x), round(y), round(x + width), round(y + height)))
        bounds = crop.point(lambda value: 255 if value > 127 else 0).getbbox()
        if bounds:
            rows.append((bounds[2] - bounds[0], bounds[3] - bounds[1], left + bounds[0], top + bounds[1]))
    if not rows:
        raise ValueError("Could not determine glyph ink bounds")
    return {
        "width": statistics.median(row[0] for row in rows),
        "height": statistics.median(row[1] for row in rows),
        "left": statistics.median(row[2] for row in rows),
        "top": statistics.median(row[3] for row in rows),
    }


def source_kana_metrics(table: dict, image: Image.Image) -> dict[str, int | dict[str, float]]:
    rows = []
    for index, key in enumerate(table["keys"]):
        character = unpacked_character(key)
        record = RECORD_48.unpack_from(table["raw"], index * RECORD_48.size)
        if character is not None and 0x3041 <= ord(character) <= 0x30FF and record[4] > 10 and record[5] > 10:
            rows.append(record)
    if not rows:
        raise ValueError("Could not determine original kana glyph dimensions")
    metrics = {
        "sample_count": len(rows),
        "width": round(statistics.median(row[4] for row in rows)),
        "height": round(statistics.median(row[5] for row in rows)),
        "left": round(statistics.median(row[6] for row in rows)),
        "top": round(statistics.median(row[7] for row in rows)),
        "advance": round(statistics.median(row[8] for row in rows)),
    }
    metrics["ink"] = ink_metrics(image, [(row[2], row[3], row[4], row[5], row[6], row[7]) for row in rows])
    return metrics


def output_directory(game: str) -> Path:
    return OUTPUT_ROOT / game


def stage_runtime_fonts(game: str) -> None:
    output = output_directory(game)
    runtime = RUNTIME_ROOT / game
    font_runtime = runtime / "Font"
    if game == "B1":
        assets = [(output / "mainfonthd720_0.g1t", "MainFontHD720_0.g1t")]
        tables = [output / "ko-glyph-table-0x1c.bin"]
    else:
        assets = [
            (output / f"{specification['name']}.g1t", f"MainFont{slot[-2:]}_HD1080_0.g1t")
            for slot, specification in GAME_CONFIGS[game]["slots"].items()
        ]
        tables = [output / f"{slot}-glyph-table-0x48.bin" for slot in GAME_CONFIGS[game]["slots"]]
    missing = [path for path, _name in assets if not path.is_file() or path.stat().st_size == 0]
    missing += [path for path in tables if not path.is_file() or path.stat().st_size == 0]
    if missing:
        raise RuntimeError(f"{game} runtime font assets are missing or empty: {missing[0]}")

    font_runtime.mkdir(parents=True, exist_ok=True)
    for source, name in assets:
        shutil.copy2(source, font_runtime / name)
    for source in tables:
        shutil.copy2(source, font_runtime / source.name)

    extra_manifest: list[str] = []
    if game == "B1":
        menu_font = output / "menu-g1n/default.g1n"
        if not menu_font.is_file() or menu_font.stat().st_size == 0:
            raise RuntimeError("B1 menu font is missing or empty")
        layout_runtime = runtime / "Layout"
        layout_runtime.mkdir(parents=True, exist_ok=True)
        shutil.copy2(menu_font, layout_runtime / "default.g1n")
        extra_manifest.append("Layout/default.g1n")

    manifest = runtime / "manifest.txt"
    current = []
    if manifest.is_file():
        current = [
            line.strip()
            for line in manifest.read_text(encoding="utf-8-sig").splitlines()
            if line.strip()
            and not line.lstrip().startswith("#")
        ]
    current += [f"Font/{name}" for _source, name in assets] + extra_manifest
    manifest.write_text(
        "\n".join(sorted(set(current), key=str.casefold)) + "\n", encoding="utf-8"
    )


def source_font_root(game: str) -> Path:
    return SOURCE_ROOT / game / "x64/res_jp/font"


def load_original_tables(game: str) -> tuple[bytes, dict[str, dict]]:
    config = GAME_CONFIGS[game]
    dll_path = ROOT / config["dll"]
    dll = dll_path.read_bytes()
    tables: dict[str, dict] = {}
    for slot, specification in config["slots"].items():
        record_size = specification.get("record_size", RECORD_48.size)
        start = specification["offset"]
        end = start + specification["count"] * record_size
        raw = dll[start:end]
        if len(raw) != specification["count"] * record_size:
            raise ValueError(f"{game} {slot} table is truncated")
        keys = [struct.unpack_from("<I", raw, index)[0] for index in range(0, len(raw), record_size)]
        characters = {character for key in keys if (character := unpacked_character(key)) is not None}
        hangul = {character for character in characters if 0xAC00 <= ord(character) <= 0xD7A3}
        tables[slot] = {
            "raw": raw,
            "keys": keys,
            "hangul": hangul,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "strictly_increasing": all(left < right for left, right in zip(keys, keys[1:])),
        }
    return dll, tables


def write_missing_corpus(game: str, text_path: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    characters = output / "characters.txt"
    characters.write_bytes(b"")
    manifest = {
        "status": "translation_tree_missing",
        "text_root": project_path(text_path),
        "json_file_count": 0,
        "json_snapshot_sha256": None,
        "message_snapshot_sha256": None,
        "message_count": 0,
        "field_name": "msg_string",
        "field_kind": "decoded JSON string literal",
        "source_kind": GAME_CONFIGS[game]["source_kind"],
        "target_language": TARGET_LANGUAGE,
        "unique_characters": 0,
        "total_occurrences": 0,
        "unique_visible_characters": 0,
        "total_visible_occurrences": 0,
        "characters_file": characters.name,
        "characters_sha256": file_sha256(characters),
    }
    (output / "corpus.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def prepare_game(game: str) -> dict:
    config = GAME_CONFIGS[game]
    font = FONT
    output = output_directory(game)
    corpus = collect_game_hangul(game)
    corpus_manifest = write_corpus_artifacts(corpus, None, output)
    forced_replacements = [
        character for character in corpus.characters
        if 0xAC00 <= ord(character) <= 0xD7A3
    ]

    dll, tables = load_original_tables(game)
    aliases: dict[str, str] = {}
    added_symbols: list[str] = []
    font_sizes = {
        specification.get("render_size", specification["size"])
        for specification in config["slots"].values()
    }
    font_faces = [ImageFont.truetype(str(font), size) for size in sorted(font_sizes)]
    family, style = font_faces[0].getname()
    font_name = " ".join(part for part in (family, style) if part).strip() or font.stem

    def font_supports(character: str) -> bool:
        rendered = GLYPH_SUBSTITUTIONS.get(character, character)
        return all(inspect_glyphs(face, [rendered])["passed"] for face in font_faces)

    original_keys = [set(table["keys"]) for table in tables.values()]
    for character in corpus.visible_characters:
        if character in forced_replacements or all(packed_utf8(character) in keys for keys in original_keys):
            continue
        if font_supports(character):
            added_symbols.append(character)
            continue
        alias = GLYPH_ALIASES.get(character)
        if alias is not None and all(packed_utf8(alias) in keys for keys in original_keys):
            aliases[character] = alias
        elif alias is not None and font_supports(alias):
            aliases[character] = alias
            added_symbols.append(alias)
        else:
            added_symbols.append(character)

    # Existing symbols stay original. Korean syllables keep the proven same-key
    # replacement policy; every other printable character is generated only when
    # at least one original slot lacks it.
    added = sorted(set(forced_replacements) | set(added_symbols))
    added_path = output / "added-characters.txt"
    added_path.write_text("".join(added) + ("\n" if added else ""), encoding="utf-8")

    shared_source_validation = None
    if game == "B0":
        _shared_dll, shared_tables = load_original_tables("B4")
        slot_identity = {
            slot: table["raw"] == shared_tables[slot]["raw"]
            for slot, table in tables.items()
        }
        if not all(slot_identity.values()):
            raise ValueError("B0 and B4 original dynamic-font tables no longer match")
        shared_source_validation = {
            "b0_and_b4_original_tables_byte_identical": slot_identity,
            "passed": True,
        }
    validations = {}
    unsupported: set[str] = set()
    rendered_added = sorted({GLYPH_SUBSTITUTIONS.get(character, character) for character in added})
    for size in sorted(font_sizes):
        result = inspect_glyphs(ImageFont.truetype(str(font), size), rendered_added)
        validations[str(size)] = result
        unsupported.update(result["unsupported_characters"])

    table_coverage = {}
    for slot, table in tables.items():
        same_key = sorted(set(forced_replacements) & table["hangul"])
        table_coverage[slot] = {
            "original_record_count": config["slots"][slot]["count"],
            "original_table_sha256": table["sha256"],
            "original_keys_strictly_increasing": table["strictly_increasing"],
            "original_hangul_count": len(table["hangul"]),
            "existing_coverage_reused": 0,
            "existing_same_key_overridden": len(same_key),
            "new_required": len(added),
            "compatible_alias_records": len(set(aliases.values())),
            "uncovered": len(unsupported),
        }

    readiness = {
        "schema_version": 1,
        "game": game,
        "status": (
            "translation_tree_missing" if corpus is None else
            "no_current_glyphs" if not added else
            "font_has_unsupported_glyphs" if unsupported else
            "ready"
        ),
        "policy": "replace same-key Korean syllables for compatibility; preserve original glyphs and generate other printable translation characters only when an original slot lacks them",
        "corpus": corpus_manifest,
        "coverage": {
            "characters_count": len(corpus.characters),
            "forced_korean_replacements": len(forced_replacements),
            "visible_characters_count": len(corpus.visible_characters),
            "added_characters_count": len(added),
            "characters_equal_added_characters": corpus.characters == added,
            "added_symbol_codepoints": [f"U+{ord(character):04X}" for character in added_symbols],
            "aliased_symbols": {
                f"U+{ord(source):04X}": f"U+{ord(target):04X}" for source, target in aliases.items()
            },
            "drawn_symbol_substitutions": {
                f"U+{ord(source):04X}": f"U+{ord(target):04X}"
                for source, target in GLYPH_SUBSTITUTIONS.items() if source in added
            },
            "uncovered": len(unsupported),
            "uncovered_codepoints": [f"U+{ord(character):04X}" for character in sorted(unsupported)],
            "unsupported_font_glyphs": len(unsupported),
            "unsupported_font_codepoints": [
                f"U+{ord(character):04X}" for character in sorted(unsupported)
            ],
            "tables": table_coverage,
        },
        "font": {
            "name": font_name,
            "path": project_path(font),
            "sha256": file_sha256(font),
            "validations_by_pixel_size": validations,
        },
        "dll": {
            "path": config["dll"],
            "sha256": hashlib.sha256(dll).hexdigest(),
        },
        "added_characters": project_path(added_path),
        "added_characters_sha256": file_sha256(added_path),
    }
    if "shared_source_game" in config:
        readiness["shared_source_game"] = config["shared_source_game"]
    if "shared_source_reason" in config:
        readiness["shared_source_reason"] = config["shared_source_reason"]
    if "shared_source_evidence" in config:
        readiness["shared_source_evidence"] = config["shared_source_evidence"]
    if shared_source_validation is not None:
        readiness["shared_source_validation"] = shared_source_validation
    readiness_path = output / "font-readiness.json"
    readiness_path.write_text(json.dumps(readiness, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "config": config,
        "output": output,
        "corpus": corpus,
        "characters": added,
        "added": added,
        "aliases": aliases,
        "unsupported": sorted(unsupported),
        "tables": tables,
        "readiness": readiness,
        "readiness_path": readiness_path,
        "font": font,
    }


def run_gust_g1t(*arguments: Path | str) -> str:
    command = [str(GUST_G1T), *(str(argument) for argument in arguments)]
    completed = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if completed.returncode:
        raise RuntimeError(f"gust_g1t failed ({completed.returncode}):\n{completed.stdout}")
    return completed.stdout


def type72_dds_info(raw: bytes) -> dict:
    if raw[:4] != b"DDS " or raw[84:88] != b"DX10" or struct.unpack_from("<I", raw, 128)[0] != 98:
        raise ValueError("expected a gust_tools type-0x72 DDS wrapper")
    height, width = struct.unpack_from("<II", raw, 12)
    mipmaps = struct.unpack_from("<I", raw, 28)[0]
    expected = DDS_DX10_HEADER_SIZE + width * height
    if mipmaps != 1 or len(raw) != expected:
        raise ValueError(f"unexpected type-0x72 layout: {width}x{height}, mips={mipmaps}, bytes={len(raw)}")
    return {"width": width, "height": height, "payload_offset": DDS_DX10_HEADER_SIZE}


def type72_image(raw: bytes) -> Image.Image:
    info = type72_dds_info(raw)
    return Image.frombytes(
        "L",
        (info["width"], info["height"]),
        raw[info["payload_offset"] :],
    )


def generate_sdf(
    characters: list[str],
    size: int,
    work: Path,
    maximum_size: tuple[int, int],
    font: Path,
) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    stem = f"font-{size}"
    charset_path = work / f"{stem}-charset.txt"
    png_path = work / f"{stem}-sdf.png"
    layout_path = work / f"{stem}-sdf.json"
    charset_path.write_text(", ".join(f"0x{ord(character):04X}" for character in characters) + "\n", encoding="utf-8")
    common = [
        str(MSDFGEN), "-font", str(font), "-charset", str(charset_path), "-type", "sdf",
        "-size", str(size), "-pxrange", str(SDF_PIXEL_RANGE), "-yorigin", "top", "-seed", "0", "-threads", "0",
        "-imageout", str(png_path), "-json", str(layout_path),
    ]
    packing = {"mode": "power-of-two rectangle"}
    subprocess.run(common + ["-potr"], check=True)
    with Image.open(png_path) as opened:
        initial_size = opened.size
    if initial_size[0] > maximum_size[0] or initial_size[1] > maximum_size[1]:
        fallback_height = 1 << int(math.log2(maximum_size[1]))
        subprocess.run(
            common + ["-dimensions", str(maximum_size[0]), str(fallback_height)],
            check=True,
        )
        packing = {
            "mode": "fixed dimensions after power-of-two rectangle exceeded safe rows",
            "initial_dimensions": list(initial_size),
            "fixed_dimensions": [maximum_size[0], fallback_height],
        }
    with Image.open(png_path) as opened:
        final_size = opened.size
        image_mode = opened.mode
    if final_size[0] > maximum_size[0] or final_size[1] > maximum_size[1]:
        raise ValueError(f"SDF atlas {final_size} exceeds safe area {maximum_size}")
    if image_mode != "L":
        raise ValueError(f"SDF atlas must be an 8-bit grayscale image, got {image_mode}")
    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    expected = {ord(character) for character in characters}
    actual_sequence = [glyph["unicode"] for glyph in layout["glyphs"]]
    duplicate_codepoints = sorted(
        codepoint for codepoint, count in Counter(actual_sequence).items() if count > 1
    )
    if duplicate_codepoints:
        raise ValueError(
            "SDF layout contains duplicate glyphs: "
            + ", ".join(f"U+{codepoint:04X}" for codepoint in duplicate_codepoints)
        )
    actual = set(actual_sequence)
    if actual != expected:
        missing = sorted(expected - actual)
        raise ValueError(f"SDF layout is missing: {[f'U+{codepoint:04X}' for codepoint in missing]}")
    return {
        "layout": layout,
        "png": png_path,
        "json": layout_path,
        "charset": charset_path,
        "packing": packing,
    }


def verify_empty_region(source_dds: Path, safe_y: int) -> dict:
    image = type72_image(source_dds.read_bytes())
    region = image.crop((0, safe_y, image.width, image.height))
    extrema = region.getextrema()
    if extrema != (0, 0):
        raise ValueError(f"{source_dds} is not empty from y={safe_y}: grayscale extrema={extrema}")
    return {"x": 0, "y": safe_y, "width": image.width, "height": image.height - safe_y, "grayscale_extrema": extrema}


def sdf_stats(path: Path) -> dict:
    with Image.open(path) as opened:
        image = opened.convert("L")
        raw = image.tobytes()
    return {
        "mode": image.mode,
        "extrema": list(image.getextrema()),
        "distinct_values": len(set(raw)),
        "single_channel_raster": True,
    }


def patch_type72(source_raw: bytes, tile_path: Path, destination_y: int) -> tuple[bytes, dict]:
    source = type72_dds_info(source_raw)
    with Image.open(tile_path) as opened:
        tile_image = opened.convert("L")
    tile = {"width": tile_image.width, "height": tile_image.height}
    if tile["width"] > source["width"] or destination_y + tile["height"] > source["height"]:
        raise ValueError("SDF tile does not fit the verified safe region")
    patched = bytearray(source_raw)
    tile_raw = tile_image.tobytes()
    changed_pixels = 0
    for row in range(tile["height"]):
        destination_start = source["payload_offset"] + (destination_y + row) * source["width"]
        tile_start = row * tile["width"]
        old_row = source_raw[destination_start : destination_start + tile["width"]]
        new_row = tile_raw[tile_start : tile_start + tile["width"]]
        changed_pixels += sum(old != new for old, new in zip(old_row, new_row))
        patched[destination_start : destination_start + tile["width"]] = new_row
    patched_bytes = bytes(patched)

    return patched_bytes, {
        "selected_pixel_count": tile["width"] * tile["height"],
        "changed_pixel_count": changed_pixels,
        "rect": [0, destination_y, tile["width"], tile["height"]],
        "source": source,
        "tile": tile,
    }


def verify_patch_bytes(source: bytes, patched: bytes, rect: list[int]) -> bool:
    info = type72_dds_info(source)
    if len(source) != len(patched) or source[:DDS_DX10_HEADER_SIZE] != patched[:DDS_DX10_HEADER_SIZE]:
        return False
    x, y, width, height = rect
    for row in range(info["height"]):
        start = info["payload_offset"] + row * info["width"]
        if y <= row < y + height:
            if source[start : start + x] != patched[start : start + x]:
                return False
            if source[start + x + width : start + info["width"]] != patched[start + x + width : start + info["width"]]:
                return False
        elif source[start : start + info["width"]] != patched[start : start + info["width"]]:
            return False
    return True


def verify_decoded_glyphs(
    path: Path,
    records: list[dict],
    expected_rect: list[int] | None = None,
    type72: bool = False,
    allowed_duplicate_codepoints: frozenset[str] = frozenset(),
) -> dict:
    if type72:
        image = type72_image(path.read_bytes())
    else:
        with Image.open(path) as opened:
            image = opened.convert("L")
    outside: list[str] = []
    outside_expected_rect: list[str] = []
    empty: list[str] = []
    signatures: dict[bytes, list[str]] = {}
    for record in records:
        x, y = record["x"], record["y"]
        right = x + record["width"]
        bottom = y + record["height"]
        codepoint = f"U+{ord(record['character']):04X}"
        if x < 0 or y < 0 or right > image.width or bottom > image.height:
            outside.append(codepoint)
            continue
        if expected_rect is not None:
            expected_x, expected_y, expected_width, expected_height = expected_rect
            if (
                x < expected_x or y < expected_y
                or right > expected_x + expected_width
                or bottom > expected_y + expected_height
            ):
                outside_expected_rect.append(codepoint)
        crop = image.crop((x, y, right, bottom))
        if crop.getbbox() is None:
            empty.append(codepoint)
        signature = hashlib.sha256(
            struct.pack("<HH", crop.width, crop.height) + crop.tobytes()
        ).digest()
        signatures.setdefault(signature, []).append(codepoint)
    duplicate_crops = [
        codepoints for codepoints in signatures.values()
        if len(codepoints) > 1 and not set(codepoints).issubset(allowed_duplicate_codepoints)
    ]
    if outside or outside_expected_rect or empty or duplicate_crops:
        raise ValueError(
            f"glyph validation failed for {path}: outside_atlas={outside}, "
            f"outside_expected_rect={outside_expected_rect}, empty={empty}, "
            f"duplicate_crops={duplicate_crops}"
        )
    return {
        "checked_count": len(records),
        "channel": "L",
        "outside_atlas": 0,
        "outside_expected_rect": 0,
        "empty_pixels": 0,
        "duplicate_crop_groups": 0,
        "distinct_crop_sha256_count": len(signatures),
        "passed": True,
    }


def pack_and_roundtrip_g1t(asset_directory: Path, destination: Path) -> dict:
    # gust_g1t 1.58 uses a 256-byte internal output-path buffer. Copy inputs to
    # the platform temp directory so arbitrary project/output paths never reach it.
    with tempfile.TemporaryDirectory(prefix="brq-g1t-") as temporary:
        temporary_path = Path(temporary)
        packed_input = temporary_path / asset_directory.name
        shutil.copytree(asset_directory, packed_input)
        if len(str(packed_input).encode("utf-8")) > 220:
            raise ValueError("temporary G1T path is too long for gust_g1t")
        run_gust_g1t("-y", packed_input)
        packed = temporary_path / f"{asset_directory.name}.g1t"
        if not packed.is_file():
            raise ValueError(f"gust_g1t did not create {packed.name}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(packed, destination)

        verify_root = temporary_path / "verify"
        verify_root.mkdir()
        verify_g1t = verify_root / "asset.g1t"
        shutil.copy2(packed, verify_g1t)
        run_gust_g1t("-y", verify_g1t)
        roundtrip_dds = verify_root / "asset/000.dds"
        source_dds = asset_directory / "000.dds"
        if not roundtrip_dds.is_file() or roundtrip_dds.read_bytes() != source_dds.read_bytes():
            raise ValueError("G1T roundtrip DDS differs from the generated DDS")
    return {
        "path": project_path(destination),
        "sha256": file_sha256(destination),
        "roundtrip_dds_byte_identical": True,
    }


def current_corpus_hashes(game: str) -> tuple[str | None, str | None]:
    if not (TRANSLATIONS / f"{game}.json").is_file():
        return None, None
    corpus = collect_game_hangul(game)
    return corpus.snapshot_sha256, corpus.message_snapshot_sha256


def verify_summary_sources(game: str, summary: dict) -> None:
    if game == "B1":
        records = summary["source_files_unchanged_through_g1t_pack"]
    else:
        records = summary["source_font_files"] | {"dll": summary["source_dll"]}
    changed = []
    for name, record in records.items():
        path = Path(record["path"])
        if not path.is_absolute():
            path = ROOT / path
        if not path.is_file() or file_sha256(path) != record["sha256_after"]:
            changed.append(name)
    if changed:
        raise RuntimeError(f"{game} source files changed before the all-game build finished: {changed}")


def verify_file_hash(path: Path, expected: str, label: str) -> None:
    if not path.is_file() or file_sha256(path) != expected:
        raise RuntimeError(f"{label} file or SHA-256 differs: {path}")


def verify_extension_table(path: Path, record_size: int, label: str) -> int:
    raw = path.read_bytes()
    if not raw or len(raw) % record_size:
        raise RuntimeError(f"{label} extension table is empty or misaligned")
    keys = [struct.unpack_from("<I", raw, offset)[0] for offset in range(0, len(raw), record_size)]
    if any(left >= right for left, right in zip(keys, keys[1:])):
        raise RuntimeError(f"{label} extension keys are not strictly increasing")
    return len(keys)


def verify_generated_game(game: str) -> dict:
    output = output_directory(game)
    if (output / INVALID_MARKER).exists():
        raise RuntimeError(f"{game} output is marked invalid")
    summary_path = output / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("font", {}).get("sha256") != file_sha256(FONT):
        raise RuntimeError(f"{game} summary does not use the selected font")
    expected = summary["inputs" if game == "B1" else "corpus"]
    current_json, current_messages = current_corpus_hashes(game)
    if (
        expected.get("json_snapshot_sha256") != current_json
        or expected.get("message_snapshot_sha256") != current_messages
    ):
        raise RuntimeError(f"{game} output does not match the selected translation table")
    verify_summary_sources(game, summary)

    runtime_font = RUNTIME_ROOT / game / "Font"
    checked_files = 0
    checked_records = 0
    manifest_names = []
    if game == "B1":
        g1t_hash = summary["runtime_assets"]["g1t"]["sha256"]
        verify_file_hash(output / "mainfonthd720_0.g1t", g1t_hash, "B1 generated G1T")
        verify_file_hash(runtime_font / "MainFontHD720_0.g1t", g1t_hash, "B1 runtime G1T")
        table_hash = summary["glyphs"]["extension_table_sha256"]
        table = output / "ko-glyph-table-0x1c.bin"
        verify_file_hash(table, table_hash, "B1 generated glyph table")
        verify_file_hash(runtime_font / table.name, table_hash, "B1 runtime glyph table")
        checked_records += verify_extension_table(table, 0x1C, "B1")

        menu_summary = summary["menu_font"]
        menu_output = output / "menu-g1n/default.g1n"
        menu_runtime = RUNTIME_ROOT / "B1/Layout/default.g1n"
        verify_file_hash(menu_output, menu_summary["output_sha256"], "B1 generated menu font")
        verify_file_hash(menu_runtime, menu_summary["output_sha256"], "B1 runtime menu font")
        original = (SOURCE_ROOT / build_b1_menu_font.SOURCE_RELATIVE).read_bytes()
        corpus = build_b1_menu_font.collect_corpus(TRANSLATIONS, TARGET_LANGUAGE)
        characters, aliases = build_b1_menu_font.required_characters(original, corpus.characters, FONT)
        build_b1_menu_font.check(
            original, menu_output.read_bytes(), characters, aliases, (FONT, FONT)
        )
        manifest_names = ["Font/MainFontHD720_0.g1t", "Layout/default.g1n"]
        checked_files = 6
    else:
        for slot, specification in GAME_CONFIGS[game]["slots"].items():
            slot_summary = summary["slots"][slot]
            g1t_name = f"{specification['name']}.g1t"
            runtime_name = f"MainFont{slot[-2:]}_HD1080_0.g1t"
            verify_file_hash(output / g1t_name, slot_summary["g1t"]["sha256"], f"{game} {slot} generated G1T")
            verify_file_hash(runtime_font / runtime_name, slot_summary["g1t"]["sha256"], f"{game} {slot} runtime G1T")
            table = output / f"{slot}-glyph-table-0x48.bin"
            verify_file_hash(table, slot_summary["extension_table_sha256"], f"{game} {slot} generated glyph table")
            verify_file_hash(runtime_font / table.name, slot_summary["extension_table_sha256"], f"{game} {slot} runtime glyph table")
            checked_records += verify_extension_table(table, RECORD_48.size, f"{game} {slot}")
            manifest_names.append(f"Font/{runtime_name}")
            checked_files += 4

    manifest = (RUNTIME_ROOT / game / "manifest.txt").read_text(encoding="utf-8-sig").splitlines()
    for name in manifest_names:
        if manifest.count(name) != 1:
            raise RuntimeError(f"{game} runtime manifest does not contain exactly one {name}")
    return {
        "game": game,
        "font_sha256": file_sha256(FONT),
        "corpus_sha256": current_json,
        "message_sha256": current_messages,
        "checked_files": checked_files,
        "extension_records": checked_records,
        "passed": True,
    }


def build_b1() -> dict:
    source_directory = SOURCE_ROOT / "B1/x64/font/mainfonthd720_0"
    source_paths = {
        "dll": ROOT / "B1.dll",
        "dds": source_directory / "000.dds",
        "g1t_json": source_directory / "g1t.json",
        "g1t": source_directory.parent / "mainfonthd720_0.g1t",
    }
    source_hashes_before = {name: file_sha256(path) for name, path in source_paths.items()}
    prepared = prepare_game("B1")
    if prepared["unsupported"]:
        raise ValueError(f"B1: unsupported corpus codepoints: {prepared['readiness']['coverage']['uncovered_codepoints']}")
    build_b1_corpus_font.build(
        argparse.Namespace(
            game_root=ROOT,
            dll=ROOT / "B1.dll",
            text_root=None,
            translation_table=TRANSLATIONS / "B1.json",
            source_dir=source_directory,
            font=prepared["font"],
            characters_file=prepared["output"] / "added-characters.txt",
            output=prepared["output"],
            atlas_width=2048,
            atlas_height=2048,
            target_language=TARGET_LANGUAGE,
        )
    )
    output = output_directory("B1")
    legacy_combined = output / "combined-glyph-table-0x1c.bin"
    if legacy_combined.exists():
        rejected = output / "rejected-combined-tables"
        rejected.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy_combined), rejected / legacy_combined.name)
    runtime_g1t_path = output / "mainfonthd720_0.g1t"
    if prepared["characters"]:
        runtime_g1t = pack_and_roundtrip_g1t(output / "mainfonthd720_0", runtime_g1t_path)
    else:
        shutil.copy2(source_paths["g1t"], runtime_g1t_path)
        runtime_g1t = {
            "path": project_path(runtime_g1t_path),
            "sha256": file_sha256(runtime_g1t_path),
            "source_and_output_byte_identical": (
                file_sha256(runtime_g1t_path) == source_hashes_before["g1t"]
            ),
            "roundtrip_dds_byte_identical": None,
        }
    summary_path = output / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if (
        summary["inputs"]["json_snapshot_sha256"] != prepared["readiness"]["corpus"]["json_snapshot_sha256"]
        or summary["inputs"]["message_snapshot_sha256"] != prepared["readiness"]["corpus"]["message_snapshot_sha256"]
    ):
        raise RuntimeError("B1 translation files changed between readiness and atlas generation; rerun the command")
    current_json, current_messages = current_corpus_hashes("B1")
    if (
        current_json != summary["inputs"]["json_snapshot_sha256"]
        or current_messages != summary["inputs"]["message_snapshot_sha256"]
    ):
        raise RuntimeError("B1 translation files changed while font assets were being built; rerun the command")
    source_hashes_after = {name: file_sha256(path) for name, path in source_paths.items()}
    source_files_unchanged = {
        name: source_hashes_before[name] == source_hashes_after[name]
        for name in source_paths
    }
    if not all(source_files_unchanged.values()):
        changed = [name for name, unchanged in source_files_unchanged.items() if not unchanged]
        raise RuntimeError(f"B1 source files changed while packing G1T: {changed}")
    summary["generator"] = "tools/fonts/build_font_assets.py"
    summary["font"] = prepared["readiness"]["font"]
    summary["coverage"] = prepared["readiness"]["coverage"]
    summary["toolchain"] = {
        "gust_g1t": {"path": project_path(GUST_G1T), "sha256": file_sha256(GUST_G1T)},
    }
    summary["runtime_assets"] = {
        "texture_dds": summary["atlas"]["dds"],
        "texture_dds_sha256": summary["atlas"]["dds_sha256"],
        "g1t": runtime_g1t,
        "installer_or_game_file": False,
    }
    summary["source_files_unchanged_through_g1t_pack"] = {
        name: {
            "path": project_path(source_paths[name]),
            "sha256_before": source_hashes_before[name],
            "sha256_after": source_hashes_after[name],
            "unchanged": source_files_unchanged[name],
        }
        for name in source_paths
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def build_48(game: str) -> dict:
    config = GAME_CONFIGS[game]
    dll_path = ROOT / config["dll"]
    dll_before = file_sha256(dll_path)
    source_game = config["shared_source_game"]
    source_root = source_font_root(source_game)
    source_paths = {
        f"{slot}.dds": source_root / specification["name"] / "000.dds"
        for slot, specification in config["slots"].items()
    }
    source_paths.update({
        f"{slot}.g1t_json": source_root / specification["name"] / "g1t.json"
        for slot, specification in config["slots"].items()
    })
    source_paths.update({
        f"{slot}.g1t": source_root / f"{specification['name']}.g1t"
        for slot, specification in config["slots"].items()
    })
    source_hashes_before = {name: file_sha256(path) for name, path in source_paths.items()}
    prepared = prepare_game(game)
    output = prepared["output"]
    characters = prepared["characters"]
    if prepared["unsupported"]:
        failure = prepared["readiness"] | {
            "status": "failed_before_atlas_generation",
            "reason": (
                f"{prepared['readiness']['font']['name']} has blank, .notdef, or duplicate rasters "
                "for listed corpus codepoints"
            ),
        }
        (output / "summary.json").write_text(json.dumps(failure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        raise ValueError(f"{game}: unsupported corpus codepoints: {failure['coverage']['uncovered_codepoints']}")

    sdf_work = output / "sdf-work"
    for stale_work in (output / "msdf-work", sdf_work):
        if stale_work.exists():
            shutil.rmtree(stale_work)
    layouts = {}
    if characters:
        rendered_characters = sorted(
            {GLYPH_SUBSTITUTIONS.get(character, character) for character in characters},
            key=packed_utf8,
        )
        layout_specs = sorted({
            (specification["size"], specification["render_size"])
            for specification in config["slots"].values()
        })
        for size, render_size in layout_specs:
            slots_at_size = [
                specification
                for specification in config["slots"].values()
                if (specification["size"], specification["render_size"]) == (size, render_size)
            ]
            available_height = min(2048 - specification["safe_y"] for specification in slots_at_size)
            layout_key = (size, render_size)
            layouts[layout_key] = generate_sdf(
                rendered_characters,
                render_size,
                sdf_work / f"advance-{size}-render-{render_size}",
                (4096, available_height),
                prepared["font"],
            )
            layouts[layout_key]["advance"] = size
            layouts[layout_key]["render_size"] = render_size

    mapping_by_character = {
        character: {
            "character": character,
            "codepoint": f"U+{ord(character):04X}",
            "utf8_packed": packed_utf8(character),
            "utf8_packed_hex": f"0x{packed_utf8(character):08X}",
            "occurrences": prepared["corpus"].visible_occurrences[character] if prepared["corpus"] else 0,
            "slots": {},
        }
        for character in characters
    }
    slot_results = {}
    for slot, specification in config["slots"].items():
        source_table = prepared["tables"][slot]
        source_directory = source_root / specification["name"]
        source_dds = source_directory / "000.dds"
        source_image = type72_image(source_dds.read_bytes())
        reference_metrics = source_kana_metrics(source_table, source_image)
        source_g1t = source_root / f"{specification['name']}.g1t"
        output_directory = output / specification["name"]
        output_directory.mkdir(parents=True, exist_ok=True)
        output_dds = output_directory / "000.dds"
        output_g1t = output / source_g1t.name
        shutil.copy2(source_directory / "g1t.json", output_directory / "g1t.json")
        safe_region = verify_empty_region(source_dds, specification["safe_y"])

        patch_result = None
        if characters:
            tile = layouts[(specification["size"], specification["render_size"])]
            source_raw = source_dds.read_bytes()
            patched, patch_result = patch_type72(
                source_raw, tile["png"], specification["safe_y"]
            )
            output_dds.write_bytes(patched)
            if not verify_patch_bytes(source_raw, patched, patch_result["rect"]):
                raise ValueError(f"{game} {slot}: bytes outside the selected SDF rectangle changed")
            patch_result["bytes_outside_rect_identical"] = True
            g1t_result = pack_and_roundtrip_g1t(output_directory, output_g1t)
        else:
            shutil.copy2(source_dds, output_dds)
            shutil.copy2(source_g1t, output_g1t)
            g1t_result = {
                "path": project_path(output_g1t),
                "sha256": file_sha256(output_g1t),
                "source_and_output_byte_identical": file_sha256(output_g1t) == file_sha256(source_g1t),
                "roundtrip_dds_byte_identical": None,
            }

        layout_key = (specification["size"], specification["render_size"])
        layout_item = layouts.get(layout_key, {})
        layout = layout_item.get("layout")
        render_size = layout_item.get("render_size", specification["size"])
        glyphs = {glyph["unicode"]: glyph for glyph in layout["glyphs"]} if layout else {}
        for character in characters:
            rendered_character = GLYPH_SUBSTITUTIONS.get(character, character)
            glyph = glyphs[ord(rendered_character)]
            atlas = glyph["atlasBounds"]
            plane = glyph["planeBounds"]
            metrics = layout["metrics"]
            x = math.floor(atlas["left"])
            y = specification["safe_y"] + math.floor(atlas["top"])
            width = round(atlas["right"] - atlas["left"])
            height = round(atlas["bottom"] - atlas["top"])
            if 0xAC00 <= ord(character) <= 0xD7A3:
                left = reference_metrics["left"] - (SDF_PIXEL_RANGE - 1)
                top = reference_metrics["top"] - (SDF_PIXEL_RANGE - 1)
            else:
                left, top = sdf_glyph_offsets(plane, metrics, render_size)
            rendered_advance = max(1, round(glyph["advance"] * render_size))
            if 0xAC00 <= ord(character) <= 0xD7A3:
                advance = specification["size"]
            elif character == "・":
                advance = specification["size"]
                left += round((advance - rendered_advance) / 2)
            else:
                advance = rendered_advance
            mapping_by_character[character]["slots"][slot] = {
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "left": left,
                "top": top,
                "advance": advance,
                "bottom": 0,
            }
        decoded_records = [
            {"character": character, **mapping_by_character[character]["slots"][slot]}
            for character in characters
        ]
        hangul_records = [record for record in decoded_records if 0xAC00 <= ord(record["character"]) <= 0xD7A3]
        height_calibration = None
        if hangul_records:
            with Image.open(layout_item["png"]) as opened:
                generated_ink = ink_metrics(
                    opened.convert("L"),
                    [
                        (
                            record["x"], record["y"] - specification["safe_y"],
                            record["width"], record["height"], record["left"], record["top"],
                        )
                        for record in hangul_records
                    ],
                )
            left_adjust = round(reference_metrics["ink"]["left"] - generated_ink["left"])
            top_adjust = round(reference_metrics["ink"]["top"] - generated_ink["top"])
            fitted_advance = max(
                1,
                round(
                    generated_ink["width"]
                    + reference_metrics["advance"]
                    - reference_metrics["ink"]["width"]
                ),
            )
            for record in hangul_records:
                values = mapping_by_character[record["character"]]["slots"][slot]
                values["left"] += left_adjust
                values["top"] += top_adjust
                values["advance"] = fitted_advance
            decoded_records = [
                {"character": character, **mapping_by_character[character]["slots"][slot]}
                for character in characters
            ]
            hangul_records = [
                record for record in decoded_records if 0xAC00 <= ord(record["character"]) <= 0xD7A3
            ]
            with Image.open(layout_item["png"]) as opened:
                generated_ink = ink_metrics(
                    opened.convert("L"),
                    [
                        (
                            record["x"], record["y"] - specification["safe_y"],
                            record["width"], record["height"], record["left"], record["top"],
                        )
                        for record in hangul_records
                    ],
                )
            height_calibration = {
                "render_size": render_size,
                "reference": reference_metrics,
                "generated_ink": generated_ink,
                "left_adjustment": left_adjust,
                "top_adjustment": top_adjust,
                "advance_adjustment": fitted_advance - reference_metrics["advance"],
                "generated_median_width": statistics.median(record["width"] for record in hangul_records),
                "generated_median_height": statistics.median(record["height"] for record in hangul_records),
                "generated_median_left": statistics.median(record["left"] for record in hangul_records),
                "generated_median_top": statistics.median(record["top"] for record in hangul_records),
                "generated_median_advance": statistics.median(record["advance"] for record in hangul_records),
            }
            if any(abs(generated_ink[name] - reference_metrics["ink"][name]) > 2 for name in generated_ink):
                raise ValueError(f"{game} {slot}: Replacement glyph ink bounds or position differ from the original reference")
        dot_image = type72_image(output_dds.read_bytes())
        center_y = reference_metrics["ink"]["top"] + reference_metrics["ink"]["height"] / 2
        for character in "·・":
            if character not in mapping_by_character:
                continue
            values = mapping_by_character[character]["slots"][slot]
            dot_record = {"character": character, **values}
            center_middle_dot(dot_record, dot_image, center_y)
            values["left"], values["top"] = dot_record["left"], dot_record["top"]
        records = []
        for character in characters:
            values = mapping_by_character[character]["slots"][slot]
            packed_values = tuple(
                float(values[name])
                for name in ("x", "y", "width", "height", "left", "top", "advance", "bottom")
            )
            records.append((packed_utf8(character), RECORD_48.pack(packed_utf8(character), 0, *packed_values)))
        source_indexes = {key: index for index, key in enumerate(source_table["keys"])}
        for target in sorted(set(prepared["aliases"].values()), key=packed_utf8):
            key = packed_utf8(target)
            index = source_indexes.get(key)
            if index is None:
                continue
            start = index * RECORD_48.size
            records.append((key, source_table["raw"][start:start + RECORD_48.size]))
        compatibility_record = None
        if not records:
            key = packed_utf8("?")
            index = source_indexes.get(key)
            if index is None:
                raise ValueError(f"{game} {slot}: Original glyph table has no ? record for an empty extension table")
            start = index * RECORD_48.size
            records.append((key, source_table["raw"][start:start + RECORD_48.size]))
            compatibility_record = "U+003F copied byte-for-byte from the original table"
        records.sort(key=lambda item: item[0])
        if any(left[0] >= right[0] for left, right in zip(records, records[1:])):
            raise ValueError(f"{game} {slot}: extension keys are not strictly increasing")
        expected_rect = patch_result["rect"] if patch_result else [0, specification["safe_y"], 0, 0]
        decoded_glyph_validation = verify_decoded_glyphs(
            output_dds, decoded_records, expected_rect, type72=True,
            allowed_duplicate_codepoints=frozenset({"U+00B7", "U+30FB"}),
        )
        if characters:
            tile = layouts[layout_key]
            with Image.open(tile["png"]) as tile_image:
                tile_size = tile_image.size
            uncompressed_glyph_validation = verify_decoded_glyphs(
                tile["png"],
                [
                    {**record, "y": record["y"] - specification["safe_y"]}
                    for record in decoded_records
                ],
                [0, 0, tile_size[0], tile_size[1]],
                allowed_duplicate_codepoints=frozenset({"U+00B7", "U+30FB"}),
            )
        else:
            uncompressed_glyph_validation = {
                "checked_count": 0,
                "channel": "RGB",
                "outside_atlas": 0,
                "outside_expected_rect": 0,
                "empty_pixels": 0,
                "duplicate_crop_groups": 0,
                "distinct_crop_sha256_count": 0,
                "passed": True,
            }
        extension_path = output / f"{slot}-glyph-table-0x48.bin"
        extension_path.write_bytes(b"".join(raw for _key, raw in records))
        original_keys = set(prepared["tables"][slot]["keys"])
        overlaps = original_keys & {key for key, _raw in records}
        slot_results[slot] = {
            "record_count": len(records),
            "compatibility_record": compatibility_record,
            "record_size": RECORD_48.size,
            "extension_table": project_path(extension_path),
            "extension_table_sha256": file_sha256(extension_path),
            "extension_keys_strictly_increasing": True,
            "same_keys_overridden_from_original": len(overlaps),
            "original_table_preserved_separately": True,
            "source_dds": project_path(source_dds),
            "source_dds_sha256": file_sha256(source_dds),
            "output_dds": project_path(output_dds),
            "output_dds_sha256": file_sha256(output_dds),
            "source_and_output_dds_identical": file_sha256(source_dds) == file_sha256(output_dds),
            "safe_region": safe_region,
            "patch": patch_result,
            "uncompressed_glyph_validation": uncompressed_glyph_validation,
            "decoded_glyph_validation": decoded_glyph_validation,
            "font_metric_calibration": height_calibration,
            "g1t": g1t_result,
            "runtime_dimensions": {
                "old": [4096, 2048],
                "new": [4096, 2048],
                "changed": False,
                "descriptor_rva": f"0x{specification['descriptor_rva']:X}",
                "expansion_rule": (
                    "Keep 4096x2048 when the SDF fits. If a future supported corpus requires 4096px height, "
                    "preserve original rows at the same coordinates and have the ASI update this descriptor height."
                ),
            },
        }

    records_json = sorted(mapping_by_character.values(), key=lambda record: record["utf8_packed"])
    mapped_characters = [record["character"] for record in records_json]
    missing_mapping = sorted(set(characters) - set(mapped_characters))
    extra_mapping = sorted(set(mapped_characters) - set(characters))
    duplicate_mapping = len(mapped_characters) - len(set(mapped_characters))
    if missing_mapping or extra_mapping or duplicate_mapping:
        raise ValueError(f"{game}: mapping does not exactly cover the corpus")
    mapping = {
        "schema_version": 1,
        "record_layout": "<II8d (0x48 bytes)",
        "record_semantics": {
            "coordinates": "x/y/width/height are pixel-valued float64 fields at +0x08/+0x10/+0x18/+0x20",
            "layout_metrics": "left/top/advance are float64 fields at +0x28/+0x30/+0x38; +0x40 is not read on the checked path",
            "static_evidence": {
                "B2": "renderer around RVA 0x569911 reads the descriptor and divides x/y/w/h by width/height",
                "B3": (
                    "renderer RVA 0x56C2F0 reads x/y/w/h and divides by descriptor dimensions; "
                    "RVA [0x57114C, 0x5711B6) scales left/top/width/height/advance for the quad and cursor"
                ),
                "B4": (
                    "UV function RVA 0x571FC0 (field reads 0x5720AB..0x5720FC); "
                    "quad metric reads RVA [0x576E1C, 0x576E86)"
                ),
                "B0": "same descriptor and 0x48 layout; behavior is a static structural inference",
            },
        },
        "lookup_policy": "replace same-key Korean syllables; otherwise keep exact original glyphs and use this extension only when missing",
        "records": records_json,
    }
    mapping_path = output / "mapping.json"
    mapping_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    mapping_csv = output / "mapping.csv"
    with mapping_csv.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["character", "codepoint", "utf8_packed_hex", "occurrences", "slot", "x", "y", "width", "height", "left", "top", "advance", "bottom"])
        for record in records_json:
            for slot, values in record["slots"].items():
                writer.writerow([
                    record["character"], record["codepoint"], record["utf8_packed_hex"], record["occurrences"], slot,
                    values["x"], values["y"], values["width"], values["height"], values["left"], values["top"], values["advance"], values["bottom"],
                ])

    main_preview_source = output / config["slots"]["main00"]["name"] / "000.dds"
    main_image = type72_image(main_preview_source.read_bytes())
    preview = main_image.convert("RGB")
    if preview.width > 2048:
        preview = preview.resize((preview.width // 2, preview.height // 2), Image.Resampling.LANCZOS)
    preview_path = output / "atlas-preview-main00-rgb.png"
    preview.save(preview_path, optimize=True)
    region_path = output / "korean-region-preview-main00-rgb.png"
    main_specification = config["slots"]["main00"]
    if characters:
        with Image.open(layouts[(main_specification["size"], main_specification["render_size"])]["png"]) as tile_image:
            tile_size = tile_image.size
        region = main_image.crop((0, main_specification["safe_y"], tile_size[0], main_specification["safe_y"] + tile_size[1]))
    else:
        region = main_image.crop((0, main_specification["safe_y"], 1024, min(main_image.height, main_specification["safe_y"] + 512)))
    region.save(region_path, optimize=True)

    source_hashes_after = {name: file_sha256(path) for name, path in source_paths.items()}
    source_files_unchanged = {
        name: source_hashes_before[name] == source_hashes_after[name]
        for name in source_paths
    }
    if not all(source_files_unchanged.values()):
        changed = [name for name, unchanged in source_files_unchanged.items() if not unchanged]
        raise RuntimeError(f"{game} source font files changed during build: {changed}")
    dll_after = file_sha256(dll_path)
    dll_unchanged = dll_before == dll_after
    if not dll_unchanged:
        raise RuntimeError(f"{game} DLL changed during font generation")

    summary = {
        "schema_version": 1,
        "game": game,
        "status": "generated" if characters else (
            "source-identical assets; translation inputs need no added characters" if prepared["corpus"] else
            "source-identical assets; translation tree is not present yet"
        ),
        "policy": "same-key Korean syllables and otherwise missing printable characters use the selected font; exact original glyphs remain unchanged",
        "generator": "tools/fonts/build_font_assets.py",
        "corpus": prepared["readiness"]["corpus"],
        "coverage": prepared["readiness"]["coverage"],
        "font": prepared["readiness"]["font"],
        "toolchain": {
            "msdf_atlas_gen": {
                "path": project_path(MSDFGEN),
                "sha256": file_sha256(MSDFGEN),
            },
            "gust_g1t": {"path": project_path(GUST_G1T), "sha256": file_sha256(GUST_G1T)},
        },
        "sdf_generation": {
            f"advance-{size[0]}-render-{size[1]}": {
                "advance": item["advance"],
                "render_size": item["render_size"],
                "pixel_range": SDF_PIXEL_RANGE,
                "packing": item["packing"],
                "png": project_path(item["png"]),
                "png_sha256": file_sha256(item["png"]),
                "raster_validation": sdf_stats(item["png"]),
                "layout_json": project_path(item["json"]),
                "layout_json_sha256": file_sha256(item["json"]),
            }
            for size, item in layouts.items()
        },
        "record_layout": "<II8d (0x48 bytes)",
        "lookup": {
            "function_rva": config["lookup_rva"],
            "required_behavior": "replace same-key Korean syllables; otherwise keep the exact original key, then try the generated and compatible fallbacks",
            "compatible_original_symbols": prepared["readiness"]["coverage"]["aliased_symbols"],
            "runtime_verified": False,
        },
        "shared_source_game": source_game,
        "shared_source_reason": config.get("shared_source_reason"),
        "shared_source_evidence": config.get("shared_source_evidence"),
        "shared_source_validation": prepared["readiness"].get("shared_source_validation"),
        "slots": slot_results,
        "mapping": {
            "json": project_path(mapping_path),
            "json_sha256": file_sha256(mapping_path),
            "csv": project_path(mapping_csv),
            "csv_sha256": file_sha256(mapping_csv),
            "validation": {
                "expected_characters": len(characters),
                "mapped_characters": len(mapped_characters),
                "missing": len(missing_mapping),
                "extra": len(extra_mapping),
                "duplicates": duplicate_mapping,
                "passed": True,
            },
        },
        "atlas_preview": {
            "full_main00": {"path": project_path(preview_path), "sha256": file_sha256(preview_path)},
            "korean_region_main00": {"path": project_path(region_path), "sha256": file_sha256(region_path)},
        },
        "runtime_assets_are_installer_or_original_game_files": False,
        "source_dll_unchanged_during_build": dll_unchanged,
        "source_dll": {
            "path": project_path(dll_path),
            "sha256_before": dll_before,
            "sha256_after": dll_after,
            "unchanged_during_build": dll_unchanged,
        },
        "source_font_files": {
            name: {
                "path": project_path(source_paths[name]),
                "sha256_before": source_hashes_before[name],
                "sha256_after": source_hashes_after[name],
                "unchanged_during_build": source_files_unchanged[name],
            }
            for name in source_paths
        },
        "limitations": [
            "G1T type 0x72 stores an uncompressed 8-bit SDF; gust_tools only labels its extracted DDS wrapper as BC7.",
            "The SDF command uses source-calibrated render sizes, pxrange 4, top y-origin, and seed 0.",
            "The 0x48 field use is statically confirmed, but a newly selected font still requires an in-game visual check.",
            (
                "B2/B3/B4 code confirms UV division uses a font descriptor; B0 follows the same layout by static "
                "structure comparison. Any future height expansion requires an ASI descriptor patch and an in-game check."
            ),
        ],
    }
    current_json, current_messages = current_corpus_hashes(game)
    expected_json = prepared["readiness"]["corpus"]["json_snapshot_sha256"]
    expected_messages = prepared["readiness"]["corpus"]["message_snapshot_sha256"]
    if current_json != expected_json or current_messages != expected_messages:
        raise RuntimeError(f"{game} translation files changed while font assets were being built; rerun the command")
    summary_path = output / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def configure(args: argparse.Namespace) -> None:
    global ROOT, SOURCE_ROOT, TRANSLATIONS, FONT
    global MSDFGEN, GUST_G1T, OUTPUT_ROOT, RUNTIME_ROOT, TARGET_LANGUAGE

    ROOT = args.game_root.resolve()
    SOURCE_ROOT = args.source_root.resolve()
    TRANSLATIONS = args.translations_root.resolve()
    FONT = args.font.resolve()
    MSDFGEN = args.msdf_atlas_gen.resolve()
    GUST_G1T = args.gust_g1t.resolve()
    OUTPUT_ROOT = args.output_root.resolve()
    RUNTIME_ROOT = args.runtime_root.resolve()
    TARGET_LANGUAGE = args.target_language

    required_directories = (("game root", ROOT), ("translations root", TRANSLATIONS))
    required_files = (("font", FONT), ("msdf-atlas-gen", MSDFGEN), ("gust_g1t", GUST_G1T))
    for label, path in required_directories:
        if not path.is_dir():
            raise ValueError(f"{label} directory does not exist: {path}")
    for label, path in required_files:
        if not path.is_file():
            raise ValueError(f"{label} file does not exist: {path}")
    if OUTPUT_ROOT == ROOT or RUNTIME_ROOT == ROOT:
        raise ValueError("output and runtime roots must not replace the game root")
    if OUTPUT_ROOT == RUNTIME_ROOT:
        raise ValueError("output and runtime roots must be separate directories")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate game font assets required by the translations.")
    parser.add_argument("--game-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--translations-root", type=Path, required=True)
    parser.add_argument("--font", type=Path, required=True)
    parser.add_argument("--msdf-atlas-gen", type=Path, required=True)
    parser.add_argument("--gust-g1t", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--target-language", required=True)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--game", choices=tuple(GAME_CONFIGS))
    target.add_argument("--all", action="store_true", help="Generate font assets for all titles from B0 through B4.")
    parser.add_argument("--manifest-only", action="store_true", help="Update only the corpus and preparation status without generating fonts.")
    parser.add_argument("--check-only", action="store_true", help="Check only existing output and runtime copies.")
    args = parser.parse_args()
    configure(args)

    if args.manifest_only and args.check_only:
        parser.error("--manifest-only and --check-only cannot be used together")
    if args.check_only:
        games = GAME_CONFIGS if args.all else (args.game,)
        print(json.dumps({game: verify_generated_game(game) for game in games}, ensure_ascii=False, indent=2))
        return

    if args.all:
        results = {}
        for game in GAME_CONFIGS:
            results[game] = (
                prepare_game(game)["readiness"] if args.manifest_only else
                build_game(game)
            )
        if not args.manifest_only:
            for game, result in results.items():
                expected = result["inputs"] if game == "B1" else result["corpus"]
                try:
                    current_json, current_messages = current_corpus_hashes(game)
                    if (
                        current_json != expected["json_snapshot_sha256"]
                        or current_messages != expected["message_snapshot_sha256"]
                    ):
                        raise RuntimeError(
                            f"{game} translation files changed before the all-game build finished; rerun --all"
                        )
                    verify_summary_sources(game, result)
                except Exception as error:
                    mark_invalid(game, str(error))
                    raise
            for game in results:
                stage_runtime_fonts(game)
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return
    if args.manifest_only:
        print(json.dumps(prepare_game(args.game)["readiness"], ensure_ascii=False, indent=2))
        return
    result = build_game(args.game)
    stage_runtime_fonts(args.game)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
