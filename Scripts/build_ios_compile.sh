#!/usr/bin/env bash
# Verify the iOS arm64 C++ target without Xcode app finalization or signing.
# This does not cook assets, make an installable IPA, or deploy to a device.
set -euo pipefail
source "$(dirname "$0")/engine_env.sh"

mkdir -p "$PROJECT_ROOT/Saved"
LOG_FILE="$PROJECT_ROOT/Saved/BuildIOSCompile.log"
REPORT_FILE="$PROJECT_ROOT/Saved/BuildIOSCompileVerification.json"

# UE 5.8 source references:
# UEDeployIOS.cs:692: UBT_NO_POST_DEPLOY skips the deployment preparation path.
# AppleToolChain.cs:1268: UE_BUILD_FROM_XCODE skips modern Xcode finalization.
# The latter is required by this installed engine's ApplePostBuildSync action.
set +e
(
  echo "iOS arm64 Development target compile/link verification only."
  echo "Xcode app finalization, signing, cooking, IPA packaging and device deployment are skipped."
  echo "UBT_NO_POST_DEPLOY=true UE_BUILD_FROM_XCODE=1"
  env UBT_NO_POST_DEPLOY=true UE_BUILD_FROM_XCODE=1 \
    "$UE_ROOT/Engine/Build/BatchFiles/Mac/Build.sh" \
    NZMCharacterViewer IOS Development "$PROJECT_FILE" \
    -waitmutex -NoHotReloadFromIDE -MaxParallelActions=2
) 2>&1 | tee "$LOG_FILE"
BUILD_EXIT=${PIPESTATUS[0]}
set -e

python3 - "$PROJECT_ROOT" "$UE_ROOT" "$BUILD_EXIT" "$LOG_FILE" "$REPORT_FILE" <<'PY'
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import struct
import sys

project, engine = map(Path, sys.argv[1:3])
exit_code = int(sys.argv[3])
log_file, report_file = map(Path, sys.argv[4:6])
log = log_file.read_text(errors="replace")
binary = project / "Binaries/IOS/NZMCharacterViewer"

def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()

def apple_version(number):
    return f"{number >> 16}.{(number >> 8) & 255}.{number & 255}"

mach_o = {}
if binary.is_file():
    with binary.open("rb") as stream:
        header = stream.read(32)
        if len(header) == 32:
            magic, cpu, subtype, filetype, commands, size, flags, reserved = struct.unpack("<IiiIIIII", header)
            mach_o.update(magic=hex(magic), cpu_type=hex(cpu), file_type=filetype)
            if magic == 0xFEEDFACF:
                for _ in range(commands):
                    position = stream.tell()
                    command, length = struct.unpack("<II", stream.read(8))
                    if length < 8:
                        raise ValueError("Invalid Mach-O load-command size")
                    if command == 0x32:  # LC_BUILD_VERSION
                        platform, minimum, sdk, tools = struct.unpack("<IIII", stream.read(16))
                        mach_o.update(platform_id=platform, platform="iOS" if platform == 2 else str(platform),
                                      minimum_os=apple_version(minimum), sdk=apple_version(sdk))
                    stream.seek(position + length)

target_valid = (mach_o.get("magic") == "0xfeedfacf" and
                mach_o.get("cpu_type") == "0x100000c" and
                mach_o.get("file_type") == 2 and mach_o.get("platform_id") == 2)
unexpected_finalization = any(token in log for token in
    ("Running xcodebuild", "GatherProvisioningInputs", "CodeSign ", "Failed to finalize the .app"))
succeeded = exit_code == 0 and target_valid and not unexpected_finalization
source_hashes = {str(path.relative_to(project)): digest(path)
                 for path in sorted((project / "Source").rglob("*"))
                 if path.is_file() and path.suffix in (".h", ".cpp", ".cs")}
report = {
    "status": "passed_unsigned_compile_link_only" if succeeded else "failed",
    "recorded_utc": datetime.now(timezone.utc).isoformat(),
    "project": str(project), "engine": str(engine),
    "engine_build": json.loads((engine / "Engine/Build/Build.version").read_text()),
    "target": "NZMCharacterViewer", "platform": "IOS", "configuration": "Development",
    "environment": {"UBT_NO_POST_DEPLOY": "true", "UE_BUILD_FROM_XCODE": "1"},
    "exit_code": exit_code, "log": str(log_file), "log_sha256": digest(log_file),
    "compile_actions_in_this_run": len(re.findall(r"^\[\d+/\d+\] Compile", log, re.MULTILINE)),
    "link_actions_in_this_run": len(re.findall(r"^\[\d+/\d+\] Link", log, re.MULTILINE)),
    "incremental_build": "Target uses valid cached compile/link products when inputs have not changed.",
    "binary": {"path": str(binary), "size_bytes": binary.stat().st_size if binary.exists() else None,
               "sha256": digest(binary) if binary.is_file() else None, "mach_o": mach_o,
               "verified_ios_arm64_executable": target_valid},
    "xcode_signing_finalization_observed": unexpected_finalization,
    "signing_performed": False, "asset_cooking_performed": False,
    "installable_ipa_produced": False, "device_deployment_performed": False,
    "source_sha256": source_hashes,
    "engine_source_basis": [
        "Engine/Source/Programs/UnrealBuildTool/Platform/IOS/UEDeployIOS.cs:692",
        "Engine/Source/Programs/UnrealBuildTool/Platform/Apple/AppleToolChain.cs:1268"
    ],
    "scope": "C++ target compilation/linking only; does not verify iOS asset cooking, signing or device runtime."
}
report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print("iOS unsigned compile/link verification:", report["status"])
print("Log:", log_file)
print("Record:", report_file)
if not succeeded:
    raise SystemExit(exit_code or 1)
PY

echo "iOS 编译/链接验证通过；未生成可安装 IPA，未签名或装机。"
