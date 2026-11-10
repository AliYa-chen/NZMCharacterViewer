"""Author only verified unchanged-chain subsets for PC-native skeletons.

Run in a fresh UE Editor Python commandlet, optionally with -nullrhi. Original
profiles, meshes and Catalog are read-only. Deterministic new profiles are saved
under /Game/PCAssets/Dynamics; missing native chains are explicitly omitted.
"""
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import traceback


ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "Saved/PCViewerIntegration"
SPECIFICATION = importlib.util.spec_from_file_location("pc_dynamics_authoring_policy", ROOT / "Scripts/pc_viewer_dynamics.py")
POLICY = importlib.util.module_from_spec(SPECIFICATION)
SPECIFICATION.loader.exec_module(POLICY)


def prepare_pc_dynamics(ue, source_outfit, source_part, baseline, target_mesh):
    source, row, context = POLICY.inspect_dynamics(ue, source_outfit, source_part, baseline, target_mesh)
    if row["status"] != "requires_compatible_subset_derivation":
        return source if row["status"] == "reused_exact_same_outfit_part_profile" else None, row
    target = POLICY.derived_target(row)
    existing = ue.EditorAssetLibrary.does_asset_exist(target)
    if existing:
        profile = ue.load_asset(target)
    else:
        directory, name = target.rsplit("/", 1)
        ue.EditorAssetLibrary.make_directory(directory)
        profile = ue.AssetToolsHelpers.get_asset_tools().duplicate_asset(name, directory, source)
        if not isinstance(profile, ue.ViewerDynamicsProfile):
            raise POLICY.DynamicsCompatibilityError("Could not duplicate the exact original dynamics profile", row)
        original_chains = list(source.get_editor_property("chains"))
        kept = row["preserved_original_settings_indices"]
        profile.set_editor_property("chains", [original_chains[index] for index in kept])
        profile.set_editor_property("reconstruction_notes", POLICY.derived_notes(source, kept))
    if not isinstance(profile, ue.ViewerDynamicsProfile) or profile.get_path_name() != POLICY.object_path(target):
        raise POLICY.DynamicsCompatibilityError("Derived profile has an unexpected type or target", row)
    verification = POLICY.verify_derived_profile(profile, source, context, row)
    if not existing and not ue.EditorAssetLibrary.save_loaded_asset(profile, False):
        raise POLICY.DynamicsCompatibilityError("Could not save verified compatible dynamics subset", row)
    row.update(status="derived_compatible_subset", profile=profile.get_path_name(),
               profile_asset=POLICY.fingerprint(target), verification=verification,
               operation="verified_existing" if existing else "created_compatible_subset")
    return profile, row


def main():
    import unreal as ue
    plan_path, baseline_path = WORK / "viewer_plan.json", WORK / "baseline_catalog.json"
    plan, baseline = (json.loads(path.read_text(encoding="utf-8-sig")) for path in (plan_path, baseline_path))
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    report = {"schema_version": 2, "policy_version": POLICY.VERSION, "status": "running", "parts": [], "errors": [],
              "inputs": {"plan_sha256": sha(plan_path), "baseline_catalog_sha256": sha(baseline_path)},
              "helper_sha256": sha(ROOT / "Scripts/pc_viewer_dynamics.py"), "authoring_script_sha256": sha(Path(__file__)),
              "original_content_saved": False, "new_content_scope": "/Game/PCAssets/Dynamics",
              "limitations": [
                  "Only verified unchanged chains from an exact same-outfit original part are reused.",
                  "Missing or changed native particles are omitted; original physics completeness is not claimed.",
                  "No bone renaming, inferred roots, shared defaults, virtual tips or new-character cloth is introduced.",
                  "The existing bounded viewer solver is retained; original game physics and cloth are not reconstructed."]}
    protected = {}
    catalog = POLICY.fingerprint("/Game/Viewer/Data/DA_ViewerCatalog")
    protected[catalog["package"]] = catalog

    def persist():
        WORK.mkdir(parents=True, exist_ok=True)
        temporary = POLICY.REPORT_PATH.with_suffix(".json.new")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(POLICY.REPORT_PATH)

    persist()
    for character in plan["characters"]:
        for outfit in character["outfits"]:
            for part in outfit["parts"]:
                try:
                    mesh = ue.load_asset(part.get("mesh_target", part.get("mesh")))
                    _, row = prepare_pc_dynamics(ue, outfit, part, baseline, mesh)
                    row["target_mesh_asset"] = POLICY.fingerprint(mesh.get_path_name())
                    for key in ("target_mesh_asset", "baseline_mesh_asset", "source_profile_asset"):
                        if key in row:
                            protected[row[key]["package"]] = row[key]
                except Exception as error:
                    row = getattr(error, "report", {"outfit_id": outfit["id"], "part_id": part["id"]})
                    row.update(status="failed", traceback=traceback.format_exc())
                    report["errors"].append({"character_id": character["id"], **row})
                row["character_id"] = character["id"]
                report["parts"].append(row)
                if len(report["parts"]) % 8 == 0:
                    persist()
    report["protected_assets"] = []
    for name, before in sorted(protected.items()):
        after = POLICY.fingerprint(name)
        report["protected_assets"].append({"package": name, "sha256_before": before["sha256"],
                                            "sha256_after": after["sha256"], "unchanged": before == after})
    report["processed_parts"] = len(report["parts"])
    report["counts"] = dict(Counter(row["status"] for row in report["parts"]))
    report["partial_source_compatibility"] = any(row["status"] in
        ("derived_compatible_subset", "unavailable_native_chain_mismatch") for row in report["parts"])
    report["status"] = "passed" if not report["errors"] and report["processed_parts"] == plan["counts"]["parts"] and all(
        row["unchanged"] for row in report["protected_assets"]) else "failed"
    persist()
    ue.log("PC_VIEWER_DYNAMICS_PREPARE " + json.dumps({key: report[key] for key in
           ("status", "processed_parts", "counts", "partial_source_compatibility")}))
    if report["status"] != "passed":
        raise RuntimeError("PC dynamics subset preparation failed; see " + str(POLICY.REPORT_PATH))


if __name__ == "__main__":
    main()
