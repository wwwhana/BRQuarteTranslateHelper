#!/usr/bin/env python3
"""Replace changed 4x4 blocks in a single-level BC1 or BC7 DDS image."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import struct

from PIL import Image, ImageChops


_DDS_MAGIC = b"DDS "
_DDS_HEADER_SIZE = 124
_DDS_PIXEL_FORMAT_SIZE = 32
_DDPF_FOURCC = 0x4
_DDSCAPS2_CUBEMAP = 0x200
_DDSCAPS2_CUBEMAP_ALL_FACES = 0xFC00
_DDSCAPS2_VOLUME = 0x200000
_D3D10_RESOURCE_DIMENSION_TEXTURE2D = 3
_D3D11_RESOURCE_MISC_TEXTURECUBE = 0x4
_BC1_DXGI_FORMATS = {70, 71, 72}
_BC7_DXGI_FORMATS = {97, 98, 99}


@dataclass(frozen=True)
class _DdsLayout:
    width: int
    height: int
    data_offset: int
    block_bytes: int
    format: str
    total_blocks: int


def _invalid(message: str) -> ValueError:
    return ValueError(message)


def _parse_dds(data: bytes) -> _DdsLayout:
    if len(data) < 128:
        raise _invalid("DDS data is truncated")
    if data[:4] != _DDS_MAGIC:
        raise _invalid("DDS magic is invalid")

    header_size, _, height, width, _, depth, mip_count = struct.unpack_from(
        "<7I", data, 4
    )
    if header_size != _DDS_HEADER_SIZE:
        raise _invalid("DDS header size is invalid")
    if width == 0 or height == 0:
        raise _invalid("DDS dimensions must be positive")
    if depth:
        raise _invalid("DDS volume textures are unsupported")
    if mip_count > 1:
        raise _invalid("DDS mipmaps are unsupported; provide one mip level")

    pixel_format_size, pixel_flags = struct.unpack_from("<2I", data, 76)
    if pixel_format_size != _DDS_PIXEL_FORMAT_SIZE:
        raise _invalid("DDS pixel format header size is invalid")
    caps2 = struct.unpack_from("<I", data, 112)[0]
    if caps2 & (_DDSCAPS2_CUBEMAP | _DDSCAPS2_CUBEMAP_ALL_FACES | _DDSCAPS2_VOLUME):
        raise _invalid("DDS cubemaps and volume textures are unsupported")

    fourcc = data[84:88]
    data_offset = 128
    if not pixel_flags & _DDPF_FOURCC:
        raise _invalid("unsupported DDS format; expected BC1 or BC7")
    if fourcc == b"DXT1":
        texture_format, block_bytes = "BC1", 8
    elif fourcc == b"DX10":
        if len(data) < 148:
            raise _invalid("DDS DX10 header is truncated")
        dxgi_format, resource_dimension, misc_flag, array_size, _ = struct.unpack_from(
            "<5I", data, 128
        )
        if resource_dimension != _D3D10_RESOURCE_DIMENSION_TEXTURE2D:
            raise _invalid("DDS resource is not a 2D texture")
        if array_size != 1:
            raise _invalid("DDS texture arrays are unsupported")
        if misc_flag & _D3D11_RESOURCE_MISC_TEXTURECUBE:
            raise _invalid("DDS cubemaps are unsupported")
        if dxgi_format in _BC1_DXGI_FORMATS:
            texture_format, block_bytes = "BC1", 8
        elif dxgi_format in _BC7_DXGI_FORMATS:
            texture_format, block_bytes = "BC7", 16
        else:
            raise _invalid("unsupported DDS DXGI format; expected BC1 or BC7")
        data_offset += 20
    else:
        raise _invalid("unsupported DDS format; expected BC1 or BC7")

    block_columns = (width + 3) // 4
    block_rows = (height + 3) // 4
    total_blocks = block_columns * block_rows
    expected_length = data_offset + total_blocks * block_bytes
    if len(data) != expected_length:
        raise _invalid(
            f"DDS payload length is invalid: expected {expected_length}, got {len(data)}"
        )
    return _DdsLayout(width, height, data_offset, block_bytes, texture_format, total_blocks)


def _decode(data: bytes, layout: _DdsLayout) -> Image.Image:
    try:
        with Image.open(BytesIO(data)) as opened:
            image = opened.convert("RGBA")
            image.load()
    except (OSError, SyntaxError, ValueError, NotImplementedError) as error:
        raise _invalid(f"DDS pixels cannot be decoded: {error}") from error
    if image.size != (layout.width, layout.height):
        raise _invalid("DDS decoded dimensions do not match its header")
    return image


def _changed_blocks(original: Image.Image, edited: Image.Image, layout: _DdsLayout) -> bytearray:
    difference = ImageChops.difference(original.getchannel("A"), edited.getchannel("A"))
    # Game textures can use RGB independently of alpha; transparent lettering
    # must also be removed when its pixels are edited.
    for channel in 'RGB':
        difference = ImageChops.lighter(
            difference,
            ImageChops.difference(original.getchannel(channel), edited.getchannel(channel)),
        )

    changed = bytearray(layout.total_blocks)
    difference_bytes = difference.tobytes()
    block_columns = (layout.width + 3) // 4
    for y in range(layout.height):
        row = y * layout.width
        for x in range(layout.width):
            if difference_bytes[row + x] > 1:
                changed[(y // 4) * block_columns + (x // 4)] = 1
    return changed


def _compress_bc1(image: Image.Image, expected_payload_size: int) -> bytes:
    output = BytesIO()
    try:
        image.save(output, format="DDS", pixel_format="DXT1")
    except (OSError, ValueError) as error:
        raise _invalid(f"BC1 compression failed: {error}") from error
    encoded = output.getvalue()
    if len(encoded) < 128 or encoded[:4] != _DDS_MAGIC or encoded[84:88] != b"DXT1":
        raise _invalid("BC1 compressor returned an invalid DDS")
    payload = encoded[128:]
    if len(payload) != expected_payload_size:
        raise _invalid("BC1 compressor returned an unexpected payload size")
    return payload


def _compress_bc7(image: Image.Image, expected_payload_size: int) -> bytes:
    try:
        from ispc_texcomp import BC7EncSettings, RGBASurface, compress_blocks_bc7
    except ImportError as error:
        raise _invalid("BC7 compression requires the ispc_texcomp package") from error
    surface = RGBASurface(image.tobytes(), image.width, image.height, image.width * 4)
    try:
        payload = compress_blocks_bc7(surface, BC7EncSettings.from_profile("alpha_slow"))
    except (OSError, RuntimeError, ValueError) as error:
        raise _invalid(f"BC7 compression failed: {error}") from error
    if len(payload) != expected_payload_size:
        raise _invalid("BC7 compressor returned an unexpected payload size")
    return payload


def patch_dds(source: bytes, edited: Image.Image) -> tuple[bytes, dict[str, int | bool]]:
    """Patch changed blocks while retaining the source DDS header and other blocks.

    ``edited`` is the complete replacement image. All four channels are
    compared independently, allowing a one-level rounding difference.
    """
    if not isinstance(source, (bytes, bytearray, memoryview)):
        raise _invalid("DDS source must be bytes-like")
    if not isinstance(edited, Image.Image):
        raise _invalid("edited must be a PIL image")
    data = bytes(source)
    layout = _parse_dds(data)
    original = _decode(data, layout)
    if edited.size != (layout.width, layout.height):
        raise _invalid(
            f"edited image dimensions must be {layout.width}x{layout.height}"
        )
    try:
        replacement = edited.convert("RGBA")
    except (OSError, ValueError) as error:
        raise _invalid(f"edited image cannot be converted to RGBA: {error}") from error

    changed = _changed_blocks(original, replacement, layout)
    changed_count = sum(changed)
    stats: dict[str, int | bool] = {
        "changed_blocks": changed_count,
        "total_blocks": layout.total_blocks,
        "untouched_blocks_identical": True,
    }
    if not changed_count:
        return data, stats

    payload_size = layout.total_blocks * layout.block_bytes
    if layout.format == "BC1":
        encoded_payload = _compress_bc1(replacement, payload_size)
    else:
        encoded_payload = _compress_bc7(replacement, payload_size)

    result = bytearray(data)
    for block_index, is_changed in enumerate(changed):
        if not is_changed:
            continue
        start = layout.data_offset + block_index * layout.block_bytes
        encoded_start = block_index * layout.block_bytes
        result[start : start + layout.block_bytes] = encoded_payload[
            encoded_start : encoded_start + layout.block_bytes
        ]
    return bytes(result), stats


__all__ = ["patch_dds"]
