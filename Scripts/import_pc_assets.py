"""UE5.8 editor import of the audited PC asset plan, with resumable reports.

Run with -run=pythonscript -script=... and -PCImportStage=textures|materials|
meshes|bindings|documents|verify. All writes are isolated to /Game/PCAssets.
The source shader graphs and animations are not reconstructed by this importer.
"""
import hashlib
import json
from pathlib import Path
import re
import traceback

import unreal

ROOT = Path(unreal.Paths.project_dir()).resolve()
WORK = ROOT / "Saved/PCAssetConversion"
PLAN = json.loads((WORK / "asset_plan.json").read_text(encoding="utf-8"))
STATE_PATH = WORK / "import_state.json"
VERSION = 2
LIB = unreal.EditorAssetLibrary
TOOLS = unreal.AssetToolsHelpers.get_asset_tools()
EDIT = unreal.MaterialEditingLibrary
PREFIX = "PCAssetConversion."


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".new")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def option(name, default):
    matches = re.findall(r"(?:^|\s)-" + name + r"=([^\s]+)", unreal.SystemLibrary.get_command_line())
    return matches[-1] if matches else default


def target_file(target):
    if not target.startswith("/Game/PCAssets/"):
        raise ValueError("Target outside isolated import directory: " + target)
    return ROOT / "Content" / (target[len("/Game/"):] + ".uasset")


def source_file(row):
    path = (ROOT / row["file"]).resolve()
    if not path.is_relative_to(ROOT / "PCResources") or not path.is_file():
        raise ValueError("Source outside PCResources or missing: " + row["file"])
    if digest(path) != row["sha256"]:
        raise ValueError("Source hash changed: " + row["file"])
    return path


def ensure_owned(asset, row):
    if asset is not None:
        previous = LIB.get_metadata_tag(asset, PREFIX + "SourceSHA256")
        if previous != row["sha256"]:
            raise RuntimeError("Refusing to overwrite an unrecognized asset: " + row["target"])


def mark(asset, row, kind):
    for name, value in {
        "SourceFile": row["file"], "SourceSHA256": row["sha256"],
        "SourcePackage": row.get("source_package") or "", "ConversionKind": kind,
        "Version": str(VERSION), "ItemSHA256": row.get("item_sha256", row["sha256"]),
    }.items():
        LIB.set_metadata_tag(asset, PREFIX + name, str(value))


def save(asset):
    if not LIB.save_loaded_asset(asset, only_if_is_dirty=False):
        raise RuntimeError("Could not save " + asset.get_path_name())


def enum_value(enum_class, value):
    key = str(value).split("::")[-1]
    normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
    matches = [name for name in dir(enum_class)
               if re.sub(r"[^a-z0-9]", "", name.casefold()) == normalized]
    if len(matches) != 1:
        raise ValueError(f"Unknown or ambiguous {enum_class.__name__} value: {value}")
    return getattr(enum_class, matches[0])


def texture_settings(row):
    settings = row.get("effective_settings", row.get("settings", {}))
    settings = dict(settings.get("effective", settings.get("effective_stock_defaults", settings)))
    # The source fork added an anisotropic filter enum absent from stock UE5.8.
    # The group controls anisotropy in this candidate; preserve the source enum
    # in the plan and report this compatibility adaptation explicitly.
    if settings.get("filter") == "TF_AnisotropicLinear":
        settings["filter"] = "TF_Default"
    if settings.get("lod_group") == "TEXTUREGROUP_GrayscaleDeprecated":
        settings["lod_group"] = "TEXTUREGROUP_World"
    elif settings.get("lod_group") == "TEXTUREGROUP_EffectsSequenceFrame":
        settings["lod_group"] = "TEXTUREGROUP_Effects"
    return settings


def import_texture(row):
    source = source_file(row)
    existing = LIB.load_asset(row["target"]) if LIB.does_asset_exist(row["target"]) else None
    ensure_owned(existing, row)
    settings = texture_settings(row)
    task = unreal.AssetImportTask()
    task.filename = str(source)
    task.destination_path, task.destination_name = row["target"].rsplit("/", 1)
    task.automated, task.replace_existing, task.save = True, existing is not None, False
    factory = unreal.TextureFactory()
    factory.set_editor_property("create_material", False)
    if settings.get("compression_settings"):
        factory.set_editor_property("compression_settings", enum_value(unreal.TextureCompressionSettings, settings["compression_settings"]))
    task.factory = factory
    TOOLS.import_asset_tasks([task])
    objects = [obj for obj in task.get_objects() if isinstance(obj, unreal.Texture)]
    if len(objects) != 1:
        raise RuntimeError("Expected one texture, received: " + repr(task.imported_object_paths))
    texture = objects[0]
    if texture.get_path_name().split(".", 1)[0] != row["target"]:
        raise RuntimeError("Texture importer did not honor the exact destination")
    expected = unreal.TextureCube if source.suffix.lower() == ".hdr" else unreal.Texture2D
    if not isinstance(texture, expected):
        raise RuntimeError("Texture has wrong class: " + texture.get_class().get_name())
    enums = {
        "compression_settings": unreal.TextureCompressionSettings,
        "lod_group": unreal.TextureGroup, "address_x": unreal.TextureAddress,
        "address_y": unreal.TextureAddress, "filter": unreal.TextureFilter,
        "mip_gen_settings": unreal.TextureMipGenSettings,
    }
    applied = {}
    for name in ("srgb", "compression_settings", "lod_group", "address_x", "address_y", "filter",
                 "mip_gen_settings", "flip_green_channel", "never_stream", "compression_no_alpha", "lod_bias"):
        if name not in settings or settings[name] is None:
            continue
        if isinstance(texture, unreal.TextureCube) and name in ("address_x", "address_y"):
            continue
        value = enum_value(enums[name], settings[name]) if name in enums else settings[name]
        texture.set_editor_property(name, value)
        actual = texture.get_editor_property(name)
        if actual != value:
            raise RuntimeError("Texture setting did not persist in memory: " + name)
        applied[name] = str(actual) if name in enums else actual
    mark(texture, row, "decoded_image_import_source_settings_preserved")
    save(texture)
    result = {"class": texture.get_class().get_name(), "settings": applied}
    original_settings = row.get("effective_settings", row.get("settings", {}))
    adaptations = [{"field": key, "source": original_settings[key], "candidate": settings[key],
                    "basis": "source_fork_enum_absent_in_stock_UE58; explicit_stock_candidate_fallback"}
                   for key in ("filter", "lod_group") if key in original_settings and original_settings[key] != settings[key]]
    if adaptations:
        result["compatibility_adaptations"] = adaptations
    if isinstance(texture, unreal.Texture2D):
        result["size"] = [texture.blueprint_get_size_x(), texture.blueprint_get_size_y()]
    return result












def import_material(row):
    source_file(row)
    existing = LIB.load_asset(row["target"]) if LIB.does_asset_exist(row["target"]) else None
    ensure_owned(existing, row)
    import sys
    script_directory = str(ROOT / "Scripts")
    if script_directory not in sys.path:
        sys.path.insert(0, script_directory)
    from pc_material_adapter import build_or_update_material
    asset, result = build_or_update_material(row, PLAN, existing)
    mark(asset, row, "stock_PBR_candidate_with_source_parameters_archived")
    LIB.set_metadata_tag(asset, PREFIX + "SourceParameters", json.dumps(row.get("parameters", {}), ensure_ascii=False))
    LIB.set_metadata_tag(asset, PREFIX + "SourceRootProperties", json.dumps(row.get("serialized_root_properties", {}), ensure_ascii=False))
    save(asset)
    return result


def import_mesh(row):
    source = source_file(row)
    # Native bridge validates source ownership, bounds, indices, channels,
    # bones and all nonzero influences before creating a package.
    options = {"slot_names": row["slot_names"], "source_package": row.get("source_package") or "",
               "source_sha256": row["sha256"], "rebuild_source_corner_layout": True}
    result = json.loads(unreal.PCAssetImportLibrary.import_actorx(str(source), row["target"], json.dumps(options)))
    if result.get("status") not in ("passed", "imported", "success", "already_imported") and not result.get("success"):
        raise RuntimeError("Native ActorX import failed: " + json.dumps(result))
    asset = LIB.load_asset(row["target"])
    if not isinstance(asset, unreal.SkeletalMesh):
        raise RuntimeError("Native bridge did not save a SkeletalMesh")
    mark(asset, row, "native_ActorX_import_preserving_exported_LOD0")
    save(asset)
    return result


def bind_mesh(row):
    source_file(row)
    mesh = LIB.load_asset(row["target"])
    if not isinstance(mesh, unreal.SkeletalMesh):
        raise RuntimeError("Mesh not imported: " + row["target"])
    ensure_owned(mesh, row)
    slots = list(mesh.get_editor_property("materials"))
    if len(slots) != len(row["slot_names"]):
        raise RuntimeError("Imported material slot count differs from source")
    mapping = PLAN["packages"]
    bound, source_null = [], []
    for index, package in enumerate(row["material_packages"]):
        if not package:
            slots[index].material_interface = None
            source_null.append(index)
            continue
        target = mapping.get(package)
        target = target.get("target") if isinstance(target, dict) else target
        material = LIB.load_asset(target) if target else None
        if not isinstance(material, unreal.MaterialInterface):
            raise RuntimeError("Missing imported source material: " + package)
        slots[index].material_interface = material
        bound.append({"slot": index, "source_package": package, "target": target})
    mesh.set_editor_property("materials", slots)
    save(mesh)
    return {"bound_slots": bound, "source_null_slots": source_null}


def import_document(row):
    source = source_file(row)
    existing = LIB.load_asset(row["target"]) if LIB.does_asset_exist(row["target"]) else None
    ensure_owned(existing, row)
    if existing is None:
        destination, name = row["target"].rsplit("/", 1)
        factory = unreal.DataAssetFactory()
        factory.set_editor_property("data_asset_class", unreal.PCSourceRecord)
        existing = TOOLS.create_asset(name, destination, unreal.PCSourceRecord, factory)
    if not isinstance(existing, unreal.PCSourceRecord):
        raise RuntimeError("Source record has wrong type")
    existing.set_editor_property("source_package", row.get("source_package") or "")
    existing.set_editor_property("source_file", row["file"])
    existing.set_editor_property("source_sha256", row["sha256"])
    existing.set_editor_property("source_type", row.get("source_type") or "SourceJSON")
    existing.set_editor_property("source_json", source.read_text(encoding="utf-8-sig"))
    references = row.get("imported_assets", [])
    # Python's SoftObjectProperty coercion requires loaded UObjects. The
    # native bridge writes soft paths without loading the full asset set.
    if not unreal.PCAssetImportLibrary.set_source_record_references(existing, references):
        raise RuntimeError("Could not assign source record soft references")
    mark(existing, row, "source_JSON_archive_not_executable_graph")
    save(existing)
    return {"class": existing.get_class().get_name(), "scope": "editable_source_record"}


def verify(row):
    source = source_file(row)
    asset = LIB.load_asset(row["target"])
    if asset is None:
        raise RuntimeError("Saved asset did not load: " + row["target"])
    ensure_owned(asset, row)
    if LIB.get_metadata_tag(asset, PREFIX + "ItemSHA256") != row.get("item_sha256", row["sha256"]):
        raise RuntimeError("Saved asset was built from another plan item: " + row["target"])
    if isinstance(asset, unreal.SkeletalMesh):
        result = json.loads(unreal.PCAssetImportLibrary.verify_actorx(str(source), row["target"]))
        if result.get("status") not in ("passed", "success") and not result.get("success"):
            raise RuntimeError("Native mesh verification failed: " + json.dumps(result))
        return result
    if isinstance(asset, unreal.Texture2D):
        dimensions = [asset.blueprint_get_size_x(), asset.blueprint_get_size_y()]
        expected = row.get("dimensions", {})
        if expected and dimensions != [expected["width"], expected["height"]]:
            raise RuntimeError("Texture source dimensions differ after saving: " + row["target"])
        settings = texture_settings(row)
        enums = {"compression_settings": unreal.TextureCompressionSettings, "lod_group": unreal.TextureGroup,
                 "address_x": unreal.TextureAddress, "address_y": unreal.TextureAddress, "filter": unreal.TextureFilter}
        for name in ("srgb", "compression_settings", "lod_group", "address_x", "address_y", "filter",
                     "flip_green_channel", "never_stream", "lod_bias"):
            if name in settings:
                expected_value = enum_value(enums[name], settings[name]) if name in enums else settings[name]
                if asset.get_editor_property(name) != expected_value:
                    raise RuntimeError("Saved texture setting differs: " + name + " " + row["target"])
        return {"class": "Texture2D", "size": dimensions, "source_settings_verified": True}
    if isinstance(asset, unreal.PCSourceRecord):
        if asset.get_editor_property("source_json") != source.read_text(encoding="utf-8-sig"):
            raise RuntimeError("Source JSON did not survive the saved asset")
        options = unreal.AssetRegistryDependencyOptions()
        options.set_editor_property("include_soft_package_references", True)
        options.set_editor_property("include_hard_package_references", False)
        dependencies = unreal.AssetRegistryHelpers.get_asset_registry().get_dependencies(row["target"], options)
        actual = {str(package) for package in dependencies or []}
        if not set(row.get("imported_assets", [])).issubset(actual):
            raise RuntimeError("Saved source record lost imported-asset soft references")
    if isinstance(asset, unreal.MaterialInterface):
        parent = row.get("parent_target")
        if parent:
            if not isinstance(asset, unreal.MaterialInstanceConstant) or asset.get_editor_property("parent").get_path_name().split(".", 1)[0] != parent:
                raise RuntimeError("Saved material parent differs from the import plan")
        elif not isinstance(asset, unreal.Material):
            raise RuntimeError("Saved root material has the wrong class")
    return {"class": asset.get_class().get_name(), "fresh_load": True}


def main():
    stage = option("PCImportStage", "verify")
    start = int(option("PCImportStart", "0"))
    count = int(option("PCImportCount", "1000000"))
    source_stage = "meshes" if stage == "bindings" else stage
    rows = PLAN[source_stage] if stage != "verify" else sum((PLAN[name] for name in ("textures", "materials", "meshes", "documents")), [])
    if stage == "materials":
        # Source instances may inherit other instances; hashes used in target
        # paths have no relation to the required parent-before-child order.
        indexed = {row["target"]: row for row in rows}
        ordered, active, seen = [], set(), set()

        def visit(row):
            key = row["target"]
            if key in seen:
                return
            if key in active:
                raise ValueError("Material parent cycle: " + key)
            active.add(key)
            parent = row.get("parent_target")
            if parent:
                if parent not in indexed:
                    raise ValueError("Material parent outside plan: " + parent)
                visit(indexed[parent])
            active.remove(key)
            seen.add(key)
            ordered.append(row)

        for row in rows:
            visit(row)
        rows = ordered
    operation = {"textures": import_texture, "materials": import_material, "meshes": import_mesh,
                 "bindings": bind_mesh, "documents": import_document, "verify": verify}[stage]
    if stage == "verify":
        unreal.AssetRegistryHelpers.get_asset_registry().scan_paths_synchronous(["/Game/PCAssets"], True)
    if stage == "textures":
        for row in PLAN["textures"]:
            settings = texture_settings(row)
            for key, cls in (("compression_settings", unreal.TextureCompressionSettings),
                             ("lod_group", unreal.TextureGroup), ("filter", unreal.TextureFilter),
                             ("address_x", unreal.TextureAddress), ("address_y", unreal.TextureAddress)):
                if settings.get(key):
                    enum_value(cls, settings[key])
    state = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {"schema_version": 1, "stages": {}}
    state["plan_sha256"] = digest(WORK / "asset_plan.json")
    current = state["stages"].setdefault(stage, {})
    report = {"stage": stage, "start": start, "count": count, "status": "running", "completed": [], "failed": []}
    report_path = WORK / "Batches" / f"{stage}_{start:05d}.json"
    write_json(report_path, report)
    try:
        for index, row in enumerate(rows[start:start + count], start):
            key = row["target"]
            previous = current.get(key)
            output = target_file(key)
            item_hash = row.get("item_sha256", row["sha256"])
            previous_result = (previous or {}).get("result", {})
            layout_current = stage != "meshes" or (previous_result.get("corner_layout_version") == 2 and
                previous_result.get("tangent_build_method") == "MeshDescriptionMikkTSpace")
            if stage != "verify" and layout_current and previous and previous.get("version") == VERSION and previous.get("item_sha256") == item_hash and previous.get("source_sha256") == row["sha256"] and output.is_file() and previous.get("asset_sha256") == digest(output):
                report["completed"].append({"target": key, "status": "resumed"})
                continue
            unreal.log(f"PC_ASSET_IMPORT {stage} {index+1}/{len(rows)} {key}")
            result = operation(row)
            if not output.is_file():
                raise RuntimeError("Asset was not persisted: " + key)
            current[key] = {"status": "passed", "source_sha256": row["sha256"], "item_sha256": item_hash, "asset_sha256": digest(output),
                            "asset_bytes": output.stat().st_size, "result": result, "version": VERSION}
            if stage == "bindings" and key in state["stages"].get("meshes", {}):
                # Binding changes the mesh package, not its imported geometry.
                # Keep that stage's resume fingerprint synchronized with the
                # last saved package so repeating the full pipeline is stable.
                mesh_state = state["stages"]["meshes"][key]
                mesh_state["asset_sha256"] = current[key]["asset_sha256"]
                mesh_state["asset_bytes"] = current[key]["asset_bytes"]
            report["completed"].append({"target": key, "status": "passed"})
            # Assets are saved individually. Bound the resumable checkpoint
            # window without rewriting a growing multi-megabyte report for
            # every tiny source record; finally always persists the latest.
            if (index - start + 1) % 16 == 0:
                write_json(STATE_PATH, state)
                write_json(report_path, report)
        report["status"] = "passed"
    except Exception:
        report["status"] = "failed"
        report["failed"].append(traceback.format_exc())
        raise
    finally:
        write_json(report_path, report)
        write_json(STATE_PATH, state)
        unreal.log("PC_ASSET_IMPORT_BATCH " + json.dumps({k: v for k, v in report.items() if k != "completed"}))


main()
