"""Read the Quartet A22 PAK variant; never modify an archive."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import struct

# Same Quartet format used by the existing local image extraction tool.
MASTER_KEY = b'fyrixtT9AhA4v0cFahgMcgVwxFrry42A'
HEADER = struct.Struct('<IIII')
ENTRY = struct.Struct('<128sI32sIQQ')


def decode(data, key):
    mask = bytes(a ^ b for a, b in zip(key, MASTER_KEY))
    return bytes(value ^ mask[i % 32] for i, value in enumerate(data))


def member_path(raw):
    name = raw.split(b'\0', 1)[0].decode('ascii')
    parts = name.replace('\\', '/').lstrip('/').split('/')
    if not parts or any(not p or p in ('.', '..') or p.endswith((' ', '.')) or
                        any(c in p for c in ':"*?<>|') or any(ord(c) < 32 for c in p)
                        for p in parts):
        raise ValueError(f'Invalid PAK member path: {name!r}')
    return PurePosixPath(*parts)


@dataclass(frozen=True)
class Member:
    path: PurePosixPath
    offset: int
    size: int
    key: bytes | None


def members(stream, *, base=0, limit=None):
    stream.seek(0, 2)
    end = stream.tell() if limit is None else min(limit, stream.tell())
    stream.seek(base)
    raw = stream.read(HEADER.size)
    if len(raw) != HEADER.size:
        raise ValueError('Truncated PAK header')
    version, count, header_size, flags = HEADER.unpack(raw)
    if version != 0x20000 or header_size != HEADER.size or not 0 < count <= 65536:
        raise ValueError('Unsupported PAK format; Quartet A22 is required')
    start = base + HEADER.size + count * ENTRY.size
    if start > end:
        raise ValueError('Truncated PAK member table')
    table = stream.read(count * ENTRY.size)
    result, seen = [], set()
    for raw_name, size, key, _extra, offset, _flags in ENTRY.iter_unpack(table):
        encoded = bool(flags & 0x10) or any(key)
        path = member_path(decode(raw_name, key) if encoded else raw_name)
        if path.as_posix().lower() in seen or start + offset + size > end:
            raise ValueError(f'Duplicate or out-of-bounds PAK member: {path}')
        seen.add(path.as_posix().lower())
        result.append(Member(path, start + offset, size, key if encoded else None))
    return result


def read_member(stream, member):
    stream.seek(member.offset)
    data = stream.read(member.size)
    if len(data) != member.size:
        raise ValueError(f'Truncated PAK member data: {member.path}')
    return decode(data, member.key) if member.key is not None else data
