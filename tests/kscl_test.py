"""Check KSCL text growth, offsets, opaque resources, and invalid input rejection."""
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from kscl import strings, rewrite


def fixture(values=('はい', 'いいえ')):
    offsets, payload = bytearray(), bytearray()
    for value in values:
        offsets.extend(struct.pack('<I', len(payload)))
        raw = value.encode('utf-16-le') + b'\0\0'
        payload.extend(struct.pack('<I', len(raw)) + raw)
    table = struct.pack('<3I', 6, len(values), 12 + len(offsets) + len(payload)) + offsets + payload
    fixed = b'opaque fixed layout data'
    chunk = struct.pack('<7I', 1, 28 + len(fixed) + len(table), len(fixed), len(table),
                        0x10001, 28, 28 + len(fixed)) + fixed + table
    # A following chunk must move with its own relative offsets unchanged.
    following = struct.pack('<7I', 5, 32, 4, 0, 0x10000, 28, 0xffffffff) + b'tail'
    header = bytearray(76)
    header[:8] = b'LCSK2600'
    struct.pack_into('<I', header, 8, len(header) + len(chunk) + len(following))
    return bytes(header) + chunk + following + b'opaque embedded texture resources'


def main():
    raw = fixture()
    assert strings(raw) == {'はい', 'いいえ'}
    assert rewrite(raw, {}) == raw
    mapping = {'はい': 'A much longer replacement', 'いいえ': 'No'}
    result = rewrite(raw, mapping)
    assert strings(result) == set(mapping.values())
    assert len(result) > len(raw)
    assert rewrite(result, {v: k for k, v in mapping.items()}) == raw
    before_end = struct.unpack_from('<I', raw, 8)[0]
    after_end = struct.unpack_from('<I', result, 8)[0]
    assert result[after_end:] == raw[before_end:]
    assert result[after_end - 32:after_end] == raw[before_end - 32:before_end]
    for offset, value in ((8, 75), (80, 0xffffffff), (96, 20), (139, 0xffffffff)):
        broken = bytearray(raw)
        struct.pack_into('<I', broken, offset, value)
        try:
            rewrite(broken, mapping)
        except ValueError:
            pass
        else:
            raise AssertionError(f'Invalid KSCL accepted at {offset}')
    try:
        rewrite(raw, {'はい': 'bad\0value'})
    except ValueError:
        pass
    else:
        raise AssertionError('Embedded null was accepted')
    print('KSCL round-trip, longer strings, resource preservation and invalid data checks passed')


if __name__ == '__main__':
    main()
