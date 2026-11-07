#!/usr/bin/env python3
"""Write local signing selections where UE's Xcode subprocess can read them."""
import os
import json
from pathlib import Path
import plistlib
import re
import subprocess

PROJECT = Path(__file__).resolve().parent.parent
BEGIN = "; BEGIN VIEWER LOCAL IOS SIGNING"
END = "; END VIEWER LOCAL IOS SIGNING"


def main():
    names = ("IOS_TEAM_ID", "IOS_BUNDLE_ID", "IOS_SIGNING_IDENTITY_SHA1", "IOS_PROFILE_UUID")
    values = {name: os.environ.get(name, "") for name in names}
    if any(not value or any(c in value for c in "\r\n[];") for value in values.values()):
        raise SystemExit("Missing or invalid local signing selection; run ios_device_preflight.py first")
    readiness = json.loads((PROJECT / "Saved/DevicePreparation/Readiness.json").read_text())
    selected = next(row for row in readiness["profiles"] if row["uuid"] == values["IOS_PROFILE_UUID"])
    profile = plistlib.loads(subprocess.run(
        ["security", "cms", "-D", "-i", selected["path"]], capture_output=True, check=True).stdout)
    automatic = "True" if profile.get("IsXcodeManaged", False) else "False"
    block = f"""{BEGIN}
[/Script/IOSRuntimeSettings.IOSRuntimeSettings]
BundleIdentifier={values['IOS_BUNDLE_ID']}
bAutomaticSigning={automatic}
IOSTeamID={values['IOS_TEAM_ID']}

[/Script/MacTargetPlatform.XcodeProjectSettings]
CodeSigningTeam={values['IOS_TEAM_ID']}
BundleIdentifier={values['IOS_BUNDLE_ID']}
ApplicationDisplayName=人物查看器
bUseAutomaticCodeSigning={automatic}
IOSSigningIdentity={values['IOS_SIGNING_IDENTITY_SHA1']}
IOSProvisioningProfile={values['IOS_PROFILE_UUID']}
{END}
"""
    # Modern project generation reads the bundle ID and shared signing mode
    # from Mac config, then platform signing fields from IOS config.
    for platform in ("IOS", "Mac"):
        path = PROJECT / f"Saved/Config/{platform}/Engine.ini"
        original = path.read_text() if path.exists() else ""
        original = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", "", original, flags=re.S)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(original.rstrip() + "\n\n" + block)
        path.chmod(0o600)
    print("Prepared local signing overrides in ignored Saved/Config (Xcode-managed profile: " + automatic + ")")


if __name__ == "__main__":
    main()
