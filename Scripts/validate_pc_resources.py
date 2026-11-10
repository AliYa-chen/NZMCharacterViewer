#!/usr/bin/env python3
"""Verify the published PC resource files without Unreal Engine or game tools."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import struct


def extended_path(path):
    path = path.resolve()
    if os.name == "nt" and not str(path).startswith("\\\\?\\"):
        text = str(path)
        path = Path("\\\\?\\UNC\\" + text[2:] if text.startswith("\\\\") else "\\\\?\\" + text)
    return path


def validate_character_collection(root, manifest):
    """Check that published plans, dependencies and validation records agree."""
    expansion = manifest.get("character_expansion")
    if not expansion:
        return None
    indexed = {row["file"]: row for row in manifest["files"]}

    def published(relative):
        if relative.startswith("PCResources/"):
            relative = relative[len("PCResources/"):]
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or relative not in indexed:
            raise ValueError(f"Unpublished character reference: {relative}")
        return path, indexed[relative]

    report = json.loads(published(expansion["manifest"])[0].read_text(encoding="utf-8"))
    if report["status"] != "collected_and_data_validated" or report["failed_count"] or report["failures"]:
        raise ValueError("Character collection has failed or unvalidated items")
    plan_path, _ = published("Characters/Configuration/female_mesh_plan.json")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan_meshes = set(plan["mesh_paths"])
    resources = {row["source_package"]: row for row in report["resources"]}
    if len(resources) != report["resource_count"] or len(plan_meshes) != report["mesh_package_count"]:
        raise ValueError("Character resource or mesh count mismatch")
    mesh_files = set()
    image_files = set()
    for name, resource in resources.items():
        if resource["status"] != "exported":
            raise ValueError(f"Unexported character resource: {name}")
        suffixes = set()
        for row in resource["files"]:
            path, primary = published(row["file"])
            if row["sha256"] != primary["sha256"] or row["bytes"] != primary["bytes"]:
                raise ValueError(f"Character resource hash disagrees: {row['file']}")
            suffixes.add(path.suffix.lower())
            if path.suffix.lower() in (".psk", ".pskx"):
                mesh_files.add(row["file"])
            elif path.suffix.lower() in (".png", ".hdr"):
                image_files.add(row["file"])
        if ".json" not in suffixes:
            raise ValueError(f"Character resource lacks metadata: {name}")
        if name in plan_meshes and not suffixes.intersection((".psk", ".pskx")):
            raise ValueError(f"Planned mesh lacks ActorX: {name}")
        if not resource.get("source_records"):
            raise ValueError(f"Character resource lacks selected source provenance: {name}")
        published(resource["export_report"])
    if not plan_meshes.issubset(resources):
        raise ValueError("Character plan contains missing mesh packages")
    for edge in report["mesh_material_edges"] + report["material_dependency_edges"]:
        published(edge["json_file"])
        for key in ("material_package", "texture_package", "parent_package"):
            dependency = edge.get(key)
            if dependency and dependency not in resources:
                raise ValueError(f"Missing character dependency: {dependency}")
    validated = set()
    validation_files = {item["validation_report"] for item in report["steps"] + report["resources"]
                        if item.get("validation_report")}
    for relative in validation_files:
        validation_path, _ = published(relative)
        validation = json.loads(validation_path.read_text(encoding="utf-8"))
        for row in validation["results"]:
            if row["status"] != "pass":
                raise ValueError(f"Character validation failed: {row['file']}")
            published(row["file"])
            validated.add(row["file"])
    if not (mesh_files | image_files).issubset(validated):
        raise ValueError("Character ActorX/PNG/HDR lacks data validation record")
    config_path, _ = published(expansion["configuration"])
    config = json.loads(config_path.read_text(encoding="utf-8"))
    for row in config["sources"]:
        _, primary = published(row["file"])
        if primary["sha256"] != row["sha256"]:
            raise ValueError(f"Character configuration hash disagrees: {row['file']}")

    def check_references(value):
        if isinstance(value, list):
            for item in value:
                check_references(item)
        elif isinstance(value, dict):
            if isinstance(value.get("file"), str) and value["file"].startswith("PCResources/") and "sha256" in value:
                _, row = published(value["file"])
                if value["sha256"] != row["sha256"] or value.get("bytes", row["bytes"]) != row["bytes"]:
                    raise ValueError(f"Character evidence reference disagrees: {value['file']}")
            for item in value.values():
                check_references(item)

    for path in (root / "Characters/Configuration").rglob("*.json"):
        check_references(json.loads(path.read_text(encoding="utf-8")))
    check_references(report.get("supplemental_exports", []))
    catalog_path, _ = published("Characters/catalog.json")
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog_meshes = {mesh["source_package"] for family in catalog["families"]
                      for group in family["groups"] for mesh in group["meshes"]}
    if catalog_meshes != plan_meshes:
        raise ValueError("Character catalog and mesh plan disagree")
    return {"mesh_packages": len(plan_meshes), "resources": len(resources),
            "validated_mesh_and_image_files": len(mesh_files | image_files)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path,
                        default=Path(__file__).resolve().parent.parent / "PCResources")
    args = parser.parse_args()
    root = extended_path(args.root)
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
    characters = validate_character_collection(root, manifest)
    print(json.dumps({"status": "passed", **counts,
                      **({"character_expansion": characters} if characters else {}),
                      "unreal_editor_import": "not_verified",
                      "animation_and_visual_result": "not_verified"}, indent=2))


if __name__ == "__main__":
    main()
