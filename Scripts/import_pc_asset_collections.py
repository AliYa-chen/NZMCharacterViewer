"""Build editor-only navigation collections for the imported PC resource library.

Run inside UE5.8 after mesh, texture, material and source-record imports.
The collections preserve catalog directory groups; they do not create viewer
outfits, modify the runtime Catalog, or reconstruct original game logic.
All dependency references are serialized SoftObjectPaths without loading them.
"""
import hashlib
import json
from pathlib import Path
import re
import traceback

import unreal


OWNER = 'pc_asset_collections_v1'
OWNER_TAG = 'PCAssetConversion.CollectionsOwner'
PREFIX = 'PCAssetConversion.'
COLLECTION_ROOT = '/Game/PCAssets/Collections'
CATALOG_FILE = 'PCResources/Characters/catalog.json'
SOURCE_MANIFEST_FILE = 'PCResources/source_manifest.json'
PLAN_FILE = 'Saved/PCAssetConversion/asset_plan.json'


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.new')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def object_path(package):
    if not package.startswith('/Game/PCAssets/') or '.' in package:
        raise ValueError('Invalid imported asset package: ' + package)
    return package + '.' + package.rsplit('/', 1)[1]


def build_collection_specs(plan, catalog, catalog_sha256, manifest_sha256, plan_sha256):
    """Pure source-to-collection mapping; does not query or load UE assets."""
    if plan.get('schema_version') != 1 or catalog.get('schema_version') != 1:
        raise ValueError('Unsupported PC asset plan or character catalog schema')
    input_hashes = {row['file']: row['sha256'] for row in plan['inputs']}
    if input_hashes.get(CATALOG_FILE) != catalog_sha256:
        raise ValueError('Catalog bytes do not match the verified import plan')
    if input_hashes.get(SOURCE_MANIFEST_FILE) != manifest_sha256 or plan['source_manifest_sha256'] != manifest_sha256:
        raise ValueError('Source manifest bytes do not match the verified import plan')
    mesh_targets = {row['target'] for row in plan['meshes']}
    collection_specs = []
    family_ids = set()
    total_mesh_packages = set()
    for family in catalog['families']:
        family_id = family['id']
        if not re.fullmatch('[A-Za-z0-9_]+', family_id) or family_id in family_ids:
            raise ValueError('Invalid or duplicate family ID: ' + str(family_id))
        family_ids.add(family_id)
        references = set()
        packages = []
        for group in family['groups']:
            for mesh in group['meshes']:
                source_package = mesh['source_package']
                target = plan['packages'].get(source_package)
                if isinstance(target, dict):
                    target = target.get('target')
                if target not in mesh_targets:
                    raise ValueError('Catalog mesh has no planned imported mesh: ' + source_package)
                object_path(target)
                references.add(target)
                packages.append(source_package)
        if len(packages) != family['mesh_count'] or len(packages) != len(set(packages)):
            raise ValueError('Family mesh count or uniqueness disagrees with catalog: ' + family_id)
        total_mesh_packages.update(packages)
        collection_specs.append({
            'target': COLLECTION_ROOT + '/DA_' + family_id,
            'source_package': family_id, 'source_file': CATALOG_FILE,
            'source_sha256': catalog_sha256, 'source_type': 'PCCharacterCollection',
            'source_json': family, 'references': sorted(references),
            'source_mesh_packages': sorted(packages), 'directory_group_count': len(family['groups']),
        })
    if len(total_mesh_packages) != catalog['mesh_packages']:
        raise ValueError('Catalog total mesh package count disagrees with its family entries')
    source_records = {row['file']: row['target'] for row in plan.get('documents', [])}
    asset_refs = {kind: sorted({row['target'] for row in plan[kind]})
                  for kind in ('meshes', 'textures', 'materials', 'documents')}
    for references in asset_refs.values():
        for target in references:
            object_path(target)
    collection_targets = sorted(spec['target'] for spec in collection_specs)
    library_target = COLLECTION_ROOT + '/DA_PCAssetLibrary'
    if library_target in collection_targets:
        raise ValueError('Family collection name collides with the total library')
    source_indices = [
        {'file': CATALOG_FILE, 'sha256': catalog_sha256,
         'source_record': source_records.get(CATALOG_FILE)},
        {'file': SOURCE_MANIFEST_FILE, 'sha256': manifest_sha256,
         'source_record': source_records.get(SOURCE_MANIFEST_FILE)},
        {'file': PLAN_FILE, 'sha256': plan_sha256,
         'scope': 'deterministically_generated_local_import_plan'},
    ]
    summary = {
        'schema_version': 1,
        'scope': 'Editor navigation index for imported source candidates; collections are not runtime outfits.',
        'counts': {**{kind: len(references) for kind, references in asset_refs.items()},
                   'character_collections': len(collection_specs),
                   'source_character_mesh_packages': len(total_mesh_packages),
                   'source_documents': len(plan.get('documents', []))},
        'source_indices': source_indices, 'collections': collection_targets,
        'imported_asset_references': asset_refs,
        'limitations': list(plan.get('limitations', [])) + [
            'Family groups preserve source directories and cannot establish selectable outfit combinations.',
            'Collection records are editor-only; they do not alter the existing runtime ViewerCatalog.',
            'Source JSON evidence does not become executable shader, animation, particle, or Blueprint logic.',
        ],
    }
    collection_specs.append({
        'target': library_target, 'source_package': 'PCAssetLibrary',
        'source_file': SOURCE_MANIFEST_FILE, 'source_sha256': manifest_sha256,
        'source_type': 'PCAssetLibrary', 'source_json': summary,
        'references': sorted(set(collection_targets).union(*asset_refs.values())),
        'source_indices': source_indices,
    })
    return collection_specs


def _save_collection(root, spec, plan_sha256):
    lib = unreal.EditorAssetLibrary
    target = spec['target']
    existing = lib.load_asset(target) if lib.does_asset_exist(target) else None
    if existing is not None:
        if not isinstance(existing, unreal.PCSourceRecord) or lib.get_metadata_tag(existing, OWNER_TAG) != OWNER:
            raise RuntimeError('Refusing to overwrite an unowned collection: ' + target)
        if existing.get_editor_property('source_package') != spec['source_package']:
            raise RuntimeError('Owned collection source identity changed: ' + target)
    else:
        destination, name = target.rsplit('/', 1)
        factory = unreal.DataAssetFactory()
        factory.set_editor_property('data_asset_class', unreal.PCSourceRecord)
        existing = unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, destination, unreal.PCSourceRecord, factory)
        if not isinstance(existing, unreal.PCSourceRecord):
            raise RuntimeError('Could not create source collection: ' + target)
    for name in ('source_package', 'source_file', 'source_sha256', 'source_type'):
        existing.set_editor_property(name, spec[name])
    # Keep the family object itself unchanged in SourceJSON. The total library
    # uses a compact index rather than embedding the 30+ MB import plan.
    existing.set_editor_property('source_json', json.dumps(spec['source_json'], ensure_ascii=False, indent=2))
    references = list(spec['references'])
    if not unreal.PCAssetImportLibrary.set_source_record_references(existing, references):
        raise RuntimeError('Could not assign collection soft references: ' + target)
    for name, value in {
        OWNER_TAG: OWNER,
        PREFIX + 'SourceFile': spec['source_file'],
        PREFIX + 'SourceSHA256': spec['source_sha256'],
        PREFIX + 'SourcePackage': spec['source_package'],
        PREFIX + 'PlanSHA256': plan_sha256,
        PREFIX + 'ConversionKind': 'editor_only_navigation_collection_not_runtime_outfit',
    }.items():
        lib.set_metadata_tag(existing, name, value)
    if not lib.save_loaded_asset(existing, only_if_is_dirty=False):
        raise RuntimeError('Could not persist collection: ' + target)
    disk = root / 'Content' / (target[len('/Game/'):] + '.uasset')
    if not disk.is_file() or disk.stat().st_size <= 0:
        raise RuntimeError('Collection package was not written: ' + target)
    return {'target': target, 'source_package': spec['source_package'], 'source_type': spec['source_type'],
            'source_file': spec['source_file'], 'source_sha256': spec['source_sha256'],
            'soft_reference_count': len(references), 'asset_bytes': disk.stat().st_size,
            'asset_sha256': digest(disk), 'editor_only_by_PCSourceRecord_class': True}


def run():
    root = Path(unreal.Paths.project_dir()).resolve()
    output = root / 'Saved/PCAssetConversion/collections_report.json'
    report = {'schema_version': 1, 'status': 'running', 'owner': OWNER,
              'collections': [], 'errors': [],
              'scope': 'Editor-only source navigation; no runtime Catalog or outfit changes.'}
    try:
        if not hasattr(unreal, 'PCSourceRecord'):
            raise RuntimeError('Compile and enable the PCAssetImporter plugin before creating collections')
        plan_path, catalog_path, manifest_path = root / PLAN_FILE, root / CATALOG_FILE, root / SOURCE_MANIFEST_FILE
        plan = json.loads(plan_path.read_text(encoding='utf-8-sig'))
        catalog = json.loads(catalog_path.read_text(encoding='utf-8-sig'))
        plan_sha256 = digest(plan_path)
        specs = build_collection_specs(plan, catalog, digest(catalog_path), digest(manifest_path), plan_sha256)
        all_collection_targets = {spec['target'] for spec in specs}
        # Complete all dependency and ownership checks before the first write.
        for spec in specs:
            for target in spec['references']:
                if target not in all_collection_targets and not unreal.EditorAssetLibrary.does_asset_exist(target):
                    raise RuntimeError('Required imported asset is missing: ' + target)
            if unreal.EditorAssetLibrary.does_asset_exist(spec['target']):
                existing = unreal.EditorAssetLibrary.load_asset(spec['target'])
                if not isinstance(existing, unreal.PCSourceRecord) or unreal.EditorAssetLibrary.get_metadata_tag(existing, OWNER_TAG) != OWNER:
                    raise RuntimeError('Collection path is occupied by an unowned asset: ' + spec['target'])
                if existing.get_editor_property('source_package') != spec['source_package']:
                    raise RuntimeError('Collection source identity differs: ' + spec['target'])
        report['plan_sha256'] = plan_sha256
        report['input_indices'] = specs[-1]['source_indices']
        write_json(output, report)
        for spec in specs:
            unreal.log('PC_ASSET_COLLECTION ' + spec['target'])
            report['collections'].append(_save_collection(root, spec, plan_sha256))
            write_json(output, report)
        registry = unreal.AssetRegistryHelpers.get_asset_registry()
        registry.scan_paths_synchronous([COLLECTION_ROOT], True)
        options = unreal.AssetRegistryDependencyOptions()
        options.set_editor_property('include_soft_package_references', True)
        options.set_editor_property('include_hard_package_references', False)
        for spec, result in zip(specs, report['collections']):
            dependencies = registry.get_dependencies(spec['target'], options)
            dependencies = {str(value) for value in dependencies or []}
            missing = set(spec['references']) - dependencies
            if missing:
                raise RuntimeError('Saved collection lacks expected soft package references: ' +
                                   spec['target'] + ': ' + repr(sorted(missing)))
            result['saved_soft_dependency_check'] = 'passed'
        report['counts'] = specs[-1]['source_json']['counts']
        report['status'] = 'passed'
        write_json(output, report)
        unreal.log('PC_ASSET_COLLECTIONS_COMPLETE ' + str(output))
    except Exception:
        report['status'] = 'failed'
        report['errors'].append(traceback.format_exc())
        write_json(output, report)
        raise


if __name__ == '__main__':
    run()
