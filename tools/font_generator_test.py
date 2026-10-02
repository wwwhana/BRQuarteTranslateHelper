#!/usr/bin/env python3
"""Small platform-neutral checks for the public font generator."""

from __future__ import annotations

import json
import struct
import tempfile
from pathlib import Path

from fonts import build_font_assets as assets
from fonts.font_corpus import collect_characters


def check_table_only_corpus(work: Path) -> None:
    english = work / "B1-en.json"
    english.write_text(
        json.dumps({"値%s<CR>": "English %s<CR>"}, ensure_ascii=False), encoding="utf-8"
    )
    corpus = collect_characters(
        None,
        translation_tables=(english,),
        source_kind="test translation table",
        target_language="en",
    )
    assert not any(0xAC00 <= ord(character) <= 0xD7A3 for character in corpus.characters)
    assert set("English") <= set(corpus.characters)
    assert corpus.message_count == 1 and corpus.target_language == "en"

    chinese = work / "B1-zh.json"
    chinese.write_text(json.dumps({"開始": "开始"}, ensure_ascii=False), encoding="utf-8")
    corpus = collect_characters(
        None,
        translation_tables=(chinese,),
        source_kind="test translation table",
        target_language="zh",
    )
    assert corpus.characters == sorted("开始")
    assert not any(0xAC00 <= ord(character) <= 0xD7A3 for character in corpus.characters)


def check_unicode_keys() -> None:
    for character in ("A", "€", "😀"):
        assert assets.unpacked_character(assets.packed_utf8(character)) == character


def check_nonempty_extension(work: Path) -> None:
    table = work / "glyph-table.bin"
    table.write_bytes(struct.pack("<IHHHHiiii", assets.packed_utf8("?"), 0, 0, 1, 1, 0, 0, 1, 0))
    assert assets.verify_extension_table(table, 0x1C, "test") == 1
    table.write_bytes(b"")
    try:
        assets.verify_extension_table(table, 0x1C, "test")
    except RuntimeError:
        pass
    else:
        raise AssertionError("Empty extension glyph table was accepted")


def check_native_argv() -> None:
    original_tool = assets.GUST_G1T
    original_run = assets.subprocess.run
    captured = {}

    class Result:
        returncode = 0
        stdout = "ok"

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Result()

    try:
        assets.GUST_G1T = Path("C:/Tools With Space/gust_g1t.exe")
        assets.subprocess.run = fake_run
        assets.run_gust_g1t("-y", Path("C:/Build With Space/font"))
    finally:
        assets.GUST_G1T = original_tool
        assets.subprocess.run = original_run
    assert captured["command"] == [
        "C:/Tools With Space/gust_g1t.exe", "-y", "C:/Build With Space/font"
    ]
    assert "env" not in captured["kwargs"]


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="brq-font-generator-test-") as directory:
        work = Path(directory)
        check_table_only_corpus(work)
        check_nonempty_extension(work)
    check_unicode_keys()
    check_native_argv()
    print("font generator self-test passed")


if __name__ == "__main__":
    main()
