"""Adapt existing viewer idle motion to the planned PC mesh skeletons.

Run inside UE5.8 after compiling PCAssetImporter. -PCAnimationStart and
-PCAnimationCount bound a fresh-process batch. --plan-only works in ordinary
Python and writes the deterministic source-animation job list without UE.

New characters use an explicitly labeled shared standing idle; no missing
original character-specific animation is claimed as recovered.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import traceback


ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "Saved/PCViewerIntegration"
PLAN_PATH = WORK / "viewer_plan.json"
BASELINE_PATH = WORK / "baseline_catalog.json"
REPORT_PATH = WORK / "animation_report.json"
JOBS_PATH = WORK / "animation_jobs.json"
VERSION = 1
MODEL_TO_BASELINE = {
    "10002000008": ("angel", "angel"), "10002000053": ("angel", "angellucia"),
    "10002000046": ("angela", "angela"), "10002000028": ("molly", "molly"),
    "10002000054": ("molly", "mollynanami"), "10002000045": ("naimei", "suit_a"),
    "10002000055": ("naimei", "naimeibianca"), "10002000044": ("queen", "queen"),
    "10002010044": ("queen", "queen_masked"),
}
NAIMEI_SEMANTIC_PARTS = {
    "EAvatarSlot::Body": "body", "EAvatarSlot::Head": "head",
    "EAvatarSlot::Hair": "hair", "EAvatarSlot::Bag": "cloak",
}
# These source meshes have local accessory skeletons. Their motion comes from
# the parent component/socket integration, rather than a humanoid idle clip.
SOCKET_ACCESSORIES = {"10056010007", "10054010006", "10054010007"}


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".new")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def package(reference):
    if isinstance(reference, list):
        reference = reference[0] if reference else None
    return reference.split(".", 1)[0] if reference else None


def baseline_body(outfit):
    candidates = [part for part in outfit["parts"] if part.get("animation") and
                  ("身体" in part.get("name", "") or part["id"] in ("body", "10801040001"))]
    if len(candidates) != 1:
        raise ValueError("Baseline outfit has no unambiguous body motion: " + outfit["id"])
    return candidates[0]


def build_jobs(plan, baseline):
    outfits = {(character["id"], outfit["id"]): outfit
               for character in baseline["characters"] for outfit in character["outfits"]}
    default_source = baseline_body(outfits["angel", "angel"])
    jobs = []
    for character in plan["characters"]:
        for outfit in character["outfits"]:
            identity = MODEL_TO_BASELINE.get(str(outfit["model_id"]))
            source_outfit = outfits.get(identity) if identity else None
            if source_outfit is None:
                family_candidates = [(key, value) for key, value in outfits.items()
                                     if key[0] == character["family"].lower()]
                if len(family_candidates) == 1:
                    identity, source_outfit = family_candidates[0]
                    source_kind = "same_character_idle"
                else:
                    source_kind = "shared_idle"
            else:
                source_kind = "same_outfit_idle"
            for part in outfit["parts"]:
                chosen = None
                selection = "shared_standing_body_idle"
                if source_outfit:
                    original_id = str(part["source_part_id"])
                    if identity == ("naimei", "suit_a"):
                        original_id = NAIMEI_SEMANTIC_PARTS.get(part.get("slot"), original_id)
                    exact = [row for row in source_outfit["parts"] if row["id"] == original_id and row.get("animation")]
                    if len(exact) == 1:
                        chosen = exact[0]
                        selection = "same_outfit_existing_part_idle"
                    else:
                        chosen = baseline_body(source_outfit)
                        selection = "same_outfit_body_motion_to_other_part_bind_basis"
                if chosen is None:
                    chosen = default_source
                source = package(chosen["animation"])
                mesh = part.get("mesh_target", part.get("mesh"))
                if not mesh or not source or not mesh.startswith("/Game/PCAssets/Meshes/") or not source.startswith("/Game/Characters/"):
                    raise ValueError("Invalid source/target animation job")
                # A repeated mesh/source pair (e.g. Queen unmasked/masked body)
                # uses exactly one asset and one independently verified clip.
                key = hashlib.sha256((source + "\n" + mesh).encode()).hexdigest()[:14]
                name = re.sub("[^A-Za-z0-9_]", "_", character["family"] + "_" + str(part["source_part_id"]))
                target = f"/Game/PCAssets/Animations/{key[:2]}/A_PC_{name}_{key}"
                jobs.append({
                    "character_id": character["id"], "family": character["family"],
                    "outfit_id": outfit["id"], "model_id": str(outfit["model_id"]),
                    "part_id": part["id"], "source_part_id": str(part["source_part_id"]),
                    "source_mesh_package": part["source_package"], "mesh": mesh,
                    "source_animation": source, "source_kind": source_kind,
                    "source_selection": selection, "animation": target,
                    "static_socket_attachment": str(part["source_part_id"]) in SOCKET_ACCESSORIES,
                })
    if len({(row["character_id"], row["outfit_id"], row["part_id"]) for row in jobs}) != len(jobs):
        raise ValueError("Animation jobs have duplicate part identities")
    return jobs


def option(command_line, name, default):
    matches = re.findall(r"(?:^|\s)-" + name + r"=(\d+)(?=\s|$)", command_line)
    return int(matches[-1]) if matches else default


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-only", action="store_true")
    args, _ = parser.parse_known_args()
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8-sig"))
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8-sig"))
    plan_sha, baseline_sha = digest(PLAN_PATH), digest(BASELINE_PATH)
    jobs = build_jobs(plan, baseline)
    job_document = {"schema_version": VERSION, "plan_sha256": plan_sha,
                    "baseline_catalog_sha256": baseline_sha, "jobs": jobs}
    write_json(JOBS_PATH, job_document)
    if args.plan_only:
        print(json.dumps({"parts": len(jobs), "unique_animation_jobs": len({row["animation"] for row in jobs}),
                          "static_socket_parts": sum(row["static_socket_attachment"] for row in jobs),
                          "source_kind_counts": {kind: sum(row["source_kind"] == kind for row in jobs)
                                                 for kind in ("same_outfit_idle", "same_character_idle", "shared_idle")}}, indent=2))
        return
    import unreal
    if not hasattr(unreal, "PCViewerAnimationLibrary"):
        raise RuntimeError("Compile PCAssetImporter before adapting viewer animations")
    command_line = unreal.SystemLibrary.get_command_line()
    start, count = option(command_line, "PCAnimationStart", 0), option(command_line, "PCAnimationCount", len(jobs))
    if start >= len(jobs) or count < 1:
        raise ValueError("Animation batch is empty")
    previous = json.loads(REPORT_PATH.read_text(encoding="utf-8")) if REPORT_PATH.exists() else {}
    if previous and (previous.get("plan_sha256") != plan_sha or previous.get("baseline_catalog_sha256") != baseline_sha):
        raise RuntimeError("Existing animation report uses another source plan; archive it before rebuilding")
    records = {(row["character_id"], row["outfit_id"], row["part_id"]): row for row in previous.get("parts", [])}
    report = {"schema_version": VERSION, "status": "running", "plan_sha256": plan_sha,
              "baseline_catalog_sha256": baseline_sha, "total_parts": len(jobs), "parts": [],
              "limitations": [
                  "Existing viewer bone motion is adapted to new mesh bind bases; missing original character-specific animation is not recovered.",
                  "Shared standing idle is explicitly labeled and is not the source character's original motion.",
                  "Unmatched target bones keep local reference transforms and follow animated parents.",
                  "Float curves, notifies, morphs, material animation and original cloth are not copied or reconstructed.",
              ]}

    def persist(status):
        report["parts"] = [records[(row["character_id"], row["outfit_id"], row["part_id"])] for row in jobs
                           if (row["character_id"], row["outfit_id"], row["part_id"]) in records]
        report["processed_parts"] = len(report["parts"])
        statuses = {row["status"] for row in report["parts"]}
        report["counts"] = {value: sum(row["status"] == value for row in report["parts"]) for value in sorted(statuses)}
        report["status"] = status
        report["recorded_at_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(REPORT_PATH, report)

    persist("running")
    had_failure = False
    for index, job in enumerate(jobs[start:start+count], start):
        row = dict(job)
        key = row["character_id"], row["outfit_id"], row["part_id"]
        records[key] = row
        unreal.log(f"PC_VIEWER_ANIMATION {index+1}/{len(jobs)} {row['family']} {row['outfit_id']} {row['part_id']}")
        try:
            if row["static_socket_attachment"]:
                row.update(animation=None, status="attached_reference_pose",
                           reason="Source accessory has a local non-humanoid skeleton; it follows the configured animated parent socket.")
                continue
            compatibility = json.loads(unreal.PCViewerAnimationLibrary.inspect_animation_compatibility(row["source_animation"], row["mesh"]))
            row["compatibility"] = compatibility
            if compatibility.get("status") != "passed":
                error = str(compatibility.get("error", ""))
                if "Insufficient matching animated/core bones" in error:
                    row.update(animation=None, status="reference_pose_incompatible", reason=error)
                    continue
                raise RuntimeError("Animation compatibility failed: " + json.dumps(compatibility))
            options = {"source_kind": row["source_kind"], "source_selection": row["source_selection"],
                       "family": row["family"], "model_id": row["model_id"], "source_part_id": row["source_part_id"]}
            result = json.loads(unreal.PCViewerAnimationLibrary.adapt_animation(row["source_animation"], row["mesh"], row["animation"], json.dumps(options)))
            row["adaptation"] = result
            if result.get("status") != "passed":
                raise RuntimeError("Animation adaptation failed: " + json.dumps(result))
            result = json.loads(unreal.PCViewerAnimationLibrary.verify_adapted_animation(row["source_animation"], row["mesh"], row["animation"]))
            row["verification"] = result
            if result.get("status") != "passed":
                raise RuntimeError("Fresh animation verification failed: " + json.dumps(result))
            disk = ROOT / "Content" / (row["animation"][len("/Game/"):] + ".uasset")
            if not disk.is_file():
                raise RuntimeError("Verified adapted animation did not save")
            row["asset_file"] = disk.relative_to(ROOT).as_posix()
            row["asset_bytes"], row["asset_sha256"] = disk.stat().st_size, digest(disk)
            row["status"] = "reused" if row["adaptation"].get("operation") == "verified_existing" else "adapted"
        except Exception:
            row["status"] = "failed"
            row["error"] = traceback.format_exc()
            had_failure = True
        finally:
            persist("running")
    failed = any(row.get("status") == "failed" for row in records.values())
    persist("failed" if failed else "passed" if len(records) == len(jobs) else "partial")
    unreal.log("PC_VIEWER_ANIMATION_REPORT " + json.dumps({key: report[key] for key in ("status", "processed_parts", "total_parts", "counts")}))
    if had_failure or failed:
        raise RuntimeError("Some animation adaptations failed; inspect animation_report.json")


if __name__ == "__main__":
    run()
