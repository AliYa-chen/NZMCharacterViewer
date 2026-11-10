#!/usr/bin/env python3
"""Archive the completed native shading/Chaos cloth evidence, without raw data.

Only the explicit feature reports, verified build logs and six runtime PNGs
are included. Content packages and the 231 MB temporary source cache are
fingerprinted but never copied into the archive. --preflight-only validates
without publishing; --verify-only checks a published archive after Saved cleanup.
No Unreal process, build, rendering, cleanup or Git operation is performed.
"""
import argparse
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import tarfile
import tempfile

from archive_pc_viewer_evidence import png_size


ROOT = Path(__file__).resolve().parent.parent
WORK = "Saved/PCFeatureIntegration"
DESTINATION = "Docs/PCFeatureIntegration"
ARCHIVE = DESTINATION + "/integration_evidence.tar.gz"
MANIFEST = DESTINATION + "/evidence_manifest.json"
SOURCE_COMMIT = "03152adb7ce937649f890455d6650f346e82acc1"
CATALOG = "Content/Viewer/Data/DA_ViewerCatalog.uasset"
RUNTIME_MODULE = "Binaries/Mac/libUnrealEditor-NZMCharacterViewer.dylib"
INDEX = "Docs/PCAssetConversion/asset_index.json"
MAX_MEMBER_BYTES = 128 * 1024 * 1024
RUNTIME_CASES = 6
RUNTIME_CLOTH_PARTS = 7
FIXED_NAMES = (
    "source_manifest.json", "shading_plan.json", "shading_report.json",
    "shading_shader_verification.json", "cloth_plan.json", "cloth_report.json",
    "cloth_fresh_verification.json", "cloth_source_array_review.json",
    "catalog_feature_report.json", "catalog_feature_snapshot.json",
    "build_macos_verified.log", "validation_summary.json", "ContentAudit.json",
    "Runtime/Report.json", "iOS/BuildIOSCompileVerification.json", "iOS/BuildIOSCompile.log",
)
FIXED_FILES = {WORK + "/" + name for name in FIXED_NAMES}
SHADER_FLAGS = ("shader_map_present", "platform_matches_active_rhi", "compilation_finalized",
                "compiled_successfully", "required_shader_set_complete", "valid_for_rendering")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def relative_name(root, value):
    root = Path(root).resolve()
    path = Path(value)
    if path.is_absolute():
        require(path.is_relative_to(root), "File is outside the project: " + str(value))
        value = path.relative_to(root).as_posix()
    else:
        value = str(value)
    require(value and "\\" not in value and not PurePosixPath(value).is_absolute()
            and all(part not in ("", ".", "..") for part in value.split("/")),
            "Invalid project-relative file: " + value)
    return value


def local(root, value, max_bytes=None):
    root = Path(root).resolve()
    name = relative_name(root, value)
    path = root / name
    require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root)
            and not any(parent.is_symlink() for parent in path.parents if parent != root),
            "Expected a regular project file without symlinks: " + name)
    require(max_bytes is None or path.stat().st_size <= max_bytes, "Evidence file is too large: " + name)
    return path


def archive_name_allowed(name):
    if name in FIXED_FILES:
        return True
    prefix = WORK + "/Runtime/"
    return (name.startswith(prefix) and "/" not in name[len(prefix):]
            and name.endswith(".png") and name[len(prefix):].startswith("Case_"))


def file_record(root, name, max_bytes=MAX_MEMBER_BYTES):
    path = local(root, name, max_bytes)
    return {"file": path.relative_to(Path(root).resolve()).as_posix(),
            "bytes": path.stat().st_size, "sha256": digest(path)}


def package(value):
    return str(value).split(".", 1)[0]


def asset_name(target):
    target = package(target)
    require(target.startswith("/Game/") and all(part not in ("", ".", "..")
            for part in target[6:].split("/")), "Invalid project package: " + target)
    return "Content/" + target[6:] + ".uasset"


def preflight(root=ROOT):
    root = Path(root).resolve()
    hashes = {}

    def current_hash(name):
        relative = relative_name(root, name)
        if relative not in hashes:
            hashes[relative] = digest(local(root, relative))
        return hashes[relative]

    def checked_asset(row, target=None):
        reference = target or row.get("target") or row.get("package")
        name = asset_name(reference)
        require(row.get("file") == name, "Asset fingerprint does not identify its exact package: " + name)
        path = local(root, name)
        require(path.stat().st_size == row["bytes"] and current_hash(name) == row["sha256"],
                "Saved package changed after validation: " + name)
        return {"package": package(reference), "file": name, "bytes": row["bytes"], "sha256": row["sha256"]}

    documents = {name: read(local(root, WORK + "/" + name)) for name in FIXED_NAMES if name.endswith(".json")}
    sources = documents["source_manifest.json"]
    require(sources.get("status") == "verified_selected_source_cache" and sources.get("source_commit") == SOURCE_COMMIT,
            "Source manifest does not identify the verified published source cache")
    require(sources.get("asset_index_sha256") == current_hash(INDEX), "Source manifest uses another conversion index")
    source_rows = {row["source_file"]: row for row in sources["sources"]}
    require(len(source_rows) == len(sources["sources"]) and len(source_rows) == sources["counts"]["source_JSON_files"]
            and sum(row["source_file"].endswith("/ClothPhysicalMeshDataNv.json") for row in source_rows.values()) == 25,
            "Source manifest has duplicate/missing source records")
    # Check the source hashes against the already published conversion plan.
    # This does not read or include the temporary raw JSON cache.
    from export_pc_feature_sources import read_asset_plan
    original_inputs = {row["file"]: row for row in read_asset_plan(root)["inputs"]}
    for name, row in source_rows.items():
        require(name in original_inputs and name.startswith("PCResources/") and name.endswith(".json")
                and all(row[key] == original_inputs[name][key] for key in ("bytes", "sha256")),
                "Source manifest fingerprint differs from the published conversion: " + name)

    shading = documents["shading_report.json"]
    shading_plan = documents["shading_plan.json"]
    shaders = documents["shading_shader_verification.json"]
    require(shading.get("status") == "passed" and shading.get("owner") == "pc_viewer_native_shading_v1"
            and shading.get("protected_assets_unchanged") is True, "Native shading authoring did not pass")
    require(shading_plan.get("status") == "planned" and shading_plan.get("owner") == shading["owner"]
            and shading_plan.get("counts") == shading.get("counts"), "Native shading plan/report coverage differs")
    for document in (shading, shading_plan):
        require(document.get("plan_sha256") == sources["viewer_plan_sha256"]
                and document.get("asset_index_sha256") == current_hash(INDEX)
                and document.get("source_manifest_sha256") == current_hash(WORK + "/source_manifest.json")
                and document.get("cloth_plan_sha256") == current_hash(WORK + "/cloth_plan.json"),
                "Native shading uses stale viewer/source/cloth inputs")
    new_shading = shading["masters"] + shading["materials"] + shading["profiles"]
    require(len(shading["masters"]) == 3 and len(shading["materials"]) == 60 and len(shading["profiles"]) == 3
            and len({row["target"] for row in new_shading}) == 66, "Expected all66native shading packages")
    require({row["target"] for row in shading_plan["materials"]} == {row["target"] for row in shading["materials"]}
            and {row["target"] for row in shading_plan["profiles"]} == {row["target"] for row in shading["profiles"]}
            and set(shading_plan["masters"].values()) == {row["target"] for row in shading["masters"]},
            "Saved native shading targets differ from their plan")
    new_assets = []
    for row in new_shading:
        require(row["target"].startswith("/Game/PCAssets/ViewerShading/"), "Shading package is outside its owner root")
        new_assets.append(checked_asset(row))
    require(shaders.get("status") == "passed" and shaders.get("success") is True
            and shaders.get("owner") == shading["owner"] and shaders.get("content_saved") is False
            and shaders.get("rhi") == "Metal" and shaders.get("using_null_rhi") is False
            and shaders.get("shader_platform") == "METAL_SM6" and shaders.get("shader_format") == "SF_METAL_SM6"
            and shaders.get("feature_level") == "SM6"
            and shaders.get("report_sha256") == current_hash(WORK + "/shading_report.json"),
            "Fresh native shader verification did not use the current report on real MetalSM6")
    for key in ("requested_count", "passed_count", "checked_count"):
        require(shaders.get(key) == 63, "Native shader verification does not cover63materials: " + key)
    require(shaders.get("failed_count") == 0 and shaders.get("shader_jobs_after") == 0,
            "Native material shaders have errors or unfinished jobs")
    expected_shader_targets = {row["target"] for row in shading["masters"] + shading["materials"]}
    actual_shader_targets = [row["package"] for row in shaders["materials"]]
    require(len(actual_shader_targets) == len(set(actual_shader_targets)) == 63
            and set(actual_shader_targets) == expected_shader_targets, "Native shader target coverage differs")
    for row in shaders["materials"]:
        require(row.get("status") == "passed" and all(row.get(flag) is True for flag in SHADER_FLAGS)
                and row.get("compile_errors") == [] and "error" not in row and row.get("shader_count", 0) > 0
                and package(row.get("base_material")) in expected_shader_targets
                and package(row.get("resource_owner")) in expected_shader_targets,
                "Native shader map failed or resolved to a fallback: " + row["package"])

    protected = shading["protected_assets"]
    require(len(protected) == len({row["target"] for row in protected}) == 6918,
            "Protected native/source package coverage differs from6918")
    index_assets = {row["target"]: row for row in read(root / INDEX)["assets"]}
    protected_by_target = {row["target"]: row for row in protected}
    require(set(index_assets) <= set(protected_by_target), "Protected records omit original converted packages")
    for target, row in index_assets.items():
        require(all(protected_by_target[target][key] == row[key] for key in ("file", "bytes", "sha256")),
                "Protected baseline differs from the original conversion index: " + target)
    for row in protected:
        checked_asset(row)

    cloth_plan, cloth, fresh, arrays = (documents[name] for name in
        ("cloth_plan.json", "cloth_report.json", "cloth_fresh_verification.json", "cloth_source_array_review.json"))
    expected_cloth_counts = {"source_meshes": 9, "authored_meshes": 7, "source_cloth_assets": 25,
                             "authored_cloth_assets": 19, "live_source_LOD0_sections": 7, "runtime_part_bindings": 7}
    require(cloth_plan.get("status") == "planned_native_chaos_vertex_cloth"
            and cloth.get("status") == "passed" and cloth.get("owner") == cloth_plan.get("owner") == "pc_viewer_chaos_cloth_v1"
            and cloth.get("counts") == cloth_plan.get("counts") == expected_cloth_counts
            and cloth.get("catalog_saved") is False and cloth.get("original_meshes_saved") is False
            and cloth.get("errors") == [], "Native cloth authoring did not pass all7meshes/19assets")
    require(cloth.get("plan_sha256") == current_hash(WORK + "/cloth_plan.json")
            and cloth.get("viewer_plan_sha256") == cloth_plan.get("viewer_plan_sha256") == sources["viewer_plan_sha256"]
            and cloth.get("asset_index_sha256") == cloth_plan.get("asset_index_sha256") == current_hash(INDEX)
            and cloth_plan.get("source_manifest_sha256") == current_hash(WORK + "/source_manifest.json"),
            "Native cloth authoring uses stale source/plan inputs")
    require(fresh.get("status") == "passed" and fresh.get("content_saved") is False
            and fresh.get("cloth_report_sha256") == current_hash(WORK + "/cloth_report.json"),
            "Saved cloth copies have not been freshly checked against their current report")
    require(arrays.get("status") == "passed_source_array_review" and arrays.get("not_runtime_simulation_evidence") is True
            and arrays.get("cloth_plan_sha256") == current_hash(WORK + "/cloth_plan.json"),
            "Source cloth array review is stale or misrepresents runtime evidence")
    planned_cloth = {row["target"]: row for row in cloth_plan["meshes"]}
    saved_cloth = {row["target"]: row for row in cloth["meshes"]}
    fresh_cloth = {row["target"]: row for row in fresh["meshes"]}
    require(len(planned_cloth) == len(saved_cloth) == len(fresh_cloth) == 7
            and set(planned_cloth) == set(saved_cloth) == set(fresh_cloth)
            and len(arrays["live_source_cloth"]) == 7
            and {row["target"] for row in arrays["live_source_cloth"]} == set(saved_cloth),
            "Native cloth plan/report/fresh/source-review mesh coverage differs")
    require(cloth["part_bindings"] == cloth_plan["part_bindings"] and len(cloth["part_bindings"]) == 7,
            "Native cloth part binding coverage differs")
    for target, row in saved_cloth.items():
        planned, reloaded = planned_cloth[target], fresh_cloth[target]
        require(target.startswith("/Game/PCAssets/ViewerCloth/") and row.get("status") == "passed"
                and row["source_mesh"] == planned["source_mesh"] and row["skeleton"] == planned["skeleton"]
                and row["inspection"] == reloaded["inspection"] and row["asset"] == reloaded["asset"]
                and row["inspection"].get("status") == "passed"
                and row["inspection"].get("has_active_clothing") is True
                and row["live_cloth_sections"] == planned["live_cloth_sections"] == 1,
                "A saved cloth copy differs from its plan/fresh inspection: " + target)
        source_cloths = {"CA_" + item["name"]: item for item in planned["cloths"]}
        require(len(row["cloth_assets"]) == len(source_cloths), "Native cloth subasset coverage differs: " + target)
        for asset in row["cloth_assets"]:
            original = source_cloths[asset["name"]]
            require(asset.get("chaos_config") is True and asset.get("collision_bodies", 0) > 0
                    and asset.get("live_binding") == original["bind"]
                    and all(asset[key] == original[key] for key in ("physical_vertices", "triangles", "pinned_vertices")),
                    "Native cloth physical data/configuration does not match its verified source")
        new_assets.append(checked_asset(row["asset"], target))
    require(sum(len(row["cloth_assets"]) for row in saved_cloth.values()) == 19,
            "Native cloth report omits authored physical meshes")
    for row in arrays["live_source_cloth"]:
        require(row.get("source_zero_distance_pins", 0) > 0 and row.get("stock_chaos_dynamic_vertices", 0) > 0
                and row.get("weight_sum_error_max", math.inf) <= 1e-5
                and all(math.isfinite(value) and 0.99 <= value <= 1.01 for value in row["normal_length_min_max"]),
                "Source cloth pin/normal/skin-weight review failed")
    for folder, expected in (("ViewerShading", 66), ("ViewerCloth", 7)):
        actual = {path.relative_to(root).as_posix() for path in (root / "Content/PCAssets" / folder).rglob("*.uasset")}
        recorded = {row["file"] for row in new_assets if row["file"].startswith("Content/PCAssets/" + folder + "/")}
        require(len(actual) == expected and actual == recorded, "Unexpected/missing packages remain in " + folder)

    catalog = documents["catalog_feature_report.json"]
    snapshot = documents["catalog_feature_snapshot.json"]
    catalog_hash, runtime_hash = current_hash(CATALOG), current_hash(RUNTIME_MODULE)
    require(catalog.get("status") == "passed" and catalog.get("pc_parts") == 72 and catalog.get("cloth_parts") == 7
            and catalog.get("baseline_parts_preserved") == 40 and catalog.get("unchanged_fields_verified") is True
            and catalog.get("eye_overrides_preserved") is True and catalog.get("portrait_rendering_performed") is False
            and catalog.get("catalog_after_sha256") == catalog_hash, "Feature Catalog binding is incomplete or stale")
    for name, expected in catalog["inputs"].items():
        require(current_hash(name) == expected, "Feature Catalog binding uses stale evidence: " + name)
    snapshot_outfits = {(character["id"], outfit["id"]): outfit for character in snapshot for outfit in character["outfits"]}
    snapshot_parts = {(cid, oid, part["id"]): part for (cid, oid), outfit in snapshot_outfits.items() for part in outfit["parts"]}
    bound_parts = {(row["character_id"], row["outfit_id"], row["part_id"]): row for row in catalog["parts"]}
    require(len(snapshot) == 12 and len(snapshot_outfits) == 26 and len(snapshot_parts) == 112
            and len(catalog["parts"]) == len(bound_parts) == 72, "Feature Catalog snapshot has missing/duplicate selections or parts")
    cloth_bindings = {(row["character_id"], row["outfit_id"], row["part_id"]): row for row in cloth["part_bindings"]}
    require(len(cloth_bindings) == 7 and set(cloth_bindings) <= set(bound_parts), "Native cloth Catalog identities differ")
    for key, row in bound_parts.items():
        actual = snapshot_parts[key]
        require([package(value) for value in actual["mesh"]] == [row["mesh"]]
                and bool(row["cloth"]) == (key in cloth_bindings), "Feature Catalog snapshot does not match its binding report")
        if key in cloth_bindings:
            require(row["mesh"] == cloth_bindings[key]["target"] and row["mesh"] in saved_cloth,
                    "Feature Catalog points to an unverified cloth target")

    runtime = documents["Runtime/Report.json"]
    cases = runtime.get("results", [])
    expected_cases = {(key[0], key[1]) for key in cloth_bindings}
    require(runtime.get("status") == "pass" and runtime.get("planned_combinations") == runtime.get("completed_combinations") == len(cases) == RUNTIME_CASES
            and len(expected_cases) == RUNTIME_CASES and {(row["character_id"], row["outfit_id"]) for row in cases} == expected_cases
            and runtime.get("validation_settings_persisted") is False
            and runtime.get("original_user_runtime_settings_restored") is True, "All6exact cloth outfit runtime cases must pass")
    for key, expected in (("validated_catalog_sha256", catalog_hash), ("validated_runtime_module_sha256", runtime_hash)):
        if key in runtime:
            require(runtime[key] == expected, "Runtime report is stale: " + key)
    names = set(FIXED_FILES)
    observed_cloth_parts = set()
    for case in cases:
        identity = case["character_id"], case["outfit_id"]
        require(case.get("status") == "pass" and case.get("checks")
                and all(row.get("status") in ("pass", "not_applicable") for row in case["checks"]),
                "A runtime case has missing, failed or unknown checks")
        rendering = case["viewport_and_ui"]["rendering"]
        require(rendering.get("rhi_name") == "Metal" and rendering.get("using_null_rhi") is False
                and rendering.get("maximum_rhi_shader_platform") == "METAL_SM6"
                and rendering.get("scene_feature_level_name") == "SM6", "Runtime case did not use real MetalSM6")
        outfit_parts = snapshot_outfits[identity]["parts"]
        require(len(case["parts"]) == case.get("expected_parts") == len(outfit_parts), "Runtime part count differs from the final Catalog")
        for actual, expected in zip(case["parts"], outfit_parts):
            require(actual["id"] == expected["id"] and [actual["mesh_reference"]] == expected["mesh"],
                    "Runtime used a different Catalog mesh/part")
        expected_indices = {index for index, part in enumerate(outfit_parts) if (*identity, part["id"]) in cloth_bindings}
        observations = case.get("native_cloth", [])
        require(observations and {row["part_index"] for row in observations} == expected_indices
                and len(observations) == len(expected_indices), "Runtime native cloth evidence is empty or misses a bound part")
        for row in observations:
            require(row.get("finite") is True and row.get("frames_with_simulation_data", 0) >= 3
                    and row.get("simulated_particles", 0) > 0 and row.get("moving_frames", 0) >= 2
                    and math.isfinite(row.get("max_particle_step_cm", math.inf)), "Runtime native cloth particles did not advance finitely")
            require(row.get("solver_cloths_max", 0) > 0 and row.get("kinematic_particles_max", 0) > 0
                    and row.get("dynamic_particles_max", 0) > 0
                    and any("ChaosCloth" in value for value in row.get("solver_factories", []))
                    and row.get("pin_binding_comparable") is True and row.get("pin_frames", 0) >= 3
                    and row.get("source_zero_distance_pins_checked", 0) > 0
                    and math.isfinite(row.get("source_zero_pin_error_max_cm", math.inf))
                    and row["source_zero_pin_error_max_cm"] <= row.get("source_zero_pin_tolerance_cm", -1)
                    and row.get("deformation_within_conservative_safety_bound") is True,
                    "Runtime cloth did not verify its actual Chaos solver, fixed particles and source pins")
            observed_cloth_parts.add((*identity, outfit_parts[row["part_index"]]["id"]))
        screenshot_name = Path(case["screenshot"]).name
        screenshot_relative = WORK + "/Runtime/" + screenshot_name
        require(archive_name_allowed(screenshot_relative), "Unexpected runtime screenshot filename")
        screenshot = local(root, screenshot_relative, MAX_MEMBER_BYTES)
        width, height = png_size(screenshot)
        require(width == case.get("screenshot_width") and height == case.get("screenshot_height")
                and screenshot.stat().st_size == case.get("screenshot_bytes"), "Copied runtime screenshot differs from its report")
        names.add(screenshot_relative)
    require(observed_cloth_parts == set(cloth_bindings) and len(observed_cloth_parts) == RUNTIME_CLOTH_PARTS
            and len(names - FIXED_FILES) == RUNTIME_CASES, "Runtime did not observe all7native cloth parts across6screenshots")

    audit = documents["ContentAudit.json"]
    require(audit["missing_packages"] == [] and audit["unknown_dependencies"] == [] and audit["unused"] == []
            and audit.get("unused_bytes") == 0 and audit.get("project_packages", 0) > 0
            and audit["reachable_packages"] == audit["project_packages"], "Dependency audit has missing/unknown/unused packages")
    actual_package_count = sum(path.suffix in (".uasset", ".umap") and path.is_file() for path in (root / "Content").rglob("*"))
    require(audit["project_packages"] == actual_package_count, "Dependency audit does not cover the current Content package set")

    ios = documents["iOS/BuildIOSCompileVerification.json"]
    require(ios.get("status") == "passed_unsigned_compile_link_only" and ios.get("exit_code") == 0
            and ios.get("binary", {}).get("verified_ios_arm64_executable") is True
            and all(ios.get(key) is False for key in ("xcode_signing_finalization_observed", "signing_performed",
                "asset_cooking_performed", "installable_ipa_produced", "device_deployment_performed")),
            "iOS evidence must be unsigned compile/link only, without cook/sign/install claims")
    ios_binary = local(root, ios["binary"]["path"])
    require(ios_binary.stat().st_size == ios["binary"]["size_bytes"]
            and current_hash(ios_binary) == ios["binary"]["sha256"]
            and ios["log_sha256"] == current_hash(WORK + "/iOS/BuildIOSCompile.log"),
            "iOS binary or copied build log changed after verification")
    current_runtime_sources = {path.relative_to(root).as_posix() for path in (root / "Source").rglob("*")
                               if path.is_file() and path.suffix in (".h", ".cpp", ".cs")}
    require(set(ios["source_sha256"]) == current_runtime_sources, "Runtime C++ source set changed after iOS compilation")
    for name, expected in ios["source_sha256"].items():
        require(current_hash(name) == expected, "Runtime source changed after iOS compilation: " + name)
    for name in ("build_macos_verified.log", "iOS/BuildIOSCompile.log"):
        text = local(root, WORK + "/" + name).read_text(encoding="utf-8", errors="replace")
        require("Result: Succeeded" in text and "Result: Failed" not in text, "Final build log did not pass: " + name)

    summary = documents["validation_summary.json"]
    require(summary.get("status") == "passed" and summary.get("catalog_sha256") == catalog_hash
            and summary.get("runtime_module_sha256") == runtime_hash
            and summary.get("protected_assets_verified") == 6918
            and summary.get("new_shading_assets_verified") == 66 and summary.get("new_cloth_assets_verified") == 7
            and summary.get("dependency_audit") == {"missing": 0, "unknown": 0, "unused": 0}
            and summary.get("mobile_runtime_validation") == "not_run" and summary.get("ios_cook_performed") is False,
            "Final summary is incomplete, stale, or overstates validation scope")
    expected_inputs = names - {WORK + "/validation_summary.json"}
    require(expected_inputs <= set(summary.get("inputs", {})), "Final summary does not fingerprint every archived report/log/PNG")
    for name, expected in summary["inputs"].items():
        require(current_hash(name) == expected, "Final summary input changed: " + name)
    for name, expected in summary.get("implementation_inputs", {}).items():
        require(name.startswith(("Source/", "Scripts/", "Plugins/", "Config/")) or name == "NZMCharacterViewer.uproject",
                "Unexpected implementation fingerprint path: " + name)
        require(current_hash(name) == expected, "Implementation changed after final verification: " + name)
    mac = summary.get("mac_build", {})
    require(mac.get("status") == "succeeded" and mac.get("log") == WORK + "/build_macos_verified.log"
            and mac.get("log_sha256") == current_hash(mac["log"]), "Final summary does not identify the verified Mac build")
    ios_summary = summary.get("ios_compile", {})
    require(ios_summary.get("status") == "passed_unsigned_compile_link_only"
            and ios_summary.get("report") == WORK + "/iOS/BuildIOSCompileVerification.json"
            and ios_summary.get("report_sha256") == current_hash(ios_summary["report"]),
            "Final summary does not identify the verified iOS compile report")

    records = [file_record(root, name) for name in sorted(names)]
    metadata = {
        "catalog_sha256": catalog_hash, "runtime_module_sha256": runtime_hash,
        "source_commit": SOURCE_COMMIT,
        "published_source_url": "https://github.com/AliYa-chen/NZMCharacterViewer/tree/" + SOURCE_COMMIT + "/PCResources",
        "source_manifest_sha256": current_hash(WORK + "/source_manifest.json"),
        "validation_summary_sha256": current_hash(WORK + "/validation_summary.json"),
        "counts": {"pc_parts": 72, "protected_packages_unchanged": 6918, "new_shading_packages": 66,
                   "verified_metal_sm6_materials": 63, "new_cloth_meshes": 7, "native_cloth_assets": 19,
                   "runtime_outfit_cases": RUNTIME_CASES, "runtime_native_cloth_parts": RUNTIME_CLOTH_PARTS,
                   "runtime_PNGs": RUNTIME_CASES,
                   "project_packages": actual_package_count},
        "new_package_fingerprints_not_archived": sorted(new_assets, key=lambda row: row["file"]),
        "mobile_runtime_validation": "not_run", "ios_validation": "unsigned_compile_link_only",
        "ios_cook_performed": False,
        "scope": "Native stock shader reconstruction and Chaos cloth integration evidence. Raw source JSON, Content packages, checkpoints, temporary drivers and upload state are excluded. Original private shader/solver equivalence, mobile runtime, iOS cooking/signing and device performance are not inferred.",
    }
    return records, metadata


def verify_archive(root, manifest, archive_path=None, check_current=False):
    root = Path(root).resolve()
    require(manifest.get("schema_version") == 1 and manifest.get("status") == "passed",
            "Feature evidence manifest has not passed")
    expected_archive = manifest["archive"]
    require(expected_archive["file"] == ARCHIVE, "Unexpected feature archive destination")
    path = Path(archive_path) if archive_path is not None else local(root, ARCHIVE)
    require(path.stat().st_size == expected_archive["bytes"] and digest(path) == expected_archive["sha256"],
            "Feature archive differs from its manifest")
    records = {row["file"]: row for row in manifest["files"]}
    require(len(records) == len(manifest["files"]) == len(FIXED_FILES) + RUNTIME_CASES
            and FIXED_FILES <= set(records) and all(archive_name_allowed(name) for name in records),
            "Feature archive has missing, duplicate or unapproved members")
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        require(len(members) == len(records) and {member.name for member in members} == set(records)
                and all(member.isfile() and not member.issym() and not member.islnk() for member in members),
                "Feature archive member set differs from its manifest")
        for member in members:
            expected = records[member.name]
            require(0 <= member.size == expected["bytes"] <= MAX_MEMBER_BYTES, "Feature archive member has an invalid size")
            stream = archive.extractfile(member)
            require(stream is not None, "Feature archive member cannot be read")
            result = hashlib.sha256()
            for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                result.update(block)
            require(result.hexdigest() == expected["sha256"], "Feature archive member checksum differs: " + member.name)
    if check_current:
        for expected in records.values():
            require(file_record(root, expected["file"]) == expected, "Evidence changed during archive publication")
    return {"status": "verified", "files": len(records), "archive_bytes": expected_archive["bytes"],
            "archive_sha256": expected_archive["sha256"]}


def publish_archive(root, records, metadata):
    root = Path(root).resolve()
    destination = root / DESTINATION
    destination.mkdir(parents=True, exist_ok=True)
    require(destination.resolve() == destination and not destination.is_symlink(), "Feature archive destination contains a symlink")
    archive_fd, archive_temporary = tempfile.mkstemp(prefix=".feature_evidence_", suffix=".tar.gz", dir=destination)
    manifest_temporary = None
    try:
        with os.fdopen(archive_fd, "wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                    for row in sorted(records, key=lambda value: value["file"]):
                        payload = local(root, row["file"], MAX_MEMBER_BYTES).read_bytes()
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
        manifest = {**metadata, "schema_version": 1, "status": "passed",
                    "archive": {"file": ARCHIVE, "bytes": temporary.stat().st_size, "sha256": digest(temporary)},
                    "files": sorted(records, key=lambda value: value["file"])}
        verify_archive(root, manifest, temporary, check_current=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=".feature_manifest_", suffix=".json",
                                         dir=destination, delete=False) as stream:
            manifest_temporary = stream.name
            json.dump(manifest, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, root / ARCHIVE)
        os.replace(manifest_temporary, root / MANIFEST)
        return manifest
    finally:
        for name in (archive_temporary, manifest_temporary):
            if name is not None and Path(name).exists():
                Path(name).unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--preflight-only", action="store_true")
    mode.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    if args.verify_only:
        result = verify_archive(root, read(local(root, MANIFEST)))
    else:
        records, metadata = preflight(root)
        if args.preflight_only:
            result = {"status": "preflight_passed", "files": len(records),
                      "uncompressed_bytes": sum(row["bytes"] for row in records),
                      "catalog_sha256": metadata["catalog_sha256"], "archive_would_be": ARCHIVE}
        else:
            result = verify_archive(root, publish_archive(root, records, metadata))
            result["manifest"] = MANIFEST
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
