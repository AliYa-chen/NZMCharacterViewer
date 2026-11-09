#!/usr/bin/env python3
"""Build an evidence-only PC postprocess handoff; never imports into Unreal.

Run from any platform with Python 3.9+: python3 Scripts/build_pc_postprocess_profiles.py
Use --check to verify the checked-in output without writing it. Values and
override flags remain source evidence, not proof of a final rendered frame.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
BRANCH_NAMES = ("DesktopBloomSettings", "MobileBloomSettings")
STATES = {
    "value_and_explicit_override": "A value and bOverride flag=true are serialized in this branch; effective volume blending and runtime overrides remain unverified.",
    "override_without_serialized_value": "bOverride flag=true is serialized, but no paired value is serialized; the inherited/native value is unknown.",
    "value_without_explicit_override": "A value is serialized, but no matching bOverride flag is serialized; the omitted flag is unknown rather than assumed enabled.",
    "explicit_disabled_override": "bOverride flag=false is explicitly serialized; a serialized value, including false or zero, is retained without treating it as an enabled override.",
}
VOLUME_FIELDS = ("Priority", "bEnabled", "bUnbound", "BlendWeight", "BlendRadius")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inside(root, relative):
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError(f"Expected a relative forward-slash source path: {relative}")
    path = (root / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f"Missing or outside source root: {relative}")
    return path


def serialized_field(properties, field):
    result = {"serialized": field in properties,
              "status": "serialized_value" if field in properties else "unknown_not_serialized"}
    if field in properties:
        result["value"] = properties[field]
    return result


def branch_fields(properties):
    """Pair literal property names, plus an explicitly evidenced bool b-prefix.

    UE bool values may be serialized as bIsUI while their override is
    bOverride_IsUI. Such a pair is used only when exactly the b-prefixed value
    exists and the unprefixed value does not; the original keys remain intact.
    No missing value or flag is filled with a native default.
    """
    if not isinstance(properties, dict):
        raise ValueError("Postprocess branch must be a JSON object")
    override_prefix = "bOverride_"
    overrides = {key[len(override_prefix):]: key for key in properties if key.startswith(override_prefix)}
    for key in overrides.values():
        if not isinstance(properties[key], bool):
            raise ValueError(f"Override flag is not a JSON boolean: {key}")
    paired_values, consumed = {}, set()
    for field in overrides:
        if field in properties:
            paired_values[field] = (field, "literal_property_name")
            consumed.add(field)
        elif "b" + field in properties and isinstance(properties["b" + field], bool):
            paired_values[field] = ("b" + field, "boolean_b_prefix_pair")
            consumed.add("b" + field)
    fields = set(overrides)
    fields.update(key for key in properties if not key.startswith(override_prefix) and key not in consumed)
    results = []
    for field in sorted(fields):
        value_key, pairing = paired_values.get(field, (field, "literal_property_name"))
        has_value = value_key in properties
        override_key = overrides.get(field)
        has_override = override_key is not None
        flag = properties[override_key] if has_override else None
        if has_override and flag is False:
            state = "explicit_disabled_override"
        elif has_override and has_value:
            state = "value_and_explicit_override"
        elif has_override:
            state = "override_without_serialized_value"
        else:
            state = "value_without_explicit_override"
        result = {"field": field, "evidence_state": state,
                  "serialized_value_present": has_value,
                  "serialized_override_present": has_override,
                  "value_pairing": pairing}
        if has_value:
            result.update(serialized_value_key=value_key, value=properties[value_key])
        if has_override:
            result.update(serialized_override_key=override_key, override_value=flag)
        results.append(result)
    return results


def native_mapping():
    """A handoff map, not an executable conversion or a reflected API claim."""
    mappings = []
    for source, target in (
        ("ColorSaturation", "color_saturation"), ("ColorContrast", "color_contrast"),
        ("ColorGamma", "color_gamma"), ("ColorGain", "color_gain"), ("ColorOffset", "color_offset"),
        ("ColorContrastMidtones", "color_contrast_midtones"),
        ("VignetteIntensity", "vignette_intensity"), ("LensFlareIntensity", "lens_flare_intensity"),
        ("DepthOfFieldScale", "depth_of_field_scale"),
        ("DepthOfFieldFocalRegion", "depth_of_field_focal_region"),
        ("DepthOfFieldNearDistance", "depth_of_field_near_distance"),
        ("DepthOfFieldNearTransitionRegion", "depth_of_field_near_transition_region"),
        ("DepthOfFieldFarTransitionRegion", "depth_of_field_far_transition_region"),
    ):
        mappings.append({"source_branch": "mainSettings", "source_field": source,
                         "candidate_ue_python_property": target,
                         "status": "native_candidate_requires_target_reflection_and_source_override_checks"})
    bloom_fields = [("BloomIntensity", "bloom_intensity"), ("BloomThreshold", "bloom_threshold"),
                    ("BloomSizeScale", "bloom_size_scale")]
    bloom_fields += [(f"Bloom{n}{kind}", f"bloom{n}_{kind.lower()}")
                     for n in range(1, 7) for kind in ("Size", "Tint")]
    for branch in BRANCH_NAMES:
        for source, target in bloom_fields:
            mappings.append({"source_branch": branch, "source_field": source,
                             "candidate_ue_python_property": target,
                             "status": "fork_branch_requires_explicit_platform_selection_and_response_validation"})
    for field in ("AutoExposureMinBrightness", "AutoExposureMaxBrightness"):
        mappings.append({"source_branch": "mainSettings", "source_field": field,
                         "candidate_ue_python_property": None,
                         "status": "source_exposure_semantics_require_conversion_before_UE5_EV100_or_manual_exposure",
                         "note": "Source min/max zero must not be treated as a verified UE5 EV0 conversion."})
    for field in ("IsUI", "UIRenderResolution", "MobileHQGaussian"):
        mappings.append({"source_branch": "mainSettings", "source_field": field,
                         "candidate_ue_python_property": None,
                         "status": "source_fork_or_platform_behavior_not_implemented_by_this_tool"})
    return mappings


def verify_known_evidence(volumes):
    """Guard against the misleading active-override conclusions seen in this source."""
    standard = [row for row in volumes
                if row["map_package"] == "/Game/Maps/Lobby/Role/Lobby_Scene_Light_Role"
                and row["name"] == "PostProcessVolume_Role"]
    if len(standard) != 1:
        raise ValueError("Expected one standard character postprocess source for evidence guards")
    branches = standard[0]["branches"]
    by_field = {branch: {row["field"]: row for row in values} for branch, values in branches.items()}
    expected = (
        ("DesktopBloomSettings", "BloomIntensity", "value_and_explicit_override", 2.0),
        ("MobileBloomSettings", "BloomIntensity", "value_without_explicit_override", 2.631746),
        ("mainSettings", "DepthOfFieldScale", "override_without_serialized_value", None),
        ("mainSettings", "LensFlareIntensity", "value_without_explicit_override", 3.7),
        ("mainSettings", "IsUI", "value_and_explicit_override", True),
    )
    for branch, field, state, value in expected:
        row = by_field[branch].get(field)
        if row is None or row["evidence_state"] != state or row.get("value") != value:
            raise ValueError(f"Known source evidence changed; review {branch}.{field}")


def build(project_root):
    environment = project_root / "PCResources/Environment"
    summary_path = environment / "scene_parameters.json"
    summary = read_json(summary_path)
    volumes, seen = [], set()
    for map_row in summary["maps"]:
        for volume_summary in map_row.get("postprocess_volumes", []):
            relative = volume_summary["source_json"]
            if relative in seen:
                raise ValueError(f"Duplicate postprocess source: {relative}")
            seen.add(relative)
            path = inside(environment, relative)
            raw = read_json(path)
            if raw.get("Type") != "PostProcessVolume":
                raise ValueError(f"Unexpected exported type: {relative}")
            properties = raw.get("Properties", {})
            settings = properties.get("Settings")
            if not isinstance(settings, dict):
                raise ValueError(f"Missing serialized Settings object: {relative}")
            main = {key: value for key, value in settings.items() if key not in BRANCH_NAMES}
            branches = {"mainSettings": branch_fields(main)}
            for branch in BRANCH_NAMES:
                if branch in settings:
                    branches[branch] = branch_fields(settings[branch])
            volumes.append({
                "map_package": map_row["virtual_package"], "name": raw["Name"],
                "source_json": path.relative_to(project_root).as_posix(),
                "source_sha256": sha256(path),
                "template_reference": raw.get("Template", volume_summary.get("template_reference")),
                "template_inheritance_status": volume_summary.get("template_inheritance_status", "unknown"),
                "volume_properties": {field: serialized_field(properties, field) for field in VOLUME_FIELDS},
                "settings": settings,
                "branches": branches,
                "final_frame_effective": "not_established",
            })
    volumes.sort(key=lambda row: (row["map_package"], row["name"]))
    if len(volumes) != 7:
        raise ValueError(f"Expected the audited seven postprocess volumes, got {len(volumes)}; review the source scope")
    verify_known_evidence(volumes)
    counts = Counter(row["evidence_state"] for volume in volumes
                     for branch in volume["branches"].values() for row in branch)
    return {
        "schema_version": 1,
        "status": "serialized_postprocess_evidence_only_apply_not_implemented",
        "source_scope": summary["source_scope"],
        "source_summary": {"file": summary_path.relative_to(project_root).as_posix(),
                           "sha256": sha256(summary_path)},
        "state_definitions": STATES,
        "value_pairing_policy": "Literal names are paired first; a unique bool bX value may pair with bOverride_X only when no X value is serialized. All original Settings keys are retained.",
        "limitations": [
            "Missing serialized values, override flags and volume properties remain unknown; no native or template defaults are filled.",
            "An explicit source override flag does not prove the winning per-frame value; template inheritance, volume blending and runtime changes require separate verification.",
            "DesktopBloomSettings and MobileBloomSettings are separate source branches, not simultaneous stock UE settings.",
            "Candidate mappings do not implement an import or conversion. Target UE5.8 reflection, exposure semantics, platform support and visible response must be checked before applying them.",
            "Original fork exposure min/max zero does not independently establish a UE5 EV0/manual exposure equivalent.",
        ],
        "native_candidate_mapping": native_mapping(),
        "summary": {"volume_count": len(volumes), "field_evidence_count": sum(counts.values()),
                    "state_counts": {state: counts[state] for state in STATES},
                    "known_evidence_guards": "passed"},
        "volumes": volumes,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--check", action="store_true", help="Compare deterministic output without writing")
    args = parser.parse_args()
    root = args.project_root.resolve()
    output = root / "PCResources/Lighting/postprocess_profiles.json"
    report = build(root)
    serialized = (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if args.check:
        if not output.is_file() or output.read_bytes() != serialized:
            raise ValueError("Postprocess profiles are absent or stale; run the generator and review the diff")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(serialized)
    print(json.dumps({"status": "checked" if args.check else "generated",
                      "output": output.relative_to(root).as_posix(), **report["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (KeyError, OSError, TypeError, ValueError) as error:
        print(f"Postprocess profile error: {error}", file=sys.stderr)
        raise SystemExit(1)
