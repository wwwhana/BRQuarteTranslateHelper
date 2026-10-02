#!/usr/bin/env python3
"""Extract Japanese-erased PNG/PSD templates, then build external G1T overrides."""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from PIL import Image, ImageChops

from generate_embedded import is_graphics_path, validate_g1t
from pak_source import members, read_member
from prepare_runtime import paths_overlap

PROJECT = Path(__file__).resolve().parents[1]
CATALOG = PROJECT / 'data/graphics_templates.json'
MARKER = '.brq-graphics-output'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def rgba(path):
    with Image.open(path) as image:
        if image.width * image.height > 16_777_216:
            raise ValueError('Image exceeds the 16-megapixel limit')
        return image.convert('RGBA')


def png_bytes(image):
    output = io.BytesIO()
    image.save(output, format='PNG', optimize=True)
    return output.getvalue()


def difference_mask(before, after):
    """Ignore invisible RGB and one-step premultiplication rounding only."""
    if before.size != after.size:
        raise ValueError('Edited image dimensions must match the original')
    channels = [ImageChops.difference(before.getchannel('A'), after.getchannel('A'))]
    for channel in 'RGB':
        a = ImageChops.multiply(before.getchannel(channel), before.getchannel('A'))
        b = ImageChops.multiply(after.getchannel(channel), after.getchannel('A'))
        channels.append(ImageChops.difference(a, b).point(lambda n: 255 if n > 1 else 0))
    result = channels[0]
    for channel in channels[1:]:
        result = ImageChops.lighter(result, channel)
    return result.point(lambda n: 255 if n else 0)


def encode_restoration(original, clean):
    """Only changed pixels are embedded; not a copy of the original image."""
    if original.size != clean.size:
        raise ValueError('Edited image dimensions must match the original')
    # Restoration must also carry RGB changes hidden by alpha zero.  Those
    # channels are invisible to visual comparison but remain in the source
    # texture and can contain erased Japanese pixels.
    channels = [ImageChops.difference(original.getchannel(channel), clean.getchannel(channel))
                for channel in 'RGBA']
    mask = channels[0]
    for channel in channels[1:]:
        mask = ImageChops.lighter(mask, channel)
    mask = mask.point(lambda value: 255 if value else 0)
    box = mask.getbbox()
    if box is None:
        raise ValueError('The retouch image contains no changed pixels')
    patch = Image.new('RGBA', original.size)
    patch.paste(clean, (0, 0), mask)
    return dict(size=list(original.size), rect=list(box),
                overlay=base64.b64encode(png_bytes(patch.crop(box))).decode('ascii'),
                mask=base64.b64encode(png_bytes(mask.crop(box).convert('1'))).decode('ascii'))


def restore(original, record):
    if list(original.size) != record['size']:
        raise ValueError(f"Source dimensions do not match: {record['id']}")
    x0, y0, x1, y1 = record['rect']
    if not 0 <= x0 < x1 <= original.width or not 0 <= y0 < y1 <= original.height:
        raise ValueError('Retouch rectangle is outside the image')
    patch = rgba(io.BytesIO(base64.b64decode(record['overlay'], validate=True)))
    with Image.open(io.BytesIO(base64.b64decode(record['mask'], validate=True))) as encoded:
        mask = encoded.convert('L')
    if patch.size != (x1 - x0, y1 - y0) or mask.size != patch.size or set(mask.getdata()) - {0, 255}:
        raise ValueError('Invalid retouch dimensions or non-binary mask')
    clean = original.copy()
    clean.paste(patch, (x0, y0), mask)
    full_patch = Image.new('RGBA', original.size)
    full_patch.paste(patch, (x0, y0))
    visibility = Image.new('L', original.size, 255)
    visibility.paste(ImageChops.invert(mask), (x0, y0))
    return clean, full_patch, visibility


def catalog(path=CATALOG):
    document = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(document, dict) or document.get('version') != 1 or not isinstance(document.get('templates'), list):
        raise ValueError('Unsupported template catalog')
    result = {}
    for record in document['templates']:
        if not isinstance(record, dict):
            raise ValueError('Invalid template record')
        identity = record.get('id', '')
        segment, pak, member = record.get('segment'), record.get('pak'), record.get('member')
        texture, source_hash = record.get('texture'), record.get('source_sha256')
        if (not isinstance(identity, str) or not re.fullmatch(r'B[0-4]/[a-z0-9_]+', identity) or identity in result or
                segment != identity[:2] or not isinstance(pak, str) or
                not re.fullmatch(r'PACK[0-9_]+\.PAK', pak) or
                not is_graphics_path(member, segment) or not isinstance(texture, str) or
                not re.fullmatch(r'[0-9]{3}\.dds', texture) or not isinstance(source_hash, str) or
                not re.fullmatch(r'[0-9a-f]{64}', source_hash)):
            raise ValueError(f'Invalid template path or source hash: {identity}')
        size, rect = record.get('size'), record.get('rect')
        if (not isinstance(size, list) or len(size) != 2 or
                any(type(value) is not int or value <= 0 for value in size)):
            raise ValueError('Missing template dimensions or rectangle')
        if (not isinstance(rect, list) or len(rect) != 4 or
                any(type(value) is not int for value in rect)):
            raise ValueError('Missing template dimensions or rectangle')
        result[identity] = record
    return result


def regular(path):
    if path.is_symlink() or not path.is_file() or any(p.is_symlink() for p in path.parents):
        raise ValueError(f'Expected a regular file without symlinks: {path}')
    return path


@contextmanager
def staged_output(output, inputs, *, update):
    if output.is_symlink() or any(p.is_symlink() for p in output.parents):
        raise ValueError('Output path contains a symlink')
    for path in inputs:
        if paths_overlap(output, path):
            raise ValueError('Output must not overlap any input directory')
    if output.exists() and (not update or not output.is_dir() or not (output / MARKER).is_file()):
        raise ValueError('Existing work will not be overwritten; choose a new output directory')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='brq-graphics-', dir=output.parent) as temporary:
        stage = Path(temporary).resolve() / 'output'
        stage.mkdir()
        yield stage
        (stage / MARKER).write_text('BRQ graphics output\n', encoding='utf-8')
        previous = Path(temporary).resolve() / 'previous'
        if output.exists():
            output.rename(previous)
        try:
            stage.rename(output)
        except OSError:
            if previous.exists():
                previous.rename(output)
            raise


def run_gust(tool, path):
    result = subprocess.run([str(tool), '-y', str(path)], cwd=path.parent, capture_output=True, text=True)
    if result.returncode:
        raise ValueError(result.stderr.strip() or result.stdout.strip() or 'G1T conversion failed')


def unpack(record, pak_root, tool, work):
    pak = regular(pak_root / record['pak'])
    before = pak.stat()
    with pak.open('rb') as stream:
        entry = next((e for e in members(stream) if str(e.path).lower() == record['member'].lower()), None)
        if entry is None:
            raise ValueError(f"Source image not found in PAK: {record['member']}")
        data = read_member(stream, entry)
    if sha(data) != record['source_sha256']:
        raise ValueError(f"Source image hash mismatch; modified images are not accepted: {record['id']}")
    if (before.st_size, before.st_mtime_ns) != (pak.stat().st_size, pak.stat().st_mtime_ns):
        raise ValueError('PAK changed during extraction')
    source = work / Path(record['member']).name
    source.write_bytes(data)
    validate_g1t(source)
    run_gust(tool, source)
    folder = source.with_suffix('')
    dds = regular(folder / record['texture'])
    image = rgba(dds)
    if list(image.size) != record['size']:
        raise ValueError(f"Source image dimensions do not match: {record['id']}")
    return source, folder, dds, image


def extract(args):
    records = catalog(args.templates)
    selected = args.id or list(records)
    if not selected or len(set(selected)) != len(selected) or any(k not in records for k in selected):
        raise ValueError('Unknown, empty, or duplicate template selection')
    regular(args.gust_g1t)
    blank_hashes = {}
    with staged_output(args.output, (args.pak_root, args.templates), update=False) as stage:
        for identity in selected:
            record = records[identity]
            with tempfile.TemporaryDirectory(prefix='brq-extract-') as temporary:
                work = Path(temporary).resolve()
                _, _, _, original = unpack(record, args.pak_root, args.gust_g1t, work)
                clean, patch, mask = restore(original, record)
                destination = stage / identity
                destination.mkdir(parents=True)
                clean.save(destination / 'image.png')
                blank_hashes[identity] = sha((destination / 'image.png').read_bytes())
                if args.psd:
                    for image, name in ((original, 'original'), (patch, 'restore'), (mask, 'mask')):
                        image.save(work / f'{name}.png')
                    subprocess.run([args.node, str(PROJECT / 'tools/graphics-psd/export.mjs'),
                                    '--original', str(work / 'original.png'), '--restore', str(work / 'restore.png'),
                                    '--mask', str(work / 'mask.png'), '--clean', str(destination / 'image.png'),
                                    '--output', str(destination / 'image.psd'), '--python', sys.executable],
                                   check=True, stdout=subprocess.DEVNULL)
            print(f'Extracted: {identity}', flush=True)
        (stage / 'project.json').write_text(json.dumps({'version': 1, 'images': selected, 'blank_sha256': blank_hashes}, indent=2) + '\n')


def apply(args):
    from dds_overlay import patch_dds
    records = catalog(args.templates)
    document = json.loads(regular(args.edits / 'project.json').read_text(encoding='utf-8'))
    selected = args.id or document.get('images', [])
    if document.get('version') != 1 or not selected or len(set(selected)) != len(selected) or any(k not in records for k in selected):
        raise ValueError('Unknown, empty, or duplicate edit selection')
    regular(args.gust_g1t)
    # Several editable images can belong to the same G1T: rebuild it once.
    groups = {}
    for identity in selected:
        record = records[identity]
        groups.setdefault((record['segment'], record['pak'], record['member']), []).append(record)
    with staged_output(args.output, (args.pak_root, args.templates, args.edits), update=True) as stage:
        report = []
        for (segment, _, member), group in groups.items():
            with tempfile.TemporaryDirectory(prefix='brq-apply-') as temporary:
                work = Path(temporary).resolve()
                source, folder, _, _ = unpack(group[0], args.pak_root, args.gust_g1t, work)
                before = {p.name: p.read_bytes() for p in folder.glob('*.dds')}
                changed = False
                for record in group:
                    edited = rgba(regular(args.edits / record['id'] / 'image.png'))
                    original = rgba(io.BytesIO(before[record['texture']]))
                    clean, _, _ = restore(original, record)
                    if clean.size == edited.size and clean.tobytes() == edited.tobytes():
                        raise ValueError(f"Template is still blank; add your translation first: {record['id']}")
                    patched, stats = patch_dds(before[record['texture']], edited)
                    (folder / record['texture']).write_bytes(patched)
                    changed |= patched != before[record['texture']]
                    report.append({'id': record['id'], **stats})
                if not changed:
                    continue
                expected = {p.name: p.read_bytes() for p in folder.glob('*.dds')}
                run_gust(args.gust_g1t, folder)
                validate_g1t(source)
                verify = work / 'verify'
                verify.mkdir()
                copied = verify / source.name
                shutil.copyfile(source, copied)
                run_gust(args.gust_g1t, copied)
                actual = {p.name: p.read_bytes() for p in copied.with_suffix('').glob('*.dds')}
                if actual != expected:
                    raise ValueError('Re-extracted G1T textures differ from the edited DDS data')
                # B0 also loads shared B4 UI resources.
                for target_segment in ('B0', 'B1', 'B2', 'B3', 'B4'):
                    if is_graphics_path(member, target_segment):
                        target = stage / 'graphics' / target_segment / member
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(source, target)
            print(f'Converted: {segment}/{Path(member).name}', flush=True)
        (stage / 'build-report.json').write_text(json.dumps({'images': report, 'gameRun': False}, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('list', help='List available image templates')
    for name in ('extract', 'apply'):
        sub = commands.add_parser(name)
        for option in ('pak-root', 'output', 'gust-g1t'):
            sub.add_argument('--' + option, type=Path, required=True)
        sub.add_argument('--templates', type=Path, default=CATALOG)
        sub.add_argument('--id', action='append', help='Example: B1/timezone_001 (repeatable)')
        if name == 'extract':
            sub.add_argument('--psd', action='store_true')
            sub.add_argument('--node', default='node')
        else:
            sub.add_argument('--edits', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'list':
        print('\n'.join(catalog()))
    else:
        # Absolute paths are needed because conversion programs run in a scratch folder.
        for name in ('pak_root', 'output', 'templates', 'gust_g1t', 'edits'):
            value = getattr(args, name, None)
            if value is not None:
                setattr(args, name, value.absolute())
        (extract if args.command == 'extract' else apply)(args)


if __name__ == '__main__':
    main()
