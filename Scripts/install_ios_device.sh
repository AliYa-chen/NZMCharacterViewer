#!/usr/bin/env bash
# Run only after the parent packaging/import work has completed.
# Accept an already signed/cooked .app; validate it before installation.
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$PROJECT_ROOT/Scripts/ios_device.env"
APP_PATH="${1:?用法：install_ios_device.sh 已完成且签名的.app路径}"
python3 "$PROJECT_ROOT/Scripts/ios_device_preflight.py" --require-profile
python3 - "$APP_PATH" "$IOS_BUNDLE_ID" "$IOS_TEAM_ID" "$PROJECT_ROOT/Saved/DevicePreparation/device-details.json" <<'PY'
from datetime import datetime, timezone
import fnmatch
import json
from pathlib import Path
import plistlib
import subprocess
import sys
app = Path(sys.argv[1]).resolve()
if app.suffix != ".app" or not app.is_dir():
    raise SystemExit("安装源必须为打包完成的 .app 目录。")
info = plistlib.loads((app / "Info.plist").read_bytes())
if info.get("CFBundleIdentifier") != sys.argv[2]:
    raise SystemExit("应用 Bundle ID 与查看器不一致。")
if not (app / info["CFBundleExecutable"]).is_file():
    raise SystemExit("应用可执行文件缺失。")
if not any(p.is_file() and p.suffix in (".pak", ".ucas") for p in app.rglob("*")):
    raise SystemExit("应用中没有 cooked 游戏资源；不安装编译验证用的空 app。")
profile_path = app / "embedded.mobileprovision"
profile = plistlib.loads(subprocess.run(["security", "cms", "-D", "-i", str(profile_path)], capture_output=True, check=True).stdout)
entitlements = profile.get("Entitlements", {})
pattern = entitlements.get("application-identifier", "").split(".", 1)[-1]
device = json.loads(Path(sys.argv[4]).read_text())["result"]["hardwareProperties"]["udid"]
if sys.argv[3] not in profile.get("TeamIdentifier", []) or not fnmatch.fnmatchcase(sys.argv[2], pattern):
    raise SystemExit("应用内嵌 profile 不属于已选 Team/Bundle ID。")
if device not in profile.get("ProvisionedDevices", []):
    raise SystemExit("应用内嵌 profile 不包含目标设备。")
expiry = profile.get("ExpirationDate")
if not expiry or expiry.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc):
    raise SystemExit("应用内嵌 profile 已过期。")
subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
PY
xcrun devicectl device install app --device "$IOS_DEVICE_ID" "$APP_PATH" \
  --timeout 120 --json-output "$PROJECT_ROOT/Saved/DevicePreparation/install-result.json"
if [ "${IOS_LAUNCH_AFTER_INSTALL:-1}" = "1" ]; then
  xcrun devicectl device process launch --device "$IOS_DEVICE_ID" "$IOS_BUNDLE_ID" \
    --timeout 30 --json-output "$PROJECT_ROOT/Saved/DevicePreparation/launch-result.json"
fi
