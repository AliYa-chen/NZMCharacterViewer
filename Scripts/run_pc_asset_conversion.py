#!/usr/bin/env python3
"""Run the existing PC import plan in bounded, fresh UE5.8 Mac processes.

This runner never generates a plan, compiles the project, or pushes Git. Build
the Editor modules and run build_pc_asset_plan.py first. The importer owns asset
resume checks; this runner always requires a fresh successful batch report.

Examples::

    python3 Scripts/run_pc_asset_conversion.py --dry-run
    python3 Scripts/run_pc_asset_conversion.py --stage textures --batch-size 64
    python3 Scripts/run_pc_asset_conversion.py --stage meshes --start 32
    python3 Scripts/run_pc_asset_conversion.py --stage all
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BATCHES = {"textures": 64, "materials": 128, "meshes": 32,
                   "bindings": 512, "documents": 8192, "verify": 256}
ALL_STAGES = tuple(DEFAULT_BATCHES)
MIN_DISK_GIB = 15


def sha256(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".new")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def nonnegative(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be 0 or greater")
    return number


def resolve_engine(explicit=None):
    candidates = [Path(explicit).expanduser()] if explicit else []
    if not explicit and os.environ.get("UE_ROOT"):
        candidates = [Path(os.environ["UE_ROOT"]).expanduser()]
    if not candidates:
        # Same locations and order as Scripts/engine_env.sh.
        candidates = [Path.home() / "UE/UE_5.8", Path("/Users/Shared/Epic Games/UE_5.8"),
                      Path("/Users/Shared/Epic Games/UE_5.8.4")]
    for candidate in candidates:
        engine = candidate.resolve()
        version_path = engine / "Engine/Build/Build.version"
        executable = engine / "Engine/Binaries/Mac/UnrealEditor.app/Contents/MacOS/UnrealEditor"
        if not version_path.is_file() or not executable.is_file():
            continue
        version = read_json(version_path)
        if (version.get("MajorVersion"), version.get("MinorVersion")) != (5, 8):
            raise RuntimeError("UE_ROOT must identify an Unreal Engine 5.8 installation")
        if not os.access(executable, os.X_OK):
            raise RuntimeError("UnrealEditor is not executable: " + str(executable))
        return engine, executable, version
    raise RuntimeError("UE5.8 Mac Editor not found; set UE_ROOT or pass --ue-root")


def validated_plan(path):
    encoded = path.read_bytes()
    plan = json.loads(encoded.decode("utf-8-sig"))
    if plan.get("schema_version") != 1 or plan.get("status") != "inputs_verified_import_pending":
        raise ValueError("Expected a schema 1, inputs_verified_import_pending PC asset plan")
    if plan.get("target_root") != "/Game/PCAssets":
        raise ValueError("Plan target root is not the isolated PCAssets directory")
    seen = set()
    for stage in ("textures", "materials", "meshes", "documents"):
        rows = plan.get(stage)
        if not isinstance(rows, list):
            raise ValueError("Missing plan array: " + stage)
        if plan.get("counts", {}).get(stage) != len(rows):
            raise ValueError("Plan count disagrees with array: " + stage)
        for row in rows:
            target = row.get("target", "")
            if not re.fullmatch(r"/Game/PCAssets/(Textures|Materials|Meshes|Documents)/[0-9a-f]{2}/[A-Za-z0-9_]+", target):
                raise ValueError("Invalid target in plan: " + str(target))
            if target in seen:
                raise ValueError("Duplicate target in plan: " + target)
            seen.add(target)
            if not re.fullmatch("[0-9a-f]{64}", row.get("sha256", "")):
                raise ValueError("Invalid source hash in plan: " + target)
    return plan, hashlib.sha256(encoded).hexdigest()


def material_order(rows):
    """Match import_pc_assets.py's stable parent-before-child stage ordering."""
    indexed = {row["target"]: row for row in rows}
    ordered, active, seen = [], set(), set()

    def visit(row):
        key = row["target"]
        if key in seen:
            return
        if key in active:
            raise ValueError("Material parent cycle: " + key)
        active.add(key)
        parent = row.get("parent_target")
        if parent:
            if parent not in indexed:
                raise ValueError("Material parent outside plan: " + parent)
            visit(indexed[parent])
        active.remove(key)
        seen.add(key)
        ordered.append(row)

    for row in rows:
        visit(row)
    return ordered


def stage_rows(plan, stage):
    if stage == "materials":
        return material_order(plan["materials"])
    if stage == "bindings":
        return plan["meshes"]
    if stage == "verify":
        return [row for key in ("textures", "materials", "meshes", "documents") for row in plan[key]]
    return plan[stage]


def memory_snapshot():
    """Record Mac pressure and available page estimate without changing VM state."""
    result = {"status": "not_available"}
    if sys.platform != "darwin":
        return result
    for key, argument in (("pressure_level", "kern.memorystatus_vm_pressure_level"),
                          ("physical_bytes", "hw.memsize")):
        process = subprocess.run(["/usr/sbin/sysctl", "-n", argument], capture_output=True,
                                 text=True, timeout=5, check=False)
        if process.returncode == 0 and process.stdout.strip().isdigit():
            result[key] = int(process.stdout.strip())
    process = subprocess.run(["/usr/bin/vm_stat"], capture_output=True, text=True, timeout=5, check=False)
    if process.returncode == 0:
        page_size = re.search(r"page size of (\d+) bytes", process.stdout)
        pages = {key.strip(): int(value) for key, value in re.findall(r"^([^:\n]+):\s*(\d+)\.", process.stdout, re.M)}
        if page_size:
            result["free_inactive_speculative_bytes"] = int(page_size.group(1)) * sum(
                pages.get(key, 0) for key in ("Pages free", "Pages inactive", "Pages speculative"))
            result["estimate_scope"] = "free + inactive + speculative pages; excludes reclaimable compression and caches"
    if len(result) > 1:
        result["status"] = "sampled"
    return result


def resources(root):
    free = shutil.disk_usage(root).free
    memory = memory_snapshot()
    if free < MIN_DISK_GIB * 1024 ** 3:
        raise RuntimeError(f"Only {free/1024**3:.1f} GiB free; at least {MIN_DISK_GIB} GiB is required between batches")
    # Darwin pressure flags: 1=normal, 2=warning, 4=critical. A fresh process
    # releases the previous batch; do not infer exhaustion from a low free-page
    # count while the OS can reclaim inactive/compressed memory.
    if memory.get("pressure_level", 0) & 4:
        raise RuntimeError("Mac memory pressure is critical; no next Editor batch was started")
    return {"disk_free_bytes": free, "memory": memory}


def assert_unchanged(path, expected, importer, importer_sha, supporting_scripts=None):
    if sha256(path) != expected:
        raise RuntimeError("asset_plan.json changed during this run; stopped before continuing with a different plan")
    if sha256(importer) != importer_sha:
        raise RuntimeError("import_pc_assets.py changed during this run; stopped before mixing importer versions")
    for script, expected_sha in (supporting_scripts or {}).items():
        current_sha = sha256(script) if script.is_file() else None
        if current_sha != expected_sha:
            raise RuntimeError(script.name + " changed during this run; stopped before mixing material adapter versions")


def command(executable, project, importer, stage, start, count, engine_log):
    return [str(executable), str(project), "-run=pythonscript", "-script=" + str(importer),
            "-nullrhi", "-unattended", "-nosplash", "-NoSound", "-stdout", "-FullStdOutLogOutput",
        "-NoEnginePlugins", "-EnablePlugins=PythonScriptPlugin,EditorScriptingUtilities,PCAssetImporter,ACLPlugin,ControlRig,RigVM",
            "-abslog=" + str(engine_log), "-PCImportStage=" + stage,
            "-PCImportStart=" + str(start), "-PCImportCount=" + str(count)]


def run_process(args, root, console_log):
    with console_log.open("wb") as stream:
        process = subprocess.Popen(args, cwd=root, stdin=subprocess.DEVNULL,
                                   stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            return process.wait()
        except BaseException:
            # A cancelled runner must not leave a detached Editor modifying
            # assets while the caller begins another conversion process.
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
            raise


def check_batch_report(path, stage, start, count, expected_targets):
    if not path.is_file():
        raise RuntimeError("Editor did not create its batch report: " + str(path))
    report = read_json(path)
    if report.get("status") != "passed" or report.get("failed"):
        raise RuntimeError("Editor batch report did not pass: " + str(path))
    if (report.get("stage"), report.get("start"), report.get("count")) != (stage, start, count):
        raise RuntimeError("Editor batch report does not match the requested range: " + str(path))
    completed = report.get("completed", [])
    actual = [row.get("target") for row in completed]
    if len(actual) != count or set(actual) != set(expected_targets):
        raise RuntimeError("Editor batch report did not cover every requested target exactly once: " + str(path))
    if any(row.get("status") not in ("passed", "resumed") for row in completed):
        raise RuntimeError("Editor batch contains an incomplete result: " + str(path))
    return {"passed": sum(row["status"] == "passed" for row in completed),
            "resumed": sum(row["status"] == "resumed" for row in completed)}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", choices=("all",) + ALL_STAGES, default="all")
    parser.add_argument("--start", type=nonnegative, default=0, help="Zero-based index for a single stage")
    parser.add_argument("--batch-size", type=positive, help="Override the per-stage default for this run")
    parser.add_argument("--ue-root", type=Path, help="Engine installation; otherwise UE_ROOT / engine_env.sh locations")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--dry-run", action="store_true", help="Validate and print the batch plan without launching Unreal")
    args = parser.parse_args()
    root = args.root.resolve()
    if args.stage == "all" and args.start:
        parser.error("--start is supported only with a single --stage; all always begins each stage at 0")
    work = root / "Saved/PCAssetConversion"
    plan_path = work / "asset_plan.json"
    plan, plan_sha = validated_plan(plan_path)
    importer = root / "Scripts/import_pc_assets.py"
    project = root / "NZMCharacterViewer.uproject"
    if not importer.is_file() or not project.is_file():
        raise RuntimeError("Importer or project file is missing")
    importer_sha = sha256(importer)
    stages = ALL_STAGES if args.stage == "all" else (args.stage,)
    # Only the material stage imports the adapter. Other stages remain
    # independent while that adapter is being developed or inspected.
    supporting_scripts = {}
    for filename in (("PC_material_adapter.py", "pc_material_adapter.py") if "materials" in stages else ()):
        helper = root / "Scripts" / filename
        supporting_scripts[helper] = sha256(helper) if helper.is_file() else None
    _, executable, version = resolve_engine(args.ue_root)
    batch_plan = []
    totals = []
    for stage in stages:
        rows = stage_rows(plan, stage)
        if args.start > len(rows):
            parser.error(f"--start {args.start} exceeds {stage} count {len(rows)}")
        batch_size = args.batch_size or DEFAULT_BATCHES[stage]
        batches = list(range(args.start, len(rows), batch_size))
        totals.append({"stage": stage, "items": len(rows) - args.start,
                       "start": args.start, "batch_size": batch_size, "batches": len(batches)})
        for start in batches:
            count = min(batch_size, len(rows) - start)
            batch_plan.append({"stage": stage, "start": start, "count": count,
                               "targets": [row["target"] for row in rows[start:start + count]]})
    capacity = resources(root)
    print(f"Plan {plan_sha[:12]} · UE {version['MajorVersion']}.{version['MinorVersion']}.{version['PatchVersion']} · "
          f"free disk {capacity['disk_free_bytes']/1024**3:.1f} GiB", flush=True)
    for row in totals:
        print(f"{row['stage']}: {row['items']} items, {row['batches']} batches × up to {row['batch_size']} "
              f"(start {row['start']})", flush=True)
    if args.dry_run:
        print(f"DRY_RUN {len(batch_plan)} Editor processes; no Unreal process started", flush=True)
        return
    if sys.platform != "darwin":
        raise RuntimeError("This runner uses the Mac Editor executable; run it on macOS")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    logs = work / "Logs"
    logs.mkdir(parents=True, exist_ok=True)
    run_report = {"schema_version": 1, "run_id": run_id, "status": "running",
                  "plan_sha256": plan_sha, "importer_sha256": importer_sha,
                  "supporting_script_sha256": {str(path.relative_to(root)): value
                                              for path, value in supporting_scripts.items()},
                  "engine_version": version, "stages": totals, "completed": []}
    run_path = logs / ("run_" + run_id + ".json")
    write_json(run_path, run_report)
    try:
        for index, batch in enumerate(batch_plan):
            assert_unchanged(plan_path, plan_sha, importer, importer_sha, supporting_scripts)
            capacity = resources(root)
            stage, start, count = batch["stage"], batch["start"], batch["count"]
            batch_report = work / "Batches" / f"{stage}_{start:05d}.json"
            stem = f"{run_id}_{stage}_{start:05d}"
            console_log = logs / (stem + ".console.log")
            engine_log = logs / (stem + ".engine.log")
            # Move the old report away so a crash before Python starts cannot
            # be mistaken for an earlier successful run of this range.
            if batch_report.is_file():
                batch_report.replace(logs / (stem + ".previous_batch.json"))
            print(f"RUN {index+1}/{len(batch_plan)} {stage} [{start}, {start+count})", flush=True)
            started = time.monotonic()
            exit_code = run_process(command(executable, project, importer, stage, start, count, engine_log), root, console_log)
            assert_unchanged(plan_path, plan_sha, importer, importer_sha, supporting_scripts)
            outcome = check_batch_report(batch_report, stage, start, count, batch["targets"])
            if exit_code != 0:
                raise RuntimeError(f"Editor returned {exit_code} despite its passed report; inspect {console_log}")
            result = {"stage": stage, "start": start, "count": count, "exit_code": exit_code,
                      "seconds": round(time.monotonic()-started, 2), "resources_before": capacity,
                      "console_log": str(console_log.relative_to(root)),
                      "engine_log": str(engine_log.relative_to(root)), **outcome}
            run_report["completed"].append(result)
            write_json(run_path, run_report)
            print(f"PASSED {stage} [{start}, {start+count}) ({result['seconds']:.1f}s, "
                  f"{outcome['passed']} imported/verified, {outcome['resumed']} resumed)", flush=True)
        run_report["status"] = "passed"
    except BaseException as error:
        run_report["status"] = "interrupted" if isinstance(error, KeyboardInterrupt) else "failed"
        run_report["error"] = str(error)
        raise
    finally:
        write_json(run_path, run_report)
    print(f"ALL_REQUESTED_BATCHES_PASSED {run_path.relative_to(root)}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        print("PC_ASSET_CONVERSION_STOPPED: " + str(error), file=sys.stderr)
        raise SystemExit(1)
