#!/usr/bin/env python3
"""Validate exact-source translation tables and display markers."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1] / 'translations'
JAPANESE = re.compile(r'[ぁ-ゖァ-ヺ一-龯々〆ヶ]')
TAG = re.compile(r'<[^<>]+>')
CONTROL = re.compile(
    r'<[^<>]+>|\^[0-9A-Fa-f]{2}|%(?:%|[-+ #0-9.*hlLzjt]*[diuoxXfFeEgGaAcspn])|\\[nrt]|[\n\r\t]'
)


def valid_pair(before, after):
    if not (isinstance(before, str) and isinstance(after, str)
            and bool(before.strip())
            and bool(after.strip()) and after.strip().lower() not in ('-', 'dummy', 'todo')
            and '\x00' not in before + after
            and '\ufffd' not in before + after):
        return False
    # Layout breaks can move or be added when fitting the translated text.
    soft_breaks = {'^00', '<CR>', '\\n', '\\r', '\n', '\r'}
    source = [token for token in CONTROL.findall(before) if token not in soft_breaks]
    target = [token for token in CONTROL.findall(after) if token not in soft_breaks]
    # Two original B1 descriptions contain only one half of this green span.
    repaired_green_span = source in (['<CLGR>'], ['<CLNR>']) and target == ['<CLGR>', '<CLNR>']
    return source == target or repaired_green_span


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate translation key: {key!r}')
        result[key] = value
    return result


def parse(text):
    data = json.loads(text, object_pairs_hook=unique_object)
    if not isinstance(data, dict):
        raise ValueError('Translation tables must use the {"Japanese source": "translation"} format')
    for before, after in data.items():
        if not valid_pair(before, after):
            raise ValueError(f'Invalid translation or display markers: {before!r}')
        before.encode('utf-8')
        after.encode('utf-8')
    return data


def load(path):
    return parse(path.read_text(encoding='utf-8-sig'))


def load_workspace(root, segment, language):
    candidates = [root / f'{segment}.{language}.json', root / f'{segment}.json']
    present = [path for path in candidates if path.exists()]
    if len(present) != 1:
        raise ValueError(f'Expected exactly one translation table for {segment}: {root}')
    path = present[0]
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'Translation table must be a regular file: {path}')
    data = json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=unique_object)
    if not isinstance(data, dict):
        raise ValueError('Translation table must be an object')
    for key, value in data.items():
        if not isinstance(value, str) or not key.strip():
            raise ValueError(f'Invalid translation entry: {key!r}')
    # Empty exported values mean untranslated; validate every populated value normally.
    return parse(json.dumps({k: v for k, v in data.items() if v.strip()}, ensure_ascii=False))


def self_test():
    assert parse('{"開始":"Start"}') == {'開始': 'Start'}
    assert valid_pair('値%s<CR>終了\\n', 'Value%s<CR>End\\n')
    assert parse('{"開始":"开始"}') == {'開始': '开始'}
    assert valid_pair('開始', '開始')
    assert valid_pair('No Data', '정보 없음')
    assert valid_pair('High', '높음')
    assert valid_pair('説明<CR>終了', '설명 끝')
    assert valid_pair('説明終了', '설명<CR>끝')
    assert valid_pair('説明\\n終了', '설명\n끝')
    assert valid_pair('効果：説明<CLNR>', '효과: <CLGR>설명<CLNR>')
    assert valid_pair('効果：<CLGR>説明', '효과: <CLGR>설명<CLNR>')
    assert not valid_pair('効果：<CLGR>説明', '효과: 설명')
    assert not valid_pair('効果：<CLRED>説明', '효과: <CLGR>설명<CLNR>')
    assert not valid_pair('値%s', 'Value%d')
    assert valid_pair('説明^00<CLGR>文字<CLNR>', 'Description <CLGR>text<CLNR>^00')
    assert not valid_pair('説明^01文字', 'Description^00text')
    assert not valid_pair('説明<CLGR>文字<CLNR>', 'Description<CLNR>text<CLGR>')
    for invalid in ('{"開始":"Start","開始":"Begin"}', '{"値%s":"Value%d"}',
                    '{"開始":""}', '{"開始":1}',
                    '{"開始<IM00>終了":"StartEnd"}', '{" ":"Text"}', '[]'):
        try:
            parse(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError('Invalid translation table was accepted')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files', nargs='*', type=Path)
    parser.add_argument('--translations-root', type=Path)
    parser.add_argument('--target-language', help='Target ISO 639-1 language code')
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        self_test()
    if args.files:
        for path in args.files:
            table = load(path)
            if not table:
                raise SystemExit(f'Empty translation table: {path}')
            print(f'{path.name}: {len(table):,} translations; duplicate key and display marker checks passed')
    elif args.translations_root or args.target_language or not args.self_test:
        if not args.target_language:
            parser.error('--target-language is required for a translation workspace')
        workspace = args.translations_root or ROOT / args.target_language
        for i in range(5):
            segment = f'B{i}'
            table = load_workspace(workspace, segment, args.target_language)
            print(f'{segment}: {len(table):,} translations; duplicate key and display marker checks passed')
