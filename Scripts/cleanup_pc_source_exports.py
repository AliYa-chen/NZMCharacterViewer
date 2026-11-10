#!/usr/bin/env python3
"""Remove the published raw PC export after the saved HD viewer passes validation.

Default is a read-only preflight. --apply removes only this project's exact
PCResources directory; Content, Git history and compilation products are kept.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile


ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "Saved/PCViewerIntegration"
SOURCE_COMMIT = "03152adb7ce937649f890455d6650f346e82acc1"
OUTPUT = ROOT / "Docs/PCViewerIntegration/source_cleanup.json"


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def plan(index):
    path = WORK / "asset_plan.json"
    if path.is_file():
        payload = path.read_bytes()
    else:
        with tarfile.open(ROOT / "Docs/PCAssetConversion/conversion_evidence.tar.gz") as archive:
            payload = archive.extractfile("Saved/PCAssetConversion/asset_plan.json").read()
    if hashlib.sha256(payload).hexdigest() != index["plan_sha256"]:
        raise ValueError("Source conversion plan differs from the verified index")
    return json.loads(payload)


def verify_new_assets():
    records = []
    for row in read(WORK / "animation_report.json")["parts"]:
        if row.get("status") in ("adapted", "reused"):
            records.append({"file": row["asset_file"], "bytes": row["asset_bytes"], "sha256": row["asset_sha256"]})
    records += [row["texture"] for row in read(WORK / "portraits_report.json")["portraits"]]
    records += read(WORK / "material_overrides_report.json")["materials"]
    records += [row["profile_asset"] for row in read(WORK / "dynamics_report.json")["parts"]
                if row.get("status") == "derived_compatible_subset"]
    environment = read(WORK / "viewer_environment_report.json")
    records += [row["asset"] for row in environment["environments"]]
    seen = set()
    for row in records:
        relative = row["file"]
        path = (ROOT / relative).resolve()
        if not path.is_relative_to(ROOT / "Content") or path.is_symlink():
            raise ValueError("New saved asset is outside Content: " + relative)
        if path.stat().st_size != row["bytes"] or digest(path) != row["sha256"]:
            raise ValueError("New saved asset changed after verification: " + relative)
        seen.add(relative)
    return len(seen)


def verify_archived_evidence(required_paths, catalog_sha):
    manifest_path = ROOT / "Docs/PCViewerIntegration/evidence_manifest.json"
    manifest = read(manifest_path)
    archive_row = manifest["archive"]
    archive_path = ROOT / archive_row["file"]
    if not archive_path.resolve().is_relative_to(ROOT / "Docs/PCViewerIntegration"):
        raise ValueError("Final evidence archive is outside its declared directory")
    if archive_path.stat().st_size != archive_row["bytes"] or digest(archive_path) != archive_row["sha256"]:
        raise ValueError("Final evidence archive fingerprint differs from its manifest")
    if manifest.get("catalog_sha256") != catalog_sha:
        raise ValueError("Final evidence archive was made for another Catalog")
    archived = {row["file"]: row for row in manifest["files"]}
    if len(archived) != len(manifest["files"]) or not set(required_paths).issubset(archived):
        raise ValueError("Final evidence archive is missing required current reports")
    with tarfile.open(archive_path) as archive:
        members = archive.getmembers()
        if any(not member.isfile() for member in members) or {m.name for m in members} != set(archived):
            raise ValueError("Final archive member set differs from its manifest")
        for member in members:
            row = archived[member.name]
            payload = archive.extractfile(member).read()
            if len(payload) != row["bytes"] or hashlib.sha256(payload).hexdigest() != row["sha256"]:
                raise ValueError("Archived evidence bytes differ: " + member.name)
    for filename in required_paths:
        if digest(ROOT / filename) != archived[filename]["sha256"]:
            raise ValueError("Final report changed after archiving: " + filename)
    return digest(manifest_path)


def preflight(skip_mobile_validation=False):
    source = ROOT / "PCResources"
    if source.is_symlink() or source.resolve() != source or not source.is_dir():
        raise ValueError("Expected the original project PCResources directory, without symlinks")
    index_path = ROOT / "Docs/PCAssetConversion/asset_index.json"
    index = read(index_path)
    asset_plan = plan(index)
    expected = {row["file"]: row for row in asset_plan["inputs"]}
    actual, sidecars = {}, []
    for path in source.rglob("*"):
        if path.is_symlink():
            raise ValueError("Raw source tree contains a symlink: " + str(path))
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT).as_posix()
        if path.name == ".DS_Store":
            sidecars.append({"file": relative, "bytes": path.stat().st_size})
        else:
            actual[relative] = path
    if set(actual) != set(expected):
        raise ValueError("Raw file set differs from the verified input set: " + json.dumps({
            "missing": sorted(set(expected) - set(actual)), "unexpected": sorted(set(actual) - set(expected))}))
    source_bytes = 0
    for name, path in actual.items():
        row = expected[name]
        if path.stat().st_size != row["bytes"] or digest(path) != row["sha256"]:
            raise ValueError("Raw source changed since conversion: " + name)
        source_bytes += path.stat().st_size
    tree = subprocess.run(["git", "rev-parse", SOURCE_COMMIT + ":PCResources"], cwd=ROOT,
                          check=True, capture_output=True, text=True).stdout.strip()
    changes = subprocess.run(["git", "diff", "--name-only", SOURCE_COMMIT, "--", "PCResources"], cwd=ROOT,
                             check=True, capture_output=True, text=True).stdout.strip()
    if changes:
        raise ValueError("Raw source differs from its published Git snapshot")
    for row in index["assets"]:
        path = ROOT / row["file"]
        if not path.is_file() or path.stat().st_size != row["bytes"] or digest(path) != row["sha256"]:
            raise ValueError("Verified converted package changed: " + row["file"])
    required = {
        "animation_report.json": "passed", "portraits_report.json": "passed",
        "material_overrides_report.json": "passed", "dynamics_report.json": "passed",
        "viewer_environment_report.json": "passed",
        "catalog_import_report.json": "passed", "integration_audit.json": "passed",
        "desktop_smoke_report.json": "pass",
    }
    if not skip_mobile_validation:
        required["mobile_smoke_report.json"] = "pass"
    evidence = []
    for filename, status in required.items():
        path = WORK / filename
        report = read(path)
        if report.get("status") != status:
            raise ValueError("Required final validation has not passed: " + filename)
        evidence.append({"file": path.relative_to(ROOT).as_posix(), "sha256": digest(path), "status": status})
    catalog = ROOT / "Content/Viewer/Data/DA_ViewerCatalog.uasset"
    # The import report records the saved Catalog even if its audit disk row
    # did not request a full hash during dependency traversal.
    if digest(catalog) != read(WORK / "catalog_import_report.json")["catalog_sha256"]:
        raise ValueError("Runtime Catalog changed since publication validation")
    integration = read(WORK / "integration_audit.json")
    if integration.get("catalog_file_sha256") != digest(catalog):
        raise ValueError("Final integration audit is stale for the current Catalog")
    input_keys = {"plan_sha256": "viewer_plan.json", "baseline_catalog_sha256": "baseline_catalog.json",
                  "animation_report_sha256": "animation_report.json", "portraits_report_sha256": "portraits_report.json",
                  "dynamics_report_sha256": "dynamics_report.json", "material_overrides_report_sha256": "material_overrides_report.json"}
    for key, filename in input_keys.items():
        if integration["inputs"].get(key) != digest(WORK / filename):
            raise ValueError("Final integration audit uses stale inputs: " + filename)
    expected_cases = {(c["id"], o["id"]) for c in integration["characters"] for o in c["outfits"]}
    if len(expected_cases) != 26:
        raise ValueError("Expected all 26 Catalog outfit selections before cleanup")
    module = ROOT / "Binaries/Mac/libUnrealEditor-NZMCharacterViewer.dylib"
    runtime_reports = [("desktop_smoke_report.json", "SM6")]
    if not skip_mobile_validation:
        runtime_reports.append(("mobile_smoke_report.json", "ES3_1"))
    for filename, feature in runtime_reports:
        smoke = read(WORK / filename)
        cases = smoke["results"]
        if smoke.get("planned_combinations") != 26 or smoke.get("completed_combinations") != 26 or len(cases) != 26:
            raise ValueError("Runtime validation does not cover all 26 combinations: " + filename)
        if {(r["character_id"], r["outfit_id"]) for r in cases} != expected_cases or any(r.get("status") != "pass" for r in cases):
            raise ValueError("Runtime validation has missing or failed cases: " + filename)
        if smoke.get("validated_catalog_sha256") != digest(catalog) or smoke.get("validated_runtime_module_sha256") != digest(module):
            raise ValueError("Runtime validation uses another Catalog or executable module: " + filename)
        for case in cases:
            rendering = case["viewport_and_ui"]["rendering"]
            if rendering.get("using_null_rhi") is not False or "metal" not in rendering.get("rhi_name", "").lower() or rendering.get("scene_feature_level_name") != feature:
                raise ValueError("Runtime validation used an unexpected rendering path: " + filename)
    ios_path = ROOT / "Saved/BuildIOSCompileVerification.json"
    ios = read(ios_path)
    if ios.get("status") != "passed_unsigned_compile_link_only":
        raise ValueError("Current unsigned iOS compile/link verification has not passed")
    for filename, expected_sha in ios.get("source_sha256", {}).items():
        if digest(ROOT / filename) != expected_sha:
            raise ValueError("Runtime source changed after iOS compilation: " + filename)
    current_sources = {p.relative_to(ROOT).as_posix() for p in (ROOT / "Source").rglob("*")
                       if p.is_file() and p.suffix in (".h", ".cpp", ".cs")}
    if set(ios.get("source_sha256", {})) != current_sources:
        raise ValueError("Runtime source file set changed after iOS compilation")
    if digest(Path(ios["binary"]["path"])) != ios["binary"]["sha256"] or digest(Path(ios["log"])) != ios["log_sha256"]:
        raise ValueError("iOS verification binary or log changed after compilation")
    new_assets = verify_new_assets()
    archive_manifest_sha = verify_archived_evidence(
        [row["file"] for row in evidence] + [ios_path.relative_to(ROOT).as_posix()], digest(catalog))
    if skip_mobile_validation:
        archived = read(ROOT / "Docs/PCViewerIntegration/evidence_manifest.json")
        if archived.get("mobile_runtime_validation") != "not_run_user_requested_cleanup":
            raise ValueError("The archive does not record the requested stop to additional mobile testing")
    aliases = []
    texture_by_hash = {}
    for row in asset_plan["textures"]:
        texture_by_hash.setdefault(row["sha256"], []).append(row["target"])
    for filename in sorted(set(expected) - set(asset_plan["files"])):
        targets = texture_by_hash.get(expected[filename]["sha256"], [])
        if not targets:
            raise ValueError("Source input lacks a converted output or exact image-byte alias: " + filename)
        aliases.append({"file": filename, "source_sha256": expected[filename]["sha256"],
                        "converted_exact_image_aliases": targets})
    return {"schema_version": 1, "status": "preflight_passed", "directory": "PCResources",
            "source_files": len(actual), "source_bytes": source_bytes,
            "finder_sidecars": sidecars, "total_removal_bytes": source_bytes + sum(row["bytes"] for row in sidecars),
            "published_source_commit": SOURCE_COMMIT, "published_source_tree": tree,
            "source_restore_command": "git restore --source=" + SOURCE_COMMIT + " -- PCResources",
            "asset_index_sha256": digest(index_path), "verified_converted_packages": len(index["assets"]),
            "plan_sha256": index["plan_sha256"], "input_aliases": aliases,
            "evidence": evidence, "ios_compile_report_sha256": digest(ios_path),
            "runtime_catalog_sha256": digest(catalog),
            "verified_new_packages": new_assets, "evidence_manifest_sha256": archive_manifest_sha,
            "mobile_runtime_validation": "not_run_user_requested_cleanup" if skip_mobile_validation else "passed_local_metal_preview",
            "scope": "Remove only redundant raw working-tree exports. Saved editable Content packages and published source Git history are retained."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--skip-mobile-validation", action="store_true",
                        help="Record the explicit user request to stop additional mobile testing before cleanup")
    args = parser.parse_args()
    report = preflight(args.skip_mobile_validation)
    if args.apply:
        shutil.rmtree(ROOT / "PCResources")
        if (ROOT / "PCResources").exists():
            raise RuntimeError("Raw source directory still exists after removal")
        report["status"] = "removed"
    report["recorded_at_utc"] = datetime.now(timezone.utc).isoformat()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "directory", "source_files", "total_removal_bytes", "verified_converted_packages")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
