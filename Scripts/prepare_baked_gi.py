"""Prepare one isolated, native CPU Lightmass feasibility map.

Run in the full Editor (the PythonScript commandlet has no mesh subsystem):
  UnrealEditor PROJECT /Engine/Maps/Entry -ExecutePythonScript=THIS_FILE -NullRHI
  '-ini:Engine:[/Script/Engine.RendererSettings]:r.AllowStaticLighting=True'

Default GIMode=prepare creates only /Game/Viewer/GI/L_GI_Standard and isolated
mesh copies under /Game/Viewer/GI. GIMode=audit only reads source assets.
GIMode=verify loads the prepared map without modifying it. Nothing invokes a
lighting build, changes project configuration, or starts the viewer/simulator.
GIMode=configure is an explicit proof helper that only changes this test map's
native bounce counts; it requires both -GIIndirectBounces=N -GISkyBounces=N.
An existing test map is left intact unless -GIReplacePreparedMap is explicit.
See Saved/FullRendering/BakedGIPrepare.json for evidence and the build command.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import traceback

import unreal

ROOT = Path(unreal.Paths.project_dir()).resolve()
DEST = "/Game/Viewer/GI"
MAP = DEST + "/L_GI_Standard"
ENVIRONMENT = "/Game/Viewer/Environment/DA_Environment"
COMMAND_LINE = unreal.SystemLibrary.get_command_line()
MODE_MATCH = re.search(r"(?:^|\s)-GIMode=(\S+)", COMMAND_LINE, re.I)
MODE = MODE_MATCH.group(1).lower() if MODE_MATCH else "prepare"
TAG_MATCH = re.search(r"(?:^|\s)-GIProofTag=([a-z0-9_-]+)(?:\s|$)", COMMAND_LINE, re.I)
PROOF_TAG = TAG_MATCH.group(1) if TAG_MATCH else None
REPORT_NAME = {"verify": "BakedGIVerify.json", "audit": "BakedGISourceAudit.json", "configure": "BakedGIConfigure.json"}.get(MODE, "BakedGIPrepare.json")
if MODE == "verify" and PROOF_TAG:
    REPORT_NAME = f"BakedGIVerify-{PROOF_TAG}.json"
REPORT_PATH = ROOT / "Saved/FullRendering" / REPORT_NAME
REPORT = {"status": "started", "mode": MODE, "recorded_utc": datetime.now(timezone.utc).isoformat(),
          "map": MAP, "environment": ENVIRONMENT, "meshes": [], "lights": [], "materials": [], "warnings": [],
          "lighting_build": "not_invoked_by_prepare", "runtime_integration": "not_changed",
          "visual_and_device_validation": "not_run", "source_assets_saved": False}
LIBRARY = unreal.EditorAssetLibrary


def asset_file(asset):
    package = asset.get_path_name().split(".", 1)[0]
    if package.startswith("/Game/"):
        return ROOT / "Content" / (package[6:] + ".uasset")
    return None


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path and path.is_file() else None


def vector(value):
    return [value.x, value.y, value.z]


def transform(value):
    return {"location_cm": vector(value.translation), "scale": vector(value.scale3d),
            "rotation_xyzw": [value.rotation.x, value.rotation.y, value.rotation.z, value.rotation.w]}


def base_material(material):
    while isinstance(material, unreal.MaterialInstance):
        material = material.get_editor_property("parent")
    return material


def material_info(material):
    base = base_material(material)
    shading = str(base.get_editor_property("shading_model"))
    return {"asset": material.get_path_name(), "base": base.get_path_name(), "shading_model": shading,
            "unlit": "UNLIT" in shading.upper()}


def require_subsystem(cls):
    result = unreal.get_editor_subsystem(cls)
    if result is None:
        raise RuntimeError(f"{cls.__name__} unavailable; use full Editor -ExecutePythonScript with -NullRHI")
    return result


def spawn(subsystem, cls, label, location=None, rotation=None):
    actor = subsystem.spawn_actor_from_class(cls, location or unreal.Vector(), rotation or unreal.Rotator())
    if actor is None:
        raise RuntimeError(f"Cannot spawn {label}")
    actor.set_actor_label(label)
    return actor


def inspect_sources(environment, meshes):
    for spec in environment.get_editor_property("meshes"):
        mesh = spec.get_editor_property("mesh")
        if not isinstance(mesh, unreal.StaticMesh):
            raise RuntimeError(f"Unresolved source mesh: {mesh}")
        materials = list(spec.get_editor_property("materials"))
        if not materials or any(not isinstance(item, unreal.MaterialInterface) for item in materials):
            raise RuntimeError("Standard environment has unresolved materials")
        build = meshes.get_lod_build_settings(mesh, 0)
        row = {"id": str(spec.get_editor_property("id")), "source": mesh.get_path_name(),
               "source_sha256_before": digest(asset_file(mesh)), "source_transform": transform(spec.get_editor_property("transform")),
               "source_uv_channels_lod0": meshes.get_num_uv_channels(mesh, 0),
               "source_lightmap_index": mesh.get_editor_property("light_map_coordinate_index"),
               "source_lightmap_resolution": mesh.get_editor_property("light_map_resolution"),
               "source_generates_lightmap_uv": bool(build.get_editor_property("generate_lightmap_u_vs")),
               "materials": [dict(material_info(item), source_sha256_before=digest(asset_file(item))) for item in materials]}
        row["all_unlit"] = all(item["unlit"] for item in row["materials"])
        if row["source_uv_channels_lod0"] < 1:
            raise RuntimeError(f"No source UV0 for {row['id']}")
        REPORT["meshes"].append(row)


def prepare_mesh(spec, row, subsystem):
    source = spec.get_editor_property("mesh")
    if row["all_unlit"]:
        row["prepared_mesh"] = source.get_path_name()
        row["lightmap_policy"] = "unlit_backdrop_uses_original_asset_no_surface_lightmap"
        return source
    destination = DEST + "/Meshes/SM_GI_" + row["id"]
    mesh = LIBRARY.load_asset(destination) if LIBRARY.does_asset_exist(destination) else LIBRARY.duplicate_asset(source.get_path_name(), destination)
    if not isinstance(mesh, unreal.StaticMesh):
        raise RuntimeError(f"Cannot create isolated GI mesh: {destination}")
    settings = subsystem.get_lod_build_settings(mesh, 0)
    settings.set_editor_property("generate_lightmap_u_vs", True)
    settings.set_editor_property("src_lightmap_index", 0)
    settings.set_editor_property("dst_lightmap_index", 1)
    settings.set_editor_property("min_lightmap_resolution", 64)
    subsystem.set_lod_build_settings(mesh, 0, settings)
    mesh.set_editor_property("light_map_coordinate_index", 1)
    mesh.set_editor_property("light_map_resolution", 128)
    if not LIBRARY.save_loaded_asset(mesh):
        raise RuntimeError(f"Cannot save isolated GI mesh: {destination}")
    row.update({"prepared_mesh": mesh.get_path_name(), "lightmap_policy": "native_generate_uv0_to_uv1_isolated_copy",
                "prepared_uv_channels_lod0": subsystem.get_num_uv_channels(mesh, 0),
                "prepared_lightmap_index": 1, "prepared_lightmap_resolution": 128,
                "uv_nonoverlap_validation": "requires_actual_Lightmass_build_log"})
    return mesh


def prepare_material(material, mesh_row):
    if mesh_row["all_unlit"]:
        return material
    if not isinstance(material, unreal.Material):
        raise RuntimeError("Standard fixture expected base materials; copying material instances needs a separately audited parent chain")
    destination = DEST + "/Materials/M_GI_" + material.get_name()
    copy = LIBRARY.load_asset(destination) if LIBRARY.does_asset_exist(destination) else LIBRARY.duplicate_asset(material.get_path_name(), destination)
    if not isinstance(copy, unreal.Material):
        raise RuntimeError(f"Cannot create isolated GI material: {destination}")
    unreal.MaterialEditingLibrary.set_base_material_usage(copy, unreal.MaterialUsage.MATUSAGE_STATIC_LIGHTING, True)
    if not unreal.MaterialEditingLibrary.has_material_usage(copy, unreal.MaterialUsage.MATUSAGE_STATIC_LIGHTING):
        raise RuntimeError(f"StaticLighting material usage did not persist: {destination}")
    if not LIBRARY.save_loaded_asset(copy):
        raise RuntimeError(f"Cannot save GI material: {destination}")
    REPORT["materials"].append({"source": material.get_path_name(), "prepared": copy.get_path_name(),
                                "native_static_lighting_usage": True, "source_saved": False})
    return copy


def prepare(environment, meshes):
    levels = require_subsystem(unreal.LevelEditorSubsystem)
    actors = require_subsystem(unreal.EditorActorSubsystem)
    disk_map = ROOT / "Content/Viewer/GI/L_GI_Standard.umap"
    if disk_map.is_file() and "-gireplacepreparedmap" not in COMMAND_LINE.lower():
        raise RuntimeError("GI map already exists; use GIMode=verify or explicit -GIReplacePreparedMap")
    LIBRARY.make_directory(DEST + "/Meshes")
    LIBRARY.make_directory(DEST + "/Materials")
    if disk_map.is_file():
        if not levels.load_level(MAP):
            raise RuntimeError("Cannot reload isolated GI level")
        for actor in actors.get_all_level_actors():
            if actor.get_actor_label().startswith("GI_"):
                actors.destroy_actor(actor)
            elif not isinstance(actor, (unreal.WorldSettings, unreal.Brush)):
                raise RuntimeError(f"Refusing to replace unowned actor: {actor.get_actor_label()}")
    elif not levels.new_level(MAP):
        raise RuntimeError("Cannot create isolated GI level")
    world = require_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    settings = world.get_world_settings()
    settings.set_editor_property("default_game_mode", unreal.GameModeBase)
    settings.set_editor_property("force_no_precomputed_lighting", False)
    lightmass = settings.get_editor_property("lightmass_settings")
    for key, value in {"static_lighting_level_scale": 2.0, "num_indirect_lighting_bounces": 3,
                       "num_sky_lighting_bounces": 2, "indirect_lighting_quality": 1.0,
                       "volumetric_lightmap_detail_cell_size": 100.0,
                       "volumetric_lightmap_maximum_brick_memory_mb": 16.0,
                       "volume_lighting_method": unreal.VolumeLightingMethod.VLM_VOLUMETRIC_LIGHTMAP}.items():
        lightmass.set_editor_property(key, value)
    settings.set_editor_property("lightmass_settings", lightmass)
    REPORT["lightmass"] = {"method": str(lightmass.get_editor_property("volume_lighting_method")),
                          "bounces": 3, "sky_bounces": 2, "quality": "Preview_build_requested_separately",
                          "detail_cell_cm": 100, "brick_memory_mb": 16, "static_lighting_level_scale": 2}
    for spec, row in zip(environment.get_editor_property("meshes"), REPORT["meshes"]):
        mesh = prepare_mesh(spec, row, meshes)
        actor = spawn(actors, unreal.StaticMeshActor, "GI_" + row["id"])
        component = actor.static_mesh_component
        component.set_mobility(unreal.ComponentMobility.STATIC)
        component.set_static_mesh(mesh)
        # Lightmass only exports shadow-casting triangles into its ray scene.
        # The runtime source disables floor shadows because it uses only
        # dynamic IBL; this isolated native-GI fixture must include the floor
        # in transport and occlusion without changing the original asset.
        cast_static = not row["all_unlit"]
        component.set_editor_property("cast_shadow", cast_static)
        component.set_editor_property("cast_static_shadow", cast_static)
        row["source_cast_shadows"] = bool(spec.get_editor_property("cast_shadows"))
        row["prepared_cast_shadows"] = cast_static
        row["prepared_cast_static_shadow"] = cast_static
        if row["all_unlit"]:
            component.set_editor_property("lightmap_type", unreal.LightmapType.FORCE_VOLUMETRIC)
        for index, material in enumerate(spec.get_editor_property("materials")):
            prepared_material = prepare_material(material, row)
            component.set_material(index, prepared_material)
            if not row["all_unlit"]:
                mesh.set_material(index, prepared_material)
        if not row["all_unlit"] and not LIBRARY.save_loaded_asset(mesh):
            raise RuntimeError("Cannot save isolated mesh material assignments")
        if not actors.set_actor_transform(actor, spec.get_editor_property("transform")):
            raise RuntimeError(f"Cannot set original transform for {row['id']}")
        origin, extent = actor.get_actor_bounds(False)
        row["actor_bounds_cm"] = {"origin": vector(origin), "extent": vector(extent)}
        row["mobility"] = "Static"
    for spec in environment.get_editor_property("lights"):
        kind = str(spec.get_editor_property("kind"))
        cls = {"Directional": unreal.DirectionalLight, "Point": unreal.PointLight, "Spot": unreal.SpotLight}[kind]
        actor = spawn(actors, cls, "GI_" + str(spec.get_editor_property("id")), spec.get_editor_property("location"), spec.get_editor_property("rotation"))
        light = actor.get_component_by_class(unreal.LightComponent)
        light.set_mobility(unreal.ComponentMobility.STATIONARY)
        for target, source in {"intensity": "intensity", "cast_shadows": "cast_shadows", "temperature": "temperature",
                               "use_temperature": "use_temperature", "specular_scale": "specular_scale", "shadow_bias": "shadow_bias"}.items():
            light.set_editor_property(target, spec.get_editor_property(source))
        light.set_light_color(spec.get_editor_property("color"), False)
        mask = int(spec.get_editor_property("lighting_channel_mask"))
        light.set_editor_property("lighting_channels", unreal.LightingChannels(channel0=bool(mask & 1), channel1=bool(mask & 2), channel2=bool(mask & 4)))
        # Static lighting ignores lighting channels. Keep character-only fill
        # direct light, but do not bake its energy into the environment GI.
        light.set_editor_property("indirect_lighting_intensity", 1.0 if mask & 1 else 0.0)
        if kind != "Directional":
            units = int(spec.get_editor_property("intensity_units"))
            light.set_editor_property("intensity_units", {0: unreal.LightUnits.UNITLESS, 1: unreal.LightUnits.CANDELAS, 2: unreal.LightUnits.LUMENS}[units])
            for target, source in {"attenuation_radius": "radius", "use_inverse_squared_falloff": "inverse_squared_falloff",
                                   "light_falloff_exponent": "falloff_exponent", "source_radius": "source_radius", "source_length": "source_length"}.items():
                light.set_editor_property(target, spec.get_editor_property(source))
        if kind == "Spot":
            light.set_editor_property("inner_cone_angle", spec.get_editor_property("inner_cone"))
            light.set_editor_property("outer_cone_angle", spec.get_editor_property("outer_cone"))
        REPORT["lights"].append({"id": str(spec.get_editor_property("id")), "kind": kind, "mobility": "Stationary",
                                 "intensity": float(spec.get_editor_property("intensity")), "channel_mask": mask,
                                 "bake_indirect": bool(mask & 1)})
    sky = spawn(actors, unreal.SkyLight, "GI_Stationary_HDR_SkyLight").get_component_by_class(unreal.SkyLightComponent)
    sky.set_mobility(unreal.ComponentMobility.STATIONARY)
    sky.set_editor_property("source_type", unreal.SkyLightSourceType.SLS_SPECIFIED_CUBEMAP)
    sky.set_cubemap(environment.get_editor_property("cubemap"))
    sky.set_intensity(environment.get_editor_property("sky_intensity"))
    sky.set_light_color(environment.get_editor_property("sky_color"))
    sky.set_editor_property("cast_shadows", True)
    REPORT["sky"] = {"cubemap": sky.get_editor_property("cubemap").get_path_name(), "mobility": "Stationary",
                      "intensity": float(environment.get_editor_property("sky_intensity")), "cast_shadows": True,
                      "change_from_runtime": "enable_native_baked_sky_occlusion"}
    volume = spawn(actors, unreal.LightmassImportanceVolume, "GI_Character_Importance", unreal.Vector(0, 0, 120))
    _, native_extent = volume.get_actor_bounds(False)
    if min(vector(native_extent)) <= 0:
        raise RuntimeError("Native LightmassImportanceVolume has no brush geometry")
    volume.set_actor_scale3d(unreal.Vector(600.0 / native_extent.x, 600.0 / native_extent.y, 400.0 / native_extent.z))
    origin, extent = volume.get_actor_bounds(False)
    REPORT["importance_volume"] = {"origin_cm": vector(origin), "extent_cm": vector(extent),
                                   "character_region_cm": {"min": [-100, -100, 0], "max": [100, 100, 240]}}
    if min(vector(extent)) <= 0:
        raise RuntimeError("Importance volume scaling failed")
    spawn(actors, unreal.PlayerStart, "GI_Character_Region", unreal.Vector(0, 0, 90))
    if not levels.save_current_level():
        raise RuntimeError("Cannot save isolated GI level")
    REPORT["status"] = "prepared_requires_actual_lightmass_build"


def verify():
    levels = require_subsystem(unreal.LevelEditorSubsystem)
    if not levels.load_level(MAP):
        raise RuntimeError("Cannot load prepared GI map")
    settings = require_subsystem(unreal.UnrealEditorSubsystem).get_editor_world().get_world_settings()
    lightmass = settings.get_editor_property("lightmass_settings")
    REPORT["persisted_lightmass"] = {key: str(lightmass.get_editor_property(key)) for key in (
        "num_indirect_lighting_bounces", "num_sky_lighting_bounces", "volume_lighting_method",
        "volumetric_lightmap_detail_cell_size", "volumetric_lightmap_maximum_brick_memory_mb")}
    REPORT["persisted_test_meshes"] = []
    for actor in require_subsystem(unreal.EditorActorSubsystem).get_all_level_actors():
        if isinstance(actor, unreal.StaticMeshActor):
            component = actor.static_mesh_component
            materials = [component.get_material(index) for index in range(component.get_num_materials())]
            REPORT["persisted_test_meshes"].append({"actor": actor.get_actor_label(), "mesh": component.static_mesh.get_path_name(),
                "mobility": str(component.get_editor_property("mobility")), "cast_shadow": bool(component.get_editor_property("cast_shadow")),
                "cast_static_shadow": bool(component.get_editor_property("cast_static_shadow")),
                "materials": [{"asset": material.get_path_name(), "used_with_static_lighting":
                    bool(unreal.MaterialEditingLibrary.has_material_usage(material, unreal.MaterialUsage.MATUSAGE_STATIC_LIGHTING))}
                    for material in materials]})
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    registry.scan_paths_synchronous([DEST], True)
    REPORT["registered_assets"] = [{"path": str(item.package_name), "class": str(item.asset_class_path)}
                                  for item in registry.get_assets_by_path(DEST, recursive=True)]
    built = ROOT / "Content/Viewer/GI/L_GI_Standard_BuiltData.uasset"
    REPORT["map_build_data"] = {"file": str(built), "exists": built.is_file(), "bytes": built.stat().st_size if built.is_file() else 0,
                                "sha256": digest(built), "VLM_validity": "verify_with_actual_Lightmass_log_not_filename_alone"}
    REPORT["native_lightmap_exports"] = []
    output = REPORT_PATH.parent / "BakedGILightmaps"
    if PROOF_TAG:
        output = output / PROOF_TAG
    output.mkdir(exist_ok=True, parents=True)
    for texture in unreal.ObjectIterator(unreal.Texture2D):
        if not texture.get_path_name().startswith(MAP + "_BuiltData."):
            continue
        filename = output / (texture.get_name() + ".tga")
        task = unreal.AssetExportTask()
        task.object = texture
        task.filename = str(filename)
        task.exporter = unreal.TextureExporterTGA()
        task.automated = True
        task.prompt = False
        task.replace_identical = True
        succeeded = unreal.Exporter.run_asset_export_task(task)
        REPORT["native_lightmap_exports"].append({"asset": texture.get_path_name(), "file": str(filename),
                                                  "exported": bool(succeeded), "sha256": digest(filename)})
    REPORT["status"] = "build_data_present_requires_log_validation" if built.is_file() else "no_built_data"


def configure():
    values = {}
    for option, property_name in (("GIIndirectBounces", "num_indirect_lighting_bounces"), ("GISkyBounces", "num_sky_lighting_bounces")):
        match = re.search(r"(?:^|\s)-" + option + r"=(\d+)(?:\s|$)", COMMAND_LINE, re.I)
        if not match or not 0 <= int(match.group(1)) <= 10:
            raise ValueError(f"Proof configure requires explicit -{option}=0..10")
        values[property_name] = int(match.group(1))
    levels = require_subsystem(unreal.LevelEditorSubsystem)
    if not levels.load_level(MAP):
        raise RuntimeError("Cannot load isolated proof map for bounce configuration")
    settings = require_subsystem(unreal.UnrealEditorSubsystem).get_editor_world().get_world_settings()
    lightmass = settings.get_editor_property("lightmass_settings")
    REPORT["previous_bounces"] = {key: lightmass.get_editor_property(key) for key in values}
    for key, value in values.items():
        lightmass.set_editor_property(key, value)
    settings.set_editor_property("lightmass_settings", lightmass)
    if not levels.save_current_level():
        raise RuntimeError("Cannot save isolated proof bounce configuration")
    REPORT["configured_bounces"] = values
    REPORT["status"] = "isolated_map_bounces_configured_requires_rebuild"


try:
    if MODE not in ("audit", "prepare", "verify", "configure"):
        raise ValueError(f"Unknown GIMode: {MODE}")
    if MODE == "verify":
        verify()
    elif MODE == "configure":
        configure()
    else:
        environment = LIBRARY.load_asset(ENVIRONMENT)
        if not isinstance(environment, unreal.ViewerEnvironment):
            raise RuntimeError("Standard DA_Environment unavailable")
        mesh_subsystem = require_subsystem(unreal.StaticMeshEditorSubsystem)
        inspect_sources(environment, mesh_subsystem)
        REPORT["temporary_static_lighting_enabled"] = unreal.SystemLibrary.get_console_variable_int_value("r.AllowStaticLighting")
        if MODE == "prepare":
            if not REPORT["temporary_static_lighting_enabled"]:
                raise RuntimeError("Pass the command-line Engine ini override r.AllowStaticLighting=True; project config is intentionally unchanged")
            prepare(environment, mesh_subsystem)
        else:
            REPORT["status"] = "source_audit_only_no_assets_changed"
        for row, spec in zip(REPORT["meshes"], environment.get_editor_property("meshes")):
            row["source_sha256_after"] = digest(asset_file(spec.get_editor_property("mesh")))
            if row["source_sha256_before"] != row["source_sha256_after"]:
                raise RuntimeError("Source mesh package changed unexpectedly")
            for info, material in zip(row["materials"], spec.get_editor_property("materials")):
                info["source_sha256_after"] = digest(asset_file(material))
                if info["source_sha256_before"] != info["source_sha256_after"]:
                    raise RuntimeError("Source material package changed unexpectedly")
    REPORT["build_command_arguments"] = [str(ROOT / "NZMCharacterViewer.uproject"), "-run=ResavePackages", "-BuildLighting", "-Quality=Preview",
        "-MapsOnly", "-ProjectOnly", "-Map=L_GI_Standard", "-AllowCommandletRendering", "-Messaging", "-unattended", "-nosplash", "-NoSourceControl",
        "-ini:Engine:[/Script/Engine.RendererSettings]:r.AllowStaticLighting=True",
        "-ini:Lightmass:[DevOptions.StaticLighting]:bUseEmbree=False"]
    REPORT["mac_native_solver"] = "kDOP (bUseEmbree=False): proven on this installed x64 Lightmass under Rosetta; no config file changes"
    REPORT["build_command_source"] = "https://issues.unrealengine.com/issue/UE-281933"
except Exception:
    REPORT["status"] = "failed"
    REPORT["error"] = traceback.format_exc()
    unreal.log_error(REPORT["error"])
    raise
finally:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding="utf-8")
    unreal.log(f"GI_{MODE.upper()}_REPORT: {REPORT_PATH}")
