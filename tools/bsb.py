"""Read and rebuild length-prefixed UTF-8 balloon choice groups."""
import struct


def is_choice_path(path, segment):
    return (segment.upper() == 'B1' and
            path.casefold() == 'event/b1/balloonsel/balloonseldata_jp.bsb')


def read(data):
    offset = 0

    def number():
        nonlocal offset
        if offset + 4 > len(data):
            raise ValueError('Truncated BSB count or length')
        value = struct.unpack_from('<I', data, offset)[0]
        offset += 4
        return value

    groups = []
    count = number()
    if count > (len(data) - offset) // 4:
        raise ValueError('Invalid BSB group count')
    for _ in range(count):
        choices = number()
        if choices > (len(data) - offset) // 5:
            raise ValueError('Invalid BSB choice count')
        values = []
        for _ in range(choices):
            size = number()
            if size < 1 or size > len(data) - offset:
                raise ValueError('Invalid BSB string length')
            raw = data[offset:offset + size]
            offset += size
            if raw[-1] != 0 or b'\0' in raw[:-1]:
                raise ValueError('Invalid BSB string terminator')
            values.append(raw[:-1].decode('utf-8'))
        groups.append(values)
    if offset != len(data):
        raise ValueError('Unexpected trailing BSB data')
    return groups


def rewrite(data, mapping):
    groups = read(data)
    result = bytearray(struct.pack('<I', len(groups)))
    for group in groups:
        result.extend(struct.pack('<I', len(group)))
        for value in group:
            target = mapping.get(value, value)
            if not isinstance(target, str) or '\0' in target:
                raise ValueError('Invalid BSB translation')
            raw = target.encode('utf-8') + b'\0'
            result.extend(struct.pack('<I', len(raw)))
            result.extend(raw)
    rebuilt = bytes(result)
    assert read(rebuilt) == [[mapping.get(v, v) for v in group] for group in groups]
    return rebuilt
