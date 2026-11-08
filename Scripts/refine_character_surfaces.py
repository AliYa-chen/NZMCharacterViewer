"""Conservative, reversible surface calibration for the current Catalog.

Run with Unreal's Python commandlet and -SurfaceRefinementMode=audit (default)
or =apply. Optional -SurfaceRefinementFilter=angela,molly limits character IDs.
Only opaque Default Lit skin/eyes with the known scalar input graph are eligible.
Original colors, normal maps, texture settings, mesh bindings, transparency and
shading models are preserved. No subsurface/Hair shader or packed channel is
inferred. The native normal-curvature filter reduces geometric specular aliasing;
it does not recover spatial skin roughness or subsurface scattering.

Close the viewer/editor before restoring exact package bytes with:
    python3 Scripts/refine_character_surfaces.py --restore-files
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile


VERSION = 1
MARKER = 'Viewer surface calibration v1'
SKIN_SPECULAR = 0.35  # UE dielectric F0 = Specular * .08 -> .028.
EYE_SPECULAR = 0.5
EYE_ROUGHNESS = 0.045
CATALOG = '/Game/Viewer/Data/DA_ViewerCatalog'
PATH_PATTERN = re.compile(r'(/Game/[A-Za-z0-9_./-]+)')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def require_closed_project(project):
    if 'unreal' in sys.modules:
        raise RuntimeError('Restore package bytes with ordinary Python, outside Unreal.')
    if sys.platform == 'win32':
        command = ['powershell', '-NoProfile', '-Command',
                   'Get-CimInstance Win32_Process | Select-Object -ExpandProperty CommandLine']
    else:
        command = ['ps', '-axo', 'args=']
    listing = subprocess.run(command, check=True, capture_output=True, text=True).stdout
    project_text = str(project).replace('\\', '/').casefold()
    executable = re.compile(
        r'(?:^|[/\\])(?P<name>UnrealEditor(?:-Cmd)?|NZMCharacterViewer(?:-[\w-]+)?)'
        r'(?:\.exe)?(?=[\s\"\']|$)', re.IGNORECASE)
    for line in listing.splitlines():
        match = executable.search(line)
        if match and (project_text in line.replace('\\', '/').casefold()
                      or match['name'].casefold().startswith('nzmcharacterviewer')):
            raise RuntimeError('Close this project in Unreal Editor and its viewer first.')


def restoration_plan(project):
    """Validate the complete manifest and cache verified bytes before any write."""
    project = project.resolve()
    characters = project / 'Content/Characters'
    backup_dir = project / 'Saved/SurfaceRefinement/Original'
    if characters.resolve() != characters or backup_dir.resolve() != backup_dir:
        raise ValueError('Content/Characters and the original backup must not use symlinks.')
    manifest_path = backup_dir / 'manifest.json'
    if manifest_path.resolve() != manifest_path:
        raise ValueError('The original manifest must not be a symlink.')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    rows = manifest.get('files') if isinstance(manifest, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError('Original manifest must contain a nonempty files list.')
    plan, seen = [], set()
    extensions = {'.uasset', '.uexp', '.ubulk'}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Malformed original manifest file record.')
        text, expected = row.get('relative'), row.get('sha256')
        if not isinstance(text, str) or any(char in text for char in ('\\', ':', '\0')):
            raise ValueError('Backup paths must be canonical project-relative POSIX paths.')
        relative = PurePosixPath(text)
        if (relative.is_absolute() or relative.as_posix() != text
                or relative.parts[:2] != ('Content', 'Characters')
                or len(relative.parts) < 3 or '..' in relative.parts
                or relative.suffix not in extensions):
            raise ValueError('Backup target must be a character package file: ' + text)
        identity = text.casefold()
        if identity in seen:
            raise ValueError('Duplicate backup target: ' + text)
        seen.add(identity)
        if not isinstance(expected, str) or re.fullmatch(r'[0-9a-fA-F]{64}', expected) is None:
            raise ValueError('Invalid backup SHA-256: ' + text)
        source, target = backup_dir / text, project / text
        if (source.resolve() != source or not source.is_file()
                or not source.resolve().is_relative_to(backup_dir)
                or target.resolve() != target or not target.resolve().is_relative_to(characters)
                or target.exists() and not target.is_file()):
            raise ValueError('Invalid or symlinked backup source/target: ' + text)
        payload = source.read_bytes()
        if hashlib.sha256(payload).hexdigest() != expected.lower():
            raise RuntimeError('Backup hash mismatch: ' + str(source))
        plan.append({'relative': text, 'source': source, 'target': target,
                     'payload': payload, 'sha256': expected.lower()})
    packages = {item['relative'][:-len('.uasset')] for item in plan
                if item['relative'].endswith('.uasset')}
    if any(str(PurePosixPath(item['relative']).with_suffix('')) not in packages for item in plan):
        raise ValueError('Every backed-up sidecar must have its original .uasset in the manifest.')
    removals = []
    for package in sorted(packages):
        for extension in ('.uexp', '.ubulk'):
            text = package + extension
            if text.casefold() in seen:
                continue
            target = project / text
            if (target.resolve() != target or not target.resolve().is_relative_to(characters)
                    or target.exists() and not target.is_file()):
                raise ValueError('Invalid new sidecar target: ' + text)
            if target.exists():
                removals.append(target)
    return plan, removals


def restore_files(project):
    """Restore verified originals outside Unreal after validating every target."""
    project = project.resolve()
    require_closed_project(project)
    plan, removals = restoration_plan(project)
    # No package, sidecar or directory is changed before the full plan succeeds.
    for item in plan:
        target = item['target']
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.' + target.name + '.',
                                             suffix='.surface-restore.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(item['payload'])
            if digest(temporary) != item['sha256']:
                raise RuntimeError('Staged original hash mismatch: ' + str(target))
            shutil.copystat(item['source'], temporary)
            temporary.replace(target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    for target in removals:
        target.unlink()
    report = {'status': 'restored', 'finished_utc': datetime.now(timezone.utc).isoformat(),
              'files': [{'relative': item['relative'], 'sha256': item['sha256']} for item in plan],
              'removed_nonoriginal_sidecars': [path.relative_to(project).as_posix() for path in removals]}
    write_json(project / 'Saved/SurfaceRefinement/restore_report.json', report)
    print('Restored', len(plan), 'original package files.')


def serialized_fields(value):
    """Read soft-reference fields without relying on the reference being loaded."""
    text = value.export_text().strip().strip('()')
    fields, depth, quote, escaped, start = {}, 0, None, False, 0
    for index, char in enumerate(text + ','):
        if escaped:
            escaped = False
        elif quote:
            if char == '\\':
                escaped = True
            elif char == quote:
                quote = None
        elif char in ('"', "'"):
            quote = char
        elif char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
        elif char == ',' and depth == 0:
            key, _, field = text[start:index].partition('=')
            fields[key.strip().lower()] = field.strip()
            start = index + 1
    return fields


def classify(name):
    lower = name.lower()
    if any(token in lower for token in ('hair', 'headwear', 'eyelash', 'tearduc',
                                        '_ao_', '_fx', 'clothes', 'trousers',
                                        'glove', 'stocking', 'skirt', 'dress')):
        return None
    if re.search(r'(?:^|_)eye(?:_|$)', lower):
        return 'eye'
    if re.search(r'(?:^|_)(?:head|body|arm|legs?|skin)(?:_|$)', lower):
        return 'skin'
    return None


def run():
    import unreal
    edit = unreal.MaterialEditingLibrary
    library = unreal.EditorAssetLibrary
    project = Path(unreal.Paths.project_dir()).resolve()
    directory = project / 'Saved/SurfaceRefinement'
    command_line = unreal.SystemLibrary.get_command_line()
    match = re.search(r'-SurfaceRefinementMode=([^\s]+)', command_line)
    mode = match[1].lower() if match else 'audit'
    if mode not in ('audit', 'apply'):
        raise ValueError('Use audit/apply in Unreal; restore package bytes outside Unreal.')
    match = re.search(r'-SurfaceRefinementFilter=([^\s]+)', command_line)
    selected = set(match[1].lower().split(',')) if match else set()
    report = {'version': VERSION, 'mode': mode,
              'started_utc': datetime.now(timezone.utc).isoformat(),
              'parameters': {'skin_specular': SKIN_SPECULAR, 'skin_f0': 0.028,
                             'skin_roughness': 'preserve current scalar, expected 0.40..0.45',
                             'eye_roughness': EYE_ROUGHNESS, 'eye_specular': EYE_SPECULAR},
              'bindings': [], 'materials': [], 'errors': [],
              'limitations': ['No regional skin roughness/SSS or eye refraction is reconstructed.',
                             'Normal curvature filtering uses geometry, not normal-map variance.',
                             'Hair transparency and shader models are unchanged.',
                             'Shader compilation and rendered appearance need separate validation.']}
    manifest_path = directory / 'Original/manifest.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {'files': []}
    backup_index = {row['relative']: row for row in manifest['files']}

    def backup(package):
        if not package.startswith('/Game/'):
            raise RuntimeError('Only project packages can be calibrated.')
        stem = project / 'Content' / package[len('/Game/'):]
        for extension in ('.uasset', '.uexp', '.ubulk'):
            source = stem.with_suffix(extension)
            if not source.exists():
                if extension == '.uasset':
                    raise RuntimeError('Package file is missing: ' + str(source))
                continue
            relative = source.relative_to(project).as_posix()
            if relative not in backup_index:
                destination = directory / 'Original' / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                row = {'relative': relative, 'sha256': digest(source)}
                manifest['files'].append(row)
                backup_index[relative] = row
            elif digest(directory / 'Original' / relative) != backup_index[relative]['sha256']:
                raise RuntimeError('Existing original backup failed hash check.')
        write_json(manifest_path, manifest)

    material_bindings = {}
    catalog = unreal.load_asset(CATALOG)
    if not isinstance(catalog, unreal.ViewerCatalog):
        raise RuntimeError('Current ViewerCatalog is unavailable.')
    for character in catalog.get_editor_property('characters'):
        cid = str(character.get_editor_property('id'))
        if selected and cid.lower() not in selected:
            continue
        for outfit in character.get_editor_property('outfits'):
            for part in outfit.get_editor_property('parts'):
                fields = serialized_fields(part)
                paths = PATH_PATTERN.findall(fields.get('materials', ''))
                # Catalog overrides are authoritative. Fall back to mesh slots
                # only when the Catalog has no material override array.
                if not paths:
                    mesh_paths = PATH_PATTERN.findall(fields.get('mesh', ''))
                    mesh = unreal.load_asset(mesh_paths[0]) if mesh_paths else None
                    if not isinstance(mesh, unreal.SkeletalMesh):
                        raise RuntimeError('Cannot inspect mesh binding: ' + str(part))
                    paths = [slot.get_editor_property('material_interface').get_path_name()
                             for slot in mesh.get_editor_property('materials')
                             if slot.get_editor_property('material_interface')]
                binding = {'character': cid, 'outfit': str(outfit.get_editor_property('id')),
                           'part': str(part.get_editor_property('id')), 'materials': paths}
                report['bindings'].append(binding)
                for path in paths:
                    material_bindings.setdefault(path, []).append({k: v for k, v in binding.items()
                                                                  if k != 'materials'})
    for path, bindings in sorted(material_bindings.items()):
        kind = classify(path.rsplit('/', 1)[-1].split('.', 1)[0])
        if kind is None:
            continue
        row = {'path': path, 'kind': kind, 'bindings': bindings}
        report['materials'].append(row)
        try:
            material = unreal.load_asset(path)
            if not isinstance(material, unreal.Material):
                row['status'] = 'skip_not_base_material'
                continue
            if material.get_editor_property('blend_mode') != unreal.BlendMode.BLEND_OPAQUE:
                row['status'] = 'skip_not_opaque'
                continue
            if material.get_editor_property('shading_model') != unreal.MaterialShadingModel.MSM_DEFAULT_LIT:
                row['status'] = 'skip_not_default_lit'
                continue
            roughness = edit.get_material_property_input_node(material, unreal.MaterialProperty.MP_ROUGHNESS)
            metallic = edit.get_material_property_input_node(material, unreal.MaterialProperty.MP_METALLIC)
            if not isinstance(roughness, unreal.MaterialExpressionConstant) or not isinstance(metallic, unreal.MaterialExpressionConstant):
                row['status'] = 'skip_nonconstant_surface_input'
                continue
            roughness_before = float(roughness.get_editor_property('r'))
            if abs(float(metallic.get_editor_property('r'))) > .0001:
                row['status'] = 'skip_nonzero_metallic'
                continue
            if kind == 'skin' and not .399 <= roughness_before <= .451:
                row['status'] = 'skip_unexpected_skin_roughness'
                continue
            # Probe the native flag before creating/changing any nodes.
            curvature_before = bool(material.get_editor_property('normal_curvature_to_roughness'))
            specular = edit.get_material_property_input_node(material, unreal.MaterialProperty.MP_SPECULAR)
            if specular and not isinstance(specular, unreal.MaterialExpressionConstant):
                row['status'] = 'skip_nonconstant_specular'
                continue
            row.update(roughness_before=roughness_before,
                       normal_curvature_to_roughness_before=curvature_before,
                       specular_before=float(specular.get_editor_property('r')) if specular else 'UE_default_0.5',
                       specular_after=SKIN_SPECULAR if kind == 'skin' else EYE_SPECULAR,
                       roughness_after=roughness_before if kind == 'skin' else EYE_ROUGHNESS,
                       normal_curvature_to_roughness_after=True)
            if mode == 'apply':
                backup(path.split('.', 1)[0])
                if specular is None:
                    specular = edit.create_material_expression(material, unreal.MaterialExpressionConstant, -350, 1320)
                    specular.set_editor_property('desc', MARKER + ': dielectric specular')
                    if not edit.connect_material_property(specular, '', unreal.MaterialProperty.MP_SPECULAR):
                        raise RuntimeError('Cannot connect calibrated specular.')
                specular.set_editor_property('r', row['specular_after'])
                roughness.set_editor_property('r', row['roughness_after'])
                material.set_editor_property('normal_curvature_to_roughness', True)
                edit.recompile_material(material)
                if not library.save_loaded_asset(material):
                    raise RuntimeError('Calibrated material did not save.')
                row['status'] = 'applied'
            else:
                row['status'] = 'eligible'
        except Exception as exc:
            row['status'] = 'error'
            report['errors'].append({'path': path, 'error': str(exc)})
        write_json(directory / (mode + '_report.json'), report)
    report['finished_utc'] = datetime.now(timezone.utc).isoformat()
    report['status'] = 'failed' if report['errors'] else 'complete'
    write_json(directory / (mode + '_report.json'), report)
    unreal.log('SURFACE_REFINEMENT ' + json.dumps({'mode': mode, 'status': report['status'],
                'materials': len(report['materials']), 'errors': report['errors']}))
    if report['errors']:
        raise RuntimeError('Surface refinement has errors; inspect Saved/SurfaceRefinement report.')


if __name__ == '__main__':
    if '--restore-files' in sys.argv:
        restore_files(Path(__file__).resolve().parents[1])
    else:
        run()
