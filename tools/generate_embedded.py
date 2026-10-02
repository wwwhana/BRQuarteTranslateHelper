#!/usr/bin/env python3
"""Validate runtime manifests and generate optional Windows RCDATA inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from bsb import is_choice_path
from kscl import is_layout_path


SEGMENTS = tuple(f"B{index}" for index in range(5))
HD1080_FONTS = {
    "font/mainfont00_hd1080_0.g1t",
    "font/mainfont01_hd1080_0.g1t",
    "font/mainfont02_hd1080_0.g1t",
}
HD720_FONTS = {"font/mainfonthd720_0.g1t"}
GLYPH_TABLES = {
    segment: tuple((f"Font/main0{slot}-glyph-table-0x48.bin", slot, 0x48) for slot in range(3))
    for segment in ("B0", "B2", "B3", "B4")
}
GLYPH_TABLES["B1"] = (("Font/ko-glyph-table-0x1c.bin", 0, 0x1C),)
EXPECTED_ASSET_COUNTS = {"B0": 6, "B1": 1764, "B2": 132, "B3": 198, "B4": 4489}
EXPECTED_GLYPH_TABLE_COUNTS = {"B0": 3, "B1": 1, "B2": 3, "B3": 3, "B4": 3}
EXPECTED_ASSET_TOTAL = 6589
EXPECTED_GLYPH_TABLE_TOTAL = 13
G1T_MAGIC = b"GT1G"
G1T_SIZE_OFFSET = 8
G1T_HEADER_SIZE = G1T_SIZE_OFFSET + 4


@dataclass(frozen=True)
class Asset:
    segment: str
    game_path: str
    source: Path
    resource_id: int


@dataclass(frozen=True)
class GlyphTable:
    segment: str
    slot: int
    record_size: int
    source: Path
    resource_id: int


def _safe_path(game_path: str) -> bool:
    if not isinstance(game_path, str):
        return False
    try:
        game_path.encode("ascii")
    except UnicodeEncodeError:
        return False
    if not game_path or game_path.startswith("/") or game_path.endswith("/") or "\\" in game_path:
        return False
    forbidden = set(':"*?<>|')
    if any(ord(char) < 0x20 or ord(char) > 0x7e or char in forbidden for char in game_path):
        return False
    parts = game_path.split("/")
    if any(part in {"", ".", ".."} or part.endswith((".", " ")) for part in parts):
        return False
    return True


def _canonical_segment(segment: str) -> str | None:
    if not isinstance(segment, str):
        return None
    normalized = segment.upper()
    return normalized if normalized in SEGMENTS else None


def is_graphics_path(game_path: str, segment: str) -> bool:
    segment = _canonical_segment(segment)
    if segment is None or not _safe_path(game_path):
        return False
    key = game_path.lower()
    if not key.endswith(".g1t"):
        return False
    if segment == "B0":
        if key == "res_jp/ipu/logo_kt.g1t":
            return True
        prefixes = (
            "data/b4/x64/res_cmn/ui/",
            "data/b4/x64/res_jp/ui/",
        )
    else:
        base = f"data/{segment.lower()}/x64/"
        prefixes = tuple(
            base + suffix for suffix in (
                "res_cmn/ui/",
                "res_jp/ui/",
                "res_jp/ipu/",
                "res_jp/layout/",
            )
        )
    return key.startswith(prefixes)


def is_baseline_image_path(game_path: str, segment: str) -> bool:
    segment = _canonical_segment(segment)
    if segment is None or not isinstance(game_path, str):
        return False
    key = game_path.lower()
    return (segment == "B0" and key == "res_jp/ipu/logo_kt.g1t") or (
        segment == "B1" and key == "data/b1/x64/res_jp/ipu/headsup.g1t"
    )


def is_optional_graphics_path(game_path: str, segment: str) -> bool:
    return is_graphics_path(game_path, segment) and not is_baseline_image_path(game_path, segment)


def validate_g1t(path: Path) -> None:
    try:
        size = path.stat().st_size
        with path.open("rb") as stream:
            header = stream.read(G1T_HEADER_SIZE)
    except OSError as error:
        raise ValueError(f"Could not read G1T: {path}") from error
    if size < G1T_HEADER_SIZE or header[:4] != G1T_MAGIC:
        raise ValueError(f"Not a G1T file: {path}")
    declared_size = struct.unpack_from("<I", header, G1T_SIZE_OFFSET)[0]
    if declared_size != size:
        raise ValueError(f"G1T size mismatch: {path}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def valid_path(game_path: str, segment: str) -> bool:
    segment = _canonical_segment(segment)
    if segment is None or not _safe_path(game_path):
        return False

    key = game_path.lower()
    prefix = f"saves/{segment.lower()}/"
    if key.startswith(prefix):
        relative = key[len(prefix):]
        if (relative.startswith(("text_jp/", "ui/", "ui_cmn/"))
                and relative.endswith((".xml", ".xml.e"))):
            return True
        if segment != "B0" and relative in (
                "event/eventglossarykeyword.xml", "event/eventglossarykeyword.xml.e"):
            return True
    if segment == "B1" and key == "data/b1/x64/res_jp/ipu/headsup.g1t":
        return True
    if is_layout_path(game_path, segment) or is_choice_path(game_path, segment):
        return True
    if is_graphics_path(game_path, segment):
        return True
    if segment == "B1" and key == "layout/default.g1n":
        return True
    event_prefix = f"event/{segment.lower()}/event_jp/"
    if key.startswith(event_prefix) and key.endswith(".ebm"):
        return True
    fonts = HD720_FONTS if segment == "B1" else HD1080_FONTS
    return key in fonts


def load_manifests(runtime_root: Path) -> list[Asset]:
    assets: list[Asset] = []
    next_id = 1000
    for segment in SEGMENTS:
        segment_root = runtime_root / segment
        manifest = segment_root / "manifest.txt"
        if segment_root.is_symlink():
            raise ValueError(f"{segment} manifest path uses a symbolic link")
        if not manifest.is_file():
            continue
        if manifest.is_symlink():
            raise ValueError(f"{segment} manifest path uses a symbolic link")
        root = segment_root.resolve(strict=True)
        seen: set[str] = set()
        seen_components: dict[str, str] = {}
        for line_number, raw in enumerate(manifest.read_text(encoding="utf-8-sig").splitlines(), 1):
            game_path = raw.strip()
            if not game_path or game_path.startswith("#"):
                continue
            key = game_path.lower()
            if not valid_path(game_path, segment) or key in seen:
                raise ValueError(f"{segment} manifest line {line_number} is not allowed")
            prefix = ""
            for part in game_path.split("/"):
                prefix = f"{prefix}/{part}" if prefix else part
                component_key = prefix.lower()
                if component_key in seen_components and seen_components[component_key] != prefix:
                    raise ValueError(f"{segment} manifest line {line_number} has a case collision")
                seen_components[component_key] = prefix
            candidate = segment_root.joinpath(*game_path.split("/"))
            cursor = segment_root
            for part in game_path.split("/"):
                cursor /= part
                if cursor.is_symlink():
                    raise ValueError(f"{segment} manifest line {line_number} uses a symbolic link")
            if not candidate.is_file() or not candidate.resolve(strict=True).is_relative_to(root):
                raise ValueError(f"{segment} manifest line {line_number} has no safe file")
            if candidate.stat().st_size == 0:
                raise ValueError(f"{segment} manifest line {line_number} is empty")
            if is_baseline_image_path(game_path, segment) or is_optional_graphics_path(game_path, segment):
                validate_g1t(candidate)
            if next_id > 0xFFFF:
                raise ValueError("too many embedded assets")
            seen.add(key)
            assets.append(Asset(segment, game_path, candidate.resolve(strict=True), next_id))
            next_id += 1
    return assets


def load_glyph_tables(runtime_root: Path, first_id: int = 1000) -> list[GlyphTable]:
    tables: list[GlyphTable] = []
    next_id = first_id
    for segment in SEGMENTS:
        segment_root = runtime_root / segment
        candidates = [(segment_root / relative, slot, record_size)
                      for relative, slot, record_size in GLYPH_TABLES[segment]]
        if not any(path.exists() or path.is_symlink() for path, _, _ in candidates):
            continue
        if segment_root.is_symlink() or not segment_root.is_dir():
            raise ValueError(f"{segment} glyph table root is not safe")
        root = segment_root.resolve(strict=True)
        for path, slot, record_size in candidates:
            if path.is_symlink() or not path.is_file() or not path.resolve(strict=True).is_relative_to(root):
                raise ValueError(f"{segment} glyph table {slot} is missing or unsafe")
            data = path.read_bytes()
            if not data or len(data) % record_size:
                raise ValueError(f"{segment} glyph table {slot} has the wrong size")
            keys = [struct.unpack_from("<I", data, offset)[0] for offset in range(0, len(data), record_size)]
            if any(left >= right for left, right in zip(keys, keys[1:])):
                raise ValueError(f"{segment} glyph table {slot} keys are not strictly increasing")
            if next_id > 0xFFFF:
                raise ValueError("too many embedded resources")
            tables.append(GlyphTable(segment, slot, record_size, path.resolve(strict=True), next_id))
            next_id += 1
    return tables


def optional_graphics_count(assets: list[Asset]) -> int:
    return sum(is_optional_graphics_path(asset.game_path, asset.segment) for asset in assets)


def load_graphics_overrides(runtime_root: Path, assets: list[Asset]) -> list[dict[str, str]]:
    record_path = runtime_root / "graphics-overrides.json"
    if record_path.is_symlink():
        raise ValueError("graphics-overrides.json is a symbolic link")
    if not record_path.exists():
        return []
    if not record_path.is_file():
        raise ValueError("graphics-overrides.json is not a regular file")
    try:
        records = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("Could not read graphics-overrides.json") from error
    if not isinstance(records, list):
        raise ValueError("Invalid graphics-overrides.json format")
    by_identity = {(asset.segment, asset.game_path.lower()): asset for asset in assets}
    seen: set[tuple[str, str]] = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != {"segment", "gamePath", "sha256"}:
            raise ValueError("Invalid graphics-overrides.json entry format")
        segment = record["segment"]
        game_path = record["gamePath"]
        digest = record["sha256"]
        if not isinstance(segment, str) or not isinstance(game_path, str) or not isinstance(digest, str):
            raise ValueError("Invalid graphics-overrides.json entry values")
        identity = (segment, game_path.lower())
        asset = by_identity.get(identity)
        if (
            segment not in SEGMENTS
            or not valid_path(game_path, segment)
            or not is_graphics_path(game_path, segment)
            or asset is None
            or asset.game_path != game_path
            or identity in seen
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or sha256_file(asset.source) != digest
        ):
            raise ValueError(f"graphics-overrides.json entry differs from runtime assets: {segment}/{game_path}")
        seen.add(identity)
    return records


def validate_release_counts(assets: list[Asset], glyph_tables: list[GlyphTable]) -> None:
    baseline_assets = [
        asset for asset in assets if not is_optional_graphics_path(asset.game_path, asset.segment)
    ]
    asset_counts = Counter(asset.segment for asset in baseline_assets)
    glyph_counts = Counter(table.segment for table in glyph_tables)
    for segment in SEGMENTS:
        if asset_counts[segment] != EXPECTED_ASSET_COUNTS[segment]:
            raise ValueError(
                f"{segment} embedded asset count is {asset_counts[segment]}; "
                f"expected {EXPECTED_ASSET_COUNTS[segment]}"
            )
        if glyph_counts[segment] != EXPECTED_GLYPH_TABLE_COUNTS[segment]:
            raise ValueError(
                f"{segment} embedded glyph table count is {glyph_counts[segment]}; "
                f"expected {EXPECTED_GLYPH_TABLE_COUNTS[segment]}"
            )
    if len(baseline_assets) != EXPECTED_ASSET_TOTAL:
        raise ValueError(
            f"embedded baseline asset total is {len(baseline_assets)}; expected {EXPECTED_ASSET_TOTAL}"
        )
    if len(glyph_tables) != EXPECTED_GLYPH_TABLE_TOTAL:
        raise ValueError(
            f"embedded glyph table total is {len(glyph_tables)}; "
            f"expected {EXPECTED_GLYPH_TABLE_TOTAL}"
        )


def generate(runtime_root: Path, header: Path, resource: Path, *, translations_root: Path | None = None) -> int:
    assets = load_manifests(runtime_root)
    glyph_tables = load_glyph_tables(runtime_root, 1000 + len(assets))
    if (runtime_root / 'build-inputs.json').is_file():
        from build_translation import check_runtime
        check_runtime(runtime_root)
    else:
        validate_release_counts(assets, glyph_tables)
    load_graphics_overrides(runtime_root, assets)
    header.parent.mkdir(parents=True, exist_ok=True)
    resource.parent.mkdir(parents=True, exist_ok=True)

    rows = ",\n".join(
        f'    {{"{asset.segment}", "{asset.game_path}", {asset.resource_id}}}' for asset in assets
    )
    glyph_rows = ",\n".join(
        f'    {{"{table.segment}", {table.slot}, {table.record_size}, {table.resource_id}}}'
        for table in glyph_tables
    )
    header.write_text(
        "#pragma once\n\n"
        "#include <array>\n"
        "#include <cstdint>\n\n"
        "namespace brq {\n\n"
        "struct EmbeddedAsset final {\n"
        "    const char* segment;\n"
        "    const char* game_path;\n"
        "    std::uint16_t resource_id;\n"
        "};\n\n"
        "struct EmbeddedGlyphTable final {\n"
        "    const char* segment;\n"
        "    std::uint8_t slot;\n"
        "    std::uint8_t record_size;\n"
        "    std::uint16_t resource_id;\n"
        "};\n\n"
        f"inline constexpr std::array<EmbeddedAsset, {len(assets)}> embedded_assets{{{{\n"
        f"{rows}\n"
        "}};\n\n"
        f"inline constexpr std::array<EmbeddedGlyphTable, {len(glyph_tables)}> embedded_glyph_tables{{{{\n"
        f"{glyph_rows}\n"
        "}};\n\n"
        "}  // namespace brq\n",
        encoding="utf-8",
        newline="\n",
    )

    resource_lines = []
    for asset in assets:
        source = asset.source.as_posix()
        if '"' in source:
            raise ValueError("asset source path contains a quote")
        resource_lines.append(f'{asset.resource_id} RCDATA "{source}"')
    for table in glyph_tables:
        source = table.source.as_posix()
        if '"' in source:
            raise ValueError("glyph table source path contains a quote")
        resource_lines.append(f'{table.resource_id} RCDATA "{source}"')
    resource.write_text("\n".join(resource_lines) + ("\n" if resource_lines else ""), encoding="utf-8", newline="\n")
    return len(assets) + len(glyph_tables)


def self_test() -> None:
    assets = [
        Asset(segment, "test", Path(), 0)
        for segment, count in EXPECTED_ASSET_COUNTS.items()
        for _ in range(count)
    ]
    glyph_tables = [
        GlyphTable(segment, 0, 0, Path(), 0)
        for segment, count in EXPECTED_GLYPH_TABLE_COUNTS.items()
        for _ in range(count)
    ]
    validate_release_counts(assets, glyph_tables)
    assert optional_graphics_count(assets) == 0
    extra = Asset("B2", "data/b2/x64/res_cmn/ui/extra.g1t", Path(), 0)
    validate_release_counts(assets + [extra], glyph_tables)
    assert optional_graphics_count(assets + [extra]) == 1
    try:
        validate_release_counts(assets[:-1], glyph_tables)
    except ValueError as error:
        assert "B4 embedded asset count" in str(error)
    else:
        raise AssertionError("incomplete embedded assets were accepted")
    assert EXPECTED_ASSET_TOTAL + EXPECTED_GLYPH_TABLE_TOTAL == 6602
    assert is_graphics_path("res_jp/ipu/logo_kt.g1t", "B0")
    assert is_graphics_path("data/b4/x64/res_jp/ui/b0_title.g1t", "B0")
    assert not is_graphics_path("data/b4/x64/res_jp/ipu/b0_title.g1t", "B0")
    assert not is_graphics_path("data/b2/x64/res_cmn/ui/extra.png", "B2")
    print("self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--translations-root", type=Path)
    parser.add_argument("--header", type=Path)
    parser.add_argument("--resource", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.runtime_root is None or args.header is None or args.resource is None:
        parser.error("--runtime-root, --header, and --resource are required")
    print(generate(args.runtime_root, args.header, args.resource, translations_root=args.translations_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
