#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/Scripts/engine_env.sh"
if [ ! -f "$PROJECT_ROOT/Content/Viewer/Maps/L_Viewer.umap" ]; then
  echo "尚未导入角色。请先运行 Scripts/build_macos.sh 和 Scripts/import_assets.sh。" >&2
  exit 1
fi
exec "$UE_ROOT/Engine/Binaries/Mac/UnrealEditor.app/Contents/MacOS/UnrealEditor" \
  "$PROJECT_FILE" /Game/Viewer/Maps/L_Viewer -game -windowed -ResX=1440 -ResY=900 -log -nosplash
