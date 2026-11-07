"""Place the existing seated Queen clips on the recovered preview throne.

Run in UE Editor with -run=pythonscript -script=Scripts/align_seated_queen.py.
Only the Catalog's two Queen outfit part transforms are saved; this does not
reimport geometry, materials or animations and needs no SourceAssets.

The imported clips are the seated Lobby pose, while the retained throne comes
from the Role preview scene. Their coordinates differ. This is a measured viewer
placement, not a recovered original actor transform: the throne's seat is about
Z=46.9 cm, Y=15 cm in mesh space; the seated hip is Z=58.506 cm, and a 45-degree
yaw aligns the pose with the throne's forward direction. Preserve the clip's
height and center the hips over that seat. All avatar parts use one transform.
"""
import json
import math
from pathlib import Path
import re

import unreal

PROJECT = Path(__file__).resolve().parent.parent
CATALOG = "/Game/Viewer/Data/DA_ViewerCatalog"
ENVIRONMENT = "/Game/Viewer/Environment/DA_Environment_Queen"


def asset_paths(text):
    return re.findall(r"/(?:Game|Engine)/[A-Za-z0-9_./]+\.[A-Za-z0-9_]+", text)


def soft_asset(struct, field):
    # Soft references can read as None until loaded; preserve their native paths.
    match = re.search(r'\b' + field + r'="([^"]+)"', struct.export_text())
    if not match:
        raise RuntimeError("Missing serialized reference: " + field)
    asset = unreal.load_asset(match.group(1))
    if not asset:
        raise RuntimeError("Cannot load: " + match.group(1))
    return asset


def xyz(vector):
    return [vector.x, vector.y, vector.z]


catalog = unreal.load_asset(CATALOG)
environment = unreal.load_asset(ENVIRONMENT)
if not catalog or not environment:
    raise RuntimeError("Catalog and Queen scene must already be imported.")
thrones = [mesh for mesh in environment.get_editor_property("meshes")
           if "SM_Lobby_Queen_Throne_01" in mesh.export_text()]
if len(thrones) != 1:
    raise RuntimeError("Expected one recovered throne.")
throne = thrones[0].get_editor_property("transform")
if (abs(throne.rotation.x) + abs(throne.rotation.y) + abs(throne.rotation.z) > .0001
        or abs(abs(throne.rotation.w) - 1.) > .0001
        or any(abs(value - 1.) > .0001 for value in xyz(throne.scale3d))):
    raise RuntimeError("Throne basis changed; remeasure its seat before aligning.")

characters = list(catalog.get_editor_property("characters"))
report = {"placement_basis": "measured_seated_clip_to_preview_throne_compatibility",
          "original_serialized_actor_transform_claimed": False,
          "throne_translation_cm": xyz(throne.translation),
          "seat_mesh_point_cm": [0., 15., 46.9], "outfits": []}
for character in characters:
    if str(character.get_editor_property("id")) != "queen":
        continue
    outfits = list(character.get_editor_property("outfits"))
    for outfit in outfits:
        parts = list(outfit.get_editor_property("parts"))
        body = next(part for part in parts if str(part.get_editor_property("id")) == "14401010001")
        mesh = soft_asset(body, "Mesh")
        animation = soft_asset(body, "Animation")
        options = unreal.AnimPoseEvaluationOptions()
        options.set_editor_property("evaluation_type", unreal.AnimDataEvalType.COMPRESSED)
        options.set_editor_property("optional_skeletal_mesh", mesh)
        pose = unreal.AnimPoseExtensions.get_anim_pose_at_time(animation, 0., options)
        hip = unreal.AnimPoseExtensions.get_bone_pose(pose, "b_hips", unreal.AnimPoseSpaces.WORLD).translation
        yaw = 45.
        radians = math.radians(yaw)
        rotated_x = hip.x * math.cos(radians) - hip.y * math.sin(radians)
        rotated_y = hip.x * math.sin(radians) + hip.y * math.cos(radians)
        target = unreal.Vector(throne.translation.x, throne.translation.y + 15., hip.z)
        offset = unreal.Vector(target.x - rotated_x, target.y - rotated_y, 0.)
        placement = unreal.Transform(location=offset, rotation=unreal.Rotator(pitch=0., yaw=yaw, roll=0.),
                                     scale=unreal.Vector(1., 1., 1.))
        actual_hip = unreal.MathLibrary.transform_location(placement, hip)
        hip_error = math.sqrt(sum((a - b) ** 2 for a, b in zip(xyz(actual_hip), xyz(target))))
        rotation = placement.rotation
        if (hip_error > .001 or abs(rotation.x) > .0001 or abs(rotation.y) > .0001
                or abs(rotation.z - math.sin(radians * .5)) > .0001
                or abs(rotation.w - math.cos(radians * .5)) > .0001):
            raise RuntimeError("Applied placement does not match the measured yaw/seat target.")
        for part in parts:
            before_paths = asset_paths(part.export_text())
            part.set_editor_property("relative_transform", placement)
            if asset_paths(part.export_text()) != before_paths:
                raise RuntimeError("Alignment changed an asset reference.")
        outfit.set_editor_property("parts", parts)
        shared = all(part.get_editor_property("relative_transform") == placement for part in parts)
        above_seat = target.z - (throne.translation.z + 46.9)
        if not shared or not 8. < above_seat < 18.:
            raise RuntimeError("Queen pose no longer matches the measured throne seat.")
        report["outfits"].append({"id": str(outfit.get_editor_property("id")),
            "part_count": len(parts), "yaw_degrees": yaw, "translation_cm": xyz(offset),
            "animated_hip_before_cm": xyz(hip), "animated_hip_after_cm": xyz(actual_hip),
            "applied_rotation_xyzw": [rotation.x, rotation.y, rotation.z, rotation.w],
            "applied_hip_target_error_cm": hip_error,
            "hip_above_seat_cm": above_seat, "all_parts_share_transform": shared})
    character.set_editor_property("outfits", outfits)
if len(report["outfits"]) != 2:
    raise RuntimeError("Expected both existing Queen outfits.")
catalog.set_editor_property("characters", characters)
if not unreal.EditorAssetLibrary.save_loaded_asset(catalog):
    raise RuntimeError("Catalog could not be saved.")
report["status"] = "saved_requires_device_visual_validation"
output = PROJECT / "Saved/QueenThroneAlignment.json"
output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
unreal.log("Queen throne alignment saved: " + str(output))
