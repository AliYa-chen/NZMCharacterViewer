"""Build isolated native UE skin/Hair layers for the configured PC UI parts.

Ordinary Python: --plan-only. UE authoring: default (NullRHI is sufficient).
UE real shader verification: -PCViewerShadingVerifyOnly with actual Metal RHI
and -AllowCommandletRendering. Source packages and the Catalog are read only.
The private stripped shader arithmetic is not claimed as recovered.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_pc_feature_sources import load_source, source_path, read_asset_plan
from pc_viewer_shading import ROOT, TARGET_ROOT, REPORT_PATH, OWNER, OWNER_TAG, asset_file, digest, package


WORK = ROOT / "Saved/PCFeatureIntegration"
PLAN_PATH = WORK / "shading_plan.json"
VIEWER_PLAN = ROOT / "Saved/PCViewerIntegration/viewer_plan.json"
INDEX_PATH = ROOT / "Docs/PCAssetConversion/asset_index.json"
MASTERS = {kind: TARGET_ROOT + "/M_PC_" + kind.title() + "_Native_v1" for kind in ("skin", "hair", "cloth_surface")}
PROFILE_SOURCES = (
    "/Game/MaterialLib/Materials/Character/NewCharacterMaterial/Skin/Material/SubsurfaceProfile_Skin",
    "/Game/Avatar/Character/Angela/CommonRes/Materials/UI/SubsurfaceProfile_Skin",
    "/Game/Avatar/Character/Angela/Angela_Suit_B/Materials/SubsurfaceProfile_Angela_Suit_B_body_UI_Skin",
)
PROFILE_TARGETS = {value: TARGET_ROOT + "/SP_PC_" + hashlib.sha256(value.encode()).hexdigest()[:12] for value in PROFILE_SOURCES}
PROPERTIES = {
    "MP_BASE_COLOR": "BaseColor", "MP_METALLIC": "Metallic", "MP_SPECULAR": "Specular",
    "MP_ROUGHNESS": "Roughness", "MP_EMISSIVE_COLOR": "EmissiveColor", "MP_NORMAL": "Normal",
    "MP_OPACITY": "Opacity", "MP_OPACITY_MASK": "OpacityMask", "MP_AMBIENT_OCCLUSION": "AmbientOcclusion",
    "MP_WORLD_POSITION_OFFSET": "WorldPositionOffset", "MP_REFRACTION": "Refraction",
}
LIMITATIONS = [
    "Executable stock UE reconstruction; the source fork's stripped private MaterialFunction arithmetic and BRDF are not recovered.",
    "Only three source subsurface profiles were exported. Other referenced profiles explicitly use the recovered shared skin profile as a fallback.",
    "Source legacy profile settings use native UE5.8's Separable-to-Burley upgrade. Unserialized fields use explicitly recorded stock defaults, not proven fork defaults.",
    "Skin packed PSAR/RAM masks, glitter/rain layers, matcap and animated wink normal equations remain unresolved.",
    "Hair uses native UE Hair with a UV-V strand axis and source Roughness, clamped Specular, BackLit and root/tip Scatter controls. Root-to-tip ramp and color power are declared stock adaptations.",
    "Hair packed AIRR channels other than the already reviewed opacity R, custom two-pass depth, tangent offsets and flow-energy equations remain unresolved.",
    "ES3.1 uses native Preintegrated Skin with a declared low-strength tint fallback; it does not reproduce desktop screen-space scattering. Mobile Hair ignores material BackLit on some paths.",
    "Shader compilation establishes executable rendering support, not an exact source-game appearance match or real-device validation.",
    "The validated radial Default Lit Eye and transparent EyeOcclusion layer remains in use. The source private Eye refraction, corneal/iris normals and caustic BRDF are not reconstructed.",
]


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".new")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def utc():
    return datetime.now(timezone.utc).isoformat()


def source_package(reference):
    if isinstance(reference, dict):
        reference = reference.get("ObjectPath") or reference.get("AssetPathName")
    if not reference or reference == "None":
        return None
    if reference.startswith("NZM/Content/"):
        reference = "/Game/" + reference[len("NZM/Content/"):]
    return package(reference)


def params(row, kind):
    return {value["ParameterInfo"]["Name"]: value["ParameterValue"]
            for value in row["effective_parameters"][kind]
            if value["ParameterInfo"].get("Association", "GlobalParameter") == "GlobalParameter"}


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def control(values, name, default, minimum=0., maximum=1.):
    value = values.get(name, default)
    if not finite(value):
        raise ValueError("Nonfinite source scalar: " + name)
    return max(minimum, min(maximum, float(value)))


def property_state(row, materials, chain=()):
    if row["target"] in chain:
        raise ValueError("Material parent cycle")
    defaults = {"BlendMode": "BLEND_Opaque", "TwoSided": False,
                "OpacityMaskClipValue": .333333, "bCastDynamicShadowAsMasked": False}
    parent = materials.get(row.get("parent_target"))
    values = property_state(parent, materials, chain + (row["target"],)) if parent else defaults
    values = dict(values)
    properties = row.get("serialized_root_properties") or {}
    if parent:
        overrides = properties.get("BasePropertyOverrides") or {}
        for name, flag in (("BlendMode", "bOverride_BlendMode"), ("TwoSided", "bOverride_TwoSided"),
                           ("OpacityMaskClipValue", "bOverride_OpacityMaskClipValue"),
                           ("bCastDynamicShadowAsMasked", "bOverride_CastDynamicShadowAsMasked")):
            if overrides.get(flag) is True:
                values[name] = overrides.get(name, defaults[name])
    else:
        values.update({name: properties[name] for name in defaults if name in properties})
    return values


def implementation_scope(jobs):
    functions = []
    folder = "PCResources/Shading/Dependencies/Metadata/MaterialLib/"
    for relative in (
            "MaterialFunctions/Tools/MaterialLayers/DataProvider/MF_Data_Skin.json",
            "MaterialFunctions/Tools/MaterialLayers/MF_Basic_Skin_PC_layer.json",
            "MaterialFunctions/Tools/MaterialLayers/DataProvider/MF_Data_MarschnerHair.json",
            "MaterialFunctions/Tools/MaterialLayers/HairPCTwoPass/MF_Basic_Hair_PC_Layer_TwoPass.json",
            "Materials/Character/NewCharacterMaterial/EyeMaterials/MF_EyeRefraction_PC.json"):
        path = folder + relative
        value = load_source(path, ROOT)
        references = value["Properties"].get("FunctionExpressions", [])
        functions.append({"file": path, "sha256": digest(source_path(path, ROOT)),
                          "serialized_expression_slots": len(references),
                          "nonnull_expression_references": sum(item is not None for item in references),
                          "function_input_or_output_references": sum(
                              "MaterialExpressionFunctionInput" in str(item) or "MaterialExpressionFunctionOutput" in str(item)
                              for item in references),
                          "private_arithmetic_recovered": False})
    missing = sorted({row["source_profile"] for row in jobs if row.get("profile_basis", "").startswith("shared_")})
    return {
        "private_shader_equivalence": False,
        "native_graph": {"skin": "UE Subsurface Profile / ES3.1 Preintegrated Skin; source profile settings, optional normal-map RNM and RoughnessIntensity multiplier",
                         "hair": "UE Hair BRDF; source effective canonical textures/color/coverage preserved; world-space UV-V strand tangent, source Roughness/Specular/BackLit/root-tip Scatter",
                         "cloth_surface": "Canonical converted Default Lit graph and effective parameters preserved in an isolated Clothing-enabled master/instance for active generic cloth slots",
                         "stock_adaptations": ["Hair BaseColor power 2.0 input-semantics conversion", "UV-V linear root/tip Scatter ramp", "Skin base roughness multiplied by source RoughnessIntensity without guessed PSAR channels", "ES3.1 low-strength skin transmission tint"]},
        "missing_profile_definitions": {"count": len(missing), "source_packages": missing,
                                        "affected_unique_materials": sum(row.get("profile_basis", "").startswith("shared_") for row in jobs),
                                        "fallback": PROFILE_SOURCES[0]},
        "private_function_evidence": functions,
        "eye": {"status": "preserved_validated_radial_stock_eye_layer", "shading_model": "MSM_DefaultLit",
                "iris_sclera_source_textures_and_radius": True, "native_eye_cornea_shader_connected": False,
                "private_refraction_or_caustic_math_recovered": False,
                "reason": "Native UE Eye needs iris-mask/distance and corneal/iris normal semantics. Source MF_EyeRefraction_PC is stripped; source optical depth and normal/tangent basis are unproven. Keep the validated Eye/EyeOcclusion assets unchanged.",
                "frozen_eye_report_sha256": digest(ROOT / "Saved/PCViewerIntegration/material_overrides_report.json")},
    }


def build_plan():
    source = read_asset_plan(ROOT)
    viewer = read(VIEWER_PLAN)
    materials = {row["target"]: row for row in source["materials"]}
    by_package = {row["source_package"]: row for row in source["materials"]}
    textures = {row["target"]: row for row in source["textures"]}
    parts = [dict(part, character_id=character["id"], outfit_id=outfit["id"])
             for character in viewer["characters"] for outfit in character["outfits"] for part in outfit["parts"]]
    used = {value for part in parts for value in part["materials"]}
    parts_by_mesh = {part["mesh_target"]: part for part in parts}
    cloth_plan_path = WORK / "cloth_plan.json"
    cloth_plan = read(cloth_plan_path)
    if cloth_plan.get("viewer_plan_sha256") != digest(VIEWER_PLAN):
        raise ValueError("Cloth material usages use another viewer plan")
    cloth_material_slots = []
    for mesh in cloth_plan["meshes"]:
        part = parts_by_mesh[mesh["source_mesh"]]
        for cloth in mesh["cloths"]:
            if not cloth["bind"]:
                continue
            slot = cloth["material_index"]
            if not isinstance(slot, int) or not 0 <= slot < len(part["materials"]):
                raise ValueError("Active cloth material slot is outside the native array")
            cloth_material_slots.append({"source_mesh": mesh["source_mesh"], "slot": slot,
                                         "source": part["materials"][slot]})
    cloth_sources = {row["source"] for row in cloth_material_slots}
    profiles = []
    for original in PROFILE_SOURCES:
        relative = "PCResources/Shading/Dependencies/Metadata/" + original[6:] + ".json"
        value = load_source(relative, ROOT)
        if value.get("Type") != "SubsurfaceProfile":
            raise ValueError("Source profile has the wrong class")
        profiles.append({"source": original, "target": PROFILE_TARGETS[original],
                         "source_file": relative, "source_sha256": digest(source_path(relative, ROOT)),
                         "source_settings": value["Properties"]["Settings"]})
    jobs = []
    for target in sorted(used):
        row = materials[target]
        kinds = [kind for kind, folder in (("skin", "/Skin/"), ("hair", "/Hair/"))
                 if any(folder in parent for parent in row["parent_chain"])]
        if not kinds:
            if target not in cloth_sources:
                continue
            kinds = ["cloth_surface"]
        if len(kinds) != 1:
            raise ValueError("Ambiguous native shader family")
        kind = kinds[0]
        values = params(row, "scalars")
        root = row
        while root.get("parent_target"):
            root = materials[root["parent_target"]]
        key = hashlib.sha256(target.encode()).hexdigest()[:12]
        job = {"source": target, "source_package": row["source_package"], "kind": kind,
               "source_file": row["metadata_file"], "source_sha256": row["sha256"],
               "source_parent_chain": row["parent_chain"], "source_root": root["target"],
               "target": TARGET_ROOT + "/MI_PC_" + kind.title() + "_" + key,
               "base_properties": property_state(row, materials), "source_scalars": values,
               "required_clothing_usage": target in cloth_sources}
        if kind == "skin":
            chain = [row] + [by_package[value] for value in row["parent_chain"] if value in by_package]
            profile = next((source_package(value["serialized_root_properties"]["SubsurfaceProfile"])
                            for value in chain if value["serialized_root_properties"].get("SubsurfaceProfile")), None)
            selected = profile if profile in PROFILE_TARGETS else PROFILE_SOURCES[0]
            job.update(source_profile=profile, profile=PROFILE_TARGETS[selected],
                       profile_basis="exported_source_profile" if profile == selected else "shared_exported_profile_fallback_missing_source_definition",
                       roughness_intensity=control(values, "RoughnessIntensity", 1.),
                       detail_weight=0., detail_tiling=1.)
            detail = next((value for value in row["texture_parameters"]
                           if value["ParameterInfo"]["Name"] == "DetailNormalMap"), None)
            if detail:
                texture = textures[detail["texture_target"]]
                if texture["effective_settings"]["compression_settings"] != "TC_Normalmap":
                    raise ValueError("DetailNormalMap lacks source normal-map sampler semantics")
                job.update(detail_normal=detail["texture_target"],
                           detail_weight=control(values, "DetailWeight", 0.),
                           detail_tiling=control(values, "DetailTilling", 1., .001, 1000.),
                           detail_basis="source_normal_texture_tiling_weight_with_stock_reoriented_normal_blend")
        elif kind == "hair":
            job.update(scatter_root=control(values, "RootScatter", .25),
                       scatter_tip=control(values, "TipsScatter", values.get("RootScatter", .25)),
                       backlit=control(values, "BackLit", .1), color_power=2.,
                       roughness=control(values, "Roughness", .5, .02, 1.),
                       specular=control(values, "Specular", .5))
        jobs.append(job)
    by_source = {row["source"]: row for row in jobs}
    meshes = {}
    uses = Counter()
    affected_parts = set()
    for part in parts:
        native = part["materials"]
        overrides = [dict(slot=slot, original=value, target=by_source[value]["target"], kind=by_source[value]["kind"])
                     for slot, value in enumerate(native) if value in by_source]
        if not overrides:
            continue
        mesh = part.get("mesh_target", part["mesh"])
        result = [by_source[value]["target"] if value in by_source else value for value in native]
        record = {"native_materials": native, "materials": result, "overrides": overrides}
        if mesh in meshes and meshes[mesh] != record:
            raise ValueError("Shared mesh has inconsistent source slots")
        meshes[mesh] = record
        uses.update(value["kind"] for value in overrides)
        affected_parts.add((part["character_id"], part["outfit_id"], part["id"]))
    return {"schema_version": 1, "owner": OWNER, "status": "planned", "recorded_at_utc": utc(),
            "plan_sha256": digest(VIEWER_PLAN), "asset_index_sha256": digest(INDEX_PATH),
            "source_manifest_sha256": digest(WORK / "source_manifest.json"),
            "cloth_plan_sha256": digest(cloth_plan_path), "active_cloth_material_slots": cloth_material_slots,
            "masters": MASTERS, "profiles": profiles, "materials": jobs, "meshes": meshes,
            "implementation_scope": implementation_scope(jobs),
            "counts": {"source_materials": len(jobs), "source_profiles": len(profiles),
                       "materials_by_kind": dict(Counter(row["kind"] for row in jobs)),
                       "slot_uses_by_kind": dict(uses), "affected_part_uses": len(affected_parts),
                       "unique_affected_meshes": len(meshes),
                       "skin_profiles_exact": sum(row.get("profile_basis") == "exported_source_profile" for row in jobs),
                       "skin_profiles_fallback": sum(row.get("profile_basis", "").startswith("shared_") for row in jobs),
                       "skin_detail_normal_materials": sum(bool(row.get("detail_normal")) for row in jobs)},
            "limitations": LIMITATIONS}


class Author:
    def __init__(self, ue, plan):
        self.ue, self.plan = ue, plan
        self.edit, self.lib = ue.MaterialEditingLibrary, ue.EditorAssetLibrary
        self.tools = ue.AssetToolsHelpers.get_asset_tools()
        self.master_usage = {}

    def own(self, asset):
        self.lib.set_metadata_tag(asset, OWNER_TAG, OWNER)

    def existing(self, target, cls):
        if not self.lib.does_asset_exist(target):
            return None
        asset = self.lib.load_asset(target)
        if not isinstance(asset, cls) or self.lib.get_metadata_tag(asset, OWNER_TAG) != OWNER:
            raise RuntimeError("Refusing an unowned native shading package: " + target)
        return asset

    def audit_source_graphs(self):
        """Require every source root to use the same canonical adapter graph.

        Parameter defaults differ and are flattened from the effective source
        interface. The expression topology, pin order, sampler types, constant
        inputs and root output channels must agree before sharing a master.
        """
        ue = self.ue
        signatures = {}
        for path in sorted({row["source_root"] for row in self.plan["materials"]}):
            material = self.lib.load_asset(path)
            if (not isinstance(material, ue.Material) or material.get_editor_property("use_material_attributes")
                    or material.get_editor_property("shading_model") != ue.MaterialShadingModel.MSM_DEFAULT_LIT):
                raise RuntimeError("Source root is not a direct canonical Default Lit graph: " + path)

            def expression(node, ancestors=()):
                if node is None:
                    return None
                identity = node.get_path_name()
                if identity in ancestors:
                    raise RuntimeError("Source material graph cycle")
                cls = node.get_class().get_name()
                data = {"class": cls}
                if isinstance(node, (ue.MaterialExpressionScalarParameter, ue.MaterialExpressionVectorParameter,
                                     ue.MaterialExpressionTextureSampleParameter2D)):
                    data["parameter"] = str(node.get_editor_property("parameter_name"))
                    if not data["parameter"].startswith("PC_"):
                        raise RuntimeError("Source root has a noncanonical parameter")
                if isinstance(node, ue.MaterialExpressionTextureSampleParameter2D):
                    data["sampler"] = str(node.get_editor_property("sampler_type"))
                if isinstance(node, ue.MaterialExpressionMultiply):
                    data["defaults"] = [float(node.get_editor_property(name)) for name in ("const_a", "const_b")]
                elif isinstance(node, ue.MaterialExpressionLinearInterpolate):
                    data["defaults"] = [float(node.get_editor_property(name)) for name in ("const_a", "const_b", "const_alpha")]
                names = [str(value) for value in self.edit.get_material_expression_input_names(node)]
                inputs = list(self.edit.get_inputs_for_material_expression(material, node))
                if len(inputs) != len(names):
                    raise RuntimeError("Ordered source expression inputs unavailable")
                data["inputs"] = [[name, expression(value, ancestors + (identity,))] for name, value in zip(names, inputs)]
                return data

            graph = {}
            for prop in PROPERTIES:
                enum = getattr(ue.MaterialProperty, prop)
                node = self.edit.get_material_property_input_node(material, enum)
                graph[prop] = {"node": expression(node), "output": str(self.edit.get_material_property_input_node_output_name(material, enum)) if node else ""}
            signatures[path] = hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest()
        if len(set(signatures.values())) != 1:
            raise RuntimeError("Source root expression wiring differs; shared native masters would lose a variant: " + repr(signatures))
        return {"all_source_roots_match": True, "roots": signatures,
                "basis": "Canonical converted expression topology, root output channels, sampler and default constant inputs; effective parameters flattened per interface"}

    def node(self, material, cls, **props):
        count = len(self.edit.get_material_expressions(material))
        node = self.edit.create_material_expression(material, getattr(self.ue, "MaterialExpression" + cls),
                                                    -1600 + count % 6 * 180, count // 6 * 140)
        if node is None:
            raise RuntimeError("Native expression unavailable: " + cls)
        node.set_editor_property("desc", "PC native reconstruction v1")
        for name, value in props.items():
            node.set_editor_property(name, value)
        return node

    def enum(self, type_name, value):
        canonical = lambda name: str(name).replace("_", "").lower()
        enum = getattr(self.ue, type_name)
        choices = [name for name in dir(enum) if canonical(name) == canonical(value)]
        if len(choices) != 1:
            raise RuntimeError("Native enum unavailable: " + type_name + "." + value)
        return getattr(enum, choices[0])

    def link(self, source, destination, name, output=""):
        canonical = lambda value: str(value).replace("_", "").replace(" ", "").lower()
        aliases = {"A>B": ("A>B", "AGreaterThanB"), "A==B": ("A==B", "AEqualsB"),
                   "A<B": ("A<B", "ALessThanB"), "Exp": ("Exp", "Exponent"),
                   "Coordinates": ("Coordinates", "UVs")}.get(name, (name,))
        pins = [str(value) for value in self.edit.get_material_expression_input_names(destination)]
        found = [pin for pin in pins if any(canonical(pin) == canonical(alias) for alias in aliases)]
        if not found and len(pins) == 1:
            found = pins
        if len(found) != 1 or not self.edit.connect_material_expressions(source, output, destination, found[0]):
            raise RuntimeError("Native graph connection failed: " + name + "; available=" + repr(pins))

    def output(self, source, prop, channel=""):
        if not self.edit.connect_material_property(source, channel, getattr(self.ue.MaterialProperty, prop)):
            raise RuntimeError("Native material output failed: " + prop)

    def scalar(self, material, name, value):
        return self.node(material, "ScalarParameter", parameter_name="PCV_" + name,
                         group="PC native reconstruction", default_value=value)

    def vector(self, material, name, value):
        return self.node(material, "VectorParameter", parameter_name="PCV_" + name,
                         group="PC native reconstruction", default_value=self.ue.LinearColor(*value, 1.))

    def binary(self, material, cls, a, b):
        node = self.node(material, cls)
        self.link(a, node, "A")
        self.link(b, node, "B")
        return node

    def unary(self, material, cls, value, pin="Input"):
        node = self.node(material, cls)
        self.link(value, node, pin)
        return node

    def switch(self, material, desktop, mobile):
        node = self.node(material, "FeatureLevelSwitch")
        self.link(desktop, node, "Default")
        self.link(mobile, node, "ES3_1")
        return node

    def save(self, asset):
        self.own(asset)
        if not self.lib.save_loaded_asset(asset, only_if_is_dirty=False):
            raise RuntimeError("Native shading save failed: " + asset.get_path_name())

    def ensure_usage(self, material, kind):
        changed = False
        properties = {"used_with_skeletal_mesh": self.ue.MaterialUsage.MATUSAGE_SKELETAL_MESH,
                      "used_with_clothing": self.ue.MaterialUsage.MATUSAGE_CLOTHING}
        before = {name: bool(self.edit.has_material_usage(material, usage)) for name, usage in properties.items()}
        for name, usage in properties.items():
            if not before[name]:
                self.edit.set_base_material_usage(material, usage, True)
                changed = True
            if not self.edit.has_material_usage(material, usage) or not bool(material.get_editor_property(name)):
                raise RuntimeError("Native material usage did not persist: " + name)
        self.master_usage[kind] = {"before": before, "after": {
            name: bool(self.edit.has_material_usage(material, usage)) for name, usage in properties.items()},
            "modified_owned_master": changed, "readback_verified": True}
        return changed

    def profile(self, row):
        ue = self.ue
        asset = self.existing(row["target"], ue.SubsurfaceProfile)
        if asset is not None:
            return asset
        folder, name = row["target"].rsplit("/", 1)
        asset = self.tools.create_asset(name, folder, ue.SubsurfaceProfile, ue.SubsurfaceProfileFactory())
        if asset is None:
            raise RuntimeError("Native SubsurfaceProfile factory unavailable")
        settings = asset.get_editor_property("settings")
        source = row["source_settings"]
        mapping = {"MeanFreePathDistance": "mean_free_path_distance", "ScatterRadius": "scatter_radius",
                   "SubsurfaceColor": "subsurface_color", "FalloffColor": "falloff_color"}
        for old, new in mapping.items():
            value = source[old]
            if isinstance(value, dict):
                value = ue.LinearColor(*(value[key] for key in ("R", "G", "B", "A")))
            settings.set_editor_property(new, value)
        # The exported legacy settings omit these fields. Explicit native
        # defaults let UE's own upgrade code preserve its legacy behavior.
        for name, value in (("enable_burley", False), ("enable_mean_free_path", False), ("world_unit_scale", .1)):
            settings.set_editor_property(name, value)
        asset.set_editor_property("settings", settings)
        self.lib.set_metadata_tag(asset, "PCViewerShading.Source", row["source"])
        self.lib.set_metadata_tag(asset, "PCViewerShading.ProfileMapping", "Source legacy settings; UE5.8 native upgrade; missing fields explicitly stock defaults")
        self.save(asset)
        return asset

    def strand_axis(self, material):
        # Native Hair Normal represents a world-space strand axis. Recover
        # dP/dV from derivatives instead of treating the card normal as hair.
        ue = self.ue
        material.set_editor_property("float_precision_mode", self.enum(
            "MaterialFloatPrecisionMode", "MFPM_Full_MaterialExpressionOnly"))
        position = self.node(material, "WorldPosition",
                             world_position_shader_offset=self.enum("WorldPositionIncludedOffsets", "WPT_CameraRelative"))
        uv = self.node(material, "TextureCoordinate", coordinate_index=0)
        derivatives = {}
        for axis in ("X", "Y"):
            for label, value in (("P", position), ("UV", uv)):
                derivatives[label + axis] = self.unary(material, "DD" + axis, value, "Value")
        components = {}
        for axis in ("X", "Y"):
            for channel in ("U", "V"):
                node = self.node(material, "ComponentMask", r=channel == "U", g=channel == "V", b=False, a=False)
                self.link(derivatives["UV" + axis], node, "Input")
                components[channel + axis] = node
        numerator = self.binary(material, "Subtract",
                                self.binary(material, "Multiply", derivatives["PY"], components["UX"]),
                                self.binary(material, "Multiply", derivatives["PX"], components["UY"]))
        determinant = self.binary(material, "Subtract",
                                  self.binary(material, "Multiply", components["UX"], components["VY"]),
                                  self.binary(material, "Multiply", components["UY"], components["VX"]))
        zero, one = self.node(material, "Constant", r=0.), self.node(material, "Constant", r=1.)

        def choose(a, b, yes, no):
            node = self.node(material, "If", equals_threshold=0.)
            # UE exposes these exact display names through the editing API.
            for value, pin in ((a, "A"), (b, "B"), (yes, "A>B"), (no, "A==B"), (no, "A<B")):
                self.link(value, node, pin)
            return node

        sign = choose(determinant, zero, one, self.node(material, "Constant", r=-1.))
        signed = self.binary(material, "Multiply", numerator, sign)
        fallback = self.node(material, "Transform",
                             transform_source_type=self.enum("MaterialVectorCoordTransformSource", "TRANSFORMSOURCE_Tangent"),
                             transform_type=self.enum("MaterialVectorCoordTransform", "TRANSFORM_World"))
        self.link(self.vector(material, "StrandFallback", (0., 1., 0.)), fallback, "Input")
        length = self.binary(material, "DotProduct", numerator, numerator)
        safe = choose(length, self.node(material, "Constant", r=1e-20), signed, fallback)
        absolute = self.unary(material, "Abs", determinant)
        safe = choose(absolute, self.node(material, "Constant", r=1e-12), safe, fallback)
        return self.unary(material, "Normalize", safe, "VectorInput")

    def master(self, kind):
        ue = self.ue
        target = MASTERS[kind]
        asset = self.existing(target, ue.Material)
        if asset is not None:
            if self.ensure_usage(asset, kind):
                self.edit.recompile_material(asset)
                self.save(asset)
            return asset
        job = next(value for value in self.plan["materials"] if value["kind"] == kind)
        original = self.lib.load_asset(job["source_root"])
        if not isinstance(original, ue.Material) or original.get_editor_property("use_material_attributes"):
            raise RuntimeError("Expected a converted direct stock material root")
        asset = self.lib.duplicate_asset(job["source_root"], target)
        roots = {pin: (self.edit.get_material_property_input_node(asset, getattr(ue.MaterialProperty, prop)),
                       str(self.edit.get_material_property_input_node_output_name(asset, getattr(ue.MaterialProperty, prop))))
                 for prop, pin in PROPERTIES.items()}
        attributes = self.node(asset, "MakeMaterialAttributes")
        for pin, (node, output) in roots.items():
            if node is not None:
                self.link(node, attributes, pin, output)
        asset.set_editor_property("use_material_attributes", True)
        asset.set_editor_property("shading_model", ue.MaterialShadingModel.MSM_FROM_MATERIAL_EXPRESSION)
        self.output(attributes, "MP_MATERIAL_ATTRIBUTES")
        if kind == "skin":
            asset.set_editor_property("subsurface_profile", self.lib.load_asset(PROFILE_TARGETS[PROFILE_SOURCES[0]]))
            desktop = self.node(asset, "ShadingModel", shading_model=ue.MaterialShadingModel.MSM_SUBSURFACE_PROFILE)
            mobile = self.node(asset, "ShadingModel", shading_model=ue.MaterialShadingModel.MSM_PREINTEGRATED_SKIN)
            self.link(self.switch(asset, desktop, mobile), attributes, "ShadingModel")
            self.link(self.switch(asset, self.node(asset, "Constant", r=1.), self.scalar(asset, "MobileSkinOpacity", .85)), attributes, "Opacity")
            self.link(self.vector(asset, "MobileSubsurfaceTint", (.08, .01712904, .00053568)), attributes, "SubsurfaceColor")
            roughness = self.binary(asset, "Multiply", roots["Roughness"][0], self.scalar(asset, "RoughnessIntensity", 1.))
            self.link(self.unary(asset, "Saturate", roughness), attributes, "Roughness")
            normal = self.node(asset, "TextureSampleParameter2D", parameter_name="PCV_DetailNormal",
                               texture=ue.load_object(None, "/Engine/EngineMaterials/DefaultNormal.DefaultNormal"),
                               sampler_type=ue.MaterialSamplerType.SAMPLERTYPE_NORMAL)
            uv = self.node(asset, "TextureCoordinate", coordinate_index=0)
            self.link(self.binary(asset, "Multiply", uv, self.scalar(asset, "DetailTiling", 1.)), normal, "Coordinates")
            flat = self.vector(asset, "FlatNormal", (0., 0., 1.))
            detail = self.node(asset, "LinearInterpolate")
            self.link(flat, detail, "A", "RGB")
            self.link(normal, detail, "B", "RGB")
            self.link(self.scalar(asset, "DetailWeight", 0.), detail, "Alpha")
            # Reoriented normal mapping: t=base+(0,0,1), u=detail*(-1,-1,1).
            # The denominator is positive for decoded tangent-space normals.
            t = self.binary(asset, "Add", roots["Normal"][0], flat)
            u = self.binary(asset, "Multiply", detail, self.vector(asset, "RNMOrientation", (-1., -1., 1.)))
            z = self.node(asset, "ComponentMask", r=False, g=False, b=True, a=False)
            self.link(t, z, "Input")
            denominator = self.binary(asset, "Max", z, self.node(asset, "Constant", r=1e-4))
            scaled = self.binary(asset, "Multiply", t, self.binary(asset, "Divide", self.binary(asset, "DotProduct", t, u), denominator))
            combined = self.binary(asset, "Subtract", scaled, u)
            self.link(self.unary(asset, "Normalize", combined, "VectorInput"), attributes, "Normal")
        elif kind == "hair":
            asset.set_editor_property("tangent_space_normal", False)
            self.link(self.node(asset, "ShadingModel", shading_model=ue.MaterialShadingModel.MSM_HAIR), attributes, "ShadingModel")
            self.link(self.strand_axis(asset), attributes, "Normal")
            power = self.node(asset, "Power")
            self.link(roots["BaseColor"][0], power, "Base", roots["BaseColor"][1])
            self.link(self.scalar(asset, "HairColorPower", 2.), power, "Exp")
            self.link(power, attributes, "BaseColor")
            uv = self.node(asset, "TextureCoordinate", coordinate_index=0)
            v = self.node(asset, "ComponentMask", r=False, g=True, b=False, a=False)
            self.link(uv, v, "Input")
            scatter = self.node(asset, "LinearInterpolate")
            self.link(self.scalar(asset, "HairScatterRoot", .25), scatter, "A")
            self.link(self.scalar(asset, "HairScatterTip", .25), scatter, "B")
            self.link(self.unary(asset, "Saturate", v), scatter, "Alpha")
            self.link(scatter, attributes, "Metallic")
            self.link(self.scalar(asset, "HairBacklit", .1), attributes, "ClearCoat")
        else:
            self.link(self.node(asset, "ShadingModel", shading_model=ue.MaterialShadingModel.MSM_DEFAULT_LIT), attributes, "ShadingModel")
        self.ensure_usage(asset, kind)
        self.edit.recompile_material(asset)
        self.lib.set_metadata_tag(asset, "PCViewerShading.SourceRoot", job["source_root"])
        self.save(asset)
        return asset

    def instance(self, row):
        ue = self.ue
        source = self.lib.load_asset(row["source"])
        if not isinstance(source, ue.MaterialInterface):
            raise RuntimeError("Source converted material failed to load")
        asset = self.existing(row["target"], ue.MaterialInstanceConstant)
        if asset is None:
            folder, name = row["target"].rsplit("/", 1)
            asset = self.tools.create_asset(name, folder, ue.MaterialInstanceConstant, ue.MaterialInstanceConstantFactoryNew())
        parent = self.lib.load_asset(MASTERS[row["kind"]])
        self.edit.set_material_instance_parent(asset, parent)
        scalar_names = self.edit.get_scalar_parameter_names(source)
        vector_names = self.edit.get_vector_parameter_names(source)
        texture_names = self.edit.get_texture_parameter_names(source)
        instance = isinstance(source, ue.MaterialInstanceConstant)
        source_prefix = "get_material_instance_" if instance else "get_material_default_"
        for names, kind in ((scalar_names, "scalar"), (vector_names, "vector"), (texture_names, "texture")):
            getter = getattr(self.edit, source_prefix + kind + "_parameter_value")
            setter = getattr(self.edit, "set_material_instance_" + kind + "_parameter_value")
            target_getter = getattr(self.edit, "get_material_instance_" + kind + "_parameter_value")
            parent_names = {str(value) for value in getattr(self.edit, "get_" + kind + "_parameter_names")(parent)}
            if not {str(value) for value in names}.issubset(parent_names):
                raise RuntimeError("Native master omits source parameters")
            for name in names:
                if not str(name).startswith("PC_"):
                    raise RuntimeError("Unexpected canonical source parameter: " + str(name))
                value = getter(source, name)
                if kind == "texture" and value is None:
                    raise RuntimeError("Source has an unresolved canonical texture")
                # UE5.8 performs these setters but can return false. Verify the
                # reflected values, including inherited effective parameters.
                setter(asset, name, value)
                found = target_getter(asset, name)
                if kind == "texture":
                    valid = found is not None and found.get_path_name() == value.get_path_name()
                elif kind == "vector":
                    valid = all(math.isclose(float(getattr(found, axis)), float(getattr(value, axis)), abs_tol=1e-5)
                                for axis in ("r", "g", "b", "a"))
                else:
                    valid = math.isclose(float(found), float(value), abs_tol=1e-5)
                if not valid:
                    raise RuntimeError("Canonical parameter copy did not persist: " + str(name))
        overrides = ue.MaterialInstanceBasePropertyOverrides()
        for old, new in (("BlendMode", "blend_mode"), ("TwoSided", "two_sided"),
                         ("OpacityMaskClipValue", "opacity_mask_clip_value"),
                         ("bCastDynamicShadowAsMasked", "cast_dynamic_shadow_as_masked")):
            value = row["base_properties"][old]
            if old == "BlendMode":
                value = getattr(ue.BlendMode, value.upper())
            overrides.set_editor_property("override_" + new, True)
            overrides.set_editor_property(new, value)
        asset.set_editor_property("base_property_overrides", overrides)
        controls = {}
        if row["kind"] == "skin":
            asset.set_editor_property("override_subsurface_profile", True)
            asset.set_editor_property("subsurface_profile", self.lib.load_asset(row["profile"]))
            controls = {"RoughnessIntensity": row["roughness_intensity"], "DetailWeight": row["detail_weight"], "DetailTiling": row["detail_tiling"]}
            if row.get("detail_normal"):
                texture = self.lib.load_asset(row["detail_normal"])
                if not isinstance(texture, ue.Texture2D) or texture.get_editor_property("compression_settings") != ue.TextureCompressionSettings.TC_NORMALMAP:
                    raise RuntimeError("Native detail normal has incompatible sampler semantics")
                self.edit.set_material_instance_texture_parameter_value(asset, "PCV_DetailNormal", texture)
        elif row["kind"] == "hair":
            controls = {"HairScatterRoot": row["scatter_root"], "HairScatterTip": row["scatter_tip"],
                        "HairBacklit": row["backlit"], "HairColorPower": row["color_power"]}
            self.edit.set_material_instance_scalar_parameter_value(asset, "PC_Roughness", row["roughness"])
            self.edit.set_material_instance_scalar_parameter_value(asset, "PC_Specular", row["specular"])
        for name, value in controls.items():
            self.edit.set_material_instance_scalar_parameter_value(asset, "PCV_" + name, value)
            if not math.isclose(float(self.edit.get_material_instance_scalar_parameter_value(asset, "PCV_" + name)), value, abs_tol=1e-5):
                raise RuntimeError("Native scalar did not persist: " + name)
        self.lib.set_metadata_tag(asset, "PCViewerShading.Source", row["source"])
        self.lib.set_metadata_tag(asset, "PCViewerShading.Scope", "Native stock reconstruction; stripped private shader math unresolved")
        self.edit.update_material_instance(asset)
        row["clothing_usage_readback"] = bool(self.edit.has_material_usage(asset, ue.MaterialUsage.MATUSAGE_CLOTHING))
        if not row["clothing_usage_readback"]:
            raise RuntimeError("Native instance does not inherit Clothing usage")
        self.save(asset)
        return asset


def asset_record(target, cls, **values):
    disk = asset_file(target)
    return dict(target=target, **{"class": cls}, file=disk.relative_to(ROOT).as_posix(),
                bytes=disk.stat().st_size, sha256=digest(disk), **values)


def protected_assets():
    records = {row["target"]: {key: row[key] for key in ("target", "file", "bytes", "sha256")}
               for row in read(INDEX_PATH)["assets"]}
    eye_path = ROOT / "Saved/PCViewerIntegration/material_overrides_report.json"
    eye = read(eye_path)
    for row in eye.get("materials", []) + eye.get("protected_assets", []):
        records[row["target"]] = {key: row[key] for key in ("target", "bytes", "sha256")}
        records[row["target"]]["file"] = asset_file(row["target"]).relative_to(ROOT).as_posix()
    return [records[target] for target in sorted(records)]


def verify_protected(records):
    for row in records:
        disk = ROOT / row["file"]
        if disk.stat().st_size != row["bytes"] or digest(disk) != row["sha256"]:
            raise RuntimeError("Protected source package changed: " + row["target"])


def verify_shaders(ue):
    from pc_viewer_shading import load_report
    report = load_report(ROOT)
    paths = [value["target"] for value in report["masters"] + report["materials"]]
    value = json.loads(ue.PCAssetImportLibrary.verify_material_shaders(paths))
    value.update(owner=OWNER, report_sha256=digest(REPORT_PATH), recorded_at_utc=utc())
    write(WORK / "shading_shader_verification.json", value)
    if value.get("status") != "passed" or value.get("success") is not True:
        raise RuntimeError("Native PC viewer shader verification failed")
    ue.log("PC_VIEWER_NATIVE_SHADERS " + json.dumps({name: value.get(name) for name in ("status", "success", "rhi", "passed_count", "failed_count")}))


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-only", action="store_true")
    args, _ = parser.parse_known_args()
    if not args.plan_only:
        import unreal as ue
        if "-PCViewerShadingVerifyOnly" in ue.SystemLibrary.get_command_line():
            verify_shaders(ue)
            return
    plan = build_plan()
    write(PLAN_PATH, plan)
    if args.plan_only:
        print(json.dumps(plan["counts"], ensure_ascii=False, indent=2))
        return
    protected = protected_assets()
    verify_protected(protected)
    author = Author(ue, plan)
    source_graph_validation = author.audit_source_graphs()
    for row in plan["profiles"]:
        author.profile(row)
    for kind in MASTERS:
        author.master(kind)
    report = {name: plan[name] for name in ("schema_version", "owner", "plan_sha256", "asset_index_sha256", "source_manifest_sha256", "cloth_plan_sha256", "active_cloth_material_slots", "counts", "meshes", "limitations", "implementation_scope")}
    report.update(status="running", recorded_at_utc=utc(), shader_compilation_verified=False,
                  rendered_appearance_verified=False, protected_assets=protected,
                  source_graph_validation=source_graph_validation,
                  master_usage_validation=author.master_usage,
                  masters=[], materials=[], profiles=[])
    write(REPORT_PATH, report)
    for index, row in enumerate(plan["materials"]):
        ue.log(f"PC_VIEWER_NATIVE_SHADING {index + 1}/{len(plan['materials'])} {row['kind']} {row['source']}")
        author.instance(row)
        report["materials"].append(asset_record(row["target"], "MaterialInstanceConstant", **{name: value for name, value in row.items() if name != "target"}))
        write(REPORT_PATH, report)
    report["masters"] = [asset_record(target, "Material", kind=kind) for kind, target in MASTERS.items()]
    report["profiles"] = [asset_record(row["target"], "SubsurfaceProfile", **{name: value for name, value in row.items() if name != "target"}) for row in plan["profiles"]]
    verify_protected(protected)
    report["protected_assets_unchanged"] = True
    report["status"] = "passed"
    write(REPORT_PATH, report)
    ue.log("PC_VIEWER_NATIVE_SHADING_COMPLETE " + json.dumps(report["counts"]))


if __name__ == "__main__":
    run()
