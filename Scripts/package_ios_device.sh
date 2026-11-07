#!/usr/bin/env bash
# Build/cook/sign for the already selected existing Team and matching profile.
# This script performs no Apple portal updates and never installs the app.
set -euo pipefail
source "$(dirname "$0")/engine_env.sh"
source "$PROJECT_ROOT/Scripts/ios_device.env"
python3 "$PROJECT_ROOT/Scripts/ios_device_preflight.py" --require-profile
source "$PROJECT_ROOT/Saved/DevicePreparation/signing-selection.env"
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
  "-ini:Engine:/Script/IOSRuntimeSettings.IOSRuntimeSettings:BundleIdentifier=$IOS_BUNDLE_ID" \
  "-ini:Engine:/Script/MacTargetPlatform.XcodeProjectSettings:CodeSigningTeam=$IOS_TEAM_ID" \
  "-ini:Engine:/Script/MacTargetPlatform.XcodeProjectSettings:bUseAutomaticCodeSigning=False" \
  "-ini:Engine:/Script/MacTargetPlatform.XcodeProjectSettings:IOSSigningIdentity=$IOS_SIGNING_IDENTITY_SHA1" \
  "-ini:Engine:/Script/MacTargetPlatform.XcodeProjectSettings:IOSProvisioningProfile=$IOS_PROFILE_UUID" \
  2>&1 | tee "$PROJECT_ROOT/Saved/PackageIOSDevice.log"
RESULT=${PIPESTATUS[0]}
set -e
if [ "$RESULT" -ne 0 ]; then exit "$RESULT"; fi
echo "iOS 打包完成；输出在 Builds/IOS。尚未安装到设备。"
