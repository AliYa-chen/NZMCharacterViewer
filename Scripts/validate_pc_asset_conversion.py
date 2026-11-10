#!/usr/bin/env python3
"""Audit the completed PC asset conversion without loading Unreal or changing Content.

Checks every planned stage, the final fresh-Editor verification records, source
hashes, saved package hashes, independent Skeleton packages, and environment
candidate reports. The output distinguishes editable source archives and stock
material candidates from recovered executable shaders, animation, or particles.

--allow-viewer-integration recognizes only current, hash-verified viewer
additions and writes a separate local recheck. It never rewrites the original
conversion index/docs or relaxes source, shader or unexpected-package checks.
"""
import argparse
import ast
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import sys
import zlib


KINDS = ("textures", "materials", "meshes", "documents")
STAGES = ("textures", "materials", "meshes", "bindings", "documents", "verify")
ENVIRONMENT_SUCCESS = "saved_inactive_candidates_requires_source_mode_and_visual_validation"
STOCK_SUPPORT = {
    "/Game/PCAssets/Support/T_PC_LinearWhite": "/Engine/EngineResources/WhiteSquareTexture",
    "/Game/PCAssets/Support/T_PC_LinearBlack": "/Engine/EngineResources/Black",
    "/Game/PCAssets/Support/T_PC_MasksWhite": "/Engine/EngineResources/WhiteSquareTexture",
    "/Game/PCAssets/Support/T_PC_MasksBlack": "/Engine/EngineResources/Black",
}
VIEWER_EXTRA_COUNTS = {"Animations": 67, "Portraits": 12, "ViewerMaterials": 18,
                       "Dynamics": 4, "ViewerEnvironments": 12}


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".new")
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pending.replace(path)


def stable(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def importer_version(path):
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "VERSION"
                                                for target in node.targets):
            value = ast.literal_eval(node.value)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
    raise ValueError("Cannot determine the current importer VERSION without executing it")


def target_file(root, target, environment=False):
    allowed = "/Game/Viewer/PCEnvironment/" if environment else "/Game/PCAssets/"
    if not isinstance(target, str) or not target.startswith(allowed) or not re.fullmatch(r"/Game/[A-Za-z0-9_/]+", target):
        raise ValueError("Invalid or out-of-scope asset target: " + str(target))
    path = (root / "Content" / (target[len("/Game/"):] + ".uasset")).resolve()
    if not path.is_relative_to(root / "Content"):
        raise ValueError("Asset escapes Content: " + target)
    return path


def approved_viewer_record(root, group, target, record, indexed_targets):
    """Validate one exact approved package; never authorize a directory prefix."""
    if (group not in VIEWER_EXTRA_COUNTS or not isinstance(target, str)
            or not target.startswith("/Game/PCAssets/" + group + "/")
            or not re.fullmatch(r"/Game/[A-Za-z0-9_/]+", target)):
        raise ValueError("Viewer addition escapes its approved namespace: " + str(target))
    if target in indexed_targets:
        raise ValueError("Viewer addition collides with an original conversion asset: " + target)
    expected_file = "Content/" + target[len("/Game/"):] + ".uasset"
    size, expected_sha = record.get("bytes"), record.get("sha256")
    if (record.get("file") != expected_file or not isinstance(size, int) or isinstance(size, bool) or size <= 0
            or not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha)):
        raise ValueError("Viewer addition lacks an exact file, size and SHA256: " + target)
    if "package" in record and record["package"] != target:
        raise ValueError("Viewer addition package and record disagree: " + target)
    path = root / expected_file
    if (path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != root)
            or not path.resolve().is_relative_to(root / "Content") or not path.is_file()
            or path.stat().st_size != size or digest(path) != expected_sha):
        raise ValueError("Approved viewer package is missing or changed: " + target)
    return {"target": target, "kind": group, "file": expected_file, "bytes": size, "sha256": expected_sha}


def viewer_tree_coverage(indexed_targets, actual_targets, approved_targets=()):
    """Keep default strict coverage; opt-in permits only individually approved targets."""
    indexed, actual, approved = set(indexed_targets), set(actual_targets), set(approved_targets)
    if indexed.intersection(approved):
        raise ValueError("Approved viewer targets collide with the original index")
    expected = indexed | approved
    return {"missing": expected - actual, "extras": actual - expected, "expected": expected,
            "original_expected": len(indexed), "approved_viewer_additions": len(approved)}


def approved_viewer_integration_assets(root, indexed_targets):
    """Read frozen reports, bind them to the current passing audit and hash all extras."""
    work = root / "Saved/PCViewerIntegration"
    filenames = {"plan_sha256": work / "viewer_plan.json", "baseline_catalog_sha256": work / "baseline_catalog.json",
                 "animation_report_sha256": work / "animation_report.json", "portraits_report_sha256": work / "portraits_report.json",
                 "material_overrides_report_sha256": work / "material_overrides_report.json",
                 "dynamics_report_sha256": work / "dynamics_report.json", "viewer_environment_plan_sha256": work / "viewer_environment_plan.json",
                 "viewer_environment_report_sha256": work / "viewer_environment_report.json",
                 "asset_index_sha256": root / "Docs/PCAssetConversion/asset_index.json"}
    audit_path = work / "integration_audit.json"
    catalog_path = root / "Content/Viewer/Data/DA_ViewerCatalog.uasset"
    documents = {key: read_json(path) for key, path in filenames.items()}
    hashes = {key: digest(path) for key, path in filenames.items()}
    audit = read_json(audit_path)
    if (audit.get("status") != "passed" or audit.get("errors") != [] or audit.get("unknowns") != []
            or any(audit.get("inputs", {}).get(key) != value for key, value in hashes.items())
            or audit.get("catalog_file_sha256") != digest(catalog_path)):
        raise ValueError("Viewer additions require a passed integration audit bound to the current reports, index and Catalog")
    viewer, baseline = documents["plan_sha256"], documents["baseline_catalog_sha256"]
    if viewer.get("asset_index_sha256") != hashes["asset_index_sha256"]:
        raise ValueError("Viewer plan uses another immutable conversion index")
    part_keys = {(c["id"], o["id"], p["id"]) for c in viewer["characters"] for o in c["outfits"] for p in o["parts"]}
    character_ids = {c["id"] for c in viewer["characters"]}
    if (len(part_keys) != 72 or len(character_ids) != 12 or viewer.get("counts", {}).get("outfits") != 17
            or len(baseline["characters"]) != 5 or sum(len(c["outfits"]) for c in baseline["characters"]) != 9
            or audit.get("counts", {}).get("total_outfits") != 26 or audit.get("counts", {}).get("new_parts") != 72):
        raise ValueError("Viewer plan/audit scope differs from the approved twelve-character integration")
    for key in ("animation_report_sha256", "portraits_report_sha256", "material_overrides_report_sha256",
                "dynamics_report_sha256", "viewer_environment_report_sha256"):
        if documents[key].get("status") != "passed":
            raise ValueError("Incomplete viewer approval report: " + filenames[key].name)
    animation, dynamics = documents["animation_report_sha256"], documents["dynamics_report_sha256"]
    for report in (animation, dynamics):
        keys = {(row["character_id"], row["outfit_id"], row["part_id"]) for row in report.get("parts", [])}
        if keys != part_keys or len(report.get("parts", [])) != 72 or report.get("processed_parts") != 72:
            raise ValueError("Viewer motion report does not cover the exact approved part identities")
    if (animation.get("plan_sha256") != hashes["plan_sha256"]
            or animation.get("baseline_catalog_sha256") != hashes["baseline_catalog_sha256"]
            or dynamics.get("inputs") != {key: hashes[key] for key in ("plan_sha256", "baseline_catalog_sha256")}):
        raise ValueError("Viewer motion reports use another source plan or baseline")
    portraits = documents["portraits_report_sha256"]
    if (portraits.get("completed_portraits") != 12 or len(portraits.get("portraits", [])) != 12
            or {row["character_id"] for row in portraits["portraits"]} != character_ids
            or any(portraits.get("inputs", {}).get(key) != hashes[key] for key in
                   ("plan_sha256", "baseline_catalog_sha256", "animation_report_sha256", "asset_index_sha256", "material_overrides_report_sha256"))):
        raise ValueError("Prepared viewer portraits are incomplete or use other current inputs")
    materials = documents["material_overrides_report_sha256"]
    if any(materials.get(key) != hashes[key] for key in ("plan_sha256", "asset_index_sha256", "animation_report_sha256")):
        raise ValueError("Viewer material approval uses other current inputs")
    environments, environment_plan = documents["viewer_environment_report_sha256"], documents["viewer_environment_plan_sha256"]
    if (environments.get("plan_sha256") != hashes["viewer_environment_plan_sha256"]
            or environment_plan.get("viewer_plan_sha256") != hashes["plan_sha256"]
            or environment_plan.get("asset_index_sha256") != hashes["asset_index_sha256"]
            or environments.get("routes") != environment_plan.get("routes") or len(environments.get("routes", [])) != 17):
        raise ValueError("Viewer environment approval uses another source plan or route set")
    approved = {}

    def add(group, target, row):
        if target in approved:
            raise ValueError("Viewer approval repeats a package target: " + target)
        approved[target] = approved_viewer_record(root, group, target, row, indexed_targets)

    for row in animation["parts"]:
        if row.get("status") in ("adapted", "reused"):
            add("Animations", row["animation"], {"file": row["asset_file"], "bytes": row["asset_bytes"], "sha256": row["asset_sha256"]})
        elif row.get("status") not in ("attached_reference_pose", "reference_pose_incompatible") or row.get("animation") is not None:
            raise ValueError("Unsupported or falsely referenced viewer animation status")
    for row in portraits["portraits"]:
        if row.get("status") != "passed" or row.get("target") != "/Game/PCAssets/Portraits/T_PC_" + row["character_id"]:
            raise ValueError("Prepared portrait is not an exact approved character target")
        add("Portraits", row["target"], row["texture"])
    for row in materials.get("materials", []):
        if row.get("class") not in ("Material", "MaterialInstanceConstant"):
            raise ValueError("Viewer material approval has an unsupported class")
        add("ViewerMaterials", row["target"], row)
    for row in dynamics["parts"]:
        if row.get("status") == "derived_compatible_subset":
            target = row["profile_asset"]["package"]
            if row.get("profile") != target + "." + target.rsplit("/", 1)[-1]:
                raise ValueError("Derived dynamics target differs from its approval")
            add("Dynamics", target, row["profile_asset"])
        elif row.get("status") not in ("not_applicable", "unavailable_native_chain_mismatch", "reused_exact_same_outfit_part_profile"):
            raise ValueError("Unsupported viewer dynamics approval status")
    for row in environments.get("environments", []):
        if row.get("status") != "passed":
            raise ValueError("Viewer environment approval is incomplete")
        add("ViewerEnvironments", row["target"], row["asset"])
    counts = dict(Counter(row["kind"] for row in approved.values()))
    if counts != VIEWER_EXTRA_COUNTS:
        raise ValueError("Viewer additions differ from the approved exact package coverage: " + stable(counts))
    proof_paths = {**{str(path.relative_to(root)): hashes[key] for key, path in filenames.items()},
                   str(audit_path.relative_to(root)): digest(audit_path), str(catalog_path.relative_to(root)): digest(catalog_path)}
    return approved, {"status": "passed_current_report_and_package_hashes", "counts": counts,
                      "approved_additions": len(approved), "inputs": proof_paths,
                      "assets": [approved[key] for key in sorted(approved)],
                      "scope": "Exact approved viewer additions only. Original conversion index, source validation and rendered-evidence requirements are unchanged."}


def png_pixel_evidence(path):
    """Decode an RGB/RGBA PNG with the standard library, including all filters."""
    encoded = path.read_bytes()
    if encoded[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Preview is not a PNG")
    offset, payload, header, ended = 8, bytearray(), None, False
    while offset < len(encoded):
        if offset + 12 > len(encoded):
            raise ValueError("Truncated PNG chunk")
        count = struct.unpack_from(">I", encoded, offset)[0]
        kind, data = encoded[offset + 4:offset + 8], encoded[offset + 8:offset + 8 + count]
        if len(data) != count or offset + count + 12 > len(encoded):
            raise ValueError("Truncated PNG payload")
        if zlib.crc32(kind + data) & 0xffffffff != struct.unpack_from(">I", encoded, offset + 8 + count)[0]:
            raise ValueError("PNG CRC mismatch")
        if kind == b"IHDR":
            if header is not None:
                raise ValueError("Duplicate PNG header")
            header = struct.unpack(">IIBBBBB", data)
        elif kind == b"IDAT":
            payload.extend(data)
        offset += count + 12
        if kind == b"IEND":
            ended = True
            break
    if not ended or not header or header[2] != 8 or header[3] not in (2, 6) or any(header[4:]):
        raise ValueError("Unsupported or incomplete RGB8/RGBA8 PNG")
    width, height, bits, color, _, _, _ = header
    if width <= 0 or height <= 0 or width > 4096 or height > 4096:
        raise ValueError("Invalid preview dimensions")
    channels = 4 if color == 6 else 3
    stride = width * channels
    decoded = zlib.decompress(payload)
    if len(decoded) != height * (stride + 1):
        raise ValueError("Unexpected PNG decoded size")
    previous = bytearray(stride)
    first, different, bins = None, 0, set()
    minimum, maximum, alpha_min, alpha_max = [255] * 3, [0] * 3, 255, 0
    for y in range(height):
        mode = decoded[y * (stride + 1)]
        row = bytearray(decoded[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        if mode > 4:
            raise ValueError("Invalid PNG scanline filter")
        for i in range(stride):
            left, above = row[i - channels] if i >= channels else 0, previous[i]
            upper_left = previous[i - channels] if i >= channels else 0
            if mode == 4:
                p = left + above - upper_left
                distances = (abs(p - left), abs(p - above), abs(p - upper_left))
                predictor = (left, above, upper_left)[distances.index(min(distances))]
            else:
                predictor = (0, left, above, (left + above) // 2)[mode]
            row[i] = (row[i] + predictor) & 255
        for x in range(width):
            rgb = tuple(row[x * channels:x * channels + 3])
            if first is None:
                first = rgb
            different += rgb != first
            bins.add(tuple(value // 16 for value in rgb))
            alpha = row[x * channels + 3] if channels == 4 else 255
            alpha_min, alpha_max = min(alpha_min, alpha), max(alpha_max, alpha)
            for c in range(3):
                minimum[c], maximum[c] = min(minimum[c], rgb[c]), max(maximum[c], rgb[c])
        previous = row
    if different < max(100, width * height // 1000) or max(b - a for a, b in zip(minimum, maximum)) < 8 or alpha_max == 0:
        raise ValueError("PNG is blank, uniform or transparent")
    return {"width": width, "height": height, "bit_depth": bits, "color_type": color,
            "pixel_check": "nonuniform_rendered_pixels", "rgb_min": minimum, "rgb_max": maximum,
            "quantized_rgb_bins": len(bins), "pixels_different_from_first": different,
            "alpha_min": alpha_min, "alpha_max": alpha_max}


def audit_render_evidence(root, plan, plan_sha, asset_index):
    """Optional rendered evidence has its own status, separate from structure."""
    work = root / "Saved/PCAssetConversion"
    hashes, errors = {}, []

    def read(path):
        hashes[path] = digest(path)
        return read_json(path)

    def current_path(name):
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Evidence path escapes project: " + str(name))
        return path

    shaders = {"status": "not_verified", "expected_materials": len(plan["materials"]), "validated_materials": 0}
    candidates = [work / "shader_summary.json", work / "Shaders/shader_summary.json"]
    existing = [path for path in candidates if path.is_file()]
    if existing:
        try:
            from run_pc_asset_shader_checks import validate_report
            summary_path = existing[0]
            summary = read(summary_path)
            if len(existing) > 1 and digest(existing[1]) != hashes[summary_path]:
                raise ValueError("Two different shader summaries exist")
            expected = {row["target"] for row in plan["materials"]}
            if (summary.get("schema_version") != 1 or summary.get("status") != "passed"
                    or summary.get("plan_sha256") != plan_sha or summary.get("rhi") != "Metal"
                    or summary.get("shader_platform") != "METAL_SM6" or summary.get("missing_targets") != []
                    or summary.get("expected_materials") != len(expected) or summary.get("validated_materials") != len(expected)
                    or summary.get("asset_files_unchanged") is not True or summary.get("requested_range_completed") is not True):
                raise ValueError("Shader summary is incomplete, stale, or not a passed Metal SM6 run")
            covered = set()
            reports = []
            index_hashes = {row["target"]: row.get("sha256") for row in asset_index}
            for entry in summary.get("valid_reports", []):
                path = current_path(entry["file"])
                if not path.is_relative_to(work / "Shaders") or digest(path) != entry.get("report_sha256"):
                    raise ValueError("Shader batch report path or recorded SHA256 changed")
                hashes[path] = digest(path)
                # This shared ordinary-Python checker independently rechecks
                # every flag, exact planned range, RHI and saved package hash.
                checked = validate_report(path, root, plan["materials"], plan_sha, "METAL_SM6")
                if checked["targets"] != entry.get("targets") or checked["count"] != entry.get("count") or checked["start"] != entry.get("start"):
                    raise ValueError("Shader summary range/targets disagree with its report")
                if covered.intersection(checked["targets"]):
                    raise ValueError("Shader summary contains overlapping material coverage")
                if checked["validated_asset_sha256"] != entry.get("validated_asset_sha256"):
                    raise ValueError("Shader summary asset hashes disagree with its report")
                for target, asset_sha in checked["validated_asset_sha256"].items():
                    if asset_sha != index_hashes.get(target):
                        raise ValueError("Shader evidence and structurally audited asset differ: " + target)
                covered.update(checked["targets"])
                reports.append({key: checked[key] for key in ("file", "report_sha256", "start", "count")})
            if covered != expected:
                raise ValueError("Shader evidence does not cover every planned material exactly once")
            for name, expected_sha in summary.get("frozen_execution_inputs", {}).items():
                path = current_path(name)
                if not path.is_file() or digest(path) != expected_sha:
                    raise ValueError("Shader execution input changed: " + name)
            shaders = {"status": "passed_Metal_SM6", "rhi": "Metal", "shader_platform": "METAL_SM6",
                       "shader_format": "SF_METAL_SM6", "feature_level": "SM6", "expected_materials": len(expected),
                       "validated_materials": len(covered), "batch_reports": reports,
                       "summary_file": summary_path.relative_to(root).as_posix(), "summary_sha256": hashes[summary_path],
                       "limitations": summary.get("limitations", [])}
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, ImportError) as error:
            shaders["status"] = "failed"
            errors.append({"scope": "shaders", "error": str(error)})

    preview = {"status": "not_verified", "scope": "single_model_static_reference_pose_only"}
    preview_path = work / "Preview/preview_report.json"
    if preview_path.is_file():
        try:
            report = read(preview_path)
            expected_row = next((row for row in plan["meshes"] if row["target"] == report.get("mesh")), None)
            if (report.get("schema_version") != 1 or report.get("status") != "captured_real_Metal_reference_pose_requires_visual_review"
                    or report.get("plan_sha256") != plan_sha or report.get("content_saved") is not False
                    or report.get("map_saved") is not False or report.get("animation") != "reference_pose" or not expected_row
                    or report.get("source_sha256") != expected_row["sha256"] or report.get("source_file") != expected_row["file"]
                    or report.get("source_channels") != expected_row["channels"]):
                raise ValueError("Preview source, plan or capture status disagrees")
            rhi = report.get("rhi", {})
            if rhi != {"rhi": "Metal", "using_null_rhi": False, "shader_platform": "METAL_SM6",
                       "shader_format": "SF_METAL_SM6", "feature_level": "SM6"}:
                raise ValueError("Preview was not captured on actual Metal SM6")
            expected_materials = {value for value in expected_row["material_targets"] if value}
            actual_materials = report.get("materials", [])
            if len(actual_materials) != len(expected_row["slot_names"]):
                raise ValueError("Preview mesh material slots do not match plan")
            for i, material in enumerate(actual_materials):
                if (material.get("slot") != i or material.get("asset") != expected_row["material_targets"][i]
                        or material.get("slot_name") != expected_row["slot_names"][i]
                        or material.get("source_package") != expected_row["material_packages"][i]):
                    raise ValueError("Preview material binding differs from plan")
            flags = ("shader_map_present", "platform_matches_active_rhi", "compilation_finalized",
                     "compiled_successfully", "required_shader_set_complete", "valid_for_rendering")
            for key in ("shaders", "shaders_after_scene_setup"):
                native = report.get(key, {})
                if (native.get("status") != "passed" or native.get("success") is not True
                        or any(native.get(name) != value for name, value in rhi.items())
                        or native.get("failed_count") != 0 or native.get("passed_count") != len(expected_materials)
                        or native.get("shader_jobs_after") != 0 or native.get("content_saved") is not False):
                    raise ValueError("Preview material shader evidence failed")
                rows = native.get("materials", [])
                if {row.get("package") for row in rows} != expected_materials or len(rows) != len(expected_materials):
                    raise ValueError("Preview material shader coverage differs")
                for row in rows:
                    if (row.get("status") != "passed" or any(row.get(flag) is not True for flag in flags)
                            or row.get("compile_errors") != [] or row.get("shader_count", 0) <= 0 or "error" in row):
                        raise ValueError("Preview shader-map flag failed")
            png = report.get("png", {})
            image = current_path(png["file"])
            if not image.is_relative_to(work / "Preview") or image.stat().st_size != png.get("bytes") or digest(image) != png.get("sha256"):
                raise ValueError("Preview PNG bytes or SHA256 changed")
            statistics = png_pixel_evidence(image)
            if any(png.get(key) != value for key, value in statistics.items()):
                raise ValueError("Preview PNG dimensions or decoded pixel statistics differ from its report")
            preview = {"status": "captured_single_model_reference_pose_Metal_SM6", "scope": "single_model_static_reference_pose_only",
                       "mesh": report["mesh"], "source_package": report.get("source_package"), "png": png,
                       "report_file": preview_path.relative_to(root).as_posix(), "report_sha256": hashes[preview_path],
                       "pixel_integrity": "PNG_CRC_dimensions_SHA256_and_decoded_nonuniform_pixels_verified",
                       "original_game_comparison": "not_performed", "all_character_visual_validation": "not_performed",
                       "visual_review": "image_requires_separate_subject_review_not_established_by_pixel_statistics",
                       "asset_hash_binding": "capture report records planned assets; per-capture package hashes were not recorded",
                       "limitations": report.get("limitations", [])}
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, zlib.error) as error:
            preview["status"] = "failed"
            errors.append({"scope": "preview", "error": str(error)})

    builds = {"mac_editor": {"status": "not_verified"}, "ios": {"status": "not_verified"}}
    mac_logs = sorted((root / "Saved/PCAssetAnalysis").glob("ue_*build.log"), key=lambda path: path.stat().st_mtime, reverse=True)
    if mac_logs:
        path = mac_logs[0]
        if "Result: Succeeded" in path.read_text(errors="replace"):
            builds["mac_editor"] = {"status": "passed_editor_build_log_observed", "log": path.relative_to(root).as_posix(),
                                    "log_sha256": digest(path), "scope": "Mac Development Editor build log; no full compiler-input snapshot"}
    ios_path = root / "Saved/BuildIOSCompileVerification.json"
    if ios_path.is_file():
        try:
            ios = read(ios_path)
            false_fields = ("xcode_signing_finalization_observed", "signing_performed", "asset_cooking_performed",
                            "installable_ipa_produced", "device_deployment_performed")
            if (ios.get("status") != "passed_unsigned_compile_link_only" or ios.get("exit_code") != 0
                    or ios.get("platform") != "IOS" or any(ios.get(key) is not False for key in false_fields)
                    or ios.get("binary", {}).get("verified_ios_arm64_executable") is not True):
                raise ValueError("iOS record did not pass unsigned compile/link-only checks")
            binary = ios["binary"]
            path = current_path(binary["path"])
            log = current_path(ios["log"])
            if (path.stat().st_size != binary.get("size_bytes") or digest(path) != binary.get("sha256")
                    or digest(log) != ios.get("log_sha256")):
                raise ValueError("iOS build binary or log hash changed")
            for name, expected_sha in ios.get("source_sha256", {}).items():
                if digest(current_path(name)) != expected_sha:
                    raise ValueError("iOS compiled source changed: " + name)
            builds["ios"] = {"status": "passed_unsigned_compile_link_only", "report": ios_path.relative_to(root).as_posix(),
                             "report_sha256": hashes[ios_path], "binary_sha256": binary["sha256"],
                             "asset_cooking": "not_performed", "signing": "not_performed", "device_runtime": "not_run",
                             "scope": ios.get("scope")}
        except (OSError, ValueError, RuntimeError, TypeError, KeyError) as error:
            builds["ios"]["status"] = "failed_or_stale_evidence"
            errors.append({"scope": "ios_build", "error": str(error)})
    for path, expected_sha in hashes.items():
        if not path.is_file() or digest(path) != expected_sha:
            scope = "preview" if "Preview" in path.parts else "shaders" if "PCAssetConversion" in path.parts else "ios_build"
            errors.append({"scope": scope, "error": "Evidence changed during audit: " + path.relative_to(root).as_posix()})
            if scope in ("shaders", "preview"):
                (shaders if scope == "shaders" else preview)["status"] = "failed"
    return {"shaders": shaders, "preview": preview, "builds": builds, "errors": errors,
            "required_render_validation_satisfied": shaders["status"] == "passed_Metal_SM6"
            and preview["status"] == "captured_single_model_reference_pose_Metal_SM6"}


def audit(root, require_render_validation=False, allow_viewer_integration=False):
    root = root.resolve()
    work = root / "Saved/PCAssetConversion"
    failures = []

    def issue(code, **details):
        failures.append({"kind": code, **details})

    snapshots = {}

    def load_snapshot(path, required=True):
        if not path.is_file():
            if required:
                issue("report_or_plan_missing", file=path.relative_to(root).as_posix())
            return {}
        snapshots[path] = digest(path)
        try:
            return read_json(path)
        except (ValueError, OSError) as error:
            issue("invalid_json", file=path.relative_to(root).as_posix(), error=str(error))
            return {}

    plan_path = work / "asset_plan.json"
    plan = load_snapshot(plan_path)
    state = load_snapshot(work / "import_state.json")
    environment = load_snapshot(work / "environment_report.json")
    env_plan = load_snapshot(work / "environment_plan.json")
    collections = load_snapshot(work / "collections_report.json")
    version = importer_version(root / "Scripts/import_pc_assets.py")
    plan_sha = snapshots.get(plan_path)
    if plan.get("schema_version") != 1:
        issue("unsupported_plan_schema", actual=plan.get("schema_version"))
    if state.get("schema_version") != 1:
        issue("unsupported_state_schema", actual=state.get("schema_version"))
    if state.get("status") in ("failed", "error", "incomplete", "interrupted"):
        issue("import_state_explicit_failure", status=state["status"])
    if state.get("plan_sha256") != plan_sha:
        issue("import_state_plan_hash_mismatch", expected=plan_sha, actual=state.get("plan_sha256"))
    rows_by_kind = {kind: plan.get(kind, []) for kind in KINDS}
    rows = [row for kind in KINDS for row in rows_by_kind[kind]]
    targets = [row.get("target") for row in rows]
    if len(set(targets)) != len(targets):
        issue("duplicate_planned_target")
    for row in rows:
        actual_item_sha = hashlib.sha256(stable({key: value for key, value in row.items()
                                                if key != "item_sha256"}).encode()).hexdigest()
        if row.get("item_sha256") != actual_item_sha:
            issue("plan_item_hash_mismatch", target=row.get("target"))
    for kind, expected_rows in rows_by_kind.items():
        if plan.get("counts", {}).get(kind) != len(expected_rows):
            issue("plan_count_mismatch", stage=kind, expected=len(expected_rows), actual=plan.get("counts", {}).get(kind))

    inputs = {}
    verified_input_bytes = 0
    for row in plan.get("inputs", []):
        name = row.get("file")
        if name in inputs:
            issue("duplicate_input", file=name)
            continue
        inputs[name] = row
        path = (root / name).resolve()
        if not path.is_relative_to(root / "PCResources") or not path.is_file():
            issue("source_input_missing_or_outside_resources", file=name)
            continue
        if path.stat().st_size != row.get("bytes") or digest(path) != row.get("sha256"):
            issue("source_input_changed", file=name)
        else:
            verified_input_bytes += row["bytes"]
    source_name = plan.get("source_manifest", "PCResources/source_manifest.json")
    manifest_path = (root / source_name).resolve()
    manifest = load_snapshot(manifest_path)
    if snapshots.get(manifest_path) != plan.get("source_manifest_sha256"):
        issue("source_manifest_changed")
    manifest_names = {"PCResources/" + row["file"] for row in manifest.get("files", [])}
    expected_input_names = manifest_names | {source_name}
    if expected_input_names != set(inputs):
        issue("plan_input_coverage_mismatch", missing=sorted(expected_input_names - set(inputs)),
              extra=sorted(set(inputs) - expected_input_names))
    for row in manifest.get("files", []):
        original = inputs.get("PCResources/" + row["file"], {})
        if original.get("sha256") != row.get("sha256") or original.get("bytes") != row.get("bytes"):
            issue("source_manifest_plan_disagreement", file=row["file"])
    actual_inputs = {path.relative_to(root).as_posix() for path in (root / "PCResources").rglob("*") if path.is_file()}
    if actual_inputs != expected_input_names:
        issue("source_resource_tree_disagreement", missing=sorted(expected_input_names - actual_inputs),
              extra=sorted(actual_inputs - expected_input_names))
    covered_inputs = {name for row in rows for name in row.get("source_files", [row["file"]])}
    if covered_inputs != expected_input_names:
        issue("planned_output_source_coverage_mismatch", missing=sorted(expected_input_names - covered_inputs),
              extra=sorted(covered_inputs - expected_input_names))
    source_to_targets = {}
    for row in rows:
        for name in row.get("source_files", [row["file"]]):
            source_to_targets.setdefault(name, set()).add(row["target"])
    file_mappings = plan.get("files", {})
    # Some byte-identical texture aliases appear only in source_files rather
    # than the planner's convenience files table. The authoritative coverage
    # check above includes all aliases and does not count them as omissions.
    if set(file_mappings) - expected_input_names:
        issue("plan_file_mapping_outside_inputs", extra=sorted(set(file_mappings) - expected_input_names))
    target_set = set(targets)
    for name, mapped in file_mappings.items():
        if not mapped or not set(mapped).issubset(target_set) or not set(mapped).issubset(source_to_targets.get(name, set())):
            issue("invalid_source_output_mapping", file=name)

    state_stages = state.get("stages", {})
    stage_counts = {}
    valid_stage_rows = {}
    for stage in STAGES:
        expected_rows = rows if stage == "verify" else rows_by_kind["meshes" if stage == "bindings" else stage]
        records = state_stages.get(stage, {})
        expected_targets = {row["target"] for row in expected_rows}
        missing = failed = stale = 0
        valid = set()
        for row in expected_rows:
            target = row["target"]
            record = records.get(target)
            if record is None:
                missing += 1
                continue
            if record.get("status") != "passed":
                failed += 1
                issue("stage_record_failed", stage=stage, target=target, status=record.get("status"))
                continue
            if (record.get("version") != version or record.get("source_sha256") != row["sha256"]
                    or record.get("item_sha256") != row["item_sha256"]):
                stale += 1
                issue("stage_record_stale", stage=stage, target=target)
                continue
            valid.add(target)
        if missing:
            issue("stage_targets_missing", stage=stage, count=missing)
        extra = set(records) - expected_targets
        if extra:
            issue("stage_targets_outside_plan", stage=stage, targets=sorted(extra))
        valid_stage_rows[stage] = valid
        stage_counts[stage] = {"expected": len(expected_rows), "passed_current": len(valid),
                               "missing": missing, "failed": failed, "stale": stale, "extra": len(extra)}

    index = []
    indexed_targets = set()
    native_results = []
    native_rebuild_comparisons = []
    output_bytes = 0
    for kind in KINDS:
        for row in rows_by_kind[kind]:
            target = row["target"]
            try:
                path = target_file(root, target)
            except ValueError as error:
                issue("invalid_target", target=target, error=str(error))
                continue
            exists = path.is_file()
            asset_sha = digest(path) if exists else None
            asset_bytes = path.stat().st_size if exists else None
            record = state_stages.get("verify", {}).get(target, {})
            verified = target in valid_stage_rows["verify"]
            if not exists:
                issue("asset_file_missing", target=target)
            if verified and (record.get("asset_sha256") != asset_sha or record.get("asset_bytes") != asset_bytes):
                issue("verified_asset_bytes_or_hash_changed", target=target,
                      expected_bytes=record.get("asset_bytes"), actual_bytes=asset_bytes)
                verified = False
            if verified and kind != "meshes":
                expected_classes = {"textures": {"TextureCube" if row["file"].lower().endswith(".hdr") else "Texture2D"},
                                    "materials": {"Material", "MaterialInstanceConstant"}, "documents": {"PCSourceRecord"}}
                if record.get("result", {}).get("class") not in expected_classes[kind]:
                    issue("verified_asset_class_disagrees", target=target, actual=record.get("result", {}).get("class"))
                    verified = False
            if verified and kind == "meshes":
                result = record.get("result", {})
                channels = row["channels"]
                expected_stats = {"points": channels["points"], "triangles": channels["triangles"],
                                  "bones": channels["bones"], "uv_channels": channels["uv_channels"],
                                  "vertex_colors": channels["has_vertex_colors"],
                                  "maximum_influences": channels["max_influences"],
                                  "source_weight_rows_checked": channels["skin_weight_rows"],
                                  "corner_layout_version": 2, "built_tangent_frames_verified": True,
                                  "built_tangent_frames_invalid": 0,
                                  "tangent_build_method": "MeshDescriptionMikkTSpace",
                                  "mesh_description_corner_wedge_mismatches": 0}
                mismatch = {key: {"expected": value, "actual": result.get(key)}
                            for key, value in expected_stats.items() if result.get(key) != value}
                metric_names = ("built_tangent_frames_checked", "degenerate_uv_or_geometry_tangent_exceptions",
                                "maximum_built_source_normal_component_error", "maximum_built_normal_length_squared_error",
                                "maximum_built_tangent_normal_dot", "normal_error_max", "tangent_orthogonality_error_max", "degenerate_count",
                                "near_degenerate_geometry_tangent_exceptions", "geometry_normal_squared_tolerance",
                                "uv_determinant_absolute_tolerance", "mesh_description_corner_wedge_mismatches",
                                "mesh_description_mikk_zero_tangents")
                metrics_valid = all(isinstance(result.get(key), (int, float)) and not isinstance(result.get(key), bool)
                                    and math.isfinite(result[key]) and result[key] >= 0 for key in metric_names)
                if metrics_valid:
                    metrics_valid = (
                        result["maximum_built_source_normal_component_error"] == result["normal_error_max"]
                        and result["maximum_built_tangent_normal_dot"] == result["tangent_orthogonality_error_max"]
                        and result["degenerate_uv_or_geometry_tangent_exceptions"] == result["degenerate_count"]
                        and result["near_degenerate_geometry_tangent_exceptions"] <= result["degenerate_count"]
                        and math.isclose(result["geometry_normal_squared_tolerance"], 1e-8, rel_tol=1e-6, abs_tol=0)
                        and math.isclose(result["uv_determinant_absolute_tolerance"], 1e-12, rel_tol=1e-6, abs_tol=0)
                        and isinstance(result.get("tangent_degeneracy_classification"), str)
                        and bool(result["tangent_degeneracy_classification"])
                        and result["built_tangent_frames_checked"] + result["degenerate_count"] == result.get("built_vertices_checked")
                        and result["normal_error_max"] <= 0.0002
                        and result["maximum_built_normal_length_squared_error"] <= 0.001
                        and (result["degenerate_count"] > 0 or result["tangent_orthogonality_error_max"] <= 0.001))
                if (result.get("status") != "passed" or result.get("source_sha256") != row["sha256"] or mismatch or not metrics_valid
                        or result.get("weight_absolute_tolerance", 1) > 4 / 65535 + 1e-12
                        or result.get("built_vertices_checked", 0) <= 0 or result.get("built_weight_rows_checked", 0) <= 0):
                    issue("native_mesh_verification_disagrees", target=target, stats=mismatch)
                    verified = False
                else:
                    native_results.append(result)
                    imported = state_stages.get("meshes", {}).get(target, {}).get("result", {})
                    before = imported.get("before_rebuild")
                    if before is not None:
                        if (before.get("source_sha256") != row["sha256"]
                                or any(before.get(key) != result.get(key) for key in
                                       ("points", "triangles", "bones", "uv_channels", "vertex_colors", "source_weight_rows_checked"))
                                or imported.get("built_vertices_before") != before.get("built_vertices_checked")
                                or imported.get("built_vertices_after") != result["built_vertices_checked"]
                                or imported.get("skeleton_and_material_bindings_preserved") is not True):
                            issue("native_corner_rebuild_comparison_disagrees", target=target)
                        else:
                            native_rebuild_comparisons.append({"target": target, "before": before["built_vertices_checked"],
                                                               "after": result["built_vertices_checked"]})
            if exists:
                output_bytes += asset_bytes
            result = record.get("result", {})
            import_result = state_stages.get(kind, {}).get(target, {}).get("result", {})
            target_type = (result.get("class") or import_result.get("class")
                           or {"textures": row.get("source_type", "Texture"), "materials": "MaterialInterface",
                               "meshes": "SkeletalMesh", "documents": "PCSourceRecord"}[kind])
            entry = {"target": target, "file": path.relative_to(root).as_posix(),
                     "bytes": asset_bytes, "sha256": asset_sha, "type": target_type,
                     "source_file": row["file"], "source_sha256": row["sha256"],
                     "source_package": row.get("source_package"), "source_files": row.get("source_files", [row["file"]]),
                     "item_sha256": row["item_sha256"], "kind": kind,
                     "validation": "fresh_editor_verification_and_file_hash_passed" if verified else "incomplete_or_unverified",
                     "editor_only": kind == "documents", "conversion_scope": row.get("conversion_scope")}
            index.append(entry)
            indexed_targets.add(target)
            if kind == "meshes":
                skeleton_target = target + "_Skeleton"
                if row.get("skeleton_target", skeleton_target) != skeleton_target:
                    issue("skeleton_target_plan_disagreement", target=target)
                skeleton_path = target_file(root, skeleton_target)
                skeleton_exists = skeleton_path.is_file()
                if not skeleton_exists:
                    issue("independent_skeleton_file_missing", target=skeleton_target)
                skeleton_sha = digest(skeleton_path) if skeleton_exists else None
                skeleton_bytes = skeleton_path.stat().st_size if skeleton_exists else None
                if verified and result.get("skeleton", "").split(".", 1)[0] != skeleton_target:
                    issue("native_skeleton_reference_disagrees", target=target)
                index.append({"target": skeleton_target, "file": skeleton_path.relative_to(root).as_posix(),
                              "bytes": skeleton_bytes, "sha256": skeleton_sha, "type": "Skeleton",
                              "kind": "skeletons", "source_file": row["file"], "source_sha256": row["sha256"],
                              "source_package": row.get("skeleton_source"), "mesh_target": target,
                              "validation": "native_bind_skeleton_verified_and_package_present" if verified and skeleton_exists
                              else "incomplete_or_unverified"})
                indexed_targets.add(skeleton_target)

    support_targets = set()
    for row in state_stages.get("materials", {}).values():
        support_targets.update(row.get("result", {}).get("support_assets", []))
    support_disk = root / "Content/PCAssets/Support"
    support_targets.update("/Game/" + path.relative_to(root / "Content").with_suffix("").as_posix()
                           for path in support_disk.rglob("*.uasset"))
    for target in sorted(support_targets):
        if target not in STOCK_SUPPORT:
            issue("unrecognized_support_asset", target=target)
            continue
        path = target_file(root, target)
        if not path.is_file():
            issue("support_asset_missing", target=target)
            continue
        index.append({"target": target, "file": path.relative_to(root).as_posix(), "bytes": path.stat().st_size,
                      "sha256": digest(path), "type": "Texture2D", "kind": "stock_support",
                      "source_file": STOCK_SUPPORT[target], "source_sha256": None, "source_package": STOCK_SUPPORT[target],
                      "validation": "material_import_report_membership_and_package_present",
                      "conversion_scope": "stock_UE_default_support_texture_not_converted_game_resource"})
        indexed_targets.add(target)

    if environment.get("status") != ENVIRONMENT_SUCCESS:
        issue("environment_candidates_not_completed", status=environment.get("status"))
    env_expected = {row["target"]: row for row in env_plan.get("candidates", [])}
    env_saved = {row["target"]: row for row in environment.get("saved", [])}
    if len(env_saved) != len(environment.get("saved", [])):
        issue("duplicate_environment_report_target")
    if not env_expected or set(env_expected) != set(env_saved):
        issue("environment_candidate_coverage_mismatch", missing=sorted(set(env_expected) - set(env_saved)),
              extra=sorted(set(env_saved) - set(env_expected)))
    if environment.get("candidate_count") != len(env_expected):
        issue("environment_report_candidate_count_mismatch")
    if environment.get("source_inputs") != env_plan.get("source_inputs"):
        issue("environment_source_input_hashes_disagree")
    for name, expected_sha in env_plan.get("source_inputs", {}).items():
        if inputs.get(name, {}).get("sha256") != expected_sha:
            issue("environment_plan_input_changed", file=name)
    for target, saved in sorted(env_saved.items()):
        path = target_file(root, target, environment=True)
        if not path.is_file():
            issue("environment_candidate_file_missing", target=target)
            continue
        candidate = env_expected.get(target, {})
        if saved.get("source_package") != candidate.get("source_package") or saved.get("platform_branch") != candidate.get("platform_branch"):
            issue("environment_candidate_source_disagrees", target=target)
        actual_sha, actual_bytes = digest(path), path.stat().st_size
        if saved.get("asset_sha256") and saved["asset_sha256"] != actual_sha:
            issue("environment_saved_asset_hash_changed", target=target)
        if saved.get("asset_bytes") is not None and saved["asset_bytes"] != actual_bytes:
            issue("environment_saved_asset_bytes_changed", target=target)
        env_source = "PCResources/Environment/scene_parameters.json"
        index.append({"target": target, "file": path.relative_to(root).as_posix(), "bytes": actual_bytes,
                      "sha256": actual_sha, "type": "ViewerEnvironment", "kind": "inactive_environment_candidates",
                      "source_file": env_source, "source_sha256": inputs.get(env_source, {}).get("sha256"),
                      "source_package": saved.get("source_package"), "platform_branch": saved.get("platform_branch"),
                      "validation": "candidate_save_report_and_package_present; rendered_result_unverified",
                      "unresolved": saved.get("unresolved", [])})
        indexed_targets.add(target)
    for target in sorted(set(env_expected) - set(env_saved)):
        path = target_file(root, target, environment=True)
        env_source = "PCResources/Environment/scene_parameters.json"
        index.append({"target": target, "file": path.relative_to(root).as_posix(),
                      "bytes": path.stat().st_size if path.is_file() else None,
                      "sha256": digest(path) if path.is_file() else None,
                      "type": "ViewerEnvironment", "kind": "inactive_environment_candidates",
                      "source_file": env_source, "source_sha256": inputs.get(env_source, {}).get("sha256"),
                      "source_package": env_expected[target].get("source_package"),
                      "platform_branch": env_expected[target].get("platform_branch"),
                      "validation": "incomplete_or_unverified"})
        indexed_targets.add(target)
    catalog_name = "PCResources/Characters/catalog.json"
    catalog = load_snapshot(root / catalog_name)
    expected_collections = {}
    catalog_mesh_packages = set()
    for family in catalog.get("families", []):
        family_id = family.get("id", "")
        target = "/Game/PCAssets/Collections/DA_" + family_id
        if not re.fullmatch(r"[A-Za-z0-9_]+", family_id) or target in expected_collections:
            issue("invalid_or_duplicate_collection_family", family=family_id)
            continue
        source_packages = [mesh["source_package"] for group in family.get("groups", []) for mesh in group.get("meshes", [])]
        references = set()
        for package in source_packages:
            reference = plan.get("packages", {}).get(package)
            if isinstance(reference, dict):
                reference = reference.get("target")
            if reference not in {row["target"] for row in rows_by_kind["meshes"]}:
                issue("collection_source_mesh_without_planned_asset", source_package=package)
            else:
                references.add(reference)
        if len(source_packages) != family.get("mesh_count") or len(source_packages) != len(set(source_packages)):
            issue("collection_family_source_count_disagrees", family=family_id)
        catalog_mesh_packages.update(source_packages)
        expected_collections[target] = {"source_package": family_id, "source_file": catalog_name,
                                        "source_sha256": inputs.get(catalog_name, {}).get("sha256"),
                                        "source_type": "PCCharacterCollection", "references": sorted(references)}
    if len(catalog_mesh_packages) != catalog.get("mesh_packages"):
        issue("collection_catalog_mesh_total_disagrees")
    family_targets = set(expected_collections)
    library_target = "/Game/PCAssets/Collections/DA_PCAssetLibrary"
    if library_target in expected_collections:
        issue("collection_family_library_target_collision")
    library_references = family_targets | {row["target"] for kind in KINDS for row in rows_by_kind[kind]}
    expected_collections[library_target] = {"source_package": "PCAssetLibrary", "source_file": source_name,
                                           "source_sha256": plan.get("source_manifest_sha256"),
                                           "source_type": "PCAssetLibrary", "references": sorted(library_references)}
    expected_indices = {
        catalog_name: {"file": catalog_name, "sha256": inputs.get(catalog_name, {}).get("sha256"),
                       "source_record": next((row["target"] for row in rows_by_kind["documents"] if row["file"] == catalog_name), None)},
        source_name: {"file": source_name, "sha256": plan.get("source_manifest_sha256"),
                      "source_record": next((row["target"] for row in rows_by_kind["documents"] if row["file"] == source_name), None)},
        "Saved/PCAssetConversion/asset_plan.json": {"file": "Saved/PCAssetConversion/asset_plan.json", "sha256": plan_sha,
                                                   "scope": "deterministically_generated_local_import_plan"},
    }
    actual_indices = {row.get("file"): row for row in collections.get("input_indices", [])}
    if (actual_indices != expected_indices or len(actual_indices) != len(collections.get("input_indices", []))):
        issue("collection_source_indices_disagree")
    if collections.get("status") != "passed" or collections.get("errors"):
        issue("collections_report_not_completed", status=collections.get("status"))
    if collections.get("plan_sha256") != plan_sha:
        issue("collections_report_plan_hash_disagrees")
    if collections.get("schema_version") != 1 or collections.get("owner") != "pc_asset_collections_v1":
        issue("collections_report_schema_or_owner_disagrees")
    saved_collections = {row.get("target"): row for row in collections.get("collections", [])}
    if len(saved_collections) != len(collections.get("collections", [])):
        issue("duplicate_collection_report_target")
    if set(saved_collections) != set(expected_collections):
        issue("collection_target_coverage_disagrees", missing=sorted(set(expected_collections) - set(saved_collections)),
              extra=sorted(set(saved_collections) - set(expected_collections)))
    expected_collection_counts = {**{kind: len(rows_by_kind[kind]) for kind in KINDS},
                                  "character_collections": len(family_targets),
                                  "source_character_mesh_packages": len(catalog_mesh_packages),
                                  "source_documents": len(rows_by_kind["documents"])}
    if collections.get("counts") != expected_collection_counts:
        issue("collections_report_counts_disagree")
    valid_collections = 0
    for target, expected in sorted(expected_collections.items()):
        path = target_file(root, target)
        saved = saved_collections.get(target, {})
        exists = path.is_file()
        actual_sha = digest(path) if exists else None
        actual_bytes = path.stat().st_size if exists else None
        valid = bool(saved)
        if not exists:
            issue("collection_asset_missing", target=target)
            valid = False
        for key in ("source_package", "source_file", "source_sha256", "source_type"):
            if saved.get(key) != expected[key]:
                issue("collection_source_identity_disagrees", target=target, field=key)
                valid = False
        if saved.get("asset_sha256") != actual_sha or saved.get("asset_bytes") != actual_bytes:
            issue("collection_saved_bytes_or_hash_disagree", target=target)
            valid = False
        if (saved.get("soft_reference_count") != len(expected["references"])
                or saved.get("saved_soft_dependency_check") != "passed"
                or saved.get("editor_only_by_PCSourceRecord_class") is not True):
            issue("collection_soft_dependencies_or_editor_only_check_missing", target=target)
            valid = False
        if valid:
            valid_collections += 1
        index.append({"target": target, "file": path.relative_to(root).as_posix(), "bytes": actual_bytes,
                      "sha256": actual_sha, "type": "PCSourceRecord", "kind": "editor_navigation_collections",
                      "source_file": expected["source_file"], "source_sha256": expected["source_sha256"],
                      "source_package": expected["source_package"], "editor_only": True,
                      "soft_references": expected["references"], "soft_reference_count": len(expected["references"]),
                      "validation": "saved_soft_dependencies_and_package_hash_passed" if valid else "incomplete_or_unverified",
                      "conversion_scope": "editor_navigation_collection_not_runtime_outfit"})
        indexed_targets.add(target)

    approved_viewer_assets, viewer_approval = {}, None
    if allow_viewer_integration:
        approved_viewer_assets, viewer_approval = approved_viewer_integration_assets(root, indexed_targets)
        snapshots.update({root / name: expected_sha for name, expected_sha in viewer_approval["inputs"].items()})
    output_tree_counts = {}
    for label, directory, prefix in (("pc_assets", root / "Content/PCAssets", "/Game/PCAssets/"),
                                      ("environment_candidates", root / "Content/Viewer/PCEnvironment", "/Game/Viewer/PCEnvironment/")):
        actual_targets = {"/Game/" + path.relative_to(root / "Content").with_suffix("").as_posix()
                          for path in directory.rglob("*.uasset")}
        expected_targets = {target for target in indexed_targets if target.startswith(prefix)}
        approved_targets = {target for target in approved_viewer_assets if target.startswith(prefix)}
        coverage = viewer_tree_coverage(expected_targets, actual_targets, approved_targets)
        expected_targets = coverage["expected"]
        missing, extras = coverage["missing"], coverage["extras"]
        if missing:
            issue("indexed_generated_asset_missing", directory=label, targets=sorted(missing))
        if extras:
            issue("unindexed_generated_asset", directory=label, targets=sorted(extras))
        output_tree_counts[label] = {"expected": len(expected_targets), "actual": len(actual_targets),
                                    "missing": len(missing), "unindexed": len(extras),
                                    "exact_match": actual_targets == expected_targets}
        if allow_viewer_integration:
            output_tree_counts[label].update(original_conversion_expected=coverage["original_expected"],
                                             approved_viewer_additions=coverage["approved_viewer_additions"])

    for path, original_sha in snapshots.items():
        if not path.is_file() or digest(path) != original_sha:
            issue("audit_input_changed_during_run", file=path.relative_to(root).as_posix())
    missing_refs = [row for row in plan.get("unknowns", []) if row.get("kind") == "material_texture_target_missing"]
    missing_packages = sorted({row.get("texture_package") for row in missing_refs if row.get("texture_package")})
    # This is a structural completion state only. It must not claim the
    # original custom shader, Niagara, animation, or viewer integration passed.
    status = "passed_structural_conversion" if not failures else "incomplete_or_failed"
    counts = {"planned_" + kind: len(rows_by_kind[kind]) for kind in KINDS}
    counts.update({"manifest_resource_files": len(manifest_names), "planned_input_files_including_manifest": len(inputs),
                   "verified_input_bytes": verified_input_bytes, "planned_output_packages": len(index),
                   "existing_output_packages": sum(row.get("sha256") is not None for row in index),
                   "fresh_verified_main_assets": sum(row.get("validation") == "fresh_editor_verification_and_file_hash_passed" for row in index),
                   "native_verified_meshes": len(native_results), "independent_skeletons_present": sum(row["kind"] == "skeletons" and row["sha256"] is not None for row in index),
                   "stock_support_assets": sum(row["kind"] == "stock_support" for row in index),
                   "character_navigation_collections": len(family_targets), "planned_navigation_collections": len(expected_collections),
                   "verified_navigation_collections": valid_collections,
                   "environment_candidates_saved": len(env_saved), "source_texture_references_unavailable": len(missing_refs),
                   "unique_texture_packages_unavailable": len(missing_packages), "failure_count": len(failures)})
    mesh_stats = {
        "verified_mesh_count": len(native_results),
        "source_points": sum(row["points"] for row in native_results),
        "source_triangles": sum(row["triangles"] for row in native_results),
        "source_bones": sum(row["bones"] for row in native_results),
        "source_weight_rows_checked": sum(row["source_weight_rows_checked"] for row in native_results),
        "built_vertices_checked": sum(row["built_vertices_checked"] for row in native_results),
        "built_weight_rows_checked": sum(row["built_weight_rows_checked"] for row in native_results),
        "maximum_influences": max((row["maximum_influences"] for row in native_results), default=0),
        "maximum_uv_channels": max((row["uv_channels"] for row in native_results), default=0),
        "uv_channel_distribution": dict(Counter(str(row["uv_channels"]) for row in native_results)),
        "mesh_count_with_vertex_colors": sum(row["vertex_colors"] for row in native_results),
        "maximum_weight_absolute_tolerance": max((row["weight_absolute_tolerance"] for row in native_results), default=0),
        "corner_layout_version": 2,
        "tangent_build_method": "MeshDescriptionMikkTSpace",
        "mesh_description_corner_wedge_mismatches": sum(row["mesh_description_corner_wedge_mismatches"] for row in native_results),
        "mesh_description_mikk_zero_tangents": sum(row["mesh_description_mikk_zero_tangents"] for row in native_results),
        "mesh_count_with_mesh_description_mikk_zero_tangents": sum(row["mesh_description_mikk_zero_tangents"] > 0 for row in native_results),
        "mesh_description_mikk_zero_tangent_scope": "Raw generated MeshDescription vertex-instance count, which may include vertices on the reported degenerate geometry/UV triangles. It is a different counting domain from built render-vertex tangent exceptions and need not be zero. Built nondegenerate frames must still verify with built_tangent_frames_invalid equal to zero.",
        "built_tangent_frames_checked": sum(row["built_tangent_frames_checked"] for row in native_results),
        "built_tangent_frames_invalid": sum(row["built_tangent_frames_invalid"] for row in native_results),
        "normalized_source_normal_component_error_max": max((row["normal_error_max"] for row in native_results), default=0),
        "normalized_built_normal_length_squared_error_max": max((row["maximum_built_normal_length_squared_error"] for row in native_results), default=0),
        "tangent_orthogonality_error_max_including_degenerate_exceptions": max((row["tangent_orthogonality_error_max"] for row in native_results), default=0),
        "tangent_orthogonality_error_max_among_meshes_without_exceptions": max((row["tangent_orthogonality_error_max"]
            for row in native_results if row["degenerate_count"] == 0), default=0),
        "degenerate_uv_or_geometry_tangent_exceptions": sum(row["degenerate_count"] for row in native_results),
        "near_degenerate_geometry_tangent_exceptions": sum(row["near_degenerate_geometry_tangent_exceptions"] for row in native_results),
        "mesh_count_with_near_degenerate_geometry_tangent_exceptions": sum(row["near_degenerate_geometry_tangent_exceptions"] > 0 for row in native_results),
        "geometry_normal_squared_tolerance": 1e-8,
        "uv_determinant_absolute_tolerance": 1e-12,
        "tangent_degeneracy_classification": "Stable incident geometry requires finite triangle cross-product squared length >= UE_SMALL_NUMBER (1e-8), matching UE safe normalization, and finite absolute UV determinant > 1e-12. This is not MikkTSpace's internal degeneracy threshold. Near-degenerate exceptions touch a nonzero cross product below that geometry threshold and have no stable incident geometry/UV triangle.",
        "mesh_count_with_degenerate_tangent_exceptions": sum(row["degenerate_count"] > 0 for row in native_results),
        "tangent_exception_policy": "Only render vertices whose every incident built triangle has degenerate UVs or geometry, including geometry below the UE safe-normal threshold, may have a finite invalid tangent frame; these are counted separately and are not claimed as verified nondegenerate tangent frames. Nonfinite tangent vectors cannot receive an exception.",
        "nondegenerate_tangent_frame_orthogonality_limit": 0.001,
        "tangent_orthogonality_metric_scope": "Native per-mesh maximum includes degenerate exceptions; a separate exact maximum restricted to nondegenerate vertices is not emitted by the native report.",
        "corner_rebuild_meshes_with_comparison": len(native_rebuild_comparisons),
        "built_vertices_before_corner_rebuild": sum(row["before"] for row in native_rebuild_comparisons),
        "built_vertices_after_corner_rebuild": sum(row["after"] for row in native_rebuild_comparisons),
        "built_vertices_removed_by_corner_rebuild": sum(row["before"] - row["after"] for row in native_rebuild_comparisons),
        "corner_rebuild_comparison_scope": "Before/after values describe each mesh's latest recorded MeshDescription Mikk rebuild. Before may already use an intermediate canonical layout; these totals are not claimed as the original pre-fix baseline.",
    }
    limitations = [
        "Material assets are editable stock UE candidates; the original private shaders and complete output wiring are not reconstructed.",
        "Source JSON/Markdown records are editor-only evidence assets, not playable animation, cloth, Blueprint, Niagara or recovered material graphs.",
        "Unavailable source texture references remain unavailable; stock helper textures do not count as converted game resources.",
        "Environment assets are inactive candidates with documented unresolved fields; current viewer routing is unchanged.",
        "ActorX preserves the exported highest LOD; original bone scales and tangent vectors were absent, and tangents are recomputed.",
        "Structural/fresh-load verification does not establish target-RHI shader success, appearance, animation or device performance.",
    ]
    rendered = audit_render_evidence(root, plan, plan_sha, index)
    required_satisfied = rendered["required_render_validation_satisfied"]
    validation = {"editor_asset_structure": status, "target_RHI_shader_validation": rendered["shaders"]["status"],
                  "animation_and_cloth": "not_reconstructed_or_verified", "visual_result": rendered["preview"]["status"],
                  "mac_editor_build": rendered["builds"]["mac_editor"]["status"],
                  "ios_unsigned_compile_link": rendered["builds"]["ios"]["status"],
                  "viewer_catalog_integration": "not_performed", "device_runtime": "not_run",
                  "original_game_visual_comparison": "not_run"}
    overall_status = ("failed_structural_conversion" if failures else
                      "failed_required_render_validation" if require_render_validation and not required_satisfied else
                      "passed_required_validation" if require_render_validation else "passed_structural_conversion")
    common = {"schema_version": 1, "status": status, "plan_sha256": plan_sha,
              "overall_status": overall_status, "require_render_validation": require_render_validation,
              "source_manifest": source_name, "source_manifest_sha256": plan.get("source_manifest_sha256"),
              "importer_version": version, "counts": counts, "validation": validation, "limitations": limitations}
    report = {**common, "generated_at_utc": datetime.now(timezone.utc).isoformat(), "stages": stage_counts,
              "native_mesh_validation": mesh_stats, "failure_kinds": dict(Counter(row["kind"] for row in failures)),
              "render_evidence": rendered,
              "output_tree_coverage": output_tree_counts,
              "failures": failures, "unknown_source_texture_references": missing_refs,
              "unknown_source_texture_packages": missing_packages, "output_main_asset_bytes": output_bytes}
    if allow_viewer_integration:
        report.update(allow_viewer_integration=True, viewer_integration_additions=viewer_approval,
                      original_conversion_index_rewritten=False,
                      viewer_approval_scope="Current reports, saved package fingerprints and existing integration audit only; no new UE, shader or runtime test is performed.")
    asset_index = {**common, "assets": sorted(index, key=lambda row: row["target"])}
    summary = {**common, "native_mesh_validation": mesh_stats,
               "render_evidence": rendered,
               "output_tree_coverage": output_tree_counts,
               "navigation_collections": {"report_status": collections.get("status"), "planned": len(expected_collections),
                                          "verified": valid_collections, "source_indices": list(expected_indices.values())},
               "stage_completion": stage_counts, "remaining_structural_failures": len(failures),
               "source_snapshot": {"character_mesh_packages": plan.get("counts", {}).get("source_character_mesh_packages"),
                                   "unique_geometry": plan.get("counts", {}).get("unique_input_geometry"),
                                   "source_packages": plan.get("counts", {}).get("source_packages")},
               "unavailable_texture_policy": {"reference_count": len(missing_refs), "unique_packages": missing_packages,
                                              "converted": False, "empty_original_assets_created": False},
               "unfinished_implementation": ["Original private shader/function graph recovery", "Animation and cloth reconstruction",
                                              "Executable particle/Blueprint recreation", "Viewer source mode and scene routing",
                                              "All-character rendered appearance and motion validation", "iOS asset cook and device validation"]}
    return report, asset_index, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--no-docs", action="store_true", help="Write only the local final_report.json during an in-progress audit")
    parser.add_argument("--require-render-validation", action="store_true",
                        help="Also require current complete Metal SM6 material evidence and a verified nonblank reference-pose preview")
    parser.add_argument("--allow-viewer-integration", action="store_true",
                        help="Permit only current report/hash-approved viewer packages; write a separate local recheck and never rewrite conversion docs/index")
    args = parser.parse_args()
    root = args.root.resolve()
    report_path = (root / "Saved/PCViewerIntegration/source_conversion_recheck.json" if args.allow_viewer_integration
                   else root / "Saved/PCAssetConversion/final_report.json")
    try:
        report, asset_index, summary = audit(root, args.require_render_validation, args.allow_viewer_integration)
    except Exception as error:
        report = {"schema_version": 1, "status": "audit_error", "error": str(error),
                  "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                  "validation": {"editor_asset_structure": "not_passed", "shader_animation_visual": "not_run"}}
        write_json(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1
    write_json(report_path, report)
    if not args.no_docs and not args.allow_viewer_integration:
        write_json(root / "Docs/PCAssetConversion/asset_index.json", asset_index)
        write_json(root / "Docs/PCAssetConversion/conversion_summary.json", summary)
    print(json.dumps({key: report[key] for key in ("status", "overall_status", "counts", "stages", "native_mesh_validation", "failure_kinds", "validation")},
                     ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed_structural_conversion" and (
        not args.require_render_validation or report["render_evidence"]["required_render_validation_satisfied"]) else 1


if __name__ == "__main__":
    sys.exit(main())
