#!/usr/bin/env python3
"""Check local font cache options without downloading fonts or using game data."""

from pathlib import Path
import subprocess
import tempfile


HELPER = Path(__file__).resolve().parents[1] / "cmake/FontInput.cmake"


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="brq-font-input-") as temporary:
        root = Path(temporary)
        source = root / "source"
        source.mkdir()
        (source / "CMakeLists.txt").write_text(
            'cmake_minimum_required(VERSION 3.24)\nproject(FontInputCheck NONE)\n'
            f'include([==[{HELPER}]==])\nbrq_resolve_font()\n'
            'file(WRITE "${CMAKE_BINARY_DIR}/resolved.txt" '
            '"${BRQ_RESOLVED_FONT_TTF}\\n")\n',
            encoding="utf-8",
        )
        inputs = root / "font input with spaces"
        inputs.mkdir()
        font = inputs / "font.ttf"
        font.write_text("test input\n", encoding="utf-8")

        def configure(build, *options, success=True):
            result = subprocess.run(
                ["cmake", "-S", str(source), "-B", str(build), *options],
                capture_output=True, text=True, timeout=30,
            )
            assert (result.returncode == 0) == success, result.stdout + result.stderr
            if not success:
                assert "does not exist or is a directory" in result.stderr, result.stderr
                return None
            return (build / "resolved.txt").read_text(encoding="utf-8").splitlines()

        build = root / "build"
        assert configure(build) == [""]
        assert configure(build, f"-DBRQ_FONT_TTF:FILEPATH={font}") == [str(font)]
        assert configure(build) == [str(font)]
        assert configure(build, "-DBRQ_FONT_TTF:FILEPATH=") == [""]
        workspace = root / "translations/fr"
        (workspace / "font").mkdir(parents=True)
        (workspace / "font/font.ttf").write_bytes(font.read_bytes())
        assert configure(build, f"-DBRQ_TRANSLATIONS_ROOT={workspace}") == [str(workspace / "font/font.ttf")]
        for label, invalid in (("missing", inputs / "missing"), ("directory", inputs)):
            configure(root / label, f"-DBRQ_FONT_TTF:FILEPATH={invalid}", success=False)
        assert not list(root.rglob("font-cache"))
    print("PASS: local font cache persistence, paths with spaces, clearing, workspace fonts, and invalid paths")


if __name__ == "__main__":
    main()
