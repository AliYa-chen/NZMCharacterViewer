"""Build separately owned viewer Eye materials from explicit PC iris/sclera data.

First run --plan-only in ordinary Python (Pillow) to inspect source image sizes,
pupils and actual Eye-section UV0 ranges. Then run this script in UE5.8. The
original converted materials, textures, meshes and animation packages are only
read. New packages live exclusively below /Game/PCAssets/ViewerMaterials.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pc_viewer_materials import (OWNER, OWNER_TAG, ROOT, TARGET_ROOT, REPORT_PATH,
                                asset_file, digest, package)


WORK = ROOT / "Saved/PCViewerIntegration"
PLAN_PATH = WORK / "viewer_plan.json"
ASSET_PLAN_PATH = WORK / "asset_plan.json"
INDEX_PATH = ROOT / "Docs/PCAssetConversion/asset_index.json"
ANIMATION_PATH = WORK / "animation_report.json"
PLUGIN_PATH = ROOT / "Plugins/PCAssetImporter/Binaries/Mac/libUnrealEditor-PCAssetImporter.dylib"
BUILD_LOG_PATH = WORK / "eye_editor_build.log"
EYE_PLAN_PATH = WORK / "material_overrides_plan.json"
MASTER = TARGET_ROOT + "/M_PC_ViewerEye_v1"
BLACK = "/Engine/EngineResources/Black.Black"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(path).with_suffix(".json.new")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def parameters(row, kind):
    return {item["ParameterInfo"]["Name"]: item for item in row["effective_parameters"][kind]
            if item["ParameterInfo"].get("Association", "GlobalParameter") == "GlobalParameter"}


def scalar(values, name, default=None):
    value = values[name]["ParameterValue"] if name in values else default
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError("Missing/nonfinite Eye scalar: " + name)
    return float(value)


def color(values, name):
    value = values[name]["ParameterValue"]
    result = [float(value[key]) for key in ("R", "G", "B", "A")]
    if not all(math.isfinite(component) for component in result):
        raise ValueError("Nonfinite Eye color: " + name)
    return result


def source_texture(evidence, textures):
    from PIL import Image
    target = evidence["texture_target"]
    row = textures[target]
    settings = row["effective_settings"]
    if settings["texture_class"] != "Texture2D" or settings["compression_settings"] != "TC_Default" or settings["srgb"] is not True:
        raise ValueError("Eye RGB texture has unsupported source sampler semantics: " + target)
    path = ROOT / row["file"]
    if digest(path) != row["sha256"]:
        raise ValueError("Eye source PNG differs from the conversion plan: " + str(path))
    with Image.open(path) as image:
        dimensions = list(image.size)
        rgb = image.convert("RGB")
        center = list(rgb.getpixel((rgb.width // 2, rgb.height // 2)))
        colors = len(rgb.getcolors(rgb.width * rgb.height) or [])
    if dimensions != [row["dimensions"]["width"], row["dimensions"]["height"]] or min(dimensions) < 32 or colors < 2:
        raise ValueError("Eye source PNG is empty or has unexpected dimensions: " + str(path))
    return {"target": target, "source_package": evidence["texture_package"], "file": row["file"],
            "sha256": row["sha256"], "dimensions": dimensions, "center_rgb": center,
            "distinct_rgb_colors": colors, "parameter": evidence["ParameterInfo"]["Name"],
            "source_evidence": evidence}


def eye_uv_evidence(mesh, source_slot, radius):
    path = ROOT / mesh["file"]
    if digest(path) != mesh["sha256"]:
        raise ValueError("Eye mesh source differs from conversion plan")
    chunks = {}
    with path.open("rb") as stream:
        while header := stream.read(32):
            name, _, stride, count = struct.unpack("<20s3i", header)
            body = stream.read(stride * count)
            if len(body) != stride * count:
                raise ValueError("Truncated source Eye geometry")
            if name.rstrip(b"\0") == b"VTXW0000":
                chunks["wedges"] = (stride, count, body)
    stride, count, raw = chunks["wedges"]
    if stride != 16:
        raise ValueError("Unknown source Eye wedge layout")
    exported = {index for index, target in enumerate(mesh["exported_to_source_slots"]) if target == source_slot}
    coordinates = [struct.unpack_from("<ff", raw, index * stride + 4) for index in range(count)
                   if raw[index * stride + 12] in exported]
    if not coordinates or any(not math.isfinite(v) for uv in coordinates for v in uv):
        raise ValueError("Eye section has no finite UV0 coordinates")
    near_center = sum((u - .5)**2 + (v - .5)**2 <= radius**2 for u, v in coordinates)
    if not near_center:
        raise ValueError("Eye UV0 does not intersect the source-constrained iris disk")
    return {"file": mesh["file"], "sha256": mesh["sha256"], "channel": 0,
            "source_slot": source_slot, "exported_slots": sorted(exported), "wedges": len(coordinates),
            "minimum": [min(uv[a] for uv in coordinates) for a in range(2)],
            "maximum": [max(uv[a] for uv in coordinates) for a in range(2)],
            "wedges_inside_source_radius": near_center,
            "basis": "Actual source ActorX Eye-section UV0; centre/radius remap remains a documented stock approximation."}


def graph_evidence(material_row):
    name = "VM_Eye_Master_Emssive_PC" if any("Emssive" in value for value in material_row["parent_chain"]) else "VM_Eye_Master_PC"
    folder = ROOT / "PCResources/Shading/Dependencies/Metadata/MaterialLib/Materials/Character/NewCharacterMaterial/EyeMaterials" / name
    result = []
    for path in sorted(folder.glob("*.json")):
        properties = read(path).get("Properties", {})
        function = properties.get("MaterialFunction", {}).get("ObjectName", "")
        if properties.get("ParameterName") in ("IrisMap", "ScleraColor", "Iris UV Radius", "EmissiveMap") or "MF_Data_Eye" in function or "ScaleUVsByCenter" in function:
            result.append({"file": path.relative_to(ROOT).as_posix(), "sha256": digest(path), "properties": properties})
    samples = {value["properties"].get("ParameterName"): value["properties"] for value in result}
    if any(samples.get(name, {}).get("SamplerSource") != "SSM_Clamp_WorldGroupSettings" for name in ("IrisMap", "ScleraColor")):
        raise ValueError("Source Eye clamp sampler evidence is unavailable")
    return result


def build_plan():
    viewer, source, index, animations = map(read, (PLAN_PATH, ASSET_PLAN_PATH, INDEX_PATH, ANIMATION_PATH))
    source_materials = {row["target"]: row for row in source["materials"]}
    source_meshes = {row["target"]: row for row in source["meshes"]}
    source_textures = {row["target"]: row for row in source["textures"]}
    indexed = {row["target"]: row for row in index["assets"]}
    specifications, meshes, by_character = {}, {}, {}
    protected = {}

    def protect(target):
        if target is None or target in protected:
            return
        row, disk = indexed[target], asset_file(target)
        if digest(disk) != row["sha256"]:
            raise ValueError("Protected converted source package changed: " + target)
        protected[target] = {"target": target, "bytes": disk.stat().st_size, "sha256": row["sha256"]}

    for character in viewer["characters"]:
        count = 0
        for outfit in character["outfits"]:
            for part in outfit["parts"]:
                native = part["materials"]
                for slot, target in enumerate(native):
                    row = source_materials.get(target)
                    if not row or not any("/EyeMaterials/" in name for name in row.get("parent_chain", [])):
                        continue
                    textures = {item["ParameterInfo"]["Name"]: item for item in row["texture_parameters"]}
                    if not all(textures.get(name, {}).get("texture_target") for name in ("IrisMap", "ScleraColor")):
                        raise ValueError("Source Eye material is missing its explicit iris or sclera texture")
                    if target not in specifications:
                        scalars, vectors = parameters(row, "scalars"), parameters(row, "vectors")
                        radius = scalar(scalars, "Iris UV Radius")
                        if not .02 <= radius <= .45:
                            raise ValueError("Source Eye iris radius is outside safe UV bounds")
                        values = {"IrisRadius": radius, "IrisBrightness": scalar(scalars, "IrisBrightness"),
                                  "ScleraBrightness": scalar(scalars, "ScleraBrightness"),
                                  "ScleraScale": scalar(scalars, "ScaleByCenter", 1),
                                  "IrisRoughness": min(1., max(0., scalar(scalars, "IrisRoughness"))),
                                  "ScleraRoughness": min(1., max(0., scalar(scalars, "ScleraRoughness"))),
                                  "IrisSpecular": min(1., max(0., scalar(scalars, "SpecularityIris"))),
                                  "ScleraSpecular": min(1., max(0., scalar(scalars, "SpecularityScelera"))),
                                  "EmissionScale": scalar(scalars, "EmssiveIntensity", 1) if "EmissiveMap" in textures else 0.}
                        if any(not 0 <= values[name] <= 16 for name in ("IrisBrightness", "ScleraBrightness", "EmissionScale")) or not .01 <= values["ScleraScale"] <= 16:
                            raise ValueError("Source Eye brightness/UV scale is outside safe stock bounds")
                        key = hashlib.sha256((OWNER + "\n" + target).encode()).hexdigest()[:12]
                        name = re.sub("[^A-Za-z0-9_]", "_", row["source_package"].rsplit("/", 1)[-1])
                        evidence = {"original": target, "source_package": row["source_package"],
                                    "source_file": row["file"], "source_sha256": row["sha256"],
                                    "source_parent_chain": row["parent_chain"],
                                    "source_graph_evidence": graph_evidence(row),
                                    "original_adapter_channels": row.get("channels", {}),
                                    "iris": source_texture(textures["IrisMap"], source_textures),
                                    "sclera": source_texture(textures["ScleraColor"], source_textures),
                                    "emissive": source_texture(textures["EmissiveMap"], source_textures) if "EmissiveMap" in textures else None,
                                    "scalar_values": values, "vector_values": {"IrisTint": color(vectors, "IrisHueTint"), "ScleraTint": color(vectors, "ScleraTint")},
                                    "source_scalar_evidence": {name: scalars[name] for name in ("Iris UV Radius", "IrisBrightness", "ScleraBrightness", "IrisRoughness", "ScleraRoughness", "SpecularityIris", "SpecularityScelera", "ScaleByCenter", "EmssiveIntensity") if name in scalars},
                                    "source_vector_evidence": {name: vectors[name] for name in ("IrisHueTint", "ScleraTint")},
                                    "uv_approximation": "UV0 centred at .5; IrisMap maps a source-radius disk to [0,1]; ScleraColor scales UVs about the centre. Cooked PC refraction arithmetic is stripped.",
                                    "sampler_basis": "PC Eye master IrisMap/ScleraColor TextureSampleParameter2D explicitly uses SSM_Clamp_WorldGroupSettings.",
                                    "unimplemented": ["Original refraction/cornea/private Eye BRDF", "PupilScale deformation", "Fake lights/cubemap/highlight/limbus equations", "Normal/DetailNormal UV/refraction wiring", "Packed masks"]}
                        specifications[target] = {"target": TARGET_ROOT + "/MI_PC_Eye_" + name + "_" + key,
                                                  "evidence": evidence}
                    specification = specifications[target]
                    mesh_target = part["mesh_target"]
                    entry = meshes.setdefault(mesh_target, {"native_materials": native[:], "materials": native[:], "overrides": []})
                    if entry["native_materials"] != native:
                        raise ValueError("A reused mesh has inconsistent native source slots")
                    usage = {"character_id": character["id"], "outfit_id": outfit["id"], "part_id": part["id"]}
                    count += 1
                    previous = next((value for value in entry["overrides"] if value["slot"] == slot), None)
                    if previous:
                        previous["used_by"].append(usage)
                        continue
                    entry["materials"][slot] = specification["target"]
                    uv = eye_uv_evidence(source_meshes[mesh_target], slot, specification["evidence"]["scalar_values"]["IrisRadius"])
                    entry["overrides"].append({"slot": slot, "slot_name": part["slot_names"][slot], "original": target,
                                               "target": specification["target"], "source_evidence": specification["evidence"], "uv0_evidence": uv,
                                               "used_by": [usage]})
                    protect(mesh_target)
                    for value in native:
                        protect(value)
                    for key in ("iris", "sclera", "emissive"):
                        if specification["evidence"][key]:
                            protect(specification["evidence"][key]["target"])
        by_character[character["id"]] = {"overridden_eye_slots": count,
                                         "basis": "No separate Eye material exists in the source helmet/whole-body mesh." if not count else "Only explicit source Eye material slots are overridden."}
    if len(specifications) != 15 or len(by_character) != 12 or {key for key, value in by_character.items() if not value["overridden_eye_slots"]} != {"emma"}:
        raise ValueError("Current PC Eye material/character coverage differs from the reviewed scope")
    for row in animations["parts"]:
        if row["status"] in ("adapted", "reused"):
            disk = asset_file(row["animation"])
            if digest(disk) != row["asset_sha256"]:
                raise ValueError("Protected adapted animation package changed")
            protected[row["animation"]] = {"target": row["animation"], "bytes": row["asset_bytes"], "sha256": row["asset_sha256"]}
    return {"schema_version": 1, "owner": OWNER, "master": MASTER,
            "plan_sha256": digest(PLAN_PATH), "asset_plan_sha256": digest(ASSET_PLAN_PATH),
            "asset_index_sha256": digest(INDEX_PATH), "animation_report_sha256": digest(ANIMATION_PATH),
            "build_script_sha256": digest(Path(__file__)), "helper_sha256": digest(ROOT / "Scripts/pc_viewer_materials.py"),
            "native_plugin_sha256": digest(PLUGIN_PATH),
            "editor_build": {"file": BUILD_LOG_PATH.relative_to(ROOT).as_posix(), "sha256": digest(BUILD_LOG_PATH),
                             "status": "passed" if "Result: Succeeded" in BUILD_LOG_PATH.read_text() else "failed"},
            "material_specs": list(specifications.values()), "meshes": meshes, "characters": by_character,
            "protected_assets": list(protected.values()),
            "limitations": ["Stock DefaultLit radial iris/sclera approximation constrained by explicit PC source parameters.",
                            "Private Eye BRDF/refraction/pupil/cubemap/normal wiring is not recovered.",
                            "Iris/Sclera brightness and tint use stock multiply; BaseColor and roughness/specular use stock [0,1] ranges.",
                            "Iris edge feather .003 UV units is the existing viewer approximation.",
                            "Original converted packages, native material slots and adapted animations are unchanged."]}


def create_owned(ue, target, kind, factory):
    lib = ue.EditorAssetLibrary
    asset = ue.load_asset(target) if lib.does_asset_exist(target) else None
    if asset is not None:
        if not isinstance(asset, kind) or lib.get_metadata_tag(asset, OWNER_TAG) != OWNER:
            raise RuntimeError("Refusing to replace an unowned viewer material: " + target)
        return asset
    folder, name = target.rsplit("/", 1)
    asset = ue.AssetToolsHelpers.get_asset_tools().create_asset(name, folder, kind, factory())
    if not isinstance(asset, kind):
        raise RuntimeError("Could not create viewer Eye material")
    lib.set_metadata_tag(asset, OWNER_TAG, OWNER)
    return asset


def build_master(ue, spec):
    material = create_owned(ue, MASTER, ue.Material, ue.MaterialFactoryNew)
    edit = ue.MaterialEditingLibrary
    edit.delete_all_material_expressions(material)
    row = 0

    def node(cls):
        nonlocal row
        row += 1
        value = edit.create_material_expression(material, cls, -1800 + (row % 8) * 240, (row // 8) * 240)
        if value is None:
            raise RuntimeError("Could not create Eye graph expression")
        return value

    def wire(first, output, second, pin):
        names = [str(name) for name in edit.get_material_expression_input_names(second)]
        name = "UVs" if pin == "Coordinates" else pin
        if name not in names and len(names) == 1:
            name = names[0]
        if not edit.connect_material_expressions(first, output, second, name):
            raise RuntimeError("Could not connect Eye graph pin: " + name)

    def binary(kind, a, b, a_output="", b_output=""):
        result = node(kind)
        wire(a, a_output, result, "A"); wire(b, b_output, result, "B")
        return result

    def scalar_parameter(name, value):
        result = node(ue.MaterialExpressionScalarParameter)
        result.set_editor_property("parameter_name", name)
        result.set_editor_property("default_value", float(value))
        return result

    def vector_parameter(name, value):
        result = node(ue.MaterialExpressionVectorParameter)
        result.set_editor_property("parameter_name", name)
        result.set_editor_property("default_value", ue.LinearColor(*value))
        return result

    def sample(name, target, uv):
        texture = ue.load_object(None, target) if target.startswith("/Engine/") else ue.load_asset(target)
        if not isinstance(texture, ue.Texture2D) or not texture.get_editor_property("srgb"):
            raise RuntimeError("Eye graph needs an existing sRGB Texture2D: " + target)
        result = node(ue.MaterialExpressionTextureSampleParameter2D)
        result.set_editor_property("parameter_name", name)
        result.set_editor_property("texture", texture)
        result.set_editor_property("sampler_type", ue.MaterialSamplerType.SAMPLERTYPE_COLOR)
        result.set_editor_property("sampler_source", ue.SamplerSourceMode.SSM_CLAMP_WORLD_GROUP_SETTINGS)
        wire(uv, "", result, "Coordinates")
        return result

    def output(value, prop):
        if not edit.connect_material_property(value, "", prop):
            raise RuntimeError("Could not connect Eye material output")

    uv = node(ue.MaterialExpressionTextureCoordinate)
    uv.set_editor_property("coordinate_index", 0)
    centre = node(ue.MaterialExpressionConstant2Vector)
    centre.set_editor_property("r", .5); centre.set_editor_property("g", .5)
    centered = binary(ue.MaterialExpressionSubtract, uv, centre)
    radius = scalar_parameter("IrisRadius", .19)
    two = node(ue.MaterialExpressionConstant); two.set_editor_property("r", 2.)
    diameter = binary(ue.MaterialExpressionMultiply, radius, two)
    iris_uv = binary(ue.MaterialExpressionAdd, binary(ue.MaterialExpressionDivide, centered, diameter), centre)
    sclera_uv = binary(ue.MaterialExpressionAdd, binary(ue.MaterialExpressionMultiply, centered, scalar_parameter("ScleraScale", 1.)), centre)
    iris = sample("IrisMap", spec["evidence"]["iris"]["target"], iris_uv)
    sclera = sample("ScleraColor", spec["evidence"]["sclera"]["target"], sclera_uv)
    iris_rgb = binary(ue.MaterialExpressionMultiply, iris, vector_parameter("IrisTint", [1, 1, 1, 1]), "RGB", "RGB")
    iris_rgb = binary(ue.MaterialExpressionMultiply, iris_rgb, scalar_parameter("IrisBrightness", 1.))
    sclera_rgb = binary(ue.MaterialExpressionMultiply, sclera, vector_parameter("ScleraTint", [1, 1, 1, 1]), "RGB", "RGB")
    sclera_rgb = binary(ue.MaterialExpressionMultiply, sclera_rgb, scalar_parameter("ScleraBrightness", .8))
    distance = binary(ue.MaterialExpressionDistance, uv, centre)
    edge = node(ue.MaterialExpressionConstant); edge.set_editor_property("r", .003)
    alpha = binary(ue.MaterialExpressionDivide, binary(ue.MaterialExpressionSubtract, distance, radius), edge)
    saturated = node(ue.MaterialExpressionSaturate); wire(alpha, "", saturated, "Input")
    mask = node(ue.MaterialExpressionOneMinus); wire(saturated, "", mask, "Input")

    def blend(first, second):
        result = node(ue.MaterialExpressionLinearInterpolate)
        wire(first, "", result, "A"); wire(second, "", result, "B"); wire(mask, "", result, "Alpha")
        return result

    base = blend(sclera_rgb, iris_rgb)
    base_clamped = node(ue.MaterialExpressionSaturate); wire(base, "", base_clamped, "Input")
    output(base_clamped, ue.MaterialProperty.MP_BASE_COLOR)
    output(blend(scalar_parameter("ScleraRoughness", .1), scalar_parameter("IrisRoughness", .1)), ue.MaterialProperty.MP_ROUGHNESS)
    output(blend(scalar_parameter("ScleraSpecular", .5), scalar_parameter("IrisSpecular", .5)), ue.MaterialProperty.MP_SPECULAR)
    metallic = node(ue.MaterialExpressionConstant); metallic.set_editor_property("r", 0.)
    output(metallic, ue.MaterialProperty.MP_METALLIC)
    emissive = sample("EmissiveMap", BLACK, iris_uv)
    emission = binary(ue.MaterialExpressionMultiply, emissive, scalar_parameter("EmissionScale", 0.), "RGB")
    output(binary(ue.MaterialExpressionMultiply, emission, mask), ue.MaterialProperty.MP_EMISSIVE_COLOR)
    material.set_editor_property("blend_mode", ue.BlendMode.BLEND_OPAQUE)
    material.set_editor_property("shading_model", ue.MaterialShadingModel.MSM_DEFAULT_LIT)
    material.set_editor_property("two_sided", False)
    edit.set_material_usage(material, ue.MaterialUsage.MATUSAGE_SKELETAL_MESH)
    errors = list(edit.recompile_material(material))
    if errors:
        raise RuntimeError("Viewer Eye graph failed to compile: " + json.dumps(errors))
    return material


def configure_instance(ue, master, specification):
    asset = create_owned(ue, specification["target"], ue.MaterialInstanceConstant, ue.MaterialInstanceConstantFactoryNew)
    edit = ue.MaterialEditingLibrary
    edit.set_material_instance_parent(asset, master)
    edit.clear_all_material_instance_parameters(asset)
    evidence = specification["evidence"]
    for name, value in evidence["scalar_values"].items():
        edit.set_material_instance_scalar_parameter_value(asset, name, value)
        if not math.isclose(float(edit.get_material_instance_scalar_parameter_value(asset, name)), value, abs_tol=1.e-5):
            raise RuntimeError("Viewer Eye scalar did not persist: " + name)
    for name, value in evidence["vector_values"].items():
        edit.set_material_instance_vector_parameter_value(asset, name, ue.LinearColor(*value))
        actual = edit.get_material_instance_vector_parameter_value(asset, name)
        if any(not math.isclose(float(getattr(actual, axis)), component, abs_tol=1.e-5) for axis, component in zip("rgba", value)):
            raise RuntimeError("Viewer Eye vector did not persist: " + name)
    for name, key in (("IrisMap", "iris"), ("ScleraColor", "sclera"), ("EmissiveMap", "emissive")):
        row = evidence[key]
        texture = ue.load_asset(row["target"]) if row else ue.load_object(None, BLACK)
        if not isinstance(texture, ue.Texture2D):
            raise RuntimeError("Viewer Eye source texture did not load")
        if row and [int(texture.blueprint_get_size_x()), int(texture.blueprint_get_size_y())] != row["dimensions"]:
            raise RuntimeError("Imported Eye texture resolution differs from inspected original PNG")
        edit.set_material_instance_texture_parameter_value(asset, name, texture)
        actual = edit.get_material_instance_texture_parameter_value(asset, name)
        if actual is None or actual.get_path_name() != texture.get_path_name():
            raise RuntimeError("Viewer Eye RGB texture did not persist: " + name)
    edit.update_material_instance(asset)
    return asset


def verify_protected(document):
    for name, path in (("plan_sha256", PLAN_PATH), ("asset_plan_sha256", ASSET_PLAN_PATH),
                       ("asset_index_sha256", INDEX_PATH), ("animation_report_sha256", ANIMATION_PATH),
                       ("build_script_sha256", Path(__file__)), ("helper_sha256", ROOT / "Scripts/pc_viewer_materials.py")):
        if document[name] != digest(path):
            raise RuntimeError("Viewer Eye authoring inputs changed: " + str(path))
    if document["editor_build"]["status"] != "passed" or document["native_plugin_sha256"] != digest(PLUGIN_PATH):
        raise RuntimeError("Viewer Eye authoring requires the reviewed successful editor build")
    for row in document["protected_assets"]:
        path = asset_file(row["target"])
        if path.stat().st_size != row["bytes"] or digest(path) != row["sha256"]:
            raise RuntimeError("Protected source or animation package changed: " + row["target"])


def author(ue, document):
    verify_protected(document)
    # Check every source texture resolution before creating or saving any material.
    for specification in document["material_specs"]:
        for key in ("iris", "sclera", "emissive"):
            row = specification["evidence"][key]
            if row:
                texture = ue.load_asset(row["target"])
                if not isinstance(texture, ue.Texture2D) or [int(texture.blueprint_get_size_x()), int(texture.blueprint_get_size_y())] != row["dimensions"]:
                    raise RuntimeError("Inspected PC Eye texture is missing or has changed resolution")
    master = build_master(ue, document["material_specs"][0])
    assets = [(master, {"basis": "Shared stock radial iris/sclera graph", "limitations": document["limitations"]})]
    for specification in document["material_specs"]:
        assets.append((configure_instance(ue, master, specification), specification["evidence"]))
    report = {key: value for key, value in document.items() if key != "material_specs"}
    report.update(status="running", materials=[], recorded_at_utc=datetime.now(timezone.utc).isoformat(),
                  visual_review="Required: actual Metal close-ups and all twelve final portraits before Catalog publication.")
    for asset, evidence in assets:
        ue.EditorAssetLibrary.set_metadata_tag(asset, OWNER_TAG, OWNER)
        ue.EditorAssetLibrary.set_metadata_tag(asset, "PCViewerMaterials.PlanSHA256", document["plan_sha256"])
        ue.EditorAssetLibrary.set_metadata_tag(asset, "PCViewerMaterials.EvidenceJSON", json.dumps(evidence, ensure_ascii=False))
        if not ue.EditorAssetLibrary.save_loaded_asset(asset, only_if_is_dirty=False):
            raise RuntimeError("Could not save separately owned viewer Eye material")
        target = package(asset.get_path_name())
        disk = asset_file(target)
        report["materials"].append({"target": target, "class": asset.get_class().get_name(),
                                    "file": disk.relative_to(ROOT).as_posix(), "bytes": disk.stat().st_size,
                                    "sha256": digest(disk), "evidence": evidence})
    for target, row in report["meshes"].items():
        mesh = ue.load_asset(target)
        native = [package(slot.get_editor_property("material_interface").get_path_name()) if slot.get_editor_property("material_interface") else None for slot in mesh.get_editor_property("materials")]
        if native != row["native_materials"]:
            raise RuntimeError("Native Eye mesh material slots changed during viewer material authoring")
    verify_protected(document)
    report["status"] = "passed"
    report["counts"] = {"materials": len(report["materials"]), "eye_instances": len(document["material_specs"]),
                        "overridden_meshes": len(report["meshes"]), "eye_slots": sum(len(row["overrides"]) for row in report["meshes"].values()),
                        "characters": len(report["characters"]), "protected_assets": len(report["protected_assets"])}
    write(REPORT_PATH, report)
    from pc_viewer_materials import load_report, bound_materials
    validated = load_report()
    for target in report["meshes"]:
        bound_materials(ue, ue.load_asset(target), validated)
    ue.log("PC_VIEWER_EYE_REPORT " + json.dumps(report["counts"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-only", action="store_true")
    args, _ = parser.parse_known_args()
    if args.plan_only:
        document = build_plan()
        write(EYE_PLAN_PATH, document)
        print(json.dumps({"materials": len(document["material_specs"]), "meshes": len(document["meshes"]),
                          "characters": document["characters"], "protected_assets": len(document["protected_assets"])}, ensure_ascii=False, indent=2))
        return
    import unreal
    try:
        author(unreal, read(EYE_PLAN_PATH))
    except Exception:
        write(WORK / "material_overrides_failure.json", {"status": "failed", "error": traceback.format_exc()})
        raise


if __name__ == "__main__":
    main()
