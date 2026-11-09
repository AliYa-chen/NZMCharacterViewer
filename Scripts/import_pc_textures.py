#!/usr/bin/env python3
"""Audit/apply PCResources/upgrade_manifest.json without rebuilding the Catalog.

Outside UE: python3 Scripts/import_pc_textures.py --validate-files
UE Python: -PCTextureMode=audit (default), or -PCTextureMode=apply.
Only audited editable UE5.8 texture packages are supported. Apply requires UE
and remains subject to real shader, visual, memory and device verification.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import sys
import traceback

PROJECT = Path(__file__).resolve().parent.parent
RESOURCES = PROJECT / "PCResources"
REPORT_PATH = PROJECT / "Saved/PCAssetAnalysis/texture_import_report.json"
SETTINGS = ("srgb", "compression_settings", "lod_group", "never_stream",
            "flip_green_channel", "lod_bias", "max_texture_size", "mip_gen_settings",
            "filter", "address_x", "address_y", "compression_no_alpha",
            "compression_quality", "alpha_coverage_thresholds",
            "do_scale_mips_for_alpha_coverage", "preserve_border", "power_of_two_mode",
            "virtual_texture_streaming", "normalize_normals", "use_new_mip_filter",
            "use_legacy_gamma", "source_color_settings")


def sha256(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def inside(root, relative):
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError(f"Expected a relative forward-slash path: {relative}")
    path = (root / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f"Missing/outside input root: {relative}")
    return path


def png_size(path):
    with path.open("rb") as stream:
        header = stream.read(33)
    if len(header) != 33 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise ValueError(f"Not a PNG: {path.name}")
    return list(struct.unpack_from(">II", header, 16))


def editable_source_size(path):
    """Bounded port of inspect_texture_sources.ps1; never uses thumbnails."""
    with path.open("rb") as stream:
        data = stream.read(1024 * 1024)
    def integer(offset):
        if offset < 0 or offset + 4 > len(data):
            raise ValueError("Texture metadata exceeds the bounded read")
        return struct.unpack_from("<i", data, offset)[0]
    if data[:4] != b"\xc1\x83\x2a\x9e" or [integer(i) for i in (4, 12, 16)] != [-9, 522, 1018]:
        raise ValueError("Unsupported editable texture package version; expected audited UE5.8 layout")
    header, custom = integer(44), integer(48)
    if not 128 <= header < len(data) or not 0 <= custom <= 1024:
        raise ValueError("Invalid package header bounds")
    folder = 52 + 20 * custom
    length = integer(folder)
    if not 1 <= length <= 65536 or folder + length + 20 > header:
        raise ValueError("Invalid folder FString")
    at = folder + 8 + length
    count, cursor = integer(at), integer(at + 4)
    if not 1 <= count <= 16384 or not at + 8 <= cursor < header:
        raise ValueError("Invalid name map")
    names = []
    for _ in range(count):
        length = integer(cursor)
        cursor += 4
        size = -2 * length if length < 0 else length
        terminator = 2 if length < 0 else 1
        if not 1 <= abs(length) <= 65536 or cursor + size + 4 > header:
            raise ValueError("Invalid name-map string bounds")
        if data[cursor + size - terminator:cursor + size] != b"\0" * terminator:
            raise ValueError("Name-map terminator missing")
        names.append(data[cursor:cursor + size - terminator].decode("utf-16-le" if length < 0 else "utf-8"))
        cursor += size + 4
    if b"++UE5+Release-5.8" not in data[:header]:
        raise ValueError("Package is outside audited UE5.8 branch")
    def name(value):
        return struct.pack("<ii", names.index(value), 0)
    def unique(prefix, start, end):
        found = data.find(prefix, start, end)
        if found < 0 or data.find(prefix, found + 1, end) >= 0:
            raise ValueError("Missing/ambiguous typed texture property")
        return found
    def structure(prop, kind, module):
        return name(prop) + name("StructProperty") + struct.pack("<i", 1) + name(kind) + struct.pack("<i", 1) + name(module) + struct.pack("<i", 0)
    end = min(len(data), header + 65536)
    tag = unique(structure("Source", "TextureSource", "/Script/Engine"), header, end)
    start, source_end = tag + 49, tag + 49 + integer(tag + 44)
    if data[tag + 48] != 0 or not 100 <= source_end - start <= 65536 or source_end > end:
        raise ValueError("Invalid TextureSource bounds/flags")
    source_size = []
    for field in ("SizeX", "SizeY"):
        prefix = name(field) + name("IntProperty") + struct.pack("<iiB", 0, 4, 0)
        offset = unique(prefix, start, source_end) + 25
        if offset + 4 > source_end:
            raise ValueError("Source dimension payload outside struct")
        source_size.append(integer(offset))
    imported = unique(structure("ImportedSize", "IntPoint", "/Script/CoreUObject"), header, end)
    if imported + 57 > end or integer(imported + 44) != 8 or data[imported + 48] != 8:
        raise ValueError("Unsupported ImportedSize property")
    if source_size != [integer(imported + 49), integer(imported + 53)] or any(not 1 <= n <= 65536 for n in source_size):
        raise ValueError("Source and ImportedSize dimensions disagree")
    return source_size


def validate_files(manifest_path):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("candidates"), list) or not manifest["candidates"]:
        raise ValueError("Expected schema_version=1 and nonempty candidates")
    targets, checked = set(), []
    for row in manifest["candidates"]:
        target = row["target_asset"]
        if not re.fullmatch(r"/Game/Characters/Textures/[0-9a-f]{10}/[A-Za-z0-9_]+", target) or target.casefold() in targets:
            raise ValueError(f"Invalid/duplicate target asset: {target}")
        targets.add(target.casefold())
        expected_file = "Content/" + target[len("/Game/"):] + ".uasset"
        evidence = row.get("mapping_evidence")
        if (row["target_file"] != expected_file or row.get("mapping_verified") is not True
                or not isinstance(evidence, list) or not evidence or not all(isinstance(item, dict) for item in evidence)):
            raise ValueError(f"Target path or binding evidence invalid: {target}")
        if not isinstance(row.get("source_object_path"), str) or not row["source_object_path"].startswith(("/Game/", "NZM/Content/")):
            raise ValueError(f"PC source object reference missing: {target}")
        disk, source = inside(PROJECT, row["target_file"]), inside(RESOURCES, row["source"])
        if source.suffix.lower() != ".png":
            raise ValueError("Only PNG texture sources are supported")
        for path, expected in ((disk, row["original_sha256"]), (source, row["source_sha256"])):
            if not re.fullmatch(r"[0-9a-f]{64}", expected) or sha256(path) != expected:
                raise ValueError(f"SHA256 mismatch: {path.name}")
        old, new = editable_source_size(disk), png_size(source)
        if old != row["current_source_size"] or new != row["source_size"]:
            raise ValueError(f"Source dimension evidence mismatch: {target}")
        if not all(n > o for o, n in zip(old, new)) or old[0] * new[1] != old[1] * new[0]:
            raise ValueError(f"Expected strictly larger source with unchanged aspect: {target}")
        if any(disk.with_suffix(ext).exists() for ext in (".uexp", ".ubulk", ".uptnl")):
            raise ValueError(f"External editable package sidecars unsupported: {target}")
        checked.append((row, disk, source))
    return checked


def run_unreal(checked, mode, report):
    import unreal
    library = unreal.EditorAssetLibrary
    prepared = []
    dirty = {package.get_name() for package in unreal.EditorLoadingAndSavingUtils.get_dirty_content_packages()}
    for row, disk, source in checked:
        texture = library.load_asset(row["target_asset"])
        if not isinstance(texture, unreal.Texture2D) or texture.get_path_name().split(".", 1)[0] != row["target_asset"]:
            raise RuntimeError("Target did not load as exact Texture2D: " + row["target_asset"])
        if row["target_asset"] in dirty:
            raise RuntimeError("Target has unsaved editor changes: " + row["target_asset"])
        settings = {key: texture.get_editor_property(key) for key in SETTINGS}
        metadata = dict(library.get_metadata_tag_values(texture))
        prepared.append((row, disk, source, texture, settings, metadata))
    report["loaded_texture_count"] = len(prepared)
    if mode == "audit":
        report["status"] = "audit_passed_no_content_saved"
        return
    backup = PROJECT / "Saved/PCAssetAnalysis/TextureBackups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    backup.mkdir(parents=True)
    for row, disk, _, _, _, _ in prepared:
        destination = backup / row["target_file"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(disk, destination)
        if sha256(destination) != row["original_sha256"]:
            raise RuntimeError("Backup verification failed")
    (backup / "manifest.json").write_text(json.dumps({"candidates": [p[0] for p in prepared]}, ensure_ascii=False, indent=2), encoding="utf-8")
    report["backup_directory"] = backup.relative_to(PROJECT).as_posix()
    report["status"] = "applying_partial_changes_possible"
    for row, disk, source, _, settings, metadata in prepared:
        if sha256(disk) != row["original_sha256"]:
            raise RuntimeError("Target changed after preflight: " + row["target_asset"])
        factory = unreal.TextureFactory()
        factory.set_editor_property("create_material", False)
        factory.set_editor_property("compression_settings", settings["compression_settings"])
        factory.set_editor_property("lod_group", settings["lod_group"])
        factory.set_editor_property("flip_normal_map_green_channel", settings["flip_green_channel"])
        task = unreal.AssetImportTask()
        task.filename, task.destination_path, task.destination_name = str(source), row["target_asset"].rsplit("/", 1)[0], row["target_asset"].rsplit("/", 1)[1]
        task.automated, task.replace_existing, task.replace_existing_settings, task.save, task.factory = True, True, False, False, factory
        unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
        textures = [obj for obj in task.get_objects() if isinstance(obj, unreal.Texture2D)]
        if len(textures) != 1 or textures[0].get_path_name().split(".", 1)[0] != row["target_asset"]:
            raise RuntimeError("Import returned an unexpected texture/path")
        texture = textures[0]
        for key, value in settings.items():
            texture.set_editor_property(key, value)
        for key in library.get_metadata_tag_values(texture):
            if key not in metadata:
                library.remove_metadata_tag(texture, key)
        for key, value in metadata.items():
            library.set_metadata_tag(texture, key, value)
        if any(texture.get_editor_property(key) != value for key, value in settings.items()):
            raise RuntimeError("Texture settings did not survive reimport")
        if dict(library.get_metadata_tag_values(texture)) != metadata:
            raise RuntimeError("Texture metadata changed during reimport")
        if not library.save_loaded_asset(texture):
            raise RuntimeError("Texture save failed")
        if editable_source_size(disk) != row["source_size"]:
            raise RuntimeError("Saved texture source dimensions differ from PNG")
        report["saved_targets"].append(row["target_asset"])
    report["status"] = "applied_requires_visual_and_device_validation"
    report["editor_apply_completed"] = True
    report["source_import_data_policy"] = "Importer-managed source filenames retained; repository PCResources PNG files remain available. No unverified source-data clearing API was used."


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate-files", action="store_true")
    parser.add_argument("--manifest", type=Path, default=RESOURCES / "upgrade_manifest.json")
    args, unknown = parser.parse_known_args()
    mode = "audit"
    command_line = " ".join(unknown)
    if not args.validate_files:
        import unreal
        command_line += " " + unreal.SystemLibrary.get_command_line()
    matches = re.findall(r"(?:^|\s)-PCTextureMode=(\w+)(?=\s|$)", command_line)
    if matches:
        if len(set(matches)) != 1 or matches[0] not in ("audit", "apply"):
            raise ValueError("PCTextureMode must be audit or apply")
        mode = matches[0]
    report = {"schema_version": 1, "mode": "validate_files" if args.validate_files else mode,
              "status": "started", "saved_targets": [], "editor_apply_completed": False,
              "limitations": ["Apply is not atomic: after an error, close the Editor without saving before restoring the verified backup files.",
                              "Source metadata checks do not establish shader, pixel, UV, animation, memory or device correctness."]}
    try:
        checked = validate_files(args.manifest.resolve())
        report["validated_file_pairs"] = len(checked)
        if args.validate_files:
            report["status"] = "file_validation_passed_no_content_saved"
        else:
            run_unreal(checked, mode, report)
    except Exception:
        changing = report["status"] == "applying_partial_changes_possible" or bool(report["saved_targets"])
        report["status"] = "failed_partial_changes_possible" if changing else "failed"
        report["error"] = traceback.format_exc()
        raise
    finally:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
