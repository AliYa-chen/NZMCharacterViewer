"""Add verified PC UI combinations to the existing runtime Catalog.

Run inside UE after attach_pc_viewer_animations.py and the portrait renderer.
Existing character order and stage references are retained. PC outfits
are primary; original outfits follow as clearly labeled comparison choices.
Only the Catalog is saved; converted meshes and their native bindings stay intact.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import traceback


ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "Saved/PCViewerIntegration"
CATALOG = "/Game/Viewer/Data/DA_ViewerCatalog"
OUTPUT = WORK / "catalog_import_report.json"
SOCKET_ACCESSORIES = {"10056010007", "10054010006", "10054010007"}


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def asset_file(reference):
    package = reference.split(".", 1)[0]
    if not package.startswith("/Game/"):
        raise ValueError("Expected project package: " + reference)
    return ROOT / "Content" / (package[6:] + ".uasset")


def checked_load(ue, reference, kind):
    value = ue.load_asset(reference)
    if not isinstance(value, kind):
        raise ValueError("Missing or unexpected asset type: " + reference)
    return value


def preview_notes(outfit, animations):
    values = []
    if any(row["source_kind"] == "shared_idle" for row in animations):
        values.append("共享待机")
    elif any(row["status"] in ("adapted", "reused") for row in animations):
        values.append("待机预览")
    if any(row["status"] == "reference_pose_incompatible" for row in animations):
        values.append("部分部件为参考姿势")
    if outfit.get("missing_default_parts"):
        values.append("部分恢复 · 缺少来源部件")
    return " · ".join(values)


def run(ue, report):
    sys.path.insert(0, str(ROOT / "Scripts"))
    from audit_pc_viewer_integration import Audit, BASELINE_PART_FIELDS
    from pc_viewer_dynamics import resolve_dynamics
    from pc_viewer_materials import load_report as load_material_report, bound_materials
    from pc_viewer_environments import load_report as load_environment_report, environment_for_outfit
    plan_path, baseline_path = WORK / "viewer_plan.json", WORK / "baseline_catalog.json"
    animation_path, portrait_path = WORK / "animation_report.json", WORK / "portraits_report.json"
    plan, baseline, animation, portraits = map(read, (plan_path, baseline_path, animation_path, portrait_path))
    if animation.get("status") != "passed" or animation.get("processed_parts") != plan["counts"]["parts"]:
        raise ValueError("All planned animation rows must be validated before Catalog publication")
    if animation.get("plan_sha256") != digest(plan_path) or animation.get("baseline_catalog_sha256") != digest(baseline_path):
        raise ValueError("Animation report does not match the current plan/baseline")
    if portraits.get("status") != "passed":
        raise ValueError("PC portraits for all characters must be rendered and imported first")
    portrait_inputs = portraits.get("inputs", {})
    for key, path in (("plan_sha256", plan_path), ("baseline_catalog_sha256", baseline_path),
                      ("animation_report_sha256", animation_path)):
        if portrait_inputs.get(key) != digest(path):
            raise ValueError("Portrait report input differs from current Catalog input: " + key)
    material_path = WORK / "material_overrides_report.json"
    material_report = load_material_report(ROOT, material_path)
    if portrait_inputs.get("material_overrides_report_sha256") != digest(material_path):
        raise ValueError("Portraits do not use the current approved Eye materials")
    environment_path = WORK / "viewer_environment_report.json"
    environment_report = load_environment_report(ROOT, environment_path)
    portrait_rows = {r["character_id"]: r for r in portraits["portraits"]}
    if set(portrait_rows) != {c["id"] for c in plan["characters"]}:
        raise ValueError("Portrait report must cover every planned character")
    report["inputs"] = {p.name: digest(p) for p in (plan_path, baseline_path, animation_path, portrait_path,
                                                  material_path, environment_path)}
    catalog = checked_load(ue, CATALOG, ue.ViewerCatalog)
    original = list(catalog.get_editor_property("characters"))
    current, _ = Audit(ue).snapshot(catalog)
    current_by_id = {row["id"]: row for row in current}
    approved = {(c["id"], o["id"]) for document in (baseline, plan)
                for c in document["characters"] for o in c["outfits"]}
    if any((c["id"], o["id"]) not in approved for c in current for o in c["outfits"]):
        raise ValueError("Catalog contains outfits outside the recorded baseline and PC plan")
    baseline_ids = [row["id"] for row in baseline["characters"]]
    if [row["id"] for row in current[:len(baseline_ids)]] != baseline_ids:
        raise ValueError("Existing Catalog character order differs from the baseline")
    for character in baseline["characters"]:
        existing = current_by_id[character["id"]]
        target = portrait_rows[character["id"]]["target"]
        new_portrait = [target + "." + target.rsplit("/", 1)[-1]]
        if existing["name"] != character["name"] or existing["portrait"] not in (character["portrait"], new_portrait):
            raise ValueError("Existing character name/portrait is outside the integration plan: " + character["id"])
        existing_outfits = {o["id"]: o for o in existing["outfits"]}
        for outfit in character["outfits"]:
            saved = existing_outfits[outfit["id"]]
            if saved["name"] not in (outfit["name"], outfit["name"] + "（旧版对照）") or saved["environment"] != outfit["environment"]:
                raise ValueError("Existing outfit/stage differs from baseline: " + outfit["id"])
            before = [{k: p.get(k) for k in BASELINE_PART_FIELDS} for p in outfit["parts"]]
            after = [{k: p.get(k) for k in BASELINE_PART_FIELDS} for p in saved["parts"]]
            if before != after:
                raise ValueError("Existing part settings differ from baseline: " + outfit["id"])
    animation_rows = {(r["character_id"], r["outfit_id"], r["part_id"]): r for r in animation["parts"]}
    index = {r["target"]: r for r in read(ROOT / "Docs/PCAssetConversion/asset_index.json")["assets"]}
    character_by_id = {str(c.get_editor_property("id")): c for c in original}
    baseline_by_id = {c["id"]: c for c in baseline["characters"]}
    # Keep original part settings, with PC combinations first for every character.
    characters = [character_by_id[cid] for cid in baseline_ids]
    for source_character in plan["characters"]:
        cid = source_character["id"]
        if cid in baseline_by_id:
            character = character_by_id[cid]
            existing_outfits = {str(o.get_editor_property("id")): o for o in character.get_editor_property("outfits")}
            comparisons = []
            for source in baseline_by_id[cid]["outfits"]:
                old = existing_outfits[source["id"]]
                old.set_editor_property("display_name", ue.Text(source["name"] + "（旧版对照）"))
                comparisons.append(old)
        else:
            character = ue.ViewerCharacter()
            character.set_editor_property("id", cid)
            character.set_editor_property("display_name", ue.Text(source_character["display_name"]))
            characters.append(character)
            comparisons = []
        portrait = portrait_rows[cid]
        target = (baseline_by_id[cid]["portrait"][0] if cid in baseline_by_id
                  else portrait["target"])
        if cid not in baseline_by_id and digest(asset_file(target)) != portrait["texture"]["sha256"]:
            raise ValueError("Available portrait changed after verification: " + target)
        character.set_editor_property("portrait", checked_load(ue, target, ue.Texture2D))
        outfits = []
        for source_outfit in source_character["outfits"]:
            oid = source_outfit["id"]
            rows, parts, mesh_by_id = [], [], {}
            for source_part in source_outfit["parts"]:
                pid, target = source_part["id"], source_part["mesh_target"]
                if digest(asset_file(target)) != index[target]["sha256"]:
                    raise ValueError("Mesh changed after conversion: " + target)
                mesh = checked_load(ue, target, ue.SkeletalMesh)
                mesh_by_id[pid] = mesh
                part = ue.ViewerPart()
                part.set_editor_property("id", pid)
                part.set_editor_property("display_name", ue.Text(source_part["display_name"]))
                part.set_editor_property("mesh", mesh)
                # Native mesh materials retain source slot identity and empty slots.
                part.set_editor_property("materials", bound_materials(ue, mesh, material_report)
                                         if target in material_report["meshes"] else [])
                part.set_editor_property("visible_by_default", source_part["visible_by_default"])
                transform = ue.Transform()
                spec = source_part["transform"]
                transform.translation = ue.Vector(*spec["translation"])
                transform.rotation = ue.Quat(*spec["rotation_xyzw"])
                transform.scale3d = ue.Vector(*spec["scale"])
                part.set_editor_property("relative_transform", transform)
                row = animation_rows[cid, oid, pid]
                rows.append(row)
                if row["status"] in ("adapted", "reused"):
                    if digest(asset_file(row["animation"])) != row["asset_sha256"]:
                        raise ValueError("Animation changed after verification: " + row["animation"])
                    clip = checked_load(ue, row["animation"], ue.AnimSequence)
                    if clip.get_editor_property("skeleton") != mesh.get_editor_property("skeleton"):
                        raise ValueError("Animation skeleton differs from native mesh: " + pid)
                    part.set_editor_property("animation", clip)
                    part.set_editor_property("animation_loop", True)
                elif row["status"] not in ("attached_reference_pose", "reference_pose_incompatible"):
                    raise ValueError("Unsupported animation status: " + row["status"])
                dynamics, dynamics_record = resolve_dynamics(ue, source_outfit, source_part, baseline, mesh)
                if dynamics is not None:
                    part.set_editor_property("dynamics", dynamics)
                report["dynamics"].append(dynamics_record)
                parts.append(part)
            for source_part, part in zip(source_outfit["parts"], parts):
                if source_part["source_part_id"] not in SOCKET_ACCESSORIES:
                    continue
                bodies = [p for p in source_outfit["parts"] if p["slot"] in ("EAvatarSlot::Body", "EAvatarSlot::WholeBody")]
                if len(bodies) != 1:
                    raise ValueError("Accessory requires one body parent: " + source_part["id"])
                parent = bodies[0]["id"]
                socket = source_part["source_attachment"]["socket_name"]
                component = ue.new_object(ue.SkeletalMeshComponent)
                component.set_skeletal_mesh_asset(mesh_by_id[parent])
                if not component.does_socket_exist(socket):
                    raise ValueError("Accessory socket missing on parent: " + socket)
                part.set_editor_property("parent_part_id", parent)
                part.set_editor_property("attach_socket", socket)
            outfit = ue.ViewerOutfit()
            outfit.set_editor_property("id", oid)
            outfit.set_editor_property("display_name", ue.Text(source_outfit["display_name"]))
            outfit.set_editor_property("preview_notes", ue.Text(preview_notes(source_outfit, rows)))
            outfit.set_editor_property("parts", parts)
            stage = environment_for_outfit(oid, environment_report)
            outfit.set_editor_property("environment", checked_load(ue, stage, ue.ViewerEnvironment))
            outfits.append(outfit)
            report["outfits"].append({"character_id": cid, "outfit_id": oid, "parts": len(parts),
                                      "environment": stage, "preview_notes": preview_notes(source_outfit, rows)})
        character.set_editor_property("outfits", outfits + comparisons)
    disk = asset_file(CATALOG)
    backup = WORK / "DA_ViewerCatalog.before_integration.uasset"
    if not backup.exists():
        if digest(disk) != baseline["catalog_sha256"]:
            raise ValueError("Cannot establish an original Catalog backup from modified disk")
        shutil.copyfile(disk, backup)
    if digest(backup) != baseline["catalog_sha256"]:
        raise ValueError("Original Catalog backup differs from recorded baseline")
    catalog.set_editor_property("characters", characters)
    if not ue.EditorAssetLibrary.save_loaded_asset(catalog, False):
        raise RuntimeError("Catalog save failed")
    report.update(status="passed", catalog_sha256=digest(disk), characters=len(characters),
                  total_outfits=sum(len(c.get_editor_property("outfits")) for c in characters),
                  new_outfits=len(report["outfits"]), new_parts=sum(r["parts"] for r in report["outfits"]))
    report["portrait_policy"] = {"original_portraits_retained": len(baseline_ids),
                                "available_pc_portraits_used_for_new_characters": len(characters) - len(baseline_ids),
                                "additional_portrait_rendering_required": False}


def main():
    import unreal
    report = {"schema_version": 1, "status": "running", "outfits": [], "dynamics": [],
              "catalog": CATALOG, "started_utc": datetime.now(timezone.utc).isoformat()}
    try:
        run(unreal, report)
    except Exception:
        report.update(status="failed", error=traceback.format_exc())
    report["finished_utc"] = datetime.now(timezone.utc).isoformat()
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    unreal.log("PC_VIEWER_CATALOG_IMPORT " + json.dumps({k: report.get(k) for k in
               ("status", "characters", "total_outfits", "new_outfits", "new_parts")}))
    if report["status"] != "passed":
        raise RuntimeError(report["error"])


if __name__ == "__main__":
    main()
