"""Read and rebuild KSCL layout string tables without changing other resources."""
from __future__ import annotations

import struct

try:
    from .pak_source import decode
except ImportError:
    from pak_source import decode

U32 = struct.Struct('<I')
HEADER_SIZE = 76


def is_layout_path(path, segment):
    return (segment.upper() == 'B1' and
            path.casefold().startswith('data/b1/x64/res_jp/layout/') and
            path.casefold().endswith('.kscl'))


def _chunks(data):
    if len(data) < HEADER_SIZE or data[:4] != b'LCSK' or not data[4:8].isdigit():
        raise ValueError('Unsupported KSCL header')
    end = U32.unpack_from(data, 8)[0]
    if not HEADER_SIZE <= end <= len(data):
        raise ValueError('Invalid KSCL resource offset')
    offset = HEADER_SIZE
    while offset < end:
        if offset + 28 > end:
            raise ValueError('Truncated KSCL chunk')
        _, size, fixed_size, tables_size, _, fixed_offset, tables_offset = struct.unpack_from('<7I', data, offset)
        if (size != 28 + fixed_size + tables_size or offset + size > end or
                fixed_offset != 28 or tables_offset not in (28 + fixed_size, 0xffffffff) or
                (tables_size and tables_offset == 0xffffffff)):
            raise ValueError(f'Invalid KSCL chunk at {offset}')
        tables = []
        cursor = offset + 28 + fixed_size
        while cursor < offset + size:
            if cursor + 12 > offset + size:
                raise ValueError('Truncated KSCL table')
            kind, count, table_size = struct.unpack_from('<3I', data, cursor)
            if table_size < 12 or cursor + table_size > offset + size:
                raise ValueError('Invalid KSCL table size')
            raw = data[cursor:cursor + table_size]
            values = None
            if kind == 6:
                base = 12 + count * 4
                if base > table_size:
                    raise ValueError('Truncated KSCL string offsets')
                values, position = [], base
                for index in range(count):
                    relative = U32.unpack_from(raw, 12 + index * 4)[0]
                    if base + relative != position or position + 4 > table_size:
                        raise ValueError('Invalid KSCL string offset')
                    length = U32.unpack_from(raw, position)[0]
                    if (length < 2 or length % 2 or position + 4 + length > table_size or
                            raw[position + 2 + length:position + 4 + length] != b'\0\0'):
                        raise ValueError('Invalid KSCL string length')
                    value = raw[position + 4:position + 2 + length].decode('utf-16-le')
                    if '\0' in value:
                        raise ValueError('Embedded null in KSCL string')
                    values.append(value)
                    position += 4 + length
                if position != table_size:
                    raise ValueError('Unexpected KSCL string table data')
            tables.append((raw, values))
            cursor += table_size
        yield offset, size, fixed_size, tables
        offset += size


def strings(data):
    return {value for _, _, _, tables in _chunks(data)
            for _, values in tables if values is not None for value in values}


def rewrite(data, mapping):
    chunks = []
    for offset, size, fixed_size, tables in _chunks(data):
        rebuilt = []
        for raw, values in tables:
            if values is None or not any(mapping.get(value, value) != value for value in values):
                rebuilt.append(raw)
                continue
            offsets, payload = bytearray(), bytearray()
            for value in values:
                target = mapping.get(value, value)
                if not isinstance(target, str) or '\0' in target:
                    raise ValueError('Invalid KSCL translation')
                encoded = target.encode('utf-16-le') + b'\0\0'
                offsets.extend(U32.pack(len(payload)))
                payload.extend(U32.pack(len(encoded)) + encoded)
            rebuilt.append(struct.pack('<3I', 6, len(values), 12 + len(offsets) + len(payload)) + offsets + payload)
        tables_data = b''.join(rebuilt)
        chunk = bytearray(data[offset:offset + 28 + fixed_size])
        U32.pack_into(chunk, 4, len(chunk) + len(tables_data))
        U32.pack_into(chunk, 12, len(tables_data))
        chunks.append(bytes(chunk) + tables_data)
    old_end = U32.unpack_from(data, 8)[0]
    header = bytearray(data[:HEADER_SIZE])
    U32.pack_into(header, 8, HEADER_SIZE + sum(map(len, chunks)))
    result = bytes(header) + b''.join(chunks) + data[old_end:]
    # Re-read every rebuilt offset before publishing a replacement.
    list(_chunks(result))
    return result


def read_metadata(stream, member):
    def read(length):
        stream.seek(member.offset)
        raw = stream.read(length)
        if len(raw) != length:
            raise ValueError('Truncated KSCL archive member')
        return decode(raw, member.key) if member.key is not None else raw
    if member.size < HEADER_SIZE:
        raise ValueError('Truncated KSCL header')
    header = read(12)
    length = U32.unpack_from(header, 8)[0]
    if header[:4] != b'LCSK' or not HEADER_SIZE <= length <= member.size:
        raise ValueError('Invalid KSCL metadata size')
    return read(length)
