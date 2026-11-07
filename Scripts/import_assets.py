"""Run from UE5.8: Tools > Execute Python Script, after compiling this module.

Creates editable UE assets from SourceAssets (FBX preferred; GLB supported by
Interchange). Never loads original cooked UE4 uasset files into UE5.
Re-running updates the same project-owned /Game/Characters and /Game/Viewer
assets. Existing levels are opened, not erased. No network operations.
"""
import hashlib
import json
from pathlib import Path
import re
import sys
import traceback

import unreal

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from manifest_tools import source_file, validate_manifest
from material_policy import resolve_material_spec
from eye_material import build_eye

PROJECT = SCRIPT_DIR.parent
SOURCE = PROJECT / "SourceAssets"
REPORT_FILE = PROJECT / "Saved/ViewerImportReport.json"
TOOLS = unreal.AssetToolsHelpers.get_asset_tools()
LIBRARY = unreal.EditorAssetLibrary
REPORT = {"status": "started", "imported_meshes": [], "materials": [], "warnings": [], "limitations": []}
TEXTURES = {}
DYNAMICS = {}


def warning(message):
    REPORT["warnings"].append(message)
    unreal.log_warning(message)


def clean_name(value):
    return re.sub(r"[^A-Za-z0-9_]", "_", value)


def import_task(filename, destination, name, options=None):
    task = unreal.AssetImportTask()
    task.filename = str(filename)
    task.destination_path = destination
    task.destination_name = name
    task.automated = True
    task.replace_existing = True
    task.save = True
    if options is not None:
        task.options = options
    TOOLS.import_asset_tasks([task])
    objects = list(task.get_objects())
    if not objects:
        objects = [LIBRARY.load_asset(p) for p in task.imported_object_paths]
    return [obj for obj in objects if obj is not None]


def import_mesh(part, destination):
    source = source_file(SOURCE, part["mesh"])
    options = None
    if source.suffix.lower() == ".fbx":
        # The FBX source preserves up to eight influences. Keep the established
        # FBX path separate from the glTF >4-influence Interchange issue.
        world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
        unreal.SystemLibrary.execute_console_command(world, "Interchange.FeatureFlags.Import.FBX 0")
        options = unreal.FbxImportUI()
        options.set_editor_property("automated_import_should_detect_type", False)
        options.set_editor_property("mesh_type_to_import", unreal.FBXImportType.FBXIT_SKELETAL_MESH)
        options.set_editor_property("import_as_skeletal", True)
        options.set_editor_property("import_mesh", True)
        options.set_editor_property("import_animations", False)
        options.set_editor_property("import_materials", False)
        options.set_editor_property("import_textures", False)
        options.set_editor_property("create_physics_asset", False)
        mesh_data = options.get_editor_property("skeletal_mesh_import_data")
        mesh_data.set_editor_property("convert_scene", True)
        mesh_data.set_editor_property("convert_scene_unit", True)
        mesh_data.set_editor_property("import_uniform_scale", 1.0)
        mesh_data.set_editor_property("normal_import_method", unreal.FBXNormalImportMethod.FBXNIM_IMPORT_NORMALS_AND_TANGENTS)
        mesh_data.set_editor_property("update_skeleton_reference_pose", False)
    else:
        warning(f"{part['id']} 使用 Interchange glTF 导入；超过4权重的正确保留需要在UE实测，首选已校验FBX。")
    objects = import_task(source, destination, "SK_" + part["id"], options)
    meshes = [obj for obj in objects if isinstance(obj, unreal.SkeletalMesh)]
    if len(meshes) != 1:
        raise RuntimeError(f"{part['mesh']} 应生成一个 SkeletalMesh，实际 {len(meshes)} 个；未自动挑选或转为静态网格。")
    mesh = meshes[0]
    # Preserve source weights; the project setting caps the mobile target at 8.
    subsystem = unreal.get_editor_subsystem(unreal.SkeletalMeshEditorSubsystem)
    settings = subsystem.get_lod_build_settings(mesh, 0)
    settings.set_editor_property("bone_influence_limit", 8)
    settings.set_editor_property("use_high_precision_skin_weights", True)
    subsystem.set_lod_build_settings(mesh, 0, settings)
    skeleton = mesh.get_editor_property("skeleton")
    if not isinstance(skeleton, unreal.Skeleton):
        raise RuntimeError(f"{part['id']} 没有已导入的 Skeleton")
    # The legacy FBX task saves the primary mesh, but its generated Skeleton
    # can remain an unsaved editor object. Persist it before animation import.
    if not LIBRARY.save_loaded_asset(skeleton):
        raise RuntimeError(f"无法保存 {part['id']} 的 Skeleton")
    REPORT["imported_meshes"].append({"id": part["id"], "source": part["mesh"],
        "asset": mesh.get_path_name(), "slots": [str(s.material_slot_name) for s in mesh.materials],
        "bone_influence_limit": 8, "engine_weight_preservation_verified": False})
    return mesh


def import_texture(relative, role):
    if not relative:
        return None
    key = (relative, role)
    if key in TEXTURES:
        return TEXTURES[key]
    source = source_file(SOURCE, relative)
    digest = hashlib.sha1((relative + role).encode()).hexdigest()[:10]
    name = "T_" + clean_name(source.stem) + "_" + digest
    # Interchange may keep the source basename instead of destination_name.
    # Isolate each source/usage so equal basenames cannot overwrite a texture.
    objects = import_task(source, "/Game/Characters/Textures/" + digest, name)
    texture = next((o for o in objects if isinstance(o, unreal.Texture2D)), None)
    if texture is None:
        raise RuntimeError(f"PNG 导入未生成 Texture2D：{relative}")
    texture.set_editor_property("srgb", role in ("base_color", "emissive", "portrait"))
    texture.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_NORMALMAP
        if role == "normal" else unreal.TextureCompressionSettings.TC_DEFAULT
        if role in ("base_color", "emissive", "portrait") else unreal.TextureCompressionSettings.TC_MASKS)
    if role == "portrait":
        texture.set_editor_property("lod_group", unreal.TextureGroup.TEXTUREGROUP_UI)
        texture.set_editor_property("never_stream", True)
    if role == "normal":
        # PNG is exported from a UE normal texture, not a glTF-baked normal.
        texture.set_editor_property("flip_green_channel", False)
    LIBRARY.save_loaded_asset(texture)
    TEXTURES[key] = texture
    return texture


def expression(material, cls, x, y):
    return unreal.MaterialEditingLibrary.create_material_expression(material, cls, x, y)


def make_material(spec, destination):
    spec = resolve_material_spec(spec, SOURCE)
    masked_shadow = spec.get("cast_dynamic_shadow_as_masked", False)
    if not isinstance(masked_shadow, bool):
        raise ValueError("cast_dynamic_shadow_as_masked 必须为 JSON bool")
    serialized = json.dumps(spec, sort_keys=True, ensure_ascii=False)
    name = "M_" + clean_name(spec.get("name") or spec.get("slot_name") or f"Slot{spec['slot']}")
    name += "_" + hashlib.sha1(serialized.encode()).hexdigest()[:8]
    asset_path = destination + "/" + name
    material = LIBRARY.load_asset(asset_path)
    if material is None:
        material = TOOLS.create_asset(name, destination, unreal.Material, unreal.MaterialFactoryNew())
    if not isinstance(material, unreal.Material):
        raise RuntimeError(f"目标材质资产类型不正确：{asset_path}")
    edit = unreal.MaterialEditingLibrary
    edit.delete_all_material_expressions(material)
    material.set_editor_property("two_sided", bool(spec.get("two_sided", False)))
    mode = spec.get("blend_mode") or "opaque"
    if mode.lower() == "translucent":
        warning(f"{name}：保留透明混合，但原自定义shader/透明度方程未复原，使用清单已确认的透明度或常量近似。")
    modes = {"opaque": unreal.BlendMode.BLEND_OPAQUE, "masked": unreal.BlendMode.BLEND_MASKED,
             "translucent": unreal.BlendMode.BLEND_TRANSLUCENT}
    material.set_editor_property("blend_mode", modes.get(mode.lower(), unreal.BlendMode.BLEND_OPAQUE))
    material.set_editor_property("cast_dynamic_shadow_as_masked", masked_shadow)
    if mode.lower() == "translucent":
        material.set_editor_property("translucency_lighting_mode", unreal.TranslucencyLightingMode.TLM_SURFACE_PER_PIXEL_LIGHTING)
    edit.set_material_usage(material, unreal.MaterialUsage.MATUSAGE_SKELETAL_MESH)
    properties = {"base_color": unreal.MaterialProperty.MP_BASE_COLOR,
                  "normal": unreal.MaterialProperty.MP_NORMAL,
                  "roughness": unreal.MaterialProperty.MP_ROUGHNESS,
                  "metallic": unreal.MaterialProperty.MP_METALLIC,
                  "emissive": unreal.MaterialProperty.MP_EMISSIVE_COLOR,
                  "opacity": unreal.MaterialProperty.MP_OPACITY if mode.lower() == "translucent" else unreal.MaterialProperty.MP_OPACITY_MASK}
    used = {"cast_dynamic_shadow_as_masked": bool(material.get_editor_property("cast_dynamic_shadow_as_masked"))}
    texture_uv = None
    if spec.get("texture_uv_bounds"):
        minimum_u, minimum_v, maximum_u, maximum_v = spec["texture_uv_bounds"]
        texture_uv = expression(material, unreal.MaterialExpressionTextureCoordinate, -1100, -320)
        offset = expression(material, unreal.MaterialExpressionConstant2Vector, -1100, -160)
        offset.set_editor_property("r", minimum_u)
        offset.set_editor_property("g", minimum_v)
        subtract = expression(material, unreal.MaterialExpressionSubtract, -900, -320)
        edit.connect_material_expressions(texture_uv, "", subtract, "A")
        edit.connect_material_expressions(offset, "", subtract, "B")
        extent = expression(material, unreal.MaterialExpressionConstant2Vector, -900, -160)
        extent.set_editor_property("r", maximum_u - minimum_u)
        extent.set_editor_property("g", maximum_v - minimum_v)
        texture_uv = expression(material, unreal.MaterialExpressionDivide, -700, -320)
        edit.connect_material_expressions(subtract, "", texture_uv, "A")
        edit.connect_material_expressions(extent, "", texture_uv, "B")
        used["texture_uv_bounds"] = spec["texture_uv_bounds"]
    base_color_sample = None
    emissive_output = None
    for index, (channel, prop) in enumerate(properties.items()):
        if channel == "opacity" and mode.lower() not in ("masked", "translucent"):
            continue
        if channel == "opacity" and spec.get("opacity_factor") is not None:
            # Explicit viewer overrides (notably the procedural head AO at 0)
            # take precedence over either a separate texture or base-color alpha.
            opacity = max(0.0, min(1.0, float(spec["opacity_factor"])))
            constant = expression(material, unreal.MaterialExpressionConstant, -480, index * 160)
            constant.set_editor_property("r", opacity)
            edit.connect_material_property(constant, "", prop)
            used["opacity_factor"] = opacity
            used["opacity_source"] = {"kind": "explicit_opacity_factor", "factor": opacity}
            continue
        if channel == "opacity" and not spec.get("opacity") and base_color_sample is not None:
            alpha_factor = max(0.0, min(1.0, float((spec.get("base_color_factor") or [1, 1, 1, 1])[3])))
            scalar = expression(material, unreal.MaterialExpressionConstant, -480, index * 160)
            scalar.set_editor_property("r", alpha_factor)
            multiply = expression(material, unreal.MaterialExpressionMultiply, -180, index * 160)
            edit.connect_material_expressions(base_color_sample, "A", multiply, "A")
            edit.connect_material_expressions(scalar, "", multiply, "B")
            edit.connect_material_property(multiply, "", prop)
            used["opacity_source"] = {"kind": "base_color_alpha", "texture": spec["base_color"],
                                      "channel": "A", "factor": alpha_factor}
            continue
        texture = import_texture(spec.get(channel), channel)
        if texture:
            sample = expression(material, unreal.MaterialExpressionTextureSample, -480, index * 160)
            sample.set_editor_property("texture", texture)
            if texture_uv is not None:
                if not edit.connect_material_expressions(texture_uv, "", sample, "UVs"):
                    raise RuntimeError("Material texture UV connection failed")
            if channel == "normal":
                sample.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL)
            elif channel not in ("base_color", "emissive"):
                sample.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_MASKS)
            if channel == "base_color":
                base_color_sample = sample
                factor = spec.get("base_color_factor") or [1, 1, 1, 1]
                color = expression(material, unreal.MaterialExpressionConstant3Vector, -480, -160)
                color.set_editor_property("constant", unreal.LinearColor(*factor[:3], 1.0))
                multiply = expression(material, unreal.MaterialExpressionMultiply, -180, 0)
                edit.connect_material_expressions(sample, "RGB", multiply, "A")
                edit.connect_material_expressions(color, "", multiply, "B")
                if spec.get("hair_tint_color") is not None:
                    # Source hair exposes bUsedColorTint; multiplying a zero
                    # tint unconditionally made Lucia hair mathematically black.
                    tint = expression(material, unreal.MaterialExpressionConstant3Vector, -480, -320)
                    tint.set_editor_property("constant", unreal.LinearColor(*spec["hair_tint_color"], 1.0))
                    weight = expression(material, unreal.MaterialExpressionConstant, -480, -480)
                    weight.set_editor_property("r", float(spec["hair_tint_weight"]))
                    blend = expression(material, unreal.MaterialExpressionLinearInterpolate, 20, 0)
                    edit.connect_material_expressions(sample, "RGB", blend, "A")
                    edit.connect_material_expressions(tint, "", blend, "B")
                    edit.connect_material_expressions(weight, "", blend, "Alpha")
                    edit.connect_material_property(blend, "", prop)
                    used["hair_tint"] = {"color": spec["hair_tint_color"], "weight": spec["hair_tint_weight"],
                                         "mode": "stock_lerp_approximation_not_original_Marschner"}
                else:
                    edit.connect_material_property(multiply, "", prop)
                used["base_color_factor"] = factor
            elif channel == "emissive" and spec.get("emissive_factor") is not None:
                color = expression(material, unreal.MaterialExpressionConstant3Vector, -480, index * 160 + 80)
                color.set_editor_property("constant", unreal.LinearColor(*spec["emissive_factor"], 1.0))
                multiply = expression(material, unreal.MaterialExpressionMultiply, -180, index * 160)
                edit.connect_material_expressions(sample, "RGB", multiply, "A")
                edit.connect_material_expressions(color, "", multiply, "B")
                edit.connect_material_property(multiply, "", prop)
                emissive_output = multiply
                used["emissive_factor"] = spec["emissive_factor"]
            else:
                edit.connect_material_property(sample, "RGB" if channel in ("normal", "emissive") else spec.get(channel + "_channel", "R"), prop)
            used[channel] = spec[channel]
            if channel == "opacity":
                used["opacity_source"] = {"kind": "separate_opacity_texture", "texture": spec[channel], "channel": spec.get("opacity_channel", "R")}
        elif channel == "base_color":
            factor = spec.get("base_color_factor") or [0.65, 0.65, 0.65, 1]
            constant = expression(material, unreal.MaterialExpressionConstant3Vector, -480, 0)
            constant.set_editor_property("constant", unreal.LinearColor(*factor[:3], 1.0))
            edit.connect_material_property(constant, "", prop)
            warning(f"{name}：缺少已确认底色贴图，使用清单颜色或中性灰。")
        elif channel in ("roughness", "metallic"):
            constant = expression(material, unreal.MaterialExpressionConstant, -480, index * 160)
            supplied = spec.get(channel + "_factor")
            factor = float(supplied) if supplied is not None else 0.58 if channel == "roughness" else 0.0
            constant.set_editor_property("r", max(0.0, min(1.0, factor)))
            edit.connect_material_property(constant, "", prop)
            used[channel + "_factor"] = factor
        elif channel == "opacity":
            opacity = float((spec.get("base_color_factor") or [1, 1, 1, 1])[3])
            constant = expression(material, unreal.MaterialExpressionConstant, -480, index * 160)
            constant.set_editor_property("r", max(0.0, min(1.0, opacity)))
            edit.connect_material_property(constant, "", prop)
            used["opacity_factor"] = opacity
            used["opacity_source"] = {"kind": "base_color_factor_without_texture", "factor": opacity}
    for layer_index, layer in enumerate(spec.get("emissive_layers", [])):
        sample = expression(material, unreal.MaterialExpressionTextureSample, -500, 1100 + layer_index * 260)
        sample.set_editor_property("texture", import_texture(layer["texture"], "emissive"))
        if texture_uv is not None:
            if not edit.connect_material_expressions(texture_uv, "", sample, "UVs"):
                raise RuntimeError("Star layer texture UV connection failed")
        tint = expression(material, unreal.MaterialExpressionConstant3Vector, -500, 1240 + layer_index * 260)
        tint.set_editor_property("constant", unreal.LinearColor(*layer["factor"], 1.0))
        multiply = expression(material, unreal.MaterialExpressionMultiply, -250, 1100 + layer_index * 260)
        edit.connect_material_expressions(sample, "RGB", multiply, "A")
        edit.connect_material_expressions(tint, "", multiply, "B")
        if emissive_output is not None:
            add = expression(material, unreal.MaterialExpressionAdd, 0, 1100 + layer_index * 260)
            edit.connect_material_expressions(emissive_output, "", add, "A")
            edit.connect_material_expressions(multiply, "", add, "B")
            emissive_output = add
        else:
            emissive_output = multiply
        edit.connect_material_property(emissive_output, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    if spec.get("emissive_layers"):
        used["emissive_layers"] = spec["emissive_layers"]
    if spec.get("specular_factor") is not None:
        scalar = expression(material, unreal.MaterialExpressionConstant, -180, 720)
        scalar.set_editor_property("r", max(0.0, min(1.0, float(spec["specular_factor"]))))
        edit.connect_material_property(scalar, "", unreal.MaterialProperty.MP_SPECULAR)
        used["specular_factor"] = spec["specular_factor"]
    eye = build_eye(material, spec, import_texture, expression)
    if eye:
        used["eye_reconstruction"] = eye
    if spec.get("anisotropy_factor") is not None:
        scalar = expression(material, unreal.MaterialExpressionConstant, -180, 880)
        scalar.set_editor_property("r", float(spec["anisotropy_factor"]))
        edit.connect_material_property(scalar, "", unreal.MaterialProperty.MP_ANISOTROPY)
        used["anisotropy_factor"] = spec["anisotropy_factor"]
    edit.recompile_material(material)
    LIBRARY.save_loaded_asset(material)
    REPORT["materials"].append({"asset": material.get_path_name(), "source": spec,
        "channels_used": used, "material_appearance": "basic_PBR_reconstruction_not_original_shader"})
    return material


def canonical_slot(value):
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def import_dynamics(relative, destination, mesh):
    cache_key = (relative, mesh.get_path_name())
    if cache_key in DYNAMICS:
        return DYNAMICS[cache_key]
    filename = source_file(SOURCE, relative)
    spec = json.loads(filename.read_text(encoding="utf-8"))
    # This viewer simulates existing segments; it does not invent virtual tips
    # for a source root that is only a leaf in a separate head skeleton.
    probe = unreal.new_object(unreal.SkeletalMeshComponent)
    probe.set_skinned_asset_and_update(mesh)
    names = [probe.get_bone_name(i) for i in range(probe.get_num_bones())]
    edges = [(str(probe.get_parent_bone(name)).casefold(), str(name).casefold()) for name in names]
    applicable = False
    for row in spec["chains"]:
        root = str(row["root_bone"]).casefold()
        excluded = {str(bone).casefold() for bone in row.get("exclude_bones", [])}
        applicable |= any(parent == root and child not in excluded for parent, child in edges)
    if not applicable:
        DYNAMICS[cache_key] = None
        REPORT.setdefault("secondary_motion_skipped", []).append({"source": relative,
            "mesh": mesh.get_path_name(), "reason": "No source root has a usable child segment in this mesh; virtual-tip simulation is not reconstructed."})
        warning(f"{mesh.get_name()}：动态配置没有可用子骨链，未挂接；保留普通骨骼待机。")
        return None
    name = clean_name(spec["asset_name"])
    path = destination + "/Dynamics/" + name
    profile = LIBRARY.load_asset(path) if LIBRARY.does_asset_exist(path) else None
    if profile is None:
        factory = unreal.DataAssetFactory()
        factory.set_editor_property("data_asset_class", unreal.ViewerDynamicsProfile)
        profile = TOOLS.create_asset(name, destination + "/Dynamics", unreal.ViewerDynamicsProfile, factory)
    if not isinstance(profile, unreal.ViewerDynamicsProfile):
        raise RuntimeError("Dynamics profile has wrong type: " + path)
    scalar_fields = ("root_bone", "stiffness", "damping", "world_location_damping", "world_rotation_damping",
        "radius", "limit_angle", "max_stretch_ratio", "target_framerate", "ignore_move_influence",
        "max_displacement_cm", "max_bend_degrees", "max_bones", "source_node")
    chains = []
    for row in spec["chains"]:
        chain = unreal.ViewerDynamicChain()
        for key in scalar_fields:
            if key in row:
                chain.set_editor_property(key, row[key])
        chain.set_editor_property("exclude_bones", row.get("exclude_bones", []))
        for key in ("gravity", "constant_force", "velocity_multiplier", "move_multiplier"):
            value = row.get(key)
            if value:
                chain.set_editor_property(key, unreal.Vector(value["X"], value["Y"], value["Z"]))
        chains.append(chain)
    profile.set_editor_property("chains", chains)
    for key in ("max_delta_seconds", "max_substeps", "constraint_iterations", "teleport_distance_cm",
                "teleport_rotation_degrees", "source_asset_path", "reconstruction_notes"):
        if key in spec:
            profile.set_editor_property(key, spec[key])
    if not LIBRARY.save_loaded_asset(profile):
        raise RuntimeError("Dynamics profile did not save: " + path)
    DYNAMICS[cache_key] = profile
    REPORT.setdefault("secondary_motion_profiles", []).append({"source": relative,
        "asset": profile.get_path_name(), "chains": len(chains), "solver": "parameter_guided_reconstruction"})
    return profile


def imported_slot_name(slot):
    # This editor-only field is reflected but has no direct Python attribute
    # in UE 5.8. The public slot name remains the primary matching key.
    try:
        return slot.get_editor_property("imported_material_slot_name")
    except (AttributeError, TypeError, RuntimeError):
        return ""


def build_part(part, destination):
    mesh = import_mesh(part, destination + "/Meshes")
    slots = list(mesh.materials)
    overrides = [slot.material_interface for slot in slots]
    for spec in part.get("materials", []):
        requested = canonical_slot(spec.get("slot_name", ""))
        matches = [i for i, slot in enumerate(slots) if requested and requested in
            (canonical_slot(slot.material_slot_name), canonical_slot(imported_slot_name(slot)))]
        if len(matches) == 1:
            index = matches[0]
        else:
            index = int(spec["slot"])
            warning(f"{part['id']} 的 {spec.get('slot_name', index)} 未唯一匹配UE槽名，按已验证导出槽序号 {index} 赋值，需编辑器目视复核。")
        if not 0 <= index < len(slots):
            raise RuntimeError(f"{part['id']} 的材质槽 {index} 超出导入网格范围 {len(slots)}")
        overrides[index] = make_material(spec, destination + "/Materials")
    component = unreal.ViewerPart()
    component.set_editor_property("id", part["id"])
    component.set_editor_property("display_name", unreal.Text(part["name"]))
    component.set_editor_property("mesh", mesh)
    component.set_editor_property("materials", overrides)
    component.set_editor_property("visible_by_default", part.get("default_visible", True))
    if part.get("dynamics"):
        component.set_editor_property("dynamics", import_dynamics(part["dynamics"], destination, mesh))
    if part.get("animation"):
        skeleton = mesh.get_editor_property("skeleton")
        options = unreal.FbxImportUI()
        options.set_editor_property("automated_import_should_detect_type", False)
        options.set_editor_property("mesh_type_to_import", unreal.FBXImportType.FBXIT_ANIMATION)
        options.set_editor_property("import_mesh", False)
        options.set_editor_property("import_animations", True)
        options.set_editor_property("skeleton", skeleton)
        animation_data = options.get_editor_property("anim_sequence_import_data")
        animation_data.set_editor_property("convert_scene", True)
        animation_data.set_editor_property("convert_scene_unit", True)
        animation_data.set_editor_property("import_uniform_scale", 1.0)
        objects = import_task(source_file(SOURCE, part["animation"]), destination + "/Animations", "A_" + part["id"] + "_Idle", options)
        animations = [o for o in objects if isinstance(o, unreal.AnimSequence)]
        if len(animations) != 1 or animations[0].get_editor_property("skeleton") != skeleton:
            raise RuntimeError(f"{part['id']} 动画必须唯一且绑定该部件的已导入 Skeleton")
        component.set_editor_property("animation", animations[0])
        component.set_editor_property("animation_loop", part.get("animation_loop", True))
    transform = part.get("transform", {})
    component.set_editor_property("relative_transform", unreal.Transform(
        location=unreal.Vector(*transform.get("translation", [0, 0, 0])),
        rotation=unreal.Rotator(pitch=transform.get("rotation", [0, 0, 0])[0],
                              yaw=transform.get("rotation", [0, 0, 0])[1],
                              roll=transform.get("rotation", [0, 0, 0])[2]),
        scale=unreal.Vector(*transform.get("scale", [1, 1, 1]))))
    LIBRARY.save_loaded_asset(mesh)
    return component


def create_stage_material():
    path = "/Game/Viewer/Materials/M_Stage"
    material = LIBRARY.load_asset(path)
    if material is None:
        material = TOOLS.create_asset("M_Stage", "/Game/Viewer/Materials", unreal.Material, unreal.MaterialFactoryNew())
    edit = unreal.MaterialEditingLibrary
    edit.delete_all_material_expressions(material)
    color = expression(material, unreal.MaterialExpressionConstant3Vector, -200, 0)
    color.set_editor_property("constant", unreal.LinearColor(0.025, 0.035, 0.05, 1))
    edit.connect_material_property(color, "", unreal.MaterialProperty.MP_BASE_COLOR)
    rough = expression(material, unreal.MaterialExpressionConstant, -200, 160)
    rough.set_editor_property("r", 0.66)
    edit.connect_material_property(rough, "", unreal.MaterialProperty.MP_ROUGHNESS)
    edit.recompile_material(material)
    LIBRARY.save_loaded_asset(material)


def import_ui_art():
    imported = []
    folder = SOURCE / "UI"
    if not folder.is_dir():
        return
    for source in sorted(folder.glob("*.png")):
        name = "T_" + clean_name(source.stem)
        path = "/Game/Viewer/UI/" + name
        objects = import_task(source, "/Game/Viewer/UI", name)
        texture = next((obj for obj in objects if isinstance(obj, unreal.Texture2D)), None)
        if texture is None:
            raise RuntimeError("UI texture was not imported: " + source.name)
        if texture.get_name() != name:
            if not LIBRARY.rename_asset(texture.get_path_name(), path):
                raise RuntimeError("UI texture could not be assigned its stable path: " + path)
            texture = LIBRARY.load_asset(path)
        texture.set_editor_property("lod_group", unreal.TextureGroup.TEXTUREGROUP_UI)
        texture.set_editor_property("srgb", True)
        texture.set_editor_property("never_stream", True)
        LIBRARY.save_loaded_asset(texture)
        imported.append(texture.get_path_name())
    REPORT["original_ui_art"] = imported


def run():
    if not hasattr(unreal, "ViewerCatalog"):
        raise RuntimeError("请先编译 NZMCharacterViewerEditor 模块，再运行导入脚本。")
    manifest, stats = validate_manifest(SOURCE)
    REPORT["manifest_stats"] = stats
    REPORT["limitations"] = manifest.get("limitations", [])
    if (SOURCE / "Environment/manifest.json").is_file():
        from import_environment import run as import_environment
        REPORT["environment"] = import_environment(PROJECT, import_task, make_material)
    if (SOURCE / "Environment/Custom/custom_manifest.json").is_file():
        from import_custom_environments import run as import_custom_environments
        REPORT["custom_environments"] = import_custom_environments(PROJECT, import_task, make_material,
            scene_ids=["queen", "lucia", "naimei", "bianca"])
    characters = []
    for source_character in manifest["characters"]:
        outfits = []
        for source_outfit in source_character["outfits"]:
            if not source_outfit.get("available_in_viewer", True):
                REPORT.setdefault("excluded_source_outfits", []).append({"character": source_character["id"], "outfit": source_outfit["id"]})
                continue
            destination = f"/Game/Characters/{source_character['id']}/{source_outfit['id']}"
            active_parts = [p for p in source_outfit["parts"] if p.get("available_in_viewer", True)]
            excluded = [p["id"] for p in source_outfit["parts"] if not p.get("available_in_viewer", True)]
            REPORT.setdefault("excluded_source_parts", []).append({"character": source_character["id"],
                "outfit": source_outfit["id"], "parts": excluded})
            parts = [build_part(p, destination + "/" + p["id"]) for p in active_parts]
            outfit = unreal.ViewerOutfit()
            outfit.set_editor_property("id", source_outfit["id"])
            outfit.set_editor_property("display_name", unreal.Text(source_outfit["name"]))
            outfit.set_editor_property("parts", parts)
            if source_outfit.get("environment_asset"):
                environment = LIBRARY.load_asset(source_outfit["environment_asset"])
                if not isinstance(environment, unreal.ViewerEnvironment):
                    raise RuntimeError("Outfit environment has not been imported: " + source_outfit["environment_asset"])
                outfit.set_editor_property("environment", environment)
            outfits.append(outfit)
        if not outfits:
            continue
        character = unreal.ViewerCharacter()
        character.set_editor_property("id", source_character["id"])
        character.set_editor_property("display_name", unreal.Text(source_character["name"]))
        if source_character.get("portrait"):
            character.set_editor_property("portrait", import_texture(source_character["portrait"], "portrait"))
        character.set_editor_property("outfits", outfits)
        characters.append(character)
    if not characters:
        raise RuntimeError("清单没有已完整转换、可供查看的角色服装组合")
    catalog = LIBRARY.load_asset("/Game/Viewer/Data/DA_ViewerCatalog")
    if catalog is None:
        factory = unreal.DataAssetFactory()
        factory.set_editor_property("data_asset_class", unreal.ViewerCatalog)
        catalog = TOOLS.create_asset("DA_ViewerCatalog", "/Game/Viewer/Data", unreal.ViewerCatalog, factory)
    catalog.set_editor_property("characters", characters)
    LIBRARY.save_loaded_asset(catalog)
    create_stage_material()
    import_ui_art()
    levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    level_path = "/Game/Viewer/Maps/L_Viewer"
    # EditorAssetLibrary's generic asset existence API excludes map assets.
    if (PROJECT / "Content/Viewer/Maps/L_Viewer.umap").is_file():
        if not levels.load_level(level_path):
            raise RuntimeError("无法打开已有查看器关卡")
    else:
        LIBRARY.make_directory("/Game/Viewer/Maps")
        if not levels.new_level(level_path):
            raise RuntimeError("无法创建查看器启动关卡")
        actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        actors.spawn_actor_from_class(unreal.PlayerStart, unreal.Vector(0, 0, 0))
    if not levels.save_current_level():
        raise RuntimeError("启动关卡保存失败")
    LIBRARY.save_directory("/Game/Viewer", only_if_is_dirty=True, recursive=True)
    LIBRARY.save_directory("/Game/Characters", only_if_is_dirty=True, recursive=True)
    verification = SCRIPT_DIR / "verify_imported_assets.py"
    if verification.is_file():
        import runpy
        runpy.run_path(str(verification), run_name="__main__")
        REPORT["structural_validation"] = "Saved/AssetValidation.json"
    REPORT["status"] = "completed_requires_visual_and_device_validation"
    unreal.log("人物导入完成。打开 L_Viewer 并点击 Play；请继续核验材质、骨骼及iOS运行效果。")


if __name__ == "__main__":
    try:
        run()
    except Exception:
        REPORT["status"] = "failed"
        REPORT["error"] = traceback.format_exc()
        unreal.log_error(REPORT["error"])
        raise
    finally:
        REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
        REPORT_FILE.write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding="utf-8")
