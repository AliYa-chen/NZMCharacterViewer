"""Bind imported outfit scenes and optionally refresh selected material slots.

Pass -ViewerMaterialFilter=ExactSourceSlotName[,AnotherName] to the Editor.
Reuses mesh/skeleton/animation assets; never reimports character geometry.
"""
from pathlib import Path
import json
import re
import sys
import traceback
import unreal

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import import_assets as importer
from verify_imported_assets import struct_fields, extract_paths

report = {"status": "started", "scene_bindings": [], "materials_updated": []}
try:
    manifest = json.loads((importer.SOURCE / "manifest.json").read_text())
    sources = {(c["id"], o["id"]): o for c in manifest["characters"] for o in c["outfits"]}
    match = re.search(r"-ViewerMaterialFilter=([^\s]+)", unreal.SystemLibrary.get_command_line())
    selected = set(match[1].split(",")) if match else set()
    catalog = importer.LIBRARY.load_asset("/Game/Viewer/Data/DA_ViewerCatalog")
    if not isinstance(catalog, unreal.ViewerCatalog):
        raise RuntimeError("Import the runtime Catalog first")
    characters = list(catalog.get_editor_property("characters"))
    for character in characters:
        cid = str(character.get_editor_property("id"))
        outfits = list(character.get_editor_property("outfits"))
        for outfit in outfits:
            oid = str(outfit.get_editor_property("id"))
            source = sources[cid, oid]
            environment_path = source.get("environment_asset", "/Game/Viewer/Environment/DA_Environment")
            environment = importer.LIBRARY.load_asset(environment_path)
            if not isinstance(environment, unreal.ViewerEnvironment):
                raise RuntimeError("Missing imported outfit environment: " + environment_path)
            outfit.set_editor_property("environment", environment)
            report["scene_bindings"].append({"character": cid, "outfit": oid, "environment": environment.get_path_name()})
            if selected:
                source_parts = {p["id"]: p for p in source["parts"]}
                parts = list(outfit.get_editor_property("parts"))
                for part in parts:
                    pid = str(part.get_editor_property("id"))
                    source_part = source_parts[pid]
                    chosen = [s for s in source_part.get("materials", []) if s.get("slot_name") in selected]
                    if not chosen:
                        continue
                    # Unloaded TSoftObjectPtr values can be exposed as None
                    # by Python. Read their serialized paths before replacing
                    # a single slot so the untouched soft refs are preserved.
                    fields = struct_fields(part)
                    mesh_paths = extract_paths(fields.get("mesh", ""))
                    if len(mesh_paths) != 1:
                        raise RuntimeError("Cannot read the serialized mesh reference: " + pid)
                    mesh = importer.LIBRARY.load_asset(mesh_paths[0])
                    if not isinstance(mesh, unreal.SkeletalMesh):
                        raise RuntimeError("Cannot load existing mesh: " + mesh_paths[0])
                    slots = list(mesh.get_editor_property("materials"))
                    material_paths = extract_paths(fields.get("materials", ""))
                    materials = [importer.LIBRARY.load_asset(path) for path in material_paths]
                    if len(materials) != len(slots) or any(m is None for m in materials):
                        raise RuntimeError("Cannot preserve all existing material references: " + pid)
                    for spec in chosen:
                        key = importer.canonical_slot(spec.get("slot_name", ""))
                        matches = [i for i, slot in enumerate(slots) if key in (
                            importer.canonical_slot(slot.material_slot_name),
                            importer.canonical_slot(importer.imported_slot_name(slot)))]
                        if len(matches) != 1:
                            raise RuntimeError("Material slot must match exactly: " + spec["slot_name"])
                        material = importer.make_material(spec, f"/Game/Characters/{cid}/{oid}/{pid}/Materials")
                        materials[matches[0]] = material
                        report["materials_updated"].append({"character": cid, "outfit": oid, "part": pid,
                            "slot": spec["slot_name"], "asset": material.get_path_name()})
                    part.set_editor_property("materials", materials)
                outfit.set_editor_property("parts", parts)
        character.set_editor_property("outfits", outfits)
    catalog.set_editor_property("characters", characters)
    if not importer.LIBRARY.save_loaded_asset(catalog):
        raise RuntimeError("Catalog did not save")
    report["status"] = "saved_requires_runtime_validation"
    report["material_details"] = importer.REPORT["materials"]
except Exception:
    report["status"] = "failed"
    report["error"] = traceback.format_exc()
    raise
finally:
    (importer.PROJECT / "Saved/ViewerCatalogRefreshReport.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
