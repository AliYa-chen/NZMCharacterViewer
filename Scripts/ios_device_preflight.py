#!/usr/bin/env python3
"""Read paired-device/display and existing local signing metadata only.

Never exports private keys, changes a keychain, contacts an Apple account,
registers an identifier/device, or creates a certificate/profile.
"""
import argparse
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess

PROJECT = Path(__file__).resolve().parent.parent
SAVED = PROJECT / "Saved/DevicePreparation"


def command(arguments, input_bytes=None):
    return subprocess.run(arguments, input=input_bytes, capture_output=True, check=True).stdout


def inspect_signing(device_udid, bundle, team, identity):
    text = command(["security", "find-identity", "-v", "-p", "codesigning"]).decode()
    valid = {m[1] for m in re.finditer(r'\b([A-F0-9]{40})\s+"', text)}
    pem_data = command(["security", "find-certificate", "-a", "-c", "Apple Development", "-p"])
    certificates = []
    for pem in re.findall(rb"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", pem_data, re.S):
        der = command(["/usr/bin/openssl", "x509", "-outform", "DER"], pem)
        fingerprint = hashlib.sha1(der).hexdigest().upper()
        if fingerprint not in valid:
            continue
        subject = command(["/usr/bin/openssl", "x509", "-noout", "-subject", "-enddate", "-nameopt", "RFC2253"], pem).decode()
        org = re.search(r"(?:^|,)OU=([^,\n]+)", subject)
        end = re.search(r"notAfter=(.+)", subject)
        certificates.append({"type": "Apple Development", "sha1": fingerprint,
                             "team_id": org[1] if org else None,
                             "expires": end[1] if end else None})
    selected = [c for c in certificates if c["team_id"] == team and c["sha1"] == identity.upper()]
    profiles = []
    import plistlib
    for folder in (Path.home() / "Library/MobileDevice/Provisioning Profiles",
                   Path.home() / "Library/Developer/Xcode/UserData/Provisioning Profiles"):
        for path in sorted(folder.glob("*.mobileprovision")):
            data = plistlib.loads(command(["security", "cms", "-D", "-i", str(path)]))
            entitlements = data.get("Entitlements", {})
            app_id = entitlements.get("application-identifier", "")
            pattern = app_id.split(".", 1)[1] if "." in app_id else ""
            expiry = data.get("ExpirationDate")
            expired = not expiry or expiry.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc)
            matches_certificate = any(hashlib.sha1(c).hexdigest().upper() == identity.upper()
                                      for c in data.get("DeveloperCertificates", []))
            compatible = bool(selected) and team in data.get("TeamIdentifier", []) and not expired \
                and fnmatch.fnmatchcase(bundle, pattern) and device_udid in data.get("ProvisionedDevices", []) \
                and matches_certificate and entitlements.get("get-task-allow") is True
            profiles.append({"uuid": data.get("UUID"), "path": str(path),
                             "team_ids": data.get("TeamIdentifier", []),
                             "app_identifier_pattern": app_id,
                             "covers_bundle": fnmatch.fnmatchcase(bundle, pattern),
                             "covers_device": device_udid in data.get("ProvisionedDevices", []),
                             "matches_existing_identity": matches_certificate,
                             "development_debugging_allowed": entitlements.get("get-task-allow"),
                             "expires_utc": expiry.isoformat() + "Z" if expiry else None,
                             "expired": expired, "compatible": compatible})
    return certificates, selected, profiles


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-profile", action="store_true")
    options = parser.parse_args()
    required = ("IOS_DEVICE_ID", "IOS_TEAM_ID", "IOS_BUNDLE_ID", "IOS_SIGNING_IDENTITY_SHA1")
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        parser.error("Set local device/signing variables first: " + ", ".join(missing) +
                     "; see Scripts/ios_device.env.example")
    device, team, bundle, identity = (os.environ[key] for key in required)
    SAVED.mkdir(parents=True, exist_ok=True)
    for subcommand, filename in (("details", "device-details.json"), ("displays", "display-info.json")):
        command(["xcrun", "devicectl", "device", "info", subcommand, "--device", device,
                 "--timeout", "20", "--quiet", "--json-output", str(SAVED / filename)])
    details = json.loads((SAVED / "device-details.json").read_text())["result"]
    displays = json.loads((SAVED / "display-info.json").read_text())["result"]
    certificates, selected, profiles = inspect_signing(details["hardwareProperties"]["udid"], bundle, team, identity)
    compatible = [p for p in profiles if p["compatible"]]
    blockers = []
    if len(selected) != 1:
        blockers.append("The specified existing Apple Development identity is not uniquely available for this Team.")
    if details["deviceProperties"].get("developerModeStatus") != "enabled":
        blockers.append("Developer Mode is not enabled on the selected device.")
    if not compatible:
        blockers.append("No existing unexpired development provisioning profile covers this Bundle ID, device and certificate.")
    report = {"recorded_utc": datetime.now(timezone.utc).isoformat(),
              "device_id": device, "product_type": details["hardwareProperties"].get("productType"),
              "os_version": details["deviceProperties"].get("osVersionNumber"),
              "developer_mode": details["deviceProperties"].get("developerModeStatus"),
              "connection_transport": details["connectionProperties"].get("transportType"),
              "requested_bundle_id": bundle, "selected_team_id": team,
              "existing_signing_certificates": certificates,
              "profiles": profiles, "ready_for_manual_signing": not blockers,
              "blockers": blockers,
              "display_state": displays,
              "private_keys_exported": False, "accounts_or_certificates_created": False,
              "apple_portal_updates_performed": False, "app_installed": False}
    (SAVED / "Readiness.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    env_path = SAVED / "signing-selection.env"
    if not blockers:
        profile = sorted(compatible, key=lambda p: p["expires_utc"], reverse=True)[0]
        values = {"IOS_TEAM_ID": team, "IOS_BUNDLE_ID": bundle, "IOS_DEVICE_ID": device,
                  "IOS_SIGNING_IDENTITY_SHA1": identity, "IOS_PROFILE_UUID": profile["uuid"]}
        env_path.write_text("\n".join(f"export {k}={shlex.quote(v)}" for k, v in values.items()) + "\n")
    elif env_path.exists():
        env_path.unlink()
    print(json.dumps({"ready_for_manual_signing": not blockers, "team": team,
                      "compatible_existing_profiles": len(compatible), "blockers": blockers,
                      "report": str(SAVED / "Readiness.json")}, ensure_ascii=False, indent=2))
    if options.require_profile and blockers:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
