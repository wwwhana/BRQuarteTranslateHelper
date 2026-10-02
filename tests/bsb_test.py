"""Check balloon choice lengths, exact source matching and malformed input."""
from pathlib import Path
import struct
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from bsb import read, rewrite, is_choice_path


def fixture():
    values = ['開始\u3000', '終了\u3000']
    raw = bytearray(struct.pack('<II', 1, len(values)))
    for value in values:
        encoded = value.encode() + b'\0'
        raw.extend(struct.pack('<I', len(encoded)) + encoded)
    return bytes(raw)


if __name__ == '__main__':
    raw = fixture()
    assert read(raw) == [['開始\u3000', '終了\u3000']]
    assert rewrite(raw, {}) == raw
    assert rewrite(raw, {'開始': 'Ignored'}) == raw
    changed = rewrite(raw, {'開始\u3000': 'A much longer choice\u3000'})
    assert read(changed) == [['A much longer choice\u3000', '終了\u3000']]
    assert is_choice_path('Event/B1/balloonsel/balloonseldata_JP.bsb', 'B1')
    assert not is_choice_path('event/b1/balloonsel/balloonseldata_en.bsb', 'B1')
    assert not is_choice_path('event/b1/balloonsel/balloonseldata_jp.bsb', 'B2')
    for broken in [raw[:i] for i in range(len(raw))] + [raw + b'\0', b'\xff' * 4, raw[:-1] + b'x']:
        try:
            read(broken)
        except ValueError:
            pass
        else:
            raise AssertionError('Malformed BSB accepted')
    try:
        rewrite(raw, {'開始\u3000': 'A\0B'})
    except ValueError:
        pass
    else:
        raise AssertionError('Embedded null accepted')
