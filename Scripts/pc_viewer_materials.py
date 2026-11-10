"""Read-only validation and binding of approved PC viewer material overrides.

load_report() checks the frozen plan/index, every new material package, and all
recorded protected source packages. bound_materials(ue, mesh, report) returns
the complete native material array with only approved Eye slots replaced.
An unlisted mesh keeps its complete native array.
"""
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "Saved/PCViewerIntegration/material_overrides_report.json"
TARGET_ROOT = "/Game/PCAssets/ViewerMaterials"
OWNER = "pc_viewer_eye_v1"
OWNER_TAG = "PCViewerMaterials.Owner"


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def package(reference):
    if reference is None or str(reference) in ("", "None"):
        return None
    return str(reference).split(".", 1)[0]


def asset_file(reference, root=ROOT):
    name = package(reference)
    if not name or not name.startswith("/Game/") or any(part in (".", "..", "") for part in name[6:].split("/")):
        raise ValueError("Expected an exact project package: " + str(reference))
    return Path(root) / "Content" / (name[6:] + ".uasset")


def effective_material_paths(mesh_package, native_paths, report):
    """Validate slot identity and return package paths, preserving empty slots."""
    native = [package(value) for value in native_paths]
    row = report["meshes"].get(package(mesh_package))
    if row is None:
        return native
    if [package(value) for value in row["native_materials"]] != native:
        raise RuntimeError("Native source material slots changed: " + package(mesh_package))
    result = [package(value) for value in row["materials"]]
    if len(result) != len(native):
        raise RuntimeError("Viewer override changes native slot count: " + package(mesh_package))
    overrides = {int(value["slot"]): value for value in row["overrides"]}
    if not overrides or len(overrides) != len(row["overrides"]):
        raise RuntimeError("Empty or duplicate viewer override slots: " + package(mesh_package))
    approved = {value["target"] for value in report["materials"] if value["class"] == "MaterialInstanceConstant"}
    for slot, value in overrides.items():
        if not 0 <= slot < len(native) or package(value["original"]) != native[slot] or package(value["target"]) != result[slot]:
            raise RuntimeError("Viewer override does not identify its original slot")
        if result[slot] not in approved or not result[slot].startswith(TARGET_ROOT + "/"):
            raise RuntimeError("Viewer override target is not an approved Eye instance")
    if any(result[slot] != original for slot, original in enumerate(native) if slot not in overrides):
        raise RuntimeError("Viewer override changes an unapproved material slot")
    return result


def load_report(root=ROOT, path=None):
    root = Path(root)
    path = Path(path) if path is not None else root / "Saved/PCViewerIntegration/material_overrides_report.json"
    report = json.loads(path.read_text(encoding="utf-8-sig"))
    if report.get("status") != "passed" or report.get("owner") != OWNER:
        raise RuntimeError("PC viewer material report is not complete and owned")
    if report.get("plan_sha256") != digest(root / "Saved/PCViewerIntegration/viewer_plan.json") or report.get("asset_index_sha256") != digest(root / "Docs/PCAssetConversion/asset_index.json"):
        raise RuntimeError("PC viewer material report uses another plan or conversion index")
    records = report.get("materials", [])
    if len({value["target"] for value in records}) != len(records):
        raise RuntimeError("PC viewer material report contains duplicate packages")
    for value in records:
        target = value["target"]
        disk = asset_file(target, root)
        if not target.startswith(TARGET_ROOT + "/") or disk.relative_to(root).as_posix() != value["file"]:
            raise RuntimeError("PC viewer material report contains an out-of-scope package")
        if disk.stat().st_size != value["bytes"] or digest(disk) != value["sha256"]:
            raise RuntimeError("PC viewer Eye material changed after verification: " + target)
    for value in report.get("protected_assets", []):
        disk = asset_file(value["target"], root)
        if disk.stat().st_size != value["bytes"] or digest(disk) != value["sha256"]:
            raise RuntimeError("Protected PC source or animation package changed: " + value["target"])
    for mesh, value in report["meshes"].items():
        effective_material_paths(mesh, value["native_materials"], report)
    return report


def bound_materials(ue, mesh, report):
    """Return loaded MaterialInterface objects for every native mesh slot."""
    slots = list(mesh.get_editor_property("materials"))
    native = [slot.get_editor_property("material_interface") for slot in slots]
    paths = [value.get_path_name() if value is not None else None for value in native]
    effective = effective_material_paths(mesh.get_path_name(), paths, report)
    result = []
    for original, old_path, new_path in zip(native, map(package, paths), effective):
        if new_path == old_path:
            result.append(original)
            continue
        value = ue.load_asset(new_path)
        if not isinstance(value, ue.MaterialInterface) or ue.EditorAssetLibrary.get_metadata_tag(value, OWNER_TAG) != OWNER:
            raise RuntimeError("Viewer Eye override did not load as an owned MaterialInterface: " + str(new_path))
        result.append(value)
    return result
