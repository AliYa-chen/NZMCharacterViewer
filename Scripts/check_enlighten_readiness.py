"""Inventory real Enlighten inputs without loading SDK code or changing UE.

File presence is not license validity, binary compatibility, or working GI.
Only the JSON report is written. No provider/stub or engine setting is created.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re


def inspect(engine_root, sdk_root=None, license_file=None):
    engine = Path(engine_root).expanduser().resolve()
    sdk = Path(sdk_root).expanduser().resolve() if sdk_root else None
    license_path = Path(license_file).expanduser().resolve() if license_file else None
    blockers = []
    report = {'checked_utc': datetime.now(timezone.utc).isoformat(),
              'scope': 'File inventory only; no SDK execution, license validation, compilation or rendering.',
              'engine_root': str(engine), 'sdk_root': str(sdk) if sdk else None,
              'host': platform.system(), 'blockers': blockers,
              'runtime_integrated': None, 'runtime_integration_check': 'not_performed'}
    version_file = engine / 'Engine/Build/Build.version'
    version = json.loads(version_file.read_text()) if version_file.is_file() else None
    report['engine_version'] = version
    report['installed_engine_build'] = (engine / 'Engine/Build/InstalledBuild.txt').is_file()
    if not version or version.get('MajorVersion') != 5 or version.get('MinorVersion') not in (7, 8):
        blockers.append({'code': 'ENGINE_BASE_SUPPORT_UNVERIFIED',
                         'detail': 'The checked 4.04 installation guide lists UE5.7.0 through UE5.8.0; this engine base needs separate vendor confirmation.'})
    required_source = [engine / 'Engine/Source/Runtime/Renderer/Private/DeferredShadingRenderer.cpp']
    generators = [engine / 'GenerateProjectFiles.bat', engine / 'GenerateProjectFiles.sh']
    report['source_engine_files'] = {str(path): path.is_file() for path in required_source + generators}
    if not all(path.is_file() for path in required_source) or not any(path.is_file() for path in generators):
        blockers.append({'code': 'ENGINE_SOURCE_INCOMPLETE',
                         'detail': 'Official UE integration modifies and rebuilds engine sources; the inspected engine tree lacks required source/build entry points.'})
    if version and version.get('PatchVersion', 0):
        blockers.append({'code': 'HOTFIX_MERGE_UNVERIFIED',
                         'detail': 'The official 4.04 base includes UE5.8.0. A hotfix engine needs its matching vendor package or a verified source merge; this check does not establish that merge.'})
    files = []
    if sdk and sdk.is_dir():
        files = sorted(path for path in sdk.rglob('*') if path.is_file())
    else:
        blockers.append({'code': 'SDK_ROOT_MISSING',
                         'detail': 'Supply an extracted, licensed Enlighten UE5 Base package with --sdk-root; game shader dumps are not an SDK.'})
    headers = [path for path in files if path.suffix.lower() in ('.h', '.hpp')]
    runtime_name = re.compile(r'(?i)enlighten|geobase|geocore')
    libraries = [path for path in files if path.suffix.lower() in ('.a', '.lib', '.dll', '.dylib', '.so')
                 and runtime_name.search(path.name)]
    scripts = [path for path in files if path.name in ('BuildAndInstallEnlighten.bat', 'BuildAndInstallEnlighten.sh')]
    tools = [path for path in files if path.name.casefold() in ('geoprecomp2.exe', 'geoconvert.exe', 'geoprecompmonitor.exe')]
    report['sdk_inventory'] = {
        'file_count': len(files),
        'headers': [str(path) for path in headers],
        'runtime_library_name_matches': [str(path) for path in libraries],
        'integration_build_scripts': [str(path) for path in scripts],
        'windows_precompute_tools': [str(path) for path in tools],
        'ios_library_candidates': [str(path) for path in libraries
                                   if re.search(r'(?i)ios|iphone', str(path.relative_to(sdk)))],
        'binary_architecture_and_platform_validation': 'not_performed',
        'note': 'Names are actual files found; header/library presence alone does not authenticate a vendor package.'}
    if not headers:
        blockers.append({'code': 'SDK_HEADERS_MISSING', 'detail': 'No SDK C/C++ headers found in the selected SDK tree.'})
    if not libraries:
        blockers.append({'code': 'RUNTIME_LIBRARIES_MISSING', 'detail': 'No Enlighten/GeoBase/GeoCore runtime library files found.'})
    if not scripts:
        blockers.append({'code': 'UE_INTEGRATION_OVERLAY_UNVERIFIED',
                         'detail': 'No official BuildAndInstallEnlighten script found; a standalone SDK still requires a UE integration implementation.'})
    if not tools:
        blockers.append({'code': 'PRECOMPUTE_TOOLS_MISSING', 'detail': 'No documented Windows precompute executables found in the selected SDK tree.'})
    report['license_file'] = {'path': str(license_path) if license_path else None,
                              'present_nonempty': bool(license_path and license_path.is_file() and license_path.stat().st_size),
                              'validity': 'not_verified_by_Enlighten_Precompute',
                              'content_read': False,
                              'vendor_filename': 'not_published_in_public_documentation'}
    if not report['license_file']['present_nonempty']:
        blockers.append({'code': 'LICENSE_FILE_MISSING',
                         'detail': 'Supply the vendor-issued precompute license file with --license-file. No license filename or license approval is assumed.'})
    if platform.system() != 'Windows':
        blockers.append({'code': 'WINDOWS_PRECOMPUTE_HOST_REQUIRED',
                         'detail': 'Official precompute tools/libraries require 64-bit Windows. An external Windows build host may satisfy this; it is not verified here.'})
    report['remaining_validation'] = [
        'Vendor confirmation of the selected UE release/hotfix and target SDK versions.',
        'A valid precompute license accepted by the actual vendor tool.',
        'iOS arm64/Metal library and shader compilation, linkage, cook and on-device execution.',
        'Explicit vendor support for macOS Editor/runtime if preserving the current Mac target.',
        'Authored environment geometry, successful precompute data, runtime updates and probe sampling.',
        'Rendered regression with SSR enabled; SSR and Enlighten GI are separate systems.']
    report['status'] = 'blocked_missing_inputs' if blockers else 'files_present_requires_vendor_build_and_render_validation'
    return report


def main():
    project = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine-root', type=Path,
                        default=Path(os.environ.get('UE_ROOT', str(Path.home() / 'UE/UE_5.8'))))
    parser.add_argument('--sdk-root', type=Path)
    parser.add_argument('--license-file', type=Path)
    parser.add_argument('--report', type=Path, default=project / 'Saved/FullRendering/EnlightenReadiness.json')
    args = parser.parse_args()
    report = inspect(args.engine_root, args.sdk_root, args.license_file)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': report['status'], 'blockers': report['blockers'], 'report': str(args.report)}, indent=2))
    return 2 if report['blockers'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
