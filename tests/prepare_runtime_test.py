#!/usr/bin/env python3
import argparse
from pathlib import Path
import sys
import tempfile
import subprocess

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from prepare_runtime import prepare, MARKER
assert (Path(__file__).resolve().parents[1] / 'tools/prepare_graphics.py').is_file()

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    source, output = root / 'input', root / 'build runtime'
    source.mkdir()
    output.mkdir()  # Ninja may create the custom command output directory first.
    (source / 'keep.txt').write_bytes(b'original')
    args = argparse.Namespace(runtime_root=source, output=output, graphics_root=None,
                              translations_root=root / 'translations', font=None,
                              font_output=root / 'build fonts')
    prepare(args)
    assert (output / 'keep.txt').read_bytes() == b'original'
    assert (output / MARKER).is_file() and not (source / MARKER).exists()
    (source / 'keep.txt').write_bytes(b'updated')
    prepare(args)
    assert (output / 'keep.txt').read_bytes() == b'updated'
    for invalid in (source, source / 'nested', root):
        args.output = invalid
        try:
            prepare(args)
        except ValueError:
            pass
        else:
            raise AssertionError('입력과 겹치는 출력 경로를 허용했습니다')
    args.output = output
    args.graphics_root = root / 'bad-graphics'
    args.graphics_root.mkdir()
    (args.graphics_root / 'bad.dll').write_bytes(b'bad')
    try:
        prepare(args)
    except subprocess.CalledProcessError:
        pass
    else:
        raise AssertionError('잘못된 그래픽을 허용했습니다')
    assert (source / 'keep.txt').read_bytes() == b'updated'
    assert (output / 'keep.txt').read_bytes() == b'updated'

    args.graphics_root = None
    protected = root / 'protected'
    protected.mkdir()
    font = protected / 'font.ttf'
    font.write_bytes(b'font')
    args.graphics_root = root / 'graphics'
    args.graphics_root.mkdir()
    args.font = font
    for invalid in (
        source / 'font-output',
        output / 'font-output',
        args.graphics_root / 'font-output',
        args.translations_root / 'font-output',
        protected,
        root,
    ):
        args.font_output = invalid
        try:
            prepare(args)
        except ValueError:
            pass
        else:
            raise AssertionError('입력과 겹치는 글꼴 출력 경로를 허용했습니다')

    args.font = None
    args.font_output = root / 'build' / 'fonts'
    args.output = root / 'build' / 'runtime'
    prepare(args)
    assert (args.output / 'keep.txt').read_bytes() == b'updated'
print('입력 보존·빌드 사본 갱신·실패 시 기존 출력 보존 검사 통과')
