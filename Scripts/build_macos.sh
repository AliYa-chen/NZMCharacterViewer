#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/engine_env.sh"
python3 "$PROJECT_ROOT/Scripts/validate_project.py"
"$UE_ROOT/Engine/Build/BatchFiles/Mac/Build.sh" NZMCharacterViewerEditor Mac Development "$PROJECT_FILE" -waitmutex -NoHotReloadFromIDE
echo "Editor 模块已由引擎构建。运行 OpenEditor.command 或 OpenViewer.command。"
