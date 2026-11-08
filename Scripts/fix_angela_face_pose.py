"""Remove Angela's duplicated facial proportion offset from the head RoleIdle.

Unreal Python usage: -AngelaFacePoseMode=audit (default), or =apply after review.
The original decoded body curves and original animations are never overwritten.
An independent head animation uses identical motion tracks with a corrected
source reference for the facial branches. The Catalog's Angela head Animation
reference is the only existing Content field that may be changed.

The body RoleIdle's first facial pose already matches this imported mesh bind
shape. Applying those constant proportions relative to the generic female
reference again moves the teeth through the chin. This repair changes the
retarget reference, while retaining original head/body motion and facial deltas.
It does not substitute the separately configured FaceIdle animation.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import sys


CATALOG = '/Game/Viewer/Data/DA_ViewerCatalog'
SOURCE = Path('Saved/EntranceImport/curves/NZM/Content/Avatar/Character/Angela/LobbyRes_Angela/Empty/SA_Char_Angela_Normal_KS.curves.json')
FACE_SOURCE = Path('Saved/EntranceImport/curves_face/NZM/Content/Animations/Show/Face/SA_Char_Angela_Normal_KS.curves.json')
HEAD_PART = '14601040001'
ORIGINAL_ANIMATION = '/Game/Characters/angela/angela/14601040001/Animations/A_14601040001_RoleIdle'
DESTINATION = '/Game/Characters/angela/angela/14601040001/Animations/A_14601040001_RoleIdleFaceCorrected'
FACIAL_ROOTS = {'bake_FacialRoot_JNT': 'b_Head', 'bake_jaw_C_002_JNT': 'b_Neck'}
DIRECTORY = Path('Saved/FacialPoseCorrection')
VERSION = '1'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def data_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def prepare_reference(source):
    """Return a derived copy; only the two facial subtrees' reference changes."""
    derived = deepcopy(source)
    if len(source.get('sequences', [])) != 1:
        raise ValueError('Expected exactly one original decoded body sequence.')
    sequence, corrected = source['sequences'][0], derived['sequences'][0]
    bones, tracks = sequence['bones'], {t['bone']: t for t in sequence['tracks']}
    if len(tracks) != len(sequence['tracks']):
        raise ValueError('Duplicate original body tracks.')
    indices = {bone['name']: i for i, bone in enumerate(bones)}
    if any(root not in indices for root in FACIAL_ROOTS):
        raise ValueError('Expected Angela facial branch roots are unavailable.')
    for root, parent in FACIAL_ROOTS.items():
        b = bones[indices[root]]
        if b['parent'] < 0 or bones[b['parent']]['name'] != parent:
            raise ValueError('Unexpected facial branch parent for ' + root)
    selected, branch = set(), {}
    for i, bone in enumerate(bones):
        parent = bone['parent']
        if parent >= i or parent < -1:
            raise ValueError('Original skeleton is not a valid ordered hierarchy.')
        if bone['name'] in FACIAL_ROOTS:
            selected.add(i)
            branch[bone['name']] = bone['name']
        elif parent in selected:
            selected.add(i)
            branch[bone['name']] = branch[bones[parent]['name']]
    changes = []
    for i in sorted(selected):
        bone = bones[i]
        track = tracks.get(bone['name'])
        if not track or any(len(track[channel]) != sequence['frames'] for channel in ('positions', 'rotations', 'scales')):
            raise ValueError('Missing dense first-frame facial transform: ' + bone['name'])
        position, rotation, scale = (track[channel][0] for channel in ('positions', 'rotations', 'scales'))
        if (len(position) != 3 or len(rotation) != 4 or len(scale) != 3
                or not all(math.isfinite(v) for v in position + rotation + scale)
                or abs(sum(v * v for v in rotation) - 1.) > .001):
            raise ValueError('Invalid first-frame facial transform: ' + bone['name'])
        reference = {'Translation': dict(zip('XYZ', position)),
                     'Rotation': dict(zip('XYZW', rotation)), 'Scale3D': dict(zip('XYZ', scale))}
        corrected['bones'][i]['reference_pose'] = reference
        changes.append({'bone': bone['name'], 'parent': bones[bone['parent']]['name'],
                        'facial_branch': branch[bone['name']],
                        'reference_before': bone['reference_pose'], 'reference_after': reference})
    if corrected['tracks'] != sequence['tracks']:
        raise RuntimeError('Motion tracks changed while preparing the corrected reference.')
    for i, bone in enumerate(bones):
        if i not in selected and corrected['bones'][i] != bone:
            raise RuntimeError('A non-facial source bone changed: ' + bone['name'])
    for key, value in sequence.items():
        if key != 'bones' and corrected[key] != value:
            raise RuntimeError('A sequence field changed: ' + key)
    return derived, changes, branch


def backup_catalog(project, before_bindings):
    directory = project / DIRECTORY / 'OriginalCatalog'
    manifest_path = directory / 'manifest.json'
    if directory.resolve() != directory or manifest_path.resolve() != manifest_path:
        raise RuntimeError('Catalog backup must not use symlinks.')
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {
        'version': VERSION, 'catalog': CATALOG, 'bindings_before': before_bindings, 'files': []}
    index = {r['relative']: r for r in manifest['files']}
    for extension in ('.uasset', '.uexp', '.ubulk'):
        relative = Path('Content/Viewer/Data/DA_ViewerCatalog' + extension)
        source, target = project / relative, directory / relative
        if source.resolve() != source or target.resolve() != target:
            raise RuntimeError('Catalog package/backup may not use symlinks.')
        if not source.exists():
            if extension == '.uasset':
                raise RuntimeError('Catalog package file missing.')
            continue
        text = relative.as_posix()
        if text in index:
            if not target.is_file() or digest(target) != index[text]['sha256']:
                raise RuntimeError('Original Catalog backup SHA mismatch.')
            continue
        if target.exists():
            raise RuntimeError('Unmanifested Catalog backup exists: ' + str(target))
        target.parent.mkdir(parents=True, exist_ok=True)
        expected = digest(source)
        shutil.copy2(source, target)
        if digest(target) != expected or digest(source) != expected:
            raise RuntimeError('Catalog changed while making its original backup.')
        manifest['files'].append({'relative': text, 'sha256': expected})
    write_json(manifest_path, manifest)
    return manifest


def run():
    import unreal
    project = Path(unreal.Paths.project_dir()).resolve()
    sys.path.insert(0, str(project / 'Scripts'))
    from configure_reflection_materials import serialized_fields, soft_path
    command_line = unreal.SystemLibrary.get_command_line()
    match = re.search(r'-AngelaFacePoseMode=([^\s]+)', command_line)
    mode = match[1].lower() if match else 'audit'
    if mode not in ('audit', 'apply'):
        raise ValueError('AngelaFacePoseMode must be audit or apply.')
    directory = project / DIRECTORY
    report = {'mode': mode, 'version': VERSION, 'started_utc': datetime.now(timezone.utc).isoformat(),
              'source_body_curves': SOURCE.as_posix(), 'source_body_sha256': digest(project / SOURCE),
              'original_animation_overwritten': False, 'bindings_changed': [], 'errors': [],
              'alternative_face_overlay': 'Not used. The separate /Animations/Show/Face package is configured as FaceIdle; applying it as a new layer would change animation behavior beyond correcting duplicated bind proportions.'}
    try:
        source = json.loads((project / SOURCE).read_text())
        derived, changes, branches = prepare_reference(source)
        derived_path = directory / 'AngelaFacialReferenceCorrected.curves.json'
        write_json(derived_path, derived)
        sequence = source['sequences'][0]
        report.update(reference_changes=changes, facial_roots=FACIAL_ROOTS,
                      source_reference_changed_bones=len(changes),
                      original_motion_tracks_sha256=data_hash(sequence['tracks']),
                      corrected_motion_tracks_sha256=data_hash(derived['sequences'][0]['tracks']),
                      derived_curve_file=derived_path.relative_to(project).as_posix(),
                      derived_curve_sha256=digest(derived_path),
                      body_source_data_unchanged=digest(project / SOURCE) == report['source_body_sha256'])
        if (project / FACE_SOURCE).exists():
            face = json.loads((project / FACE_SOURCE).read_text())
            report['face_comparison'] = {'file': FACE_SOURCE.as_posix(), 'sha256': digest(project / FACE_SOURCE),
                'identical_source_reference_bones': face['sequences'][0]['bones'] == sequence['bones'],
                'scope': 'Shared generic skeleton reference is identical. This repair retains the existing body RoleIdle tracks rather than importing an alternate face state.'}
        catalog = unreal.load_asset(CATALOG)
        if not isinstance(catalog, unreal.ViewerCatalog):
            raise RuntimeError('Current runtime Catalog missing.')
        characters = list(catalog.get_editor_property('characters'))
        before_bindings = [c.export_text() for c in characters]
        selections = []
        for ci, character in enumerate(characters):
            if str(character.get_editor_property('id')) != 'angela':
                continue
            for oi, outfit in enumerate(character.get_editor_property('outfits')):
                for pi, part in enumerate(outfit.get_editor_property('parts')):
                    if str(part.get_editor_property('id')) == HEAD_PART:
                        fields = serialized_fields(part)
                        selections.append((ci, oi, pi, part, soft_path(fields['mesh']), soft_path(fields['animation'])))
        if len(selections) != 1:
            raise RuntimeError('Expected exactly one current Angela head binding.')
        ci, oi, pi, part, mesh_path, animation_path = selections[0]
        if animation_path.split('.', 1)[0] not in (ORIGINAL_ANIMATION, DESTINATION):
            raise RuntimeError('Current Angela head binding is neither the original nor this corrected RoleIdle.')
        mesh, original = unreal.load_asset(mesh_path), unreal.load_asset(ORIGINAL_ANIMATION)
        if not isinstance(mesh, unreal.SkeletalMesh) or not isinstance(original, unreal.AnimSequence):
            raise RuntimeError('Current Angela head mesh/animation is unavailable.')
        library = unreal.EditorAssetLibrary
        current_source_hash = library.get_metadata_tag(original, 'ViewerSourceCurveSHA256')
        if current_source_hash != report['source_body_sha256']:
            raise RuntimeError('Current head animation does not identify this exact decoded RoleIdle source; refusing replacement.')
        report['head_binding_before'] = {'mesh': mesh_path, 'animation': animation_path, 'part': part.export_text()}
        report['catalog_sha256_before'] = digest(project / 'Content/Viewer/Data/DA_ViewerCatalog.uasset')
        original_file = project / 'Content' / (ORIGINAL_ANIMATION[len('/Game/'):] + '.uasset')
        report['original_animation_path'] = original.get_path_name()
        report['original_animation_source_metadata_sha256'] = current_source_hash
        report['original_animation_sha256'] = digest(original_file)
        options = unreal.AnimPoseEvaluationOptions()
        options.set_editor_property('evaluation_type', unreal.AnimDataEvalType.RAW)
        options.set_editor_property('optional_skeletal_mesh', mesh)
        first_pose = unreal.AnimPoseExtensions.get_anim_pose_at_time(original, 0., options)
        target_bones = [str(n) for n in unreal.AnimPoseExtensions.get_bone_names(first_pose)]
        report['matched_target_facial_bones'] = [n for n in target_bones if n in branches]
        report['preserved_target_nonfacial_bones'] = [n for n in target_bones if n not in branches]
        if not {'b_Head', 'b_Neck'}.issubset(target_bones):
            raise RuntimeError('Head and neck motion cannot be verified on this skeleton.')
        if mode == 'audit':
            report['status'] = 'audit_ready_requires_apply_pose_validation'
            report['would_create_animation'] = DESTINATION
            return
        backup = backup_catalog(project, before_bindings)
        report['original_catalog_backup'] = (DIRECTORY / 'OriginalCatalog/manifest.json').as_posix()
        report['original_catalog_backup_files'] = backup['files']
        metadata = {'ViewerAngelaFacePoseVersion': VERSION,
                    'ViewerAngelaFacePoseSourceSHA256': report['source_body_sha256'],
                    'ViewerAngelaFacePoseCorrectedSHA256': report['derived_curve_sha256'],
                    'ViewerAngelaFacePoseTargetMesh': mesh.get_path_name()}
        corrected_animation = unreal.load_asset(DESTINATION)
        created = corrected_animation is None
        if corrected_animation:
            if (not isinstance(corrected_animation, unreal.AnimSequence)
                    or any(library.get_metadata_tag(corrected_animation, k) != v for k, v in metadata.items())):
                raise RuntimeError('Independent corrected animation already exists with different provenance.')
        else:
            folder, name = DESTINATION.rsplit('/', 1)
            factory = unreal.AnimSequenceFactory()
            factory.set_editor_property('target_skeleton', mesh.get_editor_property('skeleton'))
            factory.set_editor_property('preview_skeletal_mesh', mesh)
            corrected_animation = unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, folder, unreal.AnimSequence, factory)
            if not isinstance(corrected_animation, unreal.AnimSequence):
                raise RuntimeError('Could not create independent corrected head animation.')
            detail = unreal.ViewerAnimationImportLibrary.populate_animation_from_curves(corrected_animation, mesh, str(derived_path))
            if not detail:
                raise RuntimeError('Could not populate the independent corrected animation.')
            report['importer_report'] = json.loads(detail)
            for key, value in metadata.items():
                library.set_metadata_tag(corrected_animation, key, value)
        # Compare body/head following before changing the Catalog. Any failure
        # leaves the original runtime binding intact.
        max_position, max_rotation, max_scale = 0., 0., 0.
        samples = []
        duration = float(sequence['sequence_length'])
        for time in (0., duration / 3., duration * 2. / 3., duration):
            old = unreal.AnimPoseExtensions.get_anim_pose_at_time(original, time, options)
            new = unreal.AnimPoseExtensions.get_anim_pose_at_time(corrected_animation, time, options)
            for name in report['preserved_target_nonfacial_bones']:
                a = unreal.AnimPoseExtensions.get_bone_pose(old, name, unreal.AnimPoseSpaces.WORLD)
                b = unreal.AnimPoseExtensions.get_bone_pose(new, name, unreal.AnimPoseSpaces.WORLD)
                position = math.dist([a.translation.x, a.translation.y, a.translation.z], [b.translation.x, b.translation.y, b.translation.z])
                qa, qb = a.rotation, b.rotation
                rotation = math.degrees(2. * math.acos(min(1., abs(qa.x * qb.x + qa.y * qb.y + qa.z * qb.z + qa.w * qb.w))))
                scale = max(abs(a.scale3d.x - b.scale3d.x), abs(a.scale3d.y - b.scale3d.y), abs(a.scale3d.z - b.scale3d.z))
                if not all(math.isfinite(v) for v in (position, rotation, scale)):
                    raise RuntimeError('Nonfinite pose comparison: ' + name)
                max_position, max_rotation, max_scale = max(max_position, position), max(max_rotation, rotation), max(max_scale, scale)
            samples.append(time)
        report['body_motion_validation'] = {'times_seconds': samples, 'bone_count': len(report['preserved_target_nonfacial_bones']),
            'max_world_position_difference_cm': max_position, 'max_world_rotation_difference_degrees': max_rotation,
            'max_world_scale_difference': max_scale, 'passed': max_position < .01 and max_rotation < .05 and max_scale < .001}
        if not report['body_motion_validation']['passed']:
            raise RuntimeError('Corrected head animation changed body/head following; Catalog was not rebound.')
        # Validate neutral first-frame facial shape relative to its head/neck
        # parent, retaining physical centimetres despite the FBX scale=100.
        new = unreal.AnimPoseExtensions.get_anim_pose_at_time(corrected_animation, 0., options)
        neutral_error = 0.
        for name in report['matched_target_facial_bones']:
            parent = FACIAL_ROOTS[branches[name]]
            ref_parent = unreal.AnimPoseExtensions.get_ref_bone_pose(new, parent, unreal.AnimPoseSpaces.WORLD)
            pose_parent = unreal.AnimPoseExtensions.get_bone_pose(new, parent, unreal.AnimPoseSpaces.WORLD)
            ref_bone = unreal.AnimPoseExtensions.get_ref_bone_pose(new, name, unreal.AnimPoseSpaces.WORLD)
            pose_bone = unreal.AnimPoseExtensions.get_bone_pose(new, name, unreal.AnimPoseSpaces.WORLD)
            expected = unreal.MathLibrary.transform_location(pose_parent, unreal.MathLibrary.inverse_transform_location(ref_parent, ref_bone.translation))
            neutral_error = max(neutral_error, math.dist([expected.x, expected.y, expected.z],
                                                        [pose_bone.translation.x, pose_bone.translation.y, pose_bone.translation.z]))
        report['first_frame_facial_reference_validation'] = {'max_position_difference_cm': neutral_error, 'passed': neutral_error < .01}
        if not math.isfinite(neutral_error) or neutral_error >= .01:
            raise RuntimeError('Corrected facial proportions do not match the imported bind shape at frame zero.')
        if created and not library.save_loaded_asset(corrected_animation):
            raise RuntimeError('Independent corrected animation did not save.')
        corrected_path = corrected_animation.get_path_name()
        report['original_animation_preserved'] = digest(original_file) == report['original_animation_sha256']
        if not report['original_animation_preserved']:
            raise RuntimeError('Original animation package changed unexpectedly; Catalog was not rebound.')
        if animation_path == corrected_path:
            report['status'] = 'already_applied'
            return
        expected_bindings = [text.replace(animation_path, corrected_path) for text in before_bindings]
        if sum(text.count(animation_path) for text in before_bindings) != 1:
            raise RuntimeError('Original animation path is not unique in the Catalog; refusing broad replacement.')
        outfits = list(characters[ci].get_editor_property('outfits'))
        parts = list(outfits[oi].get_editor_property('parts'))
        parts[pi].set_editor_property('animation', corrected_animation)
        outfits[oi].set_editor_property('parts', parts)
        characters[ci].set_editor_property('outfits', outfits)
        catalog.set_editor_property('characters', characters)
        if [c.export_text() for c in catalog.get_editor_property('characters')] != expected_bindings:
            raise RuntimeError('An unexpected Catalog field changed; Catalog was not saved.')
        if not library.save_loaded_asset(catalog):
            raise RuntimeError('Corrected head animation binding did not save.')
        report['bindings_changed'] = [{'part': HEAD_PART, 'field': 'Animation', 'before': animation_path, 'after': corrected_path}]
        report['catalog_sha256_after'] = digest(project / 'Content/Viewer/Data/DA_ViewerCatalog.uasset')
        report['original_animation_preserved'] = digest(original_file) == report['original_animation_sha256']
        if not report['original_animation_preserved']:
            raise RuntimeError('Original animation package changed unexpectedly; inspect the report and Catalog backup.')
        report['status'] = 'applied_requires_rendered_oral_visible_verification'
    except Exception as exc:
        report['status'] = 'failed'
        report['errors'].append(str(exc))
        raise
    finally:
        report['finished_utc'] = datetime.now(timezone.utc).isoformat()
        write_json(directory / (mode + '_report.json'), report)
        unreal.log('ANGELA_FACE_POSE ' + json.dumps({'mode': mode, 'status': report.get('status'), 'errors': report['errors']}))


if __name__ == '__main__':
    run()
