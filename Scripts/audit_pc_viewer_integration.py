"""Read-only UE audit of the expanded runtime Catalog and its saved references.

Run with -ExecutePythonScript=<this file> after applying the viewer plan and
animation batches. Writes only Saved/PCViewerIntegration/integration_audit.json;
never saves Content assets. Uses serialized soft-reference text because an
unloaded TSoftObjectPtr can appear as None in Unreal Python.

This verifies saved assets and their cook dependency graph, not rendered
appearance, a completed cook, iOS performance, or original-game fidelity.
"""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import traceback


ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "Saved/PCViewerIntegration"
PLAN_PATH = WORK / "viewer_plan.json"
BASELINE_PATH = WORK / "baseline_catalog.json"
ANIMATION_PATH = WORK / "animation_report.json"
PORTRAIT_PATH = WORK / "portraits_report.json"
DYNAMICS_PATH = WORK / "dynamics_report.json"
MATERIAL_PATH = WORK / "material_overrides_report.json"
ENVIRONMENT_PLAN_PATH = WORK / "viewer_environment_plan.json"
ENVIRONMENT_REPORT_PATH = WORK / "viewer_environment_report.json"
INDEX_PATH = ROOT / "Docs/PCAssetConversion/asset_index.json"
OUTPUT = WORK / "integration_audit.json"
CATALOG = "/Game/Viewer/Data/DA_ViewerCatalog.DA_ViewerCatalog"
SOCKET_ACCESSORIES = {"10056010007", "10054010006", "10054010007"}
BASELINE_PART_FIELDS = ("id", "name", "mesh", "animation", "materials", "dynamics",
                        "transform", "visible", "animation_loop")


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def package(path):
    return str(path).split(".", 1)[0].split(":", 1)[0]


def object_path(path):
    value = str(path)
    return value if "." in value else value + "." + value.rsplit("/", 1)[-1]


def extract_paths(text):
    return [value for value in re.findall(
        r"/[A-Za-z0-9_]+/[A-Za-z0-9_./:-]+\.[A-Za-z0-9_:]+", str(text))
        if not value.startswith("/Script/")]


def struct_fields(value):
    text = value.export_text().strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
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


def none_name(value):
    return str(value) in ("", "None", "NAME_None")


def transform_values(value):
    return {"translation": [float(getattr(value.translation, axis)) for axis in "xyz"],
            "rotation_xyzw": [float(getattr(value.rotation, axis)) for axis in "xyzw"],
            "scale": [float(getattr(value.scale3d, axis)) for axis in "xyz"]}


def transform_matches(actual, expected):
    dimensions = {"translation": 3, "rotation_xyzw": 4, "scale": 3}
    if any(len(actual.get(key, [])) != size or len(expected.get(key, [])) != size
           for key, size in dimensions.items()):
        return False
    values = [number for row in actual.values() for number in row]
    if not all(math.isfinite(number) for number in values):
        return False
    for key in ("translation", "scale"):
        if any(abs(a - b) > 1e-5 for a, b in zip(actual[key], expected[key])):
            return False
    rotation = actual["rotation_xyzw"]
    target = expected["rotation_xyzw"]
    return min(max(abs(a - sign * b) for a, b in zip(rotation, target))
               for sign in (-1, 1)) <= 1e-5


class Audit:
    def __init__(self, unreal):
        self.ue = unreal
        self.report = {"schema_version": 1, "status": "running", "catalog": CATALOG,
                       "started_at_utc": datetime.now(timezone.utc).isoformat(),
                       "checks": [], "errors": [], "unknowns": [], "new_parts": [],
                       "environments": [], "assets": [], "characters": [],
                       "limitations": [
                           "No rendered appearance, input, shader, physics, or iOS device result is inferred.",
                           "The saved hard/soft package reference graph is checked; no cook is performed.",
                           "Existing viewer bone motion is adapted; shared idle is not recovered original character animation.",
                           "Stock PC material approximations retain the source-conversion limitations."]}
        self.references, self.assets, self.loaded, self.native_checks = set(), {}, {}, {}
        self.index = {}
        self.baseline = None
        self.dynamics_policy = None
        self.dynamics_rows = {}
        self.material_policy = None
        self.material_report = None
        self.environment_policy = None
        self.environment_report = None

    def check(self, label, passed, details=None):
        row = {"check": label, "status": "pass" if passed else "fail"}
        if details is not None:
            row["details"] = details
        self.report["checks"].append(row)
        if not passed:
            self.report["errors"].append(row)
        return bool(passed)

    def unknown(self, label, reason):
        row = {"check": label, "status": "unknown", "reason": str(reason)}
        self.report["checks"].append(row)
        self.report["unknowns"].append(row)

    def disk(self, reference, hash_required=False):
        name = package(reference)
        if not name.startswith("/Game/"):
            return None
        if name not in self.assets:
            if not re.fullmatch(r"/Game/[A-Za-z0-9_/]+", name):
                raise ValueError("Invalid project asset package: " + name)
            stem = ROOT / "Content" / name[len("/Game/"):]
            path = next((stem.with_suffix(ext) for ext in (".uasset", ".umap")
                         if stem.with_suffix(ext).is_file()), stem.with_suffix(".uasset"))
            row = {"package": name, "file": path.relative_to(ROOT).as_posix(),
                   "exists": path.is_file(), "bytes": 0, "valid_package_magic": False}
            if path.is_file():
                row["bytes"] = path.stat().st_size
                with path.open("rb") as stream:
                    row["valid_package_magic"] = stream.read(4) == b"\xc1\x83\x2a\x9e"
            self.assets[name] = row
            self.check("saved_project_package", row["exists"] and row["bytes"] > 0
                       and row["valid_package_magic"], row)
        row = self.assets[name]
        if hash_required and row["exists"] and "sha256" not in row:
            row["sha256"] = digest(ROOT / row["file"])
        return row

    def indexed_asset(self, reference, label):
        name = package(reference)
        indexed = self.index.get(name)
        if not self.check(label + ".in_conversion_index", indexed is not None, name):
            return
        saved = self.disk(reference, True)
        self.check(label + ".conversion_fingerprint_unchanged", saved is not None and
                   saved.get("sha256") == indexed.get("sha256") and
                   saved.get("bytes") == indexed.get("bytes") and
                   saved.get("file") == indexed.get("file"),
                   {"actual": saved, "indexed_sha256": indexed.get("sha256")})

    def load(self, reference, expected_type, label, track_reference=True):
        if not self.check(label + ".serialized_reference", bool(reference), reference):
            return None
        reference = object_path(reference)
        if track_reference:
            self.references.add(reference)
        self.disk(reference)
        if reference not in self.loaded:
            self.loaded[reference] = self.ue.load_asset(reference)
        value = self.loaded[reference]
        valid = value is not None and isinstance(value, expected_type)
        self.check(label + ".exists_and_type", valid,
                   {"reference": reference, "expected": expected_type.__name__,
                    "actual": value.get_class().get_name() if value else None})
        if valid:
            self.check(label + ".exact_reference_target", value.get_path_name() == reference,
                       {"serialized": reference, "loaded": value.get_path_name()})
        return value if valid else None

    def snapshot(self, catalog):
        result, structs = [], {}
        for character in catalog.get_editor_property("characters"):
            fields = struct_fields(character)
            entry = {"id": str(character.get_editor_property("id")),
                     "name": str(character.get_editor_property("display_name")),
                     "portrait": extract_paths(fields.get("portrait", "")), "outfits": []}
            for outfit in character.get_editor_property("outfits"):
                serialized = struct_fields(outfit)
                row = {"id": str(outfit.get_editor_property("id")),
                       "name": str(outfit.get_editor_property("display_name")),
                       "environment": extract_paths(serialized.get("environment", "")),
                       "preview_notes": str(outfit.get_editor_property("preview_notes")), "parts": []}
                for part in outfit.get_editor_property("parts"):
                    pf = struct_fields(part)
                    record = {"id": str(part.get_editor_property("id")),
                              "name": str(part.get_editor_property("display_name")),
                              **{key: extract_paths(pf.get(key, ""))
                                 for key in ("mesh", "animation", "materials", "dynamics")},
                              "transform": pf.get("relativetransform"),
                              "visible": bool(part.get_editor_property("visible_by_default")),
                              "animation_loop": bool(part.get_editor_property("animation_loop")),
                              "parent_part_id": str(part.get_editor_property("parent_part_id")),
                              "attach_socket": str(part.get_editor_property("attach_socket"))}
                    row["parts"].append(record)
                    structs[entry["id"], row["id"], record["id"]] = part
                entry["outfits"].append(row)
            result.append(entry)
        return result, structs

    def baseline_preserved(self, baseline, actual):
        self.check("baseline_character_order_preserved", [c["id"] for c in actual[:len(baseline)]] ==
                   [c["id"] for c in baseline])
        current = {character["id"]: character for character in actual}
        for character in baseline:
            key = character["id"]
            if not self.check(key + ".baseline_character_retained", key in current):
                continue
            target = current[key]
            self.check(key + ".baseline_display_name_retained", target["name"] == character["name"])
            self.check(key + ".existing_portrait_reference_reused", target["portrait"] == character["portrait"],
                       {"actual": target["portrait"], "baseline": character["portrait"]})
            baseline_ids = [outfit["id"] for outfit in character["outfits"]]
            self.check(key + ".baseline_outfit_relative_order_preserved",
                       [outfit["id"] for outfit in target["outfits"] if outfit["id"] in baseline_ids] == baseline_ids)
            outfits = {outfit["id"]: outfit for outfit in target["outfits"]}
            for outfit in character["outfits"]:
                label = key + "/" + outfit["id"]
                if not self.check(label + ".baseline_outfit_retained", outfit["id"] in outfits):
                    continue
                saved = outfits[outfit["id"]]
                self.check(label + ".baseline_comparison_label", saved["name"] == outfit["name"] + "（旧版对照）",
                           {"actual": saved["name"], "expected": outfit["name"] + "（旧版对照）"})
                self.check(label + ".baseline_environment_retained", saved["environment"] == outfit["environment"],
                           {"actual": saved["environment"], "baseline": outfit["environment"]})
                self.check(label + ".baseline_part_order_retained", [p["id"] for p in saved["parts"]] ==
                           [p["id"] for p in outfit["parts"]])
                expected = [{key: part.get(key) for key in BASELINE_PART_FIELDS} for part in outfit["parts"]]
                observed = [{key: part.get(key) for key in BASELINE_PART_FIELDS} for part in saved["parts"]]
                self.check(label + ".baseline_part_references_and_settings_retained", expected == observed)
                self.check(label + ".baseline_parts_remain_unparented", all(
                    none_name(part["parent_part_id"]) and none_name(part["attach_socket"]) for part in saved["parts"]))

    def baseline_portraits(self, baseline, portraits):
        protected = {row["package"]: row for row in portraits.get("protected_assets", [])}
        for character in baseline:
            for reference in character["portrait"]:
                label = character["id"] + ".original_portrait_asset"
                self.load(reference, self.ue.Texture2D, label, track_reference=False)
                saved = self.disk(reference, True)
                record = protected.get(package(reference), {})
                self.check(label + ".unchanged_during_pc_portrait_replacement", saved is not None and
                           record.get("unchanged") is True and bool(record.get("sha256_before")) and
                           saved.get("sha256") == record.get("sha256_before") == record.get("sha256_after"),
                           {"reference": reference, "protection_record": record,
                            "current_sha256": saved.get("sha256") if saved else None})

    def portrait(self, character, record, settings):
        label = character["id"] + ".pc_portrait"
        target = "/Game/PCAssets/Portraits/T_PC_" + character["id"]
        self.check(label + ".catalog_uses_rendered_pc_target", character["portrait"] == [object_path(target)],
                   {"actual": character["portrait"], "expected": target})
        texture = self.load(character["portrait"][0] if len(character["portrait"]) == 1 else None,
                            self.ue.Texture2D, label)
        self.check(label + ".render_report_passed", record.get("status") == "passed" and record.get("target") == target)
        saved = self.disk(target, True)
        rendered = record.get("texture", {})
        self.check(label + ".saved_rendered_fingerprint", saved is not None and
                   all(saved.get(key) == rendered.get(key) for key in ("file", "bytes", "sha256")) and
                   rendered.get("package") == target, {"saved": saved, "rendered": rendered})
        if texture:
            dimensions = [int(texture.blueprint_get_size_x()), int(texture.blueprint_get_size_y())]
            self.check(label + ".rendered_dimensions", dimensions == rendered.get("dimensions") ==
                       [settings.get("width"), settings.get("height")], dimensions)
        png = record.get("png", {})
        source = (ROOT / png.get("file", "")).resolve()
        source_valid = source.is_relative_to(WORK) and source.is_file()
        self.check(label + ".rendered_png_evidence", source_valid and
                   source.stat().st_size == png.get("bytes") and digest(source) == png.get("sha256"), png)

    def part(self, character, outfit, expected, actual, part_struct, animation, all_parts):
        label = "/".join((character["id"], outfit["id"], expected["id"]))
        record = {"character_id": character["id"], "outfit_id": outfit["id"], "part_id": expected["id"],
                  "source_part_id": str(expected["source_part_id"]), "mesh": actual["mesh"],
                  "animation": actual["animation"], "parent_part_id": actual["parent_part_id"],
                  "attach_socket": actual["attach_socket"]}
        self.report["new_parts"].append(record)
        target = expected.get("mesh_target", expected.get("mesh"))
        self.check(label + ".planned_mesh_reference", actual["mesh"] == [object_path(target)],
                   {"actual": actual["mesh"], "expected": target})
        self.check(label + ".configured_ui_route", outfit.get("route") == "UIConfig" and
                   str(expected["source_part_id"]) in [str(value) for value in outfit.get("default_part_ids", [])],
                   {"source_package": expected.get("source_package"), "route": outfit.get("route"),
                    "explicit_default_part_ids": outfit.get("default_part_ids")})
        record["non_ui_named_mesh_reused_by_explicit_ui_configuration"] = bool(re.search(
            r"(?:^|[/_])(?:1P|CutScene|Master)(?=[/_.]|$)", expected.get("source_package", ""), re.I))
        self.check(label + ".visible_by_default", actual["visible"] == expected.get("visible_by_default", True))
        self.check(label + ".idle_animation_loops", actual["animation_loop"])
        self.check(label + ".configured_transform", transform_matches(
            transform_values(part_struct.get_editor_property("relative_transform")), expected["transform"]))
        mesh = self.load(actual["mesh"][0] if len(actual["mesh"]) == 1 else None,
                         self.ue.SkeletalMesh, label + ".mesh")
        if mesh is None:
            return
        profile, dynamics = self.dynamics_policy.resolve_dynamics(
            self.ue, outfit, expected, self.baseline, mesh)
        record["dynamics_compatibility"] = dynamics
        published_dynamics = self.dynamics_rows.get((character["id"], outfit["id"], expected["id"]), {})
        self.check(label + ".published_dynamics_policy_matches_fresh_inspection", all(
            published_dynamics.get(key) == dynamics.get(key) for key in
            ("status", "profile", "source_profile_asset", "baseline_mesh_asset", "preserved_original_settings_indices",
             "dropped_original_chains", "profile_asset")), published_dynamics.get("status"))
        expected_dynamics = [profile.get_path_name()] if profile else []
        self.check(label + ".exact_same_outfit_dynamics_binding", actual["dynamics"] == expected_dynamics,
                   {"actual": actual["dynamics"], "expected": expected_dynamics, "selection": dynamics.get("reason")})
        if profile:
            self.load(profile.get_path_name(), self.ue.ViewerDynamicsProfile, label + ".dynamics")
        self.indexed_asset(target, label + ".mesh")
        skeleton = mesh.get_editor_property("skeleton")
        skeleton_path = skeleton.get_path_name() if skeleton else None
        self.check(label + ".native_skeleton", isinstance(skeleton, self.ue.Skeleton) and
                   skeleton_path == object_path(expected["skeleton_target"]), skeleton_path)
        if skeleton:
            self.references.add(skeleton_path)
            self.indexed_asset(skeleton_path, label + ".skeleton")
        slots = list(mesh.get_editor_property("materials"))
        native = [slot.get_editor_property("material_interface") for slot in slots]
        paths = [material.get_path_name() if material else None for material in native]
        slot_names = [str(slot.get_editor_property("material_slot_name")) for slot in slots]
        planned_materials = [object_path(path) if path else None for path in expected["materials"]]
        record["native_materials"], record["native_slot_names"] = paths, slot_names
        self.check(label + ".native_material_bindings", bool(planned_materials) and paths == planned_materials,
                   {"actual": paths, "expected": planned_materials})
        self.check(label + ".native_material_slot_names", slot_names == expected["slot_names"], slot_names)
        effective_paths = self.material_policy.effective_material_paths(target, paths, self.material_report)
        effective_objects = self.material_policy.bound_materials(self.ue, mesh, self.material_report)
        effective = [object_path(path) if path else None for path in effective_paths]
        record["approved_effective_materials"] = effective
        self.check(label + ".effective_material_objects", [material.get_path_name() if material else None
                   for material in effective_objects] == effective)
        override_count = len(part_struct.get_editor_property("materials"))
        approved_override = package(target) in self.material_report["meshes"]
        self.check(label + ".material_overrides_approved_exact_slots", (override_count == len(effective) and
                   actual["materials"] == [path for path in effective if path]) if approved_override else
                   override_count == 0 and not actual["materials"],
                   {"override_count": override_count, "references": actual["materials"]})
        for index, path in enumerate(paths):
            if path:
                self.load(path, self.ue.MaterialInterface, label + f".material_{index}")
                self.indexed_asset(path, label + f".material_{index}")
        for index, path in enumerate(effective):
            if path and path != paths[index]:
                self.load(path, self.ue.MaterialInterface, label + f".approved_material_override_{index}")
        subsystem = self.ue.get_editor_subsystem(self.ue.SkeletalMeshEditorSubsystem)
        vertices, sections = int(subsystem.get_num_verts(mesh, 0)), int(subsystem.get_num_sections(mesh, 0))
        record["lod0_vertices"], record["lod0_sections"] = vertices, sections
        self.check(label + ".nonempty_lod0_geometry", vertices > 0 and sections > 0,
                   {"vertices": vertices, "sections": sections})
        bounds = mesh.get_bounds()
        extent = [float(getattr(bounds.box_extent, axis)) for axis in "xyz"]
        origin = [float(getattr(bounds.origin, axis)) for axis in "xyz"]
        self.check(label + ".finite_nonempty_bounds", all(math.isfinite(v) for v in extent + origin) and
                   all(v >= 0 for v in extent) and max(extent) > 0, {"origin": origin, "extent": extent})
        if not none_name(actual["parent_part_id"]):
            parent = all_parts.get(actual["parent_part_id"])
            parent_mesh = self.load(parent["mesh"][0] if parent and len(parent["mesh"]) == 1 else None,
                                    self.ue.SkeletalMesh, label + ".attachment_parent")
            self.check(label + ".attachment_parent_valid", parent is not None and
                       parent["id"] != actual["id"] and not none_name(actual["attach_socket"]))
            if parent_mesh:
                component = self.ue.new_object(self.ue.SkeletalMeshComponent)
                component.set_skeletal_mesh_asset(parent_mesh)
                self.check(label + ".attachment_socket_exists", component.does_socket_exist(actual["attach_socket"]),
                           actual["attach_socket"])
        if str(expected["source_part_id"]) in SOCKET_ACCESSORIES:
            self.check(label + ".local_accessory_parented", not none_name(actual["parent_part_id"]) and
                       actual["attach_socket"] == expected["source_attachment"]["socket_name"])
        else:
            self.check(label + ".shared_origin_not_double_attached", none_name(actual["parent_part_id"]) and
                       none_name(actual["attach_socket"]))
        if animation is None:
            self.check(label + ".animation_report_entry", False)
            return
        self.check(label + ".animation_report_mesh", package(animation.get("mesh")) == package(target))
        status = animation.get("status")
        record["animation_status"], record["animation_source_kind"] = status, animation.get("source_kind")
        self.check(label + ".animation_status_supported", status in
                   ("adapted", "reused", "attached_reference_pose", "reference_pose_incompatible"), status)
        if status in ("attached_reference_pose", "reference_pose_incompatible"):
            self.check(label + ".declared_reference_pose_has_no_clip", not actual["animation"] and
                       animation.get("animation") is None)
            self.check(label + ".reference_pose_reason_recorded", bool(animation.get("reason")))
            if status == "attached_reference_pose":
                self.check(label + ".reference_pose_is_local_accessory", str(expected["source_part_id"]) in SOCKET_ACCESSORIES)
            else:
                inspected = json.loads(self.ue.PCViewerAnimationLibrary.inspect_animation_compatibility(
                    animation.get("source_animation"), package(target)))
                record["fresh_incompatible_animation_inspection"] = inspected
                self.check(label + ".reference_pose_incompatibility_confirmed",
                           inspected.get("status") == "not_compatible" and
                           "Insufficient matching animated/core bones" in inspected.get("error", ""), inspected)
            return
        expected_animation = animation.get("animation")
        self.check(label + ".planned_animation_reference", actual["animation"] ==
                   ([object_path(expected_animation)] if expected_animation else []), expected_animation)
        clip = self.load(actual["animation"][0] if len(actual["animation"]) == 1 else None,
                         self.ue.AnimSequence, label + ".animation")
        if clip:
            clip_skeleton = clip.get_editor_property("skeleton")
            self.check(label + ".animation_exact_native_skeleton", skeleton is not None and clip_skeleton == skeleton,
                       {"mesh": skeleton_path, "animation": clip_skeleton.get_path_name() if clip_skeleton else None})
            duration = float(clip.get_play_length())
            self.check(label + ".animation_duration", math.isfinite(duration) and duration > 0, duration)
            saved = self.disk(expected_animation, True)
            self.check(label + ".animation_saved_fingerprint", saved and saved.get("sha256") == animation.get("asset_sha256")
                       and saved.get("bytes") == animation.get("asset_bytes"))
            key = (animation.get("source_animation"), package(target), package(expected_animation))
            if key not in self.native_checks:
                self.native_checks[key] = json.loads(self.ue.PCViewerAnimationLibrary.verify_adapted_animation(*key))
            native_check = self.native_checks[key]
            record["fresh_animation_verification"] = native_check
            self.check(label + ".fresh_animation_verification", native_check.get("status") == "passed" and
                       native_check.get("target_skeleton") == skeleton_path and
                       native_check.get("source_kind") == animation.get("source_kind") and
                       native_check.get("runtime_compressed_data_valid") is True, native_check)

    def environments(self, characters, approved):
        references = sorted({path for character in characters for outfit in character["outfits"]
                             for path in outfit["environment"]})
        for reference in references:
            self.check("environment_is_existing_approved_stage", reference in approved, reference)
            environment = self.load(reference, self.ue.ViewerEnvironment, reference)
            if not environment:
                continue
            meshes = list(environment.get_editor_property("meshes"))
            row = {"environment": reference, "mesh_count": len(meshes),
                   "light_count": len(environment.get_editor_property("lights")), "meshes": []}
            self.report["environments"].append(row)
            self.check(reference + ".stage_geometry_retained", len(meshes) > 0)
            for index, mesh in enumerate(meshes):
                fields = struct_fields(mesh)
                paths = extract_paths(fields.get("mesh", ""))
                self.load(paths[0] if len(paths) == 1 else None, self.ue.StaticMesh,
                          reference + f".stage_mesh_{index}")
                row["meshes"].extend(paths)

    def source_environments(self):
        specification = importlib.util.spec_from_file_location(
            "pc_viewer_audit_environment_authoring", ROOT / "Scripts/prepare_pc_viewer_environments.py")
        authoring = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(authoring)
        rows = []
        for record in self.environment_report["environments"]:
            retired = self.environment_policy.retired_environment_record(record, ROOT)
            if retired is not None:
                no_active_route = not any(package(route["environment"]) == record["target"]
                                          for route in self.environment_report["routes"])
                self.check(record["target"] + ".retired_candidate_has_no_active_route", no_active_route,
                           {"historical_asset": record["asset"], "retirement": retired})
                rows.append({"target": record["target"], "active": False,
                             "source_scene": record["source_scene"],
                             "source_package": record["source_package"],
                             "status": "historical_retired_editor_candidate",
                             "historical_asset": record["asset"], "retirement": retired})
                continue
            # Live inactive candidates are editor assets, not runtime Catalog dependencies.
            if record.get("sky", {}).get("target"):
                self.ue.load_asset(record["sky"]["target"])
            asset = self.ue.load_asset(record["target"])
            valid = isinstance(asset, self.ue.ViewerEnvironment) and asset.get_path_name() == object_path(record["target"])
            self.check(record["target"] + ".saved_source_environment_type", valid)
            if not valid:
                continue
            actual = authoring.snapshot(asset)
            matches = authoring.snapshots_equal(actual, record["snapshot"])
            self.check(record["target"] + ".source_parameter_snapshot_retained", matches)
            self.check(record["target"] + ".source_mode_enabled", actual.get("use_source_rendering") is True and
                       actual.get("has_mobile_post_process") is True and actual.get("character_lighting_channel_mask") == 1)
            self.check(record["target"] + ".retained_geometry_nonempty", bool(actual.get("meshes")))
            self.check(record["target"] + ".source_lights_nonempty", bool(actual.get("lights")))
            rows.append({"target": record["target"], "active": record["active"], "source_scene": record["source_scene"],
                         "source_package": record["source_package"], "geometry_source": record["geometry_source"],
                         "lights": len(actual.get("lights", [])), "meshes": len(actual.get("meshes", [])),
                         "source_snapshot_verified": matches})
        self.report["pc_source_environments"] = rows
        self.report["pc_environment_route_limitations"] = self.environment_report["routes"]

    def closure(self):
        registry = self.ue.AssetRegistryHelpers.get_asset_registry()
        registry.scan_paths_synchronous(["/Game"], True)
        options = self.ue.AssetRegistryDependencyOptions()
        for field in ("include_soft_package_references", "include_hard_package_references"):
            options.set_editor_property(field, True)
        for field in ("include_soft_management_references", "include_hard_management_references"):
            options.set_editor_property(field, False)
        queue, seen, missing, unknown = [package(CATALOG)], set(), [], []
        while queue:
            name = queue.pop()
            if name in seen or not name.startswith("/Game/"):
                continue
            seen.add(name)
            if len(seen) > 15000:
                unknown.append("Dependency traversal exceeded 15,000 project packages")
                break
            self.disk(name)
            assets = list(registry.get_assets_by_package_name(name, True))
            if not assets:
                missing.append(name)
                continue
            dependencies = registry.get_dependencies(name, options)
            classes = [str(asset.asset_class_path.asset_name) for asset in assets]
            if dependencies is None and any(name not in ("Texture2D", "TextureCube") for name in classes):
                unknown.append({"package": name, "classes": classes})
            queue.extend(str(value) for value in (dependencies or []))
        required = {package(path) for path in self.references if path.startswith("/Game/")}
        untracked = sorted(required - seen)
        self.check("catalog_saved_dependency_closure_complete", not missing and not unknown and not untracked,
                   {"missing_packages": sorted(missing), "unknown_dependencies": unknown,
                    "explicit_references_not_in_catalog_graph": untracked})
        config = (ROOT / "Config/DefaultGame.ini").read_text(encoding="utf-8-sig")
        always = re.findall(r'DirectoriesToAlwaysCook=\(Path="([^"]+)"\)', config)
        never = re.findall(r'DirectoriesToNeverCook=\(Path="([^"]+)"\)', config)
        under = lambda name, directory: name == directory.rstrip("/") or name.startswith(directory.rstrip("/") + "/")
        self.check("catalog_covered_by_packaging_directory", any(under(package(CATALOG), value) for value in always), always)
        excluded = sorted(name for name in seen if any(under(name, value) for value in never))
        self.check("runtime_dependency_graph_not_excluded_from_cook", not excluded, excluded)
        self.report["dependency_graph"] = {"root": package(CATALOG), "project_package_count": len(seen),
                                           "packages": sorted(seen), "required_explicit_packages": sorted(required),
                                           "missing_packages": sorted(missing), "unknown_dependencies": unknown,
                                           "unreachable_explicit_packages": untracked,
                                           "always_cook_directories": always, "never_cook_directories": never,
                                           "scope": "Saved hard and soft package dependencies; no cook performed."}

    def run(self):
        plan, baseline, animations, portraits, dynamics_document, index = (read_json(path) for path in
                                             (PLAN_PATH, BASELINE_PATH, ANIMATION_PATH, PORTRAIT_PATH, DYNAMICS_PATH, INDEX_PATH))
        self.baseline = baseline
        dynamics_specification = importlib.util.spec_from_file_location(
            "pc_viewer_audit_dynamics_policy", ROOT / "Scripts/pc_viewer_dynamics.py")
        self.dynamics_policy = importlib.util.module_from_spec(dynamics_specification)
        dynamics_specification.loader.exec_module(self.dynamics_policy)
        material_specification = importlib.util.spec_from_file_location(
            "pc_viewer_audit_material_policy", ROOT / "Scripts/pc_viewer_materials.py")
        self.material_policy = importlib.util.module_from_spec(material_specification)
        material_specification.loader.exec_module(self.material_policy)
        self.material_report = self.material_policy.load_report(ROOT)
        environment_specification = importlib.util.spec_from_file_location(
            "pc_viewer_audit_environment_policy", ROOT / "Scripts/pc_viewer_environments.py")
        self.environment_policy = importlib.util.module_from_spec(environment_specification)
        environment_specification.loader.exec_module(self.environment_policy)
        self.environment_report = self.environment_policy.load_report(ROOT)
        self.index = {package(row["target"]): row for row in index["assets"]}
        self.report["inputs"] = {"plan_sha256": digest(PLAN_PATH), "baseline_catalog_sha256": digest(BASELINE_PATH),
                                 "animation_report_sha256": digest(ANIMATION_PATH), "asset_index_sha256": digest(INDEX_PATH),
                                 "portraits_report_sha256": digest(PORTRAIT_PATH), "dynamics_report_sha256": digest(DYNAMICS_PATH),
                                 "material_overrides_report_sha256": digest(MATERIAL_PATH),
                                 "viewer_environment_plan_sha256": digest(ENVIRONMENT_PLAN_PATH),
                                 "viewer_environment_report_sha256": digest(ENVIRONMENT_REPORT_PATH)}
        self.check("dynamics_report_complete_and_current", dynamics_document.get("status") == "passed" and
                   dynamics_document.get("policy_version") == self.dynamics_policy.VERSION and
                   dynamics_document.get("processed_parts") == plan["counts"]["parts"] and
                   dynamics_document.get("inputs") == {key: self.report["inputs"][key]
                       for key in ("plan_sha256", "baseline_catalog_sha256")})
        self.dynamics_rows = {(row["character_id"], row["outfit_id"], row["part_id"]): row
                              for row in dynamics_document.get("parts", [])}
        self.check("dynamics_report_unique_part_identities", len(self.dynamics_rows) == len(dynamics_document.get("parts", [])))
        self.check("plan_asset_index_matches", plan.get("asset_index_sha256") == digest(INDEX_PATH))
        self.check("animation_report_current_plan", animations.get("plan_sha256") == digest(PLAN_PATH) and
                   animations.get("baseline_catalog_sha256") == digest(BASELINE_PATH))
        self.check("animation_report_complete", animations.get("status") == "passed" and
                   animations.get("processed_parts") == plan["counts"]["parts"] and
                   animations.get("total_parts") == plan["counts"]["parts"])
        portrait_inputs = portraits.get("inputs", {})
        self.check("portrait_report_current_inputs", all(portrait_inputs.get(key) == self.report["inputs"][key]
                   for key in ("plan_sha256", "baseline_catalog_sha256", "animation_report_sha256", "asset_index_sha256",
                               "material_overrides_report_sha256")))
        portrait_rows = {row["character_id"]: row for row in portraits.get("portraits", [])}
        self.check("all_twelve_pc_portraits_completed", portraits.get("status") == "passed" and
                   portraits.get("completed_portraits") == 12 and len(portrait_rows) == len(portraits.get("portraits", [])) == 12
                   and set(portrait_rows) == {character["id"] for character in plan["characters"]})
        animation_rows = {(row["character_id"], row["outfit_id"], row["part_id"]): row for row in animations["parts"]}
        self.check("animation_report_unique_part_identities", len(animation_rows) == len(animations["parts"]))
        specification = importlib.util.spec_from_file_location(
            "pc_viewer_animation_source_policy", ROOT / "Scripts/attach_pc_viewer_animations.py")
        source_policy = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(source_policy)
        for job in source_policy.build_jobs(plan, baseline):
            key = job["character_id"], job["outfit_id"], job["part_id"]
            actual_job = animation_rows.get(key, {})
            fields = ("character_id", "family", "outfit_id", "model_id", "part_id", "source_part_id",
                      "mesh", "source_animation", "source_kind", "source_selection", "static_socket_attachment")
            self.check("/".join(key) + ".deterministic_animation_source_policy", all(
                actual_job.get(field) == job[field] for field in fields),
                {"expected": {field: job[field] for field in fields},
                 "actual": {field: actual_job.get(field) for field in fields}})
            if actual_job.get("status") in ("adapted", "reused"):
                self.check("/".join(key) + ".deterministic_animation_target",
                           actual_job.get("animation") == job["animation"])
        catalog = self.load(CATALOG, self.ue.ViewerCatalog, "catalog")
        if catalog is None:
            return
        actual, structs = self.snapshot(catalog)
        self.report["catalog_file_sha256"] = self.disk(CATALOG, True).get("sha256")
        self.report["characters"] = actual
        self.baseline_preserved(baseline["characters"], actual)
        self.baseline_portraits(baseline["characters"], portraits)
        expected_ids = {c["id"] for c in baseline["characters"]} | {c["id"] for c in plan["characters"]}
        self.check("character_ids_match_baseline_plus_plan", len(actual) == len(expected_ids) and
                   {c["id"] for c in actual} == expected_ids)
        by_character = {c["id"]: c for c in actual}
        baseline_keys = {(c["id"], o["id"]) for c in baseline["characters"] for o in c["outfits"]}
        planned_keys = {(c["id"], o["id"]) for c in plan["characters"] for o in c["outfits"]}
        actual_keys = [(c["id"], o["id"]) for c in actual for o in c["outfits"]]
        self.check("outfit_ids_match_baseline_plus_plan", len(set(actual_keys)) == len(actual_keys) and
                   set(actual_keys) == baseline_keys | planned_keys and not (baseline_keys & planned_keys))
        primary_outfits = []
        for character in actual:
            planned_ids = [outfit["id"] for planned in plan["characters"] if planned["id"] == character["id"]
                           for outfit in planned["outfits"]]
            actual_ids = [outfit["id"] for outfit in character["outfits"]]
            primary_outfits.append({"character_id": character["id"],
                                   "default_outfit": actual_ids[0] if actual_ids else None,
                                   "primary_pc_outfits": planned_ids})
            self.check(character["id"] + ".default_outfit_uses_planned_pc_resources",
                       bool(planned_ids) and bool(actual_ids) and actual_ids[0] in planned_ids,
                       primary_outfits[-1])
            self.check(character["id"] + ".all_pc_outfits_before_old_comparisons",
                       actual_ids[:len(planned_ids)] == planned_ids,
                       {"actual": actual_ids, "planned_pc_prefix": planned_ids})
        self.report["primary_outfits"] = primary_outfits
        expected_part_keys = {(c["id"], o["id"], p["id"]) for c in plan["characters"]
                              for o in c["outfits"] for p in o["parts"]}
        self.check("animation_report_covers_exact_planned_parts", set(animation_rows) == expected_part_keys)
        self.check("dynamics_report_covers_exact_planned_parts", set(self.dynamics_rows) == expected_part_keys)
        self.report["counts"] = {"characters": len(actual), "baseline_outfits": len(baseline_keys),
                                 "new_outfits": len(planned_keys), "total_outfits": len(actual_keys),
                                 "new_parts": sum(len(o["parts"]) for c in actual for o in c["outfits"]
                                                  if (c["id"], o["id"]) in planned_keys),
                                 "pc_portraits": sum(character["portrait"] == [object_path(
                                     "/Game/PCAssets/Portraits/T_PC_" + character["id"])] for character in actual),
                                 "portraits": sum(len(character["portrait"]) == 1 for character in actual),
                                 "planned_new_parts": len(expected_part_keys)}
        self.check("expected_integration_counts", self.report["counts"] ==
                   {"characters": 12, "baseline_outfits": 9, "new_outfits": 17, "total_outfits": 26,
                    "new_parts": 72, "pc_portraits": 7, "portraits": 12, "planned_new_parts": 72}, self.report["counts"])
        self.report["prepared_pc_portraits"] = len(portrait_rows)
        self.report["characters_without_portrait"] = [character["id"] for character in actual
                                                      if not character["portrait"]]
        original_character_ids = {character["id"] for character in baseline["characters"]}
        for character in actual:
            if character["id"] in original_character_ids:
                for reference in character["portrait"]:
                    self.load(reference, self.ue.Texture2D, character["id"] + ".reused_existing_portrait")
            else:
                self.portrait(character, portrait_rows.get(character["id"], {}), portraits.get("settings", {}))
            for outfit in character["outfits"]:
                self.check(character["id"] + "/" + outfit["id"] + ".parts_nonempty", bool(outfit["parts"]))
                self.check(character["id"] + "/" + outfit["id"] + ".one_environment", len(outfit["environment"]) == 1)
                for part in outfit["parts"]:
                    for key in ("mesh", "animation", "materials", "dynamics"):
                        self.references.update(part[key])
        for character in plan["characters"]:
            saved = {o["id"]: o for o in by_character.get(character["id"], {}).get("outfits", [])}
            for outfit in character["outfits"]:
                if outfit["id"] not in saved:
                    continue
                current = saved[outfit["id"]]
                label = character["id"] + "/" + outfit["id"]
                expected_environment = self.environment_policy.environment_for_outfit(outfit["id"], self.environment_report)
                self.check(label + ".approved_source_environment_route", current["environment"] == [object_path(expected_environment)],
                           {"actual": current["environment"], "expected": expected_environment})
                self.check(label + ".planned_part_order", [p["id"] for p in current["parts"]] ==
                           [p["id"] for p in outfit["parts"]])
                all_parts = {p["id"]: p for p in current["parts"]}
                rows = [animation_rows.get((character["id"], outfit["id"], p["id"]), {}) for p in outfit["parts"]]
                notes = current["preview_notes"].casefold()
                if any(row.get("source_kind") == "shared_idle" for row in rows):
                    self.check(label + ".shared_idle_labeled", "共享" in notes or "shared" in notes, notes)
                if outfit.get("missing_default_parts"):
                    text = (current["name"] + " " + notes).casefold()
                    self.check(label + ".partial_recovery_labeled", any(word in text for word in ("部分", "恢复", "partial")), text)
                if any(row.get("status") == "reference_pose_incompatible" for row in rows):
                    self.check(label + ".static_pose_labeled", any(word in notes for word in
                               ("静态姿势", "参考姿势", "reference", "static")), notes)
                for expected in outfit["parts"]:
                    key = character["id"], outfit["id"], expected["id"]
                    if expected["id"] not in all_parts or key not in structs:
                        continue
                    try:
                        self.part(character, outfit, expected, all_parts[expected["id"]], structs[key],
                                  animation_rows.get(key), all_parts)
                    except Exception:
                        self.unknown("/".join(key), traceback.format_exc())
        self.report["dynamics_coverage"] = dict(Counter(row.get("dynamics_compatibility", {}).get("status", "unverified")
                                                      for row in self.report["new_parts"]))
        self.report["dynamics_source_limitations"] = [row["dynamics_compatibility"] for row in self.report["new_parts"]
            if row.get("dynamics_compatibility", {}).get("status") in
               ("derived_compatible_subset", "unavailable_native_chain_mismatch")]
        approved = {path for character in baseline["characters"] for outfit in character["outfits"]
                    for path in outfit["environment"]}
        approved.update(object_path(row["environment"]) for row in self.environment_report["routes"])
        self.source_environments()
        self.environments(actual, approved)
        self.closure()

    def write(self):
        self.report["assets"] = [self.assets[key] for key in sorted(self.assets)]
        self.report["status"] = "failed" if self.report["errors"] or self.report["unknowns"] else "passed"
        self.report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        self.report["check_counts"] = dict(Counter(row["status"] for row in self.report["checks"]))
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(json.dumps(self.report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.ue.log("PC_VIEWER_INTEGRATION_AUDIT " + json.dumps({
            "status": self.report["status"], "checks": self.report["check_counts"],
            "counts": self.report.get("counts"), "report": str(OUTPUT)}, ensure_ascii=False))


def main():
    import unreal
    audit = Audit(unreal)
    try:
        audit.run()
    except Exception:
        audit.unknown("audit_execution", traceback.format_exc())
    finally:
        audit.write()
    if audit.report["status"] != "passed":
        raise RuntimeError("PC viewer integration audit failed; see " + str(OUTPUT))


if __name__ == "__main__":
    main()
