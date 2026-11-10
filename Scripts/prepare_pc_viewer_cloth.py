"""Author independent native Chaos cloth mesh copies; never change the Catalog.

Run as a UE Python commandlet after building PCViewerClothLibrary. Source JSON
comes only from the verified temporary feature cache. --verify-only inspects
the saved copies in a new process without authoring or rendering portraits.
"""
import argparse
import json
from pathlib import Path
import sys
import traceback


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "Scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "Scripts"))
import build_pc_viewer_cloth_plan as policy

WORK = ROOT / "Saved/PCFeatureIntegration"
PLAN = WORK / "cloth_plan.json"
REPORT = WORK / "cloth_report.json"


def fingerprint(target):
    path = ROOT / "Content" / (target[len("/Game/"):] + ".uasset")
    return {"package": target, "file": path.relative_to(ROOT).as_posix(),
            "bytes": path.stat().st_size, "sha256": policy.digest(path)}


def inspect(unreal, row):
    actual = json.loads(unreal.PCViewerClothLibrary.inspect_cloth_mesh(row["target"]))
    if actual.get("status") != "passed" or not actual.get("has_active_clothing"):
        raise ValueError("Saved native cloth mesh is inactive: " + row["target"])
    if actual["skeleton"].split(".", 1)[0] != row["skeleton"]:
        raise ValueError("Cloth copy did not retain its exact animation skeleton")
    if actual.get("collision_geometry_scale") != f"{row['collision_geometry_scale']:.6f}":
        raise ValueError("Saved cloth collision geometry differs from the explicit compatibility scale")
    expected = {"CA_" + source["name"]: source for source in row["cloths"]}
    if len(actual["cloth_assets"]) != len(expected):
        raise ValueError("Native cloth asset coverage differs from complete source records")
    for asset in actual["cloth_assets"]:
        source = expected[asset["name"]]
        if not asset["chaos_config"] or asset["collision_bodies"] <= 0 or asset["live_binding"] != source["bind"]:
            raise ValueError("Native cloth lacks solver config, collision bodies or source section binding")
        for key in ("physical_vertices", "triangles", "pinned_vertices"):
            if asset[key] != source[key]:
                raise ValueError("Native saved cloth changed its source " + key)
    if actual["live_cloth_sections"] != row["live_cloth_sections"]:
        raise ValueError("Not all source LOD0 cloth sections are bound")
    return actual


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    args, _ = parser.parse_known_args()
    if args.verify_only:
        import unreal
        report = policy.read(REPORT)
        plan = policy.read(PLAN)
        if report["status"] != "passed" or report["plan_sha256"] != policy.digest(PLAN):
            raise ValueError("Cloth report is not a completed current plan")
        checks = []
        for row, saved in zip(plan["meshes"], report["meshes"]):
            if fingerprint(row["target"]) != saved["asset"]:
                raise ValueError("Cloth mesh package changed after authoring")
            checks.append({"target": row["target"], "inspection": inspect(unreal, row), "asset": saved["asset"]})
        output = {"schema_version": 1, "status": "passed", "content_saved": False,
                  "cloth_report_sha256": policy.digest(REPORT), "meshes": checks}
        policy.write(WORK / "cloth_fresh_verification.json", output)
        unreal.log("PC_VIEWER_CLOTH_FRESH " + json.dumps({"status": "passed", "meshes": len(checks)}))
        return
    plan = policy.make_plan(ROOT)
    policy.write(PLAN, plan)
    if args.plan_only:
        print(json.dumps(plan["counts"], indent=2))
        return
    import unreal
    report = {"schema_version": 1, "status": "running", "owner": policy.OWNER,
              "plan_sha256": policy.digest(PLAN), "viewer_plan_sha256": plan["viewer_plan_sha256"],
              "asset_index_sha256": plan["asset_index_sha256"], "meshes": [], "errors": [],
              "part_bindings": plan["part_bindings"], "limitations": plan["limitations"],
              "catalog_saved": False, "original_meshes_saved": False}
    protected = {row["source_mesh"]: fingerprint(row["source_mesh"]) for row in plan["meshes"]}
    try:
        for row in plan["meshes"]:
            options = {"owner": policy.OWNER, "cloths": row["cloths"], "collision_meshes": row["collision_meshes"],
                       "collision_geometry_scale": row["collision_geometry_scale"]}
            result = json.loads(unreal.PCViewerClothLibrary.build_cloth_mesh(row["source_mesh"], row["target"], json.dumps(options)))
            if result.get("status") != "passed":
                raise ValueError("Native cloth authoring failed: " + json.dumps(result))
            actual = inspect(unreal, row)
            report["meshes"].append({"source_mesh": row["source_mesh"], "target": row["target"],
                                     "status": "passed", "skeleton": row["skeleton"],
                                     "live_cloth_sections": actual["live_cloth_sections"],
                                     "source_cloth_assets": actual["source_cloth_assets"],
                                     "cloth_assets": actual["cloth_assets"], "inspection": actual,
                                     "source_config_field_coverage": [{"name": cloth["name"],
                                         "serialized_fields": cloth["source_config_field_coverage"],
                                         "stock_legacy_default_fields": cloth["omitted_legacy_constructor_fields"]}
                                         for cloth in row["cloths"]],
                                     "collision_basis": row["collision_basis"],
                                     "collision_geometry_scale": row["collision_geometry_scale"],
                                     "asset": fingerprint(row["target"])})
        if any(fingerprint(target) != before for target, before in protected.items()):
            raise ValueError("Original converted mesh changed during cloth authoring")
        if policy.digest(ROOT / "Docs/PCAssetConversion/asset_index.json") != plan["asset_index_sha256"]:
            raise ValueError("Original conversion index changed during cloth authoring")
        report["status"] = "passed"
        report["counts"] = plan["counts"]
    except Exception:
        report["status"] = "failed"
        report["errors"].append(traceback.format_exc())
        raise
    finally:
        policy.write(REPORT, report)
        unreal.log("PC_VIEWER_CLOTH " + json.dumps({"status": report["status"], "meshes": len(report["meshes"])}))


if __name__ == "__main__":
    main()
