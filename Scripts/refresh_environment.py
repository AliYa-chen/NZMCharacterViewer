"""Refresh only the recovered environment; do not touch character meshes."""
from pathlib import Path
import sys
import json
import traceback
import unreal

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import import_assets
import import_environment

try:
    result = import_environment.run(import_assets.PROJECT, import_assets.import_task, import_assets.make_material)
    report = {"status": "environment_imported_requires_visual_validation", "environment": result}
except Exception:
    report = {"status": "failed", "error": traceback.format_exc()}
    raise
finally:
    (import_assets.PROJECT / "Saved/EnvironmentRefreshReport.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
