#!/usr/bin/env python3
"""Archive the completed HD viewer evidence before raw/Saved cleanup.

Run after the final desktop/mobile smoke tests and visual reviews pass, or use
--skip-mobile-validation when the user requests cleanup without further tests.
The default writes Docs/PCViewerIntegration/integration_evidence.tar.gz and
evidence_manifest.json atomically after checking every tar member's bytes.
--preflight-only validates and lists the evidence without publishing anything.
--verify-only checks an existing archive without requiring the Saved originals.
This script never removes raw exports, Saved files, Content or build products.
"""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import struct
import subprocess
import tarfile
import tempfile
import zlib


ROOT = Path(__file__).resolve().parent.parent
WORK = "Saved/PCViewerIntegration"
DESTINATION = "Docs/PCViewerIntegration"
ARCHIVE_NAME = "integration_evidence.tar.gz"
MANIFEST_NAME = "evidence_manifest.json"
SOURCE_COMMIT = "03152adb7ce937649f890455d6650f346e82acc1"
MAX_FILE_BYTES = 128 * 1024 * 1024
DEFAULT_MAC_LOG = WORK + "/source_rendering_build.log"
REQUIRED_WORK_FILES = (
    "viewer_plan.json", "baseline_catalog.json", "DA_ViewerCatalog.before_integration.uasset",
    "animation_jobs.json", "animation_report.json", "animation_summary.json",
    "material_overrides_plan.json", "material_overrides_report.json", "eye_occlusion_override_plan.json",
    "shader_restoration_gap_audit.json", "dynamics_report.json", "dynamics_preflight.json", "dynamics_differences.json",
    "viewer_environment_plan.json", "viewer_environment_report.json", "viewer_environments_fresh_verification.json",
    "source_environment_gaps.json", "portraits_report.json", "portraits_visual_review.json", "portraits_contact_sheet.png",
    "catalog_import_report.json", "integration_audit.json", "desktop_smoke_report.json", "mobile_smoke_report.json",
    "runtime_visual_review.json", "pc_outfits_contact_sheet.png", "build_fingerprints.json",
)
PASSED_WORK_REPORTS = (
    "animation_report.json", "animation_summary.json", "material_overrides_report.json", "dynamics_report.json",
    "dynamics_preflight.json", "viewer_environment_report.json", "viewer_environments_fresh_verification.json",
    "catalog_import_report.json", "integration_audit.json", "portraits_report.json",
)
EXCLUDED_PARTS = {"PCResources", "Intermediate", "DerivedDataCache", "__pycache__", ".eye-venv", ".git", "node_modules"}


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def local_path(root, name, max_bytes=MAX_FILE_BYTES):
    """Return an exact, regular project file; reject traversal and symlink parents."""
    root = Path(root).resolve()
    value = Path(name)
    if value.is_absolute():
        require(value.is_relative_to(root), "Evidence path is outside the project: " + str(name))
        name = value.relative_to(root).as_posix()
    else:
        name = str(name)
    parts = PurePosixPath(name).parts
    require(parts and not PurePosixPath(name).is_absolute() and "\\" not in name
            and not any(part in ("", ".", "..") for part in name.split("/")), "Invalid evidence path: " + name)
    require(not any(part in EXCLUDED_PARTS for part in parts)
            and "TASK_CHECKPOINT" not in Path(name).name and Path(name).name != "asset_plan.json",
            "Excluded private, raw or intermediate evidence path: " + name)
    path = root.joinpath(*parts)
    require(not path.is_symlink() and all(not parent.is_symlink() for parent in path.parents if parent != root),
            "Evidence paths must not contain symlinks: " + name)
    require(path.is_file() and path.resolve().is_relative_to(root), "Required evidence file is missing: " + name)
    require(max_bytes is None or path.stat().st_size <= max_bytes, "Evidence file exceeds the archive limit: " + name)
    return path


def file_record(root, name):
    path = local_path(root, name)
    return {"file": path.relative_to(Path(root).resolve()).as_posix(), "bytes": path.stat().st_size, "sha256": digest(path)}


def check_record(root, row):
    current = file_record(root, row["file"])
    require(all(current[key] == row[key] for key in ("file", "bytes", "sha256")),
            "Evidence or saved asset changed after validation: " + row["file"])
    return current


def png_size(path):
    payload = Path(path).read_bytes()
    require(payload[:8] == b"\x89PNG\r\n\x1a\n", "Invalid evidence PNG signature: " + str(path))
    offset, dimensions, saw_data = 8, None, False
    while offset + 12 <= len(payload):
        count = struct.unpack_from(">I", payload, offset)[0]
        end = offset + count + 12
        require(end <= len(payload), "Truncated evidence PNG chunk: " + str(path))
        kind = payload[offset + 4:offset + 8]
        data = payload[offset + 8:offset + 8 + count]
        checksum = struct.unpack_from(">I", payload, offset + 8 + count)[0]
        require((zlib.crc32(kind + data) & 0xffffffff) == checksum, "Evidence PNG chunk checksum differs: " + str(path))
        if dimensions is None:
            require(kind == b"IHDR" and count == 13, "Evidence PNG must begin with a complete IHDR")
            dimensions = struct.unpack_from(">II", data)
            require(all(value > 0 for value in dimensions), "Evidence PNG has no image area")
        elif kind == b"IHDR":
            raise ValueError("Evidence PNG repeats IHDR: " + str(path))
        if kind == b"IDAT":
            saw_data = True
        if kind == b"IEND":
            require(count == 0 and saw_data, "Evidence PNG has an invalid IEND or no image payload")
            # UE RenderTarget exports can retain zero-filled buffer capacity
            # after the valid logical PNG. Arbitrary trailing bytes are refused.
            # UE's render-target exporter can leave unused buffer bytes after
            # the complete PNG stream. Decode only through its verified IEND;
            # the evidence record still hashes every original file byte.
            return dimensions
        offset = end
    raise ValueError("Incomplete evidence PNG; IEND is missing: " + str(path))


def verify_archive(root, manifest, archive_path=None, check_current=False):
    root = Path(root).resolve()
    require(manifest.get("status") == "passed", "Evidence manifest has not passed")
    row = manifest["archive"]
    require(row["file"] == DESTINATION + "/" + ARCHIVE_NAME, "Unexpected evidence archive destination")
    path = Path(archive_path) if archive_path is not None else local_path(root, row["file"], max_bytes=None)
    require(path.stat().st_size == row["bytes"] and digest(path) == row["sha256"], "Archive bytes differ from the manifest")
    records = {item["file"]: item for item in manifest["files"]}
    require(len(records) == len(manifest["files"]) and records, "Archive manifest has empty/duplicate file entries")
    for name in records:
        parts = PurePosixPath(name).parts
        require(parts and not PurePosixPath(name).is_absolute() and "\\" not in name
                and not any(part in ("", ".", "..") for part in name.split("/")) and not any(part in EXCLUDED_PARTS for part in parts)
                and "TASK_CHECKPOINT" not in Path(name).name and Path(name).name != "asset_plan.json", "Unsafe archive member name")
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        require(len(members) == len(records) and {member.name for member in members} == set(records)
                and all(member.isfile() and not member.issym() and not member.islnk() for member in members),
                "Archive member set differs from the manifest")
        for member in members:
            expected = records[member.name]
            require(member.size == expected["bytes"] and member.size <= MAX_FILE_BYTES, "Archive member size differs: " + member.name)
            stream = archive.extractfile(member)
            require(stream is not None, "Archive member cannot be read: " + member.name)
            result = hashlib.sha256()
            count = 0
            for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                count += len(block)
                result.update(block)
            require(count == expected["bytes"] and result.hexdigest() == expected["sha256"], "Archive member checksum differs: " + member.name)
    if check_current:
        for expected in records.values():
            check_record(root, expected)
    return {"status": "verified", "files": len(records), "archive_bytes": row["bytes"], "archive_sha256": row["sha256"]}


def publish_archive(root, records, metadata):
    """Create, fully reread and publish a bundle; metadata never enters the tar."""
    root = Path(root).resolve()
    require(records and len({row["file"] for row in records}) == len(records), "Duplicate/empty archive inputs")
    output = root / DESTINATION
    output.mkdir(parents=True, exist_ok=True)
    require(output.resolve() == output and not output.is_symlink(), "Archive destination contains a symlink")
    archive_fd, archive_temporary = tempfile.mkstemp(prefix=".integration_evidence_", suffix=".tar.gz", dir=output)
    manifest_temporary = None
    try:
        with os.fdopen(archive_fd, "wb") as raw:
            with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                    for row in sorted(records, key=lambda value: value["file"]):
                        path = local_path(root, row["file"])
                        payload = path.read_bytes()
                        require(len(payload) == row["bytes"] and hashlib.sha256(payload).hexdigest() == row["sha256"],
                                "Evidence changed while archiving: " + row["file"])
                        info = tarfile.TarInfo(row["file"])
                        info.size, info.mode, info.mtime = len(payload), 0o644, 0
                        info.uid = info.gid = 0
                        info.uname = info.gname = ""
                        archive.addfile(info, io.BytesIO(payload))
            raw.flush()
            os.fsync(raw.fileno())
        temporary = Path(archive_temporary)
        manifest = {**metadata, "schema_version": 1, "status": "passed", "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
                    "archive": {"file": DESTINATION + "/" + ARCHIVE_NAME, "bytes": temporary.stat().st_size, "sha256": digest(temporary)},
                    "files": sorted(records, key=lambda value: value["file"])}
        verify_archive(root, manifest, temporary, check_current=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=".evidence_manifest_", suffix=".json", dir=output, delete=False) as stream:
            manifest_temporary = stream.name
            json.dump(manifest, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output / ARCHIVE_NAME)
        os.replace(manifest_temporary, output / MANIFEST_NAME)
        return manifest
    finally:
        for temporary in (archive_temporary, manifest_temporary):
            if temporary is None:
                continue
            path = Path(temporary)
            if path.exists():
                path.unlink()


def preflight(root, mac_log=DEFAULT_MAC_LOG, additions=(), skip_mobile_validation=False):
    root = Path(root).resolve()
    require((root / "NZMCharacterViewer.uproject").is_file(), "Expected the NZMCharacterViewer project root")
    required_work_files = tuple(name for name in REQUIRED_WORK_FILES if not (skip_mobile_validation and name == "mobile_smoke_report.json"))
    names = {WORK + "/" + name for name in required_work_files}
    names.update(("Saved/ContentAudit.json", "Saved/BuildIOSCompile.log", "Saved/BuildIOSCompileVerification.json",
                  "Content/Viewer/Data/DA_ViewerCatalog.uasset", "Docs/PCAssetConversion/asset_index.json",
                  "Docs/PCAssetConversion/evidence_manifest.json", mac_log))
    documents = {name: read(local_path(root, WORK + "/" + name)) for name in required_work_files if name.endswith(".json")}
    for name in PASSED_WORK_REPORTS:
        require(documents[name].get("status") == "passed", "Final validation is incomplete: " + name)
    for name in ("portraits_visual_review.json", "runtime_visual_review.json"):
        accepted = ("pass", "passed", "passed_visual_review_with_documented_material_limits") if name == "portraits_visual_review.json" else ("pass", "passed")
        require(documents[name].get("status") in accepted, "Actual final visual review is incomplete: " + name)
    plan, baseline = documents["viewer_plan.json"], documents["baseline_catalog.json"]
    require(plan["counts"]["characters"] == 12 and plan["counts"]["outfits"] == 17 and plan["counts"]["parts"] == 72,
            "Expected the complete twelve-character HD viewer plan")
    expected_parts = {(c["id"], o["id"], p["id"]) for c in plan["characters"] for o in c["outfits"] for p in o["parts"]}
    require(len(expected_parts) == 72 and len(baseline["characters"]) == 5
            and sum(len(c["outfits"]) for c in baseline["characters"]) == 9, "Plan/baseline identities differ from the final integration scope")
    require(digest(local_path(root, WORK + "/DA_ViewerCatalog.before_integration.uasset")) == baseline["catalog_sha256"],
            "Original Catalog backup does not match the baseline")
    hashes = {name: digest(local_path(root, WORK + "/" + name)) for name in required_work_files if name.endswith(".json")}
    for name, keys in (("portraits_visual_review.json", ("portraits_report_sha256",)),
                       ("runtime_visual_review.json", ("desktop_smoke_report_sha256",) if skip_mobile_validation else ("desktop_smoke_report_sha256", "mobile_smoke_report_sha256"))):
        visual = documents[name]
        for key in keys:
            expected = hashes[key.removesuffix("_sha256") + ".json"]
            actual = visual.get(key, visual.get("inputs", {}).get(key))
            if name == "portraits_visual_review.json" and actual is None:
                actual = visual.get("portraits_report", {}).get("sha256")
            require(actual == expected, "Visual review does not identify the current final report: " + name + " / " + key)
    portrait_review = documents["portraits_visual_review.json"]
    require(portrait_review.get("counts", {}).get("portraits") == 12 and portrait_review.get("counts", {}).get("approved") == 12,
            "The portrait visual review does not approve all twelve final portraits")
    contact_sheet = portrait_review.get("contact_sheet", {})
    require(contact_sheet.get("file") == WORK + "/portraits_contact_sheet.png" and contact_sheet.get("sha256") == digest(local_path(root, contact_sheet["file"])),
            "The portrait visual review contact sheet changed")
    for name in ("animation_report.json", "dynamics_report.json", "dynamics_preflight.json"):
        report = documents[name]
        require(report.get("processed_parts") == 72 and len(report.get("parts", [])) == 72, "Incomplete final part report: " + name)
        identities = {(r["character_id"], r["outfit_id"], r["part_id"]) for r in report["parts"]}
        require(identities == expected_parts, "Part report does not cover the exact HD plan: " + name)
    animation = documents["animation_report.json"]
    require(animation.get("plan_sha256") == hashes["viewer_plan.json"] and animation.get("baseline_catalog_sha256") == hashes["baseline_catalog.json"],
            "Animation report uses stale plan/baseline inputs")
    clips = [row for row in animation["parts"] if row.get("status") in ("adapted", "reused")]
    require(len(clips) == 67 and sum(r["status"] == "attached_reference_pose" for r in animation["parts"]) == 3
            and sum(r["status"] == "reference_pose_incompatible" for r in animation["parts"]) == 2, "Animation coverage differs from the verified source-motion policy")
    for row in clips:
        proof = row.get("verification", {})
        require(proof.get("status") == "passed" and proof.get("runtime_compressed_data_valid") is True
                and proof.get("compressed_samples_checked") == proof.get("keys") and proof.get("keys", 0) > 0,
                "Every saved animation needs complete compressed-frame verification")
        check_record(root, {"file": row["asset_file"], "bytes": row["asset_bytes"], "sha256": row["asset_sha256"]})
    for name in ("dynamics_report.json", "dynamics_preflight.json"):
        require(all(documents[name].get("inputs", {}).get(key) == hashes[file] for key, file in
                    (("plan_sha256", "viewer_plan.json"), ("baseline_catalog_sha256", "baseline_catalog.json"))), "Stale dynamics inputs: " + name)
    for row in documents["dynamics_report.json"]["parts"]:
        if row.get("status") == "derived_compatible_subset":
            check_record(root, row["profile_asset"])
    materials = documents["material_overrides_report.json"]
    index_sha = digest(local_path(root, "Docs/PCAssetConversion/asset_index.json"))
    require(materials.get("plan_sha256") == hashes["viewer_plan.json"] and materials.get("asset_index_sha256") == index_sha,
            "Viewer material overrides use another plan or conversion index")
    for row in materials["materials"]:
        check_record(root, row)
    environment = documents["viewer_environment_report.json"]
    require(environment.get("plan_sha256") == hashes["viewer_environment_plan.json"]
            and documents["viewer_environments_fresh_verification.json"].get("environment_report_sha256") == hashes["viewer_environment_report.json"],
            "Source environments have not been freshly verified")
    require(len(environment.get("routes", [])) == 17 and environment.get("counts", {}).get("active") == 5,
            "Source environment routes are incomplete")
    for row in environment["environments"]:
        check_record(root, row["asset"])
    portraits = documents["portraits_report.json"]
    require(portraits.get("completed_portraits") == 12 and len(portraits.get("portraits", [])) == 12
            and {r["character_id"] for r in portraits["portraits"]} == {c["id"] for c in plan["characters"]}, "All twelve final portraits are required")
    for key, name in (("plan_sha256", "viewer_plan.json"), ("baseline_catalog_sha256", "baseline_catalog.json"),
                      ("animation_report_sha256", "animation_report.json"), ("material_overrides_report_sha256", "material_overrides_report.json")):
        require(portraits.get("inputs", {}).get(key) == hashes[name], "Portrait report uses stale evidence: " + name)
    for row in portraits["portraits"]:
        require(row.get("status") == "passed", "A final portrait has not passed")
        check_record(root, row["texture"])
        check_record(root, row["png"])
        png_size(local_path(root, row["png"]["file"]))
        names.add(row["png"]["file"])
    catalog_hash = digest(local_path(root, "Content/Viewer/Data/DA_ViewerCatalog.uasset"))
    imported, audit = documents["catalog_import_report.json"], documents["integration_audit.json"]
    require(imported.get("catalog_sha256") == audit.get("catalog_file_sha256") == catalog_hash, "Final Catalog import/audit is stale")
    require(imported.get("characters") == 12 and imported.get("total_outfits") == 26
            and imported.get("new_outfits") == 17 and imported.get("new_parts") == 72, "Final Catalog counts differ from the HD integration scope")
    for key, name in (("plan_sha256", "viewer_plan.json"), ("baseline_catalog_sha256", "baseline_catalog.json"),
                      ("animation_report_sha256", "animation_report.json"), ("portraits_report_sha256", "portraits_report.json"),
                      ("dynamics_report_sha256", "dynamics_report.json"), ("material_overrides_report_sha256", "material_overrides_report.json"),
                      ("viewer_environment_plan_sha256", "viewer_environment_plan.json"),
                      ("viewer_environment_report_sha256", "viewer_environment_report.json")):
        require(audit.get("inputs", {}).get(key) == hashes[name], "Integration audit uses stale inputs: " + name)
    expected_cases = {(c["id"], o["id"]) for c in audit["characters"] for o in c["outfits"]}
    require(len(expected_cases) == 26, "The final audit must cover all twenty-six outfit selections")
    runtime_hash = digest(local_path(root, "Binaries/Mac/libUnrealEditor-NZMCharacterViewer.dylib"))
    smoke_reports = (("desktop_smoke_report.json", "SM6"),) if skip_mobile_validation else (("desktop_smoke_report.json", "SM6"), ("mobile_smoke_report.json", "ES3_1"))
    for name, feature in smoke_reports:
        smoke = documents[name]
        rows = smoke.get("results", [])
        require(smoke.get("status") == "pass" and smoke.get("planned_combinations") == smoke.get("completed_combinations") == len(rows) == 26
                and {(r["character_id"], r["outfit_id"]) for r in rows} == expected_cases
                and all(r.get("status") == "pass" for r in rows), "All twenty-six runtime cases must pass: " + name)
        require(smoke.get("validated_catalog_sha256") == catalog_hash and smoke.get("validated_runtime_module_sha256") == runtime_hash,
                "Runtime report uses another Catalog or module: " + name)
        run = Path(smoke["run_directory"])
        run = run if run.is_absolute() else root / run
        require(run.resolve().is_relative_to(root / "Saved") and run.is_dir(), "Runtime evidence directory is outside project Saved")
        names.add(local_path(root, run / "Report.json").relative_to(root).as_posix())
        for row in rows:
            rendering = row["viewport_and_ui"]["rendering"]
            require(rendering.get("using_null_rhi") is False and "metal" in rendering.get("rhi_name", "").lower()
                    and rendering.get("scene_feature_level_name") == feature, "Runtime evidence used an unexpected render path: " + name)
            screenshot = local_path(root, row["screenshot"])
            require(screenshot.is_relative_to(run), "Runtime screenshot is outside its recorded run directory")
            png_size(screenshot)
            names.add(screenshot.relative_to(root).as_posix())
        for path in run.rglob("*.png"):
            names.add(local_path(root, path).relative_to(root).as_posix())
    content_audit = read(local_path(root, "Saved/ContentAudit.json"))
    require(not content_audit.get("missing_packages") and not content_audit.get("unknown_dependencies")
            and content_audit.get("project_packages", 0) > 0, "Content dependency audit has missing/unknown packages")
    ios = read(local_path(root, "Saved/BuildIOSCompileVerification.json"))
    require(ios.get("status") == "passed_unsigned_compile_link_only" and ios.get("binary", {}).get("verified_ios_arm64_executable") is True,
            "The final unsigned iOS compile/link check has not passed")
    ios_binary = local_path(root, ios["binary"]["path"], max_bytes=None)
    require(ios_binary.stat().st_size == ios["binary"]["size_bytes"] and digest(ios_binary) == ios["binary"]["sha256"],
            "The verified iOS binary changed after compilation")
    current_sources = {p.relative_to(root).as_posix() for p in (root / "Source").rglob("*") if p.is_file() and p.suffix in (".h", ".cpp", ".cs")}
    require(set(ios.get("source_sha256", {})) == current_sources, "Runtime source set changed after iOS compilation")
    for name, expected in ios["source_sha256"].items():
        require(digest(local_path(root, name)) == expected, "Runtime source changed after iOS compilation: " + name)
    require(digest(local_path(root, ios["log"])) == ios["log_sha256"], "iOS build log differs from its verification")
    mac_text = local_path(root, mac_log).read_text(encoding="utf-8", errors="replace")
    require("Result: Succeeded" in mac_text and "Result: Failed" not in mac_text, "The selected final Mac build log did not pass")
    for group in ("portraits_contact_sheet.png", "pc_outfits_contact_sheet.png"):
        png_size(local_path(root, WORK + "/" + group))
    # Intentional before/after visual evidence is retained; tools, pycache and
    # old stdout/engine logs in these directories are not selected.
    for folder in ("EyeValidation", "QueenValidation"):
        for path in (root / WORK / folder).rglob("*"):
            if path.is_file() and path.suffix.lower() in (".png", ".json"):
                names.add(local_path(root, path).relative_to(root).as_posix())
    for optional in ("material_overrides_iris_only_report.json", "eye_plan_summary.json", "dynamics_preflight_strict_baseline.json",
                     "immutable_conversion_validation.json"):
        if (root / WORK / optional).is_file():
            names.add(WORK + "/" + optional)
    names.add("Scripts/archive_pc_viewer_evidence.py")
    for folder, suffixes in (("Source", {".h", ".cpp", ".mm", ".cs"}), ("Plugins/PCAssetImporter/Source", {".h", ".cpp", ".cs"}), ("Config", {".ini"})):
        for path in (root / folder).rglob("*"):
            if path.is_file() and path.suffix in suffixes:
                names.add(local_path(root, path).relative_to(root).as_posix())
    for path in (root / "Scripts").glob("*pc*viewer*.py"):
        names.add(local_path(root, path).relative_to(root).as_posix())
    for optional in ("Scripts/cleanup_pc_source_exports.py", "Scripts/extend_pc_viewer_eye_occlusion.py", "Scripts/prepare_pc_dynamics.py",
                     "NZMCharacterViewer.uproject", "Plugins/PCAssetImporter/PCAssetImporter.uplugin", "README.md", "Docs/PC_VIEWER_INTEGRATION.md"):
        if (root / optional).is_file():
            names.add(optional)
    for name in additions:
        path = local_path(root, name)
        names.add(path.relative_to(root).as_posix())
    records = [file_record(root, name) for name in sorted(names)]
    source_tree = subprocess.run(["git", "rev-parse", SOURCE_COMMIT + ":PCResources"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    project_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    metadata = {"catalog_sha256": catalog_hash, "runtime_module_sha256": runtime_hash, "source_commit": SOURCE_COMMIT,
                "source_tree": source_tree, "source_restore_command": "git restore --source=" + SOURCE_COMMIT + " -- PCResources",
                "project_head": project_head, "mac_build_log": mac_log, "asset_index_sha256": index_sha,
                "counts": {"characters": 12, "pc_outfits": 17, "baseline_outfits": 9, "total_outfits": 26,
                           "pc_parts": 72, "adapted_animation_assets": 67, "portraits": 12, "desktop_smoke_cases": 26, "mobile_smoke_cases": 0 if skip_mobile_validation else 26},
                "mobile_runtime_validation": "not_run_user_requested_cleanup" if skip_mobile_validation else "passed_all_26_metal_es3_1_cases",
                "validation_reports": {WORK + "/" + name: hashes[name] for name in PASSED_WORK_REPORTS},
                "scope": "Final local HD viewer evidence, original Catalog rollback bytes and current authoring/runtime code. Raw exports and the previous conversion asset_plan are excluded; original shader parity and iOS device validation are not inferred."}
    return records, metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--mac-build-log", default=DEFAULT_MAC_LOG, help="Project-relative final successful Mac editor build log")
    parser.add_argument("--include", action="append", default=[], help="Additional project-relative evidence file; repeatable")
    parser.add_argument("--skip-mobile-validation", action="store_true", help="User requested cleanup without further tests: archive completed desktop evidence and explicitly record mobile runtime validation as not run")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preflight-only", action="store_true")
    mode.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    if args.verify_only:
        result = verify_archive(root, read(local_path(root, DESTINATION + "/" + MANIFEST_NAME)))
    else:
        records, metadata = preflight(root, args.mac_build_log, args.include, args.skip_mobile_validation)
        if args.preflight_only:
            result = {"status": "preflight_passed", "files": len(records), "uncompressed_bytes": sum(r["bytes"] for r in records),
                      "catalog_sha256": metadata["catalog_sha256"], "archive_would_be": DESTINATION + "/" + ARCHIVE_NAME}
        else:
            manifest = publish_archive(root, records, metadata)
            result = verify_archive(root, manifest)
            result["manifest"] = DESTINATION + "/" + MANIFEST_NAME
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
