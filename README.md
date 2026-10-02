# BRQuarteTranslateHelper

Translation tools and a Windows x64 ASI module for BLUE REFLECTION Quartet.

The source contains exact-source translation tables, image retouch overlays, font tools and runtime hooks. Original game PAKs, textures and DLLs are not included.

## Export a translation workspace

Set `BRQ_TARGET_LANGUAGE` to the target language’s two-letter ISO 639-1 code, such as `en`, `fr` or `zh`.

Export needs:

- Python 3.11 or newer.
- The game's original PAK files.
- Native [Gust Tools](https://github.com/VitaSmith/gust_tools) executables: `gust_ebm` and `gust_g1t`; `gust_enc` for encrypted-only XML.
- Node.js for optional layered PSD output.

Install the image and PSD dependencies:

```sh
python -m pip install -r tools/graphics-requirements.txt
npm ci --prefix tools/graphics-psd
```

Example with PowerShell:

```powershell
cmake -S . -B build/export `
  -DBRQ_EXPORT_LOCALIZATION=ON `
  -DBRQ_TARGET_LANGUAGE=fr `
  -DBRQ_GAME_ROOT="D:/Games/BLUE REFLECTION Quartet" `
  -DBRQ_FONT_TTF="D:/Fonts/MyFont.ttf" `
  -DBRQ_GUST_EBM_EXECUTABLE="D:/Tools/gust_ebm.exe" `
  -DBRQ_GUST_G1T_EXECUTABLE="D:/Tools/gust_g1t.exe" `
  -DBRQ_GUST_ENC_EXECUTABLE="D:/Tools/gust_enc.exe"

cmake --build build/export --target brq-export
```

To select a virtual environment, set `Python3_EXECUTABLE` to its absolute interpreter path.

The result is one editable language folder:

```text
translations/fr/
├── B0.fr.json
├── B1.fr.json
├── B2.fr.json
├── B3.fr.json
├── B4.fr.json
├── localization.json
├── font/
│   └── font.ttf
└── templates/
    ├── project.json
    ├── B1/
    │   ├── chaptertitle_001/
    │   │   ├── image.png
    │   │   └── image.psd
    │   └── timezone_001/...
    ├── B2/...
    ├── B3/...
    └── B4/...
```

Output defaults to `translations/{lang}` in this project. The selected `BRQ_FONT_TTF` is copied to `font/font.ttf` alongside the text and image workspace. Use `BRQ_LOCALIZATION_OUTPUT` for another output directory. Set `BRQ_EXPORT_PSD=OFF` for PNG-only templates. Export is an explicit target, not part of a normal build. It refuses to overwrite an existing output directory; choose a new directory to extract another snapshot.

### Text tables

Each key is the exact Japanese source string; values start empty:

```json
{
  "開始": "",
  "続きから": ""
}
```

Translate values only. Keep whitespace, line breaks, placeholders and control tags such as `<CR>` in their original order. Identical source strings share one entry within each module. Source strings that need different translations in different contexts cannot be distinguished by this format.

Extraction reads the selected installation, including any text modifications already present in its PAKs. It does not copy an existing target-language translation into a new workspace. Existing output directories are never overwritten.

The export target produces editing material. Use the translation build below to create runtime assets and the ASI after editing.

### Image templates

The catalog currently covers 33 reviewed menu/chapter images. Original textures are read from the user's PAKs and checked against their recorded hashes before applying the embedded retouch overlay. A changed source image is rejected instead of receiving an overlay intended for another version.

PNG files have the Japanese lettering removed. PSD files contain:

- `Translation`: an empty layer for your lettering.
- `Retouch`: the restoration overlay.
- `Original`: the locked source, with a visibility mask where needed.

Only retouch data is embedded in the catalog; original images are reconstructed locally. Preserve image dimensions and transparency. Export the edited PSD back to its neighboring `image.png` before conversion.

Available templates:

```sh
python tools/graphics_templates.py list
```

Convert selected edited images to G1T overrides:

```powershell
python tools/graphics_templates.py apply `
  --pak-root "D:/Games/BLUE REFLECTION Quartet/Data" `
  --edits translations/fr/templates `
  --output build/graphics-fr `
  --gust-g1t "D:/Tools/gust_g1t.exe" `
  --id B1/timezone_001
```

Repeat `--id` to select several images; omit it to convert every template in the workspace. Blank templates are rejected. The output is a complete replacement for the selected set, so include all desired IDs when rebuilding it.

Conversion supports single-mip BC1 and BC7 textures. It preserves untouched compression blocks and other textures in the same G1T, then extracts the rebuilt G1T again to compare the stored DDS data. It does not change the source PAK or install files into the game.

Translation builds convert edited templates automatically. Keep manual conversion outputs under `build/`. Use `translations/fr/graphics/B*/` for additional game-ready images that are not also generated from those templates.

## Build the Windows module

```sh
git submodule update --init --recursive
```

The module requires a Windows x64 C++ toolchain and CMake 3.24 or newer. With Visual Studio 2022's Desktop development with C++ workload:

```powershell
cmake -S . -B build/windows-x64 -G "Visual Studio 17 2022" -A x64
cmake --build build/windows-x64 --config Release
ctest --test-dir build/windows-x64 -C Release --output-on-failure
```

The module is named `BRQuarteTranslateHelper.asi`. External builds are written to `dist/external/`.

A basic module build does not need a game installation, Python or a font. Python enables additional checks. Native non-Windows builds support the checks and export tools, but do not build the Windows ASI.

The dependencies are [MinHook](https://github.com/TsudaKageyu/minhook) and [Ultimate ASI Loader](https://github.com/ThirteenAG/Ultimate-ASI-Loader). See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and their submodule licenses. Ultimate ASI Loader is built/distributed separately.

## Build a translation

The build reads the installed game files and applies translations from `translations/{lang}`. Only matching source text is replaced; other text is preserved.

```powershell
cmake -S . -B build/fr -G "Visual Studio 17 2022" -A x64 `
  -DBRQ_BUILD_TRANSLATION=ON `
  -DBRQ_EMBED_ASSETS=ON `
  -DBRQ_TARGET_LANGUAGE=fr `
  -DBRQ_GAME_ROOT="D:/Games/BLUE REFLECTION Quartet" `
  -DBRQ_GUST_EBM_EXECUTABLE="D:/Tools/gust_ebm.exe" `
  -DBRQ_GUST_ENC_EXECUTABLE="D:/Tools/gust_enc.exe" `
  -DBRQ_GUST_G1T_EXECUTABLE="D:/Tools/gust_g1t.exe" `
  -DBRQ_MSDF_ATLAS_GEN_EXECUTABLE="D:/Tools/msdf-atlas-gen.exe"
cmake --build build/fr --config Release
```

`BRQ_GAME_ROOT` accepts any game installation path. By default, the build reads its `Data` folder and uses `translations/{BRQ_TARGET_LANGUAGE}`. Optional overrides are `BRQ_SOURCE_GAME_ROOT` for the source installation, `BRQ_PAK_ROOT` for its PAK folder, and `BRQ_TRANSLATIONS_ROOT` for the translation workspace.

The workspace supports either `B0.fr.json` through `B4.fr.json` or `B0.json` through `B4.json`, with exactly one file per title. Empty values keep the original text. Duplicate keys and invalid control tokens stop the build before the previous output is replaced.

Build steps:

1. Read the selected installation's PAK files and apply exact-source translations to XML, EBM, BSB balloon choices, and KSCL layout text. BSB byte lengths and KSCL string offsets are rebuilt while preserving choice order and embedded image data. Verified DLL display strings also use the selected translation workspace.
2. When `font/font.ttf` exists, extract original game font files into the build folder and automatically generate glyphs for the translated text. `BRQ_FONT_TTF` can override the workspace font locally. No font is downloaded.
3. Convert edited `templates/*/image.png` files to game textures. Unedited blank templates are skipped. Export PSD changes to the neighboring PNG first. Optional game-ready overrides can be placed under `graphics/B*/` in the workspace.
4. Validate the generated files and publish them together. A failed build preserves the previous output.
5. Embed the generated files when `BRQ_EMBED_ASSETS=ON`.

Outputs are under `build/fr/translation/`: `sources/`, `tables/`, `fonts/`, and `runtime/`. The ASI is written to `dist/embedded/`. Rebuild after changing the game PAKs. A normal build does not modify the game installation.

Build just the assets with `cmake --build build/fr --target brq-assets`. Check the current generated assets with `--target check-translation`; Windows builds also provide `--target static-check` to inspect the ASI and its embedded resource bytes.

B1 menu fonts support U+0000–U+FFFF. Languages requiring contextual shaping or bidirectional layout need additional runtime support.

## Installation and checks

For external builds, copy the ASI and `BRQuarteTranslateHelper-assets/` from `dist/external/` beside the game executable. Embedded builds include the assets in the ASI.

The existing runtime installation uses x64 Ultimate ASI Loader's `dinput8.dll`, `BRQuarteTranslateHelper.asi` and `config/dinput8.ini` beside the game executable. Preserve an existing mod loader and check its chaining configuration before replacing a DLL.

Runtime checks cover supported game modules, hook instruction bytes, allowed paths and asset metadata. Unsupported builds or PAK layouts may disable affected replacements.

Verbose diagnostics are off by default. Logs use `BRQuarteTranslateHelper.log`. The dynamic translation build uses `check-translation` and `static-check`; it does not require old analysis files.

Run `ctest` for automated checks. Game display, text clipping, positioning and interactions with other installed mods still require an actual game run.
