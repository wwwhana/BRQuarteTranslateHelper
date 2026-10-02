#!/usr/bin/env python3
"""Prepare a build-owned runtime copy without changing supplied assets."""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

MARKER = '.brq-owned-runtime'
STAMP = '.brq-prepared-runtime'


def paths_overlap(left, right):
    left, right = left.resolve(), right.resolve()
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def prepare(args):
    source, output = args.runtime_root.resolve(), args.output.resolve()
    font_output_arg = getattr(args, 'font_output', None)
    font_output = font_output_arg.resolve() if font_output_arg else None
    outputs = [output] + ([font_output] if font_output else [])
    if not source.is_dir() or paths_overlap(source, output):
        raise ValueError('Input assets and build output must be separate directories')
    if args.output.is_symlink() or (output.exists() and
            (not output.is_dir() or (any(output.iterdir()) and not (output / MARKER).is_file()))):
        raise ValueError('Only output directories created by this tool can be updated')
    if font_output_arg and (font_output_arg.is_symlink() or font_output.exists() and not font_output.is_dir()):
        raise ValueError('Font output must be a regular directory')
    if font_output and paths_overlap(output, font_output):
        raise ValueError('Runtime output and font output must be separate directories')

    directory_inputs = (args.runtime_root, args.graphics_root, args.translations_root)
    file_inputs = tuple(
        getattr(args, name, None)
        for name in ('font', 'msdf_atlas_gen', 'gust_g1t')
    )
    for owned_output in outputs:
        for path in (*directory_inputs, *file_inputs, getattr(args, 'game_root', None)):
            if path is not None and path.resolve().is_relative_to(owned_output):
                raise ValueError('Files inside the build output cannot be used as inputs')
        for root in directory_inputs:
            if root is not None and owned_output.is_relative_to(root.resolve()):
                raise ValueError('Build output cannot be created inside an input directory')

    for root in directory_inputs:
        if root is not None and root.exists():
            if root.is_symlink() or any(p.is_symlink() for p in root.rglob('*')):
                raise ValueError(f'Input directory contains a symbolic link: {root}')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='brq-assets-', dir=output.parent) as temporary:
        stage = Path(temporary) / 'runtime'
        shutil.copytree(source, stage)
        if args.font:
            from build_translation import extract_fonts
            args.pak_root = getattr(args, 'pak_root', None) or args.game_root / 'Data'
            sources = Path(temporary) / 'sources'
            extract_fonts(args, sources)
            command = [sys.executable, '-B', str(Path(__file__).parent / 'fonts/build_font_assets.py'),
                       '--game-root', str(args.game_root), '--source-root', str(sources), '--translations-root', str(args.translations_root),
                       '--target-language', args.target_language, '--font', str(args.font),
                       '--msdf-atlas-gen', str(args.msdf_atlas_gen), '--gust-g1t', str(args.gust_g1t),
                       '--output-root', str(args.font_output), '--runtime-root', str(stage), '--all']
            subprocess.run(command, check=True)
            subprocess.run([*command, '--check-only'], check=True)
        if args.graphics_root:
            subprocess.run([sys.executable, '-B', str(Path(__file__).parent / 'prepare_graphics.py'),
                            '--graphics-root', str(args.graphics_root), '--runtime-root', str(stage)], check=True)
        (stage / MARKER).write_text('BRQuartetKorean build output\n', encoding='utf-8')
        # CMake may remove the command output when its arguments change.
        (stage / STAMP).write_text('BRQuartetKorean assets prepared\n', encoding='utf-8')
        previous = Path(temporary) / 'previous'
        if output.exists():
            output.rename(previous)
        try:
            stage.rename(output)
        except OSError:
            if previous.exists():
                previous.rename(output)
            raise
    print(f'Build asset preparation: {output}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('runtime-root', 'output', 'translations-root'):
        parser.add_argument(f'--{name}', type=Path, required=True)
    for name in ('game-root', 'pak-root', 'font', 'font-output',
                 'msdf-atlas-gen', 'gust-g1t', 'graphics-root'):
        parser.add_argument(f'--{name}', type=Path)
    parser.add_argument('--target-language')
    args = parser.parse_args()
    if args.font and any(getattr(args, name) is None for name in
                         ('game_root', 'font_output', 'msdf_atlas_gen', 'gust_g1t', 'target_language')):
        parser.error('--font requires the original game path and font generation tool paths')
    prepare(args)
