#!/usr/bin/env python3
"""Small BC1/BC7 block-overlay checks."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import struct
import sys

from PIL import Image

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

from dds_overlay import patch_dds


try:
    from ispc_texcomp import BC7EncSettings, RGBASurface, compress_blocks_bc7
except ImportError:
    BC7EncSettings = RGBASurface = compress_blocks_bc7 = None


def _bc1_fixture(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="DDS", pixel_format="DXT1")
    return output.getvalue()


def _bc7_fixture(image: Image.Image) -> bytes:
    if compress_blocks_bc7 is None:
        raise RuntimeError("ispc_texcomp is not installed")
    payload = compress_blocks_bc7(
        RGBASurface(image.tobytes(), image.width, image.height, image.width * 4),
        BC7EncSettings.from_profile("alpha_slow"),
    )
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000
    header = (
        b"DDS "
        + struct.pack("<7I", 124, flags, image.height, image.width, len(payload), 0, 1)
        + struct.pack("<11I", *([0] * 11))
        + struct.pack("<4I", 32, 0x4, 0x30315844, 0)
        + struct.pack("<4I", 0, 0, 0, 0)
        + struct.pack("<5I", 0x1000, 0, 0, 0, 0)
        + struct.pack("<5I", 98, 3, 0, 1, 0)
    )
    return header + payload


def _fixture(kind: str, image: Image.Image) -> bytes:
    return _bc1_fixture(image) if kind == "BC1" else _bc7_fixture(image)


def _decoded(data: bytes) -> Image.Image:
    with Image.open(BytesIO(data)) as opened:
        return opened.convert("RGBA")


def _blocks(data: bytes, kind: str) -> list[bytes]:
    width = int.from_bytes(data[16:20], "little")
    height = int.from_bytes(data[12:16], "little")
    offset = 128 if kind == "BC1" else 148
    block_size = 8 if kind == "BC1" else 16
    columns = (width + 3) // 4
    rows = (height + 3) // 4
    return [
        data[offset + index * block_size : offset + (index + 1) * block_size]
        for index in range(columns * rows)
    ]


def _base_image() -> Image.Image:
    image = Image.new("RGBA", (8, 8))
    for y in range(8):
        for x in range(8):
            image.putpixel((x, y), (20 + x * 20, 30 + y * 20, 80, 255))
    return image


def _check_kind(kind: str) -> None:
    source = _fixture(kind, _base_image())
    original = _decoded(source)

    no_op, no_op_stats = patch_dds(source, original)
    assert no_op == source
    assert no_op_stats == {
        "changed_blocks": 0,
        "total_blocks": 4,
        "untouched_blocks_identical": True,
    }

    edited = original.copy()
    edited.paste((255, 20, 220, 255), (0, 0, 4, 4))
    patched, stats = patch_dds(source, edited)
    assert len(patched) == len(source)
    assert patched[: (128 if kind == "BC1" else 148)] == source[: (128 if kind == "BC1" else 148)]
    assert stats["changed_blocks"] == 1
    assert stats["total_blocks"] == 4
    assert stats["untouched_blocks_identical"] is True
    assert _blocks(patched, kind)[1:] == _blocks(source, kind)[1:]

    within_tolerance = original.copy()
    r, g, b, a = within_tolerance.getpixel((0, 0))
    within_tolerance.putpixel((0, 0), (r, g, b, max(0, a - 1)))
    same, same_stats = patch_dds(source, within_tolerance)
    assert same == source
    assert same_stats["changed_blocks"] == 0

    alpha_edge = original.copy()
    alpha_edge.putpixel((1, 1), (255, 255, 255, 0))
    alpha_patched, alpha_stats = patch_dds(source, alpha_edge)
    assert alpha_stats["changed_blocks"] == 1
    assert _blocks(alpha_patched, kind)[1:] == _blocks(source, kind)[1:]

    if kind == "BC7":
        transparent = _base_image()
        transparent.putalpha(0)
        hidden_source = _fixture(kind, transparent)
        erased = _decoded(hidden_source)
        erased.paste((0, 0, 0, 0), (0, 0, 4, 4))
        cleared, cleared_stats = patch_dds(hidden_source, erased)
        assert cleared_stats["changed_blocks"] == 1, 'Transparent RGB edits were ignored'
        assert _blocks(cleared, kind)[1:] == _blocks(hidden_source, kind)[1:]


def _expect_error(action, text: str | None = None) -> None:
    try:
        action()
    except ValueError as error:
        if text is not None:
            assert text.lower() in str(error).lower(), str(error)
        return
    raise AssertionError("invalid DDS input was accepted")


def _invalid_inputs() -> None:
    source = _bc1_fixture(_base_image())
    image = _decoded(source)
    _expect_error(lambda: patch_dds(b"not DDS", image))
    _expect_error(lambda: patch_dds(source[:-1], image), "payload")
    _expect_error(lambda: patch_dds(source, Image.new("RGBA", (4, 4))), "dimensions")

    mipmapped = bytearray(source)
    struct.pack_into("<I", mipmapped, 28, 2)
    _expect_error(lambda: patch_dds(mipmapped, image), "mipmaps")

    unsupported = bytearray(source)
    unsupported[84:88] = b"DXT5"
    _expect_error(lambda: patch_dds(unsupported, image), "unsupported")

    cubemap = bytearray(source)
    struct.pack_into("<I", cubemap, 112, 0x200)
    _expect_error(lambda: patch_dds(cubemap, image), "cubemap")

    if compress_blocks_bc7 is not None:
        array = bytearray(_bc7_fixture(_base_image()))
        struct.pack_into("<I", array, 140, 2)
        _expect_error(lambda: patch_dds(array, image), "array")


def main() -> int:
    _check_kind("BC1")
    if compress_blocks_bc7 is None:
        print("BC1 overlay checks passed; BC7 checks skipped (ispc_texcomp unavailable)")
    else:
        _check_kind("BC7")
        print("BC1 and BC7 overlay checks passed")
    _invalid_inputs()
    print("DDS input checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
