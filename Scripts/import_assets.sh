#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/engine_env.sh"
python3 "$PROJECT_ROOT/Scripts/validate_project.py" --require-sources
EDITOR="$UE_ROOT/Engine/Binaries/Mac/UnrealEditor.app/Contents/MacOS/UnrealEditor"
if [ ! -x "$EDITOR" ]; then echo "未找到 Mac UnrealEditor：$EDITOR" >&2; exit 1; fi
exec "$EDITOR" "$PROJECT_FILE" /Engine/Maps/Entry -ExecutePythonScript="$PROJECT_ROOT/Scripts/import_assets.py" -unattended -nosplash -log
