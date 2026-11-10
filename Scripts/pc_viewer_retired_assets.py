"""Read exact historical package fingerprints from the completed cleanup record.

Retirement never authorizes a missing package by directory or name alone. The
caller must also compare this record with its original saved-asset fingerprint.
"""
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent.parent
MANIFEST = "Docs/PCViewerIntegration/content_cleanup.json"


def retired_record(target, root=ROOT):
    """Return an explicitly retired, currently absent /Game package, or None."""
    if not isinstance(target, str) or not re.fullmatch(r"/Game/[A-Za-z0-9_]+(?:/[A-Za-z0-9_]+)*", target):
        raise ValueError("Expected an exact project package for retirement: " + str(target))
    root = Path(root)
    path = root / MANIFEST
    if not path.is_file():
        return None
    manifest = json.loads(path.read_text(encoding="utf-8-sig"))
    if manifest.get("status") != "completed":
        return None
    rows = [row for row in manifest.get("removed_assets", []) if row.get("target") == target]
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError("Duplicate retired package: " + target)
    row = rows[0]
    expected_stem = "Content/" + target[len("/Game/"):]
    if row.get("file") not in (expected_stem + ".uasset", expected_stem + ".umap"):
        raise ValueError("Retired package file escapes its exact project namespace: " + target)
    if type(row.get("bytes")) is not int or row["bytes"] <= 0 or not re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256", ""))):
        raise ValueError("Retired package lacks a valid saved fingerprint: " + target)
    # Either package extension still being present means it is not retired.
    if any((root / (expected_stem + suffix)).exists() for suffix in (".uasset", ".umap")):
        raise ValueError("Retired package is still present: " + target)
    return row
