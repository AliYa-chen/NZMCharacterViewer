"""Prepare exact allowlisted character skin/Hair candidates using stock UE BRDFs.

Run inside Unreal Python; default is a read-only asset audit:
  -CharacterShadingMode=audit|prepare|apply|original
  -CharacterShadingFilter=angela|all|angel,molly,naimei,queen
  -CharacterShadingParts=skin|hair|both
  -CharacterHairSpecular=0.2 -CharacterHairRoughness=0.55
  -CharacterHairScatter=0.25 -CharacterHairBacklit=0.1
  -CharacterHairColorPower=2

The adjacent character_shading_allowlist.json is the source of audited asset
paths, hashes and Catalog slots; no Saved report is needed at runtime. Angela
candidate paths stay unchanged; other characters use character/outfit folders.
Prepare leaves the Catalog and any currently bound candidates unchanged.
Apply binds
selected candidates and restores the other tested part to its baseline, so
skin-only, hair-only and both are independent A/B states. Original restores
baseline material references. First apply backs up Catalog package bytes and
references in Saved/CharacterShading/Original. Semantic baselines grow only for
newly selected characters and resolve owned candidate aliases to their sources;
the first Catalog byte backup is never replaced. Exact byte restoration is an
ordinary file copy performed only while the project is closed.

No source materials/textures, renderer settings, engine shaders or original
BRDFs are modified. Saved candidates still require shader compilation and real
Mac/iOS visual checks; NullRHI saving cannot establish rendering support.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil


VERSION = 1
CATALOG = '/Game/Viewer/Data/DA_ViewerCatalog'
DESTINATION = '/Game/Viewer/CharacterShading'
TAG = 'ViewerNativeCharacterShading'
PATH_PATTERN = re.compile(r'/Game/[A-Za-z0-9_./-]+')
ALLOWLIST_PATH = Path(__file__).resolve().with_name('character_shading_allowlist.json')


def load_allowlist(path):
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema_version') != 1 or not data.get('entries'):
        raise ValueError('Unsupported or empty character shading allowlist.')
    entries, destinations = {}, set()
    for row in data['entries']:
        source, kind, character, outfit = (row[name] for name in ('source', 'kind', 'character', 'outfit'))
        if (kind not in {'skin', 'hair'} or source in entries
                or not re.fullmatch(r'[a-z0-9_]+', character)
                or not re.fullmatch(r'[a-z0-9_]+', outfit)
                or not source.startswith('/Game/Characters/' + character + '/' + outfit + '/')):
            raise ValueError('Invalid or duplicate allowlist source: ' + source)
        folder = DESTINATION + '/' + character + ('' if character == 'angela' else '/' + outfit)
        expected_destination = folder + '/' + source.rsplit('/', 1)[-1] + '_Native_v1'
        if row.get('destination') != expected_destination or expected_destination in destinations:
            raise ValueError('Invalid or colliding candidate destination: ' + source)
        if not row.get('source_hashes') or not row.get('bindings'):
            raise ValueError('Audited source hashes and Catalog bindings are required: ' + source)
        for binding in row['bindings']:
            if (binding.get('character') != character or binding.get('outfit') != outfit
                    or not isinstance(binding.get('slot'), int) or binding['slot'] < 0
                    or not binding.get('part') or not binding.get('mesh')):
                raise ValueError('Invalid exact Catalog binding: ' + source)
        entries[source] = row
        destinations.add(expected_destination)
    if set(data.get('characters', [])) != {row['character'] for row in entries.values()}:
        raise ValueError('Allowlist character index does not match its entries.')
    return data, entries


ALLOWLIST, SOURCE_INFO = load_allowlist(ALLOWLIST_PATH)
SOURCES = {source: row['kind'] for source, row in SOURCE_INFO.items()}
CHARACTERS = frozenset(row['character'] for row in SOURCE_INFO.values())
# Viewer calibration starts, not recovered source-engine material parameters.
HAIR_SCATTER = 0.25
HAIR_BACKLIT = 0.1
HAIR_SPECULAR = 0.2
HAIR_ROUGHNESS = 0.55
HAIR_COLOR_POWER = 2.0
HAIR_CALIBRATION_VERSION = 3
HAIR_COLOR_POWER_DESCRIPTION = 'Viewer native v3: Hair BaseColor semantic conversion'
SKIN_MOBILE_SUBSURFACE = (0.08, 0.02, 0.015)
SKIN_MOBILE_OPACITY = 0.85
SKIN_PROFILE_MFP_CM = 0.12  # 0.12 cm = 1.2 mm; WorldUnitScale is explicitly 1.
ROOT_PINS = {
    'MP_BASE_COLOR': 'BaseColor', 'MP_METALLIC': 'Metallic',
    'MP_SPECULAR': 'Specular', 'MP_ROUGHNESS': 'Roughness',
    'MP_ANISOTROPY': 'Anisotropy', 'MP_EMISSIVE_COLOR': 'EmissiveColor',
    'MP_OPACITY': 'Opacity', 'MP_OPACITY_MASK': 'OpacityMask',
    'MP_NORMAL': 'Normal', 'MP_TANGENT': 'Tangent',
    'MP_WORLD_POSITION_OFFSET': 'WorldPositionOffset',
    'MP_SUBSURFACE_COLOR': 'SubsurfaceColor',
    'MP_AMBIENT_OCCLUSION': 'AmbientOcclusion', 'MP_REFRACTION': 'Refraction',
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def package_files(project, package):
    if not package.startswith('/Game/'):
        raise ValueError('Expected a project asset package: ' + package)
    stem = project / 'Content' / package.removeprefix('/Game/')
    if not stem.with_suffix('.uasset').is_file():
        raise FileNotFoundError(stem.with_suffix('.uasset'))
    return [stem.with_suffix(ext) for ext in ('.uasset', '.uexp', '.ubulk')
            if stem.with_suffix(ext).is_file()]


def package_hashes(project, package):
    return {p.relative_to(project).as_posix(): sha256(p) for p in package_files(project, package)}


def fields(value):
    """Parse top-level reflected struct fields, including nested soft references."""
    text = value.export_text().strip()
    if text.startswith('(') and text.endswith(')'):
        text = text[1:-1]
    result, depth, quote, escaped, start = {}, 0, None, False, 0
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
            name, _, content = text[start:index].partition('=')
            result[name.strip().lower()] = content.strip()
            start = index + 1
    return result


def material_paths(part):
    return [path.split('.', 1)[0] for path in PATH_PATTERN.findall(fields(part).get('materials', ''))]


def without_materials(part):
    return {name: value for name, value in fields(part).items() if name != 'materials'}


def destination(source):
    return SOURCE_INFO[source]['destination']


def profile_path(source):
    return DESTINATION + '/' + SOURCE_INFO[source]['character'] + '/SP_ViewerSkin_Calibrated_v1'


def select_characters(value):
    requested = set(value.lower().split(','))
    if requested == {'all'}:
        return set(CHARACTERS)
    if not requested or not requested.issubset(CHARACTERS):
        raise ValueError('CharacterShadingFilter must be all or a comma-separated subset of '
                         + ','.join(sorted(CHARACTERS)) + '.')
    return requested


def row_identity(row):
    # Facial animation may be repaired after the first material backup. Keep
    # this comparison compatible with the Angela v1 manifest and verify all
    # current non-material fields (including Animation) after rebinding.
    return (row['indices'], row['character'], row['part'],
            {key: value for key, value in row['other_fields'].items() if key != 'animation'})


def baseline_rows(rows, prior_rows, aliases):
    """Plan incremental semantic originals without modifying a manifest."""
    result = [dict(row) for row in prior_rows]
    if any(path.startswith(DESTINATION + '/') for row in result for path in row['materials']):
        raise RuntimeError('An existing original-material baseline contains a candidate path.')
    covered = {row['character'].lower() for row in prior_rows}
    current_characters = {row['character'].lower() for row in rows}
    for character in sorted(current_characters):
        current = [row for row in rows if row['character'].lower() == character]
        previous = [row for row in prior_rows if row['character'].lower() == character]
        if character in covered:
            if [row_identity(row) for row in current] != [row_identity(row) for row in previous]:
                raise RuntimeError('Catalog structure changed since the backup for ' + character + '.')
            continue
        for row in current:
            original = dict(row)
            original['materials'] = [aliases.get(path, path) for path in row['materials']]
            if any(path.startswith(DESTINATION + '/') for path in original['materials']):
                raise RuntimeError('Cannot record an unknown candidate as an original material.')
            result.append(original)
    result.sort(key=lambda row: tuple(row['indices']))
    keys = [(tuple(row['indices']), row['character'], row['part']) for row in result]
    if len(keys) != len(set(keys)):
        raise RuntimeError('Duplicate Catalog rows in the original material baseline.')
    return result


def canonical(value):
    return str(value).replace(' ', '').replace('_', '').replace('.', '').lower()


def run():
    import unreal

    edit = unreal.MaterialEditingLibrary
    library = unreal.EditorAssetLibrary
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    # Resolve the actual project, including when invoked through runpy wrappers.
    project = Path(unreal.Paths.project_dir()).resolve()
    directory = project / 'Saved/CharacterShading'
    command = unreal.SystemLibrary.get_command_line()

    def option(name, default):
        match = re.search(r'(?:^|\s)-' + re.escape(name) + r'=([^\s]+)', command, re.I)
        return match[1].strip('"\'').lower() if match else default

    mode = option('CharacterShadingMode', 'audit')
    selection_filter = option('CharacterShadingFilter', 'angela')
    selected = select_characters(selection_filter)
    selected_sources = {source: kind for source, kind in SOURCES.items()
                        if SOURCE_INFO[source]['character'] in selected}
    parts = option('CharacterShadingParts', 'both')
    if mode not in {'audit', 'prepare', 'apply', 'original'}:
        raise ValueError('CharacterShadingMode must be audit/prepare/apply/original.')
    if parts not in {'skin', 'hair', 'both'}:
        raise ValueError('CharacterShadingParts must be skin/hair/both.')
    selected_parts = {'skin', 'hair'} if parts == 'both' else {parts}
    hair_calibration = {}
    for name, default, minimum, maximum in (
            ('Specular', HAIR_SPECULAR, 0.0, 1.0), ('Roughness', HAIR_ROUGHNESS, 0.02, 1.0),
            ('Scatter', HAIR_SCATTER, 0.0, 1.0), ('Backlit', HAIR_BACKLIT, 0.0, 1.0),
            ('ColorPower', HAIR_COLOR_POWER, 0.25, 4.0)):
        value = float(option('CharacterHair' + name, str(default)))
        if not minimum <= value <= maximum:
            raise ValueError('CharacterHair' + name + ' must be finite and in '
                             + str(minimum) + '..' + str(maximum) + '.')
        hair_calibration[name] = value
    report = {
        'version': VERSION, 'mode': mode, 'parts': parts, 'started_utc': utc_now(),
        'selected_characters': sorted(selected), 'selected_source_count': len(selected_sources),
        'allowlist_file': str(ALLOWLIST_PATH), 'allowlist_sha256': sha256(ALLOWLIST_PATH),
        'allowlist_source_count': len(SOURCES), 'excluded_source_count': len(ALLOWLIST.get('excluded', [])),
        'status': 'started', 'materials': [], 'bindings': [], 'errors': [],
        'shader_compilation_verified': False, 'rendered_appearance_verified': False,
        'limitations': [
            'UE BRDF adaptation; NZM custom engine BRDFs and GI are not recovered.',
            'Mobile Preintegrated Skin is not desktop screen-space Subsurface Profile.',
            'Native mobile Hair skips SpecularIBL, including its composited SSR contribution.',
            'The rejected earlier iOS Hair/SSS trial remains evidence until new real-device A/B passes.',
            'V follows reviewed strand-card atlases; per-source braid/UV exceptions are preserved in the allowlist notes.',
            'Hair blend/depth behavior remains stock Translucent, not NZM custom translucent depth writing.',
            'Mobile directional Hair initializes bUseBacklit=false; its material Backlit input is ignored on that path.',
            'Hair ColorPower is a calibrated input-semantics conversion, not a recovered source BRDF or device-validated color match.',
        ],
        'calibration': {
            'is_source_reconstruction': False, 'hair_calibration_version': HAIR_CALIBRATION_VERSION,
            'hair_parameters': hair_calibration,
            'mobile_directional_material_backlit_effective': False,
            'mobile_subsurface_color': SKIN_MOBILE_SUBSURFACE,
            'mobile_opacity': SKIN_MOBILE_OPACITY, 'profile_mean_free_path_cm': SKIN_PROFILE_MFP_CM,
            'profile_world_unit_scale': 1.0,
        },
    }
    selection_label = 'all' if selected == set(CHARACTERS) else '_'.join(sorted(selected))
    # Preserve the existing Angela report paths and keep all/subset evidence
    # separate so preparing more characters does not erase the pilot history.
    report_label = '' if selected == {'angela'} else '_' + selection_label
    report_path = directory / ('report_' + mode + report_label + '_' + parts + '.json')
    source_hashes = {source: package_hashes(project, source) for source in selected_sources}
    source_snapshot_mismatches = [source for source in selected_sources
                                  if source_hashes[source] != SOURCE_INFO[source]['source_hashes']]
    catalog_hashes = package_hashes(project, CATALOG)

    def enum_value(type_name, entry):
        enum = getattr(unreal, type_name)
        matches = [name for name in dir(enum) if canonical(name) == canonical(entry)]
        if len(matches) != 1:
            raise RuntimeError(type_name + '.' + entry + ' is not reflected by this Unreal build.')
        return getattr(enum, matches[0])

    def expression(material, class_name, description, **properties):
        nodes = edit.get_material_expressions(material)
        node = edit.create_material_expression(material, getattr(unreal, class_name),
                                               -1400 + 180 * (len(nodes) % 5), 160 * (len(nodes) // 5))
        if node is None:
            raise RuntimeError('Expression unavailable: ' + class_name)
        node.set_editor_property('desc', 'Viewer native v1: ' + description)
        for name, value in properties.items():
            node.set_editor_property(name, value)
        return node

    def link(source, target, input_name, output=''):
        pins = [str(pin) for pin in edit.get_material_expression_input_names(target)]
        aliases = {
            'A>B': ('A>B', 'AGreaterThanB'),
            'A==B': ('A==B', 'AEqualsB'),
            'A<B': ('A<B', 'ALessThanB'),
            'Exp': ('Exp', 'Exponent'),
        }.get(input_name, (input_name,))
        matching = [pin for pin in pins if any(canonical(pin) == canonical(alias) for alias in aliases)]
        if not matching and len(pins) == 1:
            matching = pins
        if len(matching) != 1 or not edit.connect_material_expressions(source, output, target, matching[0]):
            raise RuntimeError('Cannot connect ' + input_name + ' on ' + target.get_class().get_name()
                               + '; reflected inputs: ' + repr(pins))

    def scalar(material, value, name):
        return expression(material, 'MaterialExpressionScalarParameter', name,
                          parameter_name='Viewer_' + name, default_value=value)

    def vector(material, value, name):
        return expression(material, 'MaterialExpressionVectorParameter', name,
                          parameter_name='Viewer_' + name, default_value=unreal.LinearColor(*value, 1.0))

    def binary(material, kind, a, b, name):
        node = expression(material, 'MaterialExpression' + kind, name)
        link(a, node, 'A')
        link(b, node, 'B')
        return node

    def constant(material, value, name):
        return expression(material, 'MaterialExpressionConstant', name, r=value)

    def choose(material, a, b, above, below, name):
        node = expression(material, 'MaterialExpressionIf', name, equals_threshold=0.0)
        for source, pin in ((a, 'A'), (b, 'B'), (above, 'A>B'), (below, 'A==B'), (below, 'A<B')):
            link(source, node, pin)
        return node

    def feature_switch(material, desktop, mobile, name):
        node = expression(material, 'MaterialExpressionFeatureLevelSwitch', name)
        link(desktop, node, 'Default')
        link(mobile, node, 'ES3_1')
        return node

    def model(material, value, name):
        return expression(material, 'MaterialExpressionShadingModel', name, shading_model=value)

    def roots(material):
        result = {}
        for enum_name, pin in ROOT_PINS.items():
            prop = getattr(unreal.MaterialProperty, enum_name)
            source = edit.get_material_property_input_node(material, prop)
            if source:
                result[pin] = (source, str(edit.get_material_property_input_node_output_name(material, prop)))
        return result

    def make_attributes(material, original):
        attributes = expression(material, 'MaterialExpressionMakeMaterialAttributes', 'Preserve original inputs')
        for pin, (source, output) in original.items():
            link(source, attributes, pin, output)
        material.set_editor_property('use_material_attributes', True)
        material.set_editor_property('shading_model', unreal.MaterialShadingModel.MSM_FROM_MATERIAL_EXPRESSION)
        if not edit.connect_material_property(attributes, '', unreal.MaterialProperty.MP_MATERIAL_ATTRIBUTES):
            raise RuntimeError('Cannot connect Material Attributes.')
        return attributes

    def prepare_profile(source):
        target = profile_path(source)
        folder = target.rsplit('/', 1)[0]
        if library.does_asset_exist(target):
            profile = library.load_asset(target)
            if (not isinstance(profile, unreal.SubsurfaceProfile)
                    or library.get_metadata_tag(profile, TAG) != str(VERSION)):
                raise RuntimeError('Refusing to overwrite an unowned profile: ' + target)
            return profile
        library.make_directory(folder)
        profile = tools.create_asset(target.rsplit('/', 1)[-1], folder,
                                     unreal.SubsurfaceProfile, unreal.SubsurfaceProfileFactory())
        if not isinstance(profile, unreal.SubsurfaceProfile):
            raise RuntimeError('Subsurface profile factory is unavailable.')
        settings = profile.get_editor_property('settings')
        values = {
            'enable_burley': True, 'enable_mean_free_path': True,
            'mean_free_path_distance': SKIN_PROFILE_MFP_CM, 'world_unit_scale': 1.0,
            'mean_free_path_color': unreal.LinearColor(1.0, 0.25, 0.12, 1.0),
            'surface_albedo': unreal.LinearColor(0.6, 0.3, 0.24, 1.0),
            'tint': unreal.LinearColor(0.3, 0.22, 0.18, 1.0),
        }
        for name, value in values.items():
            settings.set_editor_property(name, value)
        profile.set_editor_property('settings', settings)
        library.set_metadata_tag(profile, TAG, str(VERSION))
        library.set_metadata_tag(profile, 'ViewerCalibration', 'Native UE profile; MFP 0.12 cm (1.2 mm), world scale 1; not recovered NZM source profile.')
        if not library.save_loaded_asset(profile):
            raise RuntimeError('Profile save failed.')
        return profile

    def strand_direction(material):
        """Normalized dP/dV using standard nodes; no BRDF or custom HLSL."""
        material.set_editor_property('float_precision_mode', enum_value(
            'MaterialFloatPrecisionMode', 'MFPM_Full_MaterialExpressionOnly'))
        position = expression(material, 'MaterialExpressionWorldPosition', 'Camera-relative world position',
                              world_position_shader_offset=enum_value('WorldPositionIncludedOffsets', 'WPT_CameraRelative'))
        uv = expression(material, 'MaterialExpressionTextureCoordinate', 'Source hair atlas UV0', coordinate_index=0)
        derivatives = {}
        for axis in ('X', 'Y'):
            for label, source in (('P', position), ('UV', uv)):
                node = expression(material, 'MaterialExpressionDD' + axis, 'd' + label + '/d' + axis.lower())
                link(source, node, 'Value')
                derivatives[label + axis] = node
        components = {}
        for axis in ('X', 'Y'):
            for channel in ('U', 'V'):
                node = expression(material, 'MaterialExpressionComponentMask', 'd' + channel + '/d' + axis.lower(),
                                  r=channel == 'U', g=channel == 'V', b=False, a=False)
                link(derivatives['UV' + axis], node, 'Input')
                components[channel + axis] = node
        # numerator = dP/dy * dU/dx - dP/dx * dU/dy; dP/dV = numerator / det.
        n = binary(material, 'Subtract',
                   binary(material, 'Multiply', derivatives['PY'], components['UX'], 'dPdy*dUdx'),
                   binary(material, 'Multiply', derivatives['PX'], components['UY'], 'dPdx*dUdy'), 'V numerator')
        det = binary(material, 'Subtract',
                     binary(material, 'Multiply', components['UX'], components['VY'], 'dUdx*dVdy'),
                     binary(material, 'Multiply', components['UY'], components['VX'], 'dUdy*dVdx'), 'UV Jacobian determinant')
        zero, one = constant(material, 0.0, 'Zero'), constant(material, 1.0, 'One')
        sign = choose(material, det, zero, one, constant(material, -1.0, 'Minus one'), 'Preserve mirrored UV orientation')
        signed = binary(material, 'Multiply', n, sign, 'Orient V numerator')
        length_squared = binary(material, 'DotProduct', n, n, 'V numerator length squared')
        floor = constant(material, 1e-20, 'Finite normalization floor')
        fallback = expression(material, 'MaterialExpressionTransform', 'Degenerate UV mesh-binormal fallback',
                              transform_source_type=enum_value('MaterialVectorCoordTransformSource', 'TRANSFORMSOURCE_Tangent'),
                              transform_type=enum_value('MaterialVectorCoordTransform', 'TRANSFORM_World'))
        link(vector(material, (0.0, 1.0, 0.0), 'DegenerateUVFallbackAxis'), fallback, 'Input')
        finite_direction = choose(material, length_squared, floor, signed, fallback, 'Fallback for zero position derivatives')
        abs_det = expression(material, 'MaterialExpressionAbs', 'Absolute UV determinant')
        link(det, abs_det, 'Input')
        valid_direction = choose(material, abs_det, constant(material, 1e-12, 'UV determinant floor'),
                                 finite_direction, fallback, 'Fallback for degenerate UVs')
        # Select a nonzero fallback before Normalize, so even an eagerly evaluated
        # shader does not compute 0/0 in an unused conditional branch.
        normalized = expression(material, 'MaterialExpressionNormalize', 'Normalized world-space V axis')
        link(valid_direction, normalized, 'VectorInput')
        return normalized

    def validate_source(source, kind):
        material = library.load_asset(source)
        if not isinstance(material, unreal.Material):
            raise RuntimeError('Source must be a directly audited Material: ' + source)
        if (material.get_editor_property('use_material_attributes')
                or material.get_editor_property('shading_model') != unreal.MaterialShadingModel.MSM_DEFAULT_LIT):
            raise RuntimeError('Unsupported source shading model/attribute graph: ' + source)
        expected_blend = unreal.BlendMode.BLEND_OPAQUE if kind == 'skin' else unreal.BlendMode.BLEND_TRANSLUCENT
        if material.get_editor_property('blend_mode') != expected_blend:
            raise RuntimeError('Unexpected source blend mode: ' + source)
        inputs = roots(material)
        needed = {'BaseColor', 'Normal', 'Roughness'} if kind == 'skin' else {'BaseColor', 'Opacity', 'Roughness', 'Specular'}
        if not needed.issubset(inputs):
            raise RuntimeError('Source graph lacks known inputs: ' + repr(sorted(needed - inputs.keys())))
        return material, inputs

    def calibrate_hair(candidate, row):
        """Update only owned, hash-matched candidate controls; keep its color/coverage graph."""
        attributes = edit.get_material_property_input_node(candidate, unreal.MaterialProperty.MP_MATERIAL_ATTRIBUTES)
        if not isinstance(attributes, unreal.MaterialExpressionMakeMaterialAttributes):
            raise RuntimeError('Owned Hair candidate has an unexpected attribute output graph.')
        expected_blend = unreal.BlendMode.BLEND_TRANSLUCENT
        if candidate.get_editor_property('blend_mode') != expected_blend:
            raise RuntimeError('Owned Hair candidate no longer has the original Translucent blend.')
        parameters = [node for node in edit.get_material_expressions(candidate)
                      if isinstance(node, unreal.MaterialExpressionScalarParameter)]
        controls = {'Specular': 'Specular', 'Roughness': 'Roughness',
                    'Scatter': 'Metallic', 'Backlit': 'ClearCoat'}
        for name, pin in controls.items():
            parameter_name = 'Viewer_Hair' + name
            matches = [node for node in parameters
                       if str(node.get_editor_property('parameter_name')) == parameter_name]
            if len(matches) > 1:
                raise RuntimeError('Duplicate candidate parameter: ' + parameter_name)
            control = matches[0] if matches else scalar(candidate, hair_calibration[name], 'Hair' + name)
            control.set_editor_property('default_value', hair_calibration[name])
            link(control, attributes, pin)

        # Always start from the source graph's cloned BaseColor expression. Never
        # use the current Attributes.BaseColor as the base: it may already be our
        # Power node from an earlier run, which would compound the conversion.
        source = library.load_asset(row['source'])
        source_base, source_output = roots(source)['BaseColor']
        raw_matches = [node for node in edit.get_material_expressions(candidate)
                       if node.get_name() == source_base.get_name()
                       and node.get_class() == source_base.get_class()]
        if len(raw_matches) != 1:
            raise RuntimeError('Cannot uniquely identify the original cloned Hair BaseColor expression.')
        raw_color = raw_matches[0]
        powers = [node for node in edit.get_material_expressions(candidate)
                  if str(node.get_editor_property('desc')) == HAIR_COLOR_POWER_DESCRIPTION]
        if len(powers) > 1 or powers and not isinstance(powers[0], unreal.MaterialExpressionPower):
            raise RuntimeError('Unexpected or duplicate Viewer Hair ColorPower nodes.')
        power = powers[0] if powers else expression(
            candidate, 'MaterialExpressionPower', 'Hair BaseColor semantic conversion')
        power.set_editor_property('desc', HAIR_COLOR_POWER_DESCRIPTION)
        power_parameters = [node for node in parameters
                            if str(node.get_editor_property('parameter_name')) == 'Viewer_HairColorPower']
        if len(power_parameters) > 1:
            raise RuntimeError('Duplicate Viewer_HairColorPower parameters.')
        exponent = power_parameters[0] if power_parameters else scalar(candidate, hair_calibration['ColorPower'], 'HairColorPower')
        exponent.set_editor_property('default_value', hair_calibration['ColorPower'])
        link(raw_color, power, 'Base', source_output)
        link(exponent, power, 'Exp')
        link(power, attributes, 'BaseColor')

        # Reflected connections are checked after wiring rather than accepting
        # only the connect() return value as evidence. GetInputs preserves nulls.
        def input_sources(node):
            names = list(edit.get_material_expression_input_names(node))
            inputs = list(edit.get_inputs_for_material_expression(candidate, node))
            if len(names) != len(inputs):
                raise RuntimeError('This Unreal build does not expose ordered input connections.')
            return {canonical(name): value for name, value in zip(names, inputs)}

        power_inputs = input_sources(power)
        attribute_inputs = input_sources(attributes)
        if (power_inputs.get('base') != raw_color
                or power_inputs.get('exp', power_inputs.get('exponent')) != exponent
                or attribute_inputs.get('basecolor') != power
                or raw_color == power
                or edit.get_material_property_input_node(candidate, unreal.MaterialProperty.MP_MATERIAL_ATTRIBUTES) != attributes):
            raise RuntimeError('Hair color conversion connection validation failed.')
        power_count = sum(str(node.get_editor_property('desc')) == HAIR_COLOR_POWER_DESCRIPTION
                          for node in edit.get_material_expressions(candidate))
        if power_count != 1:
            raise RuntimeError('Hair ColorPower must have exactly one owned conversion node.')
        library.set_metadata_tag(candidate, 'ViewerHairCalibrationVersion', str(HAIR_CALIBRATION_VERSION))
        library.set_metadata_tag(candidate, 'ViewerHairCalibration', json.dumps(hair_calibration, sort_keys=True))
        library.set_metadata_tag(candidate, 'ViewerHairColorSourceExpression', raw_color.get_name())
        library.set_metadata_tag(candidate, 'ViewerHairColorPowerExpression', power.get_name())
        row['hair_calibration_version'] = HAIR_CALIBRATION_VERSION
        row['hair_parameters'] = dict(hair_calibration)
        row['preserved_inputs'] = [pin for pin in row['preserved_inputs']
                                   if pin not in {'BaseColor', 'Metallic', 'Roughness', 'Specular'}]
        row['hair_color_conversion'] = {
            'source_expression': source_base.get_path_name(),
            'cloned_source_expression': raw_color.get_path_name(), 'source_output': source_output,
            'owned_power_expression': power.get_path_name(), 'owned_power_node_count': power_count,
            'parameter': 'Viewer_HairColorPower', 'power': hair_calibration['ColorPower'],
            'connections_verified': True,
            'calibration_only': True, 'real_device_color_match_verified': False,
        }
        row['changes'] = [
            'Native UE Hair; original BaseColor graph feeds one calibrated Power conversion; Opacity and blend are preserved',
            'World-space UV-V strand axis; calibrated Specular/Roughness/Scatter controls',
            'Backlit parameter is present but ignored by this mobile directional-light path',
        ]

    def audit_bound_hair(candidate, row):
        """Read an already applied v3 Hair graph without mutating its asset."""
        if (library.get_metadata_tag(candidate, 'ViewerHairCalibrationVersion') != str(HAIR_CALIBRATION_VERSION)
                or library.get_metadata_tag(candidate, 'ViewerHairCalibration') != json.dumps(hair_calibration, sort_keys=True)):
            raise RuntimeError('Prepare would recalibrate an active Hair material; restore its original binding or apply that calibration explicitly after review.')
        nodes = list(edit.get_material_expressions(candidate))
        parameters = [node for node in nodes if isinstance(node, unreal.MaterialExpressionScalarParameter)]
        for name, value in hair_calibration.items():
            matches = [node for node in parameters
                       if str(node.get_editor_property('parameter_name')) == 'Viewer_Hair' + name]
            if len(matches) != 1 or abs(float(matches[0].get_editor_property('default_value')) - value) > 1e-6:
                raise RuntimeError('Active Hair control differs from the reviewed calibration: ' + name)
        attributes = edit.get_material_property_input_node(candidate, unreal.MaterialProperty.MP_MATERIAL_ATTRIBUTES)
        powers = [node for node in nodes if str(node.get_editor_property('desc')) == HAIR_COLOR_POWER_DESCRIPTION]
        if not isinstance(attributes, unreal.MaterialExpressionMakeMaterialAttributes) or len(powers) != 1:
            raise RuntimeError('Active Hair material does not have one owned v3 color conversion.')
        power = powers[0]
        if not isinstance(power, unreal.MaterialExpressionPower):
            raise RuntimeError('Active Hair color conversion is not the reviewed Power node.')

        def inputs(node):
            names = list(edit.get_material_expression_input_names(node))
            sources = list(edit.get_inputs_for_material_expression(candidate, node))
            if len(names) != len(sources):
                raise RuntimeError('Ordered material inputs are unavailable.')
            return {canonical(name): source for name, source in zip(names, sources)}

        attribute_inputs, power_inputs = inputs(attributes), inputs(power)
        source_base, _ = roots(library.load_asset(row['source']))['BaseColor']
        raw = power_inputs.get('base')
        exponent = power_inputs.get('exp', power_inputs.get('exponent'))
        if (attribute_inputs.get('basecolor') != power or raw is None or raw == power
                or raw.get_name() != source_base.get_name() or raw.get_class() != source_base.get_class()
                or not isinstance(exponent, unreal.MaterialExpressionScalarParameter)
                or str(exponent.get_editor_property('parameter_name')) != 'Viewer_HairColorPower'
                or not isinstance(attribute_inputs.get('normal'), unreal.MaterialExpressionNormalize)):
            raise RuntimeError('Active Hair source color, Power or strand-axis connection changed.')
        descriptions = {str(node.get_editor_property('desc')) for node in nodes}
        if not {'Viewer native v1: Fallback for degenerate UVs',
                'Viewer native v1: Fallback for zero position derivatives'}.issubset(descriptions):
            raise RuntimeError('Active Hair graph lacks the reviewed finite-UV fallbacks.')
        row['hair_calibration_version'] = HAIR_CALIBRATION_VERSION
        row['hair_parameters'] = dict(hair_calibration)
        row['hair_color_conversion'] = {'owned_power_node_count': 1, 'connections_verified': True,
                                        'active_asset_unchanged': True, 'power': hair_calibration['ColorPower']}

    def prepare(source, kind):
        original, original_inputs = validate_source(source, kind)
        row = {'source': source, 'kind': kind, 'destination': destination(source),
               'character': SOURCE_INFO[source]['character'], 'outfit': SOURCE_INFO[source]['outfit'],
               'audited_bindings': SOURCE_INFO[source]['bindings'], 'notes': SOURCE_INFO[source].get('notes', []),
               'source_hashes': source_hashes[source], 'status': 'audited_supported_graph',
               'shader_status': 'not_verified', 'render_status': 'not_verified',
               'preserved_inputs': sorted(original_inputs)}
        report['materials'].append(row)
        if mode == 'audit':
            return None
        target = destination(source)
        library.make_directory(target.rsplit('/', 1)[0])
        expected_source_hash = json.dumps(source_hashes[source], sort_keys=True)
        if library.does_asset_exist(target):
            candidate = library.load_asset(target)
            if (not isinstance(candidate, unreal.Material)
                    or library.get_metadata_tag(candidate, TAG) != str(VERSION)
                    or library.get_metadata_tag(candidate, 'ViewerSourceMaterial') != source
                    or library.get_metadata_tag(candidate, 'ViewerSourceHashes') != expected_source_hash):
                raise RuntimeError('Candidate already exists with another source/version; refusing overwrite: ' + target)
            if kind == 'hair':
                if mode == 'prepare' and target in active_paths:
                    before = package_hashes(project, target)
                    audit_bound_hair(candidate, row)
                    if package_hashes(project, target) != before:
                        raise RuntimeError('Reading the active Hair candidate unexpectedly modified its package.')
                    row['status'] = 'reused_active_v3_candidate_unchanged_requires_render_validation'
                    return candidate
                calibrate_hair(candidate, row)
                edit.recompile_material(candidate)
                library.set_metadata_tag(candidate, 'ViewerValidation', 'Hair calibration updated; shader and real Mac/iOS A/B validation required.')
                if not library.save_loaded_asset(candidate):
                    raise RuntimeError('Owned Hair candidate calibration save failed.')
                row['status'] = 'updated_owned_candidate_requires_shader_and_render_validation'
            else:
                row['status'] = 'reused_saved_candidate_requires_render_validation'
            return candidate
        candidate = library.duplicate_asset(source, target)
        if not isinstance(candidate, unreal.Material):
            raise RuntimeError('Could not duplicate material: ' + source)
        copied_inputs = roots(candidate)
        if set(copied_inputs) != set(original_inputs):
            raise RuntimeError('Duplicate did not preserve the original root inputs.')
        attributes = make_attributes(candidate, copied_inputs)
        if kind == 'skin':
            candidate.set_editor_property('subsurface_profile', prepare_profile(source))
            desktop = model(candidate, unreal.MaterialShadingModel.MSM_SUBSURFACE_PROFILE, 'Desktop native Subsurface Profile')
            mobile = model(candidate, unreal.MaterialShadingModel.MSM_PREINTEGRATED_SKIN, 'ES3.1 native Preintegrated Skin')
            link(feature_switch(candidate, desktop, mobile, 'Skin feature level'), attributes, 'ShadingModel')
            opacity = feature_switch(candidate, constant(candidate, 1.0, 'Desktop SSS opacity'),
                                     scalar(candidate, SKIN_MOBILE_OPACITY, 'MobileSkinOpacity'), 'Skin opacity feature level')
            link(opacity, attributes, 'Opacity')
            link(vector(candidate, SKIN_MOBILE_SUBSURFACE, 'MobileSubsurfaceColor'), attributes, 'SubsurfaceColor')
            row['changes'] = ['Native desktop Subsurface Profile / ES3.1 Preintegrated Skin',
                              'Calibrated mobile subsurface tint and opacity; desktop MFP 1.2 mm']
        else:
            candidate.set_editor_property('tangent_space_normal', False)
            link(model(candidate, unreal.MaterialShadingModel.MSM_HAIR, 'Native UE Hair BRDF'), attributes, 'ShadingModel')
            link(strand_direction(candidate), attributes, 'Normal')
            calibrate_hair(candidate, row)
        edit.set_material_usage(candidate, unreal.MaterialUsage.MATUSAGE_SKELETAL_MESH)
        edit.recompile_material(candidate)
        library.set_metadata_tag(candidate, TAG, str(VERSION))
        library.set_metadata_tag(candidate, 'ViewerSourceMaterial', source)
        library.set_metadata_tag(candidate, 'ViewerSourceHashes', expected_source_hash)
        library.set_metadata_tag(candidate, 'ViewerValidation', 'Asset prepared; shader compilation and real Mac/iOS A/B are required.')
        if not library.save_loaded_asset(candidate):
            raise RuntimeError('Candidate save failed: ' + target)
        row['status'] = 'saved_candidate_requires_shader_and_render_validation'
        return candidate

    def catalog_rows(catalog):
        rows = []
        for ci, character in enumerate(catalog.get_editor_property('characters')):
            cid = str(character.get_editor_property('id'))
            if cid.lower() not in selected:
                continue
            for oi, outfit in enumerate(character.get_editor_property('outfits')):
                for pi, part in enumerate(outfit.get_editor_property('parts')):
                    rows.append({'indices': [ci, oi, pi], 'character': cid,
                                 'outfit': str(outfit.get_editor_property('id')),
                                 'part': str(part.get_editor_property('id')),
                                 'materials': material_paths(part), 'other_fields': without_materials(part)})
        return rows

    def read_catalog_backup():
        manifest_path = directory / 'Original/manifest.json'
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            if manifest.get('version') != VERSION or manifest.get('catalog') != CATALOG:
                raise RuntimeError('Existing Catalog backup belongs to another version.')
            for row in manifest['files']:
                if sha256(directory / 'Original' / row['relative']) != row['sha256']:
                    raise RuntimeError('Catalog backup hash mismatch.')
            return manifest
        return None

    def source_from_binding(path):
        source = aliases.get(path)
        if source is None:
            if path.startswith(DESTINATION + '/'):
                raise RuntimeError('Unrecognized candidate binding cannot become an original: ' + path)
            return path
        candidate = library.load_asset(path)
        expected_hash = json.dumps(source_hashes[source], sort_keys=True)
        if (not isinstance(candidate, unreal.Material)
                or library.get_metadata_tag(candidate, TAG) != str(VERSION)
                or library.get_metadata_tag(candidate, 'ViewerSourceMaterial') != source
                or library.get_metadata_tag(candidate, 'ViewerSourceHashes') != expected_hash):
            raise RuntimeError('Current candidate alias is not owned by this audited source: ' + path)
        return source

    def validate_bindings(rows):
        expected = {(source, binding['character'], binding['outfit'], binding['part'], binding['slot'],
                     binding['mesh'].split('.', 1)[0])
                    for source in selected_sources for binding in SOURCE_INFO[source]['bindings']}
        observed = set()
        resolved = {}
        row_keys = [(row['character'].lower(), row['outfit'], row['part']) for row in rows]
        if len(row_keys) != len(set(row_keys)):
            raise RuntimeError('Selected Catalog contains duplicate character/outfit/part identities.')
        for row in rows:
            mesh_paths = PATH_PATTERN.findall(row['other_fields'].get('mesh', ''))
            if len(mesh_paths) != 1:
                raise RuntimeError('Catalog mesh identity is ambiguous: ' + row['part'])
            mesh = mesh_paths[0].split('.', 1)[0]
            for slot, path in enumerate(row['materials']):
                source = source_from_binding(path)
                if path != source:
                    resolved[path] = source
                if source in SOURCES:
                    observed.add((source, row['character'].lower(), row['outfit'], row['part'], slot, mesh))
        if observed != expected:
            raise RuntimeError('Exact audited Catalog slots changed; missing=' + repr(sorted(expected - observed))
                               + '; unexpected=' + repr(sorted(observed - expected)))
        report['exact_catalog_bindings_verified'] = len(observed)
        report['resolved_owned_aliases'] = resolved

    def backup_catalog(rows, manifest, planned_rows):
        manifest_path = directory / 'Original/manifest.json'
        if manifest is not None:
            # Only semantic material baselines grow. The first byte snapshot,
            # its hashes and its timestamp remain an immutable historical copy.
            if manifest['character_rows'] != planned_rows:
                updated = dict(manifest)
                updated['character_rows'] = planned_rows
                updated['material_baseline_revision'] = 2
                updated['baseline_characters'] = sorted({row['character'].lower() for row in planned_rows})
                updated['baseline_updated_utc'] = utc_now()
                write_json(manifest_path, updated)
                manifest = updated
            return manifest
        manifest = {'version': VERSION, 'catalog': CATALOG, 'created_utc': utc_now(),
                    'character_rows': planned_rows, 'files': [], 'material_baseline_revision': 2,
                    'baseline_characters': sorted({row['character'].lower() for row in planned_rows}),
                    'byte_backup_semantics': 'Exact first-apply snapshot; semantic originals resolve owned native candidates to audited source refs.',
                    'snapshot_had_owned_candidates': any(path in aliases for row in rows for path in row['materials'])}
        for source in package_files(project, CATALOG):
            relative = source.relative_to(project).as_posix()
            target = directory / 'Original' / relative
            if target.exists():
                raise RuntimeError('Unmanifested Catalog backup file exists; inspect before continuing.')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            if sha256(source) != sha256(target):
                raise RuntimeError('Catalog backup copy did not verify.')
            manifest['files'].append({'relative': relative, 'sha256': sha256(target)})
        write_json(manifest_path, manifest)
        return manifest

    try:
        if source_snapshot_mismatches:
            raise RuntimeError('Audited source packages changed; review the static allowlist again: '
                               + repr(source_snapshot_mismatches))
        catalog = library.load_asset(CATALOG)
        if not isinstance(catalog, unreal.ViewerCatalog):
            raise RuntimeError('Viewer Catalog is unavailable.')
        rows = catalog_rows(catalog)
        if not rows:
            raise RuntimeError('Selected characters are not present in the active Catalog.')
        if {row['character'].lower() for row in rows} != selected:
            raise RuntimeError('Some selected characters are absent from the active Catalog.')
        aliases = {destination(source): source for source in selected_sources}
        active_paths = {path for row in rows for path in row['materials']}
        validate_bindings(rows)
        manifest = read_catalog_backup()
        planned_rows = baseline_rows(rows, manifest['character_rows'] if manifest else [], aliases)
        report['baseline_plan'] = {
            'byte_backup_exists': manifest is not None,
            'already_covered_characters': sorted({row['character'].lower() for row in manifest['character_rows']}) if manifest else [],
            'new_characters': sorted(selected - {row['character'].lower() for row in manifest['character_rows']}) if manifest else sorted(selected),
            'planned_original_row_count': len(planned_rows),
            'contains_candidate_as_original': False,
            'written': False,
        }
        candidates = {}
        if mode != 'original':
            for source, kind in selected_sources.items():
                if kind not in selected_parts:
                    continue
                try:
                    candidates[source] = prepare(source, kind)
                except Exception as exc:
                    report['errors'].append({'source': source, 'status': 'unsupported_or_prepare_failed', 'message': str(exc)})
        if report['errors']:
            raise RuntimeError('Candidate preparation failed; Catalog was not changed.')
        if mode in {'apply', 'original'}:
            if mode == 'original':
                covered = {row['character'].lower() for row in manifest['character_rows']} if manifest else set()
                if not selected.issubset(covered):
                    raise RuntimeError('No recorded original material baseline for: ' + repr(sorted(selected - covered)))
                baseline = manifest
            else:
                baseline = backup_catalog(rows, manifest, planned_rows)
                report['baseline_plan']['written'] = True
            original_rows = [row for row in baseline['character_rows'] if row['character'].lower() in selected]
            # A later facial-animation repair may legitimately change Animation.
            # Rebinding materials preserves that current value and verifies every
            # non-material field against the current snapshot after the edit.
            current_nonmaterials = [row['other_fields'] for row in rows]
            if [row_identity(row) for row in rows] != [row_identity(row) for row in original_rows]:
                raise RuntimeError('Catalog structure changed since the backup; refusing index-based rebinding.')
            plans = {}
            for current, original_row in zip(rows, original_rows):
                targets = []
                for source in original_row['materials']:
                    kind = SOURCES.get(source)
                    target = destination(source) if mode == 'apply' and kind in selected_parts else source
                    asset = library.load_asset(target)
                    if asset is None:
                        raise RuntimeError('Catalog target did not load: ' + target)
                    targets.append(asset)
                plans[tuple(current['indices'])] = targets
            characters = list(catalog.get_editor_property('characters'))
            for ci, character in enumerate(characters):
                outfits = list(character.get_editor_property('outfits'))
                for oi, outfit in enumerate(outfits):
                    parts_array = list(outfit.get_editor_property('parts'))
                    for pi, part in enumerate(parts_array):
                        targets = plans.get((ci, oi, pi))
                        if targets is None:
                            continue
                        before = material_paths(part)
                        after = [asset.get_path_name().split('.', 1)[0] for asset in targets]
                        if before != after:
                            part.set_editor_property('materials', targets)
                            report['bindings'].append({'indices': [ci, oi, pi], 'part': str(part.get_editor_property('id')),
                                                       'from': before, 'to': after})
                    outfit.set_editor_property('parts', parts_array)
                character.set_editor_property('outfits', outfits)
            catalog.set_editor_property('characters', characters)
            after_rows = catalog_rows(catalog)
            if ([row_identity(row) for row in after_rows] != [row_identity(row) for row in rows]
                    or [row['other_fields'] for row in after_rows] != current_nonmaterials):
                raise RuntimeError('Non-material Catalog fields unexpectedly changed; Catalog was not saved.')
            if report['bindings'] and not library.save_loaded_asset(catalog):
                raise RuntimeError('Catalog save failed.')
        report['status'] = {
            'audit': 'asset_graph_audit_passed_shader_and_platform_support_unverified',
            'prepare': 'candidates_prepared_catalog_unchanged_requires_render_validation',
            'apply': 'candidate_bindings_saved_requires_render_validation',
            'original': 'original_material_references_restored',
        }[mode]
    except Exception as exc:
        report['status'] = 'failed'
        report['errors'].append({'message': str(exc)})
        raise
    finally:
        report['source_hashes_before'] = source_hashes
        report['source_hashes_after'] = {source: package_hashes(project, source) for source in selected_sources}
        report['catalog_hashes_before'] = catalog_hashes
        report['catalog_hashes_after'] = package_hashes(project, CATALOG)
        report['sources_unchanged'] = report['source_hashes_after'] == source_hashes
        report['catalog_changed'] = report['catalog_hashes_after'] != catalog_hashes
        report['finished_utc'] = utc_now()
        write_json(report_path, report)
        unreal.log('Viewer native character shading report: ' + str(report_path))
        if not report['sources_unchanged']:
            raise RuntimeError('An original material package changed unexpectedly.')
        if mode in {'audit', 'prepare'} and report['catalog_changed']:
            raise RuntimeError('Read-only/prepare mode changed the Catalog package unexpectedly.')


if __name__ == '__main__':
    run()
