"""Export blank translation tables from the original Quartet PAK files."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
import json
from html import unescape
from pathlib import Path
import re
import subprocess
import tempfile

try:
    from .pak_source import Member, members, read_member
    from .runtime_text import sources_from_module
    from .bsb import is_choice_path, read as read_choices
    from .kscl import is_layout_path, read_metadata, strings as layout_strings
except ImportError:  # The tools directory is also used as a script directory.
    from pak_source import Member, members, read_member
    from runtime_text import sources_from_module
    from bsb import is_choice_path, read as read_choices
    from kscl import is_layout_path, read_metadata, strings as layout_strings


SEGMENTS = ("B0", "B1", "B2", "B3", "B4")
PAK_NAMES = {
    "B0": ("PACK02_03.PAK",),
    "B1": ("PACK10_04_03.PAK", "PACK11.PAK", "PACK12.PAK"),
    "B2": ("PACK01_01.PAK", "PACK02_01.PAK"),
    "B3": ("PACK01_02.PAK", "PACK02_02.PAK"),
    "B4": ("PACK01_03.PAK", "PACK02_03.PAK"),
}

_JAPANESE = re.compile(r"[ぁ-ゖァ-ヺ一-龯々〆ヶ]")
_LANGUAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}\Z")
_MSG_STRING = re.compile(r'"msg_string"\s*:\s*("(?:\\.|[^"\\])*")')
_XML_STRING_FIELDS = frozenset(("text", "jp"))
# Game UI files allow raw display tags in attributes and multiple root elements.
_XML_ATTRIBUTE = re.compile(
    r"<!--.*?-->|(?P<name>[A-Za-z_][\w:.-]*)\s*=\s*(?P<quote>[\"'])"
    r"(?P<value>.*?)(?P=quote)", re.DOTALL)


def _language_name(language: str) -> str:
    if not isinstance(language, str) or _LANGUAGE.fullmatch(language) is None:
        raise ValueError("Language must be a simple file-name component")
    return language


def _regular_file(path: Path, label: str) -> Path:
    path = Path(path)
    if path.is_symlink() or not path.is_file() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError(f"{label} is not a regular file: {path}")
    return path


def _source_directory(path: Path) -> Path:
    path = Path(path)
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"PAK directory is not a regular directory: {path}")
    return path


def _output_directory(path: Path) -> Path:
    path = Path(path)
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"Output must be an existing directory: {path}")
    if any(parent.is_symlink() for parent in path.parents):
        raise ValueError(f"Output path contains a symlink: {path}")
    return path


def _find_pak(root: Path, name: str) -> Path:
    """Find a PAK by name without depending on the host case rules."""
    candidates = (root, root / "Data")
    for directory in candidates:
        if not directory.is_dir() or directory.is_symlink():
            continue
        direct = directory / name
        if direct.is_file() and not direct.is_symlink():
            return _regular_file(direct, f"Source PAK {name}")
        wanted = name.casefold()
        for entry in directory.iterdir():
            if entry.name.casefold() == wanted and entry.is_file() and not entry.is_symlink():
                return _regular_file(entry, f"Source PAK {name}")
    raise ValueError(f"Source PAK is missing: {name}")


def _xml_text(data: bytes, label: str) -> str:
    for encoding in ("utf-8-sig", "cp932"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise ValueError(f"Cannot decode XML source {label}")


def _is_japanese(value: str) -> bool:
    return bool(_JAPANESE.search(value))


def _xml_strings(data: bytes, label: str) -> set[str]:
    return {value for match in _XML_ATTRIBUTE.finditer(_xml_text(data, label))
            if (match['name'] or '').casefold() in _XML_STRING_FIELDS
            and _is_japanese(value := unescape(match['value']))}


def _xml_game_id(label: str) -> str:
    logical = label.casefold()
    if not re.match(r'^saves/b[0-4]/', logical):
        raise ValueError(f'XML game selection requires a game-relative saves/b0..b4 path: {label}')
    return 'BR' if logical.startswith('saves/b1/') else 'A21'


def _run_converter(tool: Path, ebm: Path, label: str) -> Path:
    try:
        result = subprocess.run(
            [str(tool), str(ebm)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise ValueError(f"Could not run gust_ebm for {label}: {error}") from error
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or str(result.returncode)
        raise ValueError(f"gust_ebm failed for {label}: {detail}")
    converted = ebm.with_suffix(".json")
    if not converted.is_file() or converted.is_symlink():
        raise ValueError(f"gust_ebm did not create JSON for {label}")
    return converted


def _ebm_strings(
    data: bytes,
    label: str,
    tool: Path,
    work: Path,
    number: int,
) -> set[str]:
    ebm = work / f"event-{number:06d}.ebm"
    ebm.write_bytes(data)
    converted = _run_converter(tool, ebm, label)
    try:
        text = converted.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ValueError(f"Cannot read gust_ebm output for {label}: {error}") from error
    if '"messages"' not in text:
        raise ValueError(f"Malformed gust_ebm output for {label}")
    result: set[str] = set()
    for match in _MSG_STRING.finditer(text):
        try:
            value = json.loads(match.group(1))
        except json.JSONDecodeError as error:
            raise ValueError(f"Malformed msg_string in gust_ebm output for {label}") from error
        if not isinstance(value, str):
            raise ValueError(f"msg_string is not a string for {label}")
        if _is_japanese(value):
            result.add(value)
    return result


def _xml_candidates(path: str) -> tuple[str, str] | None:
    """Return (kind, logical path) for one supported XML member."""
    lowered = path.casefold()
    suffix = ""
    if lowered.endswith(".xml.e"):
        logical = lowered[:-2]
        suffix = "encrypted"
    elif lowered.endswith(".xml"):
        logical = lowered
        suffix = "plain"
    else:
        return None
    parts = logical.split('/')
    if len(parts) < 4 or parts[0] != "saves" or parts[1].upper() not in SEGMENTS:
        return None
    if parts[2] in ("text_jp", "ui", "ui_cmn"):
        return suffix, logical
    if parts[2:] == ["event", "eventglossarykeyword.xml"]:
        return suffix, logical
    return None


def _member_map(stream) -> dict[str, Member]:
    result: dict[str, Member] = {}
    for member in members(stream):
        result[member.path.as_posix().casefold()] = member
    return result


def _archive_signature(path: Path) -> tuple[int, int, int, int]:
    try:
        stat_result = path.stat()
    except OSError as error:
        raise ValueError(f"Cannot stat source PAK: {path}") from error
    return (
        stat_result.st_dev,
        stat_result.st_ino,
        stat_result.st_size,
        stat_result.st_mtime_ns,
    )


def _decode_encrypted_xml(
    data: bytes,
    label: str,
    tool: Path,
    work: Path,
    number: int,
) -> bytes:
    encrypted = work / f"xml-{number:06d}.xml.e"
    encrypted.write_bytes(data)
    try:
        result = subprocess.run(
            [str(tool), "-" + _xml_game_id(label), str(encrypted)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise ValueError(f"Could not run gust_enc for {label}: {error}") from error
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or str(result.returncode)
        raise ValueError(f"gust_enc failed for {label}: {detail}")
    plain = encrypted.with_suffix("")
    if not plain.is_file() or plain.is_symlink():
        raise ValueError(f"gust_enc did not create XML for {label}")
    try:
        return plain.read_bytes()
    except OSError as error:
        raise ValueError(f"Cannot read gust_enc output for {label}: {error}") from error


def _collect_segment(
    segment: str,
    archives: Iterable[Path],
    gust_ebm: Path | None,
    gust_enc: Path | None,
    work: Path,
) -> tuple[set[str], int, int, int, int]:
    """Collect source strings and XML, EBM, and KSCL file counts."""
    result: set[str] = set()
    xml_files = 0
    ebm_files = 0
    kscl_files = 0
    bsb_files = 0
    xml_candidates: dict[str, dict[str, tuple[Path, Member]]] = {}
    ebm_members: list[tuple[Path, Member]] = []
    segment_prefix = segment.casefold()
    archive_signatures = {archive: _archive_signature(archive) for archive in archives}
    for archive in archives:
        with archive.open("rb") as stream:
            selected = _member_map(stream)
            for path, member in selected.items():
                if is_choice_path(path, segment):
                    result.update(v for group in read_choices(read_member(stream, member))
                                  for v in group if _is_japanese(v))
                    bsb_files += 1
                if is_layout_path(path, segment):
                    result.update(value for value in layout_strings(read_metadata(stream, member))
                                  if _is_japanese(value))
                    kscl_files += 1
                candidate = _xml_candidates(path)
                if candidate is not None:
                    kind, logical = candidate
                    # Restrict candidates to this segment. Shared PACK02_03.PAK
                    # contains both B0 and B4 trees.
                    if f"/saves/{segment_prefix}/" not in f"/{logical}":
                        continue
                    xml_candidates.setdefault(logical, {})[kind] = (archive, member)
                if (
                    path.startswith(f"event/{segment_prefix}/event_jp/")
                    and path.endswith(".ebm")
                ):
                    ebm_members.append((archive, member))

    for logical in sorted(xml_candidates, key=str.casefold):
        candidates = xml_candidates[logical]
        selected_archive, member = candidates.get("plain") or candidates.get("encrypted")
        with selected_archive.open("rb") as stream:
            raw = read_member(stream, member)
        if "plain" not in candidates:
            if gust_enc is None:
                raise ValueError(f"Encrypted XML needs gust_enc: {logical}")
            raw = _decode_encrypted_xml(raw, logical, gust_enc, work, xml_files)
        result.update(_xml_strings(raw, logical))
        xml_files += 1

    if ebm_members:
        if gust_ebm is None:
            raise ValueError("EBM sources were found but gust_ebm was not provided")
        for index, (archive, member) in enumerate(sorted(ebm_members, key=lambda item: item[1].path.as_posix().casefold())):
            with archive.open("rb") as stream:
                raw = read_member(stream, member)
            result.update(_ebm_strings(raw, member.path.as_posix(), gust_ebm, work, index))
            ebm_files += 1
    for archive, before in archive_signatures.items():
        if _archive_signature(archive) != before:
            raise ValueError(f"Source PAK changed during extraction: {archive}")
    return result, xml_files, ebm_files, kscl_files, bsb_files


def _write_json(path: Path, values: set[str]) -> None:
    content = json.dumps(
        {value: "" for value in sorted(values)},
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    try:
        with path.open("x", encoding="utf-8", newline="") as stream:
            stream.write(content)
    except FileExistsError as error:
        raise ValueError(f"Refusing to overwrite existing output: {path}") from error


def export_text(
    pak_root: Path,
    output: Path,
    language: str,
    gust_ebm: Path | None,
    gust_enc: Path | None,
) -> dict[str, object]:
    """Export five blank source-key tables from read-only PAK input.

    ``pak_root`` may be either the directory containing the PAK files or its
    game-root parent containing a ``Data`` directory.  ``output`` must be an
    existing build-owned stage directory.  Existing language files are never
    overwritten.
    """
    language = _language_name(language)
    source = _source_directory(Path(pak_root))
    destination = _output_directory(Path(output))
    ebm_tool = _regular_file(Path(gust_ebm), "gust_ebm") if gust_ebm is not None else None
    enc_tool = _regular_file(Path(gust_enc), "gust_enc") if gust_enc is not None else None

    targets = [destination / f"{segment}.{language}.json" for segment in SEGMENTS]
    for target in targets:
        if target.exists() or target.is_symlink():
            raise ValueError(f"Refusing to overwrite existing output: {target}")

    archive_paths: dict[str, Path] = {}
    for names in PAK_NAMES.values():
        for name in names:
            archive_paths.setdefault(name, _find_pak(source, name))

    # The converter emits a sidecar next to each temporary EBM.  Keeping all
    # scratch files below the output parent makes the PAK input strictly read-only.
    with tempfile.TemporaryDirectory(prefix="brq-export-text-", dir=destination.parent) as folder:
        work = Path(folder)
        values: dict[str, set[str]] = {}
        report: dict[str, dict[str, int]] = {}
        for segment in SEGMENTS:
            archives = [archive_paths[name] for name in PAK_NAMES[segment]]
            segment_work = work / segment.lower()
            segment_work.mkdir()
            collected, xml_files, ebm_files, kscl_files, bsb_files = _collect_segment(
                segment, archives, ebm_tool, enc_tool, segment_work
            )
            module = next((p for p in (source / (segment + ".dll"), source.parent / (segment + ".dll"))
                           if p.is_file() and not p.is_symlink()), None)
            dll_sources = sources_from_module(module, segment) if module else set()
            collected.update(dll_sources)
            values[segment] = collected
            report[segment] = {
                "keys": len(collected),
                "xml_files": xml_files,
                "kscl_files": kscl_files,
                "bsb_files": bsb_files,
                "dll_strings": len(dll_sources),
                "ebm_files": ebm_files,
            }
            print(
                f"Collected {segment}: {len(collected)} keys from "
                f"{xml_files} XML, {ebm_files} EBM, {kscl_files} KSCL and {bsb_files} BSB files",
                flush=True,
            )

    # All source parsing and conversion succeeds before the first output file
    # is created.  Exclusive creation also protects against a late race.
    for segment, target in zip(SEGMENTS, targets):
        _write_json(target, values[segment])
    return {
        "language": language,
        "files": len(targets),
        "keys": sum(item["keys"] for item in report.values()),
        "segments": report,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pak-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--language", required=True)
    parser.add_argument("--gust-ebm", type=Path)
    parser.add_argument("--gust-enc", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            export_text(args.pak_root, args.output, args.language, args.gust_ebm, args.gust_enc),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
