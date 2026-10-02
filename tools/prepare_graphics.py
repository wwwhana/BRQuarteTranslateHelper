#!/usr/bin/env python3
"""Validate reviewed G1T graphics and stage them in a build runtime copy."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path

from generate_embedded import (
    SEGMENTS,
    is_graphics_path,
    load_graphics_overrides,
    load_manifests,
    sha256_file,
    validate_g1t,
    valid_path,
)


@dataclass(frozen=True)
class Graphic:
    segment: str
    game_path: str
    source: Path


def _casefold_child(parent: Path, name: str) -> Path | None:
    if not parent.exists():
        return None
    if not parent.is_dir() or parent.is_symlink():
        raise ValueError(f"Graphics destination directory is unsafe: {parent}")
    matches = [child for child in parent.iterdir() if child.name.casefold() == name.casefold()]
    if len(matches) > 1 or (matches and matches[0].name != name):
        raise ValueError(f"Graphics paths differ only in letter case: {parent / name}")
    return matches[0] if matches else None


def _unsupported_path(game_path: str) -> ValueError:
    key = game_path.lower()
    if key.endswith(".png"):
        return ValueError("Automatic PNG conversion is unsupported. Supply game-ready G1T files")
    if key.startswith("font/"):
        return ValueError("Font paths cannot be used as graphics inputs")
    if key.endswith((".exe", ".dll")):
        return ValueError("EXE/DLL files cannot be used as graphics inputs")
    return ValueError(f"Graphics path is not allowed: {game_path}")


def _collect_graphics(graphics_root: Path) -> list[Graphic]:
    if graphics_root.is_symlink():
        raise ValueError("Graphics input directory is a symbolic link")
    if not graphics_root.exists():
        return []
    if not graphics_root.is_dir():
        raise ValueError(f"Not a graphics input directory: {graphics_root}")
    children = list(graphics_root.iterdir())
    if not children:
        return []
    for child in children:
        if child.is_symlink():
            raise ValueError(f"Graphics input contains a symbolic link: {child}")
        if child.name not in SEGMENTS:
            raise ValueError(f"Graphics input directory must be B0 through B4: {child.name}")

    source_root = graphics_root.resolve(strict=True)
    graphics: list[Graphic] = []
    seen: dict[tuple[str, str], Path] = {}
    seen_components: dict[tuple[str, str], str] = {}
    for segment in SEGMENTS:
        segment_root = graphics_root / segment
        if not segment_root.exists():
            continue
        if segment_root.is_symlink() or not segment_root.is_dir():
            raise ValueError(f"{segment} graphics input directory is unsafe")
        for current, directories, filenames in os.walk(segment_root, topdown=True, followlinks=False):
            current_path = Path(current)
            for name in directories:
                path = current_path / name
                if path.is_symlink():
                    raise ValueError(f"Graphics input contains a symbolic link: {path}")
            directories.sort()
            for name in sorted(filenames):
                path = current_path / name
                if path.is_symlink():
                    raise ValueError(f"Graphics input contains a symbolic link: {path}")
                if not path.is_file():
                    raise ValueError(f"Graphics input is not a regular file: {path}")
                game_path = path.relative_to(segment_root).as_posix()
                if not valid_path(game_path, segment) or not is_graphics_path(game_path, segment):
                    raise _unsupported_path(game_path)
                key = game_path.lower()
                identity = (segment, key)
                if identity in seen:
                    raise ValueError(f"Graphics paths differ only in letter case: {game_path}")
                prefix = ""
                for part in game_path.split("/"):
                    prefix = f"{prefix}/{part}" if prefix else part
                    component_identity = (segment, prefix.lower())
                    existing = seen_components.get(component_identity)
                    if existing is not None and existing != prefix:
                        raise ValueError(f"Graphics directories differ only in letter case: {prefix}")
                    seen_components[component_identity] = prefix
                validate_g1t(path)
                resolved = path.resolve(strict=True)
                if not resolved.is_relative_to(source_root):
                    raise ValueError(f"Graphics input file points outside the directory: {path}")
                seen[identity] = path
                graphics.append(Graphic(segment, game_path, resolved))
    return graphics


def _read_manifest(segment_root: Path, segment: str) -> tuple[list[str], dict[str, str]]:
    manifest = segment_root / "manifest.txt"
    if segment_root.is_symlink():
        raise ValueError(f"{segment} runtime directory is a symbolic link")
    if manifest.is_symlink():
        raise ValueError(f"{segment} manifest is a symbolic link")
    if segment_root.exists() and not segment_root.is_dir():
        raise ValueError(f"{segment} runtime directory is not a regular directory")
    if not manifest.exists():
        return [], {}
    if not manifest.is_file():
        raise ValueError(f"{segment} manifest is not a regular file")
    lines = manifest.read_text(encoding="utf-8-sig").splitlines()
    entries: dict[str, str] = {}
    components: dict[str, str] = {}
    for line_number, raw in enumerate(lines, 1):
        game_path = raw.strip()
        if not game_path or game_path.startswith("#"):
            continue
        if not valid_path(game_path, segment):
            raise ValueError(f"{segment} manifest line {line_number} contains a disallowed path")
        key = game_path.lower()
        if key in entries:
            raise ValueError(f"{segment} manifest contains a path case conflict: {game_path}")
        prefix = ""
        for part in game_path.split("/"):
            prefix = f"{prefix}/{part}" if prefix else part
            component_key = prefix.lower()
            if component_key in components and components[component_key] != prefix:
                raise ValueError(f"{segment} manifest contains a directory case conflict: {prefix}")
            components[component_key] = prefix
        entries[key] = game_path
    return lines, entries


def _check_target_path(runtime_root: Path, graphic: Graphic) -> None:
    existing = _casefold_child(runtime_root, graphic.segment)
    if existing is None:
        return
    if existing.is_symlink() or not existing.is_dir():
        raise ValueError(f"{graphic.segment} runtime directory is unsafe")
    parent = existing
    parts = graphic.game_path.split("/")
    for index, part in enumerate(parts):
        child = _casefold_child(parent, part)
        if child is None:
            return
        if child.is_symlink():
            raise ValueError(f"Graphics destination contains a symbolic link: {child}")
        if index + 1 < len(parts) and not child.is_dir():
            raise ValueError(f"Graphics destination path is not a directory: {child}")
        if index + 1 == len(parts) and not child.is_file():
            raise ValueError(f"Graphics destination path is not a regular file: {child}")
        parent = child


def _target_path(runtime_root: Path, graphic: Graphic) -> Path:
    segment_root = runtime_root / graphic.segment
    if segment_root.is_symlink():
        raise ValueError(f"{graphic.segment} runtime directory is a symbolic link")
    segment_root.mkdir(parents=True, exist_ok=True)
    parent = segment_root
    parts = graphic.game_path.split("/")
    for part in parts[:-1]:
        child = parent / part
        if child.is_symlink():
            raise ValueError(f"Graphics destination contains a symbolic link: {child}")
        child.mkdir(exist_ok=True)
        if not child.is_dir():
            raise ValueError(f"Graphics destination path is not a directory: {child}")
        parent = child
    target = parent / parts[-1]
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise ValueError(f"Graphics destination file is unsafe: {target}")
    return target


def prepare_graphics(graphics_root: Path, runtime_root: Path) -> int:
    graphics = _collect_graphics(graphics_root)
    if not graphics:
        return 0
    if runtime_root.is_symlink() or not runtime_root.is_dir():
        raise ValueError(f"Runtime destination directory is missing or unsafe: {runtime_root}")
    source_resolved = graphics_root.resolve(strict=True)
    runtime_resolved = runtime_root.resolve(strict=True)
    if source_resolved.is_relative_to(runtime_resolved) or runtime_resolved.is_relative_to(source_resolved):
        raise ValueError("Graphics input and runtime destination must be separate directories")

    manifests: dict[str, tuple[list[str], dict[str, str]]] = {
        segment: _read_manifest(runtime_root / segment, segment) for segment in SEGMENTS
    }
    record_path = runtime_root / "graphics-overrides.json"
    if record_path.is_symlink():
        raise ValueError("graphics-overrides.json is a symbolic link")
    if record_path.exists():
        overrides = {
            (record["segment"], record["gamePath"].lower()): record
            for record in load_graphics_overrides(runtime_root, load_manifests(runtime_root))
        }
    else:
        overrides = {}
    additions: dict[str, list[str]] = {segment: [] for segment in SEGMENTS}
    for graphic in graphics:
        _check_target_path(runtime_root, graphic)
        _, entries = manifests[graphic.segment]
        key = graphic.game_path.lower()
        if key in entries:
            if entries[key] != graphic.game_path:
                raise ValueError(f"Manifest and graphics path letter case does not match: {graphic.game_path}")
        else:
            additions[graphic.segment].append(graphic.game_path)

    for graphic in graphics:
        target = _target_path(runtime_root, graphic)
        shutil.copy2(graphic.source, target)
        identity = (graphic.segment, graphic.game_path.lower())
        overrides[identity] = {
            "segment": graphic.segment,
            "gamePath": graphic.game_path,
            "sha256": sha256_file(target),
        }

    for segment, new_paths in additions.items():
        if not new_paths:
            continue
        lines, _ = manifests[segment]
        manifest = runtime_root / segment / "manifest.txt"
        if manifest.is_symlink():
            raise ValueError(f"{segment} manifest is a symbolic link")
        updated = lines + new_paths
        manifest.write_text("\n".join(updated) + "\n", encoding="utf-8", newline="\n")
    records = sorted(overrides.values(), key=lambda item: (item["segment"], item["gamePath"].lower()))
    record_path.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(graphics)


def _expect_error(action) -> None:
    try:
        action()
    except ValueError:
        return
    raise AssertionError("Invalid graphics input was accepted")


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="brq-graphics-") as temporary:
        root = Path(temporary)
        runtime = root / "runtime"
        graphics = root / "graphics"
        assert prepare_graphics(graphics, runtime) == 0
        graphics.mkdir()
        assert prepare_graphics(graphics, runtime) == 0
        assert not runtime.exists()

        runtime.mkdir()
        (runtime / "B2").mkdir()
        (runtime / "B2" / "manifest.txt").write_text("# baseline\n", encoding="utf-8")
        fixture = bytearray(16)
        fixture[:4] = b"GT1G"
        struct.pack_into("<I", fixture, 8, len(fixture))
        source = graphics / "B2" / "data" / "b2" / "x64" / "res_cmn" / "ui" / "sample.g1t"
        source.parent.mkdir(parents=True)
        source.write_bytes(fixture)
        shared = graphics / "B0" / "data" / "b4" / "x64" / "res_jp" / "ui" / "shared.g1t"
        shared.parent.mkdir(parents=True)
        shared.write_bytes(fixture)
        assert prepare_graphics(graphics, runtime) == 2
        assert (runtime / "B2" / "data" / "b2" / "x64" / "res_cmn" / "ui" / "sample.g1t").read_bytes() == fixture
        assert "data/b2/x64/res_cmn/ui/sample.g1t" in (runtime / "B2" / "manifest.txt").read_text()
        assert "data/b4/x64/res_jp/ui/shared.g1t" in (runtime / "B0" / "manifest.txt").read_text()
        overrides = json.loads((runtime / "graphics-overrides.json").read_text(encoding="utf-8"))
        assert {(item["segment"], item["gamePath"]) for item in overrides} == {
            ("B0", "data/b4/x64/res_jp/ui/shared.g1t"),
            ("B2", "data/b2/x64/res_cmn/ui/sample.g1t"),
        }
        assert prepare_graphics(graphics, runtime) == 2

        bad_png = root / "bad-png"
        bad = bad_png / "B2" / "data" / "b2" / "x64" / "res_cmn" / "ui" / "bad.png"
        bad.parent.mkdir(parents=True)
        bad.write_bytes(b"PNG")
        _expect_error(lambda: prepare_graphics(bad_png, root / "runtime-png"))

        bad_g1t = root / "bad-g1t"
        bad = bad_g1t / "B2" / "data" / "b2" / "x64" / "res_cmn" / "ui" / "bad.g1t"
        bad.parent.mkdir(parents=True)
        bad.write_bytes(b"not-g1t")
        _expect_error(lambda: prepare_graphics(bad_g1t, root / "runtime-g1t"))

        collision = root / "collision"
        for name in ("a.g1t", "A.G1T"):
            path = collision / "B2" / "data" / "b2" / "x64" / "res_cmn" / "ui" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(fixture)
        _expect_error(lambda: prepare_graphics(collision, root / "runtime-collision"))
    print("graphics self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--graphics-root", type=Path)
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.graphics_root is None or args.runtime_root is None:
        parser.error("--graphics-root and --runtime-root are required")
    print(prepare_graphics(args.graphics_root, args.runtime_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
