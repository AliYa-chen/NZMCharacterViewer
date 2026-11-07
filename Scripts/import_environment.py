"""Import the recovered Scene103 environment into editable UE5 assets.

Called by import_assets.py as run(PROJECT, import_task, make_material).
The caller owns the Editor process. This module never opens another Editor,
loads cooked UE4 packages, downloads resources, or changes project C++.

Original geometry, UVs, transforms, light properties and HDRs are retained.
The stripped sky/floor shader graph, custom mobile light normalization,
reflection capture, particles and per-outfit scene routing are not claimed to
be reproduced. Unsupported/unobserved values are listed in the report.

Checked against installed UE5.8 headers: StaticMesh::GetBounds/SetMaterial,
TextureFactory::HDRImportShouldBeLongLatCubeMap, ELightUnits, and ComposeTransforms.
GLTFCore/Private/GLTF/ConversionUtilities.h maps (X,Y,Z) -> (X,Z,Y).
"""
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import traceback

import unreal

DESTINATION = "/Game/Viewer/Environment"


def _name(value):
    return re.sub(r"[^A-Za-z0-9_]", "_", str(value))


def _stem(reference):
    if isinstance(reference, dict):
        reference = reference.get("ObjectPath") or reference.get("AssetPathName")
    if not reference or reference == "None":
        return None
    return str(reference).rsplit("/", 1)[-1].split(".", 1)[0]


def _vector(value, default=(0.0, 0.0, 0.0)):
    value = value or {}
    return [float(value.get(k, default[i])) for i, k in enumerate("XYZ")]


def _rotation(value):
    value = value or {}
    return unreal.Rotator(pitch=float(value.get("Pitch", 0.0)),
                          yaw=float(value.get("Yaw", 0.0)), roll=float(value.get("Roll", 0.0)))


def _linear_color(value, byte_color=False):
    value = value or {}
    channels = [float(value.get(k, 255 if byte_color else 1.0)) for k in "RGB"]
    if byte_color:
        channels = [x / 255.0 for x in channels]
        channels = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in channels]
    return unreal.LinearColor(*channels, 1.0)


def _json_transform(transform):
    t, q, s = transform.translation, transform.rotation, transform.scale3d
    return {"location_cm": [t.x, t.y, t.z], "rotation_xyzw": [q.x, q.y, q.z, q.w],
            "scale": [s.x, s.y, s.z]}


def glb_metadata(path):
    """Read source geometry bounds in glTF metres; no external buffer execution."""
    raw = Path(path).read_bytes()
    if len(raw) < 20 or struct.unpack_from("<III", raw) != (0x46546C67, 2, len(raw)):
        raise ValueError(f"Invalid GLB: {path}")
    length, kind = struct.unpack_from("<II", raw, 12)
    if kind != 0x4E4F534A:
        raise ValueError("GLB JSON chunk missing")
    data = json.loads(raw[20:20 + length])
    minima, maxima = [], []
    for mesh in data.get("meshes", []):
        for primitive in mesh.get("primitives", []):
            accessor = data["accessors"][primitive["attributes"]["POSITION"]]
            minima.append(accessor["min"])
            maxima.append(accessor["max"])
    if not minima:
        raise ValueError("GLB contains no mesh positions")
    low = [min(v[i] for v in minima) for i in range(3)]
    high = [max(v[i] for v in maxima) for i in range(3)]
    # glTF metres/Y-up -> native UE centimetres/Z-up. Actor scale is separate.
    minimum_cm = [low[i] * 100.0 for i in (0, 2, 1)]
    maximum_cm = [high[i] * 100.0 for i in (0, 2, 1)]
    return {"source_minimum_metres": low, "source_maximum_metres": high,
            "expected_origin_cm": [(a + b) * 0.5 for a, b in zip(minimum_cm, maximum_cm)],
            "expected_extent_cm": [(b - a) * 0.5 for a, b in zip(minimum_cm, maximum_cm)],
            "materials": [m.get("name", f"Material_{i}") for i, m in enumerate(data.get("materials", []))]}


class EnvironmentImporter:
    """Reusable recovered-asset importer; standard Scene103 is the first client."""
    def __init__(self, project, import_task, make_material):
        self.project = Path(project).resolve()
        self.source = self.project / "SourceAssets/Environment"
        self.import_task = import_task
        self.make_material = make_material
        self.library = unreal.EditorAssetLibrary
        self.tools = unreal.AssetToolsHelpers.get_asset_tools()
        self.manifest = json.loads((self.source / "manifest.json").read_text())
        self.profile = json.loads(self.asset_file(self.manifest["profile"]).read_text())
        self.standard = json.loads(self.asset_file("Evidence/standard_scene_parameters.json").read_text())
        self.assets = self.manifest["assets"]
        self.cache = {}
        self.report = {"status": "started", "recorded_utc": datetime.now(timezone.utc).isoformat(),
                       "scene_id": 103, "assets": [], "materials": [], "meshes": [], "lights": [],
                       "warnings": [], "approximations": [], "omitted": [],
                       "runtime_and_visual_validation": "not_run"}

    def asset_file(self, relative):
        path = (self.source / relative).resolve()
        if not path.is_relative_to(self.source) or not path.is_file():
            raise ValueError(f"Missing/outside environment source: {relative}")
        return path

    def verify_inputs(self):
        for row in self.manifest.get("files", []):
            path = self.asset_file(row["file"])
            if row.get("sha256") and hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
                raise ValueError(f"Environment source hash mismatch: {row['file']}")
        self.report["manifest_hashes_verified"] = True

    def warning(self, text):
        self.report["warnings"].append(text)
        unreal.log_warning("Environment import: " + text)

    def import_texture(self, key, role="color"):
        cache_key = (key, role)
        if cache_key in self.cache:
            return self.cache[cache_key]
        if key not in self.assets:
            raise ValueError(f"Texture absent from environment manifest: {key}")
        path = self.asset_file(self.assets[key])
        objects = self.import_task(path, DESTINATION + "/Textures", "T_Env_" + _name(key) + "_" + role)
        texture = next((obj for obj in objects if isinstance(obj, unreal.Texture2D)), None)
        if texture is None:
            raise RuntimeError(f"Expected Texture2D for {path}")
        texture.set_editor_property("srgb", role in ("color", "emissive"))
        texture.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_NORMALMAP
            if role == "normal" else unreal.TextureCompressionSettings.TC_DEFAULT
            if role in ("color", "emissive") else unreal.TextureCompressionSettings.TC_MASKS)
        if role == "normal":
            texture.set_editor_property("flip_green_channel", False)
        self.library.save_loaded_asset(texture)
        self.cache[cache_key] = texture
        self.report["assets"].append({"source": self.assets[key], "asset": texture.get_path_name(), "role": role})
        return texture

    def import_cube(self, key):
        cache_key = (key, "cubemap")
        if cache_key in self.cache:
            return self.cache[cache_key]
        path = self.asset_file(self.assets[key])
        # Use the native automated .hdr factory path. Its long/lat dialog state
        # is not exposed by this installed build's Python reflection; do not
        # attempt to set the private HDRImportShouldBeLongLatCubeMap field.
        factory = unreal.TextureFactory()
        task = unreal.AssetImportTask()
        task.filename = str(path)
        task.destination_path = DESTINATION + "/Textures"
        task.destination_name = "TC_Env_" + _name(key)
        task.automated = True
        task.replace_existing = True
        task.save = True
        task.factory = factory
        self.tools.import_asset_tasks([task])
        objects = list(task.get_objects()) or [self.library.load_asset(p) for p in task.imported_object_paths]
        cube = next((obj for obj in objects if isinstance(obj, unreal.TextureCube)), None)
        if cube is None:
            raise RuntimeError(f"HDR did not produce a TextureCube (not accepted as an IBL): {path}")
        cube.set_editor_property("srgb", False)
        self.library.save_loaded_asset(cube)
        self.cache[cache_key] = cube
        self.report["assets"].append({"source": self.assets[key], "asset": cube.get_path_name(), "role": "linear_HDR_cubemap"})
        return cube

    def material(self, name):
        path = DESTINATION + "/Materials/" + name
        material = self.library.load_asset(path)
        if material is None:
            material = self.tools.create_asset(name, DESTINATION + "/Materials", unreal.Material, unreal.MaterialFactoryNew())
        if not isinstance(material, unreal.Material):
            raise RuntimeError(f"Cannot create environment Material: {path}")
        unreal.MaterialEditingLibrary.delete_all_material_expressions(material)
        return material

    def make_background(self):
        texture = self.import_texture("T_General_BackGround_01a", "color")
        material = self.material("M_Env_OriginalBackdrop")
        material.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)
        material.set_editor_property("two_sided", True)
        material.set_editor_property("blend_mode", unreal.BlendMode.BLEND_OPAQUE)
        edit = unreal.MaterialEditingLibrary
        sample = edit.create_material_expression(material, unreal.MaterialExpressionTextureSample, -300, 0)
        sample.set_editor_property("texture", texture)
        sample.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_COLOR)
        if not edit.connect_material_property(sample, "RGB", unreal.MaterialProperty.MP_EMISSIVE_COLOR):
            raise RuntimeError("Failed to connect original backdrop texture to Unlit emissive")
        edit.recompile_material(material)
        self.library.save_loaded_asset(material)
        source = next(x for x in self.profile["background_profiles"] if x["material"] == "MI_HDRsky_General_BackGround_01a")
        self.report["materials"].append({"asset": material.get_path_name(), "source_material": source["material"],
            "implementation": "Original panorama + original sphere UV0, unlit and two-sided for the inner sphere.",
            "original_properties": source["properties"]})
        self.report["approximations"].append("Backdrop Rotate=3.1/HeightScale=5/Blend=.01 fork expressions are stripped: preserved as evidence, not assigned guessed units or UV equations. Original sphere UV0 and full panorama are used.")
        return material

    def make_floor_material(self, source_name):
        source = next(x for x in self.profile["floor_materials"] if x["material"] == source_name)
        textures = {x["ParameterInfo"]["Name"]: _stem(x.get("ParameterValue"))
                    for x in source["properties"].get("TextureParameterValues", [])}
        spec = {"name": "Env_" + source_name, "slot": 0, "base_color_factor": [1, 1, 1, 1],
                "roughness_factor": 0.45, "metallic_factor": 0.0, "two_sided": False,
                "blend_mode": "opaque", "mapping_verified": True}
        scalars = {x["ParameterInfo"]["Name"]: x["ParameterValue"]
                   for x in source["properties"].get("ScalarParameterValues", [])}
        if source_name == "VMI_General_Background_Ground_01b":
            # The source floor is translucent; an opaque reconstruction hid
            # the background and produced a large solid grey slab. Until the
            # original distance-fade equation is recovered, use its explicit
            # opacity upper bound rather than silently forcing opaque.
            spec["blend_mode"] = "translucent"
            spec["opacity_factor"] = float(scalars["OpacityMaskMax"])
        if source_name == "VMI_Generic_EmmisiveLighting_PatchLight02a":
            ground_component = next(x for x in self.profile["background_geometry"]
                if _stem(x["properties"]["StaticMesh"]) == "SM_General_Background_Ground01a"
                and Path(x["source"]["json"]).stem == "Lobby_Scene_General_Background")
            primitive_data = ground_component["properties"].get("CustomPrimitiveData", {}).get("Data", [])
            if len(primitive_data) >= 3:
                # The source M_Lighting_StandardMaster EmissionColor vector
                # explicitly uses CustomPrimitiveData at its default index0.
                spec["emissive_factor"] = primitive_data[:3]
        for parameter, channel in (("BaseColorMap", "base_color"), ("NormalMap", "normal"), ("EmissiveMap", "emissive")):
            key = textures.get(parameter)
            if key:
                if key not in self.assets:
                    raise RuntimeError(f"Required original ground material texture missing: {key}")
                spec[channel] = "Environment/" + self.assets[key]
        material = self.make_material(spec, DESTINATION + "/Materials")
        self.report["materials"].append({"asset": material.get_path_name(), "source_material": source_name,
            "bindings": spec, "original_properties": source["properties"],
            "approximate_fields": {"roughness": 0.45, "metallic": 0.0,
                "constant_source_opacity_upper_bound_instead_of_original_distance_fade": source_name == "VMI_General_Background_Ground_01b"}})
        return material

    def import_static_mesh(self, key):
        path = self.asset_file(self.assets[key])
        metadata = glb_metadata(path)
        objects = self.import_task(path, DESTINATION + "/Meshes", _name(key))
        meshes = [obj for obj in objects if isinstance(obj, unreal.StaticMesh)]
        if len(meshes) != 1:
            raise RuntimeError(f"Environment GLB must yield one StaticMesh: {key}; got {len(meshes)}")
        mesh = meshes[0]
        bounds = mesh.get_bounds()
        actual_extent = [bounds.box_extent.x, bounds.box_extent.y, bounds.box_extent.z]
        actual_origin = [bounds.origin.x, bounds.origin.y, bounds.origin.z]
        expected = metadata["expected_extent_cm"]
        for index in range(3):
            tolerance = max(0.1, abs(expected[index]) * 0.005)
            if not math.isfinite(actual_extent[index]) or abs(actual_extent[index] - expected[index]) > tolerance:
                raise RuntimeError(f"glTF metre/axis conversion mismatch for {key}: {actual_extent} != {expected}; no second manual 100x scale applied")
            if abs(actual_origin[index] - metadata["expected_origin_cm"][index]) > tolerance:
                raise RuntimeError(f"glTF origin conversion mismatch for {key}")
        slots = list(mesh.get_editor_property("static_materials"))
        if len(slots) != len(metadata["materials"]):
            raise RuntimeError(f"Imported material-slot count differs from original GLB: {key}")
        self.report["meshes"].append({"source": self.assets[key], "asset": mesh.get_path_name(),
            "original_glb": metadata, "imported_extent_cm": actual_extent, "imported_origin_cm": actual_origin,
            "units_and_axes_verified": True, "manual_geometry_scale_applied": 1.0,
            "slot_names": [str(s.get_editor_property("material_slot_name")) for s in slots]})
        return mesh, metadata

    def component_transform(self, record, scene_records):
        indexed = {x["source"]["export_index"]: x for x in scene_records}
        chain = []
        visited = set()
        def compose(current):
            props = current.get("properties", {})
            chain.append(current["source"])
            transform = unreal.Transform(location=unreal.Vector(*_vector(props.get("RelativeLocation"))),
                rotation=_rotation(props.get("RelativeRotation")),
                scale=unreal.Vector(*_vector(props.get("RelativeScale3D"), (1, 1, 1))))
            parent = (props.get("AttachParent") or {}).get("ObjectPath", "")
            match = re.search(r"\.(\d+)$", parent)
            if match:
                index = int(match[1])
                if index in visited:
                    raise ValueError("Cyclic original scene component hierarchy")
                visited.add(index)
                if index not in indexed:
                    raise ValueError(f"Original parent component absent from scene evidence: {parent}")
                transform = transform.multiply(compose(indexed[index]))
            return transform
        result = compose(record)
        return result, chain

    def build_lights(self):
        records = self.profile["lighting_profiles"]["Lobby_Scene_Light_Role"]["lights"]
        lights, sky = [], None
        for row in records:
            props = row["properties"]
            if props.get("bAffectsWorld") is False:
                self.report["omitted"].append({"source": row["source"], "reason": "bAffectsWorld=false in original scene"})
                continue
            if row["type"] == "SkyLightComponent":
                sky = row
                continue
            if row["type"] not in ("SpotLightComponent", "PointLightComponent", "DirectionalLightComponent"):
                continue
            transform, chain = self.component_transform(row, self.standard["Lobby_Scene_Light_Role"])
            rotation = transform.rotation.rotator()
            spec = unreal.ViewerEnvironmentLight()
            properties = {"id": row["name"], "kind": row["type"].removesuffix("LightComponent"),
                "location": transform.translation, "rotation": rotation,
                "intensity": float(props.get("Intensity", 1.0)), "color": _linear_color(props.get("LightColor"), True),
                "radius": float(props.get("AttenuationRadius", 1000.0)),
                "inner_cone": float(props.get("InnerConeAngle", 0.0)), "outer_cone": float(props.get("OuterConeAngle", 44.0)),
                "source_radius": float(props.get("SourceRadius", 0.0)), "source_length": float(props.get("SourceLength", 0.0)),
                "temperature": float(props.get("Temperature", 6500.0)), "use_temperature": bool(props.get("bUseTemperature", False)),
                "cast_shadows": bool(props.get("CastShadows", True)),
                "inverse_squared_falloff": bool(props.get("bUseInverseSquaredFalloff", True)),
                "falloff_exponent": float(props.get("LightFalloffExponent", 8.0)),
                "specular_scale": float(props.get("SpecularScale", 1.0)), "shadow_bias": float(props.get("ShadowBias", 0.5)),
                "intensity_units": {"ELightUnits::Unitless": 0, "ELightUnits::Candelas": 1,
                                    "ELightUnits::Lumens": 2, "ELightUnits::EV": 3}.get(props.get("IntensityUnits"), 0)}
            for key, value in properties.items():
                spec.set_editor_property(key, value)
            lights.append(spec)
            self.report["lights"].append({"source": row["source"], "original_properties": props,
                "world_transform": _json_transform(transform), "parent_chain": chain,
                "UE5_intensity_units_enum": properties["intensity_units"],
                "unserialized_values_use_explicit_viewer_defaults": [key for key in
                    ("CastShadows", "bUseInverseSquaredFalloff", "LightFalloffExponent", "AttenuationRadius", "SpecularScale") if key not in props]})
        if sky is None:
            raise ValueError("Original Scene103 SkyLight evidence missing")
        return lights, sky

    def build_post_process(self):
        row = self.profile["lighting_profiles"]["Lobby_Scene_Light_Role"]["post_process"][0]
        source = row["properties"]["Settings"]
        result = unreal.PostProcessSettings()
        copied, skipped = {}, []
        for original, python_name in (("ColorContrastMidtones", "color_contrast_midtones"),
            ("VignetteIntensity", "vignette_intensity"), ("DepthOfFieldFocalRegion", "depth_of_field_focal_region"),
            ("DepthOfFieldFarTransitionRegion", "depth_of_field_far_transition_region"),
            ("DepthOfFieldNearTransitionRegion", "depth_of_field_near_transition_region"),
            ("DepthOfFieldNearDistance", "depth_of_field_near_distance")):
            if not source.get("bOverride_" + original) or original not in source:
                continue
            value = source[original]
            if original == "ColorContrastMidtones":
                value = unreal.Vector4(*(float(value[k]) for k in "XYZW"))
            try:
                result.set_editor_property(python_name, value)
                result.set_editor_property("override_" + python_name, True)
                copied[original] = source[original]
            except Exception as error:
                skipped.append({"field": original, "reason": str(error)})
        # Fixed manual EV0 is a documented UE5 compatibility choice for the
        # original fixed Min/Max0 UI exposure, not a recovered fork implementation.
        for key, value in {"override_auto_exposure_method": True, "auto_exposure_method": unreal.AutoExposureMethod.AEM_MANUAL,
            "override_auto_exposure_apply_physical_camera_exposure": True, "auto_exposure_apply_physical_camera_exposure": False,
            "override_auto_exposure_bias": True, "auto_exposure_bias": 0.0,
            "override_motion_blur_amount": True, "motion_blur_amount": 0.0,
            "override_depth_of_field_scale": True, "depth_of_field_scale": 0.0,
            "override_lens_flare_intensity": True, "lens_flare_intensity": 0.0,
            "override_bloom_intensity": True, "bloom_intensity": 0.15}.items():
            result.set_editor_property(key, value)
        self.report["post_process"] = {"source": row["source"], "copied_active_overrides": copied,
            "unsupported_properties": skipped, "compatibility_exposure": "manual EV0, physical camera exposure disabled",
            "lens_flare": "disabled: original3.7 has no active override", "depth_of_field": "disabled: no serialized scale; user preference can enable viewer DOF",
            "bloom": "stock UE intensity.15 for the viewer Bloom setting; mild visual calibration, not the source fork MobileBloom conversion. Original nested settings retained in source evidence"}
        self.report["approximations"].append("UE5 manual EV0/exposure and omitted custom mobile Bloom/DOF/UI fields are explicit compatibility choices; original rendering response is unverified.")
        return result

    def create_environment(self, name, meshes, lights, cubemap, sky, post_process, camera):
        destination = DESTINATION + "/" + name
        asset = self.library.load_asset(destination)
        if asset is None:
            factory = unreal.DataAssetFactory()
            factory.set_editor_property("data_asset_class", unreal.ViewerEnvironment)
            asset = self.tools.create_asset(name, DESTINATION, unreal.ViewerEnvironment, factory)
        if not isinstance(asset, unreal.ViewerEnvironment):
            raise RuntimeError(f"Wrong DataAsset class at {destination}")
        props = sky["properties"]
        values = {"meshes": meshes, "lights": lights, "cubemap": cubemap,
            "sky_intensity": float(props.get("Intensity", 1.0)), "sky_color": _linear_color(props.get("LightColor"), True),
            "post_process": post_process, "camera_horizontal_fov": camera["fov_inference_not_runtime_measurement"]["horizontal_from_filmback_and_focal_degrees"],
            "reference_aspect_ratio": 36.0 / 20.25, "camera_distance": camera["actor_transform"]["RelativeLocation"]["Y"],
            "camera_height": camera["actor_transform"]["RelativeLocation"]["Z"],
            "camera_pitch": camera["actor_transform"]["RelativeRotation"]["Pitch"],
            "camera_yaw": camera["actor_transform"]["RelativeRotation"]["Yaw"],
            "reconstruction_notes": "Scene103 original geometry/UVs/transforms, HDR and serialized lights. Unlit panorama/floor PBR are documented approximations; original custom shaders, reflections, particles and fork normalization are not reproduced. See Saved/EnvironmentImportReport.json."}
        for key, value in values.items():
            asset.set_editor_property(key, value)
        source_transform = camera.get("world_transform")
        if source_transform is None and camera.get("actor_transform"):
            original = camera["actor_transform"]
            source_transform = unreal.Transform(location=unreal.Vector(*_vector(original.get("RelativeLocation"))),
                rotation=_rotation(original.get("RelativeRotation")),
                scale=unreal.Vector(1, 1, 1))
        if source_transform is not None:
            asset.set_editor_property("use_source_camera", True)
            asset.set_editor_property("source_camera_transform", source_transform)
            asset.set_editor_property("preserve_model_origin", True)
        self.library.save_loaded_asset(asset)
        self.report["environment_asset"] = asset.get_path_name()
        self.report["camera"] = {"source": camera["source"], "actor_transform": camera["actor_transform"],
            "horizontal_fov": values["camera_horizontal_fov"], "stored_vertical_fov": camera["stored_field_of_view_degrees"],
            "runtime_limit": "Source camera transform is used directly when available; mesh origins are retained. Source ModelLocationZ=90 refers to the original character actor/capsule system and is not added a second time to the recovered floor-origin mesh."}
        self.report["sky"] = {"source": sky["source"], "cubemap": cubemap.get_path_name(),
            "intensity": values["sky_intensity"], "intensity_is_serialized": "Intensity" in props,
            "default_intensity_reason": "Explicit viewer fallback1 when source value absent; not a measured original intensity."}
        return asset

    def standard_scene(self):
        self.verify_inputs()
        if not hasattr(unreal, "ViewerEnvironment"):
            raise RuntimeError("Compile the ViewerEnvironment UDataAsset class before import")
        cubes = {key: self.import_cube(key) for key in ("IBL_Role01_M", "IBL_Cha_01_M", "IBL_Studio_01_Mobile")}
        self.import_texture("T_Ground_Reflection_02a", "color")
        background = self.make_background()
        ground = self.make_floor_material("VMI_General_Background_Ground_01b")
        patch = self.make_floor_material("VMI_Generic_EmmisiveLighting_PatchLight02a")
        material_map = {"MI_HDRsky_General_BackGround_01a": background,
                        "VMI_General_Background_Ground_01b": ground,
                        "VMI_Generic_EmmisiveLighting_PatchLight02a": patch}
        components = []
        scene_records = self.standard["Lobby_Scene_General_Background"]
        for row in self.profile["background_geometry"]:
            if Path(row["source"]["json"]).stem != "Lobby_Scene_General_Background":
                continue
            key = _stem(row["properties"]["StaticMesh"])
            mesh, metadata = self.import_static_mesh(key)
            slots = list(mesh.get_editor_property("static_materials"))
            ordered = [None] * len(slots)
            for source_index, source_name in enumerate(metadata["materials"]):
                target_material = material_map[source_name]
                expected = re.sub(r"[^a-z0-9]", "", source_name.lower())
                matches = [i for i, slot in enumerate(slots) if expected in (
                    re.sub(r"[^a-z0-9]", "", str(slot.get_editor_property("material_slot_name")).lower()),
                    re.sub(r"[^a-z0-9]", "", str(slot.get_editor_property("imported_material_slot_name")).lower()))]
                target = matches[0] if len(matches) == 1 else source_index
                if len(matches) != 1:
                    self.warning(f"{key}: material name not unique; using actual original GLB material index {source_index}, not cooked legacy override-array index.")
                ordered[target] = target_material
                mesh.set_material(target, target_material)
            if any(m is None for m in ordered):
                raise RuntimeError(f"Incomplete environment material assignment: {key}")
            self.library.save_loaded_asset(mesh)
            transform, hierarchy = self.component_transform(row, scene_records)
            spec = unreal.ViewerEnvironmentMesh()
            spec.set_editor_property("id", key)
            spec.set_editor_property("mesh", mesh)
            spec.set_editor_property("materials", ordered)
            spec.set_editor_property("transform", transform)
            spec.set_editor_property("cast_shadows", False)
            components.append(spec)
            self.report["meshes"][-1].update(world_transform=_json_transform(transform), original_parent_chain=hierarchy,
                materials=[m.get_path_name() for m in ordered])
        if len(components) != 2:
            raise RuntimeError(f"Scene103 should have its original background sphere and ground: got {len(components)}")
        lights, sky = self.build_lights()
        camera = next(x for x in self.profile["camera_profiles"] if x["scene"] == "Lobby_Scene_Camera_Role"
                      and x["actor"] == "Role_CameraActor_Female")
        asset = self.create_environment("DA_Environment", components, lights, cubes["IBL_Role01_M"], sky,
                                        self.build_post_process(), camera)
        self.report["approximations"].append("Ground keeps its original mesh, UVs, BaseColor/Normal, source translucent blend and opacity upper bound. Constant opacity.1/roughness.45/metallic0 approximate the stripped distance-fade/reflection shader; the emissive patch uses its source CPD EmissionColor.")
        self.report["omitted"].append({"feature": "BoxReflectionCapture / IBL_Cha_01_M", "reason": "HDR imported and preserved; existing runtime environment class has no reflection-capture component field."})
        self.report["omitted"].append({"feature": "Cascade particles and custom ShadowVolume", "reason": "Source module/property evidence is preserved but native UE5 implementations were not recovered."})
        self.report["omitted"].append({"feature": "Individual Queen/Naimei/Bianca/Lucia scenes", "reason": "This first DA is standard103 only; custom scene importers can reuse EnvironmentImporter primitives and create_environment."})
        self.library.save_directory(DESTINATION, only_if_is_dirty=True, recursive=True)
        self.report["status"] = "imported_requires_runtime_and_visual_validation"
        return asset


def run(PROJECT, import_task, make_material):
    """Import standard103 and return a JSON-safe report; always save a report."""
    project = Path(PROJECT).resolve()
    report_path = project / "Saved/EnvironmentImportReport.json"
    importer = None
    try:
        importer = EnvironmentImporter(project, import_task, make_material)
        importer.standard_scene()
        unreal.log("Original Scene103 source geometry/HDR environment imported: " + importer.report["environment_asset"])
        return importer.report
    except Exception:
        if importer is None:
            report = {"status": "failed_initialization", "error": traceback.format_exc()}
        else:
            importer.report["status"] = "failed"
            importer.report["error"] = traceback.format_exc()
            report = importer.report
        unreal.log_error(report["error"])
        raise
    finally:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report = importer.report if importer is not None else locals().get("report", {"status": "unknown"})
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
