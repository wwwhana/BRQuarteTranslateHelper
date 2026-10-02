"""Small archive fixtures: paths, bounds and encryption."""
import io
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from pak_source import HEADER, ENTRY, decode, members, read_member


def archive(name=b'\\Saves\\B1\\Text_JP\\strCombineAll.xml', *, offset=0, encrypted=True):
    key = bytes(range(32)) if encrypted else bytes(32)
    name = name.ljust(128, b'\0')
    content = b'<str Text="source"/>'
    return HEADER.pack(0x20000, 1, 16, 0x10 if encrypted else 0) + ENTRY.pack(
        decode(name, key) if encrypted else name, len(content), key, 0, offset, 0
    ) + (decode(content, key) if encrypted else content)


for encrypted in (False, True):
    stream = io.BytesIO(archive(encrypted=encrypted))
    entry, = members(stream)
    assert str(entry.path) == 'Saves/B1/Text_JP/strCombineAll.xml'
    assert read_member(stream, entry) == b'<str Text="source"/>'
for bad in (archive(b'../escape'), archive(b'C:\\escape'), archive(b'a//b'),
            archive(offset=1000), archive()[:100]):
    try:
        members(io.BytesIO(bad))
    except ValueError:
        pass
    else:
        raise AssertionError('unsafe archive accepted')
print('PAK reading, encryption, path safety and truncation checks passed')
