"""Read-only UE Editor asset verification, after import_assets.py completes.

Run with Tools > Execute Python Script, or -ExecutePythonScript=<this file>.
Writes only Saved/AssetValidation.json; never saves or alters content assets.
It does not claim shader appearance, iOS performance or weight fidelity from
metadata. Properties unavailable in the running editor are reported unknown.
"""
import json
import math
from pathlib import Path
import re
import traceback

import unreal

PROJECT = Path(__file__).resolve().parent.parent
CATALOG_PATH = "/Game/Viewer/Data/DA_ViewerCatalog.DA_ViewerCatalog"
OUTPUT = PROJECT / "Saved/AssetValidation.json"
REPORT = {"status": "running", "catalog": CATALOG_PATH, "checks": [], "parts": [],
          "errors": [], "unknowns": [], "dependency_packages": [], "project_disk_packages": [],
          "limitations": ["No visual or touch-input validation.", "No shader compile or iOS device test.",
                          "Disk checks require nonempty serialized package files; they do not replace a fresh-process load or cooking test.",
                          "Bone influence build setting is not proof that imported per-vertex weights match the source."]}
REFERENCE_PATHS = set()
DISK_PACKAGES = {}


def check(label, passed, details=None):
    REPORT["checks"].append({"check": label, "passed": bool(passed), "details": details})
    if not passed:
        REPORT["errors"].append({"check": label, "details": details})


def unknown(label, error):
    REPORT["unknowns"].append({"check": label, "reason": str(error)})


def probe(label, callback):
    try:
        return callback()
    except Exception as exc:
        unknown(label, exc)
        return None


def struct_fields(value):
    """Read serialized fields when Python returns an unloaded soft ref as None."""
    text = value.export_text().strip().strip("()")
    fields, depth, quote, escaped, start = {}, 0, None, False, 0
    for index, char in enumerate(text + ","):
        if escaped:
            escaped = False
            continue
        if quote:
            if char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in ("'", '"'):
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            field = text[start:index]
            if "=" in field:
                key, serialized = field.split("=", 1)
                fields[key.strip().replace("_", "").casefold()] = serialized.strip()
            start = index + 1
    return fields


def extract_paths(text):
    paths = re.findall(r"/(?:Game|Engine|[A-Za-z0-9_]+)/[A-Za-z0-9_./:-]+\.[A-Za-z0-9_:]+", str(text))
    return [path for path in paths if not path.startswith("/Script/")]


def verify_project_disk_package(path):
    """AssetRegistry may expose live unsaved UObjects; require real bytes too."""
    package = path.split(".", 1)[0].split(":", 1)[0]
    if not package.startswith("/Game/"):
        return None
    if package in DISK_PACKAGES:
        return DISK_PACKAGES[package]
    relative = package[len("/Game/"):]
    candidates = [PROJECT / "Content" / (relative + extension) for extension in (".uasset", ".umap")]
    file = next((candidate for candidate in candidates if candidate.is_file()), candidates[0])
    record = {"package": package, "file": str(file), "exists": file.is_file(), "bytes": 0,
              "readable_nonempty": False, "ue_package_magic_valid": False}
    try:
        if file.is_file():
            record["bytes"] = file.stat().st_size
            with file.open("rb") as stream:
                prefix = stream.read(32)
            record["readable_nonempty"] = len(prefix) > 0 and record["bytes"] > 0
            record["ue_package_magic_valid"] = prefix[:4] == b"\xc1\x83\x2a\x9e"
            record["prefix_hex"] = prefix[:4].hex()
    except OSError as exc:
        record["read_error"] = str(exc)
    DISK_PACKAGES[package] = record
    REPORT["project_disk_packages"].append(record)
    check("project_package_saved_to_disk", record["exists"] and record["readable_nonempty"]
          and record["ue_package_magic_valid"], record)
    return record


def load_reference(value, serialized, label, expected_type, required=True):
    if isinstance(value, unreal.Object):
        path = value.get_path_name()
        obj = value
    else:
        paths = extract_paths(serialized if serialized is not None else value)
        if not paths:
            if required:
                unknown(label, "No readable object path in reflected value or serialized soft reference")
            return None
        path = paths[0]
        obj = probe(label + ".load", lambda: unreal.load_asset(path))
    REFERENCE_PATHS.add(path)
    verify_project_disk_package(path)
    check(label + ".exists_and_type", obj is not None and isinstance(obj, expected_type),
          {"path": path, "expected": expected_type.__name__, "actual": obj.get_class().get_name() if obj else None})
    return obj


def vector3(value):
    return [float(value.x), float(value.y), float(value.z)]


def verify_part(part, expected, character_id, outfit_id, subsystem):
    part_id = str(part.get_editor_property("id"))
    label = f"{character_id}/{outfit_id}/{part_id}"
    fields = probe(label + ".serialized_fields", lambda: struct_fields(part)) or {}
    record = {"character": character_id, "outfit": outfit_id, "part": part_id}
    REPORT["parts"].append(record)
    mesh_value = probe(label + ".mesh_property", lambda: part.get_editor_property("mesh"))
    mesh = load_reference(mesh_value, fields.get("mesh"), label + ".mesh", unreal.SkeletalMesh)
    if mesh is None:
        return
    record["mesh"] = mesh.get_path_name()
    skeleton = probe(label + ".skeleton", lambda: mesh.get_editor_property("skeleton"))
    check(label + ".skeleton_exists", isinstance(skeleton, unreal.Skeleton),
          skeleton.get_path_name() if skeleton else None)
    if skeleton:
        REFERENCE_PATHS.add(skeleton.get_path_name())
        verify_project_disk_package(skeleton.get_path_name())
    bounds = probe(label + ".bounds", mesh.get_bounds)
    if bounds is not None:
        origin = probe(label + ".bounds_origin", lambda: vector3(bounds.origin))
        extent = probe(label + ".bounds_extent", lambda: vector3(bounds.box_extent))
        radius = probe(label + ".bounds_radius", lambda: float(bounds.sphere_radius))
        record["bounds_cm"] = {"origin": origin, "box_extent": extent, "sphere_radius": radius}
        if origin is not None and extent is not None and radius is not None:
            check(label + ".bounds_finite_nonempty", all(math.isfinite(v) for v in origin + extent + [radius])
                  and all(v >= 0 for v in extent) and max(extent) > 0 and radius > 0, record["bounds_cm"])
    slots = probe(label + ".mesh_slots", lambda: list(mesh.get_editor_property("materials")))
    overrides = probe(label + ".catalog_materials", lambda: list(part.get_editor_property("materials")))
    material_paths = extract_paths(fields.get("materials", ""))
    if slots is not None:
        record["mesh_slots"] = [str(slot.get_editor_property("material_slot_name")) for slot in slots]
        record["mesh_slot_count"] = len(slots)
        if expected:
            expected_indices = {int(m["slot"]) for m in expected.get("materials", [])}
            check(label + ".source_slot_count", len(slots) == len(expected_indices),
                  {"engine": len(slots), "source_declared": len(expected_indices)})
    if overrides is not None:
        record["override_count"] = len(overrides)
        if slots is not None:
            check(label + ".override_count", len(overrides) == len(slots), {"overrides": len(overrides), "mesh_slots": len(slots)})
        for index, value in enumerate(overrides):
            serialized = material_paths[index] if index < len(material_paths) else None
            load_reference(value, serialized, label + f".material_{index}", unreal.MaterialInterface)
    if subsystem:
        vertices = probe(label + ".lod0_vertices", lambda: int(subsystem.get_num_verts(mesh, 0)))
        sections = probe(label + ".lod0_sections", lambda: int(subsystem.get_num_sections(mesh, 0)))
        record.update({"lod0_vertices": vertices, "lod0_sections": sections})
        if vertices is not None:
            check(label + ".nonempty_geometry", vertices > 0, vertices)
        settings = probe(label + ".build_settings", lambda: subsystem.get_lod_build_settings(mesh, 0))
        if settings is not None:
            limit = probe(label + ".influence_limit", lambda: int(settings.get_editor_property("bone_influence_limit")))
            record["bone_influence_build_limit"] = limit
            if limit is not None:
                check(label + ".eight_influence_build_limit", limit == 8, limit)
    animation_expected = bool(expected and expected.get("animation"))
    animation_value = probe(label + ".animation_property", lambda: part.get_editor_property("animation"))
    animation_text = fields.get("animation")
    has_animation = animation_value is not None or bool(extract_paths(animation_text or ""))
    if has_animation or animation_expected:
        animation = load_reference(animation_value, animation_text, label + ".animation", unreal.AnimSequence)
        if animation:
            record["animation"] = animation.get_path_name()
            animation_skeleton = probe(label + ".animation_skeleton", lambda: animation.get_editor_property("skeleton"))
            if animation_skeleton:
                REFERENCE_PATHS.add(animation_skeleton.get_path_name())
                verify_project_disk_package(animation_skeleton.get_path_name())
            check(label + ".animation_skeleton_matches", skeleton is not None and animation_skeleton == skeleton,
                  {"mesh": skeleton.get_path_name() if skeleton else None,
                   "animation": animation_skeleton.get_path_name() if animation_skeleton else None})
            duration = probe(label + ".animation_duration", lambda: float(animation.get_play_length()))
            record["animation_duration_seconds"] = duration
            if duration is not None:
                check(label + ".animation_duration_positive", math.isfinite(duration) and duration > 0, duration)
            # Private compressed tracks are deliberately not probed or reported as verified.
            record["per_vertex_weight_fidelity"] = "unknown_requires_source_vs_engine_data_comparison"


def verify_dependency_closure():
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    # Query dependencies from newly saved packages, rather than only live objects
    # left in the editor by the importer. This updates registry state, not assets.
    registry.scan_paths_synchronous(["/Game/Characters", "/Game/Viewer"], True)
    REPORT["registry_refresh"] = {"paths": ["/Game/Characters", "/Game/Viewer"],
                                  "force_rescan": True, "completed": True}
    options = unreal.AssetRegistryDependencyOptions()
    options.set_editor_property("include_soft_package_references", True)
    options.set_editor_property("include_hard_package_references", True)
    queue = [path.split(".", 1)[0] for path in REFERENCE_PATHS]
    seen = set()
    while queue:
        package = queue.pop()
        if package in seen or package.startswith("/Script/"):
            continue
        seen.add(package)
        if len(seen) > 10000:
            unknown("dependency_closure", "Stopped at 10,000 packages; dependency coverage is partial")
            break
        disk = verify_project_disk_package(package)
        assets = list(registry.get_assets_by_package_name(package, True))
        exists = bool(assets)
        record = {"package": package, "exists_in_on_disk_asset_registry": exists,
                  "project_disk_file": disk["file"] if disk else None}
        REPORT["dependency_packages"].append(record)
        check("dependency_package_in_on_disk_registry", exists, package)
        # Follow project-owned packages; engine packages are validated as leaves.
        if exists and package.startswith("/Game/"):
            dependencies = registry.get_dependencies(package, options)
            if dependencies is None:
                classes = []
                for asset in assets:
                    name = probe("dependency_asset_class", lambda asset=asset: str(asset.asset_class_path.asset_name))
                    if name is not None:
                        classes.append(name)
                record["asset_classes"] = classes
                # Plain imported textures can legitimately be leaves (their
                # native /Script class isn't a content-package dependency).
                # A None result for mesh/material/catalog/skeleton is not proof
                # of an empty dependency graph and remains an explicit unknown.
                if len(classes) == len(assets) and all(name in ("Texture2D", "TextureCube", "Texture2DArray", "VolumeTexture") for name in classes):
                    record["dependency_query"] = "none_for_saved_texture_leaf"
                else:
                    record["dependency_query"] = "unknown_none_after_synchronous_rescan"
                    unknown("dependency_query", package + " returned None after an on-disk synchronous rescan; not treated as dependency-free")
            else:
                record["dependency_query"] = "success"
                record["dependency_count"] = len(dependencies)
                queue.extend(str(dep) for dep in dependencies)


def run():
    source = json.loads((PROJECT / "SourceAssets/manifest.json").read_text(encoding="utf-8"))
    expected = {}
    for character in source["characters"]:
        for outfit in character["outfits"]:
            if not outfit.get("available_in_viewer", True):
                continue
            parts = {part["id"]: part for part in outfit["parts"] if part.get("available_in_viewer", True)}
            if parts:
                expected[(character["id"], outfit["id"])] = parts
    REPORT["expected"] = {"characters": len({key[0] for key in expected}), "outfits": len(expected),
                          "parts": sum(len(parts) for parts in expected.values())}
    verify_project_disk_package(CATALOG_PATH)
    catalog = unreal.load_asset(CATALOG_PATH)
    check("catalog_exists_and_type", catalog is not None and isinstance(catalog, unreal.ViewerCatalog), CATALOG_PATH)
    if catalog is None:
        return
    REFERENCE_PATHS.add(CATALOG_PATH)
    subsystem = probe("skeletal_mesh_editor_subsystem", lambda: unreal.get_editor_subsystem(unreal.SkeletalMeshEditorSubsystem))
    observed = set()
    characters = list(catalog.get_editor_property("characters"))
    check("catalog_character_count", len(characters) == REPORT["expected"]["characters"], len(characters))
    for character in characters:
        character_id = str(character.get_editor_property("id"))
        for outfit in character.get_editor_property("outfits"):
            outfit_id = str(outfit.get_editor_property("id"))
            key = (character_id, outfit_id)
            observed.add(key)
            check("catalog_outfit_expected", key in expected, list(key))
            parts = list(outfit.get_editor_property("parts"))
            source_parts = expected.get(key, {})
            check("catalog_part_ids", {str(p.get_editor_property("id")) for p in parts} == set(source_parts), list(key))
            for part in parts:
                part_id = str(part.get_editor_property("id"))
                verify_part(part, source_parts.get(part_id), character_id, outfit_id, subsystem)
    check("catalog_outfit_set", observed == set(expected), {"expected": [list(k) for k in expected], "observed": [list(k) for k in sorted(observed)]})
    probe("asset_registry_dependency_closure", verify_dependency_closure)
    verify_project_disk_package("/Game/Viewer/Maps/L_Viewer")


if __name__ == "__main__":
    try:
        run()
    except Exception:
        REPORT["errors"].append({"check": "fatal", "details": traceback.format_exc()})
    finally:
        REPORT["status"] = "failed" if REPORT["errors"] else "passed_with_unknowns" if REPORT["unknowns"] else "passed_structural_checks"
        REPORT["summary"] = {"parts": len(REPORT["parts"]), "checks": len(REPORT["checks"]),
                             "errors": len(REPORT["errors"]), "unknowns": len(REPORT["unknowns"]),
                             "project_disk_packages": len(REPORT["project_disk_packages"]),
                             "missing_or_invalid_disk_packages": sum(not (r["exists"] and r["readable_nonempty"] and r["ue_package_magic_valid"])
                                 for r in REPORT["project_disk_packages"])}
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding="utf-8")
        unreal.log(f"Asset validation {REPORT['status']}: {OUTPUT}")
    if REPORT["errors"]:
        raise RuntimeError("Imported asset verification failed; see Saved/AssetValidation.json")
