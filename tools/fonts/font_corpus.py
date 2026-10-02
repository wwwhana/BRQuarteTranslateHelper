#!/usr/bin/env python3
"""Collect translated display characters and validate source-font rasters."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from PIL import ImageFont


try:
    from ..translation_table import parse as parse_translation_table
except ImportError:  # Direct execution: python tools/fonts/font_corpus.py
    import sys

    PROJECT_TOOLS = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(PROJECT_TOOLS))
    try:
        from translation_table import parse as parse_translation_table
    finally:
        sys.path.pop(0)


@dataclass(frozen=True)
class FontCorpus:
    characters: list[str]
    occurrences: Counter
    visible_characters: list[str]
    visible_occurrences: Counter
    files: list[Path]
    snapshot_sha256: str | None
    message_snapshot_sha256: str | None
    message_count: int
    field_name: str
    field_kind: str
    source_kind: str
    target_language: str


# Keep this token expression identical to tools/check_translations.py.
MSG_STRING = re.compile(r'("msg_string"\s*:\s*)("(?:\\.|[^"\\])*")')
XML_DECLARATION = re.compile(r"^\ufeff?<\?xml[^?]*\?>\s*")
IGNORED_MESSAGES = {"徃纃宁"}
UNUSED_MESSAGE = re.compile(r"^\[\[[^\[\]]+\]\]$")


def collect_characters(
    text_root: Path | None,
    *,
    translation_tables: tuple[Path, ...] = (),
    source_kind: str = "gust_tools JSON-like document (hex numeric literals permitted)",
    target_language: str | None = None,
) -> FontCorpus:
    """Collect printable Unicode scalars from translated text and table values."""
    resolved_input = text_root.resolve() if text_root is not None else None
    if resolved_input is not None and not resolved_input.exists():
        raise ValueError(f"translation input does not exist: {text_root}")
    resolved_root = (
        resolved_input.parent if resolved_input is not None and resolved_input.is_file() else resolved_input
    )
    text_files = (
        []
        if resolved_input is None
        else [resolved_input]
        if resolved_input.is_file()
        else sorted([*resolved_input.rglob("*.json"), *resolved_input.rglob("*.xml")])
    )
    resolved_tables = [path.resolve() for path in translation_tables]
    missing_tables = [path for path in resolved_tables if not path.is_file()]
    if missing_tables:
        raise ValueError(f"translation table does not exist: {missing_tables[0]}")
    inputs = [
        (path, path.relative_to(resolved_root), False)
        for path in text_files
    ] + [
        (path, Path("translations") / path.name, True)
        for path in resolved_tables
    ]
    files = [path for path, _relative, _is_table in inputs]
    if not inputs:
        raise ValueError("at least one translated text or translation table is required")
    xml_input = (
        resolved_input is not None and resolved_input.is_file()
        and resolved_input.suffix.lower() == ".xml"
    )
    mixed_input = (
        resolved_input is not None and not resolved_input.is_file()
        and any(path.suffix.lower() == ".xml" for path in text_files)
    )

    occurrences: Counter = Counter()
    visible_occurrences: Counter = Counter()
    snapshot = hashlib.sha256()
    message_snapshot = hashlib.sha256()
    message_count = 0
    for path, relative_path, is_table in inputs:
        raw = path.read_bytes()
        relative = relative_path.as_posix().encode("utf-8")
        snapshot.update(struct.pack("<I", len(relative)))
        snapshot.update(relative)
        snapshot.update(hashlib.sha256(raw).digest())
        if is_table:
            table = parse_translation_table(
                raw.decode("utf-8-sig")
            )
            messages = [table[key] for key in sorted(table)]
        elif xml_input:
            try:
                root = ET.fromstring(raw)
                messages = [element.attrib["Text"] for element in root if element.tag == "str"]
            except (ET.ParseError, KeyError) as error:
                raise ValueError(f"invalid B0 string XML: {path.name}: {error}") from error
            if root.tag != "Root" or len(messages) != len(root):
                raise ValueError(f"unexpected B0 string XML structure: {path.name}")
        elif path.suffix.lower() == ".xml":
            errors = []
            for encoding in ("utf-8-sig", "cp932"):
                try:
                    root = ET.fromstring(XML_DECLARATION.sub("", raw.decode(encoding), count=1))
                    break
                except (UnicodeDecodeError, ET.ParseError) as error:
                    errors.append(error)
            else:
                raise ValueError(
                    f"invalid translation XML: {path.relative_to(resolved_root)}: {errors[-1]}"
                ) from errors[-1]
            fields = ("JP",) if path.name.casefold() == "eventglossarykeyword.xml" else ("Text", "text")
            messages = [element.attrib[field] for element in root.iter() for field in fields if field in element.attrib]
        else:
            messages = []
            for field_index, match in enumerate(MSG_STRING.finditer(raw.decode("utf-8"))):
                try:
                    messages.append(json.loads(match.group(2)))
                except json.JSONDecodeError as error:
                    raise ValueError(
                        f"invalid msg_string JSON literal in {path.relative_to(resolved_root)} "
                        f"at field {field_index + 1}: {error}"
                    ) from error
        for field_index, message in enumerate(messages):
            message_count += 1
            encoded = message.encode("utf-8")
            message_snapshot.update(struct.pack("<I", len(relative)))
            message_snapshot.update(relative)
            message_snapshot.update(struct.pack("<II", field_index, len(encoded)))
            message_snapshot.update(encoded)
            if message not in IGNORED_MESSAGES and not UNUSED_MESSAGE.fullmatch(message):
                visible_occurrences.update(character for character in message if character.isprintable())
            occurrences.update(character for character in message if character.isprintable())

    field_name = (
        "Text" if xml_input else
        "msg_string, Text, text, JP" if mixed_input else
        "msg_string" if text_files else
        "translation values"
    )
    field_kind = (
        "decoded XML attribute" if xml_input else
        "decoded JSON string literal and XML attributes" if mixed_input else
        "decoded JSON string literal" if text_files else
        "decoded translation table values"
    )
    if resolved_tables:
        if text_files:
            field_name += ", translation values"
            field_kind += " and decoded translation table values"
        source_kind += f"; published exact-source translation table values ({target_language})"

    return FontCorpus(
        characters=sorted(occurrences),
        occurrences=occurrences,
        visible_characters=sorted(visible_occurrences),
        visible_occurrences=visible_occurrences,
        files=files,
        snapshot_sha256=snapshot.hexdigest(),
        message_snapshot_sha256=message_snapshot.hexdigest(),
        message_count=message_count,
        field_name=field_name,
        field_kind=field_kind,
        source_kind=source_kind,
        target_language=target_language,
    )


def raster_signature(mask) -> bytes:
    width, height = mask.size
    return hashlib.sha256(struct.pack("<HH", width, height) + bytes(mask)).digest()


def codepoint_list(codepoints: list[int]) -> str:
    return ", ".join(f"U+{codepoint:04X}" for codepoint in codepoints)


def write_corpus_artifacts(corpus: FontCorpus, text_root: Path | None, output: Path) -> dict:
    """Write ``characters.txt`` and its machine-readable provenance manifest."""
    output.mkdir(parents=True, exist_ok=True)
    characters_path = output / "characters.txt"
    characters_path.write_text("".join(corpus.characters) + ("\n" if corpus.characters else ""), encoding="utf-8")
    result = {
        "text_root": str(text_root) if text_root is not None else None,
        "json_file_count": len(corpus.files),
        "json_snapshot_sha256": corpus.snapshot_sha256,
        "message_snapshot_sha256": corpus.message_snapshot_sha256,
        "message_count": corpus.message_count,
        "field_name": corpus.field_name,
        "field_kind": corpus.field_kind,
        "source_kind": corpus.source_kind,
        "target_language": corpus.target_language,
        "unique_characters": len(corpus.characters),
        "total_occurrences": sum(corpus.occurrences.values()),
        "unique_visible_characters": len(corpus.visible_characters),
        "total_visible_occurrences": sum(corpus.visible_occurrences.values()),
        "characters_file": characters_path.name,
        "characters_sha256": hashlib.sha256(characters_path.read_bytes()).hexdigest(),
    }
    (output / "corpus.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def inspect_glyphs(font: ImageFont.FreeTypeFont, characters: list[str]) -> dict:
    """Return exact blank, .notdef, and duplicate-raster diagnostics."""
    notdef_signature = raster_signature(font.getmask(chr(0x0378)))
    blank: list[str] = []
    notdef: list[str] = []
    signatures: dict[bytes, list[int]] = {}
    for character in characters:
        codepoint = ord(character)
        mask = font.getmask(character)
        signature = raster_signature(mask)
        signatures.setdefault(signature, []).append(codepoint)
        if mask.getbbox() is None:
            blank.append(character)
        if signature == notdef_signature:
            notdef.append(character)
    duplicate_rasters = [[chr(codepoint) for codepoint in group] for group in signatures.values() if len(group) > 1]
    unsupported = sorted(set(blank) | set(notdef) | {character for group in duplicate_rasters for character in group})
    return {
        "checked_count": len(characters),
        "empty_rasters": len(blank),
        "empty_codepoints": [f"U+{ord(character):04X}" for character in blank],
        "notdef_raster_matches": len(notdef),
        "notdef_codepoints": [f"U+{ord(character):04X}" for character in notdef],
        "duplicate_raster_groups": len(duplicate_rasters),
        "duplicate_raster_codepoints": [
            [f"U+{ord(character):04X}" for character in group] for group in duplicate_rasters
        ],
        "distinct_raster_sha256_count": len(signatures),
        "unsupported_characters": unsupported,
        "unsupported_codepoints": [f"U+{ord(character):04X}" for character in unsupported],
        "passed": not unsupported,
    }


def validate_glyphs(font: ImageFont.FreeTypeFont, characters: list[str]) -> dict:
    """Reject blank, .notdef, and duplicate rasters with exact codepoints."""
    result = inspect_glyphs(font, characters)
    if not result["passed"]:
        raise ValueError(
            "font glyph validation failed; "
            f"blank=[{', '.join(result['empty_codepoints'])}]; "
            f"notdef=[{', '.join(result['notdef_codepoints'])}]; "
            "same_raster_groups=["
            + "; ".join(", ".join(group) for group in result["duplicate_raster_codepoints"])
            + "]"
        )
    return result


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="font-corpus-test-") as directory:
        workspace = Path(directory)
        root = workspace / "text"
        (root / "text_jp").mkdir(parents=True)
        (root / "event").mkdir()
        (root / "dialogue.json").write_text(
            json.dumps({"msg_string": "ABA·♡\n", "ignored": "Z"}, ensure_ascii=False), encoding="utf-8"
        )
        (root / "text_jp" / "strings.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?><Root><str Text="BC" Other="Z"/><str Text="[[不使用]]"/></Root>',
            encoding="utf-8",
        )
        (root / "text_jp" / "ui.xml").write_text(
            '<Root><entry text="EF" Other="Z"/></Root>', encoding="utf-8"
        )
        (root / "event" / "eventglossarykeyword.xml").write_text(
            '<Root><eventGlossaryKeyword JP="DE" EN="Z"/></Root>', encoding="utf-8"
        )
        (root / "text_jp" / "sentinel.xml").write_text(
            '<Root><str Text="徃纃宁"/></Root>', encoding="utf-8"
        )
        (root / "text_jp" / "shift-jis.xml").write_bytes(
            '<?xml version="1.0" encoding="SHIFT-JIS"?><Root><str Text="ダメージ"/></Root>'.encode(
                "cp932"
            )
        )

        translation_table = workspace / "B1.json"
        translation_table.write_text(
            json.dumps({"追加": "H"}, ensure_ascii=False), encoding="utf-8"
        )
        corpus = collect_characters(root, translation_tables=(translation_table,))
        assert set("ABCDEFH·♡ダメージ") <= set(corpus.characters)
        assert corpus.occurrences["A"] == 2 and corpus.occurrences["H"] == 1
        assert corpus.visible_occurrences["·"] == 1 and corpus.visible_occurrences["♡"] == 1
        assert "\n" not in corpus.visible_characters and not (set("徃纃宁") & set(corpus.visible_characters))
        assert not (set("不使用") & set(corpus.visible_characters))
        assert len(corpus.files) == 7 and corpus.message_count == 8
        assert corpus.field_name == "msg_string, Text, text, JP, translation values"
        translation_table.write_text(
            json.dumps({"追加": "I"}, ensure_ascii=False), encoding="utf-8"
        )
        changed = collect_characters(root, translation_tables=(translation_table,))
        assert "I" in changed.characters and "H" not in changed.characters
        assert changed.snapshot_sha256 != corpus.snapshot_sha256
        assert changed.message_snapshot_sha256 != corpus.message_snapshot_sha256

        single_xml = root / "b0.xml"
        single_xml.write_text('<Root><str Text="FG"/></Root>', encoding="utf-8")
        assert collect_characters(single_xml).characters == list("FG")
    print("self-test passed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--text-root", type=Path)
    parser.add_argument("--translation-table", action="append", default=[], type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if args.output is None or (args.text_root is None and not args.translation_table):
        parser.error("--output and at least one input are required unless --self-test is used")
    result = write_corpus_artifacts(
        collect_characters(args.text_root, translation_tables=tuple(args.translation_table)),
        args.text_root,
        args.output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()


# Transitional aliases keep the existing generators and downstream checks usable.
HangulCorpus = FontCorpus
collect_hangul = collect_characters
