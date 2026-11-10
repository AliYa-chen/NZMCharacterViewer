#!/usr/bin/env python3
"""Publish a validated offline character collection with portable file references."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil


def extended_path(path):
    """Allow long cooked package paths on Windows without changing the machine."""
    path = path.resolve()
    if os.name == "nt" and not str(path).startswith("\\\\?\\"):
        text = str(path)
        path = Path("\\\\?\\UNC\\" + text[2:] if text.startswith("\\\\") else "\\\\?\\" + text)
    return path


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def within(root, path):
    path = extended_path(path)
    if not path.is_relative_to(root):
        raise ValueError(f"Path outside repository: {path}")
    return path


def write_catalog(target, collection, configuration):
    families = {}
    for resource in collection["resources"]:
        mesh_files = [row for row in resource["files"] if row["file"].endswith((".psk", ".pskx"))]
        if not mesh_files:
            continue
        match = re.search(r"/Avatar/Character/([^/]+)/([^/]+)/", resource["source_package"])
        family_id, outfit_id = match.groups() if match else ("Shared", "Shared")
        family = families.setdefault(family_id, {"id": family_id, "configuration_names": [],
                                               "model_ids": [], "groups": {}})
        group = family["groups"].setdefault(outfit_id, {"id": outfit_id, "meshes": []})
        group["meshes"].append({"source_package": resource["source_package"], "files": mesh_files})
    for model in configuration.get("female_models", []):
        for asset in model["data_assets"]:
            match = re.search(r"/Avatar/Character/([^/]+)/", asset["virtual_package"])
            if not match or match[1] not in families:
                continue
            family = families[match[1]]
            if model["description"] not in family["configuration_names"]:
                family["configuration_names"].append(model["description"])
            if model["model_id"] not in family["model_ids"]:
                family["model_ids"].append(model["model_id"])
    rows = []
    for family in sorted(families.values(), key=lambda item: item["id"]):
        family["groups"] = sorted(family["groups"].values(), key=lambda item: item["id"])
        family["mesh_count"] = sum(len(group["meshes"]) for group in family["groups"])
        family["classification"] = ("explicit_female_configuration" if family["model_ids"]
                                    else "shared_configuration_accessory" if family["id"] == "Shared"
                                    else "source_only_female_evidence")
        rows.append(family)
    catalog = {"schema_version": 1, "scope": "Published mesh families and resource directory groups; groups are not selectable outfit claims.",
               "mesh_packages": collection["mesh_package_count"], "families": rows}
    write_json(target / "catalog.json", catalog)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", type=Path, required=True,
                        help="Directory containing character_resource_manifest.json")
    parser.add_argument("--evidence", type=Path, required=True,
                        help="Directory containing outfit_config_evidence.json")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    root = extended_path(args.root)
    collection = within(root, args.collection)
    evidence = within(root, args.evidence)
    pc = root / "PCResources"
    target = pc / "Characters"
    report = read_json(collection / "character_resource_manifest.json")
    if report["status"] != "collected_and_data_validated" or report["failed_count"]:
        raise ValueError("Only a complete, data-validated collection may be published")
    if report.get("referenced_existing_count"):
        raise ValueError("Publish from a fresh provider export; old base-package reuse is ambiguous")
    manifest = read_json(pc / "source_manifest.json")
    existing = {}
    for row in manifest["files"]:
        if row["file"].startswith("Characters/"):
            continue
        path = within(root, pc / row["file"])
        if path.suffix.lower() == ".md":
            # Publication documentation is edited with the new collection and
            # rehashed below; it is never a candidate for asset deduplication.
            continue
        if path.stat().st_size != row["bytes"] or digest(path) != row["sha256"]:
            raise ValueError(f"Existing publication changed: {row['file']}")
        existing.setdefault((row["sha256"], path.suffix.lower()), row["file"])
    mappings = {}
    published = {}
    for resource in report["resources"]:
        if resource["status"] != "exported":
            raise ValueError(f"Unexported resource: {resource['source_package']}")
        for row in resource["files"]:
            source = within(root, root / row["file"])
            if source.stat().st_size != row["bytes"] or digest(source) != row["sha256"]:
                raise ValueError(f"Collection checksum mismatch: {row['file']}")
            key = (row["sha256"], source.suffix.lower())
            relative = existing.get(key)
            if relative is None:
                local = source.relative_to(collection).as_posix()
                match = re.search(r"/(Json|Meshes|Textures|HDR)/(?:NZM/Content/|Engine/Content/)?(.+)$", "/" + local)
                if not match:
                    raise ValueError(f"Unrecognized export path: {local}")
                category, tail = match.groups()
                category = "Metadata" if category == "Json" else category
                if "/Engine/Content/" in local:
                    tail = "Engine/" + tail
                relative = f"Characters/{category}/{tail}"
                destination = within(root, pc / relative)
                if destination.exists() and digest(destination) != row["sha256"]:
                    raise ValueError(f"Conflicting export bytes: {relative}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                if not destination.exists():
                    shutil.copyfile(source, destination)
                existing[key] = relative
            mapped = "PCResources/" + relative
            mappings[row["file"]] = mapped
            published[mapped] = {"file": mapped, "bytes": row["bytes"], "sha256": row["sha256"]}

    # Retain full configuration records, including base/selected differences.
    config = read_json(evidence / "outfit_config_evidence.json")
    auxiliary = [evidence / "female_mesh_plan.json", evidence / "outfit_config_evidence.json"]
    for row in config["sources"]:
        source = within(root, evidence / row["file"])
        if digest(source) != row["sha256"]:
            raise ValueError(f"Configuration checksum mismatch: {row['file']}")
        auxiliary.append(source)
    review_directory = evidence / "ReviewCandidates"
    if review_directory.is_dir():
        auxiliary.extend(sorted(review_directory.rglob("*.json")))
    for step in report["steps"]:
        for key in ("report", "validation_report"):
            if step.get(key):
                auxiliary.append(within(root, root / step[key]))
    for resource in report["resources"]:
        for key in ("export_report", "validation_report"):
            if resource.get(key):
                auxiliary.append(within(root, root / resource[key]))
    auxiliary = list(dict.fromkeys(auxiliary))
    supplemental_exports = []
    # Stage reports also contain inline material-expression exports. Their
    # ownership is the containing package, so they need not be resource rows.
    for report_path in auxiliary:
        if report_path.name != "export_report.json":
            continue
        for export in read_json(report_path).get("exports", []):
            if not export.get("success"):
                continue
            for filename in export.get("files", []):
                source = within(root, Path(filename))
                source_relative = source.relative_to(root).as_posix()
                if source_relative in mappings or not source.is_relative_to(collection):
                    continue
                file_hash = digest(source)
                key = (file_hash, source.suffix.lower())
                relative = existing.get(key)
                if relative is None:
                    local = source.relative_to(collection).as_posix()
                    match = re.search(r"/(Json|Meshes|Textures|HDR)/(?:NZM/Content/|Engine/Content/)?(.+)$", "/" + local)
                    if not match:
                        raise ValueError(f"Unrecognized supplemental export: {local}")
                    category, tail = match.groups()
                    category = "Metadata" if category == "Json" else category
                    if "/Engine/Content/" in local:
                        tail = "Engine/" + tail
                    relative = f"Characters/{category}/{tail}"
                    destination = within(root, pc / relative)
                    if destination.exists() and digest(destination) != file_hash:
                        raise ValueError(f"Conflicting supplemental export: {relative}")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if not destination.exists():
                        shutil.copyfile(source, destination)
                    existing[key] = relative
                mapped = "PCResources/" + relative
                mappings[source_relative] = mapped
                published[mapped] = {"file": mapped, "bytes": source.stat().st_size, "sha256": file_hash}
                supplemental_exports.append({**published[mapped], "object_path": export["object_path"],
                                             "source_report": report_path.relative_to(root).as_posix()})
    for source in auxiliary:
        source = within(root, source)
        local = source.relative_to(root).as_posix()
        if source.is_relative_to(collection):
            tail = "Validation/" + source.relative_to(collection).as_posix()
        else:
            tail = "Configuration/" + source.relative_to(evidence).as_posix()
        mappings[local] = "PCResources/Characters/" + tail
        if source.is_relative_to(evidence):
            mappings[source.relative_to(evidence).as_posix()] = mappings[local]
    mappings[(collection / "source_plan.json").relative_to(root).as_posix()] = \
        "PCResources/Characters/Configuration/female_mesh_plan.json"

    game_root = str(report.get("input_root", "")).replace("\\", "/").rstrip("/")

    def portable(value):
        if isinstance(value, list):
            return [portable(item) for item in value]
        if isinstance(value, dict):
            converted = {key: portable(item) for key, item in value.items()}
            if isinstance(converted.get("file"), str) and converted["file"].startswith("LOCAL_EVIDENCE:"):
                converted["source_local_file"] = converted.pop("file").removeprefix("LOCAL_EVIDENCE:")
                converted["file_distribution"] = "not_published_provenance_only"
            return converted
        if not isinstance(value, str):
            return value
        if value in mappings:
            return mappings[value]
        text = value.replace("\\", "/")
        if text in mappings:
            return mappings[text]
        root_prefix = root.as_posix().removeprefix("//?/")
        for prefix in (root.as_posix(), root_prefix):
            if text.startswith(prefix + "/"):
                relative = text[len(prefix) + 1:]
                return mappings.get(relative, relative)
        if game_root and text == game_root:
            return "NZM_INSTALL"
        if game_root and text.startswith(game_root + "/"):
            return "NZM_INSTALL/" + text[len(game_root) + 1:]
        # Preserve serialized property text; normalize only actual file paths.
        if re.match(r"^[A-Za-z]:/", text) and "ToolRuntime" in text:
            return "LOCAL_RUNTIME_CACHE"
        if text.startswith("Saved/"):
            return "LOCAL_EVIDENCE:" + text
        return value

    def refresh_references(value):
        if isinstance(value, list):
            return [refresh_references(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: refresh_references(item) for key, item in value.items()}
        if isinstance(result.get("file"), str) and result["file"].startswith("PCResources/") and "sha256" in result:
            referenced = within(root, root / result["file"])
            if referenced.exists():
                current = digest(referenced)
                if result["sha256"] != current:
                    result.setdefault("original_sha256", result["sha256"])
                    result["sha256"] = current
                if "bytes" in result:
                    result["bytes"] = referenced.stat().st_size
        return result

    def publication_value(source):
        value = read_json(source)
        if source == evidence / "female_mesh_plan.json":
            excluded = value.pop("unavailable_mesh_references", [])
            available = set(value["mesh_paths"])
            value.pop("all_candidate_mesh_paths", None)
            for name in ("default_route_mesh_paths", "custom_replacement_mesh_paths", "inventory_candidate_mesh_paths"):
                if name in value:
                    value[name] = [path for path in value[name] if path in available]
            value["publication_scope"] = "Only installed mesh packages are published; unavailable candidates were skipped. Original source table values remain in configuration evidence."
            value["skipped_unavailable_count"] = len(excluded)
            value["limitations"] = [text for text in value.get("limitations", []) if "unavailable_mesh_references" not in text]
        return refresh_references(portable(value))

    # The review summary references its input reports; settle those hashes before
    # writing the containing configuration evidence. No source property is lost.
    for _ in range(4):
        changed = False
        for source in auxiliary:
            if source == evidence / "outfit_config_evidence.json":
                continue
            destination = within(root, root / mappings[source.relative_to(root).as_posix()])
            value = publication_value(source)
            serialized = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
            if not destination.exists() or destination.read_text(encoding="utf-8") != serialized:
                write_json(destination, value)
                changed = True
        if not changed:
            break
    else:
        raise ValueError("Published evidence hash references did not converge")
    published_config = refresh_references(portable(config))
    if "mesh_package_availability" in published_config:
        published_config["mesh_package_availability"] = [row for row in published_config["mesh_package_availability"]
                                                        if row.get("status") == "installed_provider_package"]
    write_json(target / "Configuration/outfit_config_evidence.json", published_config)

    result = portable(report)
    result["skipped_unavailable_count"] = len(result.pop("unavailable_config_references", []))
    result["supplemental_exports"] = portable(supplemental_exports)
    result["publication"] = {
        "scope": "Installed archive inventory and configuration evidence; live selectable roster is unverified.",
        "provider": "Fresh export using the offline provider with base and patch archives; runtime mounting priority unverified.",
        "deduplication": "Files reuse existing PCResources paths only after identical SHA256 and file-type comparison.",
        "unique_resource_files": len(published),
        "files_reusing_prior_publication": sum(not p.startswith("PCResources/Characters/") for p in published),
    }
    # Source plan is already published separately with portable configuration references.
    result["inputs"] = [{"file": "PCResources/Characters/Configuration/female_mesh_plan.json",
                         "bytes": (target / "Configuration/female_mesh_plan.json").stat().st_size,
                         "sha256": digest(target / "Configuration/female_mesh_plan.json")}]
    write_json(target / "character_resource_manifest.json", result)
    write_catalog(target, result, config)
    manifest["character_expansion"] = {
        "manifest": "Characters/character_resource_manifest.json",
        "configuration": "Characters/Configuration/outfit_config_evidence.json",
        "mesh_packages": report["mesh_package_count"],
        "material_packages": report["material_dependency_count"],
        "texture_packages": report["texture_dependency_count"],
        "status": report["status"],
    }
    files = []
    counts = dict(files=0, bytes=0, png=0, actorx=0, hdr=0)
    for path in sorted(pc.rglob("*")):
        if not path.is_file() or path == pc / "source_manifest.json":
            continue
        size = path.stat().st_size
        files.append(dict(file=path.relative_to(pc).as_posix(), bytes=size, sha256=digest(path)))
        counts["files"] += 1
        counts["bytes"] += size
        key = {".png": "png", ".psk": "actorx", ".pskx": "actorx", ".hdr": "hdr"}.get(path.suffix.lower())
        if key:
            counts[key] += 1
    manifest["files"] = files
    manifest["counts"] = counts
    write_json(pc / "source_manifest.json", manifest)
    print(json.dumps({"status": "published", **manifest["character_expansion"], "totals": counts}, indent=2))


if __name__ == "__main__":
    main()
