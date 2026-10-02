#!/usr/bin/env python3
"""Exercise game extraction, workspace-only builds and failure preservation."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'tools'))
import build_translation as builder
from generate_embedded import generate
from pak_source import HEADER, ENTRY
from kscl_test import fixture as layout_fixture
from bsb_test import fixture as choice_fixture
from bsb import read as read_choices
from kscl import strings as layout_strings


def pak(path, entries):
    table, payload = bytearray(), bytearray()
    for name, value in entries.items():
        table.extend(ENTRY.pack(name.encode().ljust(128, b'\0'), len(value), bytes(32), 0, len(payload), 0))
        payload.extend(value)
    path.write_bytes(HEADER.pack(0x20000, len(entries), 16, 0) + table + payload)


def main():
    raw = b'<root><!-- text="ignored" --><text name="text=other" text="<IM11>\xe9\x96\x8b\xe5\xa7\x8b"/></root><extra/>'
    result = builder.translated_xml(raw, {'<IM11>開始': '<IM11>Start'}, 'saves/b1/ui/menu.xml')
    assert result == raw.replace('<IM11>開始'.encode(), b'&lt;IM11&gt;Start')
    contexts = {'REL': {'source': 'お気に入り', 'translation': '마음에 드는 사람'}}
    rows = '<Root><str Text="お気に入り" String_ID="REL"/><str String_ID="MENU" Text="お気に入り"/></Root>'.encode()
    mapping = {'お気に入り': '즐겨찾기', '마음에 드는 사람': 'Wrong second translation'}
    result = builder.translated_xml(rows, mapping, 'saves/b2/text_jp/strcombineall.xml', contexts)
    assert result.decode() == '<Root><str Text="마음에 드는 사람" String_ID="REL"/><str String_ID="MENU" Text="즐겨찾기"/></Root>'
    comment = b'<!--\n' + rows + b'\n-->'
    assert builder.translated_xml(comment + rows, mapping, 'saves/b2/text_jp/strcombineall.xml', contexts) == comment + result
    assert builder.translated_xml(b'<str String_ID="X" Text="High"/>', {'High': '높음'},
                                  'saves/b1/text_jp/str_ui.xml',
                                  {'X': {'source': 'High', 'translation': 'High'}}) is None
    assert builder.translated_xml(rows, {}, 'saves/b2/text_jp/strcombineall.xml', contexts) is not None
    try:
        builder.translated_xml(rows.replace('お気に入り'.encode(), '変更'.encode()), {}, 'saves/b2/text_jp/strcombineall.xml', contexts)
    except ValueError:
        pass
    else:
        raise AssertionError('Changed context source was silently accepted')
    caption = '<root><text name="a" text="テ"/><text name="b" text="ム"/><text name="other" text="ム"/></root>'.encode()
    caption_context = {'name:a': {'source': 'テ', 'translation': '템'}, 'name:b': {'source': 'ム', 'translation': ''}}
    result = builder.translated_xml(caption, {'テ': '테', 'ム': '무'}, 'saves/b1/ui/menu.xml', caption_context)
    assert result.decode() == '<root><text name="a" text="템"/><text name="b" text=""/><text name="other" text="무"/></root>'
    for invalid in (caption.replace(b'name="b"', b'name="missing"'), caption + '<text name="a" text="テ"/>'.encode()):
        try:
            builder.translated_xml(invalid, {}, 'saves/b1/ui/menu.xml', caption_context)
        except ValueError:
            pass
        else:
            raise AssertionError('Missing or repeated named XML context was accepted')
    assert builder.valid_context_pair('●カット', '')
    assert not builder.valid_context_pair('値%s', '')
    assert not builder.valid_context_pair('終了<IM00>', '')
    assert not builder.valid_context_pair('開始', 0)
    assert builder._xml_game_id('saves/b1/text_jp/str_ui.xml.e') == 'BR'
    assert builder._xml_game_id('saves/b2/text_jp/str_ui.xml.e') == 'A21'
    for invalid in ('/installed/B1/saves/b1/text_jp/str_ui.xml', 'str_ui.xml',
                    'saves/b5/text_jp/str_ui.xml'):
        try:
            builder._xml_game_id(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f'Invalid game path accepted: {invalid}')

    with tempfile.TemporaryDirectory(prefix='brq-dynamic-build-') as directory:
        root = Path(directory).resolve()
        game, workspace = root / 'custom game location', root / 'translations/fr'
        data = game / 'Data'
        data.mkdir(parents=True)
        workspace.mkdir(parents=True)
        for kind, loader, relative, identity, entry in (
                ('xml', builder.load_xml_contexts, 'saves/b1/ui/menu.xml', 'name:a',
                 {'source': 'High', 'translation': '높음'}),
                ('ebm', builder.load_ebm_contexts, 'event/b1/event_jp/test.ebm', '0',
                 {'source': 'High', 'translation': '높음', 'name_id': 3})):
            context_path = workspace / f'B1.fr.{kind}-context.json'
            member = json.dumps(identity) + ':' + json.dumps(entry)
            file_entry = json.dumps(relative) + ':{' + member + '}'
            for duplicate in ('{' + file_entry + ',' + file_entry + '}',
                              '{' + json.dumps(relative) + ':{' + member + ',' + member + '}}',
                              '{' + json.dumps(relative) + ':{' + member[:-1] + ',"translation":"Other"}}}'):
                context_path.write_text(duplicate)
                try:
                    loader(workspace, 'B1', 'fr')
                except ValueError as error:
                    assert 'Duplicate translation key' in str(error)
                else:
                    raise AssertionError(f'Duplicate {kind} context key was accepted')
            context_path.unlink()
        archives = {name: {'unused.txt': b'original'} for names in builder.PAK_NAMES.values() for name in names}
        for segment, names in builder.PAK_NAMES.items():
            archives[names[-1]][f'saves/{segment.lower()}/text_jp/strcombineall.xml'] = (
                '<Root><str String_ID="CTX" ID="1" Text="開始"/><str ID="2" Text="終了"/></Root>'.encode())
            (workspace / f'{segment}.fr.json').write_text(json.dumps({'開始': 'Start', '終了': ''}, ensure_ascii=False))
        for path in ('text_jp/str_ui.xml', 'ui/scl_system/confirm.xml'):
            archives['PACK12.PAK']['saves/b1/' + path] = '<Root><text text="開始"/></Root>'.encode()
        layout_path = 'data/b1/x64/res_jp/layout/dialog.kscl'
        archives['PACK10_04_03.PAK'][layout_path] = layout_fixture(('開始', '終了'))
        choice_path = 'event/b1/balloonsel/balloonseldata_jp.bsb'
        archives['PACK11.PAK'][choice_path] = choice_fixture()
        (workspace / 'B1.fr.json').write_text(json.dumps({'開始': 'Start', '開始\u3000': 'Choose\u3000'}))
        for name, entries in archives.items():
            pak(data / name, entries)
        originals = {p: p.read_bytes() for p in data.iterdir()}
        tool = root / 'converter'
        tool.write_text('not invoked for plain XML')
        args = SimpleNamespace(game_root=game, pak_root=None, translations_root=workspace,
                               output=root / 'translation', language='fr', gust_ebm=tool,
                               font=None, gust_g1t=None, gust_enc=None, msdf_atlas_gen=None)
        (workspace / 'B1.fr.xml-context.json').write_text(json.dumps({
            'saves/b1/text_jp/strcombineall.xml': {'CTX': {'source': '開始', 'translation': 'Start context'}}
        }))
        args.output.mkdir()  # Ninja creates empty output directories before commands.
        builder.build(args)
        runtime = args.output / 'runtime'
        report = builder.check_runtime(runtime)
        assert len(report['assets']) == 9
        assert read_choices((runtime / 'B1' / choice_path).read_bytes()) == [['Choose\u3000', '終了\u3000']]
        assert layout_strings((runtime / 'B1' / layout_path).read_bytes()) == {'Start', '終了'}
        for path in ('text_jp/str_ui.xml', 'ui/scl_system/confirm.xml'):
            assert 'Start' in (runtime / 'B1/saves/b1' / path).read_text()
        text = (runtime / 'B1/saves/b1/text_jp/strcombineall.xml').read_text()
        assert 'Start' in text and '終了' in text and 'ID="2"' in text
        assert 'Start context' in text
        assert 'Start context' in json.loads((args.output / 'tables/B1.json').read_text()).values()
        (workspace / 'B1.fr.xml-context.json').write_text(json.dumps({
            'saves/b1/text_jp/missing.xml': {'CTX': {'source': '開始', 'translation': 'Missing'}}
        }))
        try:
            builder.build(args)
        except ValueError as error:
            assert 'context files are missing' in str(error)
        else:
            raise AssertionError('Missing XML context file was silently accepted')
        assert (runtime / 'B1/saves/b1/text_jp/strcombineall.xml').read_text() == text
        (workspace / 'B1.fr.xml-context.json').unlink()
        assert all(p.read_bytes() == raw for p, raw in originals.items())
        assert not (game / 'dev-assets').exists()
        builder.source_pack_header(runtime, root / 'sources.hpp')
        assert 'std::array<SourcePack, 10>' in (root / 'sources.hpp').read_text()
        assert 'dlc' not in (root / 'sources.hpp').read_text()
        assert generate(runtime, root / 'assets.hpp', root / 'assets.rc', translations_root=workspace) == 9
        table = workspace / 'B1.fr.json'
        table.write_text('{"開始":"Continue"}')
        builder.build(args)
        assert 'Continue' in (runtime / 'B1/saves/b1/text_jp/strcombineall.xml').read_text()
        table.write_text('{"開始":"A","開始":"B"}')
        try:
            builder.build(args)
        except ValueError:
            pass
        else:
            raise AssertionError('Duplicate translation key accepted')
        assert 'Continue' in (runtime / 'B1/saves/b1/text_jp/strcombineall.xml').read_text()
        table.write_text('{"開始":"Finish"}')
        # An unknown modified PAK is accepted by content, preserving unmatched text.
        changed_pak = data / builder.PAK_NAMES['B1'][-1]
        changed_path = 'saves/b1/text_jp/strcombineall.xml'
        archives[changed_pak.name][changed_path] = (
            '<Root><str ID="1" Text="開始"/><str ID="2" Text="変更済み"/>'
            '<str ID="3" Text="追加台詞"/></Root>'.encode())
        pak(changed_pak, archives[changed_pak.name])
        originals[changed_pak] = changed_pak.read_bytes()
        builder.build(args)
        modified = (runtime / 'B1' / changed_path).read_text()
        assert 'Finish' in modified and '変更済み' in modified and '追加台詞' in modified
        updated = builder.check_runtime(runtime)
        assert any(item['sha256'] == builder.digest(changed_pak) for item in updated['packs']['B1'])
        # A table edited while it is being loaded must not publish mixed inputs.
        load_table = builder.load_workspace
        def changing_table(root, segment, language):
            result = load_table(root, segment, language)
            if segment == 'B1':
                table.write_text('{"開始":"Changed during load"}')
            return result
        with patch.object(builder, 'load_workspace', changing_table):
            try:
                builder.build(args)
            except ValueError as error:
                assert 'workspace changed' in str(error)
            else:
                raise AssertionError('A table changed during loading was accepted')
        assert (runtime / 'B1' / changed_path).read_text() == modified
        table.write_text('{"開始":"Finish"}')
        # A newly added context must not reach assets without reaching the font corpus.
        build_text = builder.build_text
        new_context = workspace / 'B2.fr.xml-context.json'
        def adding_context(args, stage, tables, contexts):
            new_context.write_text(json.dumps({
                'saves/b2/text_jp/strcombineall.xml': {'CTX': {'source': '開始', 'translation': '추가된 글자'}}
            }, ensure_ascii=False))
            return build_text(args, stage, tables, contexts)
        with patch.object(builder, 'build_text', adding_context):
            try:
                builder.build(args)
            except ValueError as error:
                assert 'workspace changed' in str(error)
            else:
                raise AssertionError('A newly added context bypassed the workspace snapshot')
        new_context.unlink()
        assert (runtime / 'B1' / changed_path).read_text() == modified
        # Exercise the actual CMake build path and its change dependencies.
        build = game / 'project/build'
        options = ['cmake', '-S', str(PROJECT), '-B', str(build), '-DBUILD_TESTING=OFF',
                   '-DBRQ_BUILD_TRANSLATION=ON', f'-DBRQ_GAME_ROOT={game}',
                   f'-DBRQ_TRANSLATIONS_ROOT={workspace}', '-DBRQ_TARGET_LANGUAGE=fr',
                   f'-DBRQ_GUST_EBM_EXECUTABLE={tool}']
        for command in (options, ['cmake', '--build', str(build), '--target', 'check-translation']):
            result = subprocess.run(command, capture_output=True, text=True)
            assert result.returncode == 0, result.stdout + result.stderr
        assert 'Finish' in (build / 'translation/runtime/B1/saves/b1/text_jp/strcombineall.xml').read_text()
        assert all(p.read_bytes() == raw for p, raw in originals.items())
        result = subprocess.run([sys.executable, '-B', str(PROJECT / 'tools/translation_table.py'),
                                 '--translations-root', str(workspace), '--target-language', 'fr'],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        # The extraction reader finds raw font members without a dev-assets tree.
        font_pak = data / 'PACK00_04_03.PAK'
        pak(font_pak, {'data/b1/x64/font/mainfonthd720_0.g1t': b'font',
                       'data/b1/x64/res_jp/layout/default.g1n': b'menu',
                       'data/b4/x64/res_jp/font/mainfont00_hd1080_0.g1t': b'shared'})
        args.gust_g1t = tool
        with patch.object(builder, 'run') as convert:
            fonts = builder.extract_fonts(args, root / 'font sources')
        assert len(fonts) == 3 and convert.call_count == 2
        assert (root / 'font sources/B1/x64/res_jp/layout/default.g1n').read_bytes() == b'menu'
        assert (root / 'font sources/B4/x64/res_jp/font/mainfont00_hd1080_0.g1t').read_bytes() == b'shared'
        # Rebuilt EBM must retain every non-text field and the requested text.
        event_work = root / 'event'
        event_work.mkdir()
        original_json = '{"name":"event.ebm","messages":[{"id":7,"msg_string":"開始"}]}'
        payload = {}
        def decode(_tool, ebm, _label):
            path = ebm.with_suffix('.json')
            path.write_text(payload.get('json', original_json), encoding='utf-8')
            return path
        def encode(_tool, path):
            payload['json'] = path.read_text(encoding='utf-8')
            path.with_suffix('.ebm').write_bytes(b'rebuilt event')
        with patch.object(builder, '_run_converter', decode), patch.object(builder, 'run', encode):
            assert builder.translated_ebm(b'event', {'開始': 'Start'}, tool, event_work) == b'rebuilt event'
        assert json.loads(payload['json'])['messages'] == [{'id': 7, 'msg_string': 'Start'}]
        original_json = json.dumps({'messages': [
            {'name_id': 3, 'msg_string': 'なるほど……'},
            {'name_id': 8, 'msg_string': 'なるほど……'}]}, ensure_ascii=False)
        event_context = {'1': {'source': 'なるほど……', 'translation': '그렇군요……', 'name_id': 8}}
        with patch.object(builder, '_run_converter', decode), patch.object(builder, 'run', encode):
            payload.clear()
            builder.translated_ebm(b'event', {'なるほど……': '그렇구나……'}, tool, event_work, event_context)
            assert json.loads(payload['json'])['messages'] == [
                {'name_id': 3, 'msg_string': '그렇구나……'},
                {'name_id': 8, 'msg_string': '그렇군요……'}]
            for overrides in (
                    {'2': event_context['1']},
                    {'1': {**event_context['1'], 'name_id': 3}},
                    {'1': {**event_context['1'], 'source': '変更'}}):
                payload.clear()
                try:
                    builder.translated_ebm(b'event', {}, tool, event_work, overrides)
                except ValueError:
                    pass
                else:
                    raise AssertionError('Changed EBM position/source/speaker was silently accepted')
            event_path = 'event/b4/event_jp/mm01/event_message_mm01_010.ebm'
            archives['PACK01_03.PAK'][event_path] = b'event'
            pak(data / 'PACK01_03.PAK', archives['PACK01_03.PAK'])
            (workspace / 'B4.fr.json').write_text(json.dumps({'なるほど……': '그렇구나……'}))
            context_file = workspace / 'B4.fr.ebm-context.json'
            context_file.write_text(json.dumps({event_path: event_context}))
            payload.clear()
            builder.build(args)
            assert (runtime / 'B4' / event_path).read_bytes() == b'rebuilt event'
            assert json.loads(payload['json'])['messages'][1]['msg_string'] == '그렇군요……'
            assert '그렇군요……' in json.loads((args.output / 'tables/B4.json').read_text()).values()
            context_file.write_text(json.dumps({event_path.replace('mm01_010', 'mm01_999'): event_context}))
            try:
                builder.build(args)
            except ValueError as error:
                assert 'context files are missing' in str(error)
            else:
                raise AssertionError('Missing EBM context file was silently accepted')
            assert (runtime / 'B4' / event_path).read_bytes() == b'rebuilt event'
            original_json = json.dumps({'messages': [
                {'name_id': 3, 'msg_string': 'なるほど……'},
                {'name_id': 8, 'msg_string': '●カット'}]}, ensure_ascii=False)
            context_file.write_text(json.dumps({event_path: {
                '1': {'source': '●カット', 'translation': '', 'name_id': 8}
            }}, ensure_ascii=False))
            payload.clear()
            builder.build(args)
            assert json.loads(payload['json'])['messages'] == [
                {'name_id': 3, 'msg_string': '그렇구나……'},
                {'name_id': 8, 'msg_string': ''}]
            assert '' not in json.loads((args.output / 'tables/B4.json').read_text()).values()
    print('PASS: dynamic translation build, source preservation, blank values, generated metadata, and CMake integration')


if __name__ == '__main__':
    main()
