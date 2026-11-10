#!/usr/bin/env python3
"""Build a deterministic, verified PCResources-to-editor-asset import plan.

This script uses ordinary Python and never starts Unreal or writes Content.
ActorX geometry and source texture settings are preserved. Material channels
are explicitly labelled stock approximations; cooked graphs and JSON evidence
are archived rather than represented as recovered shaders or animation.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import struct


MATERIAL_TYPES = {'Material', 'MaterialInstanceConstant', 'VirtualMaterialInstanceConstant'}
TEXTURE_TYPES = {'Texture2D', 'TextureCube'}
PARAMETER_FIELDS = ('TextureParameterValues', 'ScalarParameterValues', 'VectorParameterValues',
                    'StaticParameters', 'StaticSwitchParameterValues')


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def stable(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def package(value):
    if isinstance(value, dict):
        value = value.get('ObjectPath') or value.get('AssetPathName') or value.get('Package')
    if not isinstance(value, str) or value in ('', 'None', 'null'):
        return None
    value = value.replace('\\', '/').split(':', 1)[0].split('.', 1)[0]
    if value.startswith('NZM/Content/'):
        return '/Game/' + value[len('NZM/Content/'):]
    if value.startswith('Engine/Content/'):
        return '/Engine/' + value[len('Engine/Content/'):]
    if value.startswith(('/Game/', '/Engine/')):
        return value
    return None


def target(kind, name, identity):
    key = hashlib.sha256(identity.encode()).hexdigest()
    safe = re.sub('[^A-Za-z0-9_]', '_', name)[:40].strip('_') or 'Resource'
    prefix = {'Meshes': 'SK', 'Textures': 'T', 'Materials': 'M', 'Documents': 'D'}[kind]
    return f'/Game/PCAssets/{kind}/{key[:2]}/{prefix}_{safe}_{key[:12]}'


def objects(data):
    rows = data if isinstance(data, list) else [data]
    return [row for row in rows if isinstance(row, dict) and isinstance(row.get('Type'), str)]


def actorx_channels(path):
    chunks = {}
    with path.open('rb') as stream:
        while header := stream.read(32):
            if len(header) != 32:
                raise ValueError(f'Truncated ActorX header: {path}')
            raw_name, _, stride, count = struct.unpack('<20s3i', header)
            name = raw_name.rstrip(b'\0').decode('ascii')
            if stride < 0 or count < 0 or name in chunks:
                raise ValueError(f'Invalid or repeated ActorX chunk {name}: {path}')
            raw = stream.read(stride * count)
            if len(raw) != stride * count:
                raise ValueError(f'Truncated ActorX chunk {name}: {path}')
            chunks[name] = (stride, count, raw)
    for name in ('ACTRHEAD', 'PNTS0000', 'VTXW0000', 'MATT0000', 'REFSKELT', 'RAWWEIGHTS'):
        if name not in chunks:
            raise ValueError(f'ActorX lacks {name}: {path}')
    points = chunks['PNTS0000'][1]
    bones = chunks['REFSKELT'][1]
    influences = Counter()
    totals = defaultdict(float)
    stride, count, raw = chunks['RAWWEIGHTS']
    if stride != 12:
        raise ValueError(f'Unknown ActorX weight layout: {path}')
    for index in range(count):
        weight, point, bone = struct.unpack_from('<fii', raw, index * stride)
        if not (math.isfinite(weight) and 0 < weight <= 1 and 0 <= point < points and 0 <= bone < bones):
            raise ValueError(f'Invalid ActorX weight: {path}')
        influences[point] += 1
        totals[point] += weight
    if max(influences.values(), default=0) > 8:
        raise ValueError(f'ActorX exceeds the declared eight influence target: {path}')
    if len(influences) != points:
        raise ValueError(f'ActorX contains points without a skin weight: {path}')
    slot_names = []
    stride, count, raw = chunks['MATT0000']
    for index in range(count):
        slot_names.append(raw[index * stride:index * stride + 64].split(b'\0', 1)[0].decode('utf-8', errors='replace'))
    return {
        'points': points, 'wedges': chunks['VTXW0000'][1], 'bones': bones,
        'triangles': sum(chunks[name][1] for name in ('FACE0000', 'FACE3200') if name in chunks),
        'skin_weight_rows': chunks['RAWWEIGHTS'][1], 'max_influences': max(influences.values(), default=0),
        'points_over_four_influences': sum(count > 4 for count in influences.values()),
        'maximum_weight_sum_error': max((abs(total - 1.0) for total in totals.values()), default=0),
        'face_index_bits': 32 if 'FACE3200' in chunks else 16,
        'uv_channels': 1 + sum(name.startswith('EXTRAUVS') for name in chunks),
        'has_vertex_normals': 'VTXNORMS' in chunks,
        'has_vertex_colors': 'VERTEXCOLOR' in chunks,
        'material_slots': len(slot_names), 'actorx_slot_names': slot_names,
        'chunks': {name: {'stride': row[0], 'count': row[1]} for name, row in chunks.items()},
        'coordinate_basis': 'ActorX source export mirrors decoded-source Y; importer must undo this exactly once.',
        'lod_scope': 'highest_available_exported_LOD_only',
    }


def build(root):
    source_manifest = root / 'PCResources/source_manifest.json'
    manifest = json.loads(source_manifest.read_text(encoding='utf-8-sig'))
    rows = {}
    inputs = []
    for row in manifest['files']:
        filename = 'PCResources/' + row['file']
        path = (root / filename).resolve()
        if not path.is_relative_to(root / 'PCResources') or not path.is_file():
            raise ValueError(f'Missing or outside PCResources: {filename}')
        if filename in rows or not re.fullmatch('[0-9a-f]{64}', row['sha256']):
            raise ValueError(f'Invalid manifest row: {filename}')
        if path.stat().st_size != row['bytes'] or digest(path) != row['sha256']:
            raise ValueError(f'Input checksum mismatch: {filename}')
        rows[filename] = {'file': filename, 'sha256': row['sha256'], 'bytes': row['bytes']}
        inputs.append(rows[filename])
    actual_files = {str(path.relative_to(root)) for path in (root / 'PCResources').rglob('*') if path.is_file()}
    if actual_files != set(rows) | {'PCResources/source_manifest.json'}:
        raise ValueError('Resource tree does not match the verified source manifest')
    source_row = {'file': 'PCResources/source_manifest.json', 'sha256': digest(source_manifest),
                  'bytes': source_manifest.stat().st_size}
    rows[source_row['file']] = source_row
    inputs.append(source_row)

    character_manifest = json.loads((root / 'PCResources/Characters/character_resource_manifest.json').read_text())
    if character_manifest['status'] != 'collected_and_data_validated' or character_manifest['failed_count']:
        raise ValueError('Character collection is incomplete')
    resource_by_file = defaultdict(list)
    resource_by_package = {}
    for resource in character_manifest['resources']:
        resource_by_package[resource['source_package']] = resource
        for file in resource['files']:
            row = rows[file['file']]
            if row['sha256'] != file['sha256'] or row['bytes'] != file['bytes']:
                raise ValueError(f'Character manifest disagrees: {file["file"]}')
            resource_by_file[file['file']].append(resource)

    json_data = {}
    metadata = defaultdict(list)
    metadata_by_name = defaultdict(list)
    metadata_by_file = defaultdict(list)
    for filename in sorted(rows):
        if not filename.endswith('.json'):
            continue
        data = json.loads((root / filename).read_text(encoding='utf-8-sig'))
        json_data[filename] = data
        for obj in objects(data):
            source_package = package(obj.get('Package'))
            if not source_package:
                continue
            entry = {'file': filename, 'source_package': source_package, 'object': obj}
            metadata[source_package].append(entry)
            metadata_by_file[filename].append(entry)
            metadata_by_name[obj.get('Name', '')].append(entry)

    def metadata_priority(entry):
        resource = resource_by_package.get(entry['source_package'])
        preferred = resource and entry['file'] in {f['file'] for f in resource['files']}
        return (0 if preferred else 1, 0 if '/Characters/' in entry['file'] else 1, entry['file'])

    for entries in metadata.values():
        entries.sort(key=metadata_priority)

    unknowns = []
    packages = {}
    package_variants = defaultdict(list)
    file_targets = defaultdict(list)

    def register(item, source_package, preferred=False):
        file_targets[item['file']].append(item['target'])
        if source_package:
            package_variants[source_package].append(item['target'])
            if preferred or source_package not in packages:
                packages[source_package] = item['target']

    def choose_metadata(filename, resource=None, types=None):
        if resource:
            candidates = [entry for file in resource['files'] if file['file'].endswith('.json')
                          for entry in metadata_by_file.get(file['file'], [])]
            candidates = [entry for entry in candidates if entry['source_package'] == resource['source_package']]
        else:
            candidates = metadata_by_name.get(Path(filename).stem, [])
        if types:
            candidates = [entry for entry in candidates if entry['object']['Type'] in types]
        if not candidates:
            return None
        candidates = sorted(candidates, key=lambda entry: (
            0 if '/Characters/' in filename and '/Characters/' in entry['file'] else 1,
            0 if '/Characters/' not in filename and '/Characters/' not in entry['file'] else 1,
            0 if str(Path(filename).with_suffix('')).endswith(entry['source_package'].split('/', 2)[-1]) else 1,
            metadata_priority(entry)))
        return candidates[0]

    meshes = []
    mesh_by_binding = {}
    geometry_channels = {}
    for filename, source in sorted(rows.items()):
        if Path(filename).suffix.lower() not in ('.psk', '.pskx'):
            continue
        if source['sha256'] not in geometry_channels:
            geometry_channels[source['sha256']] = actorx_channels(root / filename)
        channels = geometry_channels[source['sha256']]
        candidates = resource_by_file.get(filename, []) or [None]
        for resource in candidates:
            entry = choose_metadata(filename, resource, {'SkeletalMesh'})
            obj = entry['object'] if entry else {}
            source_package = resource['source_package'] if resource else entry['source_package'] if entry else None
            materials = obj.get('SkeletalMaterials', [])
            lods = obj.get('LODModels', [])
            sections = lods[0].get('Sections', []) if lods else []
            original_slots = [material.get('MaterialSlotName', '') for material in materials]
            original_packages = [package(material.get('Material')) for material in materials]
            exported_slots = [section['MaterialIndex'] for section in sections]
            if entry and (len(exported_slots) != channels['material_slots'] or
                          any(index < 0 or index >= len(materials) for index in exported_slots)):
                raise ValueError(f'Cannot map exported ActorX sections to source materials: {filename}')
            variant = {'file': filename, 'source_package': source_package,
                       'metadata_file': entry['file'] if entry else None,
                       'slot_names': [original_slots[index] for index in exported_slots] if entry else channels['actorx_slot_names'],
                       'material_packages': [original_packages[index] for index in exported_slots] if entry else [None] * channels['material_slots'],
                       'original_slot_names': original_slots, 'original_material_packages': original_packages,
                       'exported_to_source_slots': exported_slots,
                       'section_triangle_counts': [section.get('NumTriangles') for section in sections],
                       'slot_mapping_basis': 'LOD0.Sections order, matching ActorX MATT0000 export order',
                       'skeleton_source': package(obj.get('Properties', {}).get('Skeleton')),
                       'source_scope': 'character_collection' if resource else 'earlier_rendering_snapshot'}
            binding = {key: variant[key] for key in ('slot_names', 'material_packages', 'skeleton_source')}
            identity = 'mesh:' + source['sha256'] + ':' + stable(binding)
            item = mesh_by_binding.get(identity)
            if item is None:
                item = {**source, 'id': identity, 'target': target('Meshes', Path(filename).stem, identity),
                        'source_type': 'SkeletalMesh', 'source_files': [], 'source_packages': [],
                        'source_variants': [], 'metadata_file': None,
                        'channels': geometry_channels[source['sha256']],
                        'skeleton_target': target('Meshes', Path(filename).stem, identity) + '_Skeleton',
                        'conversion_scope': 'editable_LOD0_skeletal_mesh; animation and cloth simulation are not reconstructed'}
                meshes.append(item)
                mesh_by_binding[identity] = item
            if filename not in item['source_files']:
                item['source_files'].append(filename)
            item['source_variants'].append(variant)
            if source_package and source_package not in item['source_packages']:
                item['source_packages'].append(source_package)
            if item['metadata_file'] is None or resource:
                for key in ('source_package', 'metadata_file', 'slot_names', 'material_packages', 'skeleton_source',
                            'original_slot_names', 'original_material_packages', 'exported_to_source_slots',
                            'section_triangle_counts', 'slot_mapping_basis'):
                    item[key] = variant[key]
            register(item, source_package, bool(resource))
            if not entry:
                unknowns.append({'kind': 'mesh_metadata_missing', 'file': filename})
            if not item['slot_names']:
                item['slot_names'] = item['channels']['actorx_slot_names']
                item['material_packages'] = [None] * len(item['slot_names'])

    texture_records = []
    covered_images = set()
    for resource in character_manifest['resources']:
        for file in resource['files']:
            if Path(file['file']).suffix.lower() in ('.png', '.hdr'):
                texture_records.append((file['file'], resource))
                covered_images.add(file['file'])
    for filename in sorted(rows):
        if filename not in covered_images and Path(filename).suffix.lower() in ('.png', '.hdr'):
            texture_records.append((filename, None))

    textures = []
    texture_variants = {}
    for filename, resource in texture_records:
        source = rows[filename]
        entry = choose_metadata(filename, resource, TEXTURE_TYPES)
        obj = entry['object'] if entry else {}
        properties = obj.get('Properties', {})
        source_package = resource['source_package'] if resource else entry['source_package'] if entry else None
        hdr = filename.endswith('.hdr')
        serialized = {key: properties[key] for key in ('SRGB', 'CompressionSettings', 'LODGroup',
                      'Filter', 'AddressX', 'AddressY', 'bFlipGreenChannel', 'NeverStream', 'LODBias') if key in properties}
        settings = {
            'srgb': False if hdr else properties.get('SRGB', True),
            'compression': 'TC_HDR' if hdr else properties.get('CompressionSettings', 'TC_Default'),
            'lod_group': properties.get('LODGroup', 'TEXTUREGROUP_World'),
            'flip_green_channel': properties.get('bFlipGreenChannel', False),
            'address_x': properties.get('AddressX', 'TA_Wrap'),
            'address_y': properties.get('AddressY', 'TA_Wrap'),
            'filter': properties.get('Filter', 'TF_Default'),
            'never_stream': properties.get('NeverStream', False),
            'lod_bias': properties.get('LODBias', 0),
            'source_type': obj.get('Type', 'TextureCube' if hdr else 'Texture2D'),
            'source_pixel_format': obj.get('PixelFormat'),
            'serialized': serialized,
            'basis': {key: 'serialized' if raw in properties else 'stock_UE_default_pending_visual_validation'
                      for key, raw in [('srgb', 'SRGB'), ('compression', 'CompressionSettings'),
                                       ('lod_group', 'LODGroup'), ('flip_green_channel', 'bFlipGreenChannel')]},
        }
        if hdr:
            settings['basis']['srgb'] = 'linear_HDR_payload'
            settings['basis']['compression'] = 'HDR_payload'
            settings['projection'] = 'exported_latlong_Radiance; original_cube_not_byte_identical'
        settings['compression_settings'] = settings['compression']
        # Source type and original pixel format are provenance, not import
        # settings. Explicit-vs-default basis does not split identical settings.
        effective = {key: settings[key] for key in ('srgb', 'compression_settings', 'lod_group', 'flip_green_channel',
                                                   'address_x', 'address_y', 'filter', 'never_stream', 'lod_bias')}
        effective['texture_class'] = 'TextureCube' if hdr else 'Texture2D'
        identity = 'texture:' + source['sha256'] + ':' + stable(effective)
        item = texture_variants.get(identity)
        if item is None:
            item = {**source, 'id': identity, 'target': target('Textures', Path(filename).stem, identity),
                    'source_package': source_package, 'metadata_file': entry['file'] if entry else None,
                    'source_type': obj.get('Type', 'TextureCube' if hdr else 'Texture2D'),
                    'source_files': [], 'source_packages': [], 'source_variants': [],
                    'settings': settings, 'effective_settings': effective,
                    'dimensions': {'width': obj.get('SizeX'), 'height': obj.get('SizeY')},
                    'conversion_scope': 'decoded_image_and_source_settings; source_compressed_blocks_not_recovered'}
            textures.append(item)
            texture_variants[identity] = item
        if filename not in item['source_files']:
            item['source_files'].append(filename)
        if source_package and source_package not in item['source_packages']:
            item['source_packages'].append(source_package)
        item['source_variants'].append({'file': filename, 'source_package': source_package,
                                      'metadata_file': entry['file'] if entry else None, 'settings': settings})
        register(item, source_package, bool(resource))
        if not entry:
            unknowns.append({'kind': 'texture_metadata_missing', 'file': filename,
                             'applied_basis': 'stock_UE_defaults_for_explicitly_unresolved_source'})

    expression_defaults = defaultdict(list)
    for filename, data in sorted(json_data.items()):
        for obj in objects(data):
            props = obj.get('Properties', {})
            expression_type = obj['Type']
            name = props.get('ParameterName')
            owner = package(props.get('Material')) or package(obj.get('Outer'))
            if not name or not owner:
                continue
            kind = None
            value = None
            serialized_value = False
            if expression_type in ('MaterialExpressionTextureSampleParameter2D',
                                    'MaterialExpressionTextureSampleParameterCube',
                                    'MaterialExpressionTextureObjectParameter'):
                kind, value, serialized_value = 'textures', props.get('Texture'), 'Texture' in props
            elif expression_type == 'MaterialExpressionScalarParameter':
                kind, value, serialized_value = 'scalars', props.get('DefaultValue'), 'DefaultValue' in props
            elif expression_type == 'MaterialExpressionVectorParameter':
                kind, value, serialized_value = 'vectors', props.get('DefaultValue'), 'DefaultValue' in props
            elif expression_type in ('MaterialExpressionStaticSwitchParameter', 'MaterialExpressionStaticBoolParameter'):
                kind, value, serialized_value = 'static_switches', props.get('DefaultValue'), 'DefaultValue' in props
            if kind:
                expression_defaults[owner].append({
                    'kind': kind, 'ParameterInfo': {'Name': name, 'Association': 'GlobalParameter', 'Index': -1},
                    'ParameterValue': value, 'value_explicitly_serialized': serialized_value,
                    'source_file': filename, 'expression_type': expression_type,
                    'raw_expression_properties': props,
                })

    materials = []
    material_by_package = {}
    for source_package, entries in sorted(metadata.items()):
        candidates = [entry for entry in entries if entry['object']['Type'] in MATERIAL_TYPES]
        if not candidates:
            continue
        entry = candidates[0]
        obj = entry['object']
        props = obj.get('Properties', {})
        identity = 'material:' + source_package
        item = {**rows[entry['file']], 'id': identity, 'target': target('Materials', obj.get('Name', 'Material'), identity),
                'source_package': source_package, 'metadata_file': entry['file'], 'source_type': obj['Type'],
                'metadata_variants': [{'file': candidate['file'], 'sha256': rows[candidate['file']]['sha256']}
                                      for candidate in candidates],
                'parent_package': package(props.get('Parent')), 'parameters': {
                    'textures': props.get('TextureParameterValues', []),
                    'scalars': props.get('ScalarParameterValues', []),
                    'vectors': props.get('VectorParameterValues', []),
                    'static': props.get('StaticParameters', {}),
                    'static_switches': props.get('StaticParameters', {}).get('StaticSwitchParameters',
                        props.get('StaticSwitchParameterValues', [])),
                },
                'serialized_root_properties': {key: value for key, value in props.items() if key not in PARAMETER_FIELDS},
                'parameter_defaults': expression_defaults.get(source_package, []),
                'conversion_scope': 'stock_material_approximation; raw_source_graph_and_fork_properties_preserved_as_evidence'}
        materials.append(item)
        material_by_package[source_package] = item
        register(item, source_package, True)

    def parameter_key(row):
        info = row.get('ParameterInfo', {})
        return (info.get('Name', row.get('ParameterName', '')), info.get('Association', 'GlobalParameter'), info.get('Index', -1))

    def effective_parameters(item, chain=()):
        source_package = item['source_package']
        if source_package in chain:
            unknowns.append({'kind': 'material_parent_cycle', 'source_package': source_package})
            return {kind: {} for kind in ('textures', 'scalars', 'vectors', 'static_switches')}, []
        parent = material_by_package.get(item['parent_package'])
        if parent:
            effective, ancestry = effective_parameters(parent, chain + (source_package,))
            effective = {kind: dict(values) for kind, values in effective.items()}
        else:
            effective, ancestry = {kind: {} for kind in ('textures', 'scalars', 'vectors', 'static_switches')}, []
        ancestry = ancestry + ([item['parent_package']] if item['parent_package'] else [])
        # Only explicitly serialized defaults enter the effective values. The
        # stripped/missing defaults remain in parameter_defaults as evidence.
        # Character collection expression exports take precedence over older
        # snapshots of the same parent material.
        defaults = sorted(item['parameter_defaults'], key=lambda row: (
            0 if '/Characters/' not in row['source_file'] else 1, row['source_file']))
        for row in defaults:
            if row['value_explicitly_serialized']:
                effective[row['kind']][parameter_key(row)] = {
                    **row, 'source_package': source_package, 'basis': 'serialized_expression_default'}
        for kind in effective:
            for row in item['parameters'][kind]:
                if kind == 'static_switches' and not row.get('bOverride', True):
                    continue
                effective[kind][parameter_key(row)] = {**row, 'source_package': source_package,
                                                      'source_file': item['file'], 'basis': 'serialized_parameter_override'}
        return effective, list(dict.fromkeys(ancestry))

    channel_names = {'base_color': ('BaseColorMap', 'BaseColorTexture', 'DiffuseTexture', 'DiffuseMap', 'AlbedoMap'),
                     'normal': ('NormalMap', 'NormalTexture'),
                     'emissive': ('EmissiveMap', 'EmissionMap', 'EmissiveTexture'),
                     'opacity': ('OpacityMap', 'OpacityTexture')}
    for item in materials:
        effective, ancestry = effective_parameters(item)
        item['parent_target'] = packages.get(item['parent_package'])
        item['parent_chain'] = ancestry
        item['effective_parameters'] = {kind: list(values.values()) for kind, values in effective.items()}
        item['texture_parameters'] = []
        for row in item['effective_parameters']['textures']:
            tex_package = package(row.get('ParameterValue'))
            record = {**row, 'texture_package': tex_package, 'texture_target': packages.get(tex_package)}
            item['texture_parameters'].append(record)
            if tex_package and not record['texture_target']:
                unknowns.append({'kind': 'material_texture_target_missing', 'source_package': item['source_package'],
                                 'texture_package': tex_package, 'parameter_info': row.get('ParameterInfo')})
        by_name = {row.get('ParameterInfo', {}).get('Name', ''): row for row in item['texture_parameters']
                   if row.get('ParameterInfo', {}).get('Association', 'GlobalParameter') == 'GlobalParameter'}
        item['channels'] = {}
        for channel, names in channel_names.items():
            candidate = next((by_name[name] for name in names if name in by_name and by_name[name]['texture_target']), None)
            if candidate:
                item['channels'][channel] = {
                    'texture_package': candidate['texture_package'], 'texture_target': candidate['texture_target'],
                    'target': candidate['texture_target'],
                    'parameter_name': candidate['ParameterInfo']['Name'],
                    'parameter': candidate['ParameterInfo']['Name'],
                    'source_package': candidate['source_package'], 'source_file': candidate['source_file'],
                    'status': 'explicit_parameter_semantic_stock_approximation_not_recovered_output_wiring'}
        item['unconnected_texture_parameters'] = [row['ParameterInfo'].get('Name') for row in item['texture_parameters']
                                                  if row['ParameterInfo'].get('Name') not in
                                                  {c['parameter_name'] for c in item['channels'].values()}]
        item['packed_channel_policy'] = 'RAM/PSAR/ORM/AIRR channels are preserved without inferred BRDF channel wiring.'

    for item in meshes:
        item['material_targets'] = [packages.get(source_package) for source_package in item['material_packages']]
        for variant in item['source_variants']:
            variant['material_targets'] = [packages.get(source_package) for source_package in variant['material_packages']]

    metadata_targets = defaultdict(set)
    for kind in (meshes, textures, materials):
        for item in kind:
            if item.get('metadata_file'):
                metadata_targets[item['metadata_file']].add(item['target'])
                if item.get('skeleton_target'):
                    metadata_targets[item['metadata_file']].add(item['skeleton_target'])
            for variant in item.get('source_variants', []):
                if variant.get('metadata_file'):
                    metadata_targets[variant['metadata_file']].add(item['target'])
                    if item.get('skeleton_target'):
                        metadata_targets[variant['metadata_file']].add(item['skeleton_target'])

    documents = []
    for filename, source in sorted(rows.items()):
        if Path(filename).suffix.lower() not in ('.json', '.md'):
            continue
        data = json_data.get(filename)
        object_rows = objects(data) if data is not None else []
        types = sorted({obj['Type'] for obj in object_rows})
        source_packages = sorted({value for obj in object_rows if (value := package(obj.get('Package')))})
        identity = 'document:' + filename
        item = {**source, 'id': identity, 'target': target('Documents', Path(filename).stem, identity),
                'source_package': source_packages[0] if len(source_packages) == 1 else None,
                'metadata_file': filename, 'source_type': types[0] if len(types) == 1 else 'SourceEvidence',
                'source_types': types, 'source_packages': source_packages,
                'format': 'json' if filename.endswith('.json') else 'markdown',
                'imported_assets': sorted(metadata_targets[filename]),
                'editor_only': True, 'conversion_scope': 'raw_source_document_archive; not reconstructed executable asset'}
        documents.append(item)
        file_targets[filename].append(item['target'])
        # Dedicated converted textures/materials/meshes take priority. Other
        # source package references can still resolve to an evidence archive.
        for source_package in source_packages:
            if source_package not in packages:
                packages[source_package] = item['target']
            package_variants[source_package].append(item['target'])

    arrays = {'meshes': meshes, 'textures': textures, 'materials': materials, 'documents': documents}
    all_items = [item for items in arrays.values() for item in items]
    targets = [item['target'] for item in all_items]
    if len(targets) != len(set(targets)):
        raise ValueError('Duplicate planned UE target')
    if any(not re.fullmatch(r'/Game/PCAssets/(Meshes|Textures|Materials|Documents)/[0-9a-f]{2}/[A-Za-z0-9_]+', value)
           or len(value) > 160 for value in targets):
        raise ValueError('Invalid or excessive UE target path')
    covered = {filename for item in all_items for filename in item.get('source_files', [item['file']])}
    if set(rows) - covered:
        raise ValueError('Source files omitted from the import plan: ' + repr(sorted(set(rows) - covered)))
    counts = {kind: len(items) for kind, items in arrays.items()}
    counts.update({'verified_input_files': len(inputs), 'verified_input_bytes': sum(row['bytes'] for row in inputs),
                   'source_packages': len(packages), 'source_character_mesh_packages': character_manifest['mesh_package_count'],
                   'unique_input_geometry': len(geometry_channels),
                   'meshes_over_four_influences': sum(item['channels']['max_influences'] > 4 for item in meshes),
                   'maximum_influences': max(item['channels']['max_influences'] for item in meshes),
                   'unknowns': len(unknowns), 'document_source_types': dict(Counter(
                       value for item in documents for value in item['source_types']))})
    return {
        'schema_version': 1, 'status': 'inputs_verified_import_pending', 'target_root': '/Game/PCAssets',
        'source_manifest': source_row['file'], 'source_manifest_sha256': source_row['sha256'],
        'inputs': sorted(inputs, key=lambda row: row['file']), 'counts': counts,
        'resume_policy': 'Stable target and item_sha256; skip only after current input hashes and fresh editor asset validation match.',
        'limitations': ['Editable geometry uses the highest exported LOD; unexported LODs are not synthesized.',
                       'No new playable animation, original cloth simulation, Niagara, Blueprint, or private shader is claimed.',
                       'Material parameter candidates are stock approximations with raw source evidence retained.',
                       'Omitted source defaults use explicitly recorded stock UE defaults and require visual validation.',
                       'This plan does not modify the existing viewer Catalog or environments.'],
        'packages': dict(sorted(packages.items())),
        'package_variants': {key: sorted(set(values)) for key, values in sorted(package_variants.items())},
        'files': {key: sorted(set(values)) for key, values in sorted(file_targets.items())},
        **{kind: sorted(items, key=lambda item: item['target']) for kind, items in arrays.items()},
        'unknowns': unknowns,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--output', type=Path, help='Defaults to Saved/PCAssetConversion/asset_plan.json under root')
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output or root / 'Saved/PCAssetConversion/asset_plan.json'
    plan = build(root)
    for kind in ('meshes', 'textures', 'materials', 'documents'):
        for item in plan[kind]:
            item['item_sha256'] = hashlib.sha256(stable(item).encode()).hexdigest()
    encoded = (json.dumps(plan, ensure_ascii=False, indent=2) + '\n').encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    if not output.is_file() or output.read_bytes() != encoded:
        pending = output.with_suffix(output.suffix + '.new')
        pending.write_bytes(encoded)
        pending.replace(output)
    print(json.dumps({'status': plan['status'], 'plan': str(output), 'plan_sha256': digest(output),
                      'counts': plan['counts']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
