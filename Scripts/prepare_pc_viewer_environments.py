"""Author sourced PC runtime environments without changing original assets.

--plan-only works in ordinary Python. Execute in UE after building the new
ViewerEnvironment source-mode fields to save only /Game/PCAssets/ViewerEnvironments.
The original 14 PC candidates, stage assets, Catalog and maps remain unchanged.
"""
import argparse
from collections import Counter
import importlib.util
import json
import math
from pathlib import Path
import re
import traceback


ROOT = Path(__file__).resolve().parent.parent
SPECIFICATION = importlib.util.spec_from_file_location("pc_viewer_environment_policy", ROOT / "Scripts/pc_viewer_environments.py")
POLICY = importlib.util.module_from_spec(SPECIFICATION)
SPECIFICATION.loader.exec_module(POLICY)
RENDER_FIELDS = ("use_source_rendering", "has_mobile_post_process", "post_process", "mobile_post_process",
                 "source_console_variables", "mobile_source_console_variables", "sky_cast_shadows", "sky_cubemap_angle",
                 "sky_volumetric_scattering_intensity", "source_post_enabled", "source_post_unbound", "source_post_priority",
                 "source_post_blend_weight", "use_source_fog", "fog_transform", "fog_density", "fog_height_falloff",
                 "fog_start_distance", "fog_color", "fog_max_opacity", "source_volumetric_fog",
                 "fog_volumetric_scattering_distribution", "fog_volumetric_distance", "enable_fog_scattering",
                 "sky_intensity", "sky_color", "use_source_camera", "source_camera_transform", "camera_horizontal_fov",
                 "character_lighting_channel_mask", "model_transform",
                 "reference_aspect_ratio", "camera_distance", "camera_height", "camera_pitch", "camera_yaw",
                 "preserve_model_origin", "frame_from_head_bone", "reconstruction_notes")


def serialized(value):
    if hasattr(value, "get_path_name"):
        return value.get_path_name()
    if hasattr(value, "export_text"):
        return value.export_text()
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if hasattr(value, "items"):
        return {str(key): serialized(item) for key, item in value.items()}
    try:
        return [serialized(item) for item in value]
    except TypeError:
        return str(value)


def snapshot(asset):
    return {**{key: serialized(asset.get_editor_property(key)) for key in RENDER_FIELDS},
            "lights": [light.export_text() for light in asset.get_editor_property("lights")],
            "meshes": [mesh.export_text() for mesh in asset.get_editor_property("meshes")],
            "cubemap": serialized(asset.get_editor_property("cubemap"))}


def snapshots_equal(actual, expected):
    def canonical(value):
        if isinstance(value, str):
            # UE save/load canonicalizes quaternion negative zero. It carries
            # no transform difference; retain all other serialized characters.
            return re.sub(r"(?<==)-0(?:\.0+)(?=[,)])", "0.000000", value)
        if isinstance(value, dict):
            return {key: canonical(item) for key, item in value.items()}
        if isinstance(value, list):
            return [canonical(item) for item in value]
        return value
    return canonical(actual) == canonical(expected)


def transform(ue, values):
    result = ue.Transform()
    result.translation = ue.Vector(*values["location_cm"])
    result.rotation = ue.Quat(*values["rotation_xyzw"])
    result.scale3d = ue.Vector(*values["scale"])
    return result


def vector(ue, values, kind):
    axes = "XYZW" if kind == "vector4" else "RGBA"
    defaults = (0, 0, 0, 1)
    numbers = [float(values.get(axis, defaults[index])) for index, axis in enumerate(axes)]
    return ue.Vector4(*numbers) if kind == "vector4" else ue.LinearColor(*numbers)


def compatible_light_values(source):
    values = {**source["mapped"], **source["extended_mapped"]}
    adaptations = []
    if "specular_scale" in values:
        original = float(values["specular_scale"])
        if not math.isfinite(original):
            raise ValueError("Source SpecularScale is not finite")
        supported = max(0.0, min(1.0, original))
        if supported != original:
            values["specular_scale"] = supported
            adaptations.append({"source_property": "SpecularScale", "target_property": "specular_scale",
                "source_value": original, "target_value": supported,
                "reason": "Stock UE5.8 clamps both the public component setter and actual light shader parameters to [0,1]; the fork's larger highlight multiplier is not reproduced by bypassing the setter.",
                "target_engine_basis": [
                    "Engine/Source/Runtime/Engine/Private/Components/LightComponent.cpp:386 (FLightRenderParameters::MakeShaderParameters)",
                    "Engine/Source/Runtime/Engine/Private/Components/LightComponent.cpp:1370 (ULightComponent::SetSpecularScale)"]})
    return values, adaptations


def postprocess(ue, source_volumes, asset_plan, record, platform):
    settings = ue.PostProcessSettings()
    if len(source_volumes) != 1:
        raise ValueError("Expected exactly one separately mapped source volume")
    source = source_volumes[0]
    applied = []
    for row in source["mapped"]:
        key, value = row["target_property"], row["value"]
        if row["value_kind"] in ("vector4", "linear_color"):
            value = vector(ue, value, row["value_kind"])
        settings.set_editor_property(key, value)
        settings.set_editor_property("override_" + key, True)
        applied.append({"source_field": row["field"], "source_branch": row["source_branch"], "target_property": key,
                        "value": serialized(settings.get_editor_property(key)), "override": True})
    # Additional source values have stock equivalents, but only explicit true
    # override/value pairs may activate them. No absent fork defaults are filled.
    additional = {"DepthOfFieldFocalRegion": "depth_of_field_focal_region",
                  "DepthOfFieldNearTransitionRegion": "depth_of_field_near_transition_region",
                  "DepthOfFieldFarTransitionRegion": "depth_of_field_far_transition_region",
                  "BloomDirtMaskIntensity": "bloom_dirt_mask_intensity", "GrainIntensity": "film_grain_intensity"}
    object_fields = {"BloomDirtMask": "bloom_dirt_mask"}
    omitted = []
    for row in source["omitted"]:
        if row["evidence_state"] != "value_and_explicit_override":
            omitted.append(row)
            continue
        if row["field"] in additional:
            key = additional[row["field"]]
            settings.set_editor_property(key, row["value"])
            settings.set_editor_property("override_" + key, True)
            applied.append({"source_field": row["field"], "source_branch": row["source_branch"], "target_property": key,
                            "value": serialized(settings.get_editor_property(key)), "override": True,
                            "interpretation": "Stock UE equivalent field; original fork response remains unverified."})
        elif row["field"] in object_fields:
            source_package = POLICY.import_bridge().source_package(row["value"])
            target = asset_plan.get("packages", {}).get(source_package)
            value = ue.load_asset(target) if target else None
            if isinstance(value, ue.Texture2D):
                key = object_fields[row["field"]]
                settings.set_editor_property(key, value)
                settings.set_editor_property("override_" + key, True)
                applied.append({"source_field": row["field"], "target_property": key, "value": value.get_path_name(), "override": True})
            else:
                omitted.append({**row, "runtime_omission": "Referenced source texture is not an imported Texture2D; not invented."})
        else:
            omitted.append(row)
    # The exported fork's zero Min/Max cannot establish luminance or EV100.
    # Keep this compatibility choice explicit instead of rebranding EV0 as source.
    for key, value in (("auto_exposure_method", ue.AutoExposureMethod.AEM_MANUAL),
                       ("auto_exposure_apply_physical_camera_exposure", False), ("auto_exposure_bias", 0.0)):
        settings.set_editor_property(key, value)
        settings.set_editor_property("override_" + key, True)
    settings.set_editor_property("override_dynamic_global_illumination_method", True)
    settings.set_editor_property("dynamic_global_illumination_method", ue.DynamicGlobalIlluminationMethod.NONE)
    settings.set_editor_property("override_reflection_method", True)
    settings.set_editor_property("reflection_method", ue.ReflectionMethod.SCREEN_SPACE)
    record[platform] = {"source_volume": source["source_json"], "mapped": applied, "omitted": omitted,
                        "compatibility": {"exposure": "Stock manual EV0, physical exposure disabled; source Min/Max=0 units/semantics are unknown and not converted.",
                            "global_illumination": "None: private source PRT/GI is unavailable; Lumen is not substituted as recovered source GI.",
                            "reflections": "Stock screen-space reflection method is an approximation supported by historical source SSR requests; original capture/normalization is not reconstructed.",
                            "depth_of_field": "Explicit source distance/transition values retained where supported. Missing Scale override value is not invented; no forced Fstop or auto-refocus."}}
    return settings


def apply(ue, plan):
    library = ue.EditorAssetLibrary
    tools = ue.AssetToolsHelpers.get_asset_tools()
    asset_plan = POLICY.read(POLICY.WORK / "asset_plan.json")
    index = POLICY.read(ROOT / "Docs/PCAssetConversion/asset_index.json")
    indexed = {row["target"]: row for row in index["assets"]}
    scenes = {row["id"]: row for row in plan["scenes"]}
    report = {"schema_version": 1, "owner": POLICY.OWNER, "status": "running", "plan_sha256": POLICY.digest(POLICY.PLAN_PATH),
              "environments": [], "routes": plan["routes"], "protected_assets": [], "errors": [],
              "catalog_saved": False, "maps_saved": False, "original_candidates_saved": False,
              "limitations": plan["limitations"], "runtime_visual_validation": "required_separately"}
    protected = {}
    for spec in plan["environments"]:
        for reference in (spec["geometry_source"], scenes[spec["source_scene"]]["candidate_desktop"],
                          scenes[spec["source_scene"]]["candidate_mobile"]):
            protected[reference] = POLICY.fingerprint(reference)
    protected["/Game/Viewer/Data/DA_ViewerCatalog"] = POLICY.fingerprint("/Game/Viewer/Data/DA_ViewerCatalog")
    try:
        for spec in plan["environments"]:
            source = scenes[spec["source_scene"]]
            target = spec["target"]
            if not target.startswith(POLICY.TARGET_ROOT + "/"):
                raise ValueError("Runtime environment target outside owned directory")
            base = ue.load_asset(spec["geometry_source"])
            if not isinstance(base, ue.ViewerEnvironment) or not base.get_editor_property("meshes"):
                raise ValueError("Expected nonempty retained stage geometry: " + spec["geometry_source"])
            existing = library.does_asset_exist(target)
            if existing:
                asset = ue.load_asset(target)
                if not isinstance(asset, ue.ViewerEnvironment) or library.get_metadata_tag(asset, "PCViewerEnvironmentOwner") != POLICY.OWNER:
                    raise ValueError("Refusing to overwrite an unowned environment: " + target)
            else:
                directory, name = target.rsplit("/", 1)
                library.make_directory(directory)
                asset = tools.duplicate_asset(name, directory, base)
            if not isinstance(asset, ue.ViewerEnvironment):
                raise RuntimeError("Could not create sourced runtime environment")
            # Always copy exact retained geometry/framing again on rerun.
            for field in ("meshes", "camera_horizontal_fov", "reference_aspect_ratio", "camera_distance", "camera_height",
                          "camera_pitch", "camera_yaw", "use_source_camera", "source_camera_transform", "preserve_model_origin", "frame_from_head_bone"):
                asset.set_editor_property(field, base.get_editor_property(field))
            row = {"target": target, "id": spec["id"], "active": spec["active"], "source_scene": spec["source_scene"],
                   "source_package": source["source_package"], "geometry_source": spec["geometry_source"],
                   "geometry_basis": "Retained existing editable stage geometry/transforms; PC background meshes were not exported.",
                   "lights": [], "postprocess": {}, "camera": {"policy": spec["camera_policy"],
                       "exact_female_camera_evidence": source["female_cameras"],
                       "retained_camera_is_original_online_route": False}, "unsupported_components": source["unsupported_components"]}
            asset.set_editor_property("model_transform", transform(ue, spec["model_placement"]["transform"]))
            row["model_placement"] = spec["model_placement"]
            lights = []
            for item in source["lights"]:
                if not item["visibility"]["included"] or "kind" not in item:
                    continue
                light = ue.ViewerEnvironmentLight()
                light.set_editor_property("id", POLICY.import_bridge().name(item["name"]) + "_" + item["id"].rsplit(".", 1)[-1])
                light.set_editor_property("kind", item["kind"])
                pose = item["transform"]["world_candidate"]
                light.set_editor_property("location", ue.Vector(*pose["location_cm"]))
                light.set_editor_property("rotation", ue.Quat(*pose["rotation_xyzw"]).rotator())
                values, runtime_adaptations = compatible_light_values(item)
                for key, value in values.items():
                    light.set_editor_property(key, ue.LinearColor(*value) if key == "color" else value)
                light.set_editor_property("scale", ue.Vector(*item["runtime_scale"]))
                light.set_editor_property("mobility", item["runtime_mobility"])
                light.set_editor_property("visible", True)
                light.set_editor_property("affects_world", True)
                lights.append(light)
                row["lights"].append({"id": str(light.get_editor_property("id")), "source_identity": item["id"],
                    "source_mobility": item["source_mobility"], "runtime_mobility": item["runtime_mobility"],
                    "runtime_mobility_basis": item["runtime_mobility_basis"], "unknown_fields": item["unknown"],
                    "runtime_adaptations": runtime_adaptations,
                    "unsupported_fields": item["unsupported"], "transform_unknowns": item["transform"]["unknown"]})
            asset.set_editor_property("lights", lights)
            skies = [sky for sky in source["skies"] if sky["visibility"]["included"]]
            if len(skies) != 1 or not skies[0].get("target"):
                raise RuntimeError("Expected one explicitly selected imported source SkyLight")
            sky = skies[0]
            cube = ue.load_asset(sky["target"])
            if not isinstance(cube, ue.TextureCube):
                raise RuntimeError("Source HDR did not load as TextureCube")
            protected[sky["target"]] = POLICY.fingerprint(sky["target"])
            if protected[sky["target"]]["sha256"] != indexed[sky["target"]]["sha256"]:
                raise RuntimeError("Source HDR differs from immutable conversion index")
            asset.set_editor_property("cubemap", cube)
            asset.set_editor_property("sky_intensity", sky["mapped"].get("sky_intensity", 1.0))
            asset.set_editor_property("sky_color", ue.LinearColor(*sky["mapped"].get("sky_color", [1, 1, 1, 1])))
            for key, default in (("sky_cast_shadows", False), ("sky_cubemap_angle", 0.0), ("sky_volumetric_scattering_intensity", 1.0)):
                asset.set_editor_property(key, sky["mapped_extra"].get(key, default))
            row["sky"] = {"target": cube.get_path_name(), "source_package": sky["source_package"],
                          "source_intensity_serialized": "sky_intensity" in sky["mapped"],
                          "fallback_intensity_if_unknown": 1.0, "source_normalization": sky["source_normalization"],
                          "normalization_scope": "Stock UE TextureCube integration; original fork normalization/average-brightness is retained as evidence only."}
            desktop = postprocess(ue, source["desktop_postprocess"], asset_plan, row["postprocess"], "Desktop")
            mobile = postprocess(ue, source["mobile_postprocess"], asset_plan, row["postprocess"], "Mobile")
            asset.set_editor_property("post_process", desktop)
            asset.set_editor_property("mobile_post_process", mobile)
            asset.set_editor_property("has_mobile_post_process", True)
            asset.set_editor_property("use_source_rendering", True)
            asset.set_editor_property("character_lighting_channel_mask", 1)
            row["character_lighting_channels"] = {"mask": 1,
                "basis": "Stock channel0 compatibility fallback; source character channel settings were not serialized. No extra channel1 fill is invented."}
            asset.set_editor_property("source_console_variables", source["source_console_variables"])
            asset.set_editor_property("mobile_source_console_variables", source["mobile_source_console_variables"])
            volume = source["desktop_postprocess"][0]["source_volume_properties"]
            for name, source_name, default in (("source_post_enabled", "bEnabled", True), ("source_post_priority", "Priority", 0.0),
                                               ("source_post_blend_weight", "BlendWeight", 1.0)):
                asset.set_editor_property(name, volume[source_name].get("value", default))
            asset.set_editor_property("source_post_unbound", True)
            row["postprocess"]["volume_scope"] = {"source_properties": volume,
                "unbound_runtime": "Explicit source true retained; missing bounds/unbound evidence uses one unbound viewer-stage compatibility volume."}
            asset.set_editor_property("enable_fog_scattering", False)
            asset.set_editor_property("use_source_fog", bool(source["fog"]))
            if source["fog"]:
                if len(source["fog"]) != 1:
                    raise RuntimeError("Multiple source fog components cannot be silently combined")
                fog = source["fog"][0]
                # Target component defaults, recorded as compatibility fallback.
                values = {"fog_density": .02, "fog_height_falloff": .2, "fog_start_distance": 0.0,
                          "fog_max_opacity": 1.0, "fog_volumetric_scattering_distribution": .2, "fog_volumetric_distance": 6000.0,
                          **fog["mapped"]}
                for key, value in values.items():
                    asset.set_editor_property(key, value)
                color = fog["source_properties"].get("FogInscatteringColor", {"R": 0, "G": 0, "B": 0, "A": 1})
                asset.set_editor_property("fog_color", vector(ue, color, "linear_color"))
                asset.set_editor_property("fog_transform", transform(ue, fog["transform"]["world_candidate"]))
                asset.set_editor_property("source_volumetric_fog", fog["enable_volumetric"])
                row["fog"] = fog
            if len(source["female_cameras"]) == 1:
                camera = source["female_cameras"][0]
                props = camera["properties"]
                filmback = props.get("Filmback", {})
                width, height = filmback.get("SensorWidth", 36.0), filmback.get("SensorHeight", 20.25)
                focal = props.get("CurrentFocalLength")
                if focal and focal > 0:
                    asset.set_editor_property("camera_horizontal_fov", math.degrees(2 * math.atan(width / (2 * focal))))
                    asset.set_editor_property("reference_aspect_ratio", width / height)
                    asset.set_editor_property("source_camera_transform", transform(ue, camera["transform"]["world_candidate"]))
                    asset.set_editor_property("use_source_camera", True)
                    asset.set_editor_property("preserve_model_origin", True)
                    row["camera"]["policy"] = "Exact serialized female tag, parent/component transform and filmback/focal FOV; original runtime/sequence route still unresolved."
            notes = {"owner": POLICY.OWNER, "source_scene": spec["source_scene"], "active": spec["active"],
                     "plan": "Saved/PCViewerIntegration/viewer_environment_plan.json", "limitations": plan["limitations"],
                     "source_route_established": False}
            asset.set_editor_property("reconstruction_notes", json.dumps(notes, ensure_ascii=False, sort_keys=True))
            library.set_metadata_tag(asset, "PCViewerEnvironmentOwner", POLICY.OWNER)
            if not library.save_loaded_asset(asset, False):
                raise RuntimeError("Sourced runtime environment did not save")
            row.update(status="passed", asset=POLICY.fingerprint(target), snapshot=snapshot(asset))
            report["environments"].append(row)
            POLICY.write(POLICY.REPORT_PATH, report)
        report["protected_assets"] = [protected[key] for key in sorted(protected)
                                      if key != "/Game/Viewer/Data/DA_ViewerCatalog"]
        report["authoring_protected_catalog"] = {
            "before": protected["/Game/Viewer/Data/DA_ViewerCatalog"],
            "after": POLICY.fingerprint("/Game/Viewer/Data/DA_ViewerCatalog")}
        report["authoring_protected_catalog"]["unchanged"] = report["authoring_protected_catalog"]["before"] == report["authoring_protected_catalog"]["after"]
        if any(POLICY.fingerprint(row["package"]) != row for row in report["protected_assets"]) or not report["authoring_protected_catalog"]["unchanged"]:
            raise RuntimeError("An original input asset changed during runtime environment creation")
        report["status"] = "passed"
    except Exception:
        report["status"] = "failed"
        report["errors"].append(traceback.format_exc())
        raise
    finally:
        report["counts"] = {"environments": len(report["environments"]),
                            "active": sum(row["active"] for row in report["environments"]),
                            "outfit_routes": len(report["routes"])}
        POLICY.write(POLICY.REPORT_PATH, report)
        ue.log("PC_VIEWER_ENVIRONMENTS " + json.dumps({key: report[key] for key in ("status", "counts")}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    args, _ = parser.parse_known_args()
    if args.verify_only:
        import unreal
        report = POLICY.load_report(ROOT)
        checks = []
        for row in report["environments"]:
            if row.get("sky", {}).get("target"):
                unreal.load_asset(row["sky"]["target"])
            asset = unreal.load_asset(row["target"])
            actual = snapshot(asset) if isinstance(asset, unreal.ViewerEnvironment) else None
            checks.append({"target": row["target"], "saved_source_snapshot_matches": snapshots_equal(actual, row["snapshot"]),
                           "differences": {key: {"saved_report": row["snapshot"].get(key), "fresh_loaded": actual.get(key) if actual else None}
                                           for key in row["snapshot"] if actual is None or not snapshots_equal(row["snapshot"].get(key), actual.get(key))},
                           "asset": POLICY.fingerprint(row["target"])})
        result = {"schema_version": 1, "status": "passed" if all(row["saved_source_snapshot_matches"] for row in checks) else "failed",
                  "content_saved": False, "environment_report_sha256": POLICY.digest(POLICY.REPORT_PATH), "environments": checks}
        POLICY.write(POLICY.WORK / "viewer_environments_fresh_verification.json", result)
        unreal.log("PC_VIEWER_ENVIRONMENTS_FRESH " + json.dumps({"status": result["status"], "environments": len(checks)}))
        if result["status"] != "passed":
            raise RuntimeError("Fresh-process environment snapshot verification failed")
        return
    plan = POLICY.build_plan(ROOT)
    POLICY.write(POLICY.PLAN_PATH, plan)
    if args.plan_only:
        print(json.dumps({"plan": str(POLICY.PLAN_PATH), **plan["counts"]}, ensure_ascii=False, indent=2))
        return
    import unreal
    apply(unreal, plan)


if __name__ == "__main__":
    main()
