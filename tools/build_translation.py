#!/usr/bin/env python3
"""Build a translation workspace against a selected, read-only game installation."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from html import escape, unescape

from export_text import (PAK_NAMES, SEGMENTS, _archive_signature, _decode_encrypted_xml,
                         _xml_text, _XML_ATTRIBUTE, _XML_STRING_FIELDS, _xml_game_id,
                         _find_pak, _regular_file, _run_converter, _xml_candidates)
from pak_source import members, read_member
from bsb import is_choice_path, rewrite as rewrite_choices
from kscl import is_layout_path, read_metadata, rewrite as rewrite_layout
from translation_table import load_workspace, unique_object, valid_pair
from generate_embedded import load_manifests, load_glyph_tables, valid_path, _safe_path
from prepare_runtime import paths_overlap

MARKER = '.brq-translation-build'
MESSAGE = re.compile(r'("msg_string"\s*:\s*)("(?:\\.|[^"\\])*")')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def run(tool, *args, cwd=None):
    result = subprocess.run([str(tool), *map(str, args)], stdin=subprocess.DEVNULL, capture_output=True, text=True, cwd=cwd, errors="replace")
    if result.returncode:
        raise ValueError(result.stderr.strip() or result.stdout.strip() or 'Conversion failed')


def translated_xml(raw, mapping, label, contexts=None):
    text = _xml_text(raw, label)
    original_text = text
    overrides = {}
    def replace(match):
        if (match['name'] or '').casefold() not in _XML_STRING_FIELDS:
            return match[0]
        value = unescape(match['value'])
        after = overrides.get(match.start('value'), mapping.get(value, value))
        if after == value:
            return match[0]
        start = match.start('value') - match.start()
        end = match.end('value') - match.start()
        return match[0][:start] + escape(after, quote=True) + match[0][end:]
    # Context entries distinguish identical source text used by different UI rows.
    if contexts:
        seen = set()
        for match in re.finditer(r"<!--.*?-->|<(?:str|text)\b(?:[^>\"']|\"[^\"]*\"|'[^']*')*>", text, re.DOTALL):
            row = match[0]
            if row.startswith('<!--'):
                continue
            attrs = {a['name']: unescape(a['value']) for a in _XML_ATTRIBUTE.finditer(row)}
            is_text_node = row.startswith('<text')
            identity = 'name:' + attrs.get('name', '') if is_text_node else attrs.get('String_ID')
            entry = contexts.get(identity)
            if entry is None:
                continue
            field = 'text' if is_text_node else 'Text'
            if identity in seen or attrs.get(field) != entry['source']:
                raise ValueError(f'Context source changed or repeated: {label}/{identity}')
            seen.add(identity)
            for attr in _XML_ATTRIBUTE.finditer(row):
                if attr['name'] == field:
                    overrides[match.start() + attr.start('value')] = entry['translation']
        if set(contexts) != seen:
            raise ValueError(f'XML context entries are missing: {label}/{sorted(set(contexts) - seen)}')
    translated = _XML_ATTRIBUTE.sub(replace, text)
    if translated == original_text:
        return None
    translated = re.sub(r'(\A\s*<\?xml\b[^?]*\bencoding\s*=\s*)[\"\'][^\"\']+[\"\']',
                        r'\1"utf-8"', translated, count=1, flags=re.I)
    return translated.encode('utf-8')


def valid_context_pair(source, translation):
    # An explicit empty override removes non-dialogue text, unlike a blank table entry.
    return valid_pair(source, '.' if translation == '' else translation)


def load_xml_contexts(workspace, segment, language):
    path = workspace / f'{segment}.{language}.xml-context.json'
    if not path.exists():
        return {}
    _regular_file(path, 'XML context translations')
    contexts = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique_object)
    if not isinstance(contexts, dict):
        raise ValueError('XML contexts must be an object')
    for relative, entries in contexts.items():
        if not valid_path(relative, segment) or not relative.endswith('.xml') or not isinstance(entries, dict):
            raise ValueError(f'Invalid XML context path: {relative}')
        for identity, entry in entries.items():
            if (not identity or not isinstance(entry, dict)
                    or set(entry) != {'source', 'translation'}
                    or not valid_context_pair(entry['source'], entry['translation'])):
                raise ValueError(f'Invalid XML context entry: {identity}')
    return contexts


def load_ebm_contexts(workspace, segment, language):
    path = workspace / f'{segment}.{language}.ebm-context.json'
    if not path.exists():
        return {}
    _regular_file(path, 'EBM context translations')
    contexts = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique_object)
    if not isinstance(contexts, dict):
        raise ValueError('EBM contexts must be an object')
    for relative, entries in contexts.items():
        if (not valid_path(relative, segment) or not relative.endswith('.ebm')
                or not isinstance(entries, dict)):
            raise ValueError(f'Invalid EBM context path: {relative}')
        for number, entry in entries.items():
            if (not number.isdecimal() or str(int(number)) != number
                    or not isinstance(entry, dict)
                    or set(entry) != {'source', 'translation', 'name_id'}
                    or type(entry['name_id']) is not int or not 0 <= entry['name_id'] <= 0xffffffff
                    or not valid_context_pair(entry['source'], entry['translation'])):
                raise ValueError(f'Invalid EBM context entry: {relative}/{number}')
    return contexts


def translated_ebm(raw, mapping, tool, work, contexts=None):
    ebm = work / 'event.ebm'
    ebm.write_bytes(raw)
    converted = _run_converter(tool, ebm, 'translation build')
    source = converted.read_text(encoding='utf-8-sig')
    messages = list(MESSAGE.finditer(source))
    overrides = {}
    if contexts:
        speakers = re.findall(r'"name_id"\s*:\s*(0x[0-9a-fA-F]+|[0-9]+)', source)
        if len(speakers) != len(messages):
            raise ValueError('EBM speaker and message counts differ')
        for number, entry in contexts.items():
            index = int(number)
            if (not 0 <= index < len(messages)
                    or json.loads(messages[index][2]) != entry['source']
                    or int(speakers[index], 0) != entry['name_id']):
                raise ValueError(f'EBM context source or speaker changed: message {number}')
            overrides[messages[index].start()] = entry['translation']
    def replace(match):
        value = json.loads(match[2])
        return match[1] + json.dumps(overrides.get(match.start(), mapping.get(value, value)), ensure_ascii=False)
    translated = MESSAGE.sub(replace, source)
    if [m[2] for m in MESSAGE.finditer(translated)] == [m[2] for m in MESSAGE.finditer(source)]:
        return None
    converted.write_text(translated, encoding='utf-8')
    ebm.unlink()
    run(tool, converted)
    _regular_file(ebm, 'Rebuilt EBM')
    rebuilt = ebm.read_bytes()
    # Decode the actual rebuilt file; compare fields and metadata, not just the input JSON.
    converted.unlink()
    _run_converter(tool, ebm, 'rebuilt EBM')
    actual = converted.read_text(encoding='utf-8-sig')
    if MESSAGE.sub(lambda m: m[1] + '""', actual) != MESSAGE.sub(lambda m: m[1] + '""', source):
        raise ValueError('EBM conversion changed non-text metadata')
    if [json.loads(m[2]) for m in MESSAGE.finditer(actual)] != [json.loads(m[2]) for m in MESSAGE.finditer(translated)]:
        raise ValueError('Rebuilt EBM text differs from the translation')
    return rebuilt


def build_text(args, stage, tables, ebm_contexts=None):
    runtime = stage / 'runtime'
    profiles = {}
    for segment in SEGMENTS:
        event_contexts = (ebm_contexts or {}).get(segment, {})
        contexts = load_xml_contexts(args.translations_root, segment, args.language)
        root = runtime / segment
        root.mkdir(parents=True)
        selected = {}
        profiles[segment] = []
        for name in PAK_NAMES[segment]:
            archive = _find_pak(args.pak_root, name)
            profiles[segment].append({'path': archive.relative_to(args.game_root).as_posix(), 'sha256': digest(archive)})
            with archive.open('rb') as stream:
                for member in members(stream):
                    path = member.path.as_posix()
                    if valid_path(path, segment) and (path.lower().endswith('.ebm') or _xml_candidates(path) or is_layout_path(path, segment) or is_choice_path(path, segment)):
                        key = path.casefold()
                        if key in selected:
                            raise ValueError(f'Ambiguous source member: {path}')
                        selected[key] = (archive, member)
        missing_context_files = (set(event_contexts) | set(contexts)) - {str(member.path).removesuffix('.e') for _, member in selected.values()}
        if missing_context_files:
            raise ValueError(f'Translation context files are missing: {sorted(missing_context_files)}')
        outputs = []
        with tempfile.TemporaryDirectory(dir=stage) as temporary:
            work = Path(temporary)
            for key, (archive, member) in sorted(selected.items()):
                if key.endswith('.xml.e') and key[:-2] in selected:
                    continue
                path = member.path.as_posix()
                with archive.open('rb') as stream:
                    if is_layout_path(path, segment):
                        metadata = read_metadata(stream, member)
                        if rewrite_layout(metadata, tables[segment]) == metadata:
                            continue
                    raw = read_member(stream, member)
                if is_layout_path(path, segment):
                    data = rewrite_layout(raw, tables[segment])
                elif is_choice_path(path, segment):
                    data = rewrite_choices(raw, tables[segment])
                    if data == raw:
                        continue
                elif key.endswith('.ebm'):
                    data = translated_ebm(raw, tables[segment], args.gust_ebm, work, event_contexts.get(path))
                else:
                    if key.endswith('.e'):
                        if args.gust_enc is None:
                            raise ValueError(f'Encrypted XML requires gust_enc: {path}')
                        raw = _decode_encrypted_xml(raw, path, args.gust_enc, work, 0)
                        path = path[:-2]
                    data = translated_xml(raw, tables[segment], path, contexts.get(path))
                if data is None:
                    continue
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                outputs.append(path)
                if path.lower().endswith('.xml') and key.removesuffix('.e') + '.e' in selected:
                    if args.gust_enc is None:
                        raise ValueError(f'Encrypted XML output requires gust_enc: {path}')
                    run(args.gust_enc, '-' + _xml_game_id(path), target)
                    encrypted = Path(str(target) + '.e')
                    _regular_file(encrypted, 'Rebuilt encrypted XML')
                    checked = _decode_encrypted_xml(encrypted.read_bytes(), path, args.gust_enc, work, 1)
                    if checked != data:
                        raise ValueError(f'Encrypted XML round-trip differs: {path}')
                    outputs.append(path + '.e')
        (root / 'manifest.txt').write_text('\n'.join(sorted(outputs)) + '\n', encoding='utf-8')
    return profiles


def extract_fonts(args, output):
    found = {}
    for archive in sorted(args.pak_root.iterdir()):
        if not re.fullmatch(r'PACK[0-9_]+\.PAK', archive.name, re.I):
            continue
        _regular_file(archive, 'Font source PAK')
        with archive.open('rb') as stream:
            for member in members(stream):
                path = member.path.as_posix().lower()
                match = re.fullmatch(r'data/(b[1-4])/(x64/(?:res_jp/)?(?:font/mainfont[^/]+\.g1t|res_jp/layout/default\.g1n|layout/default\.g1n))', path)
                if not match:
                    continue
                segment, relative = match.groups()
                key = (segment.upper(), relative)
                if key in found:
                    raise ValueError(f'Ambiguous font source: {path}')
                target = output / segment.upper() / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(read_member(stream, member))
                found[key] = target
    if not found:
        raise ValueError('No supported font sources found in the selected PAK files')
    for target in found.values():
        if target.suffix == '.g1t':
            run(args.gust_g1t, '-y', target.name, cwd=target.parent)
    return found


def build(args):
    args.game_root = args.game_root.resolve()
    args.pak_root = (args.pak_root or args.game_root / 'Data').resolve()
    workspace = args.translations_root.resolve()
    output = args.output.absolute()
    if not args.pak_root.is_relative_to(args.game_root):
        raise ValueError('PAK directory must be inside the selected game installation')
    if not args.game_root.is_dir() or not workspace.is_dir() or not args.pak_root.is_dir():
        raise ValueError('Game, PAK and translation directories must exist')
    if output.resolve() == args.game_root or args.game_root.is_relative_to(output.resolve()):
        raise ValueError('Build output must not replace the game installation')
    inputs = [args.pak_root, workspace]
    for key in ('font', 'gust_ebm', 'gust_enc', 'gust_g1t', 'msdf_atlas_gen'):
        value = getattr(args, key, None)
        if value is not None:
            _regular_file(value, key)
            inputs.append(value)
    if output.is_symlink() or any(p.is_symlink() for p in output.parents):
        raise ValueError('Build output must not contain symlinks')
    if any(paths_overlap(output, item) for item in inputs):
        raise ValueError('Build output must be separate from input files and directories')
    if output.exists() and (not output.is_dir() or
                            (any(output.iterdir()) and not (output / MARKER).is_file())):
        raise ValueError('Refusing to overwrite an unowned build directory')
    table_snapshots = {p: digest(p) for p in workspace.glob('*.json')}
    tables = {segment: load_workspace(workspace, segment, args.language) for segment in SEGMENTS}
    ebm_contexts = {segment: load_ebm_contexts(workspace, segment, args.language) for segment in SEGMENTS}
    source_files = [p for p in args.pak_root.iterdir() if re.fullmatch(r'PACK[0-9_]+\.PAK', p.name, re.I)]
    source_files += [args.game_root / f'{segment}.dll' for segment in SEGMENTS] if args.font else []
    snapshots = {p: _archive_signature(_regular_file(p, 'Game source')) for p in source_files}
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='brq-translation-', dir=output.parent) as temporary:
        stage = Path(temporary).resolve() / 'output'
        stage.mkdir()
        normalized = stage / 'tables'
        normalized.mkdir()
        for segment, table in tables.items():
            font_table = dict(table)
            context_entries = ebm_contexts[segment] | load_xml_contexts(workspace, segment, args.language)
            for path, entries in context_entries.items():
                for number, entry in entries.items():
                    if entry['translation']:
                        font_table[f'{path}#{number}:{entry["source"]}'] = entry['translation']
            (normalized / f'{segment}.json').write_text(json.dumps(font_table, ensure_ascii=False), encoding='utf-8')
        profiles = build_text(args, stage, tables, ebm_contexts)
        if args.font:
            if not args.msdf_atlas_gen or not args.gust_g1t:
                raise ValueError('Font generation requires msdf-atlas-gen and gust_g1t')
            extract_fonts(args, stage / 'sources')
            command = [str(Path(__file__).parent / 'fonts/build_font_assets.py'),
                       '--game-root', str(args.game_root), '--source-root', str(stage / 'sources'),
                       '--translations-root', str(normalized), '--target-language', args.language,
                       '--font', str(args.font), '--msdf-atlas-gen', str(args.msdf_atlas_gen),
                       '--gust-g1t', str(args.gust_g1t), '--output-root', str(stage / 'fonts'),
                       '--runtime-root', str(stage / 'runtime'), '--all']
            import sys
            run(sys.executable, '-B', *command)
            run(sys.executable, '-B', *command, '--check-only')
        graphics_inputs = []
        template_index = workspace / 'templates/project.json'
        if template_index.is_file():
            from graphics_templates import CATALOG, apply, catalog
            from types import SimpleNamespace
            document = json.loads(_regular_file(template_index, 'Template index').read_text(encoding='utf-8'))
            records = catalog()
            selected = []
            for identity in document.get('images', []):
                if identity not in records:
                    raise ValueError(f'Unknown image template: {identity}')
                image = _regular_file(workspace / 'templates' / identity / 'image.png', 'Edited image')
                baseline = document.get('blank_sha256', {}).get(identity)
                if baseline is None:
                    raise ValueError('Image workspace has no blank-image hashes; re-export to a new directory')
                if digest(image) != baseline:
                    selected.append(identity)
            if selected:
                if not args.gust_g1t:
                    raise ValueError('Edited images require gust_g1t')
                apply(SimpleNamespace(templates=CATALOG, edits=workspace / 'templates', id=selected,
                                      pak_root=args.pak_root, gust_g1t=args.gust_g1t, output=stage / 'graphics'))
                graphics_inputs.append(stage / 'graphics/graphics')
        if (workspace / 'graphics').exists():
            graphics_inputs.append(workspace / 'graphics')
        if graphics_inputs:
            from prepare_graphics import _collect_graphics, prepare_graphics
            combined = stage / 'combined-graphics'
            combined.mkdir()
            for source in graphics_inputs:
                for graphic in _collect_graphics(source):
                    target = combined / graphic.segment / graphic.game_path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if target.exists():
                        raise ValueError(f'Duplicate generated and supplied graphic: {graphic.game_path}')
                    shutil.copyfile(graphic.source, target)
            prepare_graphics(combined, stage / 'runtime')
        assets = load_manifests(stage / 'runtime')
        glyphs = load_glyph_tables(stage / 'runtime', 1000 + len(assets))
        report = {'version': 1, 'language': args.language, 'packs': profiles,
                  'assets': [{'segment': a.segment, 'path': a.game_path, 'sha256': digest(a.source)} for a in assets],
                  'glyphs': {g.source.relative_to(stage / 'runtime').as_posix(): digest(g.source) for g in glyphs}}
        (stage / 'runtime/build-inputs.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        if any(_archive_signature(p) != before for p, before in snapshots.items()):
            raise ValueError('Game source changed during the build')
        if {p: digest(p) for p in workspace.glob('*.json')} != table_snapshots:
            raise ValueError('Translation workspace changed during the build')
        # Keep generated reports valid after the staging directory is published.
        for report_path in (stage / 'fonts').rglob('*.json'):
            text = report_path.read_text(encoding='utf-8')
            report_path.write_text(text.replace(str(stage), str(output.resolve())), encoding='utf-8')
        (stage / MARKER).write_text('BRQ translation output\n')
        previous = Path(temporary) / 'previous'
        if output.exists():
            output.rename(previous)
        try:
            stage.rename(output)
        except OSError:
            if previous.exists():
                previous.rename(output)
            raise
    print(f'Translation build completed: {output}')


def check_runtime(runtime):
    data = json.loads((runtime / 'build-inputs.json').read_text(encoding='utf-8'))
    if data.get('version') != 1 or set(data.get('packs', {})) != set(SEGMENTS):
        raise ValueError('Invalid build input report')
    for segment, records in data['packs'].items():
        if len(records) != len(PAK_NAMES[segment]):
            raise ValueError('Incomplete source PAK report')
        for name, record in zip(PAK_NAMES[segment], records):
            if not re.fullmatch(r'[0-9a-f]{64}', record['sha256']):
                raise ValueError('Invalid source PAK hash')
            parts = Path(record['path']).parts
            if not _safe_path(record['path']) or not parts or parts[-1].upper() != name:
                raise ValueError('Invalid source PAK path')
    actual = {(a.segment, a.game_path): digest(a.source) for a in load_manifests(runtime)}
    expected = {(a['segment'], a['path']): a['sha256'] for a in data['assets']}
    if actual != expected or len(expected) != len(data['assets']):
        raise ValueError('Runtime assets differ from this build report')
    glyphs = load_glyph_tables(runtime, 1000 + len(actual))
    if {g.source.relative_to(runtime.resolve()).as_posix(): digest(g.source) for g in glyphs} != data.get('glyphs', {}):
        raise ValueError('Glyph tables differ from this build report')
    return data


def source_pack_header(runtime, output):
    data = check_runtime(runtime)
    rows = [f'    {{"{segment}", "{record["path"]}", "{record["sha256"]}"}}'
            for segment, records in data['packs'].items() for record in records]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text('#pragma once\n#include <array>\n#include "source_pack.hpp"\nnamespace brq {\n'
        + f'inline constexpr std::array<SourcePack, {len(rows)}> source_pack_fingerprints{{{{\n'
        + ',\n'.join(rows) + '\n}};\n}\n', encoding='utf-8')


def main():
    import sys
    if '--check-runtime' in sys.argv:
        parser = argparse.ArgumentParser(description='Check the current translation build')
        parser.add_argument('--check-runtime', type=Path, required=True)
        parser.add_argument('--binary', type=Path)
        parser.add_argument('--header', type=Path)
        parser.add_argument('--embedded', choices=('0', '1'), default='0')
        args = parser.parse_args()
        check_runtime(args.check_runtime)
        if args.header:
            source_pack_header(args.check_runtime, args.header)
        if args.binary:
            from static_check import parse_pe, verify_embedded_resources
            binary = args.binary.read_bytes()
            pe = parse_pe(binary)
            if pe['machine'] != 0x8664 or pe['exportNames'] != ['InitializeASI']:
                raise ValueError('Unexpected ASI architecture or exports')
            if args.embedded == '1':
                assets = load_manifests(args.check_runtime)
                glyphs = load_glyph_tables(args.check_runtime, 1000 + len(assets))
                verify_embedded_resources(binary, pe['rcdata'], assets, glyphs)
        print('Current translation build checks passed')
        return
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('game-root', 'translations-root', 'output', 'gust-ebm'):
        parser.add_argument('--' + name, type=Path, required=True)
    for name in ('pak-root', 'font', 'gust-enc', 'gust-g1t', 'msdf-atlas-gen'):
        parser.add_argument('--' + name, type=Path)
    parser.add_argument('--language', required=True)
    build(parser.parse_args())


if __name__ == '__main__':
    main()
