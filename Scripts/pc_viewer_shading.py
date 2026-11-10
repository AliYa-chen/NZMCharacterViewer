"""Read-only loading and exact binding for PC skin, Hair and cloth surfaces.

The authoring report is produced by prepare_pc_viewer_shaders.py. This helper
does not edit meshes, source materials, the Catalog, or the working Eye layer.
"""
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TARGET_ROOT = "/Game/PCAssets/ViewerShading"
REPORT_PATH = ROOT / "Saved/PCFeatureIntegration/shading_report.json"
OWNER = "pc_viewer_native_shading_v1"
OWNER_TAG = "PCViewerShading.Owner"


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def package(reference):
    if reference is None or str(reference) in ("", "None"):
        return None
    return str(reference).split(".", 1)[0]


def asset_file(reference, root=ROOT):
    name = package(reference)
    if not name or not name.startswith("/Game/") or any(part in ("", ".", "..") for part in name[6:].split("/")):
        raise ValueError("Expected an exact project package: " + str(reference))
    return Path(root) / "Content" / (name[6:] + ".uasset")


def effective_material_paths(mesh_package, native_paths, report):
    """Return a complete native array with only approved material slots replaced."""
    native = [package(value) for value in native_paths]
    row = report["meshes"].get(package(mesh_package))
    if row is None:
        return native
    if [package(value) for value in row["native_materials"]] != native:
        raise RuntimeError("Native source material slots changed: " + package(mesh_package))
    result = [package(value) for value in row["materials"]]
    if len(result) != len(native):
        raise RuntimeError("Native shading changes the source slot count")
    approved = {value["target"]: value for value in report["materials"]}
    overrides = {int(value["slot"]): value for value in row["overrides"]}
    if not overrides or len(overrides) != len(row["overrides"]):
        raise RuntimeError("Empty or repeated native shading slots")
    for slot, value in overrides.items():
        target = package(value["target"])
        if (not 0 <= slot < len(native) or package(value["original"]) != native[slot]
                or target != result[slot] or target not in approved
                or approved[target].get("source") != native[slot]
                or approved[target].get("kind") != value.get("kind")
                or value.get("kind") not in ("skin", "hair", "cloth_surface")
                or not target.startswith(TARGET_ROOT + "/")):
            raise RuntimeError("Native shading does not identify an approved original slot")
    if any(result[slot] != old for slot, old in enumerate(native) if slot not in overrides):
        raise RuntimeError("Native shading changes an unapproved material slot")
    return result


def load_report(root=ROOT, path=None):
    root = Path(root)
    path = Path(path) if path else root / "Saved/PCFeatureIntegration/shading_report.json"
    report = json.loads(path.read_text(encoding="utf-8-sig"))
    if report.get("schema_version") != 1 or report.get("status") != "passed" or report.get("owner") != OWNER:
        raise RuntimeError("Native shading report is incomplete or unowned")
    plan_path = root / "Saved/PCViewerIntegration/viewer_plan.json"
    if report.get("plan_sha256") != digest(plan_path) or report.get("asset_index_sha256") != digest(root / "Docs/PCAssetConversion/asset_index.json"):
        raise RuntimeError("Native shading report uses another viewer plan/index")
    if report.get("cloth_plan_sha256") != digest(root / "Saved/PCFeatureIntegration/cloth_plan.json"):
        raise RuntimeError("Native shading Clothing usages use another cloth plan")
    usages = report.get("master_usage_validation", {})
    if not usages or any(value.get("readback_verified") is not True
                         or value.get("after", {}).get("used_with_clothing") is not True
                         or value.get("after", {}).get("used_with_skeletal_mesh") is not True for value in usages.values()):
        raise RuntimeError("Native shading Clothing usage readback is missing")
    if any(value.get("clothing_usage_readback") is not True for value in report.get("materials", [])):
        raise RuntimeError("Native material instance Clothing usage was not verified")
    records = report.get("masters", []) + report.get("materials", []) + report.get("profiles", [])
    if not records or len({value["target"] for value in records}) != len(records):
        raise RuntimeError("Native shading packages are missing or repeated")
    for value in records:
        target = value["target"]
        disk = asset_file(target, root)
        if not target.startswith(TARGET_ROOT + "/") or disk.relative_to(root).as_posix() != value["file"]:
            raise RuntimeError("Native shading contains an out-of-scope package")
        if disk.stat().st_size != value["bytes"] or digest(disk) != value["sha256"]:
            raise RuntimeError("Native shading package changed: " + target)
    for value in report.get("protected_assets", []):
        disk = asset_file(value["target"], root)
        if disk.stat().st_size != value["bytes"] or digest(disk) != value["sha256"]:
            raise RuntimeError("Protected source package changed: " + value["target"])
    for mesh, value in report["meshes"].items():
        effective_material_paths(mesh, value["native_materials"], report)
    return report


def bound_materials(ue, mesh, report):
    native = [slot.get_editor_property("material_interface") for slot in mesh.get_editor_property("materials")]
    old = [value.get_path_name() if value is not None else None for value in native]
    effective = effective_material_paths(mesh.get_path_name(), old, report)
    result = []
    for original, old_path, new_path in zip(native, map(package, old), effective):
        if old_path == new_path:
            result.append(original)
            continue
        value = ue.load_asset(new_path)
        if not isinstance(value, ue.MaterialInterface) or ue.EditorAssetLibrary.get_metadata_tag(value, OWNER_TAG) != OWNER:
            raise RuntimeError("Native shading target is not an owned MaterialInterface: " + str(new_path))
        result.append(value)
    return result
