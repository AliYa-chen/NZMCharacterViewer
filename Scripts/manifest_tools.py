"""Validation shared by the editor importer and the engine-free checker."""
import json
import math
from pathlib import Path


def source_file(source_root, value):
    source_root = Path(source_root).resolve()
    path = (source_root / value).resolve()
    if not path.is_relative_to(source_root) or not path.is_file():
        raise ValueError(f"SourceAssets 中找不到文件，或路径越界：{value}")
    return path


def validate_manifest(source_root):
    source_root = Path(source_root).resolve()
    manifest = json.loads((source_root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError("manifest.json 需要 schema_version: 1")
    if not manifest.get("characters"):
        raise ValueError("清单必须至少包含一个已导出的角色")
    stats = {"characters": 0, "outfits": 0, "runtime_characters": 0, "runtime_outfits": 0,
             "parts": 0, "runtime_parts": 0, "material_slots": 0,
             "runtime_material_slots": 0, "animations": 0, "source_files": set()}
    character_ids = set()
    for character in manifest["characters"]:
        _identity(character, character_ids, "角色")
        if character.get("portrait"):
            portrait = source_file(source_root, character["portrait"])
            if portrait.suffix.lower() != ".png":
                raise ValueError("角色头像需要已解码的 PNG")
            stats["source_files"].add(str(portrait.relative_to(source_root)))
        stats["characters"] += 1
        outfit_ids = set()
        if not character.get("outfits"):
            raise ValueError(f"角色 {character['id']} 没有可用服装")
        if any(o.get("available_in_viewer", True) for o in character["outfits"]):
            stats["runtime_characters"] += 1
        for outfit in character["outfits"]:
            _identity(outfit, outfit_ids, "服装")
            stats["outfits"] += 1
            outfit_available = outfit.get("available_in_viewer", True)
            if outfit_available:
                stats["runtime_outfits"] += 1
            part_ids = set()
            if not outfit.get("parts"):
                raise ValueError(f"服装 {outfit['id']} 没有已导出的部件")
            if outfit_available and not any(p.get("available_in_viewer", True) for p in outfit["parts"]):
                raise ValueError(f"服装 {outfit['id']} 没有通过验证的运行时部件")
            for part in outfit["parts"]:
                _identity(part, part_ids, "部件")
                mesh = source_file(source_root, part["mesh"])
                if part.get("geometry", {}).get("max_influences", 0) > 8:
                    raise ValueError(f"{part['id']} 超过当前移动端8权重导入上限，不能静默截断")
                if mesh.suffix.lower() not in (".fbx", ".glb", ".gltf"):
                    raise ValueError(f"不支持的网格源格式：{mesh.name}")
                stats["source_files"].add(str(mesh.relative_to(source_root)))
                stats["parts"] += 1
                if outfit_available and part.get("available_in_viewer", True):
                    stats["runtime_parts"] += 1
                if part.get("animation"):
                    stats["animations"] += 1
                    animation = source_file(source_root, part["animation"])
                    if animation.suffix.lower() != ".fbx":
                        raise ValueError("可选部件动画需要单独的已验证 FBX")
                    stats["source_files"].add(str(animation.relative_to(source_root)))
                if part.get("dynamics"):
                    profile = source_file(source_root, part["dynamics"])
                    motion = json.loads(profile.read_text(encoding="utf-8"))
                    if not motion.get("chains") or not motion.get("source_asset_path"):
                        raise ValueError(f"{part['id']} 动态配置缺少原始来源或骨链")
                    stats["source_files"].add(str(profile.relative_to(source_root)))
                transform = part.get("transform", {})
                for key, default in (("translation", [0, 0, 0]), ("rotation", [0, 0, 0]), ("scale", [1, 1, 1])):
                    values = transform.get(key, default)
                    if len(values) != 3 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
                        raise ValueError(f"{part['id']} 的 transform.{key} 需要三个有限数值")
                if any(v == 0 for v in transform.get("scale", [1, 1, 1])):
                    raise ValueError(f"{part['id']} 的缩放不能为零")
                slots = set()
                for material in part.get("materials", []):
                    slot = material["slot"]
                    if not isinstance(slot, int) or slot < 0 or slot in slots:
                        raise ValueError(f"{part['id']} 有无效或重复材质槽 {slot}")
                    slots.add(slot)
                    stats["material_slots"] += 1
                    if outfit_available and part.get("available_in_viewer", True):
                        stats["runtime_material_slots"] += 1
                    for channel in ("base_color", "normal", "roughness", "metallic", "opacity", "emissive"):
                        value = material.get(channel)
                        if value:
                            file = source_file(source_root, value)
                            if file.suffix.lower() != ".png":
                                raise ValueError(f"{part['id']} 的 {channel} 需要 PNG")
                            stats["source_files"].add(str(file.relative_to(source_root)))
    stats["source_files"] = sorted(stats["source_files"])
    return manifest, stats


def _identity(item, seen, label):
    identity = item.get("id", "")
    if not identity or not all(c.isascii() and (c.isalnum() or c == "_") for c in identity):
        raise ValueError(f"{label} id 只能使用英文字母、数字和下划线")
    if identity in seen:
        raise ValueError(f"重复的{label} id：{identity}")
    if not item.get("name"):
        raise ValueError(f"{label} {identity} 缺少显示名称")
    seen.add(identity)
