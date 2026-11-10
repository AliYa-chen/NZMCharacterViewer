"""Source-aware stock UE material candidates for the PC asset importer.

Only build_or_update_material(row, plan, existing=None) is public. The caller
owns final source metadata and package saving. This module never modifies
source textures, Engine assets, the viewer Catalog, or the import plan.

The stock graph is an approximation. Original Hair/Eye/SSS BRDFs, packed
RAM/PSAR/ORM wiring, material functions and animated effects are not recovered.
"""
import json
import math

import unreal


ADAPTER_VERSION = 'pc_material_adapter_v1'
SUPPORT_OWNER_TAG = 'PCAssetConversion.SupportOwner'
SOURCE_HASH_TAG = 'PCAssetConversion.SourceSHA256'
ADAPTER_TAG = 'PCAssetConversion.MaterialAdapter'
SUPPORT_ROOT = '/Game/PCAssets/Support'
ENGINE_WHITE = '/Engine/EngineResources/WhiteSquareTexture.WhiteSquareTexture'
ENGINE_BLACK = '/Engine/EngineResources/Black.Black'
ENGINE_NORMAL = '/Engine/EngineMaterials/DefaultNormal.DefaultNormal'
SUPPORT_ASSETS = {
    'linear_white': (SUPPORT_ROOT + '/T_PC_LinearWhite', ENGINE_WHITE, 'TC_DEFAULT'),
    'linear_black': (SUPPORT_ROOT + '/T_PC_LinearBlack', ENGINE_BLACK, 'TC_DEFAULT'),
    'masks_white': (SUPPORT_ROOT + '/T_PC_MasksWhite', ENGINE_WHITE, 'TC_MASKS'),
    'masks_black': (SUPPORT_ROOT + '/T_PC_MasksBlack', ENGINE_BLACK, 'TC_MASKS'),
}
_SUPPORT_CACHE = {}
_PLAN_INDEX_CACHE = {}


def _index(plan):
    key = id(plan)
    if key not in _PLAN_INDEX_CACHE:
        _PLAN_INDEX_CACHE[key] = {
            'materials': {item['target']: item for item in plan['materials']},
            'textures': {item['target']: item for item in plan['textures']},
        }
    return _PLAN_INDEX_CACHE[key]


def _enum(cls, value):
    return getattr(cls, str(value).split('::')[-1].upper())


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _color(value):
    if isinstance(value, dict) and all(_finite(value.get(key)) for key in ('R', 'G', 'B')):
        return [float(value['R']), float(value['G']), float(value['B']), float(value.get('A', 1.0))]
    return None


def _params(row, kind):
    values = (row.get('effective_parameters') or row.get('parameters') or {}).get(kind, [])
    if isinstance(values, dict):
        return {name: {'ParameterValue': value.get('value', value.get('ParameterValue'))
                       if isinstance(value, dict) else value, 'ParameterInfo': {'Name': name}}
                for name, value in values.items()}
    return {(value.get('ParameterInfo') or {}).get('Name', value.get('name', '')): value
            for value in values
            if (value.get('ParameterInfo') or {}).get('Association', 'GlobalParameter') == 'GlobalParameter'}


def _source_property_state(row, plan, chain=()):
    """Resolve only native stock-compatible base fields, keeping evidence."""
    target = row['target']
    if target in chain:
        raise ValueError('Material parent cycle: ' + target)
    native_defaults = {'BlendMode': 'BLEND_Opaque', 'TwoSided': False,
                       'OpacityMaskClipValue': 0.333333, 'bCastDynamicShadowAsMasked': False}
    parent = _index(plan)['materials'].get(row.get('parent_target'))
    if parent:
        state = _source_property_state(parent, plan, chain + (target,))
        values = dict(state['values'])
        basis = dict(state['basis'])
    else:
        values = dict(native_defaults)
        basis = {name: 'stock_UE_default_not_proven_source_fork_default' for name in values}
    props = row.get('serialized_root_properties') or {}
    applied, ignored, unresolved = {}, {}, []
    if parent:
        overrides = props.get('BasePropertyOverrides') or {}
        flags = {'BlendMode': 'bOverride_BlendMode', 'TwoSided': 'bOverride_TwoSided',
                 'OpacityMaskClipValue': 'bOverride_OpacityMaskClipValue',
                 'bCastDynamicShadowAsMasked': 'bOverride_CastDynamicShadowAsMasked'}
        for name, flag in flags.items():
            if overrides.get(flag) is True:
                value = overrides.get(name, native_defaults[name])
                values[name] = value
                applied[name] = value
                basis[name] = ('serialized_base_override' if name in overrides
                               else 'enabled_override_without_serialized_value_uses_stock_struct_default')
                if name not in overrides:
                    unresolved.append({'field': name, 'reason': basis[name], 'value': value})
            elif name in overrides:
                ignored[name] = {'value': overrides[name], 'reason': 'override_flag_not_explicit_true'}
    else:
        for name in values:
            if name in props:
                values[name] = props[name]
                applied[name] = props[name]
                basis[name] = 'serialized_root_property'
    return {'values': values, 'basis': basis, 'own_applied': applied,
            'ignored_serialized_values': ignored, 'unresolved': unresolved}


def _support(name):
    if name in _SUPPORT_CACHE:
        return _SUPPORT_CACHE[name]
    path, source, compression = SUPPORT_ASSETS[name]
    lib = unreal.EditorAssetLibrary
    asset = lib.load_asset(path) if lib.does_asset_exist(path) else None
    if asset is not None:
        if not isinstance(asset, unreal.Texture2D) or lib.get_metadata_tag(asset, SUPPORT_OWNER_TAG) != ADAPTER_VERSION:
            raise RuntimeError('Refusing to reuse an unowned support asset: ' + path)
    else:
        asset = lib.duplicate_asset(source, path)
        if not isinstance(asset, unreal.Texture2D):
            raise RuntimeError('Could not duplicate a stock support texture: ' + path)
        lib.set_metadata_tag(asset, SUPPORT_OWNER_TAG, ADAPTER_VERSION)
    asset.set_editor_property('srgb', False)
    asset.set_editor_property('compression_settings', getattr(unreal.TextureCompressionSettings, compression))
    if asset.get_editor_property('srgb') or asset.get_editor_property('compression_settings') != getattr(unreal.TextureCompressionSettings, compression):
        raise RuntimeError('Support texture settings did not persist: ' + path)
    if not lib.save_loaded_asset(asset, only_if_is_dirty=False):
        raise RuntimeError('Could not save support texture: ' + path)
    _SUPPORT_CACHE[name] = asset
    return asset


def _engine_texture(path, normal=False):
    # Built-in Engine packages need not have an AssetRegistry entry in a
    # headless commandlet. Resolve the exact exported UObject directly.
    # This loads the stock texture without scanning or modifying Engine data.
    asset = unreal.load_object(None, path)
    if not isinstance(asset, unreal.Texture2D):
        raise RuntimeError('Missing stock default texture: ' + path)
    if normal:
        if asset.get_editor_property('compression_settings') != unreal.TextureCompressionSettings.TC_NORMALMAP:
            raise RuntimeError('Stock normal does not have normal-map compression: ' + path)
    elif not asset.get_editor_property('srgb'):
        raise RuntimeError('Stock color default does not have sRGB enabled: ' + path)
    return asset


def _node(material, cls, x, y):
    node = unreal.MaterialEditingLibrary.create_material_expression(material, cls, x, y)
    if node is None:
        raise RuntimeError('Could not create ' + cls.__name__)
    return node


def _connect(source, output, destination, input_name):
    if not unreal.MaterialEditingLibrary.connect_material_expressions(source, output, destination, input_name):
        raise RuntimeError('Could not connect material expression input ' + input_name)


def _property(source, output, prop):
    if not unreal.MaterialEditingLibrary.connect_material_property(source, output, prop):
        raise RuntimeError('Could not connect material output ' + str(prop))


def _scalar(material, name, value, x, y, nodes):
    node = _node(material, unreal.MaterialExpressionScalarParameter, x, y)
    node.set_editor_property('parameter_name', name)
    node.set_editor_property('group', 'PC stock approximation')
    node.set_editor_property('default_value', float(value))
    nodes[name] = node
    return node


def _vector(material, name, value, x, y, nodes):
    node = _node(material, unreal.MaterialExpressionVectorParameter, x, y)
    node.set_editor_property('parameter_name', name)
    node.set_editor_property('group', 'PC stock approximation')
    node.set_editor_property('default_value', unreal.LinearColor(*value))
    nodes[name] = node
    return node


def _sample(material, name, texture, sampler, x, y, nodes):
    node = _node(material, unreal.MaterialExpressionTextureSampleParameter2D, x, y)
    node.set_editor_property('parameter_name', name)
    node.set_editor_property('group', 'PC stock approximation')
    node.set_editor_property('texture', texture)
    node.set_editor_property('sampler_type', sampler)
    nodes[name] = node
    return node


def _multiply(material, first, first_output, second, second_output, x, y):
    node = _node(material, unreal.MaterialExpressionMultiply, x, y)
    _connect(first, first_output, node, 'A')
    _connect(second, second_output, node, 'B')
    return node


def _lerp(material, first, first_output, second, second_output, alpha, x, y):
    node = _node(material, unreal.MaterialExpressionLinearInterpolate, x, y)
    _connect(first, first_output, node, 'A')
    _connect(second, second_output, node, 'B')
    _connect(alpha, '', node, 'Alpha')
    return node


def _build_graph(material):
    edit = unreal.MaterialEditingLibrary
    edit.delete_all_material_expressions(material)
    nodes = {}
    white = _engine_texture(ENGINE_WHITE)
    black = _engine_texture(ENGINE_BLACK)
    normal = _engine_texture(ENGINE_NORMAL, normal=True)
    samplers = unreal.MaterialSamplerType
    base = _sample(material, 'PC_BaseColor', white, samplers.SAMPLERTYPE_COLOR, -1500, 0, nodes)
    normal_node = _sample(material, 'PC_Normal', normal, samplers.SAMPLERTYPE_NORMAL, -1500, 500, nodes)
    multiplier = _vector(material, 'PC_BaseColorMultiplier', [1, 1, 1, 1], -1500, 220, nodes)
    tint = _vector(material, 'PC_BaseColorTint', [1, 1, 1, 1], -1500, 340, nodes)
    tint_weight = _scalar(material, 'PC_BaseColorTintWeight', 0, -1200, 340, nodes)
    multiplied = _multiply(material, base, 'RGB', multiplier, 'RGB', -1150, 0)
    tinted = _lerp(material, multiplied, '', tint, 'RGB', tint_weight, -850, 0)
    _property(tinted, '', unreal.MaterialProperty.MP_BASE_COLOR)
    _property(normal_node, 'RGB', unreal.MaterialProperty.MP_NORMAL)

    emissive_color = _sample(material, 'PC_EmissiveColor', black, samplers.SAMPLERTYPE_COLOR, -1500, 800, nodes)
    emissive_linear = _sample(material, 'PC_EmissiveLinear', _support('linear_black'), samplers.SAMPLERTYPE_LINEAR_COLOR, -1500, 1060, nodes)
    emissive_masks = _sample(material, 'PC_EmissiveMasks', _support('masks_black'), samplers.SAMPLERTYPE_MASKS, -1500, 1320, nodes)
    linear_select = _scalar(material, 'PC_EmissiveUseLinear', 0, -1150, 1100, nodes)
    masks_select = _scalar(material, 'PC_EmissiveUseMasks', 0, -1150, 1370, nodes)
    emission_color = _vector(material, 'PC_EmissionColor', [1, 1, 1, 1], -800, 1160, nodes)
    emission_scale = _scalar(material, 'PC_EmissionScale', 1, -800, 1350, nodes)
    mix = _lerp(material, emissive_color, 'RGB', emissive_linear, 'RGB', linear_select, -800, 800)
    mix = _lerp(material, mix, '', emissive_masks, 'RGB', masks_select, -500, 800)
    mix = _multiply(material, mix, '', emission_color, 'RGB', -200, 800)
    mix = _multiply(material, mix, '', emission_scale, '', 100, 800)
    _property(mix, '', unreal.MaterialProperty.MP_EMISSIVE_COLOR)

    opacity_color = _sample(material, 'PC_OpacityColor', white, samplers.SAMPLERTYPE_COLOR, -1500, 1650, nodes)
    opacity_linear = _sample(material, 'PC_OpacityLinear', _support('linear_white'), samplers.SAMPLERTYPE_LINEAR_COLOR, -1500, 1910, nodes)
    opacity_masks = _sample(material, 'PC_OpacityMasks', _support('masks_white'), samplers.SAMPLERTYPE_MASKS, -1500, 2170, nodes)
    opacity_linear_select = _scalar(material, 'PC_OpacityUseLinear', 0, -1150, 1980, nodes)
    opacity_masks_select = _scalar(material, 'PC_OpacityUseMasks', 0, -1150, 2240, nodes)
    opacity_texture_select = _scalar(material, 'PC_OpacityUseTexture', 0, -800, 1980, nodes)
    opacity_scale = _scalar(material, 'PC_Opacity', 1, -500, 2240, nodes)
    mask = _lerp(material, opacity_color, 'R', opacity_linear, 'R', opacity_linear_select, -800, 1650)
    mask = _lerp(material, mask, '', opacity_masks, 'R', opacity_masks_select, -500, 1650)
    mask = _lerp(material, base, 'A', mask, '', opacity_texture_select, -200, 1650)
    mask = _multiply(material, mask, '', opacity_scale, '', 100, 1650)
    _property(mask, '', unreal.MaterialProperty.MP_OPACITY_MASK)
    _property(mask, '', unreal.MaterialProperty.MP_OPACITY)
    for index, (name, default, prop) in enumerate((
        ('PC_Roughness', .5, unreal.MaterialProperty.MP_ROUGHNESS),
        ('PC_Metallic', 0., unreal.MaterialProperty.MP_METALLIC),
        ('PC_Specular', .5, unreal.MaterialProperty.MP_SPECULAR),
    )):
        scalar = _scalar(material, name, default, 100, 2100 + index * 160, nodes)
        _property(scalar, '', prop)
    edit.set_material_usage(material, unreal.MaterialUsage.MATUSAGE_SKELETAL_MESH)
    material.set_editor_property('shading_model', unreal.MaterialShadingModel.MSM_DEFAULT_LIT)
    material.set_editor_property('translucency_lighting_mode', unreal.TranslucencyLightingMode.TLM_SURFACE_PER_PIXEL_LIGHTING)
    return nodes


def _apply_properties(asset, row, state, instance):
    props = {'BlendMode': ('blend_mode', unreal.BlendMode), 'TwoSided': ('two_sided', None),
             'OpacityMaskClipValue': ('opacity_mask_clip_value', None),
             'bCastDynamicShadowAsMasked': ('cast_dynamic_shadow_as_masked', None)}
    if instance:
        overrides = unreal.MaterialInstanceBasePropertyOverrides()
        for source, value in state['own_applied'].items():
            name, enum_class = props[source]
            overrides.set_editor_property('override_' + name, True)
            overrides.set_editor_property(name, _enum(enum_class, value) if enum_class else value)
        asset.set_editor_property('base_property_overrides', overrides)
        actual = asset.get_editor_property('base_property_overrides')
        for source, value in state['own_applied'].items():
            name, enum_class = props[source]
            expected = _enum(enum_class, value) if enum_class else value
            if not actual.get_editor_property('override_' + name):
                raise RuntimeError('Material base override flag did not persist: ' + name)
            found = actual.get_editor_property(name)
            if isinstance(expected, float):
                if abs(float(found) - expected) > 1e-6:
                    raise RuntimeError('Material base override value did not persist: ' + name)
            elif found != expected:
                raise RuntimeError('Material base override value did not persist: ' + name)
    else:
        for source, value in state['values'].items():
            name, enum_class = props[source]
            asset.set_editor_property(name, _enum(enum_class, value) if enum_class else value)


def _sampler_kind(texture):
    compression = texture.get_editor_property('compression_settings')
    if compression == unreal.TextureCompressionSettings.TC_NORMALMAP:
        return 'normal'
    if compression == unreal.TextureCompressionSettings.TC_MASKS:
        return 'masks' if not texture.get_editor_property('srgb') else 'unsupported_srgb_masks'
    if compression in (unreal.TextureCompressionSettings.TC_DEFAULT, unreal.TextureCompressionSettings.TC_BC7):
        return 'color' if texture.get_editor_property('srgb') else 'linear'
    return 'unsupported_compression_' + str(compression)


def _candidate_texture(candidate, result):
    target = candidate.get('target') or candidate.get('texture_target')
    texture = unreal.EditorAssetLibrary.load_asset(target) if target else None
    if not isinstance(texture, unreal.Texture2D):
        result['unresolved'].append({'candidate': candidate, 'reason': 'candidate_is_not_imported_Texture2D'})
        return None
    return texture


def _setters(asset, nodes):
    edit = unreal.MaterialEditingLibrary
    if nodes is not None:
        def set_scalar(name, value):
            nodes[name].set_editor_property('default_value', float(value))
        def set_vector(name, value):
            nodes[name].set_editor_property('default_value', unreal.LinearColor(*value))
        def set_texture(name, value):
            nodes[name].set_editor_property('texture', value)
    else:
        def set_scalar(name, value):
            # UE5.8's MaterialEditingLibrary setters perform the update but
            # leave their bool result false. Verify through the getter instead.
            edit.set_material_instance_scalar_parameter_value(asset, name, float(value))
            actual = float(edit.get_material_instance_scalar_parameter_value(asset, name))
            if not math.isclose(actual, float(value), rel_tol=0., abs_tol=1e-5):
                raise RuntimeError(f'Canonical scalar did not persist: {name}: {actual} != {value}')
        def set_vector(name, value):
            edit.set_material_instance_vector_parameter_value(asset, name, unreal.LinearColor(*value))
            actual = edit.get_material_instance_vector_parameter_value(asset, name)
            components = [float(actual.r), float(actual.g), float(actual.b), float(actual.a)]
            if not all(math.isclose(found, float(expected), rel_tol=0., abs_tol=1e-5)
                       for found, expected in zip(components, value)):
                raise RuntimeError(f'Canonical vector did not persist: {name}: {components} != {value}')
        def set_texture(name, value):
            edit.set_material_instance_texture_parameter_value(asset, name, value)
            actual = edit.get_material_instance_texture_parameter_value(asset, name)
            if actual is None or actual.get_path_name() != value.get_path_name():
                found = actual.get_path_name() if actual is not None else None
                raise RuntimeError(f'Canonical texture did not persist: {name}: {found} != {value.get_path_name()}')
    return set_scalar, set_vector, set_texture


def _apply_parameters(asset, row, plan, nodes, state, result):
    set_scalar, set_vector, set_texture = _setters(asset, nodes)
    used = set()
    # Apply complete effective state so a descendant never inherits a stale
    # Color/Linear selector from an unrelated prior conversion attempt.
    for name in ('PC_EmissiveUseLinear', 'PC_EmissiveUseMasks', 'PC_OpacityUseLinear',
                 'PC_OpacityUseMasks', 'PC_OpacityUseTexture', 'PC_BaseColorTintWeight'):
        set_scalar(name, 0)
    defaults = {'PC_Roughness': .5, 'PC_Metallic': 0, 'PC_Specular': .5, 'PC_EmissionScale': 1, 'PC_Opacity': 1}
    for name, value in defaults.items():
        set_scalar(name, value)
    set_vector('PC_BaseColorMultiplier', [1, 1, 1, 1])
    set_vector('PC_BaseColorTint', [1, 1, 1, 1])
    set_vector('PC_EmissionColor', [1, 1, 1, 1])
    channels = row.get('channels') or {}
    for channel in ('base_color', 'normal', 'emissive'):
        candidate = channels.get(channel)
        if not candidate:
            continue
        texture = _candidate_texture(candidate, result)
        if texture is None:
            continue
        kind = _sampler_kind(texture)
        if channel == 'base_color' and kind == 'color':
            parameter = 'PC_BaseColor'
        elif channel == 'normal' and kind == 'normal':
            parameter = 'PC_Normal'
        elif channel == 'emissive' and kind in ('color', 'linear', 'masks'):
            parameter = {'color': 'PC_EmissiveColor', 'linear': 'PC_EmissiveLinear', 'masks': 'PC_EmissiveMasks'}[kind]
            set_scalar('PC_EmissiveUseLinear', float(kind == 'linear'))
            set_scalar('PC_EmissiveUseMasks', float(kind == 'masks'))
        else:
            result['unresolved'].append({'channel': channel, 'target': texture.get_path_name(),
                                         'reason': 'unsupported_source_sampler_semantics', 'sampler_kind': kind})
            continue
        set_texture(parameter, texture)
        used.add(('textures', candidate.get('parameter', candidate.get('parameter_name'))))
        result['connected'].append({'channel': channel, 'parameter': parameter, 'sampler_kind': kind,
                                    'target': texture.get_path_name(), 'source': candidate,
                                    'basis': 'explicit_source_parameter_semantic_stock_approximation'})

    scalars = _params(row, 'scalars')
    vectors = _params(row, 'vectors')
    for source, parameter, minimum, maximum in (
        ('Roughness', 'PC_Roughness', 0, 1), ('Metallic', 'PC_Metallic', 0, 1),
        ('Specular', 'PC_Specular', 0, 1), ('EmissionScale', 'PC_EmissionScale', 0, None),
        ('Opacity', 'PC_Opacity', 0, 1),
    ):
        evidence = scalars.get(source)
        value = evidence.get('ParameterValue') if evidence else None
        if _finite(value):
            adapted = max(minimum, float(value))
            if maximum is not None:
                adapted = min(maximum, adapted)
            set_scalar(parameter, adapted)
            used.add(('scalars', source))
            result['connected'].append({'source_parameter': source, 'parameter': parameter, 'value': adapted,
                                        'source_value': value, 'source_evidence': evidence,
                                        'basis': 'serialized_scalar_stock_range_adaptation'})
    for source in ('EmissionColor', 'EmissiveColor', '[Emissive] Color Tint'):
        evidence = vectors.get(source)
        value = _color(evidence.get('ParameterValue')) if evidence else None
        if value:
            set_vector('PC_EmissionColor', value)
            used.add(('vectors', source))
            result['connected'].append({'source_parameter': source, 'parameter': 'PC_EmissionColor', 'value': value,
                                        'source_evidence': evidence, 'basis': 'serialized_emission_tint_stock_multiply_approximation'})
            break
    tint = vectors.get('BaseColor')
    weight = scalars.get('bUsedColorTint')
    tint_value = _color(tint.get('ParameterValue')) if tint else None
    weight_value = weight.get('ParameterValue') if weight else None
    if tint_value is not None and _finite(weight_value):
        adapted_weight = min(1., max(0., float(weight_value)))
        set_vector('PC_BaseColorTint', tint_value)
        set_scalar('PC_BaseColorTintWeight', adapted_weight)
        used.update({('vectors', 'BaseColor'), ('scalars', 'bUsedColorTint')})
        result['connected'].append({'source_parameters': ['BaseColor', 'bUsedColorTint'],
                                    'parameters': ['PC_BaseColorTint', 'PC_BaseColorTintWeight'],
                                    'color': tint_value, 'weight': adapted_weight,
                                    'basis': 'documented_stock_lerp_preserves_explicit_disabled_tint'})
    elif vectors.get('ColorTint'):
        tint_value = _color(vectors['ColorTint'].get('ParameterValue'))
        if tint_value:
            set_vector('PC_BaseColorMultiplier', tint_value)
            used.add(('vectors', 'ColorTint'))
            result['connected'].append({'source_parameter': 'ColorTint', 'parameter': 'PC_BaseColorMultiplier',
                                        'value': tint_value, 'basis': 'serialized_color_tint_semantic_stock_multiply_approximation'})

    opacity = channels.get('opacity')
    opacity_basis = 'explicit_OpacityMap_R_stock_approximation_not_recovered_output_wiring'
    # AIRR R has repo-recorded contact-sheet and bind-pose UV silhouette
    # evidence. It is used solely for opacity, never ID/roughness/root BRDF.
    if opacity is None:
        ancestry = [row.get('source_package', '')] + row.get('parent_chain', [])
        hair = any('/Hair/' in value or 'HairBlend' in value for value in ancestry)
        airr = next((value for value in row.get('texture_parameters', [])
                     if value.get('ParameterInfo', {}).get('Name') == 'Alpha_ID_Roughness_Root'
                     and value.get('texture_target')), None)
        if hair and airr:
            opacity = {**airr, 'target': airr['texture_target'], 'parameter': 'Alpha_ID_Roughness_Root'}
            opacity_basis = 'AIRR_R_visual_silhouette_evidence_stock_opacity_approximation_from_material_policy_py'
    if opacity:
        texture = _candidate_texture(opacity, result)
        if texture:
            kind = _sampler_kind(texture)
            parameter = {'color': 'PC_OpacityColor', 'linear': 'PC_OpacityLinear', 'masks': 'PC_OpacityMasks'}.get(kind)
            if parameter:
                set_texture(parameter, texture)
                set_scalar('PC_OpacityUseLinear', float(kind == 'linear'))
                set_scalar('PC_OpacityUseMasks', float(kind == 'masks'))
                set_scalar('PC_OpacityUseTexture', 1)
                used.add(('textures', opacity.get('parameter', opacity.get('parameter_name'))))
                result['connected'].append({'channel': 'opacity', 'parameter': parameter, 'sampled_channel': 'R',
                                            'target': texture.get_path_name(), 'sampler_kind': kind, 'basis': opacity_basis})
            else:
                result['unresolved'].append({'channel': 'opacity', 'target': texture.get_path_name(), 'sampler_kind': kind})
    if not any(value.get('channel') == 'opacity' for value in result['connected']):
        if 'base_color' in channels:
            result['approximations'].append({'channel': 'opacity', 'basis': 'source_base_color_alpha_stock_candidate',
                                             'not_proven': 'original_Opacity_or_OpacityMask_output_wiring'})
        elif state['values']['BlendMode'] != 'BLEND_Opaque':
            result['unresolved'].append({'channel': 'opacity', 'basis': 'stock_white_alpha_constant_one',
                                         'reason': 'no_explicit_opacity_or_base_color_source; silhouette_not_recovered'})
    result['unused_source_parameters'] = [
        {'kind': kind, 'name': name} for kind in ('textures', 'scalars', 'vectors', 'static_switches')
        for name in _params(row, kind) if (kind, name) not in used
    ]
    result['packed_policy'] = {
        'RAM_PSAR_ORM': 'preserved_without_inferred_channel_to_BRDF_wiring',
        'AIRR': 'only_R_opacity_uses_documented_visual_approximation; ID_roughness_root_remain_unconnected',
    }


def build_or_update_material(row, plan, existing=None):
    """Return (editable UMaterial/UMaterialInstanceConstant, evidence report).

    Parent assets must already exist. Existing targets are reusable only when
    their PCAssetConversion.SourceSHA256 matches this source row. The caller
    must mark its import version/source metadata and save the returned asset.
    """
    target = row['target']
    if not target.startswith('/Game/PCAssets/Materials/'):
        raise ValueError('Material target outside isolated resource tree: ' + target)
    lib = unreal.EditorAssetLibrary
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    edit = unreal.MaterialEditingLibrary
    if existing is None and lib.does_asset_exist(target):
        existing = lib.load_asset(target)
    if existing is not None and lib.get_metadata_tag(existing, SOURCE_HASH_TAG) != row['sha256']:
        raise RuntimeError('Refusing to update an unrecognized material: ' + target)
    state = _source_property_state(row, plan)
    result = {'adapter': ADAPTER_VERSION, 'target': target, 'parent': row.get('parent_target'),
              'appearance': 'stock_PBR_candidate_original_shader_not_reconstructed',
              'base_properties': state, 'connected': [], 'unresolved': list(state['unresolved']),
              'approximations': [
                  'Stock DefaultLit replaces unrecovered private Hair/Eye/SSS BRDFs.',
                  'Explicit texture parameter semantics guide the candidate; final cooked output wiring is incomplete.',
                  'Source texture color-space/compression settings are unchanged.',
              ], 'support_assets': [value[0] for value in SUPPORT_ASSETS.values()]}
    destination, name = target.rsplit('/', 1)
    parent_target = row.get('parent_target')
    if parent_target:
        parent = lib.load_asset(parent_target)
        if not isinstance(parent, unreal.MaterialInterface):
            raise RuntimeError('Required parent material has not been imported: ' + parent_target)
        asset = existing or tools.create_asset(name, destination, unreal.MaterialInstanceConstant,
                                                unreal.MaterialInstanceConstantFactoryNew())
        if not isinstance(asset, unreal.MaterialInstanceConstant):
            raise RuntimeError('Expected a MaterialInstanceConstant: ' + target)
        edit.set_material_instance_parent(asset, parent)
        edit.clear_all_material_instance_parameters(asset)
        nodes = None
    else:
        asset = existing or tools.create_asset(name, destination, unreal.Material, unreal.MaterialFactoryNew())
        if not isinstance(asset, unreal.Material):
            raise RuntimeError('Expected a Material root: ' + target)
        nodes = _build_graph(asset)
    lib.set_metadata_tag(asset, SOURCE_HASH_TAG, row['sha256'])
    lib.set_metadata_tag(asset, ADAPTER_TAG, ADAPTER_VERSION)
    _apply_properties(asset, row, state, bool(parent_target))
    _apply_parameters(asset, row, plan, nodes, state, result)
    if parent_target:
        edit.update_material_instance(asset)
    else:
        errors = list(edit.recompile_material(asset))
        if errors:
            raise RuntimeError('Stock material graph failed to compile: ' + json.dumps(errors))
    result['class'] = asset.get_class().get_name()
    result['source_root_properties_not_mapped_to_stock'] = [
        key for key in (row.get('serialized_root_properties') or {})
        if key not in ('BlendMode', 'TwoSided', 'OpacityMaskClipValue', 'bCastDynamicShadowAsMasked', 'BasePropertyOverrides')
    ]
    own_overrides = (row.get('serialized_root_properties') or {}).get('BasePropertyOverrides') or {}
    supported_flags = {'bOverride_BlendMode', 'bOverride_TwoSided',
                       'bOverride_OpacityMaskClipValue', 'bOverride_CastDynamicShadowAsMasked'}
    result['unmapped_enabled_base_overrides'] = {
        key: value for key, value in own_overrides.items()
        if key.startswith('bOverride_') and value is True and key not in supported_flags
    }
    return asset, result
