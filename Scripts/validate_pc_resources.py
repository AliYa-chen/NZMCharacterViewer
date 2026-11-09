#!/usr/bin/env python3
"""Verify the published PC resource files without Unreal Engine or game tools."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path,
                        default=Path(__file__).resolve().parent.parent / "PCResources")
    args = parser.parse_args()
    root = args.root.resolve()
    manifest = json.loads((root / "source_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        parser.error("Unsupported PC resource manifest version")
    seen = set()
    counts = {"files": 0, "bytes": 0, "png": 0, "actorx": 0, "hdr": 0}
    for row in manifest["files"]:
        relative = row["file"]
        if not isinstance(relative, str) or relative.casefold() in seen:
            raise ValueError(f"Invalid or duplicate file: {relative}")
        seen.add(relative.casefold())
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"Missing or outside resource root: {relative}")
        if not re.fullmatch(r"[0-9a-f]{64}", row["sha256"]):
            raise ValueError(f"Invalid SHA256: {relative}")
        if path.stat().st_size != row["bytes"]:
            raise ValueError(f"File length mismatch: {relative}")
        hasher = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                hasher.update(block)
        actual = hasher.hexdigest()
        if actual != row["sha256"]:
            raise ValueError(f"SHA256 mismatch: {relative}")
        counts["files"] += 1
        counts["bytes"] += row["bytes"]
        suffix = path.suffix.lower()
        if suffix == ".png":
            with path.open("rb") as stream:
                header = stream.read(33)
            if header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
                raise ValueError(f"PNG header missing: {relative}")
            width, height = struct.unpack_from(">II", header, 16)
            if not width or not height:
                raise ValueError(f"Empty PNG: {relative}")
            counts["png"] += 1
        elif suffix in (".psk", ".pskx"):
            with path.open("rb") as stream:
                if stream.read(20).rstrip(b"\0") != b"ACTRHEAD":
                    raise ValueError(f"ActorX header missing: {relative}")
            counts["actorx"] += 1
        elif suffix == ".hdr":
            with path.open("rb") as stream:
                if not stream.read(16).startswith((b"#?RADIANCE", b"#?RGBE")):
                    raise ValueError(f"Radiance header missing: {relative}")
            counts["hdr"] += 1
    if counts != manifest["counts"]:
        raise ValueError(f"Manifest totals disagree: {counts}")
    actual_paths = {p.relative_to(root).as_posix().casefold()
                    for p in root.rglob("*") if p.is_file() and p.name != "source_manifest.json"}
    if actual_paths != seen:
        raise ValueError("Resource tree contains missing or unlisted files")
    print(json.dumps({"status": "passed", **counts,
                      "unreal_editor_import": "not_verified",
                      "animation_and_visual_result": "not_verified"}, indent=2))


if __name__ == "__main__":
    main()
