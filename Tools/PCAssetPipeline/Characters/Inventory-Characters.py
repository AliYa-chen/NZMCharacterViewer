"""Inventory existing offline NZM Pak-index JSON; never open a game or process.

SK_ leaf names are candidate hints, not a loaded SkeletalMesh class assertion.
Character directory names and outfit keywords are not gender evidence.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re


PREFIX = "NZM/Content/Avatar/Character/"
FAMILIES = {"angel", "angela", "molly", "naimei", "queen"}
KEYWORDS = re.compile(r"bunny|rabbit|maid|casino|兔|女仆|賭|赌", re.IGNORECASE)
ROLE_UI = re.compile(r"(?:^|_)ui(?:_|$)", re.IGNORECASE)
ROLE_3P = re.compile(r"(?:^|_)3p(?:_|$)", re.IGNORECASE)
ROLE_1P = re.compile(r"(?:^|_)1p(?:_|$)", re.IGNORECASE)
LOW = re.compile(r"(?:^|_)(?:low|lowpoly|lowres)(?:_|$)|(?:^|_)lod[1-9](?:_|$)|simplif|proxy", re.IGNORECASE)
PHYSICS = re.compile(r"(?:^|/|_)(?:phys|physics|phat)(?:_|/|$)|physicsproxy|physproxy|physicsasset|collision", re.IGNORECASE)
CUTSCENE = re.compile(r"cutscene|cutscence|cutscne", re.IGNORECASE)
PREVIEW = re.compile(r"preview|lobby|showcase", re.IGNORECASE)
TEXTURE = re.compile(r"^(?:T_|VT_|Texture)", re.IGNORECASE)


def canonical(path: str) -> str:
    path = path.replace("\\", "/")
    while path.startswith("../"):
        path = path[3:]
    return path


def mesh_roles(leaf: str, path: str) -> list[str]:
    result = []
    if ROLE_UI.search(leaf):
        result.append("UI")
    if ROLE_3P.search(leaf):
        result.append("3P")
    if ROLE_1P.search(leaf):
        result.append("1P")
    if CUTSCENE.search(leaf):
        result.append("CutScene")
    if PREVIEW.search(path):
        result.append("preview_or_lobby_name_hint")
    if not result:
        result.append("unspecified_view")
    if LOW.search(leaf):
        result.append("low_or_proxy_name_hint")
    if PHYSICS.search(path):
        result.append("physics_or_collision_name_hint")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--index-summary", type=Path)
    parser.add_argument("--mesh-comparison", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--female-config", type=Path, help="Optional offline ModeConfig.Gender evidence report.")
    args = parser.parse_args()
    if args.female_config:
        args.female_config = args.female_config.resolve()
    root = args.project_root.resolve()
    index_path = (args.index_summary or root / "Saved/PCAssetAnalysis/Indexing/index_summary.json").resolve()
    comparison_path = (args.mesh_comparison or root / "PCResources/mesh_comparison.json").resolve()
    output = (args.output or root / "Saved/PCAssetAnalysis/ExpandedCharacters/inventory.json").resolve()
    summary = json.loads(index_path.read_text(encoding="utf-8-sig"))
    comparison = json.loads(comparison_path.read_text(encoding="utf-8-sig"))
    previous = {
        canonical(part["pc_object_path"].rsplit(".", 1)[0]).lower()
        for part in comparison["parts"]
    }
    packages: dict[str, dict] = {}
    indexed_count = 0
    for archive in summary["archives"]:
        if archive["status"] != "verified":
            raise ValueError(f"Unverified archive index: {archive['archive']}")
        report_path = Path(archive["report"])
        if not report_path.exists():
            report_path = index_path.parent / report_path.name
        report = json.loads(report_path.read_text(encoding="utf-8-sig"))
        for entry in report["entries"]:
            indexed_count += 1
            path = canonical(entry["path"])
            if not path.lower().startswith(PREFIX.lower()):
                continue
            suffix = PurePosixPath(path).suffix.lower()
            if suffix not in {".uasset", ".uexp", ".ubulk"}:
                continue
            package = path[: -len(suffix)]
            relative = package[len(PREFIX) :]
            parts = relative.split("/")
            if len(parts) < 2:
                continue
            item = packages.setdefault(
                package.lower(),
                {
                    "virtual_package": package,
                    "source_relative_path": package.removeprefix("NZM/Content/"),
                    "requested_path": "/Game/" + package.removeprefix("NZM/Content/"),
                    "character_root": parts[0],
                    "outfit_directory": parts[1] if len(parts) > 2 else "<root>",
                    "directory": str(PurePosixPath(relative).parent),
                    "leaf": parts[-1],
                    "occurrences": {},
                },
            )
            occurrence = item["occurrences"].setdefault(
                archive["archive"],
                {"archive": archive["archive"], "files": {}},
            )
            occurrence["files"][suffix] = {
                "virtual_path": path,
                "offset": entry["offset"],
                "compressed_size": entry["compressed_size"],
                "uncompressed_size": entry["uncompressed_size"],
                "compression_method": entry["compression_method"],
                "encrypted": entry["encrypted"],
            }
    meshes, textures, auxiliary = [], [], []
    groups: dict[tuple[str, str], dict] = {}
    roots: dict[str, dict] = {}
    keyword_meshes, keyword_textures = [], []
    for item in sorted(packages.values(), key=lambda value: value["virtual_package"].lower()):
        if not any(".uasset" in value["files"] for value in item["occurrences"].values()):
            continue
        item["occurrences"] = list(item["occurrences"].values())
        item["archive_occurrence_count"] = len(item["occurrences"])
        item["patch_priority_verified"] = False
        item["gender_verified"] = False
        item["known_current_family"] = item["character_root"].lower() in FAMILIES
        leaf, path = item["leaf"], item["virtual_package"]
        is_mesh = leaf.lower().startswith("sk_") and "skeleton" not in leaf.lower() and not PHYSICS.search(path)
        is_texture = bool(TEXTURE.search(leaf))
        if is_mesh:
            item["asset_kind"] = "skeletal_mesh_name_candidate"
            item["class_verified"] = False
            item["roles"] = mesh_roles(leaf, path)
            item["already_in_pcresources"] = path.lower() in previous
            item["new_candidate"] = not item["already_in_pcresources"]
            meshes.append(item)
        elif is_texture:
            item["asset_kind"] = "texture_name_candidate"
            item["class_verified"] = False
            textures.append(item)
        else:
            item["asset_kind"] = "other_package_name_candidate"
            if PHYSICS.search(path):
                item["roles"] = ["physics_or_collision_name_hint"]
                auxiliary.append(item)
        key = (item["character_root"], item["outfit_directory"])
        group = groups.setdefault(
            key,
            {
                "character_root": key[0],
                "outfit_directory": key[1],
                "gender_verified": False,
                "known_current_family": item["known_current_family"],
                "mesh_candidates": [],
                "new_ui_mesh_candidates": [],
                "new_display_mesh_candidates": [],
                "textures": [],
                "auxiliary_physics_candidates": [],
            },
        )
        if is_mesh:
            group["mesh_candidates"].append(item["virtual_package"])
            if "UI" in item["roles"] and item["new_candidate"]:
                group["new_ui_mesh_candidates"].append(item["virtual_package"])
            if item["new_candidate"] and any(role in item["roles"] for role in ("UI", "CutScene", "preview_or_lobby_name_hint")):
                group["new_display_mesh_candidates"].append(item["virtual_package"])
        elif is_texture:
            group["textures"].append(item["virtual_package"])
        elif PHYSICS.search(path):
            group["auxiliary_physics_candidates"].append(item["virtual_package"])
        if KEYWORDS.search(path):
            if is_mesh:
                keyword_meshes.append(item)
            elif is_texture:
                keyword_textures.append(item)
    for group in groups.values():
        root_entry = roots.setdefault(
            group["character_root"],
            {"character_root": group["character_root"], "gender_verified": False, "known_current_family": group["known_current_family"], "outfit_directories": [], "mesh_candidate_count": 0, "new_ui_mesh_candidate_count": 0, "new_display_mesh_candidate_count": 0},
        )
        if group["mesh_candidates"]:
            root_entry["outfit_directories"].append(group["outfit_directory"])
        root_entry["mesh_candidate_count"] += len(group["mesh_candidates"])
        root_entry["new_ui_mesh_candidate_count"] += len(group["new_ui_mesh_candidates"])
        root_entry["new_display_mesh_candidate_count"] += len(group["new_display_mesh_candidates"])
        group["mesh_candidate_count"] = len(group["mesh_candidates"])
        group["new_ui_mesh_candidate_count"] = len(group["new_ui_mesh_candidates"])
        group["new_display_mesh_candidate_count"] = len(group["new_display_mesh_candidates"])
        group["texture_candidate_count"] = len(group["textures"])
    ui_meshes = [item for item in meshes if "UI" in item["roles"]]
    new_ui = [item for item in ui_meshes if item["new_candidate"]]
    new_display = [item for item in meshes if item["new_candidate"] and any(role in item["roles"] for role in ("UI", "CutScene", "preview_or_lobby_name_hint"))]
    naimei = [item for item in meshes if item["character_root"].lower() == "naimei"]
    naimei_groups = [group for group in groups.values() if group["character_root"].lower() == "naimei" and group["mesh_candidates"]]
    role_counts = Counter(role for item in meshes for role in item["roles"])
    report = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "method": "Read the already verified 137 offline Pak-index JSON reports; group full Avatar/Character virtual packages by character root and next-level outfit directory. Preserve every archive occurrence. SK_ names identify mesh candidates only; class and gender have not been loaded or inferred.",
        "limitations": [
            "This includes every character root, including roots that may be male, NPC, legacy, alternate, or incomplete. Directory names do not verify gender or playable roster membership.",
            "UI/3P/1P/low/physics labels are literal filename hints. The name candidates have not been loaded as SkeletalMesh objects here.",
            "The next-level directory can be CommonRes, GameData, or shared assets and is not automatically an independently selectable outfit.",
            "Bunny/Rabbit/Maid/Casino keyword matches are search hints; absent keywords do not prove that a bunny costume is absent.",
            "Existing PCResources mesh comparison sources are excluded from new candidate flags by exact full package identity; material, texture, animation, and compatibility relationships require further work.",
            "Base and update package occurrences remain separate; live mounting priority is unverified.",
        ],
        "inputs": [
            {"file": index_path.relative_to(root).as_posix(), "sha256": hashlib.sha256(index_path.read_bytes()).hexdigest()},
            {"file": comparison_path.relative_to(root).as_posix(), "sha256": hashlib.sha256(comparison_path.read_bytes()).hexdigest()},
        ],
        "summary": {
            "verified_index_archives": len(summary["archives"]),
            "indexed_file_entries": indexed_count,
            "character_roots": len(roots),
            "roots_with_mesh_candidates": sum(bool(entry["mesh_candidate_count"]) for entry in roots.values()),
            "mesh_outfit_directories": sum(bool(group["mesh_candidates"]) for group in groups.values()),
            "skeletal_mesh_name_candidates": len(meshes),
            "ui_mesh_name_candidates": len(ui_meshes),
            "existing_pcresources_unique_meshes": len(previous),
            "existing_pcresources_found": sum(item["already_in_pcresources"] for item in meshes),
            "new_ui_mesh_name_candidates": len(new_ui),
            "new_display_mesh_name_candidates": len(new_display),
            "known_current_family_new_ui_candidates": sum(item["known_current_family"] for item in new_ui),
            "texture_name_candidates": len(textures),
            "role_name_hint_counts": dict(role_counts),
            "naimei_mesh_candidates": len(naimei),
            "naimei_mesh_outfit_directories": len(naimei_groups),
            "keyword_mesh_candidates": len(keyword_meshes),
            "keyword_texture_candidates": len(keyword_textures),
        },
        "character_roots": sorted(roots.values(), key=lambda item: item["character_root"].lower()),
        "groups": sorted(groups.values(), key=lambda item: (item["character_root"].lower(), item["outfit_directory"].lower())),
        "mesh_candidates": meshes,
        "texture_candidates": textures,
        "auxiliary_physics_candidates": auxiliary,
        "naimei_mesh_candidates": naimei,
        "all_missing_mesh_requested_paths": [item["requested_path"] for item in meshes if item["new_candidate"]],
        "new_display_mesh_requested_paths": [item["requested_path"] for item in new_display],
        "keyword_search": {"pattern": KEYWORDS.pattern, "mesh_candidates": keyword_meshes, "texture_candidates": keyword_textures},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    if args.female_config:
        female_config = json.loads(args.female_config.read_text(encoding="utf-8-sig"))
        confirmed_roots = female_config.get("explicit_female_roots", [])
        if not all(isinstance(value, str) for value in confirmed_roots):
            raise ValueError("explicit_female_roots must be an array of directory names")
        female_root_set = {value.lower() for value in confirmed_roots}
        female_meshes = [item for item in meshes if item["character_root"].lower() in female_root_set]
        missing = [item for item in female_meshes if item["new_candidate"]]
        configured: set[str] = set()
        def find_configured(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key == "mesh_package" and isinstance(child, str) and child.startswith("/Game/"):
                        configured.add(child)
                    else:
                        find_configured(child)
            elif isinstance(value, list):
                for child in value:
                    find_configured(child)
        find_configured(female_config.get("female_models", []))
        find_configured(female_config.get("female_related_part_rows", []))
        known_index_paths = {item["requested_path"].lower(): item for item in meshes}
        female_report = {
            "schema_version": 1,
            "gender_basis": "explicit_female_roots supplied by ModeConfig.Gender evidence; no filename-derived gender classification",
            "source_inventory": output.relative_to(root).as_posix(),
            "source_gender_config": {"file": args.female_config.relative_to(root).as_posix(), "sha256": hashlib.sha256(args.female_config.read_bytes()).hexdigest()},
            "female_roots": confirmed_roots,
            "summary": {
                "female_roots": len(female_root_set),
                "mesh_packages_under_confirmed_roots": len(female_meshes),
                "already_in_pcresources": sum(item["already_in_pcresources"] for item in female_meshes),
                "missing_mesh_packages": len(missing),
                "missing_ui_mesh_packages": sum("UI" in item["roles"] for item in missing),
                "missing_cutscene_mesh_packages": sum("CutScene" in item["roles"] for item in missing),
                "configured_unique_mesh_paths": len(configured),
                "configured_paths_not_in_name_inventory": sum(path.lower() not in known_index_paths for path in configured),
            },
            "by_root": [{"root": name, "all_mesh_packages": sum(item["character_root"].lower() == name.lower() for item in female_meshes), "missing_mesh_packages": sum(item["character_root"].lower() == name.lower() for item in missing)} for name in confirmed_roots],
            "all_mesh_packages": [item["requested_path"] for item in female_meshes],
            "missing_mesh_packages": [item["requested_path"] for item in missing],
            "configured_mesh_packages": sorted(configured),
            "configured_paths_not_in_name_inventory": sorted(path for path in configured if path.lower() not in known_index_paths),
            "mesh_candidates": female_meshes,
            "limitations": ["All packages under a confirmed female root are retained, including shared, master, preview, 1P, 3P, UI and CutScene variants. This may include props or non-rendering masters; loading the configured objects is the class/geometry check.", "The supplied gender config report can be superseded by a later base-plus-patch union. Its hash records the exact version used."]
        }
        (output.parent / "female_mesh_inventory.json").write_text(json.dumps(female_report, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        (output.parent / "female_missing_mesh_paths.json").write_text(json.dumps(female_report["missing_mesh_packages"], ensure_ascii=False, indent=2), encoding="utf-8")
        (output.parent / "female_all_mesh_paths.json").write_text(json.dumps(female_report["all_mesh_packages"], ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"female_summary": female_report["summary"], "female_by_root": female_report["by_root"]}, ensure_ascii=False))
    print(json.dumps(report["summary"], ensure_ascii=False))
    print(json.dumps({"naimei_groups": [{"outfit": item["outfit_directory"], "meshes": item["mesh_candidate_count"], "new_ui": item["new_ui_mesh_candidate_count"], "ui_packages": item["new_ui_mesh_candidates"]} for item in naimei_groups]}, ensure_ascii=False))
    print(json.dumps({"keyword_meshes": [item["virtual_package"] for item in keyword_meshes]}, ensure_ascii=False))
    print(f"Report: {output}")


if __name__ == "__main__":
    main()
