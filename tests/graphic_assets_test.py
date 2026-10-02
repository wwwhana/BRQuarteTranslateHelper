#!/usr/bin/env python3
"""Small filesystem checks for the reviewed graphics staging path."""

from __future__ import annotations

import json
import os
import struct
import sys
import tempfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import generate_embedded
from prepare_graphics import prepare_graphics


def expect_error(action) -> None:
    try:
        action()
    except ValueError:
        return
    raise AssertionError("잘못된 그래픽 입력을 허용했습니다")


def g1t_fixture() -> bytes:
    data = bytearray(16)
    data[:4] = b"GT1G"
    struct.pack_into("<I", data, 8, len(data))
    return bytes(data)


def main() -> int:
    fixture = g1t_fixture()
    assert generate_embedded.valid_path("data/b2/x64/res_cmn/ui/sample.g1t", "B2")
    assert generate_embedded.valid_path("data/b4/x64/res_jp/ui/shared.g1t", "B0")
    assert generate_embedded.valid_path("DATA/B2/X64/RES_CMN/UI/sample.G1T", "b2")
    assert generate_embedded.is_graphics_path("res_jp/ipu/logo_kt.g1t", "B0")
    assert not generate_embedded.valid_path("data/b4/x64/res_jp/ipu/shared.g1t", "B0")
    assert not generate_embedded.valid_path("data/b2/x64/res_cmn/ui/sample.png", "B2")
    assert not generate_embedded.valid_path("data/b2/x64/res_cmn/ui/bad. /sample.g1t", "B2")
    assert not generate_embedded.valid_path("data/b2/x64/res_cmn/ui/bad\x7f/sample.g1t", "B2")

    with tempfile.TemporaryDirectory(prefix="brq-graphics-test-") as temporary:
        root = Path(temporary)
        graphics = root / "graphics"
        runtime = root / "runtime"
        assert prepare_graphics(graphics, runtime) == 0
        graphics.mkdir()
        assert prepare_graphics(graphics, runtime) == 0

        source = graphics / "B2" / "data" / "b2" / "x64" / "res_cmn" / "ui" / "sample.g1t"
        source.parent.mkdir(parents=True)
        source.write_bytes(fixture)
        shared = graphics / "B0" / "data" / "b4" / "x64" / "res_jp" / "ui" / "shared.g1t"
        shared.parent.mkdir(parents=True)
        shared.write_bytes(fixture)
        runtime.mkdir()
        (runtime / "B2" / "manifest.txt").parent.mkdir()
        (runtime / "B2" / "manifest.txt").write_text("# keep\n", encoding="utf-8")
        assert prepare_graphics(graphics, runtime) == 2

        baseline_graphics = root / "baseline-graphics"
        baseline_source = baseline_graphics / "B1" / "data" / "b1" / "x64" / "res_jp" / "ipu" / "headsup.g1t"
        baseline_source.parent.mkdir(parents=True)
        baseline_source.write_bytes(fixture)
        baseline_runtime = root / "baseline-runtime"
        baseline_manifest = baseline_runtime / "B1" / "manifest.txt"
        baseline_manifest.parent.mkdir(parents=True)
        baseline_manifest.write_text("data/b1/x64/res_jp/ipu/headsup.g1t\n", encoding="utf-8")
        assert prepare_graphics(baseline_graphics, baseline_runtime) == 1
        assert baseline_manifest.read_text(encoding="utf-8") == "data/b1/x64/res_jp/ipu/headsup.g1t\n"
        assert source.read_bytes() == fixture
        assets = generate_embedded.load_manifests(runtime)
        assert generate_embedded.optional_graphics_count(assets) == 2
        assert len(generate_embedded.load_graphics_overrides(runtime, assets)) == 2
        assert {asset.game_path for asset in assets} == {
            "data/b2/x64/res_cmn/ui/sample.g1t",
            "data/b4/x64/res_jp/ui/shared.g1t",
        }
        record_path = runtime / "graphics-overrides.json"
        original_record = record_path.read_text(encoding="utf-8")
        overrides = json.loads(original_record)
        corrupted = [dict(item, sha256="0" * 64) for item in overrides]
        record_path.write_text(json.dumps(corrupted), encoding="utf-8")
        expect_error(lambda: generate_embedded.load_graphics_overrides(runtime, assets))
        record_path.write_text(original_record, encoding="utf-8")
        assert prepare_graphics(graphics, runtime) == 2

        same_name = root / "same-name"
        for segment in ("B0", "B4"):
            path = same_name / segment / "data" / "b4" / "x64" / "res_cmn" / "ui" / "same.g1t"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(fixture)
        same_runtime = root / "same-name-runtime"
        same_runtime.mkdir()
        assert prepare_graphics(same_name, same_runtime) == 2

        bad = root / "bad"
        png = bad / "B2" / "data" / "b2" / "x64" / "res_cmn" / "ui" / "sample.png"
        png.parent.mkdir(parents=True)
        png.write_bytes(b"PNG")
        expect_error(lambda: prepare_graphics(bad, root / "bad-runtime"))

        collision = root / "collision"
        for name in ("sample.g1t", "SAMPLE.G1T"):
            path = collision / "B2" / "data" / "b2" / "x64" / "res_cmn" / "ui" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(fixture)
        expect_error(lambda: prepare_graphics(collision, root / "collision-runtime"))

        symlink_root = root / "symlink"
        real = symlink_root / "B2" / "data" / "b2" / "x64" / "res_cmn" / "ui"
        real.mkdir(parents=True)
        real_file = real / "real.g1t"
        real_file.write_bytes(fixture)
        link = real / "link.g1t"
        try:
            os.symlink(real_file, link)
        except (OSError, NotImplementedError):
            pass
        else:
            expect_error(lambda: prepare_graphics(symlink_root, root / "symlink-runtime"))
    print("graphics asset checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
