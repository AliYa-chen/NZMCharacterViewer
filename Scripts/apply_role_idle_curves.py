"""Import verified normal-role idle curves into existing part skeletons.

Run in the UE Python commandlet after decoding original animation curves with
the resource exporter. Sources are local Saved/EntranceImport inputs; normal
clone/build uses the saved Content assets and needs neither these inputs nor
SourceAssets. This script changes only each selected part's Animation field.
It preserves materials, mesh, entry clip, visibility, loop and transforms.

The initial scope is the five visually verified normal-role outfits: Angela,
Molly, Nanami, Naimei and Angel. Queen remains seated as requested, and Lucia /
Bianca are left unchanged until their original role-page state is verified.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import re

import unreal

PROJECT = Path(__file__).resolve().parent.parent
IMPORT_ROOT = Path(os.environ.get("VIEWER_ROLE_IDLE_ROOT", str(PROJECT / "Saved/EntranceImport"))).resolve()
MAPPING_FILE = IMPORT_ROOT / "normal_role_idle_mapping.json"
CATALOG_PATH = "/Game/Viewer/Data/DA_ViewerCatalog"
SELECTED = {("angela", "angela"), ("molly", "molly"), ("molly", "mollynanami"),
            ("naimei", "suit_a"), ("angel", "angel")}
LANDMARKS = {"b_root", "b_hips", "b_head", "b_leftankle", "b_rightankle", "b_lefttoe", "b_righttoe"}
LIBRARY = unreal.EditorAssetLibrary
TOOLS = unreal.AssetToolsHelpers.get_asset_tools()
REPORT = {"status": "started", "mapping": str(MAPPING_FILE), "parts": [], "outfits": [],
          "excluded": ["queen/queen", "queen/queen_masked", "angel/angellucia", "naimei/naimeibianca"],
          "coordinate_basis": "original UE local bone samples mapped to each existing imported mesh reference basis",
          "old_animations_deleted": False, "catalog_fields_other_than_animation_preserved": False,
          "visual_device_validation_still_required": True}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def soft_path(struct, field):
    match = re.search(r'\b' + field + r'="([^"]+)"', struct.export_text())
    if not match:
        raise RuntimeError("Missing serialized " + field + " in " + str(struct.get_editor_property("id")))
    return match.group(1)


def without_animation(part):
    return re.sub(r'(?<![A-Za-z])Animation="[^"]+"', 'Animation="<selected_role_idle>"', part.export_text())


def values(vector):
    return [float(getattr(vector, axis)) for axis in "xyz"]


def landmarks(animation, mesh, time, reference=False):
    options = unreal.AnimPoseEvaluationOptions()
    options.set_editor_property("evaluation_type", unreal.AnimDataEvalType.RAW)
    options.set_editor_property("optional_skeletal_mesh", mesh)
    pose = unreal.AnimPoseExtensions.get_anim_pose_at_time(animation, time, options)
    result = {}
    for name in unreal.AnimPoseExtensions.get_bone_names(pose):
        if str(name).lower() not in LANDMARKS:
            continue
        transform = (unreal.AnimPoseExtensions.get_ref_bone_pose(pose, name, unreal.AnimPoseSpaces.WORLD)
                     if reference else unreal.AnimPoseExtensions.get_bone_pose(pose, name, unreal.AnimPoseSpaces.WORLD))
        point = values(transform.translation)
        if not all(math.isfinite(value) for value in point):
            raise RuntimeError("Animation has a nonfinite landmark: " + str(name))
        result[str(name)] = point
    return result


def make_animation(mesh, curves, destination):
    source_hash = digest(curves)
    if LIBRARY.does_asset_exist(destination):
        animation = LIBRARY.load_asset(destination)
        if (not isinstance(animation, unreal.AnimSequence)
                or animation.get_editor_property("skeleton") != mesh.get_editor_property("skeleton")
                or LIBRARY.get_metadata_tag(animation, "ViewerSourceCurveSHA256") != source_hash):
            raise RuntimeError("Existing RoleIdle destination does not match this exact source/skeleton: " + destination)
        detail = json.loads(LIBRARY.get_metadata_tag(animation, "ViewerCurveImportReport"))
        return animation, detail, False
    folder, name = destination.rsplit("/", 1)
    factory = unreal.AnimSequenceFactory()
    factory.set_editor_property("target_skeleton", mesh.get_editor_property("skeleton"))
    factory.set_editor_property("preview_skeletal_mesh", mesh)
    animation = TOOLS.create_asset(name, folder, unreal.AnimSequence, factory)
    if not isinstance(animation, unreal.AnimSequence):
        raise RuntimeError("Animation asset could not be created: " + destination)
    detail = unreal.ViewerAnimationImportLibrary.populate_animation_from_curves(animation, mesh, str(curves))
    if detail is None:
        LIBRARY.delete_asset(destination)
        raise RuntimeError("Decoded RoleIdle curve import failed for " + destination)
    LIBRARY.set_metadata_tag(animation, "ViewerSourceCurveSHA256", source_hash)
    LIBRARY.set_metadata_tag(animation, "ViewerSourceCurveFile", str(curves.relative_to(IMPORT_ROOT)))
    LIBRARY.set_metadata_tag(animation, "ViewerCurveImportReport", detail)
    LIBRARY.set_metadata_tag(animation, "ViewerState", "normal CharacterMainView RoleIdle (9); original UIAnimSet rule")
    if not LIBRARY.save_loaded_asset(animation):
        raise RuntimeError("RoleIdle animation did not save: " + destination)
    return animation, json.loads(detail), True


mapping = json.loads(MAPPING_FILE.read_text())
sources = {(row["character"], row["outfit"]): row for row in mapping["outfits"]}
if not SELECTED.issubset(sources):
    raise RuntimeError("Verified RoleIdle mapping is incomplete.")
# Fail before changing assets when any requested original decoded source is absent.
curves_by_outfit = {}
for pair in SELECTED:
    package = sources[pair]["source_package"].removeprefix("/Game/")
    curves = IMPORT_ROOT / "curves/NZM/Content" / (package + ".curves.json")
    if not curves.is_file():
        raise RuntimeError("Decode this verified original clip first: " + str(curves))
    curves_by_outfit[pair] = curves

catalog = LIBRARY.load_asset(CATALOG_PATH)
if not isinstance(catalog, unreal.ViewerCatalog):
    raise RuntimeError("Viewer Catalog is not available.")
characters = list(catalog.get_editor_property("characters"))
untouched_before = {}
selected_before = {}
for character in characters:
    cid = str(character.get_editor_property("id"))
    for outfit in character.get_editor_property("outfits"):
        pair = (cid, str(outfit.get_editor_property("id")))
        if pair not in SELECTED:
            untouched_before[pair] = outfit.export_text()
        else:
            selected_before[pair] = [without_animation(part) for part in outfit.get_editor_property("parts")]

updated = set()
for character in characters:
    cid = str(character.get_editor_property("id"))
    outfits = list(character.get_editor_property("outfits"))
    for outfit in outfits:
        oid = str(outfit.get_editor_property("id"))
        pair = (cid, oid)
        if pair not in SELECTED:
            continue
        curves = curves_by_outfit[pair]
        parts = list(outfit.get_editor_property("parts"))
        for part in parts:
            pid = str(part.get_editor_property("id"))
            mesh_path = soft_path(part, "Mesh")
            old_animation_path = soft_path(part, "Animation")
            mesh = LIBRARY.load_asset(mesh_path)
            if not isinstance(mesh, unreal.SkeletalMesh):
                raise RuntimeError("Cannot retain existing mesh: " + mesh_path)
            before = without_animation(part)
            folder = old_animation_path.split(".", 1)[0].rsplit("/", 1)[0]
            destination = folder + "/A_" + pid + "_RoleIdle"
            animation, detail, created = make_animation(mesh, curves, destination)
            part.set_editor_property("animation", animation)
            if without_animation(part) != before:
                raise RuntimeError("RoleIdle import changed an unrelated Catalog part field.")
            length = animation.get_play_length()
            samples = [{"time": time, "bones_component_cm": landmarks(animation, mesh, time)}
                       for time in [0., min(1., length * .5), max(0., length - 1. / 30.)]]
            reference = landmarks(animation, mesh, 0., reference=True)
            relative = part.get_editor_property("relative_transform")
            placed = {name: values(unreal.MathLibrary.transform_location(relative, unreal.Vector(*point)))
                      for name, point in samples[0]["bones_component_cm"].items()}
            REPORT["parts"].append({"character": cid, "outfit": oid, "part": pid, "mesh": mesh_path,
                "old_animation": old_animation_path, "animation": animation.get_path_name(),
                "source_package": sources[pair]["source_package"], "curve_sha256": digest(curves),
                "import": detail, "created": created, "reference_bones_component_cm": reference,
                "samples": samples, "first_pose_after_part_transform_cm": placed,
                "unrelated_part_fields_preserved": True})
        outfit.set_editor_property("parts", parts)
        updated.add(pair)
        REPORT["outfits"].append({"character": cid, "outfit": oid, "parts": len(parts),
                                  "source_package": sources[pair]["source_package"]})
    character.set_editor_property("outfits", outfits)
if updated != SELECTED:
    raise RuntimeError("Not every verified outfit was found in the existing Catalog.")

for character in characters:
    cid = str(character.get_editor_property("id"))
    for outfit in character.get_editor_property("outfits"):
        pair = (cid, str(outfit.get_editor_property("id")))
        if pair in untouched_before and outfit.export_text() != untouched_before[pair]:
            raise RuntimeError("An excluded outfit changed during RoleIdle binding.")
        if pair in selected_before and [without_animation(part) for part in outfit.get_editor_property("parts")] != selected_before[pair]:
            raise RuntimeError("A selected outfit's unrelated part fields changed.")
catalog.set_editor_property("characters", characters)
if not LIBRARY.save_loaded_asset(catalog):
    raise RuntimeError("Verified RoleIdle Catalog bindings could not be saved.")
REPORT["catalog_fields_other_than_animation_preserved"] = True
REPORT["status"] = "saved_requires_device_visual_validation"
report_path = PROJECT / "Saved/RoleIdleCurveImport.json"
report_path.write_text(json.dumps(REPORT, ensure_ascii=False, indent=2))
unreal.log("RoleIdle curve import: " + str(report_path))
