#!/usr/bin/env python3
"""Build source-evidenced UI outfits for the converted PC resource library."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import tarfile

FAMILIES = {'Angel': '安琪儿', 'Angela': '安吉拉', 'Bella': '贝拉', 'Bunny': '芭比',
            'Emma': '艾玛', 'Lilith': '莉莉丝', 'Molly': '茉莉', 'Naimei': '奈美',
            'Queen': '精绝女王', 'Shirley': '雪莉杨', 'Tina': '黑暗天使', 'Widow': '黑寡妇'}
EVIDENCE = 'PCResources/Characters/Configuration/outfit_config_evidence.json'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def object_path(value):
    if isinstance(value, dict): value = value.get('ObjectPath') or value.get('AssetPathName')
    if not isinstance(value, str) or value in ('', 'None'): return None
    return value.replace('NZM/Content/', '/Game/', 1) if value.startswith('NZM/Content/') else value


def read_plan(root, expected_sha):
    for relative in ('Saved/PCViewerIntegration/asset_plan.json', 'Saved/PCAssetConversion/asset_plan.json'):
        path = root / relative
        if path.exists():
            payload = path.read_bytes()
            break
    else:
        with tarfile.open(root / 'Docs/PCAssetConversion/conversion_evidence.tar.gz') as archive:
            payload = archive.extractfile('Saved/PCAssetConversion/asset_plan.json').read()
    if hashlib.sha256(payload).hexdigest() != expected_sha:
        raise ValueError('Asset plan differs from the verified asset index')
    return json.loads(payload)


def build(root):
    data = json.loads((root / EVIDENCE).read_text())
    index = json.loads((root / 'Docs/PCAssetConversion/asset_index.json').read_text())
    plan = read_plan(root, index['plan_sha256'])
    indexed = {row['target']: row for row in index['assets']}
    mesh_targets = {row['target']: row for row in plan['meshes']}
    sources = {(row['virtual_package'], row['source_variant']): row for row in data['sources']}
    for source in sources.values():
        path = root / source['file']
        if not path.exists() or sha(path) != source['sha256']:
            raise ValueError('Configuration source differs from evidence: ' + source['file'])
    chars = defaultdict(list)
    excluded = []
    for model in data['female_models']:
        if 'bot' in model['description'].casefold():
            excluded.append({'model_id': model['model_id'], 'reason': 'bot_configuration'})
            continue
        choices = [(da, route) for da in model['data_assets'] for route in da['routes']
                   if da['source_variant'] == 'provider_selected_all_archives' and route['route'] == 'UIConfig']
        if not choices: continue
        # Prefer the explicit GameData DA rather than its older root alias.
        da, route = max(choices, key=lambda item: ('/GameData/' in item[0]['virtual_package'],
                                                 len(item[1]['default_parts']), item[0]['virtual_package']))
        family = re.search('/Avatar/Character/([^/]+)/', da['virtual_package'])[1]
        base = next((r for d in model['data_assets'] if d['source_variant'] == 'base_content_paks'
                     and d['virtual_package'] == da['virtual_package'] for r in d['routes'] if r['route'] == 'UIConfig'), {})
        base_parts = {row['part_id']: row for row in base.get('default_parts', [])}
        parts, missing, recoveries = [], [], []
        for original in route['default_parts']:
            entry, variant = original, da['source_variant']
            if entry['status'] != 'resolved' and base_parts.get(entry['part_id'], {}).get('status') == 'resolved':
                entry, variant = base_parts[entry['part_id']], 'base_content_paks'
                recoveries.append({'part_id': entry['part_id'], 'basis': 'same_explicit_DefaultPartItems_ID_from_base_table',
                                   'provider_status': original['status'], 'source_variant': variant})
            src = entry.get('mesh_package')
            target = plan['packages'].get(src)
            if not target or target not in mesh_targets:
                missing.append({'part_id': original['part_id'], 'source_package': src,
                                'reason': 'unresolved_part_row' if not src else 'mesh_not_imported'})
                continue
            spec = entry['configuration']; geometry = mesh_targets[target]; disk = root / indexed[target]['file']
            if not disk.exists() or sha(disk) != indexed[target]['sha256']:
                raise ValueError('Converted mesh differs from verified index: ' + target)
            transform = spec.get('OverrideSocketRelativeTransform') or {}
            part_source = sources['/Game/DataTables/Avatar/Character/NZAvatarPartConfig_Character', variant]
            parts.append({'id': 'pc_' + entry['part_id'], 'source_part_id': entry['part_id'],
                          'display_name': spec.get('Description') or entry['part_id'],
                          'source_package': src, 'mesh_target': target, 'mesh': target,
                          'source_variant': variant, 'slot': spec.get('Slot'),
                          'source_table': part_source['virtual_package'], 'source_file': part_source['file'],
                          'source_sha256': part_source['sha256'], 'source_configuration': spec,
                          'skeleton_target': geometry['skeleton_target'], 'skeleton_source': geometry.get('skeleton_source'),
                          'materials': geometry['material_targets'], 'slot_names': geometry['slot_names'],
                          'channels': geometry['channels'], 'metadata_file': geometry['metadata_file'],
                          'visible_by_default': True,
                          'transform': {'translation': [transform.get('Translation', {}).get(k, 0) for k in ('X', 'Y', 'Z')],
                                        'rotation_xyzw': [transform.get('Rotation', {}).get(k, 1 if k == 'W' else 0) for k in ('X', 'Y', 'Z', 'W')],
                                        'scale': [transform.get('Scale3D', {}).get(k, 1) for k in ('X', 'Y', 'Z')]},
                          'source_attachment': {'class': object_path(spec.get('AvatarPartAttachmentClass')),
                                                'override_attachment': spec.get('IsOverrideAttachment', False),
                                                'override_socket': spec.get('IsOverrideSocket', False),
                                                'socket_name': spec.get('OverrideSocketName'),
                                                'master_pose': spec.get('OverrideMasterPoseValue'),
                                                'copy_pose_from_master_mesh': spec.get('bCopyPoseFromMasterMesh'),
                                                'skeletal_mesh_component_class': object_path(spec.get('SkeletalMeshComponentClass')),
                                                'is_virtual_part': spec.get('IsVirtualPart', False)},
                          'attachment_validation': 'Check reference-pose bounds and local bone transforms before socket attachment; master-pose meshes can already be in shared origin.',
                          'animation': None, 'animation_status': 'reference_pose_until_verified_compatible_animation'})
        if not parts: continue
        evidence_source = sources[da['virtual_package'], da['source_variant']]
        outfit = {'id': 'pc_' + model['model_id'], 'display_name': model['description'], 'name': model['description'],
                  'model_id': model['model_id'], 'model_aliases': [], 'family': family,
                  'source_da': da['virtual_package'], 'source_file': evidence_source['file'],
                  'source_sha256': evidence_source['sha256'], 'source_variant': da['source_variant'],
                  'route': 'UIConfig', 'default_part_ids': [p['part_id'] for p in route['default_parts']],
                  'source_route_configuration': route['explicit_configuration'],
                  'source_visibility_basis': route['visibility'],
                  'parts': parts, 'missing_default_parts': missing, 'base_table_recoveries': recoveries,
                  'status': 'configured_parts_ready_for_runtime_validation' if not missing else 'partial_configured_parts_requires_visual_validation',
                  'environment': '/Game/Viewer/Environment/DA_Environment',
                  'environment_basis': 'viewer_standard_stage_for_initial_integration; no source_scene_claim',
                  'portrait': None, 'portrait_basis': 'No explicit portrait image exported; generate a preview or retain existing portrait.'}
        signature = [(p['source_part_id'], p['source_package'], p['transform'], p['source_attachment']) for p in parts]
        duplicate = next((o for o in chars[family] if [(p['source_part_id'], p['source_package'], p['transform'], p['source_attachment']) for p in o['parts']] == signature), None)
        if duplicate:
            duplicate['model_aliases'].append({'model_id': model['model_id'], 'description': model['description']})
            continue
        if missing: outfit['display_name'] += '（已恢复部件）'; outfit['name'] = outfit['display_name']
        chars[family].append(outfit)
    characters = [{'id': family.lower(), 'family': family, 'display_name': FAMILIES[family], 'name': FAMILIES[family],
                   'outfits': sorted(outfits, key=lambda row: row['model_id']), 'portrait': None}
                  for family, outfits in sorted(chars.items())]
    return {'schema_version': 1, 'status': 'configured_UI_outfits_planned_requires_editor_and_runtime_validation',
            'source_evidence': EVIDENCE, 'source_evidence_sha256': sha(root / EVIDENCE),
            'asset_index_sha256': sha(root / 'Docs/PCAssetConversion/asset_index.json'),
            'asset_plan_sha256': index['plan_sha256'], 'verified_configuration_sources': len(sources),
            'policy': 'Preserve existing viewer characters/outfits; add these PC UI combinations by family. No inventory-only, bot or master meshes become selectable outfits.',
            'counts': {'characters': len(characters), 'outfits': sum(len(c['outfits']) for c in characters),
                       'parts': sum(len(o['parts']) for c in characters for o in c['outfits']),
                       'partially_resolved_outfits': sum(bool(o['missing_default_parts']) for c in characters for o in c['outfits']),
                       'base_table_part_recoveries': sum(len(o['base_table_recoveries']) for c in characters for o in c['outfits'])},
            'characters': characters, 'excluded_models': excluded,
            'limitations': ['Serialized default UI parts do not establish final online display conditions.',
                            'Emma part 10061010001 is absent from both tables; its type and purpose remain unknown, not presumed virtual or harmless.',
                            'Private shaders, original animation curves, cloth, particles and source-scene reconstruction are separate tasks.',
                            'Source socket flags must be checked against each mesh reference coordinate frame before parenting.',
                            'Custom replacement configurations are retained below but not automatically applied to the default combination.'],
            'custom_replacement_evidence': data['female_custom_replacement_rows']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(); root = args.root.resolve(); report = build(root)
    output = args.output or root / 'Saved/PCViewerIntegration/viewer_plan.json'; output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'output': str(output), **report['counts']}, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
