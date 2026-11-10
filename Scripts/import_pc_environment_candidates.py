#!/usr/bin/env python3
"""Build independent PC lighting candidates from the published evidence.

Plain Python: ``python3 Scripts/import_pc_environment_candidates.py --plan-only``.
UE Python: execute this file after the PC texture import. It writes only
``/Game/Viewer/PCEnvironment`` and never edits the current Catalog or maps.

These assets are inactive candidates. Unserialized native defaults, Blueprint
construction, exposure semantics, fork shaders and Volume blending are not
recovered by this bridge. Every candidate fallback is recorded in its report.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import traceback


ROOT = Path(__file__).resolve().parent.parent
ENVIRONMENT = ROOT / "PCResources/Environment"
OUTPUT = ROOT / "Saved/PCAssetConversion"
DESTINATION = "/Game/Viewer/PCEnvironment"
OWNER = "NZM.PCEnvironmentCandidate.v1"
INPUTS = (
    "PCResources/Environment/Dependencies/resolved_components.json",
    "PCResources/Environment/Dependencies/object_index.json",
    "PCResources/Environment/scene_parameters.json",
    "PCResources/Lighting/postprocess_profiles.json",
)
LIGHT_FIELDS = {
    "Intensity": "intensity", "AttenuationRadius": "radius",
    "InnerConeAngle": "inner_cone", "OuterConeAngle": "outer_cone",
    "SourceRadius": "source_radius", "SourceLength": "source_length",
    "Temperature": "temperature", "bUseTemperature": "use_temperature",
    "CastShadows": "cast_shadows", "bUseInverseSquaredFalloff": "inverse_squared_falloff",
    "LightFalloffExponent": "falloff_exponent", "SpecularScale": "specular_scale",
    "ShadowBias": "shadow_bias",
}
UNIT_NAMES = {"Unitless": 0, "Candelas": 1, "Lumens": 2, "EV": 3, "Nits": 4}
KINDS = {"SpotLightComponent": "Spot", "PointLightComponent": "Point",
         "DirectionalLightComponent": "Directional"}
POST_FIELDS = {
    "VignetteIntensity": ("vignette_intensity", "scalar"),
    "LensFlareIntensity": ("lens_flare_intensity", "scalar"),
    "SceneFringeIntensity": ("scene_fringe_intensity", "scalar"),
    "ChromaticAberrationStartOffset": ("chromatic_aberration_start_offset", "scalar"),
    "BloomIntensity": ("bloom_intensity", "scalar"),
    "BloomThreshold": ("bloom_threshold", "scalar"),
    "BloomSizeScale": ("bloom_size_scale", "scalar"),
    **{f"Color{part}{range_}": ("color_" + part.lower() +
        ("_" + range_.lower() if range_ else ""), "vector4")
       for part in ("Saturation", "Contrast", "Gamma", "Gain", "Offset")
       for range_ in ("", "Shadows", "Midtones", "Highlights")},
    **{f"Bloom{i}Size": (f"bloom{i}_size", "scalar") for i in range(1, 7)},
    **{f"Bloom{i}Tint": (f"bloom{i}_tint", "linear_color") for i in range(1, 7)},
}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def name(value):
    return re.sub(r"[^A-Za-z0-9_]", "_", value)


def reference_identity(value):
    return value.get("ObjectPath") if isinstance(value, dict) else None


def source_package(value):
    text = reference_identity(value)
    if not text:
        return None
    text = text.split(".", 1)[0]
    return "/Game/" + text[len("NZM/Content/"):] if text.startswith("NZM/Content/") else text


def vector(value, axes="XYZ", default=(0.0, 0.0, 0.0)):
    return [float((value or {}).get(axis, default[i])) for i, axis in enumerate(axes)]


def normalized(q):
    length = math.sqrt(sum(n * n for n in q))
    if length < 1e-12:
        raise ValueError("Zero rotation quaternion")
    return [n / length for n in q]


def rotation_quat(rotator):
    # FRotator::Quaternion convention from the target UE implementation.
    pitch, yaw, roll = [math.radians(float((rotator or {}).get(k, 0))) / 2
                        for k in ("Pitch", "Yaw", "Roll")]
    sp, cp, sy, cy, sr, cr = math.sin(pitch), math.cos(pitch), math.sin(yaw), math.cos(yaw), math.sin(roll), math.cos(roll)
    return normalized([cr*sp*sy-sr*cp*cy, -cr*sp*cy-sr*cp*sy,
                       cr*cp*sy-sr*sp*cy, cr*cp*cy+sr*sp*sy])


def multiply_quat(a, b):
    x, y, z, w = a
    xx, yy, zz, ww = b
    return normalized([w*xx+x*ww+y*zz-z*yy, w*yy-x*zz+y*ww+z*xx,
                       w*zz+x*yy-y*xx+z*ww, w*ww-x*xx-y*yy-z*zz])


def rotate(q, point):
    x, y, z, w = q
    cross = [y*point[2]-z*point[1], z*point[0]-x*point[2], x*point[1]-y*point[0]]
    second = [y*cross[2]-z*cross[1], z*cross[0]-x*cross[2], x*cross[1]-y*cross[0]]
    return [point[i] + 2*(w*cross[i]+second[i]) for i in range(3)]


def linear_byte_color(value):
    channels = vector(value, "RGBA", (255, 255, 255, 255))
    rgb = [v/255 for v in channels[:3]]
    return [v/12.92 if v <= .04045 else ((v+.055)/1.055)**2.4 for v in rgb] + [channels[3]/255]


class Evidence:
    def __init__(self, root=ROOT):
        self.root = root.resolve()
        self.environment = self.root / "PCResources/Environment"
        self.manifest = {"PCResources/" + row["file"]: row
                         for row in read_json(self.root / "PCResources/source_manifest.json")["files"]}
        self.verified = {}
        for path in INPUTS:
            self.verify(path)
        self.resolved = read_json(self.root / INPUTS[0])
        self.index = read_json(self.root / INPUTS[1])
        self.scenes = read_json(self.root / INPUTS[2])
        self.post = read_json(self.root / INPUTS[3])
        self.objects = {row["identity"]: row for row in self.index["objects"]}
        self.components = {row["instance_identity"]: row for row in self.resolved["components"]}
        self.raw_cache = {}

    def verify(self, relative):
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root / "PCResources") or relative not in self.manifest:
            raise ValueError("Unpublished source: " + relative)
        if relative not in self.verified:
            row = self.manifest[relative]
            if path.stat().st_size != row["bytes"] or sha256(path) != row["sha256"]:
                raise ValueError("Published input hash mismatch: " + relative)
            self.verified[relative] = row["sha256"]
        return path

    def raw(self, identity):
        if identity not in self.raw_cache:
            row = self.objects.get(identity)
            if row is None:
                raise ValueError("Missing exact source object: " + identity)
            path = self.verify("PCResources/Environment/" + row["source_json"])
            self.raw_cache[identity] = read_json(path)
        return self.raw_cache[identity]

    def properties(self, identity):
        if identity in self.components:
            return self.components[identity]["effective_serialized_properties"]
        return self.raw(identity).get("Properties", {})

    def actor_chain(self, identity):
        """Follow actual Outer identities; an actor's RootComponent is its placement.

        Blueprint parameters called Transform are not ActorToWorld. Applying
        them in addition to the serialized root would double-transform lights.
        """
        result, seen = [], set()
        row = self.objects.get(identity)
        while row:
            outer = row.get("exact_outer_identity")
            if not outer or outer.get("export_index", -1) < 0:
                break
            key = outer["package"] + "." + str(outer["export_index"])
            if key in seen:
                raise ValueError("Cyclic source Outer chain")
            seen.add(key)
            row = self.objects.get(key)
            if row is None or row["class"] in ("Level", "World"):
                break
            props = self.properties(key)
            result.append({"identity": key, "class": row["class"],
                           "source_json": row["source_json"],
                           "root_component": reference_identity(props.get("RootComponent")),
                           "serialized_visibility": {k: props[k] for k in
                            ("bHidden", "bHiddenInGame", "bVisible") if k in props},
                           "blueprint_transform_parameter_not_applied": "Transform" in props})
        return result

    def transform(self, identity):
        chain, seen = [], set()
        cursor = identity
        terminal = None
        unknown = []
        while cursor:
            if cursor in seen:
                raise ValueError("Cyclic source AttachParent chain: " + cursor)
            seen.add(cursor)
            if cursor not in self.objects:
                terminal = cursor
                break
            props = self.properties(cursor)
            missing = [key for key in ("RelativeLocation", "RelativeRotation", "RelativeScale3D") if key not in props]
            if missing:
                unknown.append({"identity": cursor, "fields": missing,
                                "candidate_fallback": "identity translation/rotation/scale for absent axes; native source defaults unverified"})
            local = {"location_cm": vector(props.get("RelativeLocation")),
                     "rotation_xyzw": rotation_quat(props.get("RelativeRotation")),
                     "scale": vector(props.get("RelativeScale3D"), default=(1, 1, 1))}
            flags = {key: props[key] for key in ("bAbsoluteLocation", "bAbsoluteRotation", "bAbsoluteScale") if key in props}
            # A socket transform cannot be reconstructed without its rig/mesh.
            socket = props.get("AttachSocketName")
            if socket and socket != "None":
                unknown.append({"identity": cursor, "field": "AttachSocketName", "value": socket,
                                "candidate_fallback": "socket offset not applied; attachment component transform only"})
            chain.append({"identity": cursor, "source_json": self.objects[cursor]["source_json"],
                          "serialized_transform": {k: props[k] for k in
                           ("RelativeLocation", "RelativeRotation", "RelativeScale3D") if k in props},
                          "candidate_local_transform": local, "absolute_flags": flags,
                          "source_actor_chain": self.actor_chain(cursor)})
            cursor = reference_identity(props.get("AttachParent"))
        if terminal:
            unknown.append({"field": "AttachParent", "value": terminal, "candidate_fallback": "unresolved parent contributes identity"})
        world = {"location_cm": [0, 0, 0], "rotation_xyzw": [0, 0, 0, 1], "scale": [1, 1, 1]}
        for step in reversed(chain):
            local, flags = step["candidate_local_transform"], step["absolute_flags"]
            translated = rotate(world["rotation_xyzw"], [world["scale"][i]*local["location_cm"][i] for i in range(3)])
            world = {
                "location_cm": local["location_cm"] if flags.get("bAbsoluteLocation") else
                    [world["location_cm"][i]+translated[i] for i in range(3)],
                "rotation_xyzw": local["rotation_xyzw"] if flags.get("bAbsoluteRotation") else
                    multiply_quat(world["rotation_xyzw"], local["rotation_xyzw"]),
                "scale": local["scale"] if flags.get("bAbsoluteScale") else
                    [world["scale"][i]*local["scale"][i] for i in range(3)],
            }
        return {"status": "candidate_with_unknowns" if unknown else "serialized_transform_chain_composed",
                "composition": "UE parent rotation * child rotation; parent scale then parent rotate of child location; root component is actor placement",
                "world_candidate": world, "component_to_parent_chain": chain, "unknown": unknown,
                "runtime_construction_and_attachment": "not_evaluated"}


def visibility(evidence, component):
    props = component["effective_serialized_properties"]
    chain = evidence.transform(component["instance_identity"])["component_to_parent_chain"]
    checks = []
    for step in chain:
        current = evidence.properties(step["identity"])
        checks.append({"identity": step["identity"], "flags": {key: current[key] for key in
                      ("bAffectsWorld", "bVisible", "bHiddenInGame", "bHidden") if key in current}})
        checks.extend({"identity": actor["identity"], "flags": actor["serialized_visibility"]}
                      for actor in step["source_actor_chain"])
    inactive = [check for check in checks if check["flags"].get("bAffectsWorld") is False
                or check["flags"].get("bVisible") is False or check["flags"].get("bHiddenInGame") is True
                or check["flags"].get("bHidden") is True]
    return {"included": not inactive, "checks": checks, "explicit_inactive": inactive,
            "missing_source_component_flags": [key for key in ("bAffectsWorld", "bVisible", "bHiddenInGame") if key not in props],
            "unknown_flag_policy": "only explicitly false/hidden flags exclude; native defaults and runtime changes remain unknown"}


def provenance(component, source_key):
    prefix = "/" + source_key
    rows = {key: value for key, value in component.get("field_sources", {}).items()
            if key == prefix or key.startswith(prefix + "/")}
    return {"source_property": source_key, "field_sources": rows,
            "instance_source_json": component["source_json"]}


def light_plan(evidence, component):
    props = component["effective_serialized_properties"]
    row = {"id": component["instance_identity"], "name": component["name"],
           "source_class": component["class"], "source_json": component["source_json"],
           "template_chain": component["template_chain"], "source_mobility": props.get("Mobility"),
           "visibility": visibility(evidence, component), "transform": evidence.transform(component["instance_identity"]),
           "mapped": {}, "mapped_provenance": {}, "unknown": [], "unsupported": []}
    if component["class"] not in KINDS:
        row["status"] = "unsupported_light_class"
        return row
    row["kind"] = KINDS[component["class"]]
    for source, target in LIGHT_FIELDS.items():
        if source in props:
            row["mapped"][target] = props[source]
            row["mapped_provenance"][target] = provenance(component, source)
        else:
            row["unknown"].append({"source_property": source, "target_property": target,
                                   "candidate_fallback": "FViewerEnvironmentLight default; source native default unverified"})
    if "LightColor" in props:
        row["mapped"]["color"] = linear_byte_color(props["LightColor"])
        row["mapped_provenance"]["color"] = {**provenance(component, "LightColor"),
                "conversion": "serialized FColor byte RGB sRGB to linear, alpha/255"}
    else:
        row["unknown"].append({"source_property": "LightColor", "target_property": "color",
                               "candidate_fallback": "FViewerEnvironmentLight white; source native color unverified"})
    units = props.get("IntensityUnits")
    if units and units.rsplit("::", 1)[-1] in UNIT_NAMES:
        row["mapped"]["intensity_units"] = UNIT_NAMES[units.rsplit("::", 1)[-1]]
        row["mapped_provenance"]["intensity_units"] = provenance(component, "IntensityUnits")
    else:
        row["unknown"].append({"source_property": "IntensityUnits", "source_value": units,
                               "candidate_fallback": "FViewerEnvironmentLight default Candelas; not a verified source unit"})
    channels = props.get("LightingChannels")
    if isinstance(channels, dict) and all(k in channels for k in ("bChannel0", "bChannel1", "bChannel2")):
        row["mapped"]["lighting_channel_mask"] = sum((1 << i) if channels[f"bChannel{i}"] else 0 for i in range(3))
        row["mapped_provenance"]["lighting_channel_mask"] = provenance(component, "LightingChannels")
    else:
        row["unknown"].append({"source_property": "LightingChannels", "source_value": channels,
                               "candidate_fallback": "FViewerEnvironmentLight channel 0; unspecified source bits unverified"})
    mapped_keys = set(LIGHT_FIELDS) | {"LightColor", "IntensityUnits", "LightingChannels"}
    runtime_fields = ("LightFunctionMaterial", "IESTexture", "DynamicShadowDistanceMovableLight", "DynamicShadowCascades",
                      "bEnableMicroShadow", "bIgnoreShadowVolume", "ShadowResolutionScale", "bMobileAffectForwardObject")
    row["unsupported"] = [{"source_property": key, "source_value": props[key],
                            "status": "not_expressed_by_ViewerEnvironmentLight"} for key in runtime_fields if key in props]
    scale = row["transform"]["world_candidate"]["scale"]
    if any(abs(value - 1) > 1e-8 for value in scale):
        row["unsupported"].append({"source_property": "composed_component_scale", "source_value": scale,
                                   "status": "ViewerEnvironmentLight has no scale; location/rotation composed, light shape scale not reconstructed"})
    row["serialized_properties_not_mapped"] = sorted(set(props) - mapped_keys)
    row["status"] = "excluded_explicit_source_visibility" if not row["visibility"]["included"] else "candidate_with_unknowns"
    return row


def texture_targets(asset_plan):
    targets = {}
    if asset_plan.is_file():
        data = read_json(asset_plan)
        for texture in data.get("textures", []):
            package = texture.get("source_package")
            if package:
                targets[package] = texture.get("target") or texture.get("target_asset")
    return targets


def postprocess_plan(volume, platform):
    mappings, omitted = [], []
    selected = ("mainSettings", "DesktopBloomSettings" if platform == "Desktop" else "MobileBloomSettings")
    for branch in selected:
        for field in volume["branches"].get(branch, []):
            item = {"source_branch": branch, **field}
            if field["evidence_state"] != "value_and_explicit_override":
                item["reason"] = "source does not serialize both value and enabled override"
                omitted.append(item)
            elif field["field"] not in POST_FIELDS:
                item["reason"] = "no verified stock mapping; exposure, DOF, fork fields and object references are retained as evidence"
                omitted.append(item)
            else:
                target, kind = POST_FIELDS[field["field"]]
                item.update(target_property=target, value_kind=kind,
                            interpretation="stock UE candidate response; original fork pixel equivalence unverified")
                mappings.append(item)
    return {"volume_name": volume["name"], "source_json": volume["source_json"],
            "source_sha256": volume["source_sha256"], "source_volume_properties": volume["volume_properties"],
            "mapped": mappings, "omitted": omitted,
            "volume_blending": "not_reconstructed; candidate stores settings only; no current map or Catalog is changed"}


def build_plan(root=ROOT, asset_plan=None):
    root = root.resolve()
    evidence = Evidence(root)
    targets = texture_targets(asset_plan or root / "Saved/PCAssetConversion/asset_plan.json")
    scenes = []
    for scene in evidence.scenes["maps"]:
        package = scene["virtual_package"]
        components = [c for c in evidence.resolved["components"] if c["package"] == package]
        lights = [light_plan(evidence, c) for c in components if c["category"] == "light" and c["class"] != "SkyLightComponent"]
        skies = []
        for c in components:
            if c["category"] != "light" or c["class"] != "SkyLightComponent":
                continue
            props = c["effective_serialized_properties"]
            cube = source_package(props.get("Cubemap"))
            skies.append({"source_json": c["source_json"], "visibility": visibility(evidence, c),
                          "source_package": cube, "target": targets.get(cube),
                          "mapped": {**({"sky_intensity": props["Intensity"]} if "Intensity" in props else {}),
                                     **({"sky_color": linear_byte_color(props["LightColor"])} if "LightColor" in props else {})},
                          "mapped_provenance": {key: provenance(c, key) for key in ("Intensity", "LightColor", "Cubemap") if key in props},
                          "unknown": [key for key in ("Intensity", "LightColor", "SourceType", "Mobility") if key not in props],
                          "unsupported_source_properties": {key: value for key, value in props.items() if key in
                           ("CubemapAverageBrightness", "bUseNormalization", "SourceCubemapAngle", "CastShadows", "Mobility")},
                          "candidate_fallback": "unserialized sky fields retain ViewerEnvironment defaults; cube directions/normalization are unverified"})
        cameras = [{"source_json": c["source_json"], "name": c["instance_object_path"],
                    "transform": evidence.transform(c["instance_identity"]),
                    "properties": c["effective_serialized_properties"],
                    "status": "retained_not_auto_selected; original camera route is unresolved"}
                   for c in components if c["category"] == "camera_component"]
        volumes = [v for v in evidence.post["volumes"] if v["map_package"] == package]
        if not lights and not skies and not volumes:
            # Background particle/geometry maps cannot be an equivalent lighting environment.
            continue
        label = name(package.rsplit("/", 1)[-1])[:75] + "_" + hashlib.sha256(package.encode()).hexdigest()[:8]
        for platform in ("Desktop", "Mobile"):
            scenes.append({"source_package": package, "platform_branch": platform,
                "target": DESTINATION + "/DA_PC_" + label + "_" + platform,
                "status": "inactive_candidate_with_unknowns", "lights": lights, "skies": skies,
                "cameras": cameras, "postprocess": [postprocess_plan(v, platform) for v in volumes],
                "unsupported_components": {"fog_reflection_atmosphere": scene.get("fog_atmosphere_reflection_components", []),
                                           "particles": scene.get("particle_components", [])},
                "source_field_scope": "template serialized overlay + exact instance topology; source construction and native defaults unknown",
                "runtime_activation": "not_performed; current ViewerPawn preferences override several source fields"})
    plan = {"schema_version": 1, "status": "planned_inactive_candidates", "owner": OWNER,
            "source_scope": evidence.resolved["source_scope"], "source_inputs": evidence.verified,
            "asset_plan": (asset_plan or root / "Saved/PCAssetConversion/asset_plan.json").relative_to(root).as_posix(),
            "candidates": scenes,
            "limitations": ["Current Catalog, maps, environments and material bindings are not modified.",
                "Missing source defaults are recorded; struct/identity fallback values describe a candidate only.",
                "Mobility, shadow strategy, rectangular lights, reflection captures, fog, particles and runtime material logic are not reconstructed.",
                "Source exposure zero is not converted to EV0. DOF and fork-specific postprocess fields remain evidence.",
                "Camera candidates are not selected automatically; current ViewerPawn preferences still override exposure, DOF and several quality values.",
                "HDRs are exported long-lat RGBE projections; source brightness normalization and orientation require rendered verification."],
            "counts": {"candidates": len(scenes), "unique_source_maps": len({s['source_package'] for s in scenes}),
                       "source_lights": sum(1 for c in evidence.resolved["components"] if c['category'] == 'light'),
                       "included_light_rows_per_platform": sum(1 for s in scenes if s['platform_branch']=='Desktop'
                        for l in s['lights'] if l['visibility']['included']),
                       "explicitly_excluded_light_rows_per_platform": sum(1 for s in scenes if s['platform_branch']=='Desktop'
                        for l in s['lights'] if not l['visibility']['included'])}}
    return plan


def apply_plan(plan, root=ROOT):
    import unreal
    library = unreal.EditorAssetLibrary
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    report = {"schema_version": 1, "status": "started", "source_inputs": plan["source_inputs"],
              "saved": [], "warnings": [], "runtime_activation": "not_performed",
              "shader_and_visual_validation": "not_run", "candidate_count": len(plan["candidates"])}
    report_path = root / "Saved/PCAssetConversion/environment_report.json"
    try:
        for candidate in plan["candidates"]:
            target = candidate["target"]
            if not target.startswith(DESTINATION + "/"):
                raise ValueError("Destination outside candidate directory")
            asset = library.load_asset(target) if library.does_asset_exist(target) else None
            if asset is not None:
                if not isinstance(asset, unreal.ViewerEnvironment) or library.get_metadata_tag(asset, "PCEnvironmentOwner") != OWNER:
                    raise ValueError("Refusing to overwrite an existing noncandidate asset: " + target)
            else:
                factory = unreal.DataAssetFactory()
                factory.set_editor_property("data_asset_class", unreal.ViewerEnvironment)
                asset = tools.create_asset(target.rsplit("/", 1)[-1], target.rsplit("/", 1)[0], unreal.ViewerEnvironment, factory)
            if not isinstance(asset, unreal.ViewerEnvironment):
                raise RuntimeError("Could not create ViewerEnvironment: " + target)
            defaults = unreal.new_object(unreal.ViewerEnvironment)
            for key in ("cubemap", "sky_intensity", "sky_color"):
                asset.set_editor_property(key, defaults.get_editor_property(key))
            # Use a new struct to reset only this bridge's candidate on rerun.
            lights = []
            entry = {"target": target, "source_package": candidate["source_package"],
                     "platform_branch": candidate["platform_branch"], "lights": [], "unresolved": []}
            for row in candidate["lights"]:
                if not row["visibility"]["included"] or "kind" not in row:
                    continue
                light = unreal.ViewerEnvironmentLight()
                light.set_editor_property("id", name(row["name"]) + "_" + row["id"].rsplit(".", 1)[-1])
                light.set_editor_property("kind", row["kind"])
                transform = row["transform"]["world_candidate"]
                light.set_editor_property("location", unreal.Vector(*transform["location_cm"]))
                light.set_editor_property("rotation", unreal.Quat(*transform["rotation_xyzw"]).rotator())
                for key, value in row["mapped"].items():
                    light.set_editor_property(key, unreal.LinearColor(*value) if key == "color" else value)
                lights.append(light)
                entry["lights"].append({"source_json": row["source_json"], "unknown_count": len(row["unknown"]),
                                        "transform_status": row["transform"]["status"]})
            asset.set_editor_property("lights", lights)
            asset.set_editor_property("meshes", [])
            # No source camera route is assumed from its name or directory.
            asset.set_editor_property("use_source_camera", False)
            asset.set_editor_property("enable_fog_scattering", False)
            skies = [sky for sky in candidate["skies"] if sky["visibility"]["included"]]
            if len(skies) == 1:
                sky = skies[0]
                cube = library.load_asset(sky["target"]) if sky.get("target") else None
                if isinstance(cube, unreal.TextureCube):
                    asset.set_editor_property("cubemap", cube)
                else:
                    asset.set_editor_property("cubemap", None)
                    entry["unresolved"].append({"kind": "cubemap", "source_package": sky["source_package"],
                                                "target": sky.get("target"), "reason": "No imported TextureCube at target"})
                for key, value in sky["mapped"].items():
                    asset.set_editor_property(key, unreal.LinearColor(*value) if key == "sky_color" else value)
            elif skies:
                entry["unresolved"].append({"kind": "sky_selection", "reason": "Multiple source SkyLights; not combined"})
            settings = unreal.PostProcessSettings()
            if len(candidate["postprocess"]) == 1:
                for row in candidate["postprocess"][0]["mapped"]:
                    key, value = row["target_property"], row["value"]
                    if row["value_kind"] == "vector4":
                        value = unreal.Vector4(*vector(value, "XYZW", (0, 0, 0, 1)))
                    elif row["value_kind"] == "linear_color":
                        value = unreal.LinearColor(*vector(value, "RGBA", (0, 0, 0, 1)))
                    try:
                        settings.set_editor_property(key, value)
                        settings.set_editor_property("override_" + key, True)
                    except (AttributeError, RuntimeError, TypeError) as error:
                        entry["unresolved"].append({"kind": "postprocess_property", "source_field": row["field"],
                                                    "target_property": key, "reason": str(error)})
            elif candidate["postprocess"]:
                entry["unresolved"].append({"kind": "volume_blending", "reason": "Multiple source Volumes; not combined"})
            asset.set_editor_property("post_process", settings)
            notes = {"status": candidate["status"], "source_package": candidate["source_package"],
                     "platform_branch": candidate["platform_branch"], "plan": "Saved/PCAssetConversion/environment_plan.json",
                     "report": "Saved/PCAssetConversion/environment_report.json", "limitations": plan["limitations"],
                     "unresolved": entry["unresolved"], "source_inputs": plan["source_inputs"]}
            asset.set_editor_property("reconstruction_notes", json.dumps(notes, ensure_ascii=False, sort_keys=True))
            library.set_metadata_tag(asset, "PCEnvironmentOwner", OWNER)
            library.set_metadata_tag(asset, "PCEnvironmentSourcePackage", candidate["source_package"])
            library.set_metadata_tag(asset, "PCEnvironmentStatus", "inactive_candidate_with_unknowns")
            if not library.save_loaded_asset(asset):
                raise RuntimeError("Candidate did not save: " + target)
            report["saved"].append(entry)
            write_json(report_path, report)
        report["status"] = "saved_inactive_candidates_requires_source_mode_and_visual_validation"
    except Exception:
        report["status"] = "failed_partial_candidate_assets_possible"
        report["error"] = traceback.format_exc()
        raise
    finally:
        write_json(report_path, report)
    unreal.log("PC_ENVIRONMENT_CANDIDATES " + json.dumps({"status": report["status"], "saved": len(report["saved"])}))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--asset-plan", type=Path)
    args, _ = parser.parse_known_args()
    plan = build_plan(args.root, args.asset_plan)
    write_json(args.root / "Saved/PCAssetConversion/environment_plan.json", plan)
    if args.plan_only:
        print(json.dumps({"status": plan["status"], **plan["counts"]}, indent=2))
        return
    apply_plan(plan, args.root)


if __name__ == "__main__":
    main()
