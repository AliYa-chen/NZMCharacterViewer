#!/usr/bin/env python3
"""Engine-free source/manifest checks; does not claim C++ compilation or play-test."""
import ast
import argparse
import hashlib
import json
from pathlib import Path
import struct

from manifest_tools import validate_manifest

ROOT = Path(__file__).resolve().parent.parent


def unicode_codepoint_present(font, codepoint):
    table_count = struct.unpack_from(">H", font, 4)[0]
    cmap_offset = None
    for i in range(table_count):
        tag, _, offset, _ = struct.unpack_from(">4sIII", font, 12 + i * 16)
        if tag == b"cmap":
            cmap_offset = offset
            break
    if cmap_offset is None:
        return False
    count = struct.unpack_from(">H", font, cmap_offset + 2)[0]
    for i in range(count):
        platform, encoding, relative = struct.unpack_from(">HHI", font, cmap_offset + 4 + i * 8)
        offset = cmap_offset + relative
        format_number = struct.unpack_from(">H", font, offset)[0]
        if platform not in (0, 3) or format_number != 12:
            continue
        groups = struct.unpack_from(">I", font, offset + 12)[0]
        for j in range(groups):
            start, end, first_glyph = struct.unpack_from(">III", font, offset + 16 + j * 12)
            if start <= codepoint <= end:
                return first_glyph + codepoint - start != 0
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-sources", action="store_true",
                        help="Also require and validate the local SourceAssets import inputs")
    parser.add_argument("--require-content", action="store_true",
                        help="Require the checked-in viewer map and runtime catalog")
    args = parser.parse_args()
    project = json.loads((ROOT / "NZMCharacterViewer.uproject").read_text())
    assert project["EngineAssociation"] == "5.8"
    assert project["Modules"][0]["Name"] == "NZMCharacterViewer"
    python_files = sorted((ROOT / "Scripts").glob("*.py"))
    for path in python_files:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    required = ["Source/NZMCharacterViewer.Target.cs", "Source/NZMCharacterViewerEditor.Target.cs",
                "Source/NZMCharacterViewer/NZMCharacterViewer.Build.cs", "Config/DefaultEngine.ini",
                "Config/DefaultInput.ini", "Config/DefaultGame.ini", "Config/DefaultDeviceProfiles.ini"]
    for relative in required:
        assert (ROOT / relative).is_file(), relative
    headers = list((ROOT / "Source").rglob("*.h"))
    for path in headers:
        includes = [line for line in path.read_text().splitlines() if line.startswith("#include")]
        if "UCLASS(" in path.read_text() or "USTRUCT(" in path.read_text():
            assert includes[-1] == f'#include "{path.stem}.generated.h"', path
    config = (ROOT / "Config/DefaultEngine.ini").read_text()
    for token in ("r.Mobile.ShadingPath=0", "r.RayTracing=False", "r.Nanite.ProjectEnabled=False",
                  "r.DynamicGlobalIlluminationMethod=0", "MinimumiOSVersion=IOS_17",
                  "bSupportsPortraitOrientation=True"):
        assert token in config, token
    packaging = (ROOT / "Config/DefaultGame.ini").read_text()
    assert 'DirectoriesToAlwaysStageAsUFS=(Path="Slate/Fonts")' in packaging
    font_file = ROOT / "Content/Slate/Fonts/NotoSansCJKsc-Regular.otf"
    font = font_file.read_bytes()
    assert font[:4] == b"OTTO", "Not an OTF font"
    missing = [c for c in set("人物查看重置视角服装部件上一项下触摸旋转双指缩放Naimei0123456789")
               if not unicode_codepoint_present(font, ord(c))]
    assert not missing, f"字体缺少字形：{missing}"
    assert "SIL OPEN FONT LICENSE" in (font_file.parent / "OFL.txt").read_text()
    sources_present = (ROOT / "SourceAssets/manifest.json").is_file()
    if args.require_sources and not sources_present:
        parser.error("Missing local SourceAssets/manifest.json; see Docs/ASSET_IMPORT.md")
    if args.require_content:
        for relative in ("Content/Viewer/Maps/L_Viewer.umap", "Content/Viewer/Data/DA_ViewerCatalog.uasset",
                         "Content/Viewer/Environment/DA_Environment.uasset"):
            assert (ROOT / relative).is_file(), relative
    manifest, stats = validate_manifest(ROOT / "SourceAssets") if sources_present else (
        {"characters": []}, {"status": "not_checked", "reason": "SourceAssets are local-only import inputs"})
    # Validate the explicit source evidence hashes when supplied in the manifest.
    for character in manifest["characters"]:
        for outfit in character["outfits"]:
            for part in outfit["parts"]:
                digest = part.get("sha256")
                if isinstance(digest, str):
                    assert hashlib.sha256((ROOT / "SourceAssets" / part["mesh"]).read_bytes()).hexdigest() == digest, part["id"]
    report = {"checks": "passed", "engine_compilation": "not_checked_by_this_script", "editor_import": "not_checked_by_this_script",
              "ios_device_test": "not_checked_by_this_script", "python_syntax_files": len(python_files),
              "reflected_headers": len(headers), "font_sha256": hashlib.sha256(font).hexdigest(),
              "manifest": stats}
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
