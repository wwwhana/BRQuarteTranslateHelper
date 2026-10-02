#!/usr/bin/env python3
"""Check quiet ASI output and, optionally, a fresh B0/B4 loading log."""

import argparse
import hashlib
import json
import re
from pathlib import Path

DEBUG_MESSAGES = (
    "B1 string lookup:", "B1 string display:", "B1 string %s:",
    "B1 menu text delivery:", "B1 menu glyph selection:", "B1 font binding:",
    "B1 glyph lookup entry:", "Glyph selection:", "Font request:", "File size check:",
    "Replacement candidate found:", "Replacement path for the game:",
)
HASH_ENABLED = "PAK index name calculation optimization enabled"


def check(binary: bytes, log: str | None = None, segment: str = "B0") -> dict:
    for message in DEBUG_MESSAGES:
        if message.encode() in binary:
            raise ValueError(f"Diagnostic messages remain in the installed binary: {message}")
    for message in (HASH_ENABLED, "Loading time:", "Could not open replacement file"):
        if message.encode() not in binary:
            raise ValueError(f"Required loading or error message is missing: {message}")
    result = {"sha256": hashlib.sha256(binary).hexdigest(), "diagnostics": False}
    if log is not None:
        if HASH_ENABLED not in log:
            raise ValueError("Could not confirm PAK index optimization in the runtime log")
        for message in DEBUG_MESSAGES:
            if message in log:
                raise ValueError(f"Diagnostic messages remain in the runtime log: {message}")
        samples = re.findall(r"(B[04])\.dll\+0x[0-9a-fA-F]+ \(PAK index build\), PAK\[(\d+)\]=(\d+)ms", log)
        indexes = {}
        for module, slot, ms in samples:
            indexes.setdefault(module, {})[slot] = int(ms)
        if set(indexes.get(segment, {})) != {str(slot) for slot in range(6)}:
            raise ValueError(f"{segment} requires a fresh runtime log covering all six PAK files")
        if segment == "B4" and f"{HASH_ENABLED}: B4," not in log:
            raise ValueError("B4 optimization record is missing")
        result["index_ms"] = indexes
        result["first_file_ms"] = dict(re.findall(r"Loading time: (B[0-4]) first file request=(\d+)ms", log))
    return result


def self_test() -> None:
    binary = (HASH_ENABLED + "Loading time:Could not open replacement file").encode()
    log = HASH_ENABLED + "\n" + "\n".join(f"B0.dll+0x81e9e0 (PAK index build), PAK[{slot}]=2ms" for slot in range(6))
    assert len(check(binary, log)["index_ms"]["B0"]) == 6
    b4_log = f"{HASH_ENABLED}: B4, FNV-1a\n" + log.replace("B0.dll", "B4.dll").replace("=2ms", "=3ms")
    indexes = check(binary, log + "\n" + b4_log, "B4")["index_ms"]
    assert indexes["B0"]["4"] == 2 and indexes["B4"]["4"] == 3
    try:
        check(binary, log, "B4")
    except ValueError:
        pass
    else:
        raise AssertionError("B0 timing accepted as B4 evidence")
    for bad_binary, bad_log in ((binary + DEBUG_MESSAGES[0].encode(), log),
                                (binary, log + DEBUG_MESSAGES[1]), (binary, "")):
        try:
            check(bad_binary, bad_log)
        except ValueError:
            continue
        raise AssertionError("invalid release accepted")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path, nargs="?")
    parser.add_argument("--log", type=Path)
    parser.add_argument("--segment", choices=("B0", "B4"), default="B0")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        print("PASS: diagnostic messages and incomplete loading logs rejected")
    if args.binary:
        print(json.dumps(check(args.binary.read_bytes(),
                               args.log.read_text(encoding="utf-8-sig") if args.log else None, args.segment),
                         ensure_ascii=False, indent=2))
    elif not args.self_test:
        parser.error("binary or --self-test is required")
