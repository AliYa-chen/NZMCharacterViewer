"""Sourced PC lighting plans and read-only runtime-environment bindings.

The plan distinguishes selected viewer routes from unresolved original-game
routes. Saved runtime assets use known stage geometry and source lighting;
large source-world offsets are never guessed from light names or positions.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "Saved/PCViewerIntegration"
PLAN_PATH = WORK / "viewer_environment_plan.json"
REPORT_PATH = WORK / "viewer_environment_report.json"
TARGET_ROOT = "/Game/PCAssets/ViewerEnvironments"
OWNER = "pc_viewer_source_environment_v1"
SOURCE_MAPS = {
    "Standard": "/Game/Maps/Lobby/Role/Lobby_Scene_Light_Role",
    "QueenRole": "/Game/Maps/Lobby_New/Role_Queen/Lobby_Scene_Light_Role_Queen",
    "QueenPreview": "/Game/Maps/Lobby_New/Role_Queen/Lobby_Scene_Light_Lobby_Queen_Preview",
    "Lucia": "/Game/Maps/Lobby_New/Role_AngelLucia/Lobby_Scene_Light_Role_Role_AngelLucia",
    "NaiMei": "/Game/Maps/Lobby_New/Role_Nai_Mei/Lobby_Scene_Light_Role_Nai_Mei",
    "NaimeiOther": "/Game/Maps/Lobby_New/Role_Naimei/Lobby_Scene_Light_Role_Naimei",
    "Bianca": "/Game/Maps/Lobby_New/Role_NaimeiBianca/EditorOnly/Lobby_Scene_Light_Role_NaimeiBianca",
}
GEOMETRY = {"Standard": "/Game/Viewer/Environment/DA_Environment",
            "QueenPreview": "/Game/Viewer/Environment/DA_Environment_Queen",
            "NaiMei": "/Game/Viewer/Environment/DA_Environment_Naimei",
            "Standard_LuciaGeometry": "/Game/Viewer/Environment/DA_Environment_Lucia",
            "Standard_BiancaGeometry": "/Game/Viewer/Environment/DA_Environment"}
EXTRA_LIGHT_FIELDS = {
    "ShadowResolutionScale": "shadow_resolution_scale", "DynamicShadowDistanceMovableLight": "dynamic_shadow_distance",
    "DynamicShadowCascades": "dynamic_shadow_cascades", "CascadeDistributionExponent": "cascade_distribution_exponent",
    "CascadeTransitionFraction": "cascade_transition_fraction", "SourceWidth": "source_width", "SourceHeight": "source_height",
    "BarnDoorAngle": "barn_door_angle", "BarnDoorLength": "barn_door_length",
    "IndirectLightingIntensity": "indirect_lighting_intensity", "VolumetricScatteringIntensity": "volumetric_scattering_intensity",
}
FOG_FIELDS = {"FogDensity": "fog_density", "FogHeightFalloff": "fog_height_falloff", "StartDistance": "fog_start_distance",
              "FogMaxOpacity": "fog_max_opacity", "VolumetricFogScatteringDistribution": "fog_volumetric_scattering_distribution",
              "VolumetricFogDistance": "fog_volumetric_distance"}
DESKTOP_CVARS = ("r.BloomQuality", "r.DepthOfFieldQuality", "r.Tonemapper.Quality", "r.Tonemapper.Sharpen",
                 "r.SSR.Quality", "r.SSR.HalfResSceneColor", "r.SSR.Temporal", "r.AmbientOcclusionLevels",
                 "r.AmbientOcclusionMaxQuality", "r.ContactShadows", "r.Shadow.CSM.MaxCascades",
                 "r.Shadow.MaxCSMResolution", "r.Shadow.MaxResolution", "r.MaxAnisotropy")
MOBILE_CVARS = ("r.BloomQuality", "r.DepthOfFieldQuality", "r.Tonemapper.Quality", "r.Tonemapper.Sharpen", "r.MaxAnisotropy")


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".new")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def package(reference):
    return str(reference).split(".", 1)[0]


def object_path(reference):
    value = str(reference)
    return value if "." in value else value + "." + value.rsplit("/", 1)[-1]


def fingerprint(reference, root=ROOT):
    name = package(reference)
    if not name.startswith("/Game/") or any(value in ("", ".", "..") for value in name[6:].split("/")):
        raise ValueError("Expected exact project package: " + name)
    path = Path(root) / "Content" / (name[6:] + ".uasset")
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError("Missing saved environment input: " + name)
    return {"package": name, "file": path.relative_to(root).as_posix(), "bytes": path.stat().st_size, "sha256": digest(path)}


def import_bridge(root=ROOT):
    specification = importlib.util.spec_from_file_location("pc_environment_evidence_bridge", root / "Scripts/import_pc_environment_candidates.py")
    bridge = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(bridge)
    return bridge


def selected_route(outfit):
    model = str(outfit["model_id"])
    if model in ("10002000044", "10002010044"):
        return "QueenPreview", "QueenRole", "documented_preview_with_exact_female_camera_tag", [
            "Queen Role has a separate large world frame and no recovered default female-camera tag.",
            "The Preview branch has an exact Lobby.Camera.SubScene.Female tag and matches the retained preview throne.",
            "This is a sourced viewer presentation choice; the original online outfit route remains unresolved."]
    if model == "10002000045":
        return "NaiMei", "NaiMei", "documented_branch_with_exact_female_camera_tag", [
            "Nai_Mei has the recovered Lobby.Camera.SubScene.Female tag and a local lighting rig.",
            "The separate Naimei branch has a large unresolved world origin and is retained as an inactive alternative."]
    if model in ("10002000053", "10002000055"):
        desired = "Lucia" if model == "10002000053" else "Bianca"
        return "Standard_" + desired + "Geometry", desired, "standard_light_fallback_unresolved_special_scene_origin", [
            "The exported special-scene lights occupy a different world frame; no character placement/streaming offset is recovered.",
            "A translation from the light cluster cannot establish the character origin and is not invented.",
            "Standard PC role lighting is used with the retained viewer geometry/framing; the special source candidate remains inactive.",
            "Bianca's EditorOnly sky Intensity=0 is retained in its independent source asset." if desired == "Bianca" else
            "Lucia's only exported camera is tagged for a weapon personality view; it is not treated as the default portrait camera."]
    return "Standard", "Standard", "standard_pc_role_viewer_fallback", [
        "The serialized outfit UIConfig declares parts and role identity, but no per-model scene route.",
        "The documented standard PC role lighting is the explicit viewer fallback; original online routing is not claimed."]


def queen_model_alignment(viewer, baseline):
    original = [outfit for character in baseline["characters"] if character["id"] == "queen"
                for outfit in character["outfits"] if outfit["id"] in ("queen", "queen_masked")]
    if len(original) != 2 or any(not outfit["parts"] for outfit in original):
        raise ValueError("Queen scene alignment requires both complete original outfits")
    texts = {part["transform"] for outfit in original for part in outfit["parts"]}
    if len(texts) != 1:
        raise ValueError("Original Queen parts do not share one proven scene alignment")
    text = texts.pop()
    def values(field, axes):
        match = re.search(r"\b" + field + r"=\(([^()]*)\)", text)
        if not match:
            raise ValueError("Cannot read original Queen scene alignment " + field)
        parsed = {key: float(value) for key, value in re.findall(r"([XYZW])=([-+0-9.eE]+)", match.group(1))}
        if set(parsed) != set(axes):
            raise ValueError("Incomplete original Queen alignment axes")
        return [parsed[axis] for axis in axes]
    identity = {"translation": [0, 0, 0], "rotation_xyzw": [0, 0, 0, 1], "scale": [1, 1, 1]}
    planned = [outfit for character in viewer["characters"] if character["id"] == "queen"
               for outfit in character["outfits"]]
    if len(planned) != 2 or any(part["transform"] != identity for outfit in planned for part in outfit["parts"]):
        raise ValueError("Queen environment alignment requires identity source UI part transforms; applying it twice is forbidden")
    return {"transform": {"location_cm": values("Translation", "XYZ"), "rotation_xyzw": values("Rotation", "XYZW"),
                          "scale": values("Scale3D", "XYZ")},
            "baseline_serialized_transform": text,
            "basis": "Exact common transform of every part in both original Queen viewer outfits, reused with the same retained preview throne and adapted original idle basis. Source UI part transforms remain identity. This is existing viewer-scene alignment, not a newly recovered online PC world offset.",
            "baseline_outfits": [outfit["id"] for outfit in original],
            "verified_original_parts": sum(len(outfit["parts"]) for outfit in original)}


def build_plan(root=ROOT):
    root = root.resolve()
    bridge = import_bridge(root)
    asset_plan = root / "Saved/PCViewerIntegration/asset_plan.json"
    evidence = bridge.Evidence(root)
    candidates = bridge.build_plan(root, asset_plan)
    viewer_path = root / "Saved/PCViewerIntegration/viewer_plan.json"
    viewer = read(viewer_path)
    baseline_path = root / "Saved/PCViewerIntegration/baseline_catalog.json"
    queen_alignment = queen_model_alignment(viewer, read(baseline_path))
    quality_relative = "PCResources/Lighting/pc_quality_settings.json"
    quality = read(evidence.verify(quality_relative))["log"]["last_request_by_name"]
    scenes = []
    for label, source_map in SOURCE_MAPS.items():
        branches = {row["platform_branch"]: row for row in candidates["candidates"] if row["source_package"] == source_map}
        desktop = branches["Desktop"]
        for light in desktop["lights"]:
            component = evidence.components[light["id"]]
            properties = component["effective_serialized_properties"]
            light["extended_mapped"] = {target: properties[source] for source, target in EXTRA_LIGHT_FIELDS.items() if source in properties}
            light["source_mobility"] = properties.get("Mobility")
            light["runtime_mobility"] = 2
            light["runtime_mobility_basis"] = ("Serialized source Movable retained." if properties.get("Mobility") == "EComponentMobility::Movable"
                else "Movable compatibility reconstruction: source static/stationary baked lighting data is unavailable; intensity/shape/transform remain source values." if properties.get("Mobility")
                else "Explicit Movable compatibility fallback; source native mobility is not serialized.")
            light["runtime_scale"] = light["transform"]["world_candidate"]["scale"]
        skies = []
        for row in desktop["skies"]:
            component = next(c for c in evidence.components.values() if c["source_json"] == row["source_json"])
            props = component["effective_serialized_properties"]
            skies.append({**row, "mapped_extra": {target: props[source] for source, target in
                (("CastShadows", "sky_cast_shadows"), ("SourceCubemapAngle", "sky_cubemap_angle"),
                 ("VolumetricScatteringIntensity", "sky_volumetric_scattering_intensity")) if source in props},
                "source_normalization": {key: props[key] for key in ("bUseNormalization", "CubemapAverageBrightness") if key in props}})
        source_scene = next(row for row in evidence.scenes["maps"] if row["virtual_package"] == source_map)
        fog = []
        for row in source_scene.get("fog_atmosphere_reflection_components", []):
            if row["class"] != "ExponentialHeightFogComponent":
                continue
            path = evidence.verify("PCResources/Environment/" + row["source_json"])
            raw = read(path)
            identity = next(key for key, obj in evidence.objects.items() if obj["source_json"] == row["source_json"])
            props = raw.get("Properties", {})
            fog.append({"source_json": str(path.relative_to(root)), "source_sha256": digest(path),
                        "identity": identity, "source_properties": props,
                        "mapped": {target: props[source] for source, target in FOG_FIELDS.items() if source in props},
                        "transform": evidence.transform(identity),
                        "enable_volumetric": props.get("bEnableVolumetricFog", False),
                        "volumetric_basis": "Explicit source bEnableVolumetricFog." if "bEnableVolumetricFog" in props else
                            "Disabled compatibility fallback; no enabled source volumetric flag is serialized.",
                        "native_default_fallback": "Missing scalar fields use target UE5.8 component defaults and are not recovered source defaults.",
                        "unsupported_source_properties": {key: props[key] for key in ("DirectionalScale", "DirectionalInscatteringColor") if key in props}})
        female_cameras = []
        for obj in evidence.objects.values():
            key = obj["identity"]
            if not key.startswith(source_map.replace("/Game/", "NZM/Content/") + "."):
                continue
            props = evidence.properties(key)
            if "Lobby.Camera.SubScene.Female" not in props.get("Tags", []):
                continue
            component_key = bridge.reference_identity(props.get("CameraComponent"))
            component = evidence.components.get(component_key)
            if component is None:
                raise ValueError("Female camera lacks its exact resolved component: " + key)
            female_cameras.append({"actor_identity": key, "actor_source_json": obj["source_json"],
                                   "tag": "Lobby.Camera.SubScene.Female", "component_identity": component_key,
                                   "properties": component["effective_serialized_properties"],
                                   "transform": evidence.transform(component_key)})
        scenes.append({"id": label, "source_package": source_map, "candidate_desktop": desktop["target"],
                       "candidate_mobile": branches["Mobile"]["target"], "lights": desktop["lights"], "skies": skies,
                       "desktop_postprocess": branches["Desktop"]["postprocess"],
                       "mobile_postprocess": branches["Mobile"]["postprocess"], "fog": fog,
                       "female_cameras": female_cameras, "all_camera_candidates": desktop["cameras"],
                       "unsupported_components": desktop["unsupported_components"],
                       "coordinate_adaptation": {"translation_cm": [0, 0, 0], "rotation_xyzw": [0, 0, 0, 1],
                           "basis": "Exact composed source frame; no unproven actor/streaming origin shift applied."},
                       "source_console_variables": {name: quality[name]["value"] for name in DESKTOP_CVARS if name in quality},
                       "mobile_source_console_variables": {name: quality[name]["value"] for name in MOBILE_CVARS if name in quality}})
    routes, active = [], {}
    for character in viewer["characters"]:
        for outfit in character["outfits"]:
            selected, requested, confidence, reasons = selected_route(outfit)
            source = selected if selected in SOURCE_MAPS else "Standard"
            active[selected] = {"id": selected, "source_scene": source, "geometry_source": GEOMETRY[selected],
                                "target": TARGET_ROOT + "/DA_PC_" + selected, "active": True,
                                "camera_policy": "exact_female_tag_if_present_else_retained_geometry_compatible_viewer_framing; original online route unresolved"}
            routes.append({"character_id": character["id"], "outfit_id": outfit["id"], "model_id": str(outfit["model_id"]),
                           "requested_source_scene": requested, "active_source_scene": source, "active_variant": selected,
                           "environment": active[selected]["target"], "route_confidence": confidence, "reasons": reasons,
                           "original_online_route_established": False})
    inactive = [{"id": "Source_" + label, "source_scene": label, "target": TARGET_ROOT + "/DA_PC_Source_" + label,
                 "geometry_source": GEOMETRY.get(label, GEOMETRY["Standard"]), "active": False,
                 "camera_policy": "exact_female_tag_if_present_else_retained_standard_framing; not runtime-selected",
                 "scope": "Independent source-parameter record with viewer geometry fallback; source origin/routing may remain unresolved."}
                for label in SOURCE_MAPS]
    for spec in list(active.values()) + inactive:
        spec["model_placement"] = queen_alignment if spec["id"] in ("QueenPreview", "Source_QueenPreview") else {
            "transform": {"location_cm": [0, 0, 0], "rotation_xyzw": [0, 0, 0, 1], "scale": [1, 1, 1]},
            "basis": "Identity viewer model root. No unverified original scene/world placement is inferred."}
    return {"schema_version": 1, "owner": OWNER, "status": "planned_sourced_runtime_candidates_with_explicit_fallbacks",
            "viewer_plan_sha256": digest(viewer_path), "asset_index_sha256": digest(root / "Docs/PCAssetConversion/asset_index.json"),
            "baseline_catalog_sha256": digest(baseline_path),
            "source_inputs": {**candidates["source_inputs"], **evidence.verified},
            "quality_request_provenance": {name: quality[name] for name in DESKTOP_CVARS if name in quality},
            "scenes": scenes, "environments": list(active.values()) + inactive, "routes": routes,
            "counts": {"pc_outfits": len(routes), "active_variants": len(active), "source_scenes": len(scenes),
                       "new_environments": len(active) + len(inactive)},
            "limitations": [
                "Outfit UIConfig does not establish the original online scene route or unexported scene/model origin.",
                "Source static lights use an explicit Movable compatibility mode because their baked lighting is not recovered.",
                "Source exposure Min/Max=0 has unknown fork semantics; manual EV0 remains an explicit stock compatibility exposure.",
                "Source desktop/mobile value+enabled-override pairs are mapped separately; absent/disabled override evidence is preserved.",
                "Existing stage geometry/framing is retained; missing PC background meshes/material equations are not synthesized.",
                "HDRs are long-lat RGBE reprojections; fork cubemap normalization, PRT/GI, microshadow/lightfunction/IES behavior remain unsupported.",
                "Source camera candidates are evidence. Existing compatible viewer framing can differ from the original static or animated camera.",
                "Reflection captures, source sky atmosphere, background particles and sequencer changes require separate supported reconstruction.",
                "Quality values are historical PC requests, not proof of a current original-game frame; only a selected stock-compatible subset is applied."]}


def retired_environment_record(record, root=ROOT):
    """Recognize only the seven exact inactive candidates retired by cleanup."""
    specification = importlib.util.spec_from_file_location(
        "pc_viewer_environment_retirement", Path(root) / "Scripts/pc_viewer_retired_assets.py")
    retirement = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(retirement)
    row = retirement.retired_record(record["target"], root)
    if row is None:
        return None
    allowed = {TARGET_ROOT + "/DA_PC_Source_" + scene for scene in SOURCE_MAPS}
    expected = record["asset"]
    if record.get("active") is not False or record["target"] not in allowed:
        raise RuntimeError("An active or unknown runtime environment cannot be retired: " + record["target"])
    if expected.get("package") != record["target"] or any(row.get(key) != expected.get(key) for key in ("file", "bytes", "sha256")):
        raise RuntimeError("Retired environment differs from its historical saved fingerprint: " + record["target"])
    return row


def load_report(root=ROOT, path=None):
    root = Path(root)
    report_path = Path(path) if path else root / "Saved/PCViewerIntegration/viewer_environment_report.json"
    report = read(report_path)
    plan_path = root / "Saved/PCViewerIntegration/viewer_environment_plan.json"
    plan = read(plan_path)
    if report.get("status") != "passed" or report.get("owner") != OWNER or report.get("plan_sha256") != digest(plan_path):
        raise RuntimeError("PC runtime environment report is not complete/current/owned")
    if plan["viewer_plan_sha256"] != digest(root / "Saved/PCViewerIntegration/viewer_plan.json") or plan["asset_index_sha256"] != digest(root / "Docs/PCAssetConversion/asset_index.json"):
        raise RuntimeError("PC runtime environment plan uses another outfit plan or conversion index")
    if plan.get("baseline_catalog_sha256") != digest(root / "Saved/PCViewerIntegration/baseline_catalog.json"):
        raise RuntimeError("PC runtime environment alignment uses another original Catalog baseline")
    records = report.get("environments", [])
    if len(records) != plan["counts"]["new_environments"] or len({row["target"] for row in records}) != len(records):
        raise RuntimeError("PC environment report coverage is incomplete or duplicated")
    retired_targets = set()
    for row in records:
        if not row["target"].startswith(TARGET_ROOT + "/"):
            raise RuntimeError("Saved PC runtime environment escapes its namespace: " + row["target"])
        if retired_environment_record(row, root) is not None:
            retired_targets.add(row["target"])
        elif fingerprint(row["target"], root) != row["asset"]:
            raise RuntimeError("Saved PC runtime environment changed after verification: " + row["target"])
    for row in report.get("protected_assets", []):
        if fingerprint(row["package"], root) != row:
            raise RuntimeError("Protected original environment input changed: " + row["package"])
    if report.get("routes") != plan["routes"]:
        raise RuntimeError("Runtime environment routes differ from the sourced plan")
    if any(package(row["environment"]) in retired_targets for row in report["routes"]):
        raise RuntimeError("An active outfit route points to a retired environment")
    return report


def environment_for_outfit(outfit_id, report):
    rows = [row for row in report["routes"] if row["outfit_id"] == str(outfit_id)]
    if len(rows) != 1:
        raise ValueError("Expected exactly one approved PC environment route for " + str(outfit_id))
    return rows[0]["environment"]
