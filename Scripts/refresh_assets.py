"""Incrementally refresh verified materials, animations, UI and role environment.

Run with the UE editor Python plugin. Existing SkeletalMesh/Skeleton assets are
reused, so refreshing appearance never reimports or alters skin weights.
"""
import hashlib
import json
from pathlib import Path
import sys
import traceback
import unreal

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import import_assets as importer


def existing_mesh(part, destination):
    path = destination + "/SK_" + part["id"]
    mesh = importer.LIBRARY.load_asset(path)
    if not isinstance(mesh, unreal.SkeletalMesh):
        raise RuntimeError("Run the full import for missing SkeletalMesh: " + path)
    importer.REPORT["imported_meshes"].append({"id": part["id"], "asset": mesh.get_path_name(),
        "source": part["mesh"], "operation": "reused_existing_mesh_and_skeleton",
        "slots": [str(s.material_slot_name) for s in mesh.materials]})
    return mesh


original_texture = importer.import_texture


def existing_texture(relative, role):
    if not relative:
        return None
    key = (relative, role)
    if key in importer.TEXTURES:
        return importer.TEXTURES[key]
    digest = hashlib.sha1((relative + role).encode()).hexdigest()[:10]
    folder = "/Game/Characters/Textures/" + digest
    for path in importer.LIBRARY.list_assets(folder, recursive=False, include_folder=False):
        obj = importer.LIBRARY.load_asset(path)
        if isinstance(obj, unreal.Texture2D):
            importer.TEXTURES[key] = obj
            return obj
    return original_texture(relative, role)


try:
    importer.import_mesh = existing_mesh
    importer.import_texture = existing_texture
    importer.REPORT["operation"] = "incremental_material_animation_environment_refresh"
    # Keep all FBX animation imports on the same verified legacy path even
    # though the mesh import (which normally sets this flag) is skipped.
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    unreal.SystemLibrary.execute_console_command(world, "Interchange.FeatureFlags.Import.FBX 0")
    importer.run()
except Exception:
    importer.REPORT["status"] = "failed"
    importer.REPORT["error"] = traceback.format_exc()
    unreal.log_error(importer.REPORT["error"])
    raise
finally:
    importer.REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    importer.REPORT_FILE.write_text(json.dumps(importer.REPORT, ensure_ascii=False, indent=2), encoding="utf-8")
