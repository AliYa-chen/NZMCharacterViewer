"""Import decoded UE bone samples against existing mesh skeletons.

Uses the editor-only ViewerAnimationImportLibrary bridge. Before importing new
clips, -- supplied through a job JSON -- can compare a decoded known clip with
its existing FBX animation. No source meshes or textures are reimported.

Set VIEWER_CURVE_JOB to an absolute JSON path before running this script with
the UE Python commandlet. Jobs contain mesh, curves, destination, and optional
compare_animation/sample_times fields. The report is written under Saved.
"""
import json
import math
import os
from pathlib import Path

import unreal


def distance(a, b):
    return math.sqrt(sum((getattr(a, axis) - getattr(b, axis)) ** 2 for axis in "xyz"))


def angle(a, b):
    dot = sum(getattr(a, axis) * getattr(b, axis) for axis in "xyzw")
    return math.degrees(2. * math.acos(min(1., abs(dot))))


def compare(animation, reference, mesh, times):
    raw_options = unreal.AnimPoseEvaluationOptions()
    raw_options.set_editor_property("evaluation_type", unreal.AnimDataEvalType.RAW)
    raw_options.set_editor_property("optional_skeletal_mesh", mesh)
    old_options = unreal.AnimPoseEvaluationOptions()
    old_options.set_editor_property("evaluation_type", unreal.AnimDataEvalType.COMPRESSED)
    old_options.set_editor_property("optional_skeletal_mesh", mesh)
    samples = []
    for time in times:
        new_pose = unreal.AnimPoseExtensions.get_anim_pose_at_time(animation, time, raw_options)
        old_pose = unreal.AnimPoseExtensions.get_anim_pose_at_time(reference, time, old_options)
        names = unreal.AnimPoseExtensions.get_bone_names(new_pose)
        differences = []
        for name in names:
            new = unreal.AnimPoseExtensions.get_bone_pose(new_pose, name, unreal.AnimPoseSpaces.WORLD)
            old = unreal.AnimPoseExtensions.get_bone_pose(old_pose, name, unreal.AnimPoseSpaces.WORLD)
            differences.append({"bone": str(name), "position_error_cm": distance(new.translation, old.translation),
                                "rotation_error_degrees": angle(new.rotation, old.rotation)})
        samples.append({"time": time, "bones": len(differences),
                        "max_position_error_cm": max(x["position_error_cm"] for x in differences),
                        "max_rotation_error_degrees": max(x["rotation_error_degrees"] for x in differences),
                        "worst_positions": sorted(differences, key=lambda x: x["position_error_cm"], reverse=True)[:12],
                        "landmarks": [x for x in differences if x["bone"].lower() in ("b_head", "b_hips", "b_root")]})
    return samples


def run():
    job_file = Path(os.environ["VIEWER_CURVE_JOB"]).resolve()
    document = json.loads(job_file.read_text())
    results = []
    library = unreal.EditorAssetLibrary
    for job in document["jobs"]:
        mesh = unreal.load_asset(job["mesh"])
        if not isinstance(mesh, unreal.SkeletalMesh):
            raise RuntimeError("Missing target mesh: " + job["mesh"])
        destination = job["destination"]
        if library.does_asset_exist(destination):
            raise RuntimeError("Curve importer requires a new destination: " + destination)
        folder, name = destination.rsplit("/", 1)
        factory = unreal.AnimSequenceFactory()
        factory.set_editor_property("target_skeleton", mesh.get_editor_property("skeleton"))
        factory.set_editor_property("preview_skeletal_mesh", mesh)
        animation = unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, folder, unreal.AnimSequence, factory)
        detail = unreal.ViewerAnimationImportLibrary.populate_animation_from_curves(animation, mesh, str(Path(job["curves"]).resolve()))
        # UE exposes a bool return plus one output parameter as output-or-None.
        if detail is None:
            library.delete_asset(destination)
            raise RuntimeError("Curve import failed; inspect the editor log.")
        result = {"destination": destination, "source": job["curves"], "import": json.loads(detail)}
        if job.get("compare_animation"):
            reference = unreal.load_asset(job["compare_animation"])
            result["comparison"] = compare(animation, reference, mesh, job.get("sample_times", [0., 1., 2.]))
            result["comparison_passed"] = all(sample["max_position_error_cm"] <= job.get("position_tolerance_cm", .25)
                                              for sample in result["comparison"])
        if job.get("save", True):
            if not library.save_loaded_asset(animation):
                raise RuntimeError("Failed to save: " + destination)
        results.append(result)
        if job.get("delete_after_check"):
            if not library.delete_asset(destination):
                raise RuntimeError("Could not remove calibration asset: " + destination)
    report = Path(document.get("report", str(job_file.with_suffix(".report.json"))))
    report.write_text(json.dumps({"results": results}, ensure_ascii=False, indent=2))
    unreal.log("Curve import report: " + str(report))


run()
