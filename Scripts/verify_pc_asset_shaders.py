"""Read-only verification of converted material shaders on the active Metal RHI.

Run as a UE Python commandlet with -AllowCommandletRendering and no -nullrhi.
Use -PCShaderStart and -PCShaderCount for bounded fresh-process batches.
"""
import hashlib
import json
from pathlib import Path
import re

import unreal

root = Path(unreal.Paths.project_dir()).resolve()
work = root / "Saved/PCAssetConversion"
plan_path = work / "asset_plan.json"
plan = json.loads(plan_path.read_text(encoding="utf-8"))
command_line = unreal.SystemLibrary.get_command_line()


def option(name, default):
    matches = re.findall(r"(?:^|\s)-" + name + r"=(\d+)(?=\s|$)", command_line)
    return int(matches[-1]) if matches else default


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


start = option("PCShaderStart", 0)
count = option("PCShaderCount", 64)
rows = plan["materials"][start:start + count]
if not rows or count < 1:
    raise ValueError("Shader verification range is empty or invalid")
paths = {row["target"]: root / "Content" / (row["target"][len("/Game/"):] + ".uasset") for row in rows}
before = {target: digest(path) for target, path in paths.items()}
report = json.loads(unreal.PCAssetImportLibrary.verify_material_shaders(list(paths)))
report.update(start=start, count=len(rows), plan_sha256=digest(plan_path),
              asset_sha256_before=before, asset_sha256_after={target: digest(path) for target, path in paths.items()})
report["asset_files_unchanged"] = report["asset_sha256_before"] == report["asset_sha256_after"]
output = work / "Shaders" / f"materials_{start:05d}.json"
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
unreal.log("PC_MATERIAL_SHADERS " + json.dumps({key: report.get(key) for key in
    ("status", "success", "rhi", "shader_platform", "start", "count", "passed_count", "failed_count", "asset_files_unchanged")}))
if report.get("status") != "passed" or not report.get("success") or not report["asset_files_unchanged"]:
    raise RuntimeError("Real RHI material verification failed; see " + str(output))
