#!/usr/bin/env python3
"""Publish only the allowlisted local quality report, using portable paths."""
import argparse
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "Saved/PCAssetAnalysis/rendering_parameters.json")
    parser.add_argument("--output", type=Path, default=ROOT / "PCResources/Lighting/pc_quality_settings.json")
    args = parser.parse_args()
    raw = args.input.read_bytes()
    original = json.loads(raw.decode("utf-8-sig"))
    if original.get("schema_version") != 1:
        raise ValueError("Unsupported rendering report version")
    game = original["game_directory"].replace("\\", "/").rstrip("/")

    def portable(value):
        if isinstance(value, dict):
            return {key: portable(item) for key, item in value.items()}
        if isinstance(value, list):
            return [portable(item) for item in value]
        if isinstance(value, str):
            normalized = value.replace("\\", "/")
            if normalized.startswith(game + "/"):
                normalized = normalized[len(game) + 1:]
            if re.match(r"^[A-Za-z]:/", normalized):
                raise ValueError("Unexpected local absolute path in allowlisted report")
            return normalized
        return value

    result = {
        "schema_version": 1,
        "status": "historical_requests_and_saved_user_configuration_only",
        "source_report_sha256": hashlib.sha256(raw).hexdigest(),
        "limitations": original["limitations"],
        "config": portable(original["config"]),
        "log": portable(original["log"]),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"config_entries": result["config"]["entry_count"],
                      "historical_cvar_requests": result["log"]["entry_count"]}))


if __name__ == "__main__":
    main()
