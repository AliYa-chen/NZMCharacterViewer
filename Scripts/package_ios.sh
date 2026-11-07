#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/engine_env.sh"
: "${IOS_TEAM_ID:?请设置已登录 Xcode 的 Apple Development Team ID}"
IOS_BUNDLE_ID="${IOS_BUNDLE_ID:-com.example.nzmcharacterviewer}"
if [ ! -f "$PROJECT_ROOT/Content/Viewer/Maps/L_Viewer.umap" ]; then
  echo "请先构建 Editor 并完成 import_assets.sh，生成启动关卡后再打包。" >&2
  exit 1
fi
python3 "$PROJECT_ROOT/Scripts/validate_project.py"
exec "$UE_ROOT/Engine/Build/BatchFiles/RunUAT.sh" BuildCookRun \
  -project="$PROJECT_FILE" -noP4 -target=NZMCharacterViewer \
  -platform=IOS -clientconfig=Development -build -cook -stage -pak -iostore -package -archive \
  -archivedirectory="$PROJECT_ROOT/Builds/IOS" \
  "-ini:Engine:/Script/IOSRuntimeSettings.IOSRuntimeSettings:IOSTeamID=$IOS_TEAM_ID" \
  "-ini:Engine:/Script/IOSRuntimeSettings.IOSRuntimeSettings:BundleIdentifier=$IOS_BUNDLE_ID" \
  "-ini:Engine:/Script/IOSRuntimeSettings.IOSRuntimeSettings:bAutomaticSigning=True"
