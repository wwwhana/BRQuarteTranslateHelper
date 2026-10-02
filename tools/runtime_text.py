"""Generate exact, signature-checked DLL display translations from the workspace."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def specification():
    return json.loads((ROOT / 'data/runtime_text_hooks.json').read_text(encoding='utf-8'))


def sources_from_module(module, segment):
    spec = specification()
    if segment not in spec['modules']:
        return set()
    raw = module.read_bytes()
    return {s for s in spec['sources'] if s.encode('utf-8') + b'\0' in raw}


def generate(workspace, language, output):
    from translation_table import load_workspace
    spec = specification()
    rows, hooks = [], []
    if language:
        for segment, records in spec['modules'].items():
            table = load_workspace(workspace, segment, language)
            selected = [(s, table[s]) for s in spec['sources'] if table.get(s) and table[s] != s]
            if not selected:
                continue
            for source, target in selected:
                # These verified display sites use either a literal or one decimal argument.
                if source.count('%d') != target.count('%d') or target.count('%d') > 1:
                    raise ValueError('Invalid runtime text placeholder')
                rows.append('    {' + ', '.join(json.dumps(s, ensure_ascii=True) for s in (segment, source, target)) + '},')
            addresses = ', '.join(hex(h['rva']) for h in records)
            signatures = ', '.join('{' + ', '.join(hex(x) for x in bytes.fromhex(h['signature'])) + '}' for h in records)
            hooks.append(f'    {{"{segment}", {{{addresses}}}, {{{{{signatures}}}}}}},')
    output.write_text('#pragma once\n#include "runtime_text.hpp"\nnamespace brq {\n'
                      f'inline constexpr std::array<RuntimeText, {len(rows)}> runtime_texts{{{{\n' + '\n'.join(rows) + '\n}};\n'
                      f'inline constexpr std::array<RuntimeTextHook, {len(hooks)}> runtime_text_hooks{{{{\n' + '\n'.join(hooks) + '\n}};\n}\n', encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--language', default='')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    generate(args.workspace, args.language, args.output)
