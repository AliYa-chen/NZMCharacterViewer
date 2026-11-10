"""Plan native Chaos cloth from every retained, complete Nv physical mesh.

Uses verified temporary JSON only. Original conversion packages and the source
index stay immutable; authored runtime copies live in PCAssets/ViewerCloth.
"""
import hashlib
import json
from pathlib import Path
import re

from export_pc_feature_sources import load_source, source_path


ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "Saved/PCFeatureIntegration"
OUTPUT = WORK / "cloth_plan.json"
OWNER = "pc_viewer_chaos_cloth_v1"
LEGACY_FIELDS = {
    "WindMethod", "VerticalConstraintConfig", "HorizontalConstraintConfig", "BendConstraintConfig",
    "ShearConstraintConfig", "SelfCollisionRadius", "SelfCollisionStiffness", "SelfCollisionCullScale",
    "Damping", "Friction", "WindDragCoefficient", "WindLiftCoefficient", "LinearDrag", "AngularDrag",
    "LinearInertiaScale", "AngularInertiaScale", "CentrifugalInertiaScale", "SolverFrequency",
    "StiffnessFrequency", "GravityScale", "GravityOverride", "bUseGravityOverride", "TetherStiffness",
    "TetherLimit", "CollisionThickness", "AnimDriveSpringStiffness", "AnimDriveDamperStiffness",
}
MIGRATION_OMISSIONS = {
    "SolverFrequency": "UE shared-config migration derives IterationCount=clamp(int(frequency/120),1,100) and MaxIterationCount=clamp(int(frequency/30),1,100); Chaos does not run the original Nv frequency solver.",
    "StiffnessFrequency": "UE migration uses it to calculate damping but does not reproduce the original stiffness-frequency solver.",
    "LinearDrag": "UE's legacy-to-Chaos migration explicitly omits this Nv solver setting.",
    "AngularDrag": "UE's legacy-to-Chaos migration explicitly omits this Nv solver setting.",
    "SelfCollisionStiffness": "Only enables/disables Chaos self collision; the exact Nv spring stiffness is not migrated.",
    "SelfCollisionCullScale": "UE's legacy-to-Chaos migration explicitly omits this Nv solver setting.",
}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def package_from_record(record):
    name = record["Package"]
    if not name.startswith("NZM/Content/"):
        raise ValueError("Unexpected source mesh package: " + name)
    return "/Game/" + name[len("NZM/Content/"):]


def reference_name(reference):
    return reference["ObjectName"].split("'", 2)[1].rsplit(":", 1)[-1]


def make_plan(root=ROOT):
    root = Path(root)
    index_path = root / "Docs/PCAssetConversion/asset_index.json"
    index = read(index_path)
    viewer_path = root / "Saved/PCViewerIntegration/viewer_plan.json"
    viewer = read(viewer_path)
    manifest_path = root / "Saved/PCFeatureIntegration/source_manifest.json"
    manifest = read(manifest_path)
    source_records = {row["source_file"]: row for row in manifest["sources"]}
    all_parts = [{"character_id": character["id"], "outfit_id": outfit["id"], **part}
                 for character in viewer["characters"] for outfit in character["outfits"]
                 for part in outfit["parts"]]
    meshes_by_source = {}
    for row in index["assets"]:
        if row["type"] == "SkeletalMesh":
            meshes_by_source.setdefault(row["source_package"], []).append(row)
    physical_rows = [row for row in index["assets"]
                     if row.get("source_file", "").endswith("/ClothPhysicalMeshDataNv.json")]
    grouped = {}
    for row in physical_rows:
        physical_file = row["source_file"]
        clothing_stem = physical_file.split("/ClothLODDataNv_0/", 1)[0]
        mesh_file = clothing_stem.rsplit("/", 1)[0] + ".json"
        grouped.setdefault(mesh_file, []).append((row, clothing_stem))
    outputs, bindings = [], []
    for mesh_file, cloth_records in sorted(grouped.items()):
        metadata = load_source(mesh_file, root)
        source_package = package_from_record(metadata)
        converted = meshes_by_source.get(source_package, [])
        if len(converted) != 1:
            raise ValueError("Cloth source requires one exact converted mesh: " + source_package)
        converted = converted[0]
        target = "/Game/PCAssets/ViewerCloth/" + converted["target"].rsplit("/", 1)[-1] + "_Chaos"
        matching_parts = [part for part in all_parts if part["source_package"] == source_package]
        family = source_package.split("/Character/", 1)[1].split("/", 1)[0].casefold()
        context_part = matching_parts[0] if matching_parts else next(
            part for part in all_parts if part["character_id"] == family)
        context_parts = [part for part in all_parts if part["character_id"] == context_part["character_id"]
                         and part["outfit_id"] == context_part["outfit_id"]]
        # Fit real collision bodies to visible same-outfit head/body geometry.
        # Missing original PHYS payloads are never replaced by claimed source values.
        colliders = [part["mesh"] for slot in ("::Head", "::Body") for part in context_parts
                     if str(part.get("slot", "")).endswith(slot)]
        if not colliders:
            raise ValueError("No corresponding character geometry for compatible cloth collisions")
        referenced = [reference_name(row) for row in metadata["Properties"]["MeshClothingAssets"]]
        active_sections = {}
        for section_index, section in enumerate(metadata["LODModels"][0]["Sections"]):
            clothing_index = int(section.get("CorrespondClothAssetIndex", -1))
            if clothing_index < 0:
                continue
            name = referenced[clothing_index]
            if name in active_sections:
                raise ValueError("One source cloth is bound to multiple source LOD0 sections")
            active_sections[name] = {"material_index": int(section["MaterialIndex"]),
                                     "source_section_index": section_index,
                                     "source_num_triangles": int(section["NumTriangles"])}
        cloths = []
        for physical_record, stem in sorted(cloth_records, key=lambda row: row[1]):
            clothing_file, config_file = stem + ".json", stem + "/ClothConfigNv.json"
            clothing = load_source(clothing_file, root)
            physical = load_source(physical_record["source_file"], root)["Properties"]
            config = load_source(config_file, root)["Properties"]
            name = clothing["Name"]
            count = len(physical["Vertices"])
            if len(physical["Normals"]) != count or len(physical["MaxDistances"]) != count or len(physical["BoneData"]) != count:
                raise ValueError("Incomplete physical cloth arrays: " + name)
            fixed = sum(value == 0 for value in physical["MaxDistances"])
            if fixed != physical["NumFixedVerts"]:
                raise ValueError("Physical source pin map does not match its fixed vertex count")
            coverage = []
            for key, value in config.items():
                coverage.append({"source_field": key, "source_value": value,
                                 "legacy_property_recognized": key in LEGACY_FIELDS,
                                 "migration": MIGRATION_OMISSIONS.get(key,
                                     "Stock UE5.8 FClothConfig_Legacy to UChaosClothConfig migration; solver response is not exact Nv parity."
                                     if key in LEGACY_FIELDS else "Unsupported source field; retained as evidence only.")})
            unknown = sorted(set(config) - LEGACY_FIELDS)
            if unknown:
                raise ValueError("Source cloth config contains unmapped fields requiring an explicit adapter: " + repr(unknown))
            row = {"name": name, "source_lod": int(re.search(r"_Lod(\d+)_", name).group(1)),
                   "bind": name in active_sections,
                   "binding_status": "source_LOD0_render_section" if name in active_sections else "retained_unmapped_source_lod",
                   "clothing_source_file": clothing_file, "config_source_file": config_file,
                   "physical_source_file": physical_record["source_file"],
                   "clothing_json": str(source_path(clothing_file, root)),
                   "config_json": str(source_path(config_file, root)),
                   "physical_json": str(source_path(physical_record["source_file"], root)),
                   "source_sha256": {"clothing": source_records[clothing_file]["sha256"],
                                     "config": source_records[config_file]["sha256"],
                                     "physical": source_records[physical_record["source_file"]]["sha256"]},
                   "physical_vertices": count, "triangles": len(physical["Indices"]) // 3,
                   "pinned_vertices": fixed, "source_bone_names": clothing["Properties"]["UsedBoneNames"],
                   "stock_chaos_kinematic_distance_threshold_cm": 0.1,
                   "stock_chaos_kinematic_vertices": sum(value < 0.1 for value in physical["MaxDistances"]),
                   "parameter_maps": [key for key in ("MaxDistances", "BackstopDistances", "BackstopRadiuses", "AnimDriveMultipliers") if key in physical],
                   "source_config": config, "source_config_field_coverage": coverage,
                   "omitted_legacy_constructor_fields": sorted(LEGACY_FIELDS - set(config)),
                   "referenced_original_physics_asset": clothing["Properties"].get("PhysicsAsset"),
                   **active_sections.get(name, {})}
            cloths.append(row)
        live = {row["name"] for row in cloths if row["bind"]}
        if live != set(active_sections) or not live:
            raise ValueError("Source physical records do not cover every live source LOD0 section: " + source_package)
        outputs.append({"source_mesh": converted["target"], "source_package": source_package,
                        "source_asset": {key: converted[key] for key in ("target", "file", "bytes", "sha256")},
                        "target": target, "skeleton": converted["target"] + "_Skeleton",
                        "source_mesh_metadata": mesh_file, "cloths": cloths,
                        "live_cloth_sections": len(live), "source_cloth_assets": len(cloths),
                        "collision_meshes": list(dict.fromkeys(colliders)),
                        "collision_geometry_scale": 0.5,
                        "collision_geometry_outfit": {"character_id": context_part["character_id"], "outfit_id": context_part["outfit_id"]},
                        "collision_basis": "Stock UE capsule fit from same-outfit body/head geometry, with capsule radius and axial length scaled by 0.5 about each fitted center/orientation to form conservative inner collision shapes. Outer bounding fits overfill the visible hair/garments. Named-bone component-space reference transforms must match; original PHYS payloads are absent. This scale is an explicit viewer compatibility choice, not a recovered original parameter.",
                        "runtime_parts": [{"character_id": part["character_id"], "outfit_id": part["outfit_id"], "part_id": part["id"]} for part in matching_parts]})
        bindings.extend({"character_id": part["character_id"], "outfit_id": part["outfit_id"],
                         "part_id": part["id"], "source_mesh": converted["target"], "target": target}
                        for part in matching_parts)
    inactive = [row for row in outputs if not row["runtime_parts"]]
    outputs = [row for row in outputs if row["runtime_parts"]]
    return {"schema_version": 1, "status": "planned_native_chaos_vertex_cloth", "owner": OWNER,
            "viewer_plan_sha256": digest(viewer_path), "asset_index_sha256": digest(index_path),
            "source_manifest_sha256": digest(manifest_path), "meshes": outputs, "part_bindings": bindings,
            "inactive_source_meshes": [{**row, "authoring_status": "source_evidence_only_no_current_runtime_part"} for row in inactive],
            "counts": {"source_meshes": len(outputs) + len(inactive), "authored_meshes": len(outputs),
                       "source_cloth_assets": len(physical_rows), "authored_cloth_assets": sum(len(row["cloths"]) for row in outputs),
                       "live_source_LOD0_sections": sum(row["live_cloth_sections"] for row in outputs),
                       "runtime_part_bindings": len(bindings)},
            "limitations": [
                "All 25 exported physical meshes are retained; only source LOD0 sections can be bound because converted render meshes retain the highest exported LOD only.",
                "Actual Chaos per-vertex constraints, source pin distances, named-bone skinning and render mappings replace bone-chain approximations for these sections.",
                "Other UI parts have no exported Nv physical mesh and cannot be presented as recovered original cloth.",
                "Source PHYS collision payloads are missing. Collision capsules are an explicit stock geometry fit, not recovered original collision shapes.",
                "Serialized Nv parameters use UE5.8 migration; omitted fields use labeled stock legacy constructor defaults and Chaos mass/solver defaults. Private Nv constructor values and exact solver response are not recovered.",
                "Engine migration does not reproduce Nv compression/stretch limits, drag, self-collision stiffness/culling, or original stiffness-frequency constraints.",
                "Source MaxDistances are preserved verbatim. Stock Chaos additionally treats distances below 0.1 cm as kinematic; source zero-distance pins remain pinned, while some small nonzero source distances also become kinematic.",
                "Source inverse masses are retained in native physical data; Chaos computes particle masses from its stock density and minimum-mass settings rather than reproducing Nv per-vertex inverse masses."]}


if __name__ == "__main__":
    plan = make_plan()
    write(OUTPUT, plan)
    print(json.dumps({"plan": str(OUTPUT), **plan["counts"]}, indent=2))
