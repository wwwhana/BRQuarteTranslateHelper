#!/usr/bin/env python3
"""Focused checks for graphics template restoration and safe extraction."""
from __future__ import annotations

import json
import os
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from PIL import Image

TOOLS = Path(__file__).resolve().parents[1] / "tools"
PROJECT = TOOLS.parent
GAME = PROJECT.parent
BUILD = PROJECT / "build"
sys.path.insert(0, str(TOOLS))

import prepare_graphics as graphics_stage  # noqa: E402

from graphics_templates import (  # noqa: E402
    MARKER,
    catalog,
    difference_mask,
    encode_restoration,
    restore,
    staged_output,
)


def expect_value_error(action) -> None:
    try:
        action()
    except ValueError:
        return
    raise AssertionError("invalid graphics-template input was accepted")


def test_restoration_roundtrip() -> None:
    original = Image.new("RGBA", (4, 3), (20, 30, 40, 255))
    original.putpixel((3, 2), (200, 100, 50, 0))
    clean = original.copy()
    clean.putpixel((1, 1), (0, 0, 0, 0))  # erases an opaque source pixel
    clean.putpixel((2, 0), (220, 210, 200, 255))

    changed = difference_mask(original, clean)
    assert changed.getpixel((1, 1)) == 255
    assert changed.getpixel((2, 0)) == 255
    assert changed.getpixel((3, 2)) == 0  # invisible RGB is intentionally ignored

    record = encode_restoration(original, clean)
    restored, patch, visibility = restore(original, record)
    assert restored.tobytes() == clean.tobytes()
    assert list(record["rect"]) == [1, 0, 3, 2]
    assert patch.size == original.size
    assert patch.getpixel((1, 1)) == clean.getpixel((1, 1))
    assert visibility.getpixel((1, 1)) == 0
    assert visibility.getpixel((0, 0)) == 255

    transparent_clean = original.copy()
    transparent_clean.putpixel((3, 2), (0, 0, 0, 0))
    assert difference_mask(original, transparent_clean).getpixel((3, 2)) == 0
    transparent_record = encode_restoration(original, transparent_clean)
    transparent_restored, transparent_patch, _ = restore(original, transparent_record)
    assert transparent_restored.tobytes() == transparent_clean.tobytes()
    assert list(transparent_record["rect"]) == [3, 2, 4, 3]
    assert transparent_patch.getpixel((3, 2)) == (0, 0, 0, 0)


def test_catalog_and_output_guards() -> None:
    with tempfile.TemporaryDirectory(prefix="brq-graphics-test-") as temporary:
        root = Path(temporary)
        malformed = root / "malformed.json"
        malformed.write_text("{", encoding="utf-8")
        expect_value_error(lambda: catalog(malformed))
        malformed.write_text(json.dumps({"version": 1, "templates": {}}), encoding="utf-8")
        expect_value_error(lambda: catalog(malformed))
        malformed.write_text(json.dumps([]), encoding="utf-8")
        expect_value_error(lambda: catalog(malformed))
        valid_record = {
            "id": "B1/test_image",
            "segment": "B1",
            "pak": "PACK00_01.PAK",
            "member": "data/b1/x64/res_jp/ipu/test_image.g1t",
            "texture": "000.dds",
            "source_sha256": "0" * 64,
            "size": [1, 1],
            "rect": [0, 0, 1, 1],
        }
        malformed.write_text(
            json.dumps({"version": 1, "templates": [
                {**valid_record, "rect": [0.0, 0, 1, 1]},
            ]}),
            encoding="utf-8",
        )
        expect_value_error(lambda: catalog(malformed))

        inputs = root / "inputs"
        inputs.mkdir()
        existing = root / "existing"
        existing.mkdir()
        sentinel = existing / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")
        (existing / MARKER).write_text("marker", encoding="utf-8")

        def overwrite() -> None:
            with staged_output(existing, (inputs,), update=False):
                pass

        expect_value_error(overwrite)
        assert sentinel.read_text(encoding="utf-8") == "keep"

        def overlap() -> None:
            with staged_output(inputs / "child", (inputs,), update=False):
                pass

        expect_value_error(overlap)

        if hasattr(os, "symlink"):
            link = root / "link"
            try:
                link.symlink_to(inputs, target_is_directory=True)
            except OSError:
                pass
            else:
                def symlink_output() -> None:
                    with staged_output(link / "child", (root,), update=False):
                        pass

                expect_value_error(symlink_output)


def psd_dimensions(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:4] == b"8BPS"
    return int.from_bytes(data[18:22], "big"), int.from_bytes(data[14:18], "big")


def _tiny_edit(path: Path) -> None:
    with Image.open(path) as opened:
        image = opened.convert("RGBA")
    changed = False
    for y in range(image.height):
        for x in range(image.width):
            red, green, blue, alpha = image.getpixel((x, y))
            if alpha:
                image.putpixel((x, y), ((red + 127) % 256, green, blue, 255))
                changed = True
                break
        if changed:
            break
    if not changed:
        image.putpixel((0, 0), (255, 0, 255, 255))
    image.save(path)


def test_actual_game(game_root: Path) -> None:
    node = shutil.which("node")
    gust = game_root / "tmp" / "gust_tools-src" / "gust_g1t"
    data_root = game_root / "Data"
    assert BUILD.is_dir() and node is not None and gust.is_file() and data_root.is_dir(), (
        "--actual requires the private game data, gust_g1t, node, and project build directory"
    )

    command = [
        sys.executable,
        str(TOOLS / "graphics_templates.py"),
        "extract",
        "--pak-root",
        str(data_root),
        "--output",
        "PLACEHOLDER",
        "--gust-g1t",
        str(gust),
        "--id",
        "B4/seed_select_game_001",
        "--id",
        "B1/timezone_001",
        "--psd",
        "--node",
        node,
    ]
    with tempfile.TemporaryDirectory(prefix="brq-template-extract-", dir=BUILD) as temporary:
        scratch = Path(temporary)
        extracted = scratch / "extracted"
        command[command.index("PLACEHOLDER")] = str(extracted)
        result = subprocess.run(command, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        expected = {
            "B4/seed_select_game_001": (700, 900),
            "B1/timezone_001": (2048, 1024),
        }
        for identity, dimensions in expected.items():
            folder = extracted / identity
            with Image.open(folder / "image.png") as image:
                assert image.size == dimensions
            assert psd_dimensions(folder / "image.psd") == dimensions
            assert (folder / "image.psd").stat().st_size > 128

        edits = scratch / "edits"
        for identity in expected:
            destination = edits / identity
            destination.mkdir(parents=True)
            shutil.copyfile(extracted / identity / "image.png", destination / "image.png")
            _tiny_edit(destination / "image.png")
        (edits / "project.json").write_text(
            json.dumps({"version": 1, "images": list(expected)}, indent=2) + "\n",
            encoding="utf-8",
        )

        applied = scratch / "applied"
        apply_command = [
            sys.executable,
            str(TOOLS / "graphics_templates.py"),
            "apply",
            "--pak-root",
            str(data_root),
            "--output",
            str(applied),
            "--gust-g1t",
            str(gust),
            "--edits",
            str(edits),
            "--id",
            "B4/seed_select_game_001",
            "--id",
            "B1/timezone_001",
        ]
        result = subprocess.run(apply_command, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        graphics = applied / "graphics"
        collected = graphics_stage._collect_graphics(graphics)
        assert {(item.segment, item.game_path) for item in collected} == {
            ("B4", "data/b4/x64/res_cmn/ui/seed_select_game.g1t"),
            ("B0", "data/b4/x64/res_cmn/ui/seed_select_game.g1t"),
            ("B1", "data/b1/x64/res_jp/ipu/timezone_001.g1t"),
        }
        assert not list(applied.rglob("manifest.tsv"))
    print("actual graphics extraction and apply checks passed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--actual",
        action="store_true",
        help="run the opt-in checks against private game data",
    )
    parser.add_argument(
        "--game-root",
        type=Path,
        help="private game root used with --actual (defaults to the sibling game)",
    )
    args = parser.parse_args()
    if args.game_root is not None and not args.actual:
        parser.error("--game-root requires --actual")
    test_restoration_roundtrip()
    test_catalog_and_output_guards()
    if args.actual:
        test_actual_game((args.game_root or GAME).absolute())
    print("graphics template restoration and guard checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
