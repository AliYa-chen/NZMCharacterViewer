"""Bind saved native shading and Chaos cloth assets to the existing PC outfits.

Run in Unreal Editor after prepare_pc_viewer_shaders.py and
prepare_pc_viewer_cloth.py. All other Catalog fields, the comparison outfits,
portraits, source meshes and the approved Eye overrides are preserved.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import traceback

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "Saved/PCFeatureIntegration"
CATALOG = "/Game/Viewer/Data/DA_ViewerCatalog"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def package(value):
    return str(value).split(".", 1)[0]


def disk(reference):
    name = package(reference)
    if not name.startswith("/Game/") or any(p in ("", ".", "..") for p in name[6:].split("/")):
        raise ValueError("Expected a project asset: " + name)
    return ROOT / "Content" / (name[6:] + ".uasset")


def objpath(reference):
    name = package(reference)
    return name + "." + name.rsplit("/", 1)[-1]


def load(ue, reference, cls):
    result = ue.load_asset(reference)
    if not isinstance(result, cls):
        raise RuntimeError("Missing asset or wrong class: " + str(reference))
    return result


def run(ue, report):
    sys.path.insert(0, str(ROOT / "Scripts"))
    from audit_pc_viewer_integration import Audit
    import pc_viewer_materials as eye
    import pc_viewer_shading as shading

    viewer_path = ROOT / "Saved/PCViewerIntegration/viewer_plan.json"
    plan = read(viewer_path)
    shader_report = shading.load_report()
    eye_report = eye.load_report()
    cloth_path = WORK / "cloth_report.json"
    cloth = read(cloth_path)
    if cloth.get("status") != "passed" or cloth.get("owner") != "pc_viewer_chaos_cloth_v1":
        raise RuntimeError("Native cloth authoring report did not pass")
    if cloth.get("viewer_plan_sha256") != digest(viewer_path):
        raise RuntimeError("Cloth report belongs to a different viewer plan")
    cloth_rows = {row["source_mesh"]: row for row in cloth["meshes"]}
    if len(cloth_rows) != len(cloth["meshes"]):
        raise RuntimeError("Duplicate source cloth meshes")
    bindings = {(row["character_id"], row["outfit_id"], row["part_id"]): row
                for row in cloth["part_bindings"]}
    if len(bindings) != len(cloth["part_bindings"]):
        raise RuntimeError("Duplicate cloth part bindings")
    for row in cloth_rows.values():
        target = row["target"]
        if row.get("status") != "passed" or not target.startswith("/Game/PCAssets/ViewerCloth/"):
            raise RuntimeError("Unapproved cloth target")
        if digest(disk(target)) != row["asset"]["sha256"]:
            raise RuntimeError("Cloth asset changed after verification: " + target)
        if row.get("live_cloth_sections", 0) < 1:
            raise RuntimeError("Cloth mesh has no live render binding: " + target)

    catalog = load(ue, CATALOG, ue.ViewerCatalog)
    audit = Audit(ue)
    before, _ = audit.snapshot(catalog)
    expected = deepcopy(before)
    planned = {(c["id"], o["id"], p["id"]): p for c in plan["characters"]
               for o in c["outfits"] for p in o["parts"]}
    actual_keys = {(c["id"], o["id"], p["id"]) for c in before
                   for o in c["outfits"] for p in o["parts"]}
    if not set(planned) <= actual_keys or not set(bindings) <= set(planned):
        raise RuntimeError("Catalog/cloth keys differ from the approved PC plan")
    backup = WORK / "DA_ViewerCatalog.before_features.uasset"
    if not backup.exists():
        shutil.copyfile(disk(CATALOG), backup)
    report["catalog_before_sha256"] = digest(disk(CATALOG))
    report["inputs"] = {str(path.relative_to(ROOT)): digest(path) for path in (
        viewer_path, cloth_path, WORK / "shading_report.json",
        ROOT / "Saved/PCViewerIntegration/material_overrides_report.json")}
    report["parts"] = []
    characters = list(catalog.get_editor_property("characters"))
    for ci, character in enumerate(characters):
        cid = str(character.get_editor_property("id"))
        outfits = list(character.get_editor_property("outfits"))
        for oi, outfit in enumerate(outfits):
            oid = str(outfit.get_editor_property("id"))
            parts = list(outfit.get_editor_property("parts"))
            for pi, part in enumerate(parts):
                key = cid, oid, str(part.get_editor_property("id"))
                if key not in planned:
                    continue
                source = planned[key]["mesh_target"]
                original = load(ue, source, ue.SkeletalMesh)
                native = [slot.get_editor_property("material_interface")
                          for slot in original.get_editor_property("materials")]
                paths = [package(m.get_path_name()) if m else None for m in native]
                shaded = shading.effective_material_paths(source, paths, shader_report)
                eyes = eye.effective_material_paths(source, paths, eye_report)
                eye_slots = eye_report["meshes"].get(source, {}).get("overrides", [])
                for replacement in eye_slots:
                    shaded[int(replacement["slot"])] = eyes[int(replacement["slot"])]
                mats = [load(ue, name, ue.MaterialInterface) if name else None for name in shaded]
                binding = bindings.get(key)
                mesh = original
                if binding:
                    if binding["source_mesh"] != source or source not in cloth_rows:
                        raise RuntimeError("Cloth binding does not identify the native source: " + str(key))
                    mesh = load(ue, binding["target"], ue.SkeletalMesh)
                    if mesh.get_editor_property("skeleton") != original.get_editor_property("skeleton"):
                        raise RuntimeError("Cloth duplicate changed the animation skeleton")
                    duplicate_mats = [slot.get_editor_property("material_interface")
                                      for slot in mesh.get_editor_property("materials")]
                    if duplicate_mats != native:
                        raise RuntimeError("Cloth duplicate changed the native material slots")
                current = before[ci]["outfits"][oi]["parts"][pi]
                target = package(mesh.get_path_name())
                if current["mesh"] not in ([objpath(source)], [objpath(target)]):
                    raise RuntimeError("Unexpected preexisting mesh: " + str(key))
                # Avoid creating unnecessary overrides on unmodified source slots.
                effective = mats if shaded != paths else []
                part.set_editor_property("mesh", mesh)
                part.set_editor_property("materials", effective)
                wanted = expected[ci]["outfits"][oi]["parts"][pi]
                wanted["mesh"] = [objpath(target)]
                wanted["materials"] = [objpath(m) for m in shaded if m] if effective else []
                report["parts"].append({"character_id": cid, "outfit_id": oid,
                    "part_id": key[2], "source_mesh": source, "mesh": target,
                    "materials": shaded, "cloth": bool(binding),
                    "eye_override_slots_preserved": [r["slot"] for r in eye_slots]})
            outfit.set_editor_property("parts", parts)
        character.set_editor_property("outfits", outfits)
    catalog.set_editor_property("characters", characters)
    after, _ = audit.snapshot(catalog)
    if after != expected:
        raise RuntimeError("Catalog mutation changed a field outside mesh/material bindings")
    if len(report["parts"]) != plan["counts"]["parts"]:
        raise RuntimeError("PC binding coverage is incomplete")
    if not ue.EditorAssetLibrary.save_loaded_asset(catalog, False):
        raise RuntimeError("Catalog save failed")
    report.update(status="passed", catalog_after_sha256=digest(disk(CATALOG)),
        pc_parts=len(report["parts"]), cloth_parts=sum(p["cloth"] for p in report["parts"]),
        unchanged_fields_verified=True, eye_overrides_preserved=True,
        portrait_rendering_performed=False, baseline_parts_preserved=40)
    (WORK / "catalog_feature_snapshot.json").write_text(json.dumps(after, ensure_ascii=False, indent=2) + "\n")


def main():
    import unreal
    WORK.mkdir(parents=True, exist_ok=True)
    report = {"schema_version": 1, "status": "running",
              "started_at_utc": datetime.now(timezone.utc).isoformat()}
    try:
        run(unreal, report)
    except Exception:
        report.update(status="failed", error=traceback.format_exc())
        raise
    finally:
        (WORK / "catalog_feature_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
