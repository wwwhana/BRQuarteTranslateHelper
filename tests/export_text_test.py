"""Check source-key extraction and safe blank-table publication."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tools"))

from pak_source import ENTRY, HEADER
from kscl_test import fixture as layout_fixture
from bsb_test import fixture as choice_fixture
from export_text import export_text, _xml_candidates

for path in ("text_jp/str_ui.xml", "text_jp/str_ui.xml.e", "ui/scl_system/confirm.xml", "ui_cmn/menu.xml.e"):
    assert _xml_candidates("saves/b1/" + path) is not None
assert _xml_candidates("saves/b1/text_en/str_ui.xml") is None


PAKS = (
    "PACK02_03.PAK",
    "PACK11.PAK",
    "PACK12.PAK",
    "PACK10_04_03.PAK",
    "PACK01_01.PAK",
    "PACK02_01.PAK",
    "PACK01_02.PAK",
    "PACK02_02.PAK",
    "PACK01_03.PAK",
)


def _write_pak(path: Path, entries: dict[str, bytes]) -> None:
    table = bytearray()
    payload = bytearray()
    for name, data in entries.items():
        encoded = name.encode("ascii")
        assert len(encoded) < 128
        table.extend(ENTRY.pack(encoded.ljust(128, b"\0"), len(data), bytes(32), 0,
                                len(payload), 0))
        payload.extend(data)
    path.write_bytes(HEADER.pack(0x20000, len(entries), 16, 0) + table + payload)


def _xml(*rows: str) -> bytes:
    return ("<?xml version=\"1.0\"?><Root>" + "".join(rows) + "</Root>").encode("utf-8")


def _source_paks(root: Path) -> None:
    archives = {name: {} for name in PAKS}

    archives["PACK02_03.PAK"].update({
        "saves/b0/text_jp/strcombineall.xml": _xml(
            '<str String_ID="B0_ID" Text="開始"/>',
            '<str String_ID="B0_EN" Text="English"/>',
            '<str String_ID="B0_JP_ID" Text="名前"/>',
        ),
        # This conflicting encrypted copy must lose to the plain XML.
        "saves/b0/text_jp/strcombineall.xml.e": _xml('<str Text="暗号化だけ"/>'),
        "saves/b0/ui_cmn/seed_cabinet/uil_seed_cabinet_cell.xml": _xml(
            '<text name="tab" text="すべて"/>',
        ),
        "saves/b0/ui_cmn/seed_dlc_battle/uil_seed_dlc_battle.xml": _xml(
            '<text name="tab" text="消費"/>',
        ),
        "saves/b4/text_jp/strcombineall.xml": _xml(
            '<str String_ID="B4_ID" Text="終章"/>',
        ),
        "saves/b4/event/eventglossarykeyword.xml": _xml(
            '<eventGlossaryKeyword tag="B4_GLOSSARY" JP="用語" TC="繁體" SC="简体"/>',
        ),
        "saves/b4/ui_cmn/seed_cabinet/uil_seed_cabinet_cell.xml": _xml(
            '<text name="tab" text="素材"/>',
        ),
        "saves/b4/ui_cmn/seed_dlc_battle/uil_seed_dlc_battle.xml": _xml(
            '<text name="tab" text="その他"/>',
        ),
    })
    archives["PACK10_04_03.PAK"]["data/b1/x64/res_jp/layout/dialog.kscl"] = layout_fixture()
    archives["PACK11.PAK"]["event/b1/balloonsel/balloonseldata_jp.bsb"] = choice_fixture()
    archives["PACK11.PAK"]["event/b1/event_jp/aa/event_message_aa_000.ebm"] = b"event-b1"
    archives["PACK12.PAK"].update({
        "saves/b1/text_jp/strcombineall.xml": _xml(
            '<str String_ID="B1_ID" Text="戦闘"/>',
        ),
        "saves/b1/event/eventglossarykeyword.xml": _xml(
            '<eventGlossaryKeyword tag="B1_GLOSSARY" JP="仲間" TC="夥伴"/>',
        ),
    })

    for segment, event_pak, text_pak in (
        ("b2", "PACK01_01.PAK", "PACK02_01.PAK"),
        ("b3", "PACK01_02.PAK", "PACK02_02.PAK"),
    ):
        archives[event_pak][f"event/{segment}/event_jp/aa/event_message_{segment}.ebm"] = (
            f"event-{segment}".encode()
        )
        archives[text_pak].update({
            f"saves/{segment}/text_jp/strcombineall.xml": _xml(
                f'<str String_ID="{segment}_ID" Text="文字{segment.upper()}"/>',
            ),
            f"saves/{segment}/event/eventglossarykeyword.xml": _xml(
                f'<eventGlossaryKeyword tag="{segment}_GLOSSARY" JP="用語{segment.upper()}"/>',
            ),
            f"saves/{segment}/ui_cmn/seed_cabinet/uil_seed_cabinet_cell.xml": _xml(
                f'<text text="収納{segment.upper()}"/>',
            ),
            f"saves/{segment}/ui_cmn/seed_dlc_battle/uil_seed_dlc_battle.xml": _xml(
                f'<text text="追加{segment.upper()}"/>',
            ),
        })

    archives["PACK01_03.PAK"]["event/b4/event_jp/aa/event_message_b4.ebm"] = b"event-b4"
    for name, entries in archives.items():
        _write_pak(root / name, entries)


def _converter(root: Path) -> Path:
    path = root / "fake_gust_ebm"
    path.write_text("fixture", encoding="utf-8")
    return path


def _fake_converter(command, **_kwargs):
    source = Path(command[-1])
    raw = source.read_bytes()
    if raw == b"bad":
        source.with_suffix(".json").write_text("broken", encoding="utf-8")
    else:
        value = raw.decode("ascii").replace("event-", "台詞")
        source.with_suffix(".json").write_text(
            json.dumps(
                {"messages": [{"msg_string": value}, {"msg_string": "English"}]},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    return SimpleNamespace(returncode=0, stdout="", stderr="")


def _load(path: Path) -> dict[str, str]:
    return json.loads(path.read_text(encoding="utf-8"))


def _expect_error(action, text: str | None = None) -> None:
    try:
        action()
    except ValueError as error:
        if text is not None:
            assert text.lower() in str(error).lower(), str(error)
        return
    raise AssertionError("Invalid export input was accepted")


with tempfile.TemporaryDirectory(prefix="brq-export-text-test-") as folder:
    root = Path(folder).resolve()
    paks = root / "Data"
    paks.mkdir()
    _source_paks(paks)
    converter = _converter(root)
    output = root / "ko"
    output.mkdir()

    import export_text as exporter

    original_run = exporter.subprocess.run
    exporter.subprocess.run = _fake_converter
    try:
        summary = export_text(paks, output, "ko", converter, None)
    finally:
        exporter.subprocess.run = original_run
    assert summary["files"] == 5
    assert summary["language"] == "ko"
    assert summary["keys"] == sum(len(_load(output / f"B{i}.ko.json")) for i in range(5))
    assert summary["segments"]["B1"]["ebm_files"] == 1
    assert summary["segments"]["B4"]["ebm_files"] == 1

    b0 = _load(output / "B0.ko.json")
    assert set(b0) == {"開始", "名前", "すべて", "消費"}
    assert "暗号化だけ" not in b0
    assert all(value == "" for values in (_load(output / f"B{i}.ko.json") for i in range(5))
               for value in values.values())
    assert "ID" not in "".join(b0)
    assert "繁體" not in _load(output / "B4.ko.json")
    assert "台詞b1" in _load(output / "B1.ko.json")
    assert {"はい", "いいえ"} <= _load(output / "B1.ko.json").keys()
    assert summary["segments"]["B1"]["kscl_files"] == 1
    assert summary["segments"]["B1"]["bsb_files"] == 1
    assert {"開始\u3000", "終了\u3000"} <= _load(output / "B1.ko.json").keys()

    # Every target is checked before parsing/writing, so an existing file leaves
    # the other four files untouched.
    second = root / "fr"
    second.mkdir()
    existing = second / "B2.fr.json"
    existing.write_text('{"기존":"번역"}\n', encoding="utf-8")
    _expect_error(lambda: export_text(paks, second, "fr", converter, None), "overwrite")
    assert existing.read_text(encoding="utf-8") == '{"기존":"번역"}\n'
    assert not (second / "B0.fr.json").exists()

    _expect_error(lambda: export_text(paks, output, "../ko", converter, None), "file-name")
    _expect_error(lambda: export_text(paks, root / "missing", "de", converter, None), "existing")

    # A converter failure is reported before any output is published.
    broken_paks = root / "BrokenData"
    shutil.copytree(paks, broken_paks)
    _write_pak(
        broken_paks / "PACK01_01.PAK",
        {"event/b2/event_jp/aa/event_message_bad.ebm": b"bad"},
    )
    broken_output = root / "broken-output"
    broken_output.mkdir()
    original_run = exporter.subprocess.run
    exporter.subprocess.run = _fake_converter
    try:
        _expect_error(lambda: export_text(broken_paks, broken_output, "ko", converter, None), "malformed")
    finally:
        exporter.subprocess.run = original_run
    assert not list(broken_output.iterdir())

print("PAK source text export, plain-over-encrypted selection and safe output checks passed")
