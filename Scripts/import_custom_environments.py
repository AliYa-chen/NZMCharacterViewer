"""Import recovered per-outfit stages from Environment/Custom/custom_manifest.json.

The extraction manifest owns source-package -> converted file/property mappings.
All source map exports are retained so component parent transforms, visibility,
material overrides, mobile LOD material indices and light properties are used.
Cooked packages are evidence only; this module imports GLB/PNG/HDR, never uasset.
It does not replace missing geometry/materials with WorldGrid or another stage.

The original project's material graphs are stripped. The report distinguishes
recovered textures/parameters from the stock UE5 shader reconstruction. Packed
mask channels and stripped scalar equations are not silently guessed.
"""
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import traceback

import unreal
from import_environment import EnvironmentImporter, DESTINATION, _name, _rotation, _vector, _linear_color, _json_transform


def package(value):
    if isinstance(value, dict):
        value = value.get("ObjectPath") or value.get("AssetPathName")
    if not value or value == "None":
        return None
    value = str(value).strip("'")
    value = re.sub(r"\.[^/]*$", "", value)
    if value.startswith("/Game/"):
        value = "NZM/Content/" + value[6:]
    elif value.startswith("/Engine/"):
        value = "Engine/Content/" + value[8:]
    return value.lstrip("/")


def _index(reference):
    value = reference.get("ObjectPath", "") if isinstance(reference, dict) else str(reference or "")
    match = re.search(r"\.(\d+)$", value)
    return int(match[1]) if match else None


def _parameters(rows):
    return {row.get("ParameterInfo", {}).get("Name", row.get("ParameterName")): row.get("ParameterValue")
            for row in rows or [] if row.get("ParameterInfo", {}).get("Name", row.get("ParameterName"))}


def _merge_properties(base, override):
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict) and not any(k in value for k in ("ObjectPath", "AssetPathName")):
            result[key] = _merge_properties(result[key], value)
        else:
            result[key] = value
    return result


class CustomEnvironmentImporter(EnvironmentImporter):
    def __init__(self, project, import_task, make_material, custom_root=None):
        super().__init__(project, import_task, make_material)
        self.standard_profile = self.profile
        self.custom_root = Path(custom_root or self.source / "Custom").resolve()
        if not self.custom_root.is_relative_to(self.source):
            raise ValueError("Custom environment sources must be inside SourceAssets/Environment")
        self.custom = json.loads(self.custom_file("custom_manifest.json").read_text())
        self.custom_assets = {package(k): v for k, v in self.custom.get("assets", {}).items()}
        self.source_keys = {}
        self.material_cache = {}
        self.mesh_cache = {}
        self.properties_cache = {}
        self.template_properties_cache = {}
        self.scene_id = None
        self.report = {"status": "started", "recorded_utc": datetime.now(timezone.utc).isoformat(),
            "assets": [], "materials": [], "meshes": [], "lights": [], "warnings": [],
            "approximations": [], "omitted": [], "scenes": {}, "runtime_and_visual_validation": "not_run"}
        for source_package, row in self.custom_assets.items():
            if isinstance(row, str):
                row = {"file": row}
                self.custom_assets[source_package] = row
            if row.get("file"):
                path = self.custom_file(row["file"])
                # Short hash suffixes retain source identity without excessive UE paths.
                key = Path(source_package).name[:80] + "_" + hashlib.sha1(source_package.encode()).hexdigest()[:10]
                self.source_keys[source_package] = key
                self.assets[key] = path.relative_to(self.source).as_posix()

    def custom_file(self, relative):
        result = (self.custom_root / relative).resolve()
        if not result.is_relative_to(self.custom_root) or not result.is_file():
            raise ValueError(f"Missing/outside custom environment source: {relative}")
        return result

    def verify_custom_inputs(self):
        files = self.custom.get("files", [])
        if isinstance(files, dict):
            files = [{"file": k, "sha256": v} for k, v in files.items()]
        verified = 0
        for row in files:
            path = self.custom_file(row["file"])
            if row.get("sha256") and hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
                raise ValueError(f"Custom scene hash mismatch: {row['file']}")
            verified += bool(row.get("sha256"))
        self.report["verified_source_hashes"] = verified

    def source_key(self, source_package, suffix=None):
        source_package = package(source_package)
        key = self.source_keys.get(source_package)
        if key is None:
            raise ValueError(f"Required original converted asset missing: {source_package}")
        if suffix and self.asset_file(self.assets[key]).suffix.lower() not in suffix:
            raise ValueError(f"Wrong converted type for {source_package}: {self.assets[key]}")
        return key

    def properties(self, source_package):
        source_package = package(source_package)
        if source_package in self.properties_cache:
            return self.properties_cache[source_package]
        row = self.custom_assets.get(source_package, {})
        path = row.get("properties")
        if path:
            result = json.loads(self.custom_file(path).read_text())
        else:
            entry = self.custom.get("map_profiles", {}).get(source_package)
            if isinstance(entry, dict):
                entry = entry.get("records", entry.get("exports", entry.get("file")))
            result = json.loads(self.custom_file(entry).read_text()) if isinstance(entry, str) else entry
        if not isinstance(result, list):
            raise ValueError(f"Original property export list missing: {source_package}")
        self.properties_cache[source_package] = result
        return result

    def effective_source_properties(self, source_package, index, visited=None):
        source_package = package(source_package)
        identity = (source_package, index)
        if identity in self.template_properties_cache:
            return self.template_properties_cache[identity]
        visited = set(visited or ())
        if identity in visited:
            raise ValueError(f"Cyclic source component template chain: {identity}")
        visited.add(identity)
        rows = self.properties(source_package)
        if not 0 <= index < len(rows):
            raise ValueError(f"Source template export index outside package: {identity}")
        row = rows[index]
        explicit = row.get("Properties", row.get("properties", {}))
        template = row.get("Template", row.get("template"))
        inherited, chain = {}, []
        if template:
            parent_package, parent_index = package(template), _index(template)
            if parent_index is not None and parent_package in self.custom_assets:
                inherited, chain = self.effective_source_properties(parent_package, parent_index, visited)
                # Blueprint template topology uses indices in another package.
                # Instance SCS exports own their actual map attachment references.
                inherited = {k: v for k, v in inherited.items() if k not in
                    ("AttachParent", "AttachChildren", "RootComponent", "InstanceComponents", "BlueprintCreatedComponents")}
                chain = chain + [{"package": parent_package, "export_index": parent_index,
                    "name": self.properties(parent_package)[parent_index].get("Name")}]
        result = _merge_properties(inherited, explicit), chain
        self.template_properties_cache[identity] = result
        return result

    def records(self, source_package):
        source_package = package(source_package)
        records = []
        for index, row in enumerate(self.properties(source_package)):
            effective, template_chain = self.effective_source_properties(source_package, index)
            if "properties" in row and "source" in row:
                records.append({**row, "properties": effective, "explicit_properties": row["properties"], "template_chain": template_chain})
            else:
                records.append({"name": row.get("Name", f"Export_{index}"), "type": row.get("Type"),
                    "properties": effective, "explicit_properties": row.get("Properties", {}),
                    "template": row.get("Template"), "template_chain": template_chain, "outer": row.get("Outer"),
                    "flags": row.get("Flags", ""), "source": {"package": source_package,
                        "export_index": index, "name": row.get("Name")}})
        return records

    def map_package(self, value):
        value = package(value)
        return value if value.startswith(("NZM/", "Engine/")) else "NZM/Content/Maps/" + value

    def component_transform(self, record, scene_records):
        indexed = {r["source"]["export_index"]: r for r in scene_records}
        chain, visited = [], set()
        def compose(current):
            index = current["source"]["export_index"]
            if index in visited:
                raise ValueError("Cyclic original scene attachment hierarchy")
            visited.add(index)
            chain.append(current["source"])
            props = current.get("properties", {})
            location = unreal.Vector(*_vector(props.get("RelativeLocation")))
            rotation = _rotation(props.get("RelativeRotation"))
            scale = unreal.Vector(*_vector(props.get("RelativeScale3D"), (1, 1, 1)))
            local = unreal.Transform(location=location, rotation=rotation, scale=scale)
            parent_index = _index(props.get("AttachParent"))
            if parent_index is None:
                return local
            if package(props.get("AttachParent")) != current["source"]["package"]:
                raise ValueError(f"Cross-package attachment was not instantiated into this source map: {props.get('AttachParent')}")
            if parent_index not in indexed:
                raise ValueError(f"Original parent component missing: {props.get('AttachParent')}")
            result = local.multiply(compose(indexed[parent_index]))
            # UE scene components can opt out of one part of parent inheritance.
            return unreal.Transform(location=location if props.get("bAbsoluteLocation") else result.translation,
                rotation=rotation if props.get("bAbsoluteRotation") else result.rotation.rotator(),
                scale=scale if props.get("bAbsoluteScale") else result.scale3d)
        return compose(record), chain

    def hidden_reason(self, record, records):
        indexed = {r["source"]["export_index"]: r for r in records}
        pending, seen = [record], set()
        while pending:
            current = pending.pop()
            index = current["source"]["export_index"]
            if index in seen:
                continue
            seen.add(index)
            props = current.get("properties", {})
            if props.get("bVisible") is False or props.get("bHiddenInGame") or props.get("bHidden") or props.get("bIsEditorOnlyActor"):
                return {"source": current["source"], "properties": {k: props[k] for k in
                        ("bVisible", "bHiddenInGame", "bHidden", "bIsEditorOnlyActor") if k in props}}
            for parent in (props.get("AttachParent"), current.get("outer")):
                parent_index = _index(parent)
                if parent_index in indexed:
                    pending.append(indexed[parent_index])
        return None

    def material_parameters(self, source_package, visited=None):
        source_package = package(source_package)
        visited = set(visited or ())
        if source_package in visited:
            raise ValueError(f"Cyclic source material inheritance: {source_package}")
        visited.add(source_package)
        prepared = self.custom.get("material_specs", {}).get(source_package)
        if prepared:
            parameters = prepared["parameters"]
            chain, current = [], source_package
            while current:
                if current in chain:
                    raise ValueError(f"Cyclic prepared material inheritance: {source_package}")
                chain.insert(0, current)
                current = self.custom.get("material_specs", {}).get(current, {}).get("parent")
            return {"textures": dict(parameters.get("textures", {})), "scalars": dict(parameters.get("scalars", {})),
                "vectors": dict(parameters.get("vectors", {})), "properties": dict(prepared.get("effective_properties", {})),
                "chain": chain, "source_manifest_spec": prepared}
        rows = self.properties(source_package)
        source = next((r for r in rows if r.get("Type") in
            ("Material", "MaterialInstanceConstant", "VirtualMaterialInstanceConstant")), None)
        if source is None:
            raise ValueError(f"Original material properties missing: {source_package}")
        props = source.get("Properties", {})
        parent = package(props.get("Parent"))
        result = {"textures": {}, "scalars": {}, "vectors": {}, "properties": {}, "chain": []}
        if parent and parent in self.custom_assets and self.custom_assets[parent].get("properties"):
            result = self.material_parameters(parent, visited)
        for row in rows:
            default = row.get("Properties", {})
            name = default.get("ParameterName")
            if not name:
                continue
            if row.get("Type") in ("MaterialExpressionTextureSampleParameter2D", "MaterialExpressionTextureObjectParameter"):
                result["textures"][name] = default.get("Texture")
            elif row.get("Type") == "MaterialExpressionScalarParameter" and "DefaultValue" in default:
                result["scalars"][name] = default["DefaultValue"]
            elif row.get("Type") == "MaterialExpressionVectorParameter" and "DefaultValue" in default:
                result["vectors"][name] = default["DefaultValue"]
        result["textures"].update(_parameters(props.get("TextureParameterValues")))
        result["scalars"].update(_parameters(props.get("ScalarParameterValues")))
        result["vectors"].update(_parameters(props.get("VectorParameterValues")))
        result["properties"].update({k: props[k] for k in ("BlendMode", "TwoSided", "ShadingModel", "ShadingModels") if k in props})
        result["properties"].update(props.get("BasePropertyOverrides", {}))
        result["chain"].append(source_package)
        return result

    def build_material(self, reference):
        source_package = package(reference)
        if not source_package or "WorldGridMaterial" in source_package:
            raise ValueError(f"Unresolved original material is not accepted: {source_package}")
        if source_package in self.material_cache:
            return self.material_cache[source_package]
        if source_package == "Engine/Content/BasicShapes/BasicShapeMaterial":
            material = self.library.load_asset("/Engine/BasicShapes/BasicShapeMaterial")
            if material is None:
                raise ValueError("Original Engine BasicShapeMaterial unavailable")
            self.material_cache[source_package] = material
            return material
        source = self.material_parameters(source_package)
        textures, scalars, vectors, properties = (source[k] for k in ("textures", "scalars", "vectors", "properties"))
        name = "M_Custom_" + Path(source_package).name[:70] + "_" + hashlib.sha1(source_package.encode()).hexdigest()[:8]
        material = self.material(name)
        edit = unreal.MaterialEditingLibrary
        made = []
        def expr(cls):
            result = edit.create_material_expression(material, cls, -650 + len(made) % 4 * 200, len(made) // 4 * 180)
            made.append(result)
            return result
        def scalar(value):
            result = expr(unreal.MaterialExpressionConstant)
            result.set_editor_property("r", float(value))
            return result, ""
        def vector(value):
            if isinstance(value, dict):
                value = [float(value.get(k, 1.0)) for k in "RGB"]
            result = expr(unreal.MaterialExpressionConstant3Vector)
            result.set_editor_property("constant", unreal.LinearColor(*value[:3], 1.0))
            return result, ""
        def texture(parameter, role):
            key = self.source_key(textures[parameter], (".png", ".tga", ".exr"))
            result = expr(unreal.MaterialExpressionTextureSample)
            result.set_editor_property("texture", self.import_texture(key, role))
            result.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL if role == "normal"
                else unreal.MaterialSamplerType.SAMPLERTYPE_COLOR if role in ("color", "emissive") else unreal.MaterialSamplerType.SAMPLERTYPE_MASKS)
            return result, "RGB"
        def combine(cls, a, b, pins=("A", "B")):
            result = expr(cls)
            for value, pin in zip((a, b), pins):
                if not edit.connect_material_expressions(value[0], value[1], result, pin):
                    raise RuntimeError(f"Failed material expression connection for {source_package}")
            return result, ""
        def connect(value, prop):
            if not edit.connect_material_property(value[0], value[1], prop):
                raise RuntimeError(f"Failed material property connection for {source_package}")
        sky = "EXRTex" in textures or "BaseTex" in textures and properties.get("ShadingModel") == "MSM_Unlit"
        background = "MainTex" in textures and any("M_CharacterLobby_BG" in x for x in source["chain"])
        unlit = sky or background or properties.get("ShadingModel") == "MSM_Unlit"
        material.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT if unlit else unreal.MaterialShadingModel.MSM_DEFAULT_LIT)
        material.set_editor_property("two_sided", bool(properties.get("TwoSided", False)) or sky)
        blend = str(properties.get("BlendMode", "BLEND_Opaque")).lower()
        mode = unreal.BlendMode.BLEND_TRANSLUCENT if "translucent" in blend else unreal.BlendMode.BLEND_MASKED if "masked" in blend else unreal.BlendMode.BLEND_OPAQUE
        material.set_editor_property("blend_mode", mode)
        bindings, approximations = {}, []
        color_parameter = next((k for k in ("EXRTex", "MainTex", "BaseTex", "BaseColorMap", "01_R_Base") if k in textures), None)
        if color_parameter:
            color = texture(color_parameter, "color")
            color_sample = color[0]
            bindings["base_color"] = color_parameter
        elif "BaseColorScalar" in vectors:
            color = vector(vectors["BaseColorScalar"])
            color_sample = None
            bindings["base_color"] = "BaseColorScalar"
        elif "Water_BaseColor" in vectors:
            color = vector(vectors["Water_BaseColor"])
            color_sample = None
            bindings["base_color"] = "Water_BaseColor"
            approximations.append("Recovered Water_BaseColor constant on static water mesh; source depth/refraction/flow shader equations are stripped.")
        else:
            raise ValueError(f"No recovered source base color texture/constant for {source_package}; no grey substitution")
        if "01_R_Base" in textures and "02_G_Base" in textures:
            second = texture("02_G_Base", "color")
            vertex = expr(unreal.MaterialExpressionVertexColor)
            blend_node = expr(unreal.MaterialExpressionLinearInterpolate)
            for value, pin in ((color, "A"), (second, "B"), ((vertex, "G"), "Alpha")):
                edit.connect_material_expressions(value[0], value[1], blend_node, pin)
            color = blend_node, ""
            bindings["second_layer"] = "02_G_Base via original vertex G"
            approximations.append("Stock vertex-G layer lerp; stripped wetness/height blend and per-layer UV equations are not recovered.")
        if "BaseColorScalar" in vectors and color_parameter and color_parameter != "EXRTex":
            color = combine(unreal.MaterialExpressionMultiply, color, vector(vectors["BaseColorScalar"]))
            bindings["color_multiplier"] = "BaseColorScalar"
        elif color_parameter == "BaseTex" and "Color_BaseTex" in vectors:
            color = combine(unreal.MaterialExpressionMultiply, color, vector(vectors["Color_BaseTex"]))
            bindings["color_multiplier"] = "Color_BaseTex"
        connect(color, unreal.MaterialProperty.MP_EMISSIVE_COLOR if unlit else unreal.MaterialProperty.MP_BASE_COLOR)
        if not unlit:
            normal = next((k for k in ("NormalMap", "01_R_Normal", "WaterNormal") if k in textures), None)
            if normal:
                connect(texture(normal, "normal"), unreal.MaterialProperty.MP_NORMAL)
                bindings["normal"] = normal
            for parameter, default, prop in (("Roughness", 0.58, unreal.MaterialProperty.MP_ROUGHNESS),
                    ("Metallic", 0.0, unreal.MaterialProperty.MP_METALLIC), ("Specular", 0.5, unreal.MaterialProperty.MP_SPECULAR)):
                value = max(0.0, min(1.0, float(scalars.get(parameter, default))))
                connect(scalar(value), prop)
                bindings[parameter] = value
                if parameter not in scalars:
                    approximations.append(f"Stock {parameter}={default}: no recovered scalar or verified packed channel mapping.")
            if "EmissiveMap" in textures:
                emission = texture("EmissiveMap", "emissive")
                emission_color = vectors.get("EmissionColor", vectors.get("EmissiveColor"))
                if emission_color:
                    emission = combine(unreal.MaterialExpressionMultiply, emission, vector(emission_color))
                connect(emission, unreal.MaterialProperty.MP_EMISSIVE_COLOR)
                bindings["emissive"] = "EmissiveMap"
        if mode != unreal.BlendMode.BLEND_OPAQUE:
            explicit_opacity = next((k for k in ("OpacityMap", "OpacityMaskMap", "OpacityTexture", "AlphaMask _A") if k in textures), None)
            if explicit_opacity:
                opacity_sample = texture(explicit_opacity, "mask")[0]
                opacity = opacity_sample, "R"
                bindings["opacity"] = explicit_opacity + ".R"
                approximations.append("Source AlphaChannel_A=(1,0,0,0) selects original alpha-mask red channel; remaining alpha shader equations are stripped." if explicit_opacity == "AlphaMask _A" else
                    "Stock explicit opacity texture red channel; original opacity equation not recovered.")
            elif "Opacity" in scalars:
                opacity = scalar(scalars["Opacity"])
                bindings["opacity"] = {"scalar": scalars["Opacity"]}
            elif color_sample is not None:
                opacity = color_sample, "A"
                bindings["opacity"] = str(color_parameter) + ".A"
                approximations.append("Stock base-color alpha used for source non-opaque material; original alpha equation not recovered.")
            else:
                opacity = scalar(1.0)
                approximations.append("Opacity1 compatibility default; source scalar/texture unavailable.")
            connect(opacity, unreal.MaterialProperty.MP_OPACITY if mode == unreal.BlendMode.BLEND_TRANSLUCENT else unreal.MaterialProperty.MP_OPACITY_MASK)
            if mode == unreal.BlendMode.BLEND_TRANSLUCENT and not unlit:
                material.set_editor_property("translucency_lighting_mode", unreal.TranslucencyLightingMode.TLM_SURFACE_PER_PIXEL_LIGHTING)
            if mode == unreal.BlendMode.BLEND_MASKED and "OpacityMaskClipValue" in properties:
                material.set_editor_property("opacity_mask_clip_value", float(properties["OpacityMaskClipValue"]))
        if any(k in textures for k in ("RAMMap", "PSARMap", "ORMMap")):
            approximations.append("Packed texture retained in evidence; channels not assigned without recovered graph mapping.")
        if sky:
            approximations.append("Original EXRTex/UV0 unlit; stripped Rotate/HeightScale/Flow/HDRScale expressions retained as evidence.")
        if background:
            approximations.append("Original MainTex/UV0 unlit; stripped Add/Multiple/Power expressions retained as evidence.")
        edit.recompile_material(material)
        self.library.save_loaded_asset(material)
        self.material_cache[source_package] = material
        self.report["materials"].append({"scene": self.scene_id, "asset": material.get_path_name(),
            "source_material": source_package, "recovered_parameters": source, "bindings": bindings,
            "implementation": "stock_UE5_reconstruction", "approximations": approximations})
        return material

    def imported_slot_order(self, mesh, metadata):
        source_names = metadata["materials"]
        result = []
        normalize = lambda text: re.sub(r"[^a-z0-9]", "", str(text).lower())
        for slot in mesh.get_editor_property("static_materials"):
            names = [normalize(slot.get_editor_property(k)) for k in ("material_slot_name", "imported_material_slot_name")]
            matches = [i for i, source_name in enumerate(source_names) if normalize(source_name) in names]
            if len(matches) != 1:
                if len(source_names) == 1:
                    matches = [0]
                else:
                    raise ValueError(f"Imported GLB material slot order cannot be verified: {names} vs {source_names}")
            result.append(matches[0])
        if sorted(result) != list(range(len(source_names))):
            raise ValueError("Imported GLB material slots are not a one-to-one source mapping")
        return result

    def build_meshes(self, scene):
        components = []
        prepared = {(package(c["source_map"]), c["source_export"]): c for c in scene.get("components", [])}
        for map_key in ("mesh_map", "light_map", "effect_map"):
            if not scene.get(map_key):
                continue
            records = self.records(self.map_package(scene[map_key]))
            for row in records:
                if row["type"] != "StaticMeshComponent" or not row["properties"].get("StaticMesh"):
                    continue
                hidden = self.hidden_reason(row, records)
                if hidden:
                    self.report["omitted"].append({"scene": self.scene_id, "source": row["source"], "reason": "source visibility", "hidden_by": hidden})
                    continue
                props = row["properties"]
                source_package = package(props["StaticMesh"])
                prepared_component = prepared.get((row["source"]["package"], row["source"]["export_index"]))
                if source_package not in self.mesh_cache:
                    if source_package == "Engine/Content/BasicShapes/Plane":
                        mesh = self.library.load_asset("/Engine/BasicShapes/Plane")
                        metadata = {"materials": ["BasicShapeMaterial"]}
                        indices = [0]
                    else:
                        mesh, metadata = self.import_static_mesh(self.source_key(source_package, (".glb",)))
                        indices = self.custom_assets[source_package].get("material_indices")
                        if prepared_component and prepared_component.get("materials"):
                            if any(not r.get("available") for r in prepared_component["materials"]):
                                raise ValueError(f"Original material closure incomplete for {source_package}; no WorldGrid substitution")
                            source_index_rows = [r.get("source_material_indices", []) for r in prepared_component["materials"]]
                            if all(len(r) >= 1 for r in source_index_rows):
                                indices = [r[0] for r in source_index_rows]
                        source_mesh = next((r for r in self.properties(source_package) if r.get("Type") == "StaticMesh"), None)
                        if source_mesh is None:
                            raise ValueError(f"Original mobile material slots missing for {source_package}")
                        source_slots = source_mesh["Properties"]["StaticMaterials"]
                        if indices is None:
                            indices = []
                            for name in metadata["materials"]:
                                matches = [i for i, slot in enumerate(source_slots) if name == Path(package(slot.get("MaterialInterface")) or "").name]
                                if len(matches) != 1:
                                    matches = [i for i, slot in enumerate(source_slots) if name == slot.get("MaterialSlotName")]
                                if len(matches) != 1:
                                    matches = [i for i, slot in enumerate(source_slots) if name == slot.get("ImportedMaterialSlotName")]
                                if len(matches) != 1:
                                    raise ValueError(f"Original mobile slot mapping ambiguous: {source_package}: {name}; exporter must supply material_indices")
                                indices.append(matches[0])
                    if mesh is None:
                        raise ValueError(f"Original static mesh unavailable: {source_package}")
                    order = self.imported_slot_order(mesh, metadata)
                    indices = [indices[index] for index in order]
                    self.mesh_cache[source_package] = mesh, metadata, indices
                mesh, metadata, indices = self.mesh_cache[source_package]
                if source_package == "Engine/Content/BasicShapes/Plane":
                    source_slots = [{"MaterialInterface": "/Engine/BasicShapes/BasicShapeMaterial"}]
                else:
                    source_slots = next(r for r in self.properties(source_package) if r.get("Type") == "StaticMesh")["Properties"]["StaticMaterials"]
                overrides = props.get("OverrideMaterials", []) or []
                materials = []
                imported_order = self.imported_slot_order(mesh, metadata)
                for imported_index, source_index in enumerate(indices):
                    reference = overrides[source_index] if source_index < len(overrides) and overrides[source_index] else source_slots[source_index]["MaterialInterface"]
                    if prepared_component and prepared_component.get("materials"):
                        binding = prepared_component["materials"][imported_order[imported_index]]
                        candidates = set()
                        for candidate_index in binding["source_material_indices"]:
                            candidate = overrides[candidate_index] if candidate_index < len(overrides) and overrides[candidate_index] else source_slots[candidate_index]["MaterialInterface"]
                            candidates.add(package(candidate))
                        if candidates != {package(binding["source_material"])}:
                            raise ValueError(f"Merged GLB slot source overrides are not identical: {source_package}: {binding['slot_name']}: {candidates}")
                        reference = binding["source_material"]
                    material = self.build_material(reference)
                    materials.append(material)
                    # Give editor previews a recovered material too. Shared mesh
                    # defaults may follow the final source component; every runtime
                    # component receives its own exact override array below.
                    mesh.set_material(imported_index, material)
                if len(materials) != len(mesh.get_editor_property("static_materials")):
                    raise ValueError(f"Incomplete original material bindings: {source_package}")
                transform, chain = self.component_transform(row, records)
                component = unreal.ViewerEnvironmentMesh()
                for key, value in {"id": self.scene_id + "_" + str(row["source"]["export_index"]) + "_" + map_key,
                    "mesh": mesh, "materials": materials, "transform": transform,
                    "cast_shadows": bool(props.get("CastShadow", True))}.items():
                    component.set_editor_property(key, value)
                components.append(component)
                self.library.save_loaded_asset(mesh)
                self.report["meshes"].append({"scene": self.scene_id, "source": row["source"], "source_mesh": source_package,
                    "asset": mesh.get_path_name(), "materials": [m.get_path_name() for m in materials],
                    "source_material_indices": indices,
                    "source_glb_material_bindings": prepared_component.get("materials") if prepared_component else None,
                    "world_transform": _json_transform(transform), "parent_chain": chain})
        if not components:
            raise ValueError(f"Custom scene has no original visible geometry: {self.scene_id}")
        return components

    def source_camera(self, scene, scene_records):
        indexed = {r["source"]["export_index"]: r for r in scene_records}
        actors = [r for r in scene_records if "Lobby.Camera.SubScene.Female" in r["properties"].get("Tags", [])]
        if len(actors) != 1:
            return None, None
        actor = actors[0]
        component_index = _index(actor["properties"].get("CameraComponent"))
        if component_index not in indexed:
            raise ValueError(f"Source camera component missing: {actor['source']}")
        component = indexed[component_index]
        props = component["properties"]
        transform, chain = self.component_transform(component, scene_records)
        filmback = props.get("Filmback", {})
        width, height = float(filmback.get("SensorWidth", 36.0)), float(filmback.get("SensorHeight", 20.25))
        focal = props.get("CurrentFocalLength")
        if focal is None or float(focal) <= 0.0:
            raise ValueError(f"Source camera focal length not observed: {component['source']}")
        horizontal = math.degrees(2.0 * math.atan(width / (2.0 * float(focal))))
        rotation, location = transform.rotation.rotator(), transform.translation
        profile = {"scene": Path(self.map_package(scene["light_map"])).name, "actor": actor["name"],
            "source": {"actor": actor["source"], "component": component["source"], "template_chain": component["template_chain"]},
            "actor_transform": {"RelativeLocation": {"X": location.x, "Y": location.y, "Z": location.z},
                "RelativeRotation": {"Pitch": rotation.pitch, "Yaw": rotation.yaw, "Roll": rotation.roll}},
            "source_camera_transform": _json_transform(transform), "original_parent_chain": chain,
            "filmback_mm": {"width": width, "height": height}, "focal_length_mm": float(focal),
            "fov_inference_not_runtime_measurement": {"horizontal_from_filmback_and_focal_degrees": horizontal},
            "stored_field_of_view_degrees": props.get("FieldOfView"),
            "source_focus_settings": props.get("FocusSettings"), "original_camera_properties": props}
        return profile, transform

    def build_scene(self, scene_id, scene):
        self.scene_id = scene_id
        light_records = self.records(self.map_package(scene["light_map"]))
        light_rows = [r for r in light_records if r["type"] in ("SkyLightComponent", "SpotLightComponent", "PointLightComponent", "DirectionalLightComponent")
                      and not self.hidden_reason(r, light_records)]
        light_rows = [{**r, "name": scene_id + "_Light_" + str(r["source"]["export_index"]) + "_" + r["type"]} for r in light_rows]
        # Reuse the standard importer field mapping, with this scene's complete hierarchy.
        self.profile = {"lighting_profiles": {"Lobby_Scene_Light_Role": {"lights": light_rows}}}
        self.standard = {"Lobby_Scene_Light_Role": light_records}
        lights, sky = self.build_lights()
        source_light_rows = {r["source"]["export_index"]: r for r in light_rows}
        for light_report in self.report["lights"]:
            if light_report["source"].get("package") == self.map_package(scene["light_map"]):
                row = source_light_rows[light_report["source"]["export_index"]]
                light_report.update(template_chain=row["template_chain"], explicit_map_properties=row["explicit_properties"])
        cube = self.import_cube(self.source_key(sky["properties"].get("Cubemap"), (".hdr",)))
        meshes = self.build_meshes(scene)
        post_rows = [r for r in light_records if r["type"] == "PostProcessVolume" and r["properties"].get("Settings")]
        # Preserve standard compatibility exposure while adding all supported source grades.
        if post_rows:
            self.profile["lighting_profiles"]["Lobby_Scene_Light_Role"]["post_process"] = post_rows[:1]
            post_process = self.build_post_process()
            settings = post_rows[0]["properties"]["Settings"]
            copied = self.report["post_process"]["copied_active_overrides"]
            for original, native in (("ColorSaturation", "color_saturation"), ("ColorContrast", "color_contrast"),
                    ("ColorGamma", "color_gamma"), ("ColorGain", "color_gain"), ("ColorOffset", "color_offset"),
                    ("ColorGammaShadows", "color_gamma_shadows"), ("ColorGainShadows", "color_gain_shadows"),
                    ("ColorGainMidtones", "color_gain_midtones")):
                if settings.get("bOverride_" + original) and original in settings:
                    value = settings[original]
                    post_process.set_editor_property(native, unreal.Vector4(*(float(value[k]) for k in "XYZW")))
                    post_process.set_editor_property("override_" + native, True)
                    copied[original] = value
        else:
            self.profile["lighting_profiles"]["Lobby_Scene_Light_Role"]["post_process"] = self.standard_profile["lighting_profiles"]["Lobby_Scene_Light_Role"]["post_process"]
            post_process = self.build_post_process()
            self.report["approximations"].append(f"{scene_id}: source postprocess absent; standard role compatibility exposure used.")
        cameras = self.standard_profile["camera_profiles"]
        recovered_camera, source_camera_transform = self.source_camera(scene, light_records)
        custom_camera = scene.get("camera_profile") if isinstance(scene.get("camera_profile"), dict) else None
        camera = recovered_camera or custom_camera or next(r for r in cameras if r["scene"] == "Lobby_Scene_Camera_Role" and r["actor"] == "Role_CameraActor_Female")
        asset_name = scene.get("environment_name") or {"queen": "DA_Environment_Queen", "lucia": "DA_Environment_Lucia",
            "naimei": "DA_Environment_Naimei", "bianca": "DA_Environment_Bianca", "queen_lobby": "DA_Environment_QueenLobby"}.get(scene_id, "DA_Environment_" + _name(scene_id))
        asset = self.create_environment(asset_name, meshes, lights, cube, sky, post_process, camera)
        asset.set_editor_property("preserve_model_origin", True)
        asset.set_editor_property("frame_from_head_bone", False)
        asset.set_editor_property("use_source_camera", source_camera_transform is not None)
        if source_camera_transform is not None:
            asset.set_editor_property("source_camera_transform", source_camera_transform)
            asset.set_editor_property("reference_aspect_ratio", camera["filmback_mm"]["width"] / camera["filmback_mm"]["height"])
            source_location = source_camera_transform.translation
            asset.set_editor_property("camera_distance", math.sqrt(source_location.x ** 2 + source_location.y ** 2 + source_location.z ** 2))
        asset.set_editor_property("reconstruction_notes", f"Recovered {scene_id} map geometry, transforms, original material textures, HDR and serialized lights. Stock UE5 shader reconstruction; stripped material equations/particles/fork lighting are recorded in Saved/CustomEnvironmentImportReport.json. Camera source and model placement require runtime verification.")
        self.library.save_loaded_asset(asset)
        result = {"status": "imported_requires_runtime_and_visual_validation", "environment_asset": asset.get_path_name(),
            "source_outfit_ids": scene.get("source_outfit_ids", []), "mesh_components": len(meshes), "lights": len(lights),
            "camera": self.report.get("camera"), "sky": self.report.get("sky"), "post_process": self.report.get("post_process"),
            "source_scene": scene, "camera_inheritance": "Recovered per-scene CameraActor tag Lobby.Camera.SubScene.Female, component/parent/template chain" if recovered_camera else
                "explicit source profile" if custom_camera else "Scene103 OtherLevel Role_CameraActor_Female fallback; source camera not observed for this scene",
            "preserve_model_origin": True, "frame_from_head_bone": False,
            "use_source_camera": source_camera_transform is not None, "source_camera": recovered_camera,
            "camera_approximation": "Static source camera and derived filmback FOV; sequencer/Blueprint dynamic camera changes are not reconstructed.",
            "model_placement": "Source ModelLocation/Rotation are evidence; capsule/skeletal offset not applied by this importer. Runtime preserves original skeletal origin instead of centering visible bounds."}
        for particle in scene.get("particles", []):
            self.report["omitted"].append({"scene": scene_id, "source_particle": particle,
                "reason": "Original Cascade modules recorded in source evidence; ViewerEnvironment has no recovered particle implementation."})
        self.report["approximations"].append(f"{scene_id}: original fork ShadowVolume/micro shadows, baked local lightgroup response, custom reflection capture and mobile light normalization are not reconstructed.")
        self.report["scenes"][scene_id] = result
        return asset

    def run(self, scene_ids=None):
        self.verify_custom_inputs()
        scenes = self.custom.get("scenes", {})
        selected = list(scene_ids or self.custom.get("default_scene_ids") or scenes)
        for scene_id in selected:
            scene = scenes[scene_id]
            missing = [p for p in scene.get("missing_mesh_packages", []) if package(p) != "Engine/Content/BasicShapes/Plane"]
            if missing:
                raise ValueError(f"Incomplete original geometry closure for {scene_id}: {missing}")
            unavailable = [c.get("mesh_source") for c in scene.get("components", []) if c.get("materials") and any(not m.get("available") for m in c["materials"])]
            if unavailable:
                raise ValueError(f"Incomplete original material closure for {scene_id}: {sorted(set(unavailable))}")
            self.build_scene(scene_id, scene)
        self.library.save_directory(DESTINATION, only_if_is_dirty=True, recursive=True)
        self.report["status"] = "imported_requires_runtime_and_visual_validation"
        return self.report


def run(PROJECT, import_task, make_material, scene_ids=None, custom_root=None):
    project = Path(PROJECT).resolve()
    report_path = project / "Saved/CustomEnvironmentImportReport.json"
    importer = None
    try:
        importer = CustomEnvironmentImporter(project, import_task, make_material, custom_root)
        return importer.run(scene_ids)
    except Exception:
        report = importer.report if importer is not None else {"status": "failed_initialization"}
        report.update(status="failed", error=traceback.format_exc())
        unreal.log_error(report["error"])
        raise
    finally:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(importer.report if importer is not None else locals().get("report", {}), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
