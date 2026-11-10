#!/usr/bin/env python3
"""Cache verified shader/cloth JSON from the published PC source snapshot.

The original PCResources tree remains deleted. Only JSON needed for the
current UI parts, their material dependencies, and the exported Nv cloth
records is copied under Saved/PCFeatureIntegration/Sources. Source hashes
come from the frozen conversion plan; old viewer reports come from the
fingerprinted integration archive. No Content package is changed.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile


ROOT = Path(__file__).resolve().parent.parent
SOURCE_COMMIT = "03152adb7ce937649f890455d6650f346e82acc1"
WORK_RELATIVE = "Saved/PCFeatureIntegration"
MANIFEST_RELATIVE = WORK_RELATIVE + "/source_manifest.json"
CONVERSION_ARCHIVE = "Docs/PCAssetConversion/conversion_evidence.tar.gz"
PLAN_MEMBER = "Saved/PCAssetConversion/asset_plan.json"
PLAN_CACHE = "Saved/PCViewerIntegration/asset_plan.json"
VIEWER_ARCHIVE = "Docs/PCViewerIntegration/integration_evidence.tar.gz"
VIEWER_MANIFEST = "Docs/PCViewerIntegration/evidence_manifest.json"
VIEWER_PLAN = "Saved/PCViewerIntegration/viewer_plan.json"
_VERIFIED_MANIFESTS = {}


def digest_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def safe_path(root, relative):
    name = PurePosixPath(str(relative))
    if name.is_absolute() or not name.parts or any(part in ("", ".", "..") for part in name.parts):
        raise ValueError("Expected a project-relative path: " + str(relative))
    root = Path(root).resolve()
    path = root.joinpath(*name.parts)
    if not path.resolve().is_relative_to(root):
        raise ValueError("Path escapes the project: " + str(relative))
    for candidate in (path, *path.parents):
        if candidate == root:
            break
        if candidate.is_symlink():
            raise ValueError("Cache path contains a symlink: " + str(relative))
    return path


def checked_payload(payload, record, label):
    if len(payload) != record["bytes"] or digest_bytes(payload) != record["sha256"]:
        raise ValueError("Fingerprint mismatch: " + label)
    return payload


def write_exact(root, relative, payload):
    path = safe_path(root, relative)
    if path.exists():
        if not path.is_file() or path.read_bytes() != payload:
            raise ValueError("Existing cache/report differs from its frozen source: " + relative)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    return path


def conversion_plan_payload(root=ROOT):
    root = Path(root)
    index = read_json(root / "Docs/PCAssetConversion/asset_index.json")
    with tarfile.open(root / CONVERSION_ARCHIVE) as archive:
        member = archive.getmember(PLAN_MEMBER)
        if not member.isfile():
            raise ValueError("The archived conversion plan is not a regular file")
        payload = archive.extractfile(member).read()
    if digest_bytes(payload) != index["plan_sha256"]:
        raise ValueError("Conversion plan differs from the verified asset index")
    return payload


def read_asset_plan(root=ROOT):
    """Return the immutable source-conversion plan, verifying its index hash."""
    root = Path(root)
    path = safe_path(root, PLAN_CACHE)
    payload = path.read_bytes() if path.is_file() else conversion_plan_payload(root)
    if digest_bytes(payload) != read_json(root / "Docs/PCAssetConversion/asset_index.json")["plan_sha256"]:
        raise ValueError("Cached conversion plan differs from the verified asset index")
    return json.loads(payload)


def frozen_reports(root=ROOT):
    """Read verified archived viewer JSON without extracting arbitrary members."""
    root = Path(root)
    manifest = read_json(root / VIEWER_MANIFEST)
    record = manifest["archive"]
    if record["file"] != VIEWER_ARCHIVE:
        raise ValueError("Unexpected integration archive path")
    archive_path = safe_path(root, VIEWER_ARCHIVE)
    if archive_path.stat().st_size != record["bytes"] or digest(archive_path) != record["sha256"]:
        raise ValueError("Integration archive differs from its published manifest")
    indexed = {row["file"]: row for row in manifest["files"]}
    if len(indexed) != len(manifest["files"]):
        raise ValueError("Duplicate integration archive member fingerprints")
    reports = {}
    with tarfile.open(archive_path) as archive:
        names = {member.name for member in archive.getmembers()}
        if names != set(indexed):
            raise ValueError("Integration archive member set differs from its manifest")
        for name in sorted(indexed):
            if not name.startswith("Saved/PCViewerIntegration/") or not name.endswith(".json"):
                continue
            member = archive.getmember(name)
            if not member.isfile():
                raise ValueError("Archived report is not a regular file: " + name)
            reports[name] = checked_payload(archive.extractfile(member).read(), indexed[name], name)
    if VIEWER_PLAN not in reports:
        raise ValueError("The frozen UI viewer plan is missing from the archive")
    return reports


def source_references(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from source_references(child)
    elif isinstance(value, list):
        for child in value:
            yield from source_references(child)
    elif isinstance(value, str) and value.startswith("PCResources/") and value.endswith(".json"):
        yield value


def package(value):
    if isinstance(value, dict):
        value = value.get("ObjectPath") or value.get("AssetPathName")
    if not isinstance(value, str) or value in ("", "None"):
        return None
    if value.startswith("NZM/Content/"):
        value = "/Game/" + value[len("NZM/Content/"):]
    return value.split(".", 1)[0]


def selected_sources(asset_plan, viewer_plan, additional=()):
    """Select JSON by explicit UI use, shader dependency scope, or Nv record type."""
    inputs = {row["file"]: row for row in asset_plan["inputs"]}
    if len(inputs) != len(asset_plan["inputs"]):
        raise ValueError("Duplicate conversion input paths")
    json_inputs = {name: row for name, row in inputs.items() if name.endswith(".json")}
    reasons = defaultdict(set)

    def add(name, reason, required=True):
        if name in json_inputs:
            reasons[name].add(reason)
        elif required:
            raise ValueError("Required JSON is absent from the verified conversion inputs: " + str(name))

    def metadata(row, reason):
        for name in source_references(row):
            add(name, reason)

    for name in source_references(viewer_plan):
        add(name, "current_UI_configuration_or_mesh")
    for name in additional:
        add(name, "explicit_additional_JSON")

    parts = [part for character in viewer_plan["characters"]
             for outfit in character["outfits"] for part in outfit["parts"]]
    meshes = {row["target"]: row for row in asset_plan["meshes"]}
    materials = {row["target"]: row for row in asset_plan["materials"]}
    material_packages = {row["source_package"]: row for row in asset_plan["materials"]}
    texture_packages = {}
    for row in asset_plan["textures"]:
        for name in row.get("source_packages", [row.get("source_package")]):
            if name:
                texture_packages[name] = row
    visited_materials = set()

    def material(row):
        if row["target"] in visited_materials:
            return
        visited_materials.add(row["target"])
        metadata(row, "current_UI_material_or_parent")
        for parameter in row.get("parameters", {}).get("textures", []):
            source = package(parameter.get("ParameterValue"))
            if source in texture_packages:
                metadata(texture_packages[source], "current_UI_material_texture_metadata")
        parent = row.get("parent_package")
        if parent in material_packages:
            material(material_packages[parent])

    for part in parts:
        metadata(meshes[part["mesh_target"]], "current_UI_mesh_and_variants")
        for target in part["materials"]:
            if target is not None:
                material(materials[target])

    # The 25 exported Nv assets span multiple source LODs. Keep every serialized
    # record so the cloth planner can report exact available coverage.
    for name in json_inputs:
        if name.startswith("PCResources/Shading/"):
            add(name, "exported_private_shader_dependencies")
        if "_Clothing_" in name:
            add(name, "exported_Nv_clothing_config_LOD_or_physical_mesh")
            fields = PurePosixPath(name).parts
            mesh_index = next((index for index, field in enumerate(fields)
                               if field.startswith("SK_") and field.endswith("_Mesh")), None)
            if mesh_index is not None:
                add("/".join(fields[:mesh_index + 1]) + ".json", "Nv_clothing_parent_mesh")

    # Expressions and subobjects are exported below their owner package. Keep
    # those exact descendants, including alternative metadata exports of each
    # selected UI mesh, instead of relying on ambiguous filename matches.
    metadata_prefixes = {name[:-5] + "/" for name in reasons
                         if "/Mesh/" in name or "/Materials/" in name or "/MaterialLib/" in name}
    for name in json_inputs:
        if any(name.startswith(prefix) for prefix in metadata_prefixes):
            add(name, "selected_owner_serialized_subobject")
    return [{**json_inputs[name], "reasons": sorted(reasons[name])} for name in sorted(reasons)]


def read_git_blob(root, commit, relative):
    return subprocess.run(["git", "show", commit + ":" + relative], cwd=root,
                          check=True, capture_output=True).stdout


def verified_manifest(root=ROOT):
    root = Path(root).resolve()
    paths = [safe_path(root, relative) for relative in
             (MANIFEST_RELATIVE, "Docs/PCAssetConversion/asset_index.json", PLAN_CACHE)]
    signature = tuple((str(path), path.stat().st_size, path.stat().st_mtime_ns) for path in paths)
    cached = _VERIFIED_MANIFESTS.get(str(root))
    if cached is not None and cached[0] == signature:
        return cached[1], cached[2]
    manifest = read_json(paths[0])
    if manifest.get("status") != "verified_selected_source_cache" or manifest.get("source_commit") != SOURCE_COMMIT:
        raise ValueError("PC feature source cache has an unexpected identity")
    if manifest["asset_index_sha256"] != digest(paths[1]):
        raise ValueError("PC feature source cache uses another asset index")
    plan = read_asset_plan(root)
    if manifest["asset_plan_sha256"] != read_json(paths[1])["plan_sha256"]:
        raise ValueError("Source cache plan fingerprint differs from the verified index")
    original = {row["file"]: row for row in plan["inputs"]}
    sources = {row["source_file"]: row for row in manifest["sources"]}
    if len(sources) != len(manifest["sources"]):
        raise ValueError("Duplicate source cache entries")
    for name, row in sources.items():
        expected = original.get(name)
        if expected is None or not name.endswith(".json") or any(row[key] != expected[key] for key in ("bytes", "sha256")):
            raise ValueError("Source cache fingerprint is absent from the verified conversion plan: " + name)
        if row["cache_file"] != WORK_RELATIVE + "/Sources/" + name:
            raise ValueError("Source cache manifest contains an unexpected destination")
    _VERIFIED_MANIFESTS[str(root)] = (signature, manifest, sources)
    return manifest, sources


def source_path(relative, root=ROOT):
    """Return a fingerprint-checked cached JSON path; never fall back to raw files."""
    root = Path(root)
    _manifest, sources = verified_manifest(root)
    record = sources.get(relative)
    if record is None:
        raise ValueError("Source JSON is absent from the cache manifest: " + str(relative))
    expected = WORK_RELATIVE + "/Sources/" + relative
    path = safe_path(root, expected)
    if path.stat().st_size != record["bytes"] or digest(path) != record["sha256"]:
        raise ValueError("Cached source JSON differs from the published source: " + relative)
    return path


def load_source(relative, root=ROOT):
    """Load one selected JSON after verifying its recorded source fingerprint."""
    return read_json(source_path(relative, root))


def export(root=ROOT, additional=()):
    root = Path(root).resolve()
    resolved = subprocess.run(["git", "rev-parse", SOURCE_COMMIT + "^{commit}"], cwd=root,
                              check=True, capture_output=True, text=True).stdout.strip()
    if resolved != SOURCE_COMMIT:
        raise ValueError("The published source commit did not resolve exactly")
    plan_payload = conversion_plan_payload(root)
    asset_plan = json.loads(plan_payload)
    reports = frozen_reports(root)
    viewer_plan = json.loads(reports[VIEWER_PLAN])
    selected = selected_sources(asset_plan, viewer_plan, additional)
    restored = []
    for name, payload in sorted({**reports, PLAN_CACHE: plan_payload}.items()):
        write_exact(root, name, payload)
        restored.append({"file": name, "bytes": len(payload), "sha256": digest_bytes(payload)})
    records = []
    for row in selected:
        relative = row["file"]
        cache_relative = WORK_RELATIVE + "/Sources/" + relative
        cached = safe_path(root, cache_relative)
        if cached.is_file():
            payload = checked_payload(cached.read_bytes(), row, relative)
        else:
            payload = checked_payload(read_git_blob(root, SOURCE_COMMIT, relative), row, relative)
        # Validate JSON while retaining its exact original bytes.
        json.loads(payload.decode("utf-8-sig"))
        write_exact(root, cache_relative, payload)
        records.append({"source_file": relative, "cache_file": cache_relative,
                        "bytes": row["bytes"], "sha256": row["sha256"], "reasons": row["reasons"]})
    manifest = {
        "schema_version": 1, "status": "verified_selected_source_cache",
        "source_commit": SOURCE_COMMIT,
        "asset_index_sha256": digest(root / "Docs/PCAssetConversion/asset_index.json"),
        "asset_plan_sha256": digest_bytes(plan_payload),
        "viewer_evidence_manifest_sha256": digest(root / VIEWER_MANIFEST),
        "viewer_plan_sha256": digest_bytes(reports[VIEWER_PLAN]),
        "scope": "Selected original JSON only; PCResources is not restored and Content assets are not modified.",
        "counts": {"source_JSON_files": len(records), "source_bytes": sum(row["bytes"] for row in records),
                   "restored_frozen_reports_and_plan": len(restored),
                   "Nv_physical_mesh_records": sum(row["source_file"].endswith("/ClothPhysicalMeshDataNv.json") for row in records)},
        "sources": records, "restored_frozen_reports": restored,
    }
    output = safe_path(root, MANIFEST_RELATIVE)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def verify(root=ROOT):
    root = Path(root).resolve()
    manifest, _sources = verified_manifest(root)
    if manifest.get("asset_plan_sha256") != digest(root / PLAN_CACHE):
        raise ValueError("Source cache plan fingerprint differs from its manifest")
    for row in manifest["sources"]:
        source_path(row["source_file"], root)
    for row in manifest["restored_frozen_reports"]:
        path = safe_path(root, row["file"])
        if path.stat().st_size != row["bytes"] or digest(path) != row["sha256"]:
            raise ValueError("Restored frozen report changed: " + row["file"])
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--source", action="append", default=[], help="Additional exact indexed source JSON path")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = verify(args.root) if args.verify_only else export(args.root, args.source)
    print(json.dumps({"status": result["status"], "manifest": str(Path(args.root).resolve() / MANIFEST_RELATIVE),
                      **result["counts"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
