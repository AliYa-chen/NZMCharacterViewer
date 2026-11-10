"""Disable only the unrecovered source EyeOcclusion overlay in viewer materials.

The original PC auxiliary material has a translucent EyeOcclusion function
whose cooked arithmetic is unavailable. The generic converted candidate uses
white RGB and opacity one, hiding the separately restored iris. A real Metal
MID opacity-zero comparison must confirm that failure before this script runs.
Only a new transparent Unlit master and instance are saved; all original
converted packages and the existing fifteen viewer iris instances stay intact.
"""
from datetime import datetime, timezone
import copy
import json
from pathlib import Path
import struct
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pc_viewer_materials import OWNER, OWNER_TAG, ROOT, REPORT_PATH, TARGET_ROOT, asset_file, digest, load_report, package


WORK = ROOT / "Saved/PCViewerIntegration"
SOURCE_PARENT = "/Game/MaterialLib/Materials/Character/Parent/M_Avatar_EyeOcclusion"
MASTER = TARGET_ROOT + "/M_PC_EyeOcclusionDisabled_v1"
INSTANCE = TARGET_ROOT + "/MI_PC_EyeOcclusionDisabled_v1"
EXTENSION_PLAN = WORK / "eye_occlusion_override_plan.json"
DIAGNOSTIC_PATH = WORK / "EyeValidation/shell_transient_report.json"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.new")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def slot_geometry(mesh, source_slot):
    path = ROOT / mesh["file"]
    if digest(path) != mesh["sha256"]:
        raise RuntimeError("Source EyeOcclusion geometry changed")
    chunks = {}
    with path.open("rb") as stream:
        while header := stream.read(32):
            name, _, stride, count = struct.unpack("<20s3i", header)
            body = stream.read(stride * count)
            if len(body) != stride * count:
                raise RuntimeError("Truncated EyeOcclusion source geometry")
            chunks[name.rstrip(b"\0").decode()] = stride, count, body
    point_stride, _, points = chunks["PNTS0000"]
    wedge_stride, wedge_count, wedges = chunks["VTXW0000"]
    exported = {slot for slot, index in enumerate(mesh["exported_to_source_slots"]) if index == source_slot}
    values = []
    for index in range(wedge_count):
        if wedges[index * wedge_stride + 12] in exported:
            vertex = struct.unpack_from("<I", wedges, index * wedge_stride)[0]
            x, y, z = struct.unpack_from("<fff", points, vertex * point_stride)
            values.append([x, -y, z])
    if not values:
        raise RuntimeError("Source EyeOcclusion section is empty")
    return {"file": mesh["file"], "sha256": mesh["sha256"], "source_slot": source_slot,
            "exported_slots": sorted(exported), "wedges": len(values),
            "decoded_source_bounds_cm": {"minimum": [min(p[a] for p in values) for a in range(3)],
                                          "maximum": [max(p[a] for p in values) for a in range(3)]},
            "coordinate_basis": "ActorX exported Y mirror undone once, matching the imported native PC mesh."}


def make_plan():
    base = load_report()
    if any(value["target"] == INSTANCE for value in base["materials"]):
        raise RuntimeError("EyeOcclusion extension is already published; verify the existing report instead")
    plan_path, source_path = WORK / "viewer_plan.json", WORK / "asset_plan.json"
    viewer, source = read(plan_path), read(source_path)
    material_rows = {row["target"]: row for row in source["materials"]}
    mesh_rows = {row["target"]: row for row in source["meshes"]}
    indexed = {row["target"]: row for row in read(ROOT / "Docs/PCAssetConversion/asset_index.json")["assets"]}
    parents = [row for row in source["materials"] if row["source_package"] == SOURCE_PARENT]
    if len(parents) != 1:
        raise RuntimeError("The explicit PC EyeOcclusion parent is unavailable")
    parent = parents[0]
    props = parent["serialized_root_properties"]
    if props.get("BlendMode") != "BLEND_Translucent" or props.get("ShadingModel") != "MSM_Unlit" or not any("MF_EyeOcclusion" in json.dumps(value) for value in props.get("MaterialFunctionInfos", [])):
        raise RuntimeError("Source material is not the reviewed translucent Unlit EyeOcclusion layer")
    parent_evidence = {"target": parent["target"], "source_package": SOURCE_PARENT,
                       "source_file": parent["file"], "source_sha256": parent["sha256"],
                       "source_properties": {key: props[key] for key in ("BlendMode", "ShadingModel", "bUsedWithSkeletalMesh", "MaterialFunctionInfos", "bUseMaterialAttributes") if key in props}}
    report = copy.deepcopy(base)
    report["revision"] = 2
    report["previous_report_sha256"] = digest(REPORT_PATH)
    protected = {row["target"]: row for row in report["protected_assets"]}
    source_evidence = {}
    occurrences = 0
    for character in viewer["characters"]:
        count = 0
        for outfit in character["outfits"]:
            for part in outfit["parts"]:
                for slot, original in enumerate(part["materials"]):
                    row = material_rows.get(original)
                    if not row or SOURCE_PARENT not in row.get("parent_chain", []):
                        continue
                    if row.get("channels"):
                        raise RuntimeError("EyeOcclusion layer now has connected source channels; reassess the source before disabling it")
                    target = part["mesh_target"]
                    entry = report["meshes"].setdefault(target, {"native_materials": part["materials"][:], "materials": part["materials"][:], "overrides": []})
                    if entry["native_materials"] != part["materials"]:
                        raise RuntimeError("EyeOcclusion native mesh slots differ from the source plan")
                    count += 1
                    occurrences += 1
                    usage = {"character_id": character["id"], "outfit_id": outfit["id"], "part_id": part["id"]}
                    previous = next((value for value in entry["overrides"] if value["slot"] == slot), None)
                    if previous:
                        if previous["target"] != INSTANCE:
                            raise RuntimeError("An EyeOcclusion slot already has another approved override")
                        previous["used_by"].append(usage)
                        continue
                    evidence = {"original": original, "source_package": row["source_package"],
                                "source_file": row["file"], "source_sha256": row["sha256"],
                                "source_parent_chain": row["parent_chain"], "source_parent": parent_evidence,
                                "source_effective_parameters": row["effective_parameters"],
                                "original_adapter_channels": row.get("channels", {}),
                                "scope": "Unrecovered auxiliary EyeOcclusion shading is disabled by transparent Unlit opacity zero. This does not reconstruct the original AO function.",
                                "source_geometry": slot_geometry(mesh_rows[target], slot)}
                    source_evidence[original] = evidence
                    entry["materials"][slot] = INSTANCE
                    entry["overrides"].append({"slot": slot, "slot_name": part["slot_names"][slot], "original": original,
                                               "target": INSTANCE, "kind": "disabled_unrecovered_eye_occlusion",
                                               "source_evidence": evidence, "used_by": [usage]})
                    for value in [target, parent["target"], *part["materials"]]:
                        if value is None or value in protected:
                            continue
                        disk, index = asset_file(value), indexed[value]
                        if digest(disk) != index["sha256"]:
                            raise RuntimeError("Protected original EyeOcclusion package changed")
                        protected[value] = {"target": value, "bytes": disk.stat().st_size, "sha256": index["sha256"]}
        report["characters"][character["id"]]["disabled_eye_occlusion_slots"] = count
    if len(source_evidence) != 15 or occurrences != 16:
        raise RuntimeError("EyeOcclusion scope differs from the fifteen reviewed source materials/sixteen outfit bindings")
    report["protected_assets"] = list(protected.values())
    report["limitations"].append("The source translucent EyeOcclusion auxiliary shell is rendered transparent because its private AO function is unavailable; iris/eyelash geometry and original native slots are preserved.")
    return {"schema_version": 1, "status": "planned", "base_report_sha256": digest(REPORT_PATH),
            "source_plan_sha256": digest(source_path), "script_sha256": digest(Path(__file__)),
            "report": report, "occlusion_evidence": list(source_evidence.values()), "outfit_bindings": occurrences}


def owned(ue, target, kind, factory):
    lib = ue.EditorAssetLibrary
    value = ue.load_asset(target) if lib.does_asset_exist(target) else None
    if value is not None:
        if not isinstance(value, kind) or lib.get_metadata_tag(value, OWNER_TAG) != OWNER:
            raise RuntimeError("Refusing to modify an unowned EyeOcclusion viewer material")
        return value
    folder, name = target.rsplit("/", 1)
    value = ue.AssetToolsHelpers.get_asset_tools().create_asset(name, folder, kind, factory())
    if not isinstance(value, kind):
        raise RuntimeError("Could not create the new EyeOcclusion viewer material")
    lib.set_metadata_tag(value, OWNER_TAG, OWNER)
    return value


def author(ue, document):
    if digest(REPORT_PATH) != document["base_report_sha256"] or digest(WORK / "asset_plan.json") != document["source_plan_sha256"] or digest(Path(__file__)) != document["script_sha256"]:
        raise RuntimeError("EyeOcclusion extension inputs changed after planning")
    # load_report rechecks every frozen iris material and protected original asset.
    load_report()
    diagnostic = read(DIAGNOSTIC_PATH)
    if diagnostic.get("status") != "passed" or diagnostic.get("scope") != "transient_source_eye_occlusion_opacity_zero" or diagnostic.get("base_material_report_sha256") != document["base_report_sha256"]:
        raise RuntimeError("A complete actual Metal EyeOcclusion-only comparison is required")
    for image in diagnostic["images"]:
        if digest(ROOT / image["file"]) != image["sha256"]:
            raise RuntimeError("EyeOcclusion Metal diagnostic PNG changed")
    material = owned(ue, MASTER, ue.Material, ue.MaterialFactoryNew)
    edit = ue.MaterialEditingLibrary
    edit.delete_all_material_expressions(material)
    zero = edit.create_material_expression(material, ue.MaterialExpressionConstant, -300, 0)
    zero.set_editor_property("r", 0.)
    if not edit.connect_material_property(zero, "", ue.MaterialProperty.MP_OPACITY) or not edit.connect_material_property(zero, "", ue.MaterialProperty.MP_EMISSIVE_COLOR):
        raise RuntimeError("Could not connect transparent EyeOcclusion material outputs")
    material.set_editor_property("blend_mode", ue.BlendMode.BLEND_TRANSLUCENT)
    material.set_editor_property("shading_model", ue.MaterialShadingModel.MSM_UNLIT)
    material.set_editor_property("two_sided", False)
    edit.set_material_usage(material, ue.MaterialUsage.MATUSAGE_SKELETAL_MESH)
    errors = list(edit.recompile_material(material))
    if errors:
        raise RuntimeError("Transparent EyeOcclusion graph failed to compile: " + json.dumps(errors))
    instance = owned(ue, INSTANCE, ue.MaterialInstanceConstant, ue.MaterialInstanceConstantFactoryNew)
    edit.set_material_instance_parent(instance, material)
    edit.clear_all_material_instance_parameters(instance)
    edit.update_material_instance(instance)
    report = document["report"]
    evidence = {"source_materials": document["occlusion_evidence"],
                "transparent_graph": {"blend_mode": "BLEND_Translucent", "shading_model": "MSM_Unlit", "opacity": 0, "emissive": 0},
                "actual_metal_diagnostic": {"file": DIAGNOSTIC_PATH.relative_to(ROOT).as_posix(), "sha256": digest(DIAGNOSTIC_PATH), "evidence": diagnostic}}
    for asset in (material, instance):
        ue.EditorAssetLibrary.set_metadata_tag(asset, OWNER_TAG, OWNER)
        ue.EditorAssetLibrary.set_metadata_tag(asset, "PCViewerMaterials.EyeOcclusionPolicy", "disabled_unrecovered_source_layer_opacity_zero")
        ue.EditorAssetLibrary.set_metadata_tag(asset, "PCViewerMaterials.EvidenceJSON", json.dumps(evidence, ensure_ascii=False))
        if not ue.EditorAssetLibrary.save_loaded_asset(asset, only_if_is_dirty=False):
            raise RuntimeError("Could not save separately owned EyeOcclusion override")
        target, disk = package(asset.get_path_name()), asset_file(asset.get_path_name())
        report["materials"].append({"target": target, "class": asset.get_class().get_name(), "file": disk.relative_to(ROOT).as_posix(),
                                    "bytes": disk.stat().st_size, "sha256": digest(disk), "evidence": evidence})
    report["occlusion_extension"] = {"script": Path(__file__).relative_to(ROOT).as_posix(), "script_sha256": document["script_sha256"],
                                     "diagnostic": evidence["actual_metal_diagnostic"], "unique_source_materials": len(document["occlusion_evidence"]),
                                     "outfit_bindings": document["outfit_bindings"], "master": MASTER, "instance": INSTANCE}
    report["counts"].update(materials=len(report["materials"]), eye_occlusion_instances=1,
                            disabled_eye_occlusion_slots=sum(value.get("kind") == "disabled_unrecovered_eye_occlusion" for mesh in report["meshes"].values() for value in mesh["overrides"]),
                            protected_assets=len(report["protected_assets"]))
    for row in report["protected_assets"] + [row for row in document["report"]["materials"] if row["target"] not in (MASTER, INSTANCE)]:
        disk = asset_file(row["target"])
        if disk.stat().st_size != row["bytes"] or digest(disk) != row["sha256"]:
            raise RuntimeError("An original or existing iris package changed while authoring EyeOcclusion")
    backup = WORK / "material_overrides_iris_only_report.json"
    if not backup.exists():
        backup.write_bytes(REPORT_PATH.read_bytes())
    if digest(backup) != document["base_report_sha256"]:
        raise RuntimeError("Previous iris-only material report backup differs")
    report["recorded_at_utc"] = datetime.now(timezone.utc).isoformat()
    write(REPORT_PATH, report)
    validated = load_report()
    from pc_viewer_materials import bound_materials
    for target in validated["meshes"]:
        bound_materials(ue, ue.load_asset(target), validated)
    ue.log("PC_VIEWER_EYE_OCCLUSION_REPORT " + json.dumps(report["counts"]))


def main():
    if "--plan-only" in sys.argv:
        document = make_plan()
        write(EXTENSION_PLAN, document)
        print(json.dumps({"status": "planned", "unique_source_materials": len(document["occlusion_evidence"]), "outfit_bindings": document["outfit_bindings"]}, indent=2))
        return
    import unreal
    try:
        author(unreal, read(EXTENSION_PLAN))
    except Exception:
        write(WORK / "eye_occlusion_override_failure.json", {"status": "failed", "error": traceback.format_exc()})
        raise


if __name__ == "__main__":
    main()
