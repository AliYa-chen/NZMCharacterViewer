#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/Scripts/engine_env.sh"
exec "$UE_ROOT/Engine/Binaries/Mac/UnrealEditor.app/Contents/MacOS/UnrealEditor" \
  "$PROJECT_FILE" -log -nosplash
