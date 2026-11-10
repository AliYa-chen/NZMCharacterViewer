"""Reuse an exact original outfit part's dynamics on a compatible PC skeleton.

The resolver is read-only. It preserves an existing ViewerDynamicsProfile or
loads a verified compatible subset authored by prepare_pc_dynamics.py, and
mirrors ViewerDynamics.cpp::RebuildChains, including traversal order, excluded
subtrees, claimed particles and the clamped MaxBones limit. It does not infer
profiles for new characters, other parts, or shared-idle animation sources.
"""
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import traceback


ROOT = Path(__file__).resolve().parent.parent
VERSION = 2
REPORT_PATH = ROOT / "Saved/PCViewerIntegration/dynamics_report.json"
GLOBAL_PROFILE_FIELDS = ("max_delta_seconds", "max_substeps", "constraint_iterations",
                         "teleport_distance_cm", "teleport_rotation_degrees", "source_asset_path")
_specification = importlib.util.spec_from_file_location(
    "pc_viewer_dynamics_source_policy", ROOT / "Scripts/attach_pc_viewer_animations.py")
_source_policy = importlib.util.module_from_spec(_specification)
_specification.loader.exec_module(_source_policy)
MODEL_TO_BASELINE = _source_policy.MODEL_TO_BASELINE
NAIMEI_SEMANTIC_PARTS = _source_policy.NAIMEI_SEMANTIC_PARTS


class DynamicsCompatibilityError(ValueError):
    def __init__(self, message, report):
        report.update(status="failed", error=message)
        self.report = report
        super().__init__(message + ": " + json.dumps(report, ensure_ascii=False, sort_keys=True))


def package(reference):
    return str(reference).split(".", 1)[0]


def object_path(reference):
    value = str(reference)
    return value if "." in value else value + "." + value.rsplit("/", 1)[-1]


def fingerprint(reference):
    name = package(reference)
    if not name.startswith("/Game/"):
        raise ValueError("Expected project asset: " + name)
    path = (ROOT / "Content" / (name[len("/Game/"):] + ".uasset")).resolve()
    if not path.is_relative_to(ROOT / "Content") or not path.is_file() or path.stat().st_size <= 0:
        raise ValueError("Missing saved dynamics input asset: " + name)
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return {"package": name, "file": path.relative_to(ROOT).as_posix(),
            "bytes": path.stat().st_size, "sha256": hasher.hexdigest()}


def baseline_part(source_outfit, source_part, baseline):
    """Select by exact model and part identity; never select body/family fallback."""
    identity = MODEL_TO_BASELINE.get(str(source_outfit["model_id"]))
    if identity is None:
        return None, None
    outfits = [outfit for character in baseline["characters"] if character["id"] == identity[0]
               for outfit in character["outfits"] if outfit["id"] == identity[1]]
    if len(outfits) != 1:
        raise ValueError("Exact dynamics baseline outfit is missing or ambiguous: " + repr(identity))
    original_id = str(source_part["source_part_id"])
    if identity == ("naimei", "suit_a"):
        original_id = NAIMEI_SEMANTIC_PARTS.get(source_part.get("slot"), original_id)
    parts = [part for part in outfits[0]["parts"] if str(part["id"]) == original_id]
    if len(parts) > 1:
        raise ValueError("Exact dynamics baseline part is ambiguous: " + original_id)
    return identity, parts[0] if parts else None


def mesh_hierarchy(ue, mesh):
    component = ue.new_object(ue.SkeletalMeshComponent)
    component.set_skeletal_mesh_asset(mesh)
    names = [str(component.get_bone_name(index)) for index in range(component.get_num_bones())]
    normalized = [name.casefold() for name in names]
    if not names or len(set(normalized)) != len(names):
        raise ValueError("Mesh reference skeleton is empty or has ambiguous bone names: " + mesh.get_path_name())
    indices = {name: index for index, name in enumerate(normalized)}
    parents = []
    for index, name in enumerate(names):
        if int(component.get_bone_index(name)) != index:
            raise ValueError("Component bone name/index lookup is inconsistent: " + name)
        parent_name = str(component.get_parent_bone(name))
        root = parent_name in ("", "None", "NAME_None")
        parent = -1 if root else int(component.get_bone_index(parent_name))
        if not root and parent < 0:
            raise ValueError("Reference skeleton parent name is missing: " + name + " -> " + parent_name)
        if parent != -1 and (parent < 0 or parent >= index):
            raise ValueError("Reference skeleton is not parent-before-child: " + name)
        parents.append(parent)
    return {"names": names, "normalized_names": normalized, "parents": parents, "indices": indices}


def profile_settings(profile):
    return [{"settings_index": index, "root_bone": str(chain.get_editor_property("root_bone")),
             "exclude_bones": [str(name) for name in chain.get_editor_property("exclude_bones")],
             "max_bones": int(chain.get_editor_property("max_bones"))}
            for index, chain in enumerate(profile.get_editor_property("chains"))]


def rebuild_chains(hierarchy, settings):
    """Mirror runtime particle selection; retain order because first child aims a joint."""
    names, parents, indices = hierarchy["normalized_names"], hierarchy["parents"], hierarchy["indices"]
    claimed, active, details = set(), [], []
    for setting in settings:
        root = indices.get(setting["root_bone"].casefold(), -1)
        row = {**setting, "applied_max_bones": max(2, min(128, setting["max_bones"])), "particles": []}
        details.append(row)
        if root == -1 or root in claimed:
            row["status"] = "root_missing" if root == -1 else "root_already_claimed"
            continue
        selected, parent_particles, particles = [root], [-1], {root: 0}
        excluded = {name.casefold() for name in setting["exclude_bones"]}
        for bone in range(root + 1, len(names)):
            if len(selected) >= row["applied_max_bones"]:
                break
            parent_particle = particles.get(parents[bone])
            if parent_particle is None or bone in claimed or names[bone] in excluded:
                continue
            particles[bone] = len(selected)
            selected.append(bone)
            parent_particles.append(parent_particle)
        row["particles"] = [{"bone": names[bone],
                             "parent_particle": None if parent_particles[index] == -1
                             else names[selected[parent_particles[index]]]}
                            for index, bone in enumerate(selected)]
        if len(selected) < 2:
            row["status"] = "no_eligible_child"
            continue
        row["status"] = "active"
        claimed.update(selected)
        active.append({"settings_index": setting["settings_index"], "root_bone": names[root],
                       "particles": row["particles"]})
    return active, details


def compare_chains(baseline_hierarchy, target_hierarchy, settings, report):
    source, source_details = rebuild_chains(baseline_hierarchy, settings)
    target, target_details = rebuild_chains(target_hierarchy, settings)
    report.update(baseline_chains=source_details, target_chains=target_details)
    if not source:
        raise DynamicsCompatibilityError("Original profile has no eligible active chain on its original mesh", report)
    if source != target:
        raise DynamicsCompatibilityError("PC mesh selected particles/order/parent relationships differ from the original runtime chains", report)
    active_bones = {particle["bone"] for chain in source for particle in chain["particles"]}
    roots = {chain["root_bone"] for chain in source}
    differences = []
    for name in sorted(set(baseline_hierarchy["indices"]) & set(target_hierarchy["indices"])):
        def parent_name(hierarchy):
            parent = hierarchy["parents"][hierarchy["indices"][name]]
            return None if parent == -1 else hierarchy["normalized_names"][parent]
        old_parent, new_parent = parent_name(baseline_hierarchy), parent_name(target_hierarchy)
        if old_parent == new_parent:
            continue
        if name in active_bones and (name not in roots or old_parent in active_bones or new_parent in active_bones):
            report["parent_mismatch"] = {"bone": name, "baseline_parent": old_parent, "target_parent": new_parent}
            raise DynamicsCompatibilityError("Simulated bone parent differs within the active chain graph", report)
        differences.append({"bone": name, "baseline_parent": old_parent, "target_parent": new_parent,
                            "reason": ("Pinned chain root is seeded from evaluated component-space animation; both parents are outside the active solver graph. The target keeps its native hierarchy and separately verified adapted animation."
                                       if name in roots else
                                       "This shared bone is outside every selected dynamics particle. RebuildChains does not traverse this parent edge; the target keeps its own native hierarchy and separately verified animation. No source parent index is copied.")})
    report.update(active_chain_count=len(source), active_particle_count=sum(len(chain["particles"]) for chain in source),
                  allowed_non_chain_parent_differences=differences,
                  comparison="Exact ordered runtime particles and parent-particle edges for each active SettingsIndex; same unchanged profile supplies excludes, MaxBones and solver settings.")


def profile_values(profile):
    return {field: str(profile.get_editor_property(field)) for field in GLOBAL_PROFILE_FIELDS}


def derived_notes(profile, kept):
    return str(profile.get_editor_property("reconstruction_notes")) + (
        "\nPC native-skeleton compatible subset of this exact original part only. "
        "Original settings indices retained: " + ",".join(str(index) for index in kept) +
        ". Missing or changed chains are omitted; no roots, defaults or original physics are invented.")


def _parent_name(hierarchy, name):
    parent = hierarchy["parents"][hierarchy["indices"][name]]
    return None if parent == -1 else hierarchy["normalized_names"][parent]


def compatible_subset(source_hierarchy, target_hierarchy, settings):
    """Keep whole unchanged chains, then recheck effects of removing claimed particles."""
    source, source_details = rebuild_chains(source_hierarchy, settings)
    target, target_details = rebuild_chains(target_hierarchy, settings)
    source_by_index = {chain["settings_index"]: chain for chain in source}
    target_by_index = {chain["settings_index"]: chain for chain in target}
    kept = [index for index, chain in source_by_index.items() if chain == target_by_index.get(index)]
    rejected_after_subset = {}
    while kept:
        subset = [setting for setting in settings if setting["settings_index"] in kept]
        actual, _ = rebuild_chains(target_hierarchy, subset)
        actual_by_index = {chain["settings_index"]: chain for chain in actual}
        active_bones = {particle["bone"] for index in kept for particle in source_by_index[index]["particles"]}
        rejected = []
        for index in kept:
            expected = source_by_index[index]
            if actual_by_index.get(index) != expected:
                rejected_after_subset[index] = "Removing incompatible chains changes this chain's claimed-bone traversal; original ordered particles would not be preserved."
                rejected.append(index)
                continue
            root = expected["root_bone"]
            old_parent, new_parent = _parent_name(source_hierarchy, root), _parent_name(target_hierarchy, root)
            if old_parent != new_parent and (old_parent in active_bones or new_parent in active_bones):
                rejected_after_subset[index] = "Chain root parent changes within the surviving active solver graph."
                rejected.append(index)
        if not rejected:
            break
        kept = [index for index in kept if index not in rejected]
    dropped = [{"settings_index": index, "root_bone": chain["root_bone"],
                "baseline_particles": chain["particles"],
                "native_particles": target_by_index.get(index, {}).get("particles", []),
                "reason": rejected_after_subset.get(index, "Native skeleton does not select the original chain's exact ordered particles and parent-particle edges.")}
               for index, chain in source_by_index.items() if index not in kept]
    return {"source": source, "target": target, "baseline_chains": source_details, "target_chains": target_details,
            "kept": kept, "expected": [source_by_index[index] for index in kept], "dropped": dropped,
            "native_only_active_settings_indices": [index for index in target_by_index if index not in source_by_index]}


def derived_target(report):
    identity = {"policy_version": VERSION, "source_profile_sha256": report["source_profile_asset"]["sha256"],
                "baseline_mesh_sha256": report["baseline_mesh_asset"]["sha256"],
                "target_mesh_sha256": report["target_mesh_asset"]["sha256"],
                "preserved_original_settings_indices": report["preserved_original_settings_indices"]}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
    return "/Game/PCAssets/Dynamics/" + key[:2] + "/VD_PC_" + str(report["source_part_id"]) + "_" + key


def verify_derived_profile(profile, source_profile, context, report):
    kept = report["preserved_original_settings_indices"]
    original = list(source_profile.get_editor_property("chains"))
    actual = list(profile.get_editor_property("chains"))
    if not kept or [chain.export_text() for chain in actual] != [original[index].export_text() for index in kept]:
        raise DynamicsCompatibilityError("Derived profile chains are not an exact unchanged subset of the original structs", report)
    if profile_values(profile) != profile_values(source_profile):
        raise DynamicsCompatibilityError("Derived profile changed original global solver/safety settings", report)
    if str(profile.get_editor_property("reconstruction_notes")) != derived_notes(source_profile, kept):
        raise DynamicsCompatibilityError("Derived profile does not disclose its exact source-subset scope", report)
    settings = profile_settings(profile)
    for index, setting in enumerate(settings):
        setting["settings_index"] = kept[index]
    selected, details = rebuild_chains(context["target_hierarchy"], settings)
    if selected != context["analysis"]["expected"]:
        raise DynamicsCompatibilityError("Derived profile adds, removes or reorders runtime particles after subset traversal", report)
    return {"status": "passed", "preserved_original_settings_indices": kept,
            "profile_chain_struct_texts": [chain.export_text() for chain in actual],
            "global_solver_settings": profile_values(profile), "runtime_chains": details,
            "active_chain_count": len(selected), "active_particle_count": sum(len(chain["particles"]) for chain in selected)}


def inspect_dynamics(ue, source_outfit, source_part, baseline, target_mesh):
    """Read-only selection/classification for the resolver and separate authoring script."""
    target_path = target_mesh.get_path_name() if hasattr(target_mesh, "get_path_name") else str(target_mesh)
    report = {"outfit_id": source_outfit["id"], "model_id": str(source_outfit["model_id"]),
              "part_id": source_part["id"], "source_part_id": str(source_part["source_part_id"]),
              "target_mesh": target_path, "profile": None,
              "policy": "Exact same-outfit original part only; no body-motion, same-character or shared fallback.",
              "scope": "Existing bounded viewer solver/profile retained; original game physics or cloth is not reconstructed."}
    identity, original = baseline_part(source_outfit, source_part, baseline)
    if identity is None:
        report.update(status="not_applicable", reason="No exact original outfit baseline for this model; no dynamics inferred.")
        return None, report, None
    report["baseline_character_id"], report["baseline_outfit_id"] = identity
    if original is None:
        report.update(status="not_applicable", reason="This exact part was not present in the original outfit; no other part's profile is reused.")
        return None, report, None
    report["baseline_part_id"] = str(original["id"])
    profiles = original.get("dynamics", [])
    if not profiles:
        report.update(status="not_applicable", reason="The exact original part declares no dynamics profile.")
        return None, report, None
    if len(profiles) != 1 or len(original.get("mesh", [])) != 1:
        raise DynamicsCompatibilityError("Original part has ambiguous mesh/profile references", report)
    profile = ue.load_asset(profiles[0])
    old_mesh = ue.load_asset(original["mesh"][0])
    if not isinstance(profile, ue.ViewerDynamicsProfile) or not isinstance(old_mesh, ue.SkeletalMesh):
        raise DynamicsCompatibilityError("Original dynamics profile or mesh did not load with the required type", report)
    if profile.get_path_name() != object_path(profiles[0]) or old_mesh.get_path_name() != object_path(original["mesh"][0]):
        raise DynamicsCompatibilityError("Original profile/mesh resolved to a different asset target", report)
    if not isinstance(target_mesh, ue.SkeletalMesh):
        raise DynamicsCompatibilityError("Expected a loaded PC SkeletalMesh target", report)
    expected = source_part.get("mesh_target", source_part.get("mesh"))
    if target_mesh.get_path_name() != object_path(expected):
        raise DynamicsCompatibilityError("PC dynamics target is not the planned exact mesh", report)
    report.update(source_profile=profile.get_path_name(), baseline_mesh=old_mesh.get_path_name(),
                  source_profile_asset=fingerprint(profile.get_path_name()), baseline_mesh_asset=fingerprint(old_mesh.get_path_name()),
                  target_mesh_asset=fingerprint(target_path))
    settings = profile_settings(profile)
    source_hierarchy, target_hierarchy = mesh_hierarchy(ue, old_mesh), mesh_hierarchy(ue, target_mesh)
    analysis = compatible_subset(source_hierarchy, target_hierarchy, settings)
    if not analysis["source"]:
        raise DynamicsCompatibilityError("Original profile has no eligible active chain on its original mesh", report)
    report.update(baseline_chains=analysis["baseline_chains"], target_chains=analysis["target_chains"],
                  original_active_chain_count=len(analysis["source"]),
                  preserved_original_settings_indices=analysis["kept"], dropped_original_chains=analysis["dropped"],
                  native_only_active_settings_indices=analysis["native_only_active_settings_indices"],
                  preserved_active_chain_count=len(analysis["kept"]))
    context = {"source_hierarchy": source_hierarchy, "target_hierarchy": target_hierarchy,
               "settings": settings, "analysis": analysis}
    if analysis["source"] == analysis["target"] and len(analysis["kept"]) == len(analysis["source"]):
        compare_chains(source_hierarchy, target_hierarchy, settings, report)
        report.update(status="reused_exact_same_outfit_part_profile", profile=profile.get_path_name(),
                      profile_asset=report["source_profile_asset"])
    elif analysis["kept"]:
        report.update(status="requires_compatible_subset_derivation", reason="Only the explicitly listed unchanged original chains can run on this native PC skeleton.")
    else:
        report.update(status="unavailable_native_chain_mismatch", reason="No complete original active chain has unchanged native ordered particles and parent edges; no profile is inferred.")
    return profile, report, context


def resolve_dynamics(ue, source_outfit, source_part, baseline, target_mesh):
    """Return exact profile, verified saved subset, or a disclosed unavailable state."""
    source, report, context = inspect_dynamics(ue, source_outfit, source_part, baseline, target_mesh)
    if report["status"] in ("not_applicable", "unavailable_native_chain_mismatch"):
        return None, report
    if report["status"] == "reused_exact_same_outfit_part_profile":
        return source, report
    if not REPORT_PATH.is_file():
        raise DynamicsCompatibilityError("Run prepare_pc_dynamics.py before resolving a required compatible subset", report)
    document = json.loads(REPORT_PATH.read_text(encoding="utf-8-sig"))
    work = REPORT_PATH.parent
    expected_inputs = {"plan_sha256": hashlib.sha256((work / "viewer_plan.json").read_bytes()).hexdigest(),
                       "baseline_catalog_sha256": hashlib.sha256((work / "baseline_catalog.json").read_bytes()).hexdigest()}
    if document.get("status") != "passed" or document.get("policy_version") != VERSION or document.get("inputs") != expected_inputs:
        raise DynamicsCompatibilityError("Saved dynamics derivation report is incomplete or uses other current inputs", report)
    records = [row for row in document.get("parts", []) if row.get("outfit_id") == report["outfit_id"]
               and row.get("part_id") == report["part_id"]]
    target = derived_target(report)
    if len(records) != 1:
        raise DynamicsCompatibilityError("Saved dynamics derivation report has missing or ambiguous part identity", report)
    record = records[0]
    if record.get("status") != "derived_compatible_subset" or record.get("profile") != object_path(target) or any(
        record.get(key) != report.get(key) for key in
        ("source_profile_asset", "baseline_mesh_asset", "target_mesh_asset", "preserved_original_settings_indices", "dropped_original_chains")):
        raise DynamicsCompatibilityError("Saved dynamics derivation differs from fresh exact source/native-chain inspection", report)
    profile = ue.load_asset(target)
    if not isinstance(profile, ue.ViewerDynamicsProfile) or profile.get_path_name() != object_path(target):
        raise DynamicsCompatibilityError("Saved compatible subset profile is missing or resolves to another asset", report)
    current = fingerprint(target)
    if current != record.get("profile_asset"):
        raise DynamicsCompatibilityError("Saved compatible subset profile fingerprint differs from its authoring report", report)
    verification = verify_derived_profile(profile, source, context, report)
    report.update(status="derived_compatible_subset", profile=profile.get_path_name(), profile_asset=current,
                  verification=verification)
    return profile, report


def preflight(ue):
    """Audit all planned bindings in a fresh Editor; write Saved evidence only."""
    work = ROOT / "Saved/PCViewerIntegration"
    output = work / "dynamics_preflight.json"
    plan_path, baseline_path = work / "viewer_plan.json", work / "baseline_catalog.json"
    plan, baseline = (json.loads(path.read_text(encoding="utf-8-sig")) for path in (plan_path, baseline_path))
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    report = {"schema_version": 1, "status": "running", "content_saved": False, "parts": [], "errors": [],
              "inputs": {"plan_sha256": sha(plan_path), "baseline_catalog_sha256": sha(baseline_path),
                         "helper_sha256": sha(Path(__file__)),
                         "runtime_chains_source_sha256": sha(ROOT / "Source/NZMCharacterViewer/Private/ViewerDynamics.cpp")},
              "engine_version": ue.SystemLibrary.get_engine_version()}
    protected = {}
    catalog = fingerprint("/Game/Viewer/Data/DA_ViewerCatalog")
    protected[catalog["package"]] = catalog

    def persist():
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(".json.new")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(output)

    persist()
    for character in plan["characters"]:
        for outfit in character["outfits"]:
            for part in outfit["parts"]:
                try:
                    mesh = ue.load_asset(part.get("mesh_target", part.get("mesh")))
                    _, row = resolve_dynamics(ue, outfit, part, baseline, mesh)
                    row["target_mesh_asset"] = fingerprint(mesh.get_path_name())
                    for key in ("target_mesh_asset", "baseline_mesh_asset", "profile_asset", "source_profile_asset"):
                        if key in row:
                            protected[row[key]["package"]] = row[key]
                except Exception as error:
                    row = getattr(error, "report", {"outfit_id": outfit["id"], "part_id": part["id"]})
                    row.update(status="failed", traceback=traceback.format_exc())
                    report["errors"].append({"character_id": character["id"], **row})
                row["character_id"] = character["id"]
                report["parts"].append(row)
                if len(report["parts"]) % 8 == 0:
                    persist()
    report["protected_assets"] = [{"package": name, "sha256_before": before["sha256"],
                                    "sha256_after": fingerprint(name)["sha256"],
                                    "unchanged": fingerprint(name) == before}
                                   for name, before in sorted(protected.items())]
    report["processed_parts"] = len(report["parts"])
    report["reused_profiles"] = sum(row["status"] == "reused_exact_same_outfit_part_profile" for row in report["parts"])
    report["counts"] = dict(Counter(row["status"] for row in report["parts"]))
    report["status"] = "passed" if not report["errors"] and report["processed_parts"] == plan["counts"]["parts"] and all(
        row["unchanged"] for row in report["protected_assets"]) else "failed"
    persist()
    ue.log("PC_VIEWER_DYNAMICS_PREFLIGHT " + json.dumps({key: report[key] for key in
           ("status", "processed_parts", "reused_profiles", "content_saved")}))
    if report["status"] != "passed":
        raise RuntimeError("Dynamics compatibility preflight failed; inspect " + str(output))


if __name__ == "__main__":
    import unreal
    preflight(unreal)
