#!/usr/bin/env python3
"""Exercise the CMake graphics-input path, including change and removal."""
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

PROJECT = Path(__file__).resolve().parents[1]


def run(*command, success=True):
    result = subprocess.run(command, text=True, capture_output=True)
    assert (result.returncode == 0) == success, result.stdout + result.stderr


with tempfile.TemporaryDirectory(prefix='brq-cmake-assets-') as directory:
    root = Path(directory)
    source, graphics, build = root / 'source assets', root / 'graphics', root / 'build'
    (source / 'B1').mkdir(parents=True)
    (source / 'B1/manifest.txt').write_text('# baseline\n', encoding='utf-8')
    image_path = Path('B1/data/b1/x64/res_jp/ui/custom.g1t')
    graphic = graphics / image_path
    graphic.parent.mkdir(parents=True)
    graphic.write_bytes(b'GT1G0600' + struct.pack('<I', 12))
    run(shutil.which('cmake'), '-S', str(PROJECT), '-B', str(build), '-DBUILD_TESTING=OFF',
        '-DBRQ_RUNTIME_ROOT=' + str(source), '-DBRQ_GRAPHICS_ROOT=' + str(graphics),
        '-DBRQ_TRANSLATIONS_ROOT=' + str(root / 'translations'))
    command = (shutil.which('cmake'), '--build', str(build), '--config', 'Release', '--target', 'brq-assets')
    run(*command)
    copied = build / 'runtime' / image_path
    assert copied.read_bytes() == graphic.read_bytes()
    assert image_path.as_posix()[3:] in (build / 'runtime/B1/manifest.txt').read_text()
    stamp = build / 'runtime/.brq-prepared-runtime'
    previous_time = stamp.stat().st_mtime_ns
    run(*command)
    assert stamp.stat().st_mtime_ns == previous_time, '변경 없이 자산을 다시 생성했습니다'
    graphic.write_bytes(b'GT1G0600' + struct.pack('<I', 13) + b'x')
    run(*command)
    assert copied.read_bytes() == graphic.read_bytes()
    graphic.write_bytes(b'invalid')
    run(*command, success=False)
    assert copied.read_bytes().endswith(b'x'), '실패한 입력이 기존 빌드 자산을 덮어썼습니다'
    graphic.unlink()
    run(*command)
    assert not copied.exists(), '입력에서 제거한 그래픽이 출력에 남았습니다'
    assert (build / 'runtime/B1/manifest.txt').read_text() == '# baseline\n'
    alternate = root / 'alternate graphics'
    replacement = alternate / image_path
    replacement.parent.mkdir(parents=True)
    replacement.write_bytes(b'GT1G0600' + struct.pack('<I', 13) + b'y')
    run(shutil.which('cmake'), '-S', str(PROJECT), '-B', str(build),
        '-DBRQ_GRAPHICS_ROOT=' + str(alternate))
    run(*command)
    assert copied.read_bytes() == replacement.read_bytes(), '입력 폴더 변경 후 빌드가 반영되지 않았습니다'
    assert (source / 'B1/manifest.txt').read_text() == '# baseline\n'
    assert not (source / image_path).exists()
print('CMake 그래픽 추가·변경·제거·옵션 전환·실패 시 기존 출력 보존 검사 통과')
