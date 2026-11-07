#!/usr/bin/env bash
# Compile/link the separate experimental Metal SM6 + ray-tracing configuration.
# Does not cook, install, publish or change the project's normal mobile config.
set -euo pipefail
source "$(dirname "$0")/engine_env.sh"
mkdir -p "$PROJECT_ROOT/Saved/CinematicIOS"
FRAMEWORK_ARCHIVE="$UE_ROOT/Engine/Binaries/ThirdParty/Apple/MetalShaderConverter/IOS/libmetalirconverter.embeddedframework.zip"
if [ ! -f "$FRAMEWORK_ARCHIVE" ]; then
  echo "当前引擎缺少 Metal SM6 iOS 所需组件：$FRAMEWORK_ARCHIVE" >&2
  echo "此实验配置暂不能编译链接；常规移动版本请运行 Scripts/build_ios_compile.sh。" >&2
  exit 2
fi
env UBT_NO_POST_DEPLOY=true UE_BUILD_FROM_XCODE=1 \
  "$UE_ROOT/Engine/Build/BatchFiles/Mac/Build.sh" \
  NZMCharacterViewer IOS Development "$PROJECT_FILE" -CustomConfig=CinematicIOS \
  -waitmutex -NoHotReloadFromIDE -MaxParallelActions=2 \
  2>&1 | tee "$PROJECT_ROOT/Saved/CinematicIOS/Compile.log"
