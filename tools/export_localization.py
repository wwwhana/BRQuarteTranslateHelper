#!/usr/bin/env python3
"""Export exact-source JSON tables and blank image templates from game PAKs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
from types import SimpleNamespace

from export_text import export_text
from graphics_templates import CATALOG, extract, regular, staged_output


def export(args):
    # CMake also checks membership in ISO 639-1; the CLI guard prevents path injection.
    if not re.fullmatch(r'[a-z]{2}', args.language):
        raise ValueError('Use a two-letter ISO 639-1 language code')
    for tool in (args.gust_ebm, args.gust_g1t, args.gust_enc):
        if tool is not None:
            regular(tool)
    if args.psd:
        subprocess.run([args.node, str(Path(__file__).parent / 'graphics-psd/export.mjs'), '--help'],
                       check=True, stdout=subprocess.DEVNULL)
    font_inputs = {kind: getattr(args, 'font_' + kind, None) for kind in ('ttf',)}
    for path in font_inputs.values():
        if path is not None:
            regular(path)
    with staged_output(args.output, (args.pak_root, CATALOG, *filter(None, font_inputs.values())), update=False) as stage:
        tables = export_text(args.pak_root, stage, args.language, args.gust_ebm, args.gust_enc)
        extract(SimpleNamespace(pak_root=args.pak_root, output=stage / 'templates',
                                templates=CATALOG, gust_g1t=args.gust_g1t,
                                id=None, psd=args.psd, node=args.node))
        font = {}
        for kind, path in font_inputs.items():
            if path is not None:
                relative = 'font/' + 'font.ttf'
                target = stage / relative
                target.parent.mkdir(exist_ok=True)
                shutil.copyfile(path, target)
                font[kind] = relative
        (stage / 'localization.json').write_text(json.dumps({
            'version': 1, 'language': args.language, 'sourceLanguage': 'ja',
            'tables': tables, 'templates': 'templates/project.json', 'font': font,
        }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Translation workspace exported: {args.output}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--language', required=True, type=str.lower)
    for name in ('pak-root', 'output', 'gust-ebm', 'gust-g1t'):
        parser.add_argument('--' + name, type=lambda p: Path(p).absolute(), required=True)
    parser.add_argument('--gust-enc', type=lambda p: Path(p).absolute())
    for kind in ('ttf',):
        parser.add_argument('--font-' + kind, type=lambda p: Path(p).absolute())
    parser.add_argument('--psd', action='store_true')
    parser.add_argument('--node', default='node')
    export(parser.parse_args())


if __name__ == '__main__':
    main()
