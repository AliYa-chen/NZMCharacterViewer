"""Enable mobile SSR on eligible, actually bound translucent materials.

Run in Unreal Python with -ReflectionMaterialMode=audit (default) or =apply.
Only Default Lit, Translucent, Surface Per Pixel Lighting materials that are
not Fully Rough are eligible. Color, opacity, roughness, graph and mesh/catalog
bindings are retained; no planar reflection or GI implementation is added.

Restore exact original packages with the project closed, outside Unreal:
    python3 Scripts/configure_reflection_materials.py --restore-files

Reports: Saved/FullRendering/ReflectionMaterialAudit.json or
ReflectionMaterialApply.json. Originals: ReflectionMaterialOriginal/manifest.json.
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


CATALOG = '/Game/Viewer/Data/DA_ViewerCatalog'
DEFAULT_ENVIRONMENT = '/Game/Viewer/Environment/DA_Environment'
SAVED = Path('Saved/FullRendering')
ORIGINAL = SAVED / 'ReflectionMaterialOriginal'
PATH_PATTERN = re.compile(r'(/(?:Game|Engine)/[A-Za-z0-9_./-]+)')
EXTENSIONS = ('.uasset', '.uexp', '.ubulk')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def allowed_relative(text):
    if not isinstance(text, str) or any(c in text for c in ('\\', ':', '\0')):
        raise ValueError('Expected a canonical project-relative package path.')
    relative = PurePosixPath(text)
    roots = (('Content', 'Characters'), ('Content', 'Viewer', 'Environment', 'Materials'))
    if (relative.is_absolute() or relative.as_posix() != text or '..' in relative.parts
            or relative.suffix not in EXTENSIONS
            or not any(relative.parts[:len(root)] == root and len(relative.parts) > len(root)
                       for root in roots)):
        raise ValueError('Reflection backup target is outside character/environment materials: ' + text)
    return relative


def checked_path(root, relative, require_file=False):
    path = root / relative
    if path.resolve() != path or not path.is_relative_to(root):
        raise ValueError('Symlinked or out-of-scope package path: ' + str(path))
    if require_file and not path.is_file():
        raise ValueError('Package file missing: ' + str(path))
    if path.exists() and not path.is_file():
        raise ValueError('Package path is not a regular file: ' + str(path))
    return path


def original_manifest(project, required=False):
    directory = project / ORIGINAL
    filename = directory / 'manifest.json'
    if directory.resolve() != directory or filename.resolve() != filename:
        raise ValueError('Reflection original backup must not use symlinks.')
    if not filename.exists() and not required:
        return {'version': 1, 'files': []}
    manifest = json.loads(filename.read_text(encoding='utf-8'))
    if not isinstance(manifest, dict) or not isinstance(manifest.get('files'), list):
        raise ValueError('Malformed reflection original manifest.')
    seen = set()
    for row in manifest['files']:
        if not isinstance(row, dict):
            raise ValueError('Malformed reflection original file record.')
        relative = allowed_relative(row.get('relative'))
        identity = str(relative).casefold()
        if identity in seen:
            raise ValueError('Duplicate original package path: ' + str(relative))
        seen.add(identity)
        expected = row.get('sha256')
        if not isinstance(expected, str) or re.fullmatch(r'[0-9a-f]{64}', expected) is None:
            raise ValueError('Invalid original SHA-256: ' + str(relative))
        source = checked_path(directory, relative, True)
        checked_path(project, relative)
        if digest(source) != expected:
            raise RuntimeError('Original backup hash mismatch: ' + str(relative))
    packages = {str(PurePosixPath(row['relative']).with_suffix(''))
                for row in manifest['files'] if row['relative'].endswith('.uasset')}
    if any(str(PurePosixPath(row['relative']).with_suffix('')) not in packages
           for row in manifest['files']):
        raise ValueError('Each original sidecar must have its original .uasset.')
    if required and not manifest['files']:
        raise ValueError('There are no original reflection materials to restore.')
    return manifest


def require_closed_project(project):
    if 'unreal' in sys.modules:
        raise RuntimeError('Restore package bytes with ordinary Python, outside Unreal.')
    command = (['powershell', '-NoProfile', '-Command',
                'Get-CimInstance Win32_Process | Select-Object -ExpandProperty CommandLine']
               if sys.platform == 'win32' else ['ps', '-axo', 'args='])
    listing = subprocess.run(command, check=True, capture_output=True, text=True).stdout
    executable = re.compile(r'(?:^|[/\\])(?P<name>UnrealEditor(?:-Cmd)?|NZMCharacterViewer(?:-[\w-]+)?)'
                            r'(?:\.exe)?(?=[\s\"\']|$)', re.IGNORECASE)
    for line in listing.splitlines():
        match = executable.search(line)
        if match and (str(project).casefold() in line.replace('\\', '/').casefold()
                      or match['name'].casefold().startswith('nzmcharacterviewer')):
            raise RuntimeError('Close this project in Unreal Editor and its viewer before restoring.')


def restore_files(project):
    project = project.resolve()
    require_closed_project(project)
    manifest = original_manifest(project, required=True)
    # Validate and read all originals before writing a single package.
    plan = []
    for row in manifest['files']:
        relative = allowed_relative(row['relative'])
        source = checked_path(project / ORIGINAL, relative, True)
        payload = source.read_bytes()
        if hashlib.sha256(payload).hexdigest() != row['sha256']:
            raise RuntimeError('Original changed while preparing restore: ' + str(relative))
        plan.append((row, source, checked_path(project, relative), payload))
    original_paths = {row['relative'] for row in manifest['files']}
    removals = []
    for row in manifest['files']:
        if not row['relative'].endswith('.uasset'):
            continue
        for extension in ('.uexp', '.ubulk'):
            relative = str(PurePosixPath(row['relative']).with_suffix(extension))
            path = checked_path(project, allowed_relative(relative))
            if relative not in original_paths and path.exists():
                removals.append(path)
    for row, source, target, payload in plan:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.' + target.name + '.',
                                             suffix='.reflection-restore.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
            if digest(temporary) != row['sha256']:
                raise RuntimeError('Staged restore SHA-256 mismatch: ' + str(target))
            shutil.copystat(source, temporary)
            temporary.replace(target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    for path in removals:
        path.unlink()
    write_json(project / SAVED / 'ReflectionMaterialRestore.json', {
        'status': 'restored', 'finished_utc': datetime.now(timezone.utc).isoformat(),
        'files': manifest['files'],
        'removed_nonoriginal_sidecars': [p.relative_to(project).as_posix() for p in removals]})
    print('Restored', len(plan), 'verified original reflection-material package files.')


def split_fields(text):
    text = text.strip()
    if text.startswith('(') and text.endswith(')'):
        text = text[1:-1]
    fields, depth, quote, escaped, start = [], 0, None, False, 0
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
            fields.append(text[start:index].strip())
            start = index + 1
    if quote or depth != 0:
        raise ValueError('Unbalanced serialized soft-reference fields.')
    return fields if fields != [''] else []


def serialized_fields(value):
    fields = {}
    for field in split_fields(value.export_text()):
        key, separator, text = field.partition('=')
        if separator:
            fields[key.strip().lower()] = text.strip()
    return fields


def soft_path(text):
    paths = PATH_PATTERN.findall(text)
    if len(paths) > 1:
        raise ValueError('Expected a single soft object path: ' + text)
    return paths[0] if paths else None


def binding_materials(unreal, spec, static=False):
    fields = serialized_fields(spec)
    mesh_path = soft_path(fields.get('mesh', ''))
    mesh = unreal.load_asset(mesh_path) if mesh_path else None
    expected = unreal.StaticMesh if static else unreal.SkeletalMesh
    if not isinstance(mesh, expected):
        raise RuntimeError('Bound mesh is unavailable: ' + str(mesh_path))
    slots = list(mesh.get_editor_property('static_materials' if static else 'materials'))
    defaults = [slot.get_editor_property('material_interface') for slot in slots]
    overrides = [soft_path(text) for text in split_fields(fields.get('materials', ''))]
    result = []
    for index in range(max(len(defaults), len(overrides))):
        override = overrides[index] if index < len(overrides) else None
        default = defaults[index] if index < len(defaults) else None
        path = override or (default.get_path_name() if default else None)
        if not path:
            raise RuntimeError('Bound mesh material slot is unresolved: ' + str(mesh_path) + ':' + str(index))
        result.append({'slot': index, 'path': path, 'source': 'catalog_override' if override else 'mesh_default'})
    return mesh_path, result


def configuration_inventory(project):
    keys = {'r.Mobile.ShadingPath', 'r.MobileHDR', 'r.Mobile.ScreenSpaceReflections',
            'r.DynamicGlobalIlluminationMethod', 'r.ReflectionMethod', 'r.Lumen.DiffuseIndirect.Allow',
            'r.Lumen.Reflections.Allow', 'r.Lumen.HardwareRayTracing', 'r.RayTracing',
            'r.GenerateMeshDistanceFields', 'r.AllowStaticLighting'}
    rows = []
    for relative in ('Config/DefaultEngine.ini', 'Config/Mac/MacEngine.ini'):
        section = None
        path = project / relative
        if not path.exists():
            continue
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line.startswith('[') and line.endswith(']'):
                section = line[1:-1]
            key, separator, value = line.partition('=')
            if separator and key in keys:
                rows.append({'file': relative, 'section': section, 'key': key, 'value': value})
    return {'scope': 'Disk configuration only; not resolved runtime CVars or executed GPU passes.',
            'configuration': rows, 'ssr_is_global_illumination': False,
            'dynamic_gi_verified': None, 'enlighten_runtime_verified': None,
            'runtime_verification_fields': {
                'scene': ['rhi_name', 'scene_shader_platform', 'scene_feature_level_name'],
                'gi': ['stage_overrides_global_illumination_method', 'stage_global_illumination_method',
                       'r.DynamicGlobalIlluminationMethod', 'r.Lumen.DiffuseIndirect.Allow',
                       'r.GenerateMeshDistanceFields', 'r.Lumen.HardwareRayTracing'],
                'reflections': ['stage_reflection_method', 'stage_ssr_intensity', 'r.Mobile.ScreenSpaceReflections',
                                'r.SSR.Quality', 'r.Mobile.AntiAliasing', 'r.MSAACount']},
            'render_confirmation_required': 'Inspect GPU profile/pass events and controlled indirect-light image comparisons. HDR skylight/SSR settings alone do not establish GI.'}


def run():
    import unreal
    project = Path(unreal.Paths.project_dir()).resolve()
    command_line = unreal.SystemLibrary.get_command_line()
    match = re.search(r'-ReflectionMaterialMode=([^\s]+)', command_line)
    mode = match[1].lower() if match else 'audit'
    if mode not in ('audit', 'apply'):
        raise ValueError('Use audit/apply in Unreal; restore original bytes using ordinary Python --restore-files.')
    destination = project / SAVED / ('ReflectionMaterial' + mode.title() + '.json')
    report = {'version': 1, 'mode': mode, 'started_utc': datetime.now(timezone.utc).isoformat(),
              'scope': 'Only enables the SSR property on eligible bound base materials. No graph, opacity, roughness, shading model, mesh/catalog binding, planar-reflection or GI edits.',
              'bindings': [], 'environments': [], 'materials': [], 'errors': [],
              'pipeline_inventory': configuration_inventory(project)}
    edit, library = unreal.MaterialEditingLibrary, unreal.EditorAssetLibrary
    manifest = original_manifest(project)
    bindings = {}

    def add_binding(record, spec, static=False):
        mesh_path, materials = binding_materials(unreal, spec, static)
        row = dict(record, mesh=mesh_path, materials=materials)
        report['bindings'].append(row)
        for material in materials:
            detail = dict(record, mesh=mesh_path, slot=material['slot'], source=material['source'])
            bindings.setdefault(material['path'], []).append(detail)

    def read_property(obj, name):
        try:
            value = obj.get_editor_property(name)
            return value if value is None or isinstance(value, (bool, int, float, str)) else str(value)
        except Exception as exc:
            return {'unavailable': str(exc)}

    def graph_snapshot(material):
        result = {'expressions': sorted((node.get_path_name(), node.get_class().get_name())
                                       for node in edit.get_material_expressions(material)), 'outputs': {}}
        for name in ('MP_BASE_COLOR', 'MP_NORMAL', 'MP_ROUGHNESS', 'MP_METALLIC', 'MP_SPECULAR',
                     'MP_OPACITY', 'MP_OPACITY_MASK', 'MP_EMISSIVE_COLOR', 'MP_AMBIENT_OCCLUSION'):
            node = edit.get_material_property_input_node(material, getattr(unreal.MaterialProperty, name))
            result['outputs'][name] = {'node': node.get_path_name() if node else None,
                                      'class': node.get_class().get_name() if node else None,
                                      'r': float(node.get_editor_property('r')) if isinstance(node, unreal.MaterialExpressionConstant) else None}
        return result

    def backup(package):
        stem = Path('Content') / package[len('/Game/'):]
        index = {row['relative']: row for row in manifest['files']}
        for extension in EXTENSIONS:
            relative = allowed_relative(stem.with_suffix(extension).as_posix())
            source = checked_path(project, relative, require_file=extension == '.uasset')
            if not source.exists():
                continue
            target = checked_path(project / ORIGINAL, relative)
            text = relative.as_posix()
            if text in index:
                if digest(target) != index[text]['sha256']:
                    raise RuntimeError('Existing original backup hash mismatch: ' + text)
                continue
            if target.exists():
                raise RuntimeError('Unmanifested original backup already exists: ' + str(target))
            target.parent.mkdir(parents=True, exist_ok=True)
            expected = digest(source)
            shutil.copy2(source, target)
            if digest(target) != expected or digest(source) != expected:
                raise RuntimeError('Original changed during backup: ' + text)
            manifest['files'].append({'relative': text, 'sha256': expected})
        write_json(project / ORIGINAL / 'manifest.json', manifest)

    try:
        catalog = unreal.load_asset(CATALOG)
        if not isinstance(catalog, unreal.ViewerCatalog):
            raise RuntimeError('Current runtime Catalog is unavailable.')
        environment_paths = set()
        for character in catalog.get_editor_property('characters'):
            cid = str(character.get_editor_property('id'))
            for outfit in character.get_editor_property('outfits'):
                oid = str(outfit.get_editor_property('id'))
                for part in outfit.get_editor_property('parts'):
                    add_binding({'kind': 'character', 'character': cid, 'outfit': oid,
                                 'part': str(part.get_editor_property('id'))}, part)
                fields = serialized_fields(outfit)
                environment_paths.add(soft_path(fields.get('environment', '')) or DEFAULT_ENVIRONMENT)
        for path in sorted(environment_paths):
            environment = unreal.load_asset(path)
            if not isinstance(environment, unreal.ViewerEnvironment):
                raise RuntimeError('Bound environment unavailable: ' + path)
            post = environment.get_editor_property('post_process')
            report['environments'].append({'path': path,
                'asset_gi_method': read_property(post, 'dynamic_global_illumination_method'),
                'asset_reflection_method': read_property(post, 'reflection_method'),
                'scope': 'Asset postprocess only; ViewerPawn overrides these methods at runtime.'})
            for spec in environment.get_editor_property('meshes'):
                add_binding({'kind': 'environment', 'environment': path,
                             'part': str(spec.get_editor_property('id'))}, spec, static=True)

        candidates, seen_base = [], set()
        for path, references in sorted(bindings.items()):
            material = unreal.load_asset(path)
            row = {'path': path, 'bindings': references}
            report['materials'].append(row)
            if not isinstance(material, unreal.Material):
                row['status'] = 'skip_material_instance_or_external_requires_review'
                continue
            base_path = material.get_path_name()
            if base_path in seen_base:
                row['status'] = 'duplicate_base_material'
                continue
            seen_base.add(base_path)
            properties = ('blend_mode', 'shading_model', 'translucency_lighting_mode', 'fully_rough',
                          'screen_space_reflections', 'use_planar_forward_reflections', 'use_hq_forward_reflections')
            row['before'] = {name: read_property(material, name) for name in properties}
            row['graph_before'] = graph_snapshot(material)
            eligible = (material.get_editor_property('blend_mode') == unreal.BlendMode.BLEND_TRANSLUCENT
                        and material.get_editor_property('shading_model') == unreal.MaterialShadingModel.MSM_DEFAULT_LIT
                        and material.get_editor_property('translucency_lighting_mode') == unreal.TranslucencyLightingMode.TLM_SURFACE_PER_PIXEL_LIGHTING
                        and not material.get_editor_property('fully_rough'))
            if not eligible:
                row['status'] = 'skip_not_eligible_translucent_surface'
                continue
            if bool(material.get_editor_property('screen_space_reflections')):
                row['status'] = 'already_enabled'
                continue
            package = base_path.split('.', 1)[0]
            if not package.startswith('/Game/'):
                row['status'] = 'skip_nonproject_material'
                continue
            allowed_relative((Path('Content') / package[len('/Game/'):]).with_suffix('.uasset').as_posix())
            row['status'] = 'eligible'
            candidates.append((material, package, row))

        if mode == 'apply':
            # Complete all immutable backups before changing any material.
            for material, package, row in candidates:
                backup(package)
            original_manifest(project, required=bool(candidates))
            for material, package, row in candidates:
                before = dict(row['before'])
                material.set_editor_property('screen_space_reflections', True)
                preserved = {name: read_property(material, name) for name in before if name != 'screen_space_reflections'}
                if (preserved != {name: value for name, value in before.items() if name != 'screen_space_reflections'}
                        or graph_snapshot(material) != row['graph_before']):
                    material.set_editor_property('screen_space_reflections', False)
                    raise RuntimeError('Material graph or unrelated settings changed unexpectedly: ' + package)
                edit.recompile_material(material)
                if not library.save_loaded_asset(material):
                    raise RuntimeError('Could not save reflection material: ' + package)
                if not material.get_editor_property('screen_space_reflections'):
                    raise RuntimeError('Reflection material flag did not remain enabled: ' + package)
                row['status'] = 'applied'
                row['graph_and_other_settings_preserved'] = True
                row['package_sha256_after'] = digest(project / 'Content' / (package[len('/Game/'):] + '.uasset'))
        report['status'] = 'complete'
        report['eligible_count'] = len(candidates)
        report['applied_count'] = sum(row['status'] == 'applied' for row in report['materials'])
    except Exception as exc:
        report['status'] = 'failed'
        report['errors'].append(str(exc))
        raise
    finally:
        report['finished_utc'] = datetime.now(timezone.utc).isoformat()
        write_json(destination, report)
        unreal.log('REFLECTION_MATERIALS ' + json.dumps({'mode': mode, 'status': report.get('status'),
                    'materials': len(report['materials']), 'errors': report['errors']}))


if __name__ == '__main__':
    if '--restore-files' in sys.argv:
        restore_files(Path(__file__).resolve().parents[1])
    else:
        run()
