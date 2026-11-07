#!/usr/bin/env bash
# Build/cook/sign for the selected existing Team and matching profile.
# Xcode-managed profiles use automatic signing. This script never installs.
set -euo pipefail
source "$(dirname "$0")/engine_env.sh"
source "$PROJECT_ROOT/Scripts/ios_device.env"
python3 "$PROJECT_ROOT/Scripts/ios_device_preflight.py" --require-profile
source "$PROJECT_ROOT/Saved/DevicePreparation/signing-selection.env"
# ApplePostBuildSync starts a fresh UBT/Xcode-generation process. Command-line
# -ini overrides are not inherited there; its config hierarchy reads Saved.
python3 "$PROJECT_ROOT/Scripts/prepare_ios_signing.py"
if [ ! -f "$PROJECT_ROOT/Content/Viewer/Maps/L_Viewer.umap" ]; then
  echo "启动关卡尚未导入完成，暂不打包。" >&2
  exit 1
fi
# Remove compile-only flags so this explicit packaging command finalizes its app.
unset UBT_NO_POST_DEPLOY UE_BUILD_FROM_XCODE
set +e
"$UE_ROOT/Engine/Build/BatchFiles/RunUAT.sh" BuildCookRun \
  -project="$PROJECT_FILE" -noP4 -target=NZMCharacterViewer \
  -platform=IOS -clientconfig=Development -build -cook -stage -pak -iostore -package -archive \
  -archivedirectory="$PROJECT_ROOT/Builds/IOS" -utf8output \
  2>&1 | tee "$PROJECT_ROOT/Saved/PackageIOSDevice.log"
RESULT=${PIPESTATUS[0]}
set -e
if [ "$RESULT" -ne 0 ]; then exit "$RESULT"; fi
echo "iOS 打包完成；输出在 Builds/IOS。尚未安装到设备。"
