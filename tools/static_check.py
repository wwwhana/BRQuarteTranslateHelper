#!/usr/bin/env python3
"""Check the built ASI, verified module constants, manifests, and untouched game files."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from generate_embedded import (
    SEGMENTS,
    load_glyph_tables,
    load_manifests,
    load_graphics_overrides,
    valid_path,
    validate_release_counts,
)


IMAGE_FILE_DLL = 0x2000
IMAGE_FILE_MACHINE_AMD64 = 0x8664
IMAGE_DLLCHARACTERISTICS_HIGH_ENTROPY_VA = 0x0020
IMAGE_DLLCHARACTERISTICS_DYNAMIC_BASE = 0x0040
IMAGE_DLLCHARACTERISTICS_NX_COMPAT = 0x0100
RT_RCDATA = 10
IMAGE_SCN_MEM_READ = 0x40000000
IMAGE_SCN_MEM_WRITE = 0x80000000
ORIGINAL_KIND_COUNTS = {
    "launcher": 1,
    "game-module": 5,
    "steam-api": 1,
    "pak": 22,
}
ADDED_DEPLOYMENT_FILES = {"BRQuarteTranslateHelper.asi", "dinput8.dll"}
B1_WARNING_SHA256 = "b201c7751465555b00c36fda5d401624351a60208d7ad8e6a666fd6377c4e338"
B1_MENU_FONT_SOURCE_SHA256 = "028d8b6a0176c16ac70d02e2a85f00ea539919f4f013795cffa1c780972fb59d"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rva_to_raw(data: bytes, sections: list[tuple[str, int, int, int, int, int]], rva: int) -> int:
    for _, section_rva, virtual_size, raw, raw_size, _ in sections:
        if section_rva <= rva < section_rva + max(virtual_size, raw_size):
            result = raw + rva - section_rva
            require(result < len(data), "RVA is outside the file")
            return result
    raise RuntimeError("RVA has no section")


def parse_pe(data: bytes) -> dict[str, object]:
    require(data[:2] == b"MZ", "output is not a PE file")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    require(data[pe : pe + 4] == b"PE\0\0", "PE signature is missing")
    machine, section_count, timestamp, _, _, optional_size, characteristics = struct.unpack_from(
        "<HHIIIHH", data, pe + 4
    )
    optional = pe + 24
    require(struct.unpack_from("<H", data, optional)[0] == 0x20B, "output is not PE32+")
    size_of_image = struct.unpack_from("<I", data, optional + 56)[0]
    dll_characteristics = struct.unpack_from("<H", data, optional + 70)[0]
    sections = []
    table = optional + optional_size
    for index in range(section_count):
        offset = table + 40 * index
        name = data[offset : offset + 8].split(b"\0", 1)[0].decode("ascii", "replace")
        virtual_size, rva, raw_size, raw = struct.unpack_from("<IIII", data, offset + 8)
        section_characteristics = struct.unpack_from("<I", data, offset + 36)[0]
        sections.append((name, rva, virtual_size, raw, raw_size, section_characteristics))

    export_rva, export_size = struct.unpack_from("<II", data, optional + 112)
    import_rva, _ = struct.unpack_from("<II", data, optional + 112 + 8)
    resource_rva, resource_size = struct.unpack_from("<II", data, optional + 112 + 2 * 8)
    relocation_rva, relocation_size = struct.unpack_from("<II", data, optional + 112 + 5 * 8)
    imports: list[str] = []
    export_names: list[str] = []
    export_function_count = 0
    if export_rva:
        export = rva_to_raw(data, sections, export_rva)
        export_function_count, export_name_count = struct.unpack_from("<II", data, export + 20)
        names_rva = struct.unpack_from("<I", data, export + 32)[0]
        names = rva_to_raw(data, sections, names_rva)
        for index in range(export_name_count):
            name_rva = struct.unpack_from("<I", data, names + index * 4)[0]
            name_raw = rva_to_raw(data, sections, name_rva)
            end = data.index(0, name_raw)
            export_names.append(data[name_raw:end].decode("ascii"))
    if import_rva:
        descriptor = rva_to_raw(data, sections, import_rva)
        while any(data[descriptor : descriptor + 20]):
            name_rva = struct.unpack_from("<I", data, descriptor + 12)[0]
            name_raw = rva_to_raw(data, sections, name_rva)
            end = data.index(0, name_raw)
            imports.append(data[name_raw:end].decode("ascii"))
            descriptor += 20

    rcdata_resources: dict[int, tuple[int, int]] = {}
    if resource_rva:
        resource_base = rva_to_raw(data, sections, resource_rva)

        def resource_raw(relative: int, size: int) -> int:
            require(
                0 <= relative <= resource_size and size <= resource_size - relative,
                "resource directory entry is outside the resource directory",
            )
            result = resource_base + relative
            require(result + size <= len(data), "resource directory entry is outside the file")
            return result

        def resource_entries(relative: int) -> list[tuple[int, int]]:
            directory = resource_raw(relative, 16)
            named, identifiers = struct.unpack_from("<HH", data, directory + 12)
            count = named + identifiers
            entries = resource_raw(relative + 16, count * 8)
            return [struct.unpack_from("<II", data, entries + index * 8) for index in range(count)]

        rcdata_nodes = [
            child
            for name, child in resource_entries(0)
            if not name & 0x80000000 and name == RT_RCDATA
        ]
        require(len(rcdata_nodes) <= 1, "PE has duplicate RCDATA type directories")
        if rcdata_nodes:
            require(rcdata_nodes[0] & 0x80000000, "PE RCDATA type is not a directory")
            for resource_id, languages in resource_entries(rcdata_nodes[0] & 0x7FFFFFFF):
                require(not resource_id & 0x80000000, "PE has a named RCDATA resource")
                require(resource_id not in rcdata_resources, f"PE has duplicate RCDATA resource {resource_id}")
                require(languages & 0x80000000, f"PE RCDATA resource {resource_id} has no language directory")
                language_entries = resource_entries(languages & 0x7FFFFFFF)
                require(len(language_entries) == 1,
                        f"PE RCDATA resource {resource_id} does not have exactly one language")
                language, value = language_entries[0]
                require(not language & 0x80000000 and not value & 0x80000000,
                        f"PE RCDATA resource {resource_id} has an invalid language entry")
                entry = resource_raw(value, 16)
                data_rva, size, _code_page, _reserved = struct.unpack_from("<IIII", data, entry)
                raw = rva_to_raw(data, sections, data_rva)
                require(size <= len(data) - raw, f"PE RCDATA resource {resource_id} is outside the file")
                rcdata_resources[resource_id] = (raw, size)
    return {
        "machine": machine,
        "timestamp": timestamp,
        "sizeOfImage": size_of_image,
        "characteristics": characteristics,
        "dllCharacteristics": dll_characteristics,
        "exportDirectory": (export_rva, export_size),
        "exportFunctionCount": export_function_count,
        "exportNames": sorted(export_names),
        "baseRelocations": (relocation_rva, relocation_size),
        "imports": sorted(imports, key=str.lower),
        "rcdataResources": len(rcdata_resources),
        "rcdata": rcdata_resources,
        "sections": sections,
    }


def read_module_constants(header: Path) -> dict[str, tuple[int, int, int, int, int]]:
    text = header.read_text(encoding="utf-8")
    pattern = re.compile(
        r'L"(B[0-4]\.dll)",\s*"B[0-4]",\s*'
        r'(0x[0-9a-fA-F]+),\s*(0x[0-9a-fA-F]+),\s*(0x[0-9a-fA-F]+),\s*'
        r'(0x[0-9a-fA-F]+),\s*(0x[0-9a-fA-F]+),'
    )
    values = {
        match.group(1): tuple(int(match.group(index), 16) for index in range(2, 7))
        for match in pattern.finditer(text)
    }
    require(len(values) == 5, "module specification count is not five")
    return values


def verify_module_constants(project: Path, game_root: Path, signatures: dict[str, object], binary: bytes) -> None:
    constants = read_module_constants(project / "src" / "module_specs.hpp")
    for module in signatures["modules"]:
        name = module["name"]
        match = next(item for item in module["knownSignatureMatches"] if item["extendedSignatureMatch"])
        timestamp = int(datetime.fromisoformat(module["peTimestampUtc"].replace("Z", "+00:00")).timestamp())
        expected = (
            timestamp,
            int(module["sizeOfImage"], 16),
            int(match["rva"], 16),
            int(module["fileSize"]["rva"], 16),
            int(module["rtti"]["typeDescriptorRva"], 16),
        )
        require(constants.get(name) == expected, f"{name} constants differ from signatures.json")
        context = bytes.fromhex(match["contextBytes"])
        require(context in binary, f"{name} verified Open context is missing from the ASI")
        size_context = bytes.fromhex(module["fileSize"]["contextBytes"])
        require(size_context in binary, f"{name} verified file-size context is missing from the ASI")
        module_data = (game_root / name).read_bytes()
        module_pe = parse_pe(module_data)
        size_raw = rva_to_raw(module_data, module_pe["sections"], expected[3])
        require(size_raw == int(module["fileSize"]["fileOffset"], 16) and
                module_data[size_raw : size_raw + len(size_context)] == size_context,
                f"{name} file-size function differs from the verified original bytes")


def verify_glyph_constants(project: Path, signatures: dict[str, object]) -> None:
    text = (project / "src" / "module_specs.hpp").read_text(encoding="utf-8")
    pattern = re.compile(
        r'\{\s*"(B[0-4])",\s*(0x[0-9a-fA-F]+),\s*(0x[0-9a-fA-F]+),\s*\{([^}]*)\}\s*\}'
    )
    constants = {}
    for match in pattern.finditer(text):
        patch = tuple(int(value, 16) for value in re.findall(r"0x[0-9a-fA-F]+", match.group(4)))
        constants[match.group(1)] = (int(match.group(2), 16), int(match.group(3), 16), patch or (0, 0, 0, 0))
    require(len(constants) == 5, "glyph lookup specification count is not five")

    for segment, module in signatures["modules"].items():
        tokens = module["entry_signature"]["aob"].split()
        record_size = int(module["record_layout"].split("/", 1)[0], 16)
        patch = tuple(int(value, 16) for value in tokens[42:46]) if record_size == 0x48 else (0, 0, 0, 0)
        expected = (int(module["lookup_function"]["start"]["rva"], 16), record_size, patch)
        require(constants.get(segment) == expected, f"{segment} glyph lookup constants differ from analysis")


def verify_glyph_binaries(game_root: Path, signatures: dict[str, object]) -> None:
    for segment, module in signatures["modules"].items():
        data = (game_root / module["module"]).read_bytes()
        pattern = b"".join(
            b"." if token == "??" else re.escape(bytes((int(token, 16),)))
            for token in module["entry_signature"]["aob"].split()
        )
        matches = [match.start() for match in re.finditer(pattern, data, re.DOTALL)]
        expected = int(module["lookup_function"]["start"]["pe_file_offset"], 16)
        require(matches == [expected], f"{segment} glyph lookup signature is not unique at the verified offset")


def read_b1_font_constants(header: Path) -> dict[str, int]:
    text = header.read_text(encoding="utf-8")
    names = (
        "b1_font_manager_rva",
        "b1_font_descriptor_rva",
        "b1_font_descriptor_count_offset",
        "b1_font_descriptor_width",
        "b1_font_descriptor_original_height",
        "b1_font_descriptor_replacement_height",
        "b1_font_descriptor_glyph_count",
    )
    values = {}
    for name in names:
        match = re.search(rf"\b{name}\s*=\s*(0x[0-9a-fA-F]+|[0-9]+)\s*;", text)
        require(match is not None, f"{name} is missing from module_specs.hpp")
        values[name] = int(match.group(1), 0)
    return values


def verify_b1_font_descriptor(project: Path, game_root: Path, font_output: Path | None = None) -> None:
    constants = read_b1_font_constants(project / "src" / "module_specs.hpp")
    require(
        constants == {
            "b1_font_manager_rva": 0x1651530,
            "b1_font_descriptor_rva": 0xD80D60,
            "b1_font_descriptor_count_offset": 0x10,
            "b1_font_descriptor_width": 2048,
            "b1_font_descriptor_original_height": 1024,
            "b1_font_descriptor_replacement_height": 2048,
            "b1_font_descriptor_glyph_count": 2565,
        },
        "B1 font descriptor code constants differ from the verified values",
    )
    module_constants = read_module_constants(project / "src" / "module_specs.hpp")["B1.dll"]
    data = (game_root / "B1.dll").read_bytes()
    pe = parse_pe(data)
    dlc_shape = pe["sizeOfImage"] == 0x1a8e000 and len(pe["sections"]) == 9 and (
        pe["sections"][8][:3] == ('.br4cos', 0x1a8c000, 0x1a1f) and
        pe["sections"][8][4:] == (0x1c00, 0xe0000040))
    require(pe["timestamp"] == module_constants[0] and
            (pe["sizeOfImage"] == module_constants[1] or dlc_shape),
            "B1 timestamp or image size differs from the loader constants")
    rva = constants["b1_font_descriptor_rva"]
    count_offset = constants["b1_font_descriptor_count_offset"]
    descriptor_raw = rva_to_raw(data, pe["sections"], rva)
    section = next(
        (item for item in pe["sections"] if item[1] <= rva < item[1] + max(item[2], item[4])),
        None,
    )
    require(section is not None and section[0] == ".rdata", "B1 font descriptor is not in .rdata")
    require(section[5] & IMAGE_SCN_MEM_READ and not section[5] & IMAGE_SCN_MEM_WRITE,
            "B1 font descriptor section is not read-only")

    source_dimensions = struct.pack(
        "<HH",
        constants["b1_font_descriptor_width"],
        constants["b1_font_descriptor_original_height"],
    )
    require(data[descriptor_raw : descriptor_raw + 4] == source_dimensions,
            "B1 font descriptor bytes are not 00 08 00 04")
    glyph_count = struct.pack("<H", constants["b1_font_descriptor_glyph_count"])
    require(data[descriptor_raw + count_offset : descriptor_raw + count_offset + 2] == glyph_count,
            "B1 font descriptor glyph count bytes are not 05 0a")

    summary_root = (font_output / 'B1' if font_output else
                    game_root / 'dev-assets/B1/font/generated/nanumsquare-regular-current-ko')
    summary = json.loads((summary_root / 'summary.json').read_text(encoding='utf-8'))
    expected_source = [
        constants["b1_font_descriptor_width"],
        constants["b1_font_descriptor_original_height"],
    ]
    expected_replacement = [
        constants["b1_font_descriptor_width"],
        constants["b1_font_descriptor_replacement_height"],
    ]
    require(summary["atlas"]["old_size"] == expected_source,
            "B1 font summary source dimensions differ from the code constants")
    require(summary["atlas"]["new_size"] == expected_replacement,
            "B1 font summary replacement dimensions are not 2048x2048")


def verify_original_files(baseline_path: Path, game_root: Path,
                          signatures: dict[str, object], installed_path: Path | None = None) -> tuple[dict[str, int], list[str]]:
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    installed = json.loads(installed_path.read_text(encoding="utf-8")) if installed_path else baseline
    require({r['path'] for r in installed['files']} == {r['path'] for r in baseline['files']} and
            len(installed['files']) == len(baseline['files']), "installed snapshot must cover the original file list exactly")
    installed_hashes = {r['path']: r['sha256'] for r in installed['files']}
    require(baseline.get("schemaVersion") == 1, "original file hash schema version is not one")
    require(baseline.get("algorithm") == "SHA-256", "original file hash algorithm is not SHA-256")

    excluded = baseline.get("excludedAddedFiles")
    require(isinstance(excluded, list) and all(isinstance(item, str) for item in excluded),
            "excluded added file list is invalid")
    require(len(excluded) == len(set(excluded)) and set(excluded) == ADDED_DEPLOYMENT_FILES,
            "added dinput8.dll and BRQuarteTranslateHelper.asi must be excluded from the original baseline")

    records = baseline.get("files")
    require(isinstance(records, list), "original file hash records are missing")
    hashes: dict[str, str] = {}
    paths_by_kind = {kind: set() for kind in ORIGINAL_KIND_COUNTS}
    for record in records:
        require(isinstance(record, dict) and set(record) == {"path", "kind", "sha256"},
                "original file hash record fields are invalid")
        name = record["path"]
        kind = record["kind"]
        expected_hash = record["sha256"]
        require(isinstance(name, str) and isinstance(kind, str) and isinstance(expected_hash, str),
                "original file hash record values are invalid")
        relative = PurePosixPath(name)
        require(name == relative.as_posix() and not relative.is_absolute() and "\\" not in name and
                relative.parts and all(part not in ("", ".", "..") for part in relative.parts),
                f"original file path is not canonical: {name}")
        require(kind in ORIGINAL_KIND_COUNTS, f"unknown original file kind: {kind}")
        require(re.fullmatch(r"[0-9a-f]{64}", expected_hash) is not None,
                f"invalid SHA-256 for original file: {name}")
        require(name not in hashes and name not in ADDED_DEPLOYMENT_FILES,
                f"duplicate or added file in original baseline: {name}")
        source = game_root.joinpath(*relative.parts)
        require(source.is_file() and not source.is_symlink(), f"original file is missing or is a symlink: {name}")
        require(sha256(source) == installed_hashes[name], f"installed file SHA-256 changed: {name}")
        hashes[name] = expected_hash
        paths_by_kind[kind].add(name)

    counts = {kind: len(paths) for kind, paths in paths_by_kind.items()}
    require(counts == ORIGINAL_KIND_COUNTS, f"original file kind counts differ: {counts}")
    signature_hashes = {signatures["launcher"]["file"]: signatures["launcher"]["sha256"]}
    signature_hashes.update({module["name"]: module["sha256"] for module in signatures["modules"]})
    require(paths_by_kind["launcher"] == {signatures["launcher"]["file"]},
            "original launcher record differs from signatures.json")
    require(paths_by_kind["game-module"] == {module["name"] for module in signatures["modules"]},
            "original game module records differ from signatures.json")
    require(all(hashes.get(name) == expected_hash for name, expected_hash in signature_hashes.items()),
            "launcher or game module hash differs from signatures.json")
    require(paths_by_kind["steam-api"] == {"steam_api64.dll"},
            "original Steam API record is not steam_api64.dll")

    actual_paks = {
        path.relative_to(game_root).as_posix()
        for path in (game_root / "Data").glob("*.PAK")
        if path.is_file()
    }
    require(paths_by_kind["pak"] == actual_paks,
            "original PAK baseline does not exactly match the 22 Data PAK files")
    return counts, sorted(excluded)


def load_project_tools(game_root: Path):
    tools = game_root / "tools"
    require(tools.is_dir(), "translation tools are missing")
    sys.path.insert(0, str(tools))
    try:
        from b0_strings import check as check_b0
        from b1_strings import inspect as inspect_b1
        from b23_strings import inspect_game
        from build_font_assets import current_corpus_hashes
        from check_translations import check as check_dialogue
    finally:
        sys.path.pop(0)
    return check_b0, inspect_b1, inspect_game, current_corpus_hashes, check_dialogue


def verify_translations(game_root: Path) -> None:
    check_b0, inspect_b1, inspect_game, _current_corpus_hashes, check_dialogue = (
        load_project_tools(game_root)
    )
    require(check_b0(True) == 0, "B0 translation is incomplete")
    b1_issues = inspect_b1()
    require(not b1_issues, f"B1 non-dialogue translation has {sum(map(len, b1_issues.values()))} issues")
    for game in ("B2", "B3", "B4"):
        issues = inspect_game(game)
        require(not issues, f"{game} non-dialogue translation has {sum(map(len, issues.values()))} issues")

    with tempfile.TemporaryDirectory(prefix="brq-final-check-") as directory:
        file_list = Path(directory) / "files.txt"
        for game in ("B1", "B2", "B3", "B4"):
            source_root = game_root / "dev-assets" / game / "text" / "source" / "event_jp"
            target_root = game_root / "dev-assets" / game / "text" / "ko" / "event_jp"
            source_paths = {path.relative_to(source_root) for path in source_root.rglob("*.json")}
            target_paths = {path.relative_to(target_root) for path in target_root.rglob("*.json")}
            require(source_paths, f"{game} dialogue source files are missing")
            require(source_paths == target_paths, f"{game} dialogue source and Korean file sets differ")
            file_list.write_text(
                "".join(f"{path.as_posix()}\n" for path in sorted(source_paths)), encoding="utf-8"
            )
            require(check_dialogue(game, file_list, True) == 0, f"{game} dialogue translation is incomplete")


def verify_non_dialogue_runtime(game_root: Path, runtime_root: Path) -> None:
    tools = game_root / "tools"
    sys.path.insert(0, str(tools))
    try:
        import b1_strings
        import b23_strings
    finally:
        sys.path.pop(0)

    b0_source = game_root / "dev-assets" / "B0" / "text" / "ko" / "strcombineall-jp.xml"
    b0_plain = b0_source.read_bytes()
    expected: dict[str, dict[Path, bytes]] = {
        "B0": {
            Path("Saves/B0/Text_JP/StrCombineAll.xml"): b0_plain,
            Path("Saves/B0/Text_JP/StrCombineAll.xml.e"): b1_strings.encrypted_copy(
                b0_plain, b23_strings.run_gust_enc
            ),
        }
    }
    b1_plain = b1_strings.merge_text()
    expected["B1"] = {
        b1_strings.RUNTIME_PLAIN: b1_plain,
        b1_strings.RUNTIME_ENCRYPTED: b1_strings.encrypted_copy(b1_plain),
        b1_strings.RUNTIME_GLOSSARY: b1_strings.serialize(b1_strings.read_xml(b1_strings.KO_GLOSSARY)),
    }
    for game in ("B2", "B3", "B4"):
        expected[game] = b23_strings.runtime_files(
            game, game_root, b23_strings.EXPECTED_TABLES, b23_strings.run_gust_enc
        )

    stale = []
    for game, files in expected.items():
        for relative, data in files.items():
            runtime = runtime_root / game / relative
            if not runtime.is_file() or runtime.read_bytes() != data:
                stale.append(f"{game}:{relative.as_posix()}")
    require(not stale, f"non-dialogue runtime files are stale: {', '.join(stale)}")


def verify_text_manifests(game_root: Path, runtime_root: Path) -> None:
    for game in ("B1", "B2", "B3", "B4"):
        source_root = game_root / "dev-assets" / game / "text" / "ko" / "event_jp"
        runtime_segment = runtime_root / game
        manifest_path = runtime_segment / "text-manifest.json"
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        entries = document.get("entries")
        require(document.get("game") == game and isinstance(entries, list), f"{game} text manifest is invalid")
        require(document.get("files") == len(entries), f"{game} text manifest file count is invalid")
        by_path = {entry.get("path"): entry for entry in entries if isinstance(entry, dict)}
        require(len(by_path) == len(entries), f"{game} text manifest contains duplicate paths")

        source_paths = sorted(path.relative_to(source_root) for path in source_root.rglob("*.json"))
        expected_paths = {
            (runtime_segment / "Event" / game / "event_JP" / relative.with_suffix(".ebm"))
            .relative_to(game_root)
            .as_posix()
            for relative in source_paths
        }
        require(set(by_path) == expected_paths, f"{game} text manifest does not match current Korean JSON files")

        runtime_lines = (runtime_segment / "manifest.txt").read_text(encoding="utf-8-sig").splitlines()
        for relative in source_paths:
            source = source_root / relative
            output = runtime_segment / "Event" / game / "event_JP" / relative.with_suffix(".ebm")
            path = output.relative_to(game_root).as_posix()
            entry = by_path[path]
            require(entry.get("source_json_sha256") == sha256(source), f"{game} EBM source is stale: {relative}")
            require(output.is_file() and entry.get("ebm_sha256") == sha256(output),
                    f"{game} EBM hash differs: {relative}")
            runtime_name = output.relative_to(runtime_segment).as_posix()
            require(runtime_lines.count(runtime_name) == 1,
                    f"{game} runtime manifest does not contain exactly one {runtime_name}")


def verify_font_asset(label: str, expected_hash: object, generated: Path, runtime: Path) -> None:
    require(isinstance(expected_hash, str) and re.fullmatch(r"[0-9a-f]{64}", expected_hash) is not None,
            f"{label} summary hash is invalid")
    require(generated.is_file() and sha256(generated) == expected_hash,
            f"{label} generated file differs from the font summary")
    require(runtime.is_file() and sha256(runtime) == expected_hash,
            f"{label} runtime copy differs from the generated file")


def verify_font_summaries(game_root: Path, runtime_root: Path, require_current_corpus: bool) -> None:
    current_corpus_hashes = None
    stale_corpora = []
    if require_current_corpus:
        _check_b0, _inspect_b1, _inspect_game, current_corpus_hashes, _check_dialogue = (
            load_project_tools(game_root)
        )
    for game in SEGMENTS:
        output = game_root / "dev-assets" / game / "font" / "generated" / "nanumsquare-regular-current-ko"
        require(not (output / "BUILD_INVALID.json").exists(), f"{game} font build is marked invalid")
        summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
        font = summary.get("font", {})
        require(
            font.get("name") == "NanumSquare Neo Bold"
            and str(font.get("path", "")).endswith("NanumSquareNeo-cBd.ttf")
            and font.get("sha256") == "4749fa5691157cf56a59d297b45e88894a646846048018cd7a4117ffb2869767",
            f"{game} font source is invalid",
        )
        if require_current_corpus:
            expected = summary["inputs" if game == "B1" else "corpus"]
            require(current_corpus_hashes is not None, "font corpus checker is unavailable")
            current_json, current_messages = current_corpus_hashes(game)
            if (current_json != expected.get("json_snapshot_sha256") or
                    current_messages != expected.get("message_snapshot_sha256")):
                stale_corpora.append(game)
        if game == "B1":
            verify_font_asset(
                "B1 G1T",
                summary["runtime_assets"]["g1t"].get("sha256"),
                output / "mainfonthd720_0.g1t",
                runtime_root / game / "Font" / "MainFontHD720_0.g1t",
            )
            verify_font_asset(
                "B1 glyph table",
                summary["glyphs"].get("extension_table_sha256"),
                output / "ko-glyph-table-0x1c.bin",
                runtime_root / game / "Font" / "ko-glyph-table-0x1c.bin",
            )
            continue
        for slot, record in summary["slots"].items():
            suffix = slot[-2:]
            verify_font_asset(
                f"{game} {slot} G1T",
                record["g1t"].get("sha256"),
                output / f"mainfont{suffix}_hd1080_0.g1t",
                runtime_root / game / "Font" / f"MainFont{suffix}_HD1080_0.g1t",
            )
            verify_font_asset(
                f"{game} {slot} glyph table",
                record.get("extension_table_sha256"),
                output / f"{slot}-glyph-table-0x48.bin",
                runtime_root / game / "Font" / f"{slot}-glyph-table-0x48.bin",
            )
    require(not stale_corpora,
            f"font does not match the current Korean text and translation tables: {', '.join(stale_corpora)}")


def verify_b1_menu_font(game_root: Path, runtime_root: Path) -> None:
    tools = game_root / "tools"
    sys.path.insert(0, str(tools))
    try:
        import build_b1_menu_font as menu_font
    finally:
        sys.path.pop(0)
    require(Path(menu_font.__file__).resolve() == (tools / "build_b1_menu_font.py").resolve(),
            "B1 menu font checker was imported from the wrong path")
    corpus_module = sys.modules.get(menu_font.collect_hangul.__module__)
    require(corpus_module is not None and
            Path(corpus_module.__file__).resolve() == (tools / "font_corpus.py").resolve(),
            "B1 menu font corpus checker was imported from the wrong path")
    require(menu_font.SOURCE_SHA256 == B1_MENU_FONT_SOURCE_SHA256,
            "B1 menu font source hash constant changed")
    require(__debug__, "B1 menu font structural checks require Python assertions")

    source = game_root / "dev-assets/B1/font/source/x64/res_jp/layout/default.g1n"
    generated_root = game_root / "dev-assets/B1/font/generated/menu-g1n"
    generated = generated_root / "default.g1n"
    runtime = runtime_root / "B1/Layout/default.g1n"
    require(source.is_file() and sha256(source) == B1_MENU_FONT_SOURCE_SHA256,
            "B1 menu font source differs from the verified Japanese G1N")
    require(generated.is_file() and runtime.is_file(), "B1 menu font generated or runtime file is missing")

    original_data = source.read_bytes()
    generated_data = generated.read_bytes()
    require(runtime.read_bytes() == generated_data,
            "B1 menu font runtime copy differs from the generated file")
    corpus = menu_font.collect_corpus()
    require(corpus.characters, "B1 menu font has no Korean characters to verify")
    try:
        faces, _payload = menu_font.faces(generated_data)
        menu_font.check(original_data, generated_data, corpus.characters)
    except AssertionError as error:
        raise RuntimeError("B1 menu font cmap, glyph records, or payload differ") from error

    summary = json.loads((generated_root / "summary.json").read_text(encoding="utf-8"))
    output_hash = hashlib.sha256(generated_data).hexdigest()
    require(
        summary.get("source_sha256") == B1_MENU_FONT_SOURCE_SHA256
        and summary.get("output_sha256") == output_hash
        and summary.get("bytes") == len(generated_data)
        and summary.get("faces") == len(faces) == 2
        and summary.get("hangul_per_face") == len(corpus.characters)
        and summary.get("original_glyphs_and_pixels_preserved") is True
        and summary.get("codec") == "4bpp, original palette"
        and summary.get("corpus_sha256") == corpus.snapshot_sha256,
        "B1 menu font summary differs from the verified files, Korean text, or translation table",
    )
    lines = (runtime_root / "B1/manifest.txt").read_text(encoding="utf-8-sig").splitlines()
    require(lines.count("Layout/default.g1n") == 1,
            "B1 menu font must occur once in the runtime manifest")


def verify_b1_warning(game_root: Path, runtime_root: Path) -> None:
    source = game_root / "dev-assets" / "B1" / "image" / "ko" / "x64" / "res_jp" / "ipu" / "headsup.g1t"
    runtime = runtime_root / "B1" / "data" / "b1" / "x64" / "res_jp" / "ipu" / "headsup.g1t"
    require(source.is_file() and runtime.is_file(), "B1 Korean warning image is missing")
    require(sha256(source) == B1_WARNING_SHA256 and sha256(runtime) == B1_WARNING_SHA256,
            "B1 Korean warning image hash differs")
    lines = (runtime_root / "B1" / "manifest.txt").read_text(encoding="utf-8-sig").splitlines()
    require(lines.count("data/b1/x64/res_jp/ipu/headsup.g1t") == 1,
            "B1 warning image must occur once in the runtime manifest")


def verify_embedded_resources(
    binary: bytes,
    rcdata: dict[int, tuple[int, int]],
    assets: list[object],
    glyph_tables: list[object],
    variants: list[object] = (),
) -> None:
    expected = {item.resource_id: item.source for item in (*assets, *glyph_tables, *variants)}
    require(len(expected) == len(assets) + len(glyph_tables) + len(variants), "embedded resource IDs are duplicated")
    require(set(rcdata) == set(expected), "PE RCDATA resource IDs differ from the generated manifest")
    for resource_id, source in expected.items():
        raw, size = rcdata[resource_id]
        require(binary[raw : raw + size] == source.read_bytes(),
                f"resource {resource_id} differs from its generated source")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--game-root", required=True, type=Path)
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--signatures", required=True, type=Path)
    parser.add_argument("--glyph-signatures", required=True, type=Path)
    parser.add_argument("--original-hashes", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--installed-hashes", type=Path, help="explicit before-build snapshot, keeping the original baseline separate")
    parser.add_argument("--embedded", required=True, choices=("0", "1"))
    parser.add_argument("--preflight", action="store_true", help="check deployment without writing a report")
    parser.add_argument("--minhook-root", type=Path)
    parser.add_argument("--font-output", type=Path)
    parser.add_argument("--font-ttf", type=Path)
    parser.add_argument("--msdf-atlas-gen", type=Path)
    parser.add_argument("--gust-g1t", type=Path)
    parser.add_argument("--translations-root", type=Path, required=True)
    parser.add_argument("--target-language", required=True)
    parser.add_argument("--llvm-mingw-root", type=Path)
    args = parser.parse_args()

    binary = args.binary.read_bytes()
    pe = parse_pe(binary)
    require(pe["machine"] == IMAGE_FILE_MACHINE_AMD64, "output machine is not AMD64")
    require(pe["characteristics"] & IMAGE_FILE_DLL, "output is not a DLL")
    required_flags = (
        IMAGE_DLLCHARACTERISTICS_HIGH_ENTROPY_VA
        | IMAGE_DLLCHARACTERISTICS_DYNAMIC_BASE
        | IMAGE_DLLCHARACTERISTICS_NX_COMPAT
    )
    require(pe["dllCharacteristics"] & required_flags == required_flags, "required PE safety flags are missing")
    require(all(pe["baseRelocations"]), "base relocation directory is missing")
    require(pe["exportFunctionCount"] == 1 and pe["exportNames"] == ["InitializeASI"],
            "the ASI must export only InitializeASI")
    require(not any(name.lower() in {f"b{index}.dll" for index in range(5)} for name in pe["imports"]),
            "B modules must be found at runtime, not imported")
    local_paths = {str(args.project).encode(), str(args.game_root).encode()}
    require(not any(path in binary for path in local_paths) and b"C:\\Users\\" not in binary,
            "the binary contains a local user path")

    signatures = json.loads(args.signatures.read_text(encoding="utf-8"))
    verify_module_constants(args.project, args.game_root, signatures, binary)
    original_counts, excluded_added_files = verify_original_files(args.original_hashes, args.game_root, signatures,
                                                                 args.installed_hashes)
    glyph_signatures = json.loads(args.glyph_signatures.read_text(encoding="utf-8"))
    verify_glyph_constants(args.project, glyph_signatures)
    verify_glyph_binaries(args.game_root, glyph_signatures)
    verify_b1_font_descriptor(args.project, args.game_root, args.font_output)
    if args.font_output:
        require(all((args.font_ttf, args.msdf_atlas_gen, args.gust_g1t)), 'public font checks require the selected TTF and native tool paths')
        subprocess.run([sys.executable, '-B', str(args.project / 'tools/fonts/build_font_assets.py'),
                        '--game-root', str(args.game_root), '--output-root', str(args.font_output),
                        '--runtime-root', str(args.runtime_root), '--font', str(args.font_ttf),
                        '--msdf-atlas-gen', str(args.msdf_atlas_gen), '--gust-g1t', str(args.gust_g1t),
                        '--translations-root', str(args.translations_root), '--target-language', args.target_language,
                        '--all', '--check-only'], check=True)
    else:
        verify_font_summaries(args.game_root, args.runtime_root, args.preflight or args.embedded == '1')
        verify_b1_menu_font(args.game_root, args.runtime_root)
    assets = load_manifests(args.runtime_root)
    graphic_overrides = load_graphics_overrides(args.runtime_root, assets)
    if args.embedded == "1":
        verify_translations(args.game_root)
        verify_non_dialogue_runtime(args.game_root, args.runtime_root)
        verify_text_manifests(args.game_root, args.runtime_root)
        if not any(item['segment'] == 'B1' and item['gamePath'].lower() ==
                   'data/b1/x64/res_jp/ipu/headsup.g1t' for item in graphic_overrides):
            verify_b1_warning(args.game_root, args.runtime_root)
    glyph_tables = load_glyph_tables(args.runtime_root, 1000 + len(assets))
    require(valid_path("data/b4/x64/res_jp/ui/b0_title.g1t", "B0") and
            valid_path("data/b4/x64/res_jp/ui/b0_title.g1t", "B4") and
            not valid_path("data/b4/x64/res_jp/ui/b0_title.g1t", "B1"),
            "title graphics must remain in their game namespace")
    require(valid_path("Saves/B0/Text_JP/StrCombineAll.xml", "B0") and
            valid_path("Saves/B0/Text_JP/StrCombineAll.xml.e", "B0") and
            valid_path("Res_JP/Ipu/logo_kt.g1t", "B0") and
            not valid_path("Saves/B0/Event/EventGlossaryKeyword.xml", "B0") and
            not valid_path("Res_JP/Ipu/logo_gust.g1t", "B0") and
            not valid_path("Font/SubFont_hd1080.g1t", "B0") and
            not valid_path("Font/ExternalChars_HD1080_0.g1t", "B0") and
            not valid_path("Saves/B0/Text_JP/StrCombineAll.xml", "B1") and
            not valid_path("Saves/B0/Text_EN/StrCombineAll.xml", "B0"),
            "the B0 combined string allowlist is not exact")
    require(valid_path("Saves/B1/Text_JP/StrCombineAll.xml", "B1") and
            valid_path("Saves/B1/Text_JP/StrCombineAll.xml.e", "B1") and
            valid_path("Saves/B1/Event/EventGlossaryKeyword.xml", "B1") and
            valid_path("data/b1/x64/res_jp/ipu/headsup.g1t", "B1") and
            valid_path("Layout/default.g1n", "B1") and
            not valid_path("Layout/default.g1n", "B0") and
            not valid_path("Layout/other.g1n", "B1") and
            not valid_path("Font/SubFont_hd720.g1t", "B1") and
            valid_path("data/b1/x64/res_jp/ipu/logo_gust.g1t", "B1") and
            not valid_path("Saves/B1/Text_EN/StrCombineAll.xml", "B1"),
            "the B1 text and warning image allowlist is not exact")
    for segment in ("B2", "B3", "B4"):
        prefix = f"Saves/{segment}"
        require(valid_path(f"{prefix}/Text_JP/StrCombineAll.xml", segment) and
                valid_path(f"{prefix}/Text_JP/StrCombineAll.xml.e", segment) and
                valid_path(f"{prefix}/Event/EventGlossaryKeyword.xml", segment) and
                valid_path(f"{prefix}/ui_CMN/seed_cabinet/uil_seed_cabinet_cell.xml", segment) and
                valid_path(f"{prefix}/ui_CMN/seed_dlc_battle/uil_seed_dlc_battle.xml", segment) and
                not valid_path("Font/SubFont_hd1080.g1t", segment) and
                not valid_path("Font/ExternalChars_HD1080_0.g1t", segment) and
                not valid_path(f"{prefix}/Text_EN/StrCombineAll.xml", segment) and
                not valid_path(f"{prefix}/ui_CMN/other.xml", segment),
                f"the {segment} text allowlist is not exact")
    if args.embedded == "1":
        validate_release_counts(assets, glyph_tables)
        expected_rcdata = len(assets) + len(glyph_tables)
        require(
            pe["rcdataResources"] == expected_rcdata,
            f"PE RCDATA count is {pe['rcdataResources']}; expected {expected_rcdata}",
        )
        verify_embedded_resources(binary, pe["rcdata"], assets, glyph_tables)

    distribution = args.project / "dist" / "embedded" if args.preflight else args.binary.parent
    if args.preflight:
        staged_binary = distribution / "BRQuarteTranslateHelper.asi"
        require(staged_binary.is_file() and staged_binary.read_bytes() == binary,
                "game BRQuarteTranslateHelper.asi differs from the verified embedded build")
    minhook_license = (args.minhook_root or args.project / "external/minhook") / "LICENSE.txt"
    require(minhook_license.is_file(), "MinHook license is missing")
    require((distribution / "LICENSE-MinHook.txt").read_bytes() == minhook_license.read_bytes(),
            "the built MinHook license copy differs from the source")
    if args.llvm_mingw_root:
        require((distribution / "LICENSE-LLVM-MinGW.txt").read_bytes() ==
                (args.llvm_mingw_root / "LICENSE.TXT").read_bytes(),
                "the built LLVM-MinGW license copy differs from the source")
    notices = (args.project / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    require("v1.3.4" in notices and "c3fcafdc10146beb5919319d0683e44e3c30d537" in notices,
            "MinHook version is missing from the notices")
    require("20260826" in notices and "48bedd161f14ae25a3646cb750b57ee3188e97e34bd3c52240c1810aa74d6a7f" in notices,
            "LLVM-MinGW version or archive hash is missing from the notices")


    checks = [
        "five Open and file-size records match loader-analysis/signatures.json and the original DLLs",
        "five glyph lookup records match font-analysis/runtime-lookup-signatures.json",
        "each glyph lookup signature occurs once at the verified offset in its original DLL",
        "B1 descriptor RVA maps to read-only .rdata bytes 00 08 00 04 with untouched count 05 0a",
        "B1 generated font summary and loader constants both require 2048x2048",
        "all verified 64-byte Open contexts are present in the ASI",
        "the PE has base relocations, safety flags, and only the InitializeASI export",
        "runtime manifests contain only segment-specific Japanese EBM, approved fonts, and exact B0 special files",
        "glyph tables have valid sizes, strictly increasing keys, and are embedded when requested",
        "font summary hashes match every generated G1T and glyph table and their runtime copies",
        "B1 menu G1N source, generated structure, runtime copy, and manifest match; embedded builds check its bytes",
        "the ASI contains no full user path",
        "launcher, B0-B4 DLLs, steam_api64.dll and 22 PAKs match " +
        ("the explicit installed before-build snapshot (including user mods)" if args.installed_hashes else "the original baseline"),
        "MinHook and the selected build/asset dependencies have their licenses and notices recorded",
    ]
    if args.embedded == "1":
        checks.extend([
            "base B0-B4 translation corpus passes checks; untranslated DLC events retain game originals",
            "B0-B4 non-dialogue runtime files match the current Korean stage output",
            "B1-B4 text manifests match the current Korean JSON and EBM hashes",
            "B0-B4 font summaries match the current Korean text and translation-table corpus",
            "the B1 Korean warning image has the approved hash and one runtime entry",
        ])
        checks.append(f"all {expected_rcdata} PE RCDATA IDs and bytes match the generated resources")

    report = {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "binary": {
            "name": args.binary.name,
            "sha256": hashlib.sha256(binary).hexdigest(),
            "size": len(binary),
            "machine": "AMD64",
            "pe32Plus": True,
            "dll": True,
            "safetyFlags": ["HIGH_ENTROPY_VA", "DYNAMIC_BASE", "NX_COMPAT"],
            "baseRelocations": True,
            "exports": pe["exportNames"],
            "imports": pe["imports"],
            "rcdataResources": pe["rcdataResources"],
        },
        "assetMode": "embedded" if args.embedded == "1" else "external",
        "manifestEntries": len(assets),
        "manifestEntriesBySegment": {
            segment: sum(asset.segment == segment for asset in assets) for segment in SEGMENTS
        },
        "glyphTables": len(glyph_tables),
        "glyphTablesBySegment": {
            segment: sum(table.segment == segment for table in glyph_tables) for segment in SEGMENTS
        },
        "originalFiles": {
            "comparison": "installed snapshot" if args.installed_hashes else "original baseline",
            "checked": sum(original_counts.values()),
            "byKind": original_counts,
            "excludedAddedDeploymentFiles": excluded_added_files,
        },
        "checks": checks,
    }
    if not args.preflight:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"static checks passed: {len(report['checks'])}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        raise SystemExit(f"Check failed: {error}") from error
