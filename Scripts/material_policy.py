"""Source-aware stock UE material adaptation; no claim of original custom BRDF.

Keeps the manifest immutable. Cooked RAM/PSAR output graphs are stripped, so
unproven packed channels are retained as evidence rather than wired by name.
This module is engine independent and can be audited without starting Editor.
"""
from copy import deepcopy
import json
from pathlib import Path

_INDEX = {}
_TEXTURE_INDEX = {}


def _index(root):
    key = str(root)
    if key not in _INDEX:
        paths = list(root.rglob('*.json'))
        result = {}
        # Prefer original VMI properties over decoded duplicate exports.
        paths.sort(key=lambda p: (0 if 'Evidence/VMI_Properties/' in p.as_posix() else 1, str(p)))
        for p in paths:
            result.setdefault(p.stem, p)
        _INDEX[key] = result
    return _INDEX[key]


def _texture_file(root, reference, character):
    package = reference.get('ObjectPath', '').split('.', 1)[0]
    if package.startswith('/Game/'):
        package = 'NZM/Content/' + package[len('/Game/'):]
    direct = root / character / 'DecodedResources' / (package + '.png')
    if direct.is_file():
        return str(direct.relative_to(root))
    key = str(root)
    if key not in _TEXTURE_INDEX:
        _TEXTURE_INDEX[key] = {}
        for file in sorted(root.rglob('*.png')):
            _TEXTURE_INDEX[key].setdefault(file.stem, file)
    file = _TEXTURE_INDEX[key].get(Path(package).name)
    return str(file.relative_to(root)) if file else None


def _properties(root, spec):
    relative = spec.get('source_material_properties')
    filename = root / relative if relative else _index(root).get(spec.get('name') or spec.get('slot_name'))
    if filename is None or not filename.is_file():
        return {}, None
    data = json.loads(filename.read_text(encoding='utf-8'))
    objects = data if isinstance(data, list) else [data]
    material = next((o for o in objects if 'MaterialInstance' in o.get('Type', '')), objects[0])
    return material.get('Properties', {}), str(filename.relative_to(root))


def _params(properties, kind):
    return {p.get('ParameterInfo', {}).get('Name'): p.get('ParameterValue')
            for p in properties.get(kind + 'ParameterValues', [])}


def _rgb(value):
    return [float(value[c]) for c in ('R', 'G', 'B')]


def resolve_material_spec(spec, source_root):
    """Return a copy with explicit stock-PBR decisions and source evidence."""
    out = deepcopy(spec)
    if out.get('stock_material_policy_version') == 2:
        return out
    name = out.get('name') or out.get('slot_name', '')
    # Do not reinterpret explicit environment/importer specs through character policy.
    if not any(token in name for token in ('Char_', 'VMI_Molly', 'VMI_Oral', 'VMI_EyeTearduc', 'MI_EyeTearduc', 'VMI_Eyelash', 'VMI_Queent')):
        return out
    root = Path(source_root)
    properties, evidence = _properties(root, out)
    scalars = _params(properties, 'Scalar')
    vectors = _params(properties, 'Vector')
    textures = _params(properties, 'Texture') or out.get('actual_texture_parameters', {})
    parent = properties.get('Parent', {}).get('ObjectName', '')
    name = out.get('name') or out.get('slot_name', '')
    notes = []
    decisions = {'original_manifest_roughness_factor': out.get('roughness_factor'),
                 'original_manifest_metallic_factor': out.get('metallic_factor'),
                 'source_properties': evidence, 'source_parent': parent,
                 'source_scalars': scalars,
                 'packed_parameters_with_incomplete_source_graph': {k: v for k, v in textures.items()
                    if k in ('RAMMap', 'PSARMap', 'Alpha_ID_Roughness_Root', 'ORMMap')}}
    hair = 'Hair' in name and ('FirstPass' in name or 'SecondPass' in name)
    eye = 'Eye_Master' in parent or ('_Eye_' in name and 'Eyelash' not in name)
    skin = 'Skin' in parent or 'Skin' in name or '_Head_' in name or '_body_' in name
    fabric = any(word in name for word in ('Clothes', 'Skirt', 'Dress', 'Stocking', 'Trousers', 'Coat', 'Cloak', 'Veil', 'Gloves'))
    if not out.get('metallic'):
        source_metallic = scalars.get('Metallic')
        if source_metallic is not None:
            out['metallic_factor'] = min(1., max(0., float(source_metallic)))
            decisions['metallic_basis'] = 'serialized_Metallic_scalar_stock_range_adaptation'
            if not 0 <= float(source_metallic) <= 1:
                notes.append('Serialized Metallic exceeds stock 0..1 range; clamped for stock UE, original custom interpretation is unknown.')
        else:
            out['metallic_factor'] = 0.
            decisions['metallic_basis'] = 'nonmetal_stock_approximation_unproven_glTF_default_removed'
            notes.append('glTF metallicFactor is not proof of the custom source BRDF. Unverified scalar 1 replaced with explicit nonmetal UE approximation; packed metallic masks remain unresolved.')
    if not out.get('roughness'):
        source_roughness = scalars.get('Roughness')
        if source_roughness is not None:
            out['roughness_factor'] = min(1., max(0., float(source_roughness)))
            decisions['roughness_basis'] = 'serialized_Roughness_scalar'
        elif out.get('roughness_factor') is not None and float(out['roughness_factor']) < .999:
            decisions['roughness_basis'] = 'existing_explicit_nondefault_manifest_value'
        elif eye:
            out['roughness_factor'] = .1
            decisions['roughness_basis'] = 'glossy_eye_stock_approximation'
        else:
            out['roughness_factor'] = .55 if fabric else .45 if skin else .5
            decisions['roughness_basis'] = 'stock_approximation_unproven_glTF_default_removed'
    if 'M_Avatar_Silk_Glitter' in parent and name in (
            'VMI_Char_Angela_Suit_B_Skirt_UI', 'VMI_Char_Angela_Suit_B_Clothes_UI') and vectors.get('Param'):
        # The source Param.R values are a source-linked visual calibration,
        # not recovered graph semantics; cooked Param/output nodes are absent.
        out['roughness_factor'] = min(1., max(.02, float(vectors['Param']['R'])))
        decisions['roughness_basis'] = 'source_Param_R_hypothesis_for_glossy_silk_visual_calibration'
        decisions['source_Param'] = vectors['Param']
        notes.append('Glossy silk stock roughness uses serialized Param.R (skirt .065905 / clothes .159602) as a visual calibration hypothesis. Cooked Param-to-output nodes are stripped; this is not proof that Param.R equals original roughness, and sparkle/iridescence remain unreconstructed.')
    if 'Specular' in scalars:
        out['specular_factor'] = min(1., max(0., float(scalars['Specular'])))
    for key in ('Anisotropy', 'anisotropy'):
        if key in scalars:
            out['anisotropy_factor'] = min(1., max(-1., float(scalars[key])))
            break
    if hair:
        # The source has a tint switch/blend, not unconditional albedo*tint.
        # In particular Lucia has BaseColor=0 AND bUsedColorTint=0.
        tint = vectors.get('BaseColor')
        tint_weight = float(scalars.get('bUsedColorTint', 0.))
        if tint:
            out['base_color_factor'] = [1., 1., 1., float((out.get('base_color_factor') or [1,1,1,1])[3])]
            out['hair_tint_color'] = _rgb(tint)
            out['hair_tint_weight'] = min(1., max(0., tint_weight))
            decisions['hair_color_basis'] = 'stock_lerp_source_BaseColor_using_serialized_bUsedColorTint'
            notes.append('Hair stock approximation blends source albedo and BaseColor using bUsedColorTint. It preserves disabled tint (0); full source ID/color1/color2 and Marschner graph are stripped and are not reproduced.')
        if textures.get('Alpha_ID_Roughness_Root'):
            character = (out.get('base_color') or '').split('/', 1)[0]
            opacity_file = _texture_file(root, textures['Alpha_ID_Roughness_Root'], character)
            if opacity_file:
                out['opacity'] = opacity_file
                out['opacity_channel'] = 'R'
                decisions['opacity_basis'] = 'AIRR_R_visual_and_bindpose_UV_validated_approximation'
                decisions['opacity_source'] = textures['Alpha_ID_Roughness_Root']
                notes.append('AIRR R drives stock opacity as a documented approximation: RGBA contact sheets show R contains strand silhouettes; A is a root/tip gradient (constant 1 for Queen/Bianca). Bind-pose UV rasterization confirms R removes rectangular hair cards. Final cooked output wiring is stripped, so this is visual evidence rather than exact graph recovery.')
            else:
                notes.append('AIRR source was not decoded; hair opacity remains unresolved.')
    if eye:
        character = (out.get('base_color') or '').split('/', 1)[0]
        iris_file = _texture_file(root, textures.get('IrisMap', {}), character)
        sclera_file = _texture_file(root, textures.get('ScleraMap', {}), character)
        if iris_file and sclera_file:
            out['base_color'] = iris_file
            out['base_color_factor'] = [1., 1., 1., 1.]
            out['eye_reconstruction'] = {
                'iris': iris_file, 'sclera': sclera_file,
                'iris_radius': min(.45, max(.02, float(scalars.get('irisUVRadius', .15)))),
                'sclera_scale': float(scalars.get('ScleraScale', 1.2)),
                'iris_color': _rgb(vectors['IrisColor']) if vectors.get('IrisColor') else [.753472] * 3,
                'sclera_color': _rgb(vectors['ScleraColor']) if vectors.get('ScleraColor') else [.901961, .784314, .756863],
                'iris_edge_width': .003,
                'basis': 'stock_radial_UV_approximation_constrained_by_source_irisUVRadius_and_ScaleUVsByCenter',
                'source_parent_function': '/Game/MaterialLib/Materials/Character/NewCharacterMaterial/EyeMaterials/MF_Basic_Eye_Layer',
                'source_pupil_scale_unimplemented': scalars.get('pupilScale'),
                'source_properties': evidence}
            emissive = _texture_file(root, textures.get('EmissiveMap', {}), character)
            if emissive:
                out['eye_reconstruction']['emissive'] = emissive
                out['eye_reconstruction']['emissive_intensity'] = float(scalars.get('EmssiveIntensity', 1.))
            notes.append('Eye stock reconstruction separates source IrisMap and ScleraMap. Original IrisMap coordinates pass through MF_Data_Eye and ScleraMap through ScaleUVsByCenter; cooked arithmetic is stripped. Radial iris UV remap is an explicit approximation using source irisUVRadius, source tint/default vectors and ScleraScale. Edge feather .003 is a viewer approximation. Refraction, pupil remap, fake-light and custom cubemap BRDF remain unresolved.')
        else:
            notes.append('Decoded IrisMap or ScleraMap is unavailable; eye remains iris-only.')
        decisions['eye_shader_complete'] = False
    if name == 'VMI_Char_Molly_Nanami_B_Accessory_FX_UI':
        character = (out.get('base_color') or '').split('/', 1)[0]
        background = _texture_file(root, textures.get('BGTexture', {}), character)
        if background:
            out['base_color'] = background
            out['base_color_factor'] = [1., 1., 1., 1.]
            out['emissive'] = background
            out['emissive_factor'] = [float(scalars.get('[CubrReflection] Strength', 2.))] * 3
            out['texture_uv_bounds'] = [.06671142578125, .1695556640625, .2235107421875, .340087890625]
            out['emissive_layers'] = []
            tint = _rgb(vectors['StarTintColor']) if vectors.get('StarTintColor') else [1., 1., 1.]
            for parameter in ('Star', 'Star2'):
                file = _texture_file(root, textures.get(parameter, {}), character)
                if file:
                    out['emissive_layers'].append({'texture': file, 'factor': tint})
            decisions['star_core_basis'] = 'source_BGTexture_Star_Star2_static_emission_approximation'
            decisions['star_core_uv_basis'] = 'source_GLTF_FX_primitive_TEXCOORD_0_accessor_min_max'
            notes.append('Nanami star core stock approximation uses original BGTexture/Star/Star2 in static emissive layers with serialized StarTintColor. Its source GLB UV0 occupies [.066711,.169556]..[.223511,.340088]; this region is normalized for the static layer. Source fake cubemap alone is not an albedo. Strength 2 is a source-guided emission approximation; rotating 3D star/ray, SphereBias and original reflection shader remain unreconstructed.')
    if out.get('emissive') and not out.get('emissive_factor'):
        multiplier = scalars.get('EmissionScale', scalars.get('EmssiveIntensity', 1.))
        color = vectors.get('EmissionColor') or vectors.get('EmissiveColor')
        out['emissive_factor'] = [float(multiplier) * v for v in (_rgb(color) if color else [1.,1.,1.])]
    if decisions['packed_parameters_with_incomplete_source_graph']:
        notes.append('Cooked RAM/PSAR/AIRR final output nodes are stripped. No roughness/metallic/AO channel-to-BRDF mapping is claimed; packed sources are retained, never connected by filename convention.')
    out['stock_material_policy_version'] = 2
    out['material_reconstruction'] = decisions
    out['notes'] = list(out.get('notes', [])) + notes
    return out
