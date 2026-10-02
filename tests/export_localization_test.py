#!/usr/bin/env python3
"""Check language selection and atomic workspace export without game data."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'tools'))
import export_localization as exporter


def text_fixture(pak_root, output, language, gust_ebm, gust_enc):
    result = {}
    for i in range(5):
        name = f'B{i}.{language}.json'
        (output / name).write_text(json.dumps({'開始': ''}, ensure_ascii=False), encoding='utf-8')
        result[name] = 1
    return result


def image_fixture(args):
    args.output.mkdir()
    (args.output / 'project.json').write_text('{"version":1,"images":[]}')


with tempfile.TemporaryDirectory(prefix='brq-language-export-') as directory:
    root = Path(directory).resolve()
    pak = root / 'Data'
    pak.mkdir()
    tool = root / 'converter'
    tool.write_text('fixture')
    args = SimpleNamespace(language='fr', output=root / 'fr', pak_root=pak,
                           gust_ebm=tool, gust_g1t=tool, gust_enc=None, psd=False, node='node')
    font = root / 'local font.ttf'
    font.write_bytes(b'font fixture')
    args.font_ttf = font
    with patch.object(exporter, 'export_text', text_fixture), patch.object(exporter, 'extract', image_fixture):
        exporter.export(args)
        assert sorted(p.name for p in args.output.glob('*.fr.json')) == [f'B{i}.fr.json' for i in range(5)]
        assert not list(args.output.glob('*.ko.json'))
        assert json.loads((args.output / 'localization.json').read_text())['language'] == 'fr'
        assert (args.output / 'font/font.ttf').read_bytes() == font.read_bytes()
        assert json.loads((args.output / 'localization.json').read_text())['font']['ttf'] == 'font/font.ttf'
        edited = args.output / 'B1.fr.json'
        edited.write_text('{"開始":"Commencer"}', encoding='utf-8')
        try:
            exporter.export(args)
        except ValueError:
            pass
        else:
            raise AssertionError('Existing translation work was overwritten')
        assert json.loads(edited.read_text())['開始'] == 'Commencer'
        args.output = pak
        try:
            exporter.export(args)
        except ValueError:
            pass
        else:
            raise AssertionError('The source directory was accepted as output')
        args.output = root / 'failed'
        with patch.object(exporter, 'extract', side_effect=ValueError('fixture failure')):
            try:
                exporter.export(args)
            except ValueError:
                pass
            else:
                raise AssertionError('Image export failure was ignored')
        assert not args.output.exists(), 'A partial workspace was published'
    # Exercise the actual CMake option, including a language change in one build tree.
    build = root / 'cmake'
    for language in ('ko', 'FR', 'zh', 'zz', '../ko', ''):
        result = subprocess.run([shutil.which('cmake'), '-S', str(PROJECT), '-B', str(build),
                                 '-DBUILD_TESTING=OFF', '-DBRQ_EXPORT_LOCALIZATION=ON',
                                 '-DBRQ_EXPORT_PSD=OFF', f'-DBRQ_PAK_ROOT={pak}',
                                 f'-DBRQ_GUST_EBM_EXECUTABLE={tool}', f'-DBRQ_GUST_G1T_EXECUTABLE={tool}',
                                 f'-DBRQ_TARGET_LANGUAGE={language}'], capture_output=True, text=True)
        valid = language in ('ko', 'FR', 'zh')
        assert (result.returncode == 0) == valid, result.stdout + result.stderr
        if valid:
            assert f'/translations/{language.lower()}' in result.stdout, result.stdout
print('Language selection, output grouping, overwrite refusal and failure preservation passed')
