#!/usr/bin/env bash
# Source this file from the project scripts.
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_FILE="$PROJECT_ROOT/NZMCharacterViewer.uproject"
if [ -z "${UE_ROOT:-}" ]; then
  for candidate in "$HOME/UE/UE_5.8" "/Users/Shared/Epic Games/UE_5.8" "/Users/Shared/Epic Games/UE_5.8.4"; do
    if [ -f "$candidate/Engine/Build/Build.version" ] && [ -f "$candidate/Engine/Build/BatchFiles/Mac/Build.sh" ]; then
      UE_ROOT="$candidate"; break
    fi
  done
fi
if [ -z "${UE_ROOT:-}" ] || [ ! -f "$UE_ROOT/Engine/Build/Build.version" ] || [ ! -f "$UE_ROOT/Engine/Build/BatchFiles/Mac/Build.sh" ]; then
  echo "找不到虚幻引擎。请安装 UE 5.8.4，并设置 UE_ROOT 为含 Engine 的安装目录。" >&2
  exit 1
fi
python3 - "$UE_ROOT/Engine/Build/Build.version" <<'PY'
import json, sys
version = json.load(open(sys.argv[1]))
if (version.get("MajorVersion"), version.get("MinorVersion")) != (5, 8):
    raise SystemExit("本工程需要 UE 5.8；当前 UE_ROOT 的 Build.version 不匹配。")
PY
export UE_ROOT
