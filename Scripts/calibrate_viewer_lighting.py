"""Reversible calibration of the existing viewer environment data assets.

Use Unreal's Python commandlet with -LightingCalibrationMode=audit (default)
or =apply. Standard-stage lights are adjusted in place: Fill02 becomes the
front key, Fill01 the front fill, and Main the rear rim. Custom-stage lights
and every HDR cubemap/intensity are preserved. All four environments receive
the same modest AO/bloom and neutral contrast/saturation settings.

Quick tuning: -LightingKeyLumens=80 -LightingFillLumens=25
-LightingRimLumens=80; corresponding environment variables are
NZM_LIGHTING_KEY_LUMENS, NZM_LIGHTING_FILL_LUMENS, NZM_LIGHTING_RIM_LUMENS.
Other options use the same convention, e.g. -LightingAORadius=15 or
NZM_LIGHTING_AO_RADIUS=15. These are scene-centimetre authoring values;
SourceRadius controls specular size and does not promise mobile PCSS shadows.

Close this project's Editor/viewer before exact byte restoration:
    python3 Scripts/calibrate_viewer_lighting.py --restore-files
No source assets or reimport is needed. A successful apply still requires
rendered A/B inspection and iOS validation.
"""
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import traceback


VERSION = 1
ENVIRONMENTS = (
    '/Game/Viewer/Environment/DA_Environment',
    '/Game/Viewer/Environment/DA_Environment_Queen',
    '/Game/Viewer/Environment/DA_Environment_Lucia',
    '/Game/Viewer/Environment/DA_Environment_Naimei',
)
LIGHT_FIELDS = ('id', 'kind', 'location', 'rotation', 'color', 'intensity',
                'radius', 'inner_cone', 'outer_cone', 'source_radius',
                'source_length', 'temperature', 'use_temperature',
                'cast_shadows', 'inverse_squared_falloff', 'falloff_exponent',
                'specular_scale', 'shadow_bias', 'intensity_units',
                'lighting_channel_mask')
POST_FIELDS = ('ambient_occlusion_intensity', 'ambient_occlusion_radius',
               'ambient_occlusion_radius_in_ws', 'ambient_occlusion_power',
               'ambient_occlusion_bias', 'bloom_intensity', 'bloom_threshold',
               'vignette_intensity', 'color_contrast', 'color_saturation',
               'color_contrast_midtones', 'auto_exposure_bias',
               'auto_exposure_method', 'auto_exposure_apply_physical_camera_exposure')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n',
                         encoding='utf-8')
    temporary.replace(path)


def package_files(project, package):
    if package not in ENVIRONMENTS:
        raise ValueError('Calibration is limited to the four known environments.')
    stem = project / 'Content' / package[len('/Game/'):]
    files = [stem.with_suffix(ext) for ext in ('.uasset', '.uexp', '.ubulk')]
    if not files[0].is_file():
        raise RuntimeError('Package file missing: ' + str(files[0]))
    return [file for file in files if file.is_file()]


def validated_manifest(project, backup):
    manifest = json.loads((backup / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('version') != VERSION:
        raise ValueError('Unsupported lighting backup manifest version.')
    permitted = {'Content/' + package[len('/Game/'):] + extension
                 for package in ENVIRONMENTS for extension in ('.uasset', '.uexp', '.ubulk')}
    required = {'Content/' + package[len('/Game/'):] + '.uasset' for package in ENVIRONMENTS}
    seen = set()
    for row in manifest['files']:
        relative = row['relative']
        if relative not in permitted or relative in seen:
            raise ValueError('Unexpected/duplicate backup target: ' + relative)
        seen.add(relative)
        source = backup / relative
        if not source.is_file() or digest(source) != row['sha256']:
            raise RuntimeError('Original backup hash mismatch: ' + str(source))
    if not required.issubset(seen):
        raise RuntimeError('Original backup does not cover all environment package files.')
    return manifest


def restore_files(project):
    """Restore on disk outside Unreal, after checking the entire backup."""
    if 'unreal' in sys.modules:
        raise RuntimeError('Restore package bytes with ordinary Python, outside Unreal.')
    if sys.platform == 'win32':
        listing = subprocess.run(
            ['powershell', '-NoProfile', '-Command',
             'Get-CimInstance Win32_Process | Select-Object -ExpandProperty CommandLine'],
            check=True, capture_output=True, text=True).stdout
    else:
        listing = subprocess.run(['ps', '-axo', 'args='], check=True,
                                 capture_output=True, text=True).stdout
    project_file = str(project / 'NZMCharacterViewer.uproject')
    if any('UnrealEditor' in line and project_file in line
           for line in listing.splitlines()):
        raise RuntimeError('Close this project in Unreal Editor and its viewer first.')
    directory = project / 'Saved/LightingCalibration'
    backup = directory / 'OriginalContent'
    manifest = validated_manifest(project, backup)
    restored = []
    removed_sidecars = []
    original_files = {row['relative'] for row in manifest['files']}
    for package in ENVIRONMENTS:
        for extension in ('.uexp', '.ubulk'):
            relative = 'Content/' + package[len('/Game/'):] + extension
            target = project / relative
            if relative not in original_files and target.is_file():
                target.unlink()
                removed_sidecars.append(relative)
    for row in manifest['files']:
        target = project / row['relative']
        shutil.copy2(backup / row['relative'], target)
        actual = digest(target)
        if actual != row['sha256']:
            raise RuntimeError('Restored file hash mismatch: ' + str(target))
        restored.append({'relative': row['relative'], 'sha256': actual})
    report = {'version': VERSION, 'mode': 'restore', 'status': 'restored',
              'finished_utc': datetime.now(timezone.utc).isoformat(), 'files': restored,
              'removed_nonoriginal_sidecars': removed_sidecars}
    write_json(directory / 'restore_report.json', report)
    print('Restored', len(restored), 'original environment package files.')


def options(command_line):
    def option(argument, variable, default):
        match = re.search(r'(?:^|\s)-' + re.escape(argument) + r'=([^\s]+)', command_line,
                          re.IGNORECASE)
        return match[1].strip('"\'') if match else os.environ.get(variable, default)

    result = {'mode': str(option('LightingCalibrationMode', 'NZM_LIGHTING_MODE', 'audit')).lower()}
    specifications = {
        'key_lumens': ('LightingKeyLumens', 80., 0., 100000.),
        'fill_lumens': ('LightingFillLumens', 25., 0., 100000.),
        'rim_lumens': ('LightingRimLumens', 80., 0., 100000.),
        'ao_intensity': ('LightingAOIntensity', .45, 0., 1.),
        'ao_radius': ('LightingAORadius', 15., .1, 500.),
        'ao_power': ('LightingAOPower', 1.4, .1, 8.),
        'ao_bias': ('LightingAOBias', 1.5, 0., 10.),
        'bloom_intensity': ('LightingBloomIntensity', .08, 0., 10.),
        'bloom_threshold': ('LightingBloomThreshold', 1., -1., 100.),
        'vignette_max': ('LightingVignetteMax', .18, 0., 1.),
    }
    for name, (argument, default, minimum, maximum) in specifications.items():
        value = float(option(argument, 'NZM_LIGHTING_' + name.upper(), default))
        if not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError(f'{argument} must be finite and between {minimum} and {maximum}.')
        result[name] = value
    if result['mode'] not in ('audit', 'apply'):
        raise ValueError('Use audit/apply in Unreal, or --restore-files outside Unreal.')
    return result


def serial(value):
    if isinstance(value, (str, bool, int, float)) or value is None:
        return value
    for names in (('x', 'y', 'z', 'w'), ('x', 'y', 'z'),
                  ('pitch', 'yaw', 'roll'), ('r', 'g', 'b', 'a')):
        if all(hasattr(value, name) for name in names):
            return {name: float(getattr(value, name)) for name in names}
    return value.get_path_name() if hasattr(value, 'get_path_name') else str(value)


def snapshot(asset):
    post = asset.get_editor_property('post_process')
    values = {}
    for name in POST_FIELDS:
        values[name] = serial(post.get_editor_property(name))
        values['override_' + name] = bool(post.get_editor_property('override_' + name))
    return {'path': asset.get_path_name(),
            'cubemap': serial(asset.get_editor_property('cubemap')),
            'sky_intensity': float(asset.get_editor_property('sky_intensity')),
            'sky_color': serial(asset.get_editor_property('sky_color')),
            'lights': [{name: serial(light.get_editor_property(name)) for name in LIGHT_FIELDS}
                       for light in asset.get_editor_property('lights')],
            'post_process': values}


def backup_originals(project, directory, before):
    backup = directory / 'OriginalContent'
    manifest_path = backup / 'manifest.json'
    if manifest_path.exists():
        validated_manifest(project, backup)
        if not (directory / 'original_parameters.json').is_file():
            raise RuntimeError('Original parameter JSON is missing; existing byte backup retained.')
        return
    # Never replace an original, even after interrupted first-time setup.
    rows = []
    for package in ENVIRONMENTS:
        for source in package_files(project, package):
            relative = source.relative_to(project).as_posix()
            target = backup / relative
            original_hash = digest(source)
            if target.exists():
                if digest(target) != original_hash:
                    raise RuntimeError('Incomplete backup differs from current package: ' + str(target))
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            if digest(target) != original_hash:
                raise RuntimeError('Cannot verify original backup: ' + str(target))
            rows.append({'relative': relative, 'sha256': original_hash,
                         'bytes': source.stat().st_size})
    original_json = directory / 'original_parameters.json'
    if original_json.exists():
        raise RuntimeError('Original parameter JSON exists without a manifest; inspect incomplete backup.')
    write_json(original_json, {'version': VERSION, 'captured_utc': datetime.now(timezone.utc).isoformat(),
                               'environments': before})
    write_json(manifest_path, {'version': VERSION, 'files': rows})
    validated_manifest(project, backup)


def calibrate_lights(unreal, asset, parameters):
    recipes = {
        'SpotLight_Fill02': {'role': 'key', 'location': (-200., -280., 280.),
                            'target': (0., 0., 110.), 'outer': 32., 'inner': 14.,
                            'source_radius': 60., 'color': (1., .98, .95), 'shadows': True,
                            'lighting_channel_mask': 1},
        'SpotLight_Fill01': {'role': 'fill', 'location': (240., -180., 190.),
                            'target': (0., 0., 110.), 'outer': 40., 'inner': 18.,
                            'source_radius': 60., 'color': (.91, .95, 1.), 'shadows': False,
                            'lighting_channel_mask': 2},
        'SpotLight_Main': {'role': 'rim', 'location': (-170., 380., 300.),
                          'target': (0., 0., 130.), 'outer': 30., 'inner': 12.,
                          'source_radius': 45., 'color': (1., .98, .95), 'shadows': False,
                          'lighting_channel_mask': 1},
    }
    lights = list(asset.get_editor_property('lights'))
    names = [str(light.get_editor_property('id')) for light in lights]
    if len(names) != len(recipes) or set(names) != set(recipes):
        raise RuntimeError('Standard-stage light IDs changed; refusing to edit unknown lights: ' + str(names))
    for light in lights:
        name = str(light.get_editor_property('id'))
        if str(light.get_editor_property('kind')) != 'Spot':
            raise RuntimeError('Expected existing Spot light: ' + name)
        recipe = recipes[name]
        location, target = recipe['location'], recipe['target']
        direction = [target[index] - location[index] for index in range(3)]
        pitch = math.degrees(math.atan2(direction[2], math.hypot(direction[0], direction[1])))
        yaw = math.degrees(math.atan2(direction[1], direction[0]))
        fields = {'location': unreal.Vector(*location),
                  'rotation': unreal.Rotator(pitch=pitch, yaw=yaw, roll=0.),
                  'color': unreal.LinearColor(*recipe['color'], 1.),
                  'intensity': parameters[recipe['role'] + '_lumens'],
                  'intensity_units': 2, 'inverse_squared_falloff': True,
                  'radius': 1200., 'outer_cone': recipe['outer'], 'inner_cone': recipe['inner'],
                  'source_radius': recipe['source_radius'], 'source_length': 0.,
                  'use_temperature': False, 'specular_scale': 1.,
                  'cast_shadows': recipe['shadows'], 'shadow_bias': .2,
                  'lighting_channel_mask': recipe['lighting_channel_mask']}
        for field, value in fields.items():
            light.set_editor_property(field, value)
    asset.set_editor_property('lights', lights)


def calibrate_post(unreal, asset, parameters):
    post = asset.get_editor_property('post_process')
    fields = {'ambient_occlusion_intensity': parameters['ao_intensity'],
              'ambient_occlusion_radius': parameters['ao_radius'],
              'ambient_occlusion_radius_in_ws': True,
              'ambient_occlusion_power': parameters['ao_power'],
              'ambient_occlusion_bias': parameters['ao_bias'],
              'bloom_intensity': parameters['bloom_intensity'],
              'bloom_threshold': parameters['bloom_threshold'],
              'vignette_intensity': min(float(post.get_editor_property('vignette_intensity')),
                                        parameters['vignette_max']),
              'color_contrast': unreal.Vector4(1., 1., 1., 1.),
              'color_saturation': unreal.Vector4(1., 1., 1., 1.),
              'color_contrast_midtones': unreal.Vector4(1., 1., 1., 1.)}
    for name, value in fields.items():
        post.set_editor_property(name, value)
        post.set_editor_property('override_' + name, True)
    asset.set_editor_property('post_process', post)


def run():
    import unreal
    project = Path(unreal.Paths.project_dir()).resolve()
    directory = project / 'Saved/LightingCalibration'
    parameters = options(unreal.SystemLibrary.get_command_line())
    report = {'version': VERSION, 'mode': parameters['mode'], 'parameters': parameters,
              'started_utc': datetime.now(timezone.utc).isoformat(), 'environments': [],
              'status': 'running', 'limitations': [
                  'Lighting values are viewer calibration, not recovered source-shader semantics.',
                  'Only the existing standard-stage lights move; custom-stage lights and all HDRs are preserved.',
                  'Source radius does not enable mobile PCSS; shadow softness needs rendered verification.',
                  'Standard-stage only key casts shadows; fill/rim remain unshadowed photographic fills.',
                  'Compilation success does not validate rendered quality or physical iOS performance.']}
    report_path = directory / (parameters['mode'] + '_report.json')
    try:
        assets = [unreal.load_asset(package) for package in ENVIRONMENTS]
        if any(not isinstance(asset, unreal.ViewerEnvironment) for asset in assets):
            raise RuntimeError('One or more existing ViewerEnvironment assets failed to load.')
        before = [snapshot(asset) for asset in assets]
        if parameters['mode'] == 'apply':
            # Every original is verified before the first UObject mutation/save.
            backup_originals(project, directory, before)
        for index, asset in enumerate(assets):
            row = {'package': ENVIRONMENTS[index], 'before': before[index]}
            report['environments'].append(row)
            if parameters['mode'] == 'apply':
                if index == 0:
                    calibrate_lights(unreal, asset, parameters)
                calibrate_post(unreal, asset, parameters)
                row['after'] = snapshot(asset)
                if row['after']['cubemap'] != row['before']['cubemap'] or any(
                        row['after'][name] != row['before'][name] for name in ('sky_intensity', 'sky_color')):
                    raise RuntimeError('Unexpected sky change: ' + ENVIRONMENTS[index])
                if index and row['after']['lights'] != row['before']['lights']:
                    raise RuntimeError('Unexpected custom-stage light change: ' + ENVIRONMENTS[index])
                if not unreal.EditorAssetLibrary.save_loaded_asset(asset):
                    raise RuntimeError('Environment did not save: ' + ENVIRONMENTS[index])
                row['files_after'] = [{'relative': file.relative_to(project).as_posix(),
                                       'sha256': digest(file)}
                                      for file in package_files(project, ENVIRONMENTS[index])]
                row['status'] = 'saved_requires_rendered_validation'
            else:
                row['status'] = 'audited_no_content_changes'
            write_json(report_path, report)
        report['status'] = 'complete_requires_rendered_validation' if parameters['mode'] == 'apply' else 'audited'
    except Exception:
        report['status'] = 'failed'
        report['error'] = traceback.format_exc()
        raise
    finally:
        report['finished_utc'] = datetime.now(timezone.utc).isoformat()
        write_json(report_path, report)
        unreal.log('LIGHTING_CALIBRATION ' + json.dumps({'mode': parameters['mode'],
                   'status': report['status'], 'report': str(report_path)}))


if __name__ == '__main__':
    if '--restore-files' in sys.argv:
        restore_files(Path(__file__).resolve().parent.parent)
    else:
        run()
