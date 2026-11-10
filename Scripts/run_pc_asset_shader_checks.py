#!/usr/bin/env python3
"""Verify frozen PC material assets on real Metal in resumable Editor batches.

The existing asset plan and saved material files are inputs. Each batch runs
verify_pc_asset_shaders.py with commandlet rendering enabled. No timeout is
imposed on shader compilation. Existing reports are reused only after their
coverage, shader flags and current on-disk asset hashes are checked again.

Examples::

    python3 Scripts/run_pc_asset_shader_checks.py --dry-run
    python3 Scripts/run_pc_asset_shader_checks.py
    python3 Scripts/run_pc_asset_shader_checks.py --start 64 --batch-size 64
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time

from run_pc_asset_conversion import (nonnegative, positive, read_json, resources,
                                     resolve_engine, run_process, sha256,
                                     validated_plan, write_json)


ROOT = Path(__file__).resolve().parent.parent
FLAGS = ("shader_map_present", "platform_matches_active_rhi", "compilation_finalized",
         "compiled_successfully", "required_shader_set_complete", "valid_for_rendering")
PLUGINS = "PythonScriptPlugin,EditorScriptingUtilities,PCAssetImporter,ACLPlugin,ControlRig,RigVM"


def frozen_snapshot(root, plan_path):
    required = [plan_path, Path(__file__).resolve(), root / "Scripts/verify_pc_asset_shaders.py",
                root / "Scripts/run_pc_asset_conversion.py",
                root / "Plugins/PCAssetImporter/Binaries/Mac/libUnrealEditor-PCAssetImporter.dylib",
                root / "Plugins/PCAssetImporter/Binaries/Mac/libUnrealEditor-PCAssetData.dylib",
                root / "Plugins/PCAssetImporter/Binaries/Mac/UnrealEditor.modules"]
    result = {}
    for path in required:
        if not path.is_file():
            raise RuntimeError("Required frozen shader-check input is missing: " + str(path))
        result[path.resolve()] = sha256(path)
    return result


def assert_frozen(snapshot):
    for path, expected in snapshot.items():
        if not path.is_file() or sha256(path) != expected:
            raise RuntimeError("Frozen plan, shader-check script or native plugin changed during the run: " + str(path))


def material_file(root, target):
    if not re.fullmatch(r"/Game/PCAssets/Materials/[0-9a-f]{2}/[A-Za-z0-9_]+", target):
        raise ValueError("Shader report target is outside planned materials: " + str(target))
    return root / "Content" / (target[len("/Game/"):] + ".uasset")


def package_from_object_path(value):
    if not isinstance(value, str):
        return None
    package = value.split(".", 1)[0]
    return package if re.fullmatch(r"/Game/PCAssets/Materials/[0-9a-f]{2}/[A-Za-z0-9_]+", package) else None


def validate_report(path, root, materials, plan_sha, expected_platform, requested_start=None, requested_count=None):
    report = read_json(path)
    if report.get("schema_version") != 1:
        raise ValueError("Unexpected shader report schema")
    if report.get("status") != "passed" or report.get("success") is not True:
        raise ValueError("Shader report did not pass")
    if report.get("rhi") != "Metal" or report.get("using_null_rhi") is not False:
        raise ValueError("Shader report did not use actual Metal")
    if report.get("shader_platform") != expected_platform:
        raise ValueError("Active shader platform does not match the requested platform")
    if report.get("shader_format") != "SF_" + expected_platform:
        raise ValueError("Reported shader format does not match the active platform")
    expected_feature = "SM6" if expected_platform.endswith("SM6") else "SM5" if expected_platform.endswith("SM5") else None
    if expected_feature and report.get("feature_level") != expected_feature:
        raise ValueError("Feature level does not match the active shader platform")
    if report.get("plan_sha256") != plan_sha:
        raise ValueError("Shader report was generated for a different asset plan")
    if report.get("content_saved") is not False or report.get("asset_files_unchanged") is not True:
        raise ValueError("Read-only shader verification did not preserve the asset files")
    start, count = report.get("start"), report.get("count")
    if not isinstance(start, int) or isinstance(start, bool) or not isinstance(count, int) or isinstance(count, bool):
        raise ValueError("Shader report has invalid range metadata")
    if start < 0 or count < 1 or start + count > len(materials):
        raise ValueError("Shader report range is outside the material plan")
    if requested_start is not None and (start, count) != (requested_start, requested_count):
        raise ValueError("Existing shader report does not cover the requested batch range")
    if path.name != f"materials_{start:05d}.json":
        raise ValueError("Shader report filename and range disagree")
    for key, expected in (("requested_count", count), ("checked_count", count),
                          ("passed_count", count), ("failed_count", 0), ("shader_jobs_after", 0)):
        if report.get(key) != expected:
            raise ValueError("Shader report count/jobs mismatch: " + key)
    expected_targets = [row["target"] for row in materials[start:start + count]]
    expected_set = set(expected_targets)
    rows = report.get("materials")
    if not isinstance(rows, list) or len(rows) != count:
        raise ValueError("Shader report does not contain every requested material")
    actual = [row.get("package") for row in rows]
    if len(set(actual)) != count or set(actual) != expected_set:
        raise ValueError("Shader report material targets differ from its planned range")
    before, after = report.get("asset_sha256_before"), report.get("asset_sha256_after")
    if not isinstance(before, dict) or not isinstance(after, dict) or set(before) != expected_set or set(after) != expected_set:
        raise ValueError("Shader report does not include exact before/after file coverage")
    for row in rows:
        target = row["package"]
        if row.get("status") != "passed" or any(row.get(flag) is not True for flag in FLAGS):
            raise ValueError("Material shader-map flag failed: " + target)
        if row.get("compile_errors") != [] or "error" in row:
            raise ValueError("Material has shader compiler errors: " + target)
        shader_count = row.get("shader_count")
        if not isinstance(shader_count, (int, float)) or isinstance(shader_count, bool) or shader_count <= 0:
            raise ValueError("Material shader map is empty: " + target)
        if not package_from_object_path(row.get("resource_owner")) or not package_from_object_path(row.get("base_material")):
            raise ValueError("Material resolves to a missing or external fallback resource: " + target)
        if not re.fullmatch("[0-9a-f]{64}", str(before[target])) or before[target] != after[target]:
            raise ValueError("Material file changed during shader verification: " + target)
        saved = material_file(root, target)
        if not saved.is_file() or sha256(saved) != before[target]:
            raise ValueError("Material file changed after its shader report: " + target)
    return {"file": str(path.relative_to(root)), "report_sha256": sha256(path), "start": start, "count": count,
            "targets": expected_targets, "rhi": report["rhi"], "shader_platform": report["shader_platform"],
            "shader_format": report["shader_format"], "feature_level": report["feature_level"],
            "elapsed_seconds": report.get("elapsed_seconds"),
            "validated_asset_sha256": before}


def report_or_reason(path, *args, **kwargs):
    if not path.is_file():
        return None, "report missing"
    try:
        return validate_report(path, *args, **kwargs), None
    except (OSError, ValueError, RuntimeError, TypeError, KeyError) as error:
        return None, str(error)


def summarize(root, work, materials, plan_sha, expected_platform, snapshot, run_id, requested_complete):
    reports, rejected, covered = [], [], set()
    for path in sorted((work / "Shaders").glob("materials_*.json")):
        valid, reason = report_or_reason(path, root, materials, plan_sha, expected_platform)
        if valid:
            reports.append(valid)
            covered.update(valid["targets"])
        else:
            rejected.append({"file": str(path.relative_to(root)), "reason": reason})
    expected = {row["target"] for row in materials}
    complete = covered == expected
    summary = {"schema_version": 1, "status": "passed" if complete else "partial",
               "recorded_utc": datetime.now(timezone.utc).isoformat(), "run_id": run_id,
               "plan_sha256": plan_sha, "rhi": "Metal", "shader_platform": expected_platform,
               "expected_materials": len(expected), "validated_materials": len(covered),
               "missing_targets": sorted(expected-covered), "valid_reports": reports, "rejected_reports": rejected,
               "frozen_execution_inputs": {str(path.relative_to(root)): value for path, value in snapshot.items()},
               "requested_range_completed": requested_complete,
               "asset_files_unchanged": complete,
               "validation_scope": "Every planned material has a current saved-asset hash, complete successful active-Metal shader map, no compile errors, and an exact passing batch report.",
               "limitations": ["Cached/compiled shader maps do not establish a rendered image, PSO execution, original shader equivalence, animation or iOS/device performance.",
                               "Existing reports are reused after all report flags and current file hashes pass validation; their native producer-binary hashes were not recorded by the commandlet report."]}
    write_json(work / "shader_summary.json", summary)
    return summary


def command(executable, project, checker, start, count, engine_log):
    return [str(executable), str(project), "-run=pythonscript", "-script=" + str(checker),
            "-AllowCommandletRendering", "-unattended", "-nosplash", "-NoSound", "-stdout", "-FullStdOutLogOutput",
            "-NoEnginePlugins", "-EnablePlugins=" + PLUGINS, "-abslog=" + str(engine_log),
            "-PCShaderStart=" + str(start), "-PCShaderCount=" + str(count)]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", type=nonnegative, default=0)
    parser.add_argument("--batch-size", type=positive, default=64)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--ue-root", type=Path)
    parser.add_argument("--shader-platform", default="METAL_SM6", help="Require this exact active Metal platform (default METAL_SM6)")
    args = parser.parse_args()
    root = args.root.resolve()
    if not re.fullmatch(r"METAL_[A-Z0-9_]+", args.shader_platform):
        parser.error("--shader-platform must be a named Metal shader platform")
    work = root / "Saved/PCAssetConversion"
    plan_path = work / "asset_plan.json"
    plan, plan_sha = validated_plan(plan_path)
    materials = plan["materials"]
    if args.start > len(materials):
        parser.error("--start exceeds the material count")
    if not materials:
        raise RuntimeError("The frozen plan contains no material shaders to check")
    _, executable, version = resolve_engine(args.ue_root)
    snapshot = frozen_snapshot(root, plan_path)
    capacity = resources(root)
    batches = [(start, min(args.batch_size, len(materials)-start)) for start in range(args.start, len(materials), args.batch_size)]
    reusable = {}
    for start, count in batches:
        path = work / "Shaders" / f"materials_{start:05d}.json"
        report, _ = report_or_reason(path, root, materials, plan_sha, args.shader_platform, start, count)
        if report:
            reusable[start] = report
    print(f"Frozen plan {plan_sha[:12]} · UE {version['MajorVersion']}.{version['MinorVersion']}.{version['PatchVersion']} · "
          f"{len(materials)} materials · {args.shader_platform} · free disk {capacity['disk_free_bytes']/1024**3:.1f} GiB", flush=True)
    print(f"Range [{args.start}, {len(materials)}): {len(batches)} batches × up to {args.batch_size}; "
          f"{len(reusable)} reports currently reusable", flush=True)
    if args.dry_run:
        print(f"DRY_RUN {len(batches)-len(reusable)} fresh Metal Editor processes; no Unreal process started", flush=True)
        return
    if sys.platform != "darwin":
        raise RuntimeError("Real Metal shader checks require macOS")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    logs = work / "Logs"
    logs.mkdir(parents=True, exist_ok=True)
    checker = root / "Scripts/verify_pc_asset_shaders.py"
    project = root / "NZMCharacterViewer.uproject"
    if not project.is_file():
        raise RuntimeError("The Unreal project file is missing")
    run_report = {"schema_version": 1, "run_id": run_id, "status": "running", "plan_sha256": plan_sha,
                  "shader_platform": args.shader_platform, "start": args.start, "batch_size": args.batch_size,
                  "frozen_execution_inputs": {str(path.relative_to(root)): value for path, value in snapshot.items()},
                  "completed": []}
    run_path = logs / ("shaders_run_" + run_id + ".json")
    write_json(run_path, run_report)
    try:
        for index, (start, count) in enumerate(batches):
            assert_frozen(snapshot)
            path = work / "Shaders" / f"materials_{start:05d}.json"
            report, reason = report_or_reason(path, root, materials, plan_sha, args.shader_platform, start, count)
            if report:
                run_report["completed"].append({"start": start, "count": count, "status": "reused", "report": report["file"]})
                write_json(run_path, run_report)
                print(f"REUSED {index+1}/{len(batches)} materials [{start}, {start+count})", flush=True)
                continue
            capacity = resources(root)
            stem = f"{run_id}_shaders_{start:05d}"
            if path.is_file():
                path.replace(logs / (stem + ".previous_report.json"))
            console_log, engine_log = logs / (stem + ".console.log"), logs / (stem + ".engine.log")
            print(f"RUN {index+1}/{len(batches)} Metal materials [{start}, {start+count})", flush=True)
            started = time.monotonic()
            # run_process has no compilation timeout and terminates the entire
            # child process group on Ctrl-C or another interruption.
            exit_code = run_process(command(executable, project, checker, start, count, engine_log), root, console_log)
            assert_frozen(snapshot)
            result = validate_report(path, root, materials, plan_sha, args.shader_platform, start, count)
            if exit_code != 0:
                raise RuntimeError(f"Shader Editor returned {exit_code}; inspect {console_log}")
            run_report["completed"].append({"start": start, "count": count, "status": "passed",
                "seconds": round(time.monotonic()-started, 2), "resources_before": capacity,
                "report": result["file"], "console_log": str(console_log.relative_to(root)),
                "engine_log": str(engine_log.relative_to(root)), "previous_report_rejection": reason})
            write_json(run_path, run_report)
            summary = summarize(root, work, materials, plan_sha, args.shader_platform, snapshot, run_id, False)
            print(f"PASSED materials [{start}, {start+count}); total current shader coverage "
                  f"{summary['validated_materials']}/{len(materials)}", flush=True)
        assert_frozen(snapshot)
        summary = summarize(root, work, materials, plan_sha, args.shader_platform, snapshot, run_id, True)
        run_report["status"] = "passed_requested_range"
        run_report["summary_status"] = summary["status"]
        print(f"SHADER_SUMMARY {summary['status']} {summary['validated_materials']}/{len(materials)} · "
              "Saved/PCAssetConversion/shader_summary.json", flush=True)
    except BaseException as error:
        run_report["status"] = "interrupted" if isinstance(error, KeyboardInterrupt) else "failed"
        run_report["error"] = str(error)
        # A failed run must not leave a stale global 'passed' summary. Rebuild
        # coverage when the execution snapshot is still valid; otherwise mark
        # the prior evidence stale without validating under changed code.
        try:
            assert_frozen(snapshot)
            summary = summarize(root, work, materials, plan_sha, args.shader_platform, snapshot, run_id, False)
            summary["run_error"] = str(error)
            if summary["status"] == "passed":
                summary["status"] = "failed_run_with_complete_previous_evidence"
            write_json(work / "shader_summary.json", summary)
        except (OSError, ValueError, RuntimeError):
            write_json(work / "shader_summary.json", {"schema_version": 1, "status": "stale_execution_inputs",
                "plan_sha256": plan_sha, "error": str(error), "expected_materials": len(materials)})
        raise
    finally:
        write_json(run_path, run_report)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        print("PC_SHADER_CHECKS_STOPPED: " + str(error), file=sys.stderr)
        raise SystemExit(1)
