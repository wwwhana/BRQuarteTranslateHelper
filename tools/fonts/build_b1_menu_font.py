#!/usr/bin/env python3
"""Build B1's separate menu G1N with a selected TrueType font."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

try:
    from .font_corpus import collect_characters, inspect_glyphs, validate_glyphs
except ImportError:  # Direct execution: python tools/fonts/build_b1_menu_font.py
    from font_corpus import collect_characters, inspect_glyphs, validate_glyphs


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_RELATIVE = Path("B1/x64/res_jp/layout/default.g1n")
SOURCE_SHA256 = "028d8b6a0176c16ac70d02e2a85f00ea539919f4f013795cffa1c780972fb59d"
RECORD = struct.Struct("<5B3bI")
CMAP_SIZE = 0x20000
FONT_SIZE = 45
MENU_ALIASES = {
    "➇": "⑧",
    "～": "〜",
    "〜": "～",
    "—": "―",
    "−": "－",
    "ｰ": "ー",
    "〇": "○",
    "◝": "(",
    "◜": ")",
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def collect_corpus(translations_root: Path, target_language: str | None = None):
    return collect_characters(
        None,
        translation_tables=(translations_root / "B1.json",),
        source_kind="public exact-source translation table",
        target_language=target_language,
    )


def u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def faces(data: bytes) -> tuple[list[bytes], bytes]:
    if len(data) < 40 or u32(data, 0) != 0x47314E5F or u32(data, 4) != 0x30303030:
        raise ValueError("Unexpected B1 menu font format.")
    if u32(data, 8) != len(data) or u32(data, 28) != 2:
        raise ValueError("Unexpected B1 menu font size or face count.")
    offsets = [u32(data, 32), u32(data, 36), u32(data, 20)]
    if offsets[0] != 168 or offsets != sorted(offsets) or offsets[-1] > len(data):
        raise ValueError("Invalid B1 menu glyph table offset.")
    result = [data[start:end] for start, end in zip(offsets, offsets[1:])]
    for face in result:
        if len(face) < CMAP_SIZE or (len(face) - CMAP_SIZE) % RECORD.size:
            raise ValueError("Invalid menu glyph record size.")
        count = (len(face) - CMAP_SIZE) // RECORD.size
        if max(struct.unpack_from("<65536H", face)) >= count:
            raise ValueError("Menu glyph ID is outside the glyph table.")
    return result, data[offsets[-1]:]


def forced_replacement(character: str) -> bool:
    return 0xAC00 <= ord(character) <= 0xD7A3


def required_characters(
    original: bytes,
    corpus_characters: list[str],
    font_path: Path,
) -> tuple[list[str], dict[str, str]]:
    tables, _payload = faces(original)
    result = []
    aliases = {}
    font = ImageFont.truetype(font_path, FONT_SIZE)
    for character in corpus_characters:
        codepoint = ord(character)
        if codepoint > 0xFFFF:
            raise ValueError(
                f"B1 menu fonts cannot contain characters outside the BMP: U+{codepoint:06X}"
            )
        missing = any(struct.unpack_from("<H", face, codepoint * 2)[0] == 0 for face in tables)
        if not forced_replacement(character) and not missing:
            continue
        if inspect_glyphs(font, [character])["passed"]:
            result.append(character)
            continue
        alias = MENU_ALIASES.get(character)
        if alias is not None and all(
            struct.unpack_from("<H", face, ord(alias) * 2)[0] != 0 for face in tables
        ):
            aliases[character] = alias
        else:
            result.append(character)
    return result, aliases


def raster(font: ImageFont.FreeTypeFont, character: str) -> bytes:
    box = font.getbbox(character)
    if box[2] - box[0] > 48 or box[3] > 48 or box[1] < 0:
        raise ValueError(f"Glyph exceeds the 48x48 area: U+{ord(character):04X}")
    tile = Image.new("L", (48, 48))
    ImageDraw.Draw(tile).text(
        ((48 - font.getlength(character)) / 2, 0), character, font=font, fill=255
    )
    values = [(value + 8) // 17 for value in tile.tobytes()]
    if not any(values):
        raise ValueError(f"Generated glyph is empty: U+{ord(character):04X}")
    return bytes(
        (values[index] << 4) | values[index + 1]
        for index in range(0, len(values), 2)
    )


def build(
    original: bytes,
    characters: list[str],
    aliases: dict[str, str],
    fonts: tuple[Path, Path],
) -> bytes:
    original_faces, original_payload = faces(original)
    payload = bytearray(original_payload)
    result = bytearray(original[:168])
    for index, (source_face, font_path) in enumerate(zip(original_faces, fonts, strict=True)):
        font = ImageFont.truetype(font_path, FONT_SIZE)
        validate_glyphs(font, characters)
        face = bytearray(source_face)
        for character, alias in aliases.items():
            struct.pack_into(
                "<H", face, ord(character) * 2,
                struct.unpack_from("<H", source_face, ord(alias) * 2)[0],
            )
        for character in characters:
            codepoint = ord(character)
            old_glyph = struct.unpack_from("<H", face, codepoint * 2)[0]
            if old_glyph and not forced_replacement(character):
                continue
            glyph_id = (len(face) - CMAP_SIZE) // RECORD.size
            if glyph_id > 0xFFFF or len(payload) >= 0x10000000:
                raise ValueError("G1N glyph ID or image offset exceeds the limit.")
            struct.pack_into("<H", face, codepoint * 2, glyph_id)
            face.extend(RECORD.pack(48, 48, 0, 43, 48, -24, 0, 48, len(payload)))
            payload.extend(raster(font, character))
        struct.pack_into("<I", result, 32 + index * 4, len(result))
        result.extend(face)
    struct.pack_into("<I", result, 20, len(result))
    result.extend(payload)
    struct.pack_into("<I", result, 8, len(result))
    return bytes(result)


def check(
    original: bytes,
    result: bytes,
    characters: list[str],
    aliases: dict[str, str],
    fonts: tuple[Path, Path],
) -> list[int]:
    old_faces, old_payload = faces(original)
    new_faces, new_payload = faces(result)
    if new_payload[: len(old_payload)] != old_payload:
        raise ValueError("Original B1 menu font image data changed.")
    expected_header = bytearray(original[:168])
    for offset in (8, 20, 32, 36):
        expected_header[offset : offset + 4] = result[offset : offset + 4]
    if result[:168] != expected_header:
        raise ValueError("Original B1 menu font header changed.")

    generated_counts = []
    for index, (old, new) in enumerate(zip(old_faces, new_faces, strict=True)):
        old_count = (len(old) - CMAP_SIZE) // RECORD.size
        if new[CMAP_SIZE : len(old)] != old[CMAP_SIZE:]:
            raise ValueError("Original B1 menu font glyph records changed.")
        expected_map = bytearray(old[:CMAP_SIZE])
        for character, alias in aliases.items():
            struct.pack_into(
                "<H", expected_map, ord(character) * 2,
                struct.unpack_from("<H", old, ord(alias) * 2)[0],
            )
        font = ImageFont.truetype(fonts[index], FONT_SIZE)
        next_id = old_count
        for character in characters:
            codepoint = ord(character)
            old_glyph = struct.unpack_from("<H", old, codepoint * 2)[0]
            if old_glyph and not forced_replacement(character):
                continue
            glyph = struct.unpack_from("<H", new, codepoint * 2)[0]
            if glyph != next_id:
                raise ValueError("B1 menu glyph IDs do not match generation order.")
            next_id += 1
            struct.pack_into("<H", expected_map, codepoint * 2, glyph)
            record = RECORD.unpack_from(new, CMAP_SIZE + glyph * RECORD.size)
            if record[:8] != (48, 48, 0, 43, 48, -24, 0, 48):
                raise ValueError("Unexpected B1 menu glyph dimensions or position.")
            if not len(old_payload) <= record[8] < 0x10000000:
                raise ValueError("B1 menu glyph image offset is out of bounds.")
            if new_payload[record[8] : record[8] + 1152] != raster(font, character):
                raise ValueError("B1 menu glyph image differs from the selected TTF.")
        if new[:CMAP_SIZE] != expected_map or len(new) != CMAP_SIZE + next_id * RECORD.size:
            raise ValueError("B1 menu glyph table differs from the expected records.")
        generated_counts.append(next_id - old_count)
    return generated_counts


def preview(data: bytes, characters: list[str]) -> Image.Image:
    tables, payload = faces(data)
    sample = characters[:8] or ["設", "定"]
    image = Image.new("RGBA", (len(sample) * 48, 120), (24, 24, 24, 255))
    for row, face in enumerate(tables):
        for column, character in enumerate(sample):
            glyph = struct.unpack_from("<H", face, ord(character) * 2)[0]
            width, height, left, top, *_, packed = RECORD.unpack_from(
                face, CMAP_SIZE + glyph * RECORD.size
            )
            if packed >> 28 or width % 2:
                raise ValueError("Unexpected B1 menu preview glyph format.")
            pixels = payload[packed : packed + width * height // 2]
            alpha = bytes(level * 17 for byte in pixels for level in (byte >> 4, byte & 15))
            tile = Image.new("RGBA", (width, height), "white")
            tile.putalpha(Image.frombytes("L", tile.size, alpha))
            image.alpha_composite(tile, (column * 48 + left, row * 60 + 48 - top))
    return image


def generate(
    *,
    game_root: Path,
    source_root: Path,
    translations_root: Path,
    font: Path,
    output: Path,
    target_language: str | None = None,
) -> dict:
    source = source_root / SOURCE_RELATIVE
    original = source.read_bytes()
    source_hash = hashlib.sha256(original).hexdigest()
    if source_hash != SOURCE_SHA256:
        raise ValueError("File differs from the verified original Japanese G1N.")
    corpus = collect_corpus(translations_root, target_language)
    characters, aliases = required_characters(original, corpus.characters, font)
    fonts = (font.resolve(), font.resolve())
    result = build(original, characters, aliases, fonts)
    generated_counts = check(original, result, characters, aliases, fonts)

    output.mkdir(parents=True, exist_ok=True)
    target = output / "default.g1n"
    target.write_bytes(result)
    preview(result, characters).save(output / "preview.png")
    summary = {
        "source": str(source.resolve()),
        "source_sha256": source_hash,
        "output": str(target.resolve()),
        "output_sha256": hashlib.sha256(result).hexdigest(),
        "bytes": len(result),
        "faces": 2,
        "generated_per_face": generated_counts,
        "selected_characters": len(characters),
        "aliases": aliases,
        "target_language": target_language,
        "font": str(font.resolve()),
        "font_sha256": file_sha256(font),
        "original_glyphs_and_pixels_preserved": True,
        "codec": "4bpp, original palette",
        "corpus_sha256": corpus.snapshot_sha256,
        "message_sha256": corpus.message_snapshot_sha256,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a G1N font for B1 menus.")
    parser.add_argument("--game-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--translations-root", type=Path, required=True)
    parser.add_argument("--font", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target-language", required=True)
    args = parser.parse_args()
    print(json.dumps(generate(**vars(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
