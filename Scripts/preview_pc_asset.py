"""Render one converted mesh on real Metal without saving a level or Content.

Run the Editor commandlet with -run=pythonscript -AllowCommandletRendering and
this file as -script. Do not use -nullrhi. Optional arguments: -PCPreviewMesh=
source package or converted target, -PCPreviewYaw=35, -PCPreviewPitch=8,
-PCPreviewWidth=900, -PCPreviewHeight=1200, -PCPreviewFOV=35.

The three neutral lights and fixed manual exposure are a stock inspection
setup. This image does not reconstruct source lighting, animation, cloth,
camera routing or the original material shaders.
"""
from datetime import datetime, timezone
import hashlib
import itertools
import json
import math
from pathlib import Path
import re
import struct
import traceback
import zlib

import unreal


ROOT = Path(unreal.Paths.project_dir()).resolve()
OUTPUT = ROOT / "Saved/PCAssetConversion/Preview"
DEFAULT_MESH = "/Game/Avatar/Character/Angel/Angel_Lucia_A/Mesh/SK_Char_Angel_Lucia_A_Preview_Mesh"
REPORT_PATH = OUTPUT / "preview_report.json"


def option(name, default):
    matches = re.findall(r"(?:^|\s)-" + re.escape(name) + r"=(\"[^\"]*\"|[^\s]+)",
                         unreal.SystemLibrary.get_command_line())
    return matches[-1].strip('"') if matches else default


def numeric(name, default, minimum, maximum):
    value = float(option(name, str(default)))
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def write_report(report):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    temporary = REPORT_PATH.with_suffix(".new")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(REPORT_PATH)


def xyz(value):
    return [float(value.x), float(value.y), float(value.z)]


def fit_camera(origin, extent, yaw_degrees, pitch_degrees, horizontal_fov, width, height):
    """Fit all eight imported bounds corners using the actual horizontal FOV."""
    yaw, pitch = math.radians(yaw_degrees), math.radians(pitch_degrees)
    direction = [math.cos(yaw)*math.cos(pitch), math.sin(yaw)*math.cos(pitch), math.sin(pitch)]
    right = [-math.sin(yaw), math.cos(yaw), 0]
    up = [-math.cos(yaw)*math.sin(pitch), -math.sin(yaw)*math.sin(pitch), math.cos(pitch)]
    tan_h = math.tan(math.radians(horizontal_fov) / 2)
    tan_v = tan_h * height / width
    required = []
    for signs in itertools.product((-1, 1), repeat=3):
        corner = [extent[i]*signs[i] for i in range(3)]
        projected = lambda axis: sum(corner[i]*axis[i] for i in range(3))
        required.extend((projected(direction) + abs(projected(right))/tan_h,
                         projected(direction) + abs(projected(up))/tan_v))
    distance = max(max(required, default=1) * 1.15, 30)
    position = [origin[i]+direction[i]*distance for i in range(3)]
    return position, {"location_cm": position, "target_cm": origin, "yaw_degrees": yaw_degrees,
                      "elevation_degrees": pitch_degrees, "distance_cm": distance,
                      "horizontal_fov_degrees": horizontal_fov,
                      "fit": "all eight imported bounds corners with 15 percent distance padding"}


def png_statistics(path, width, height):
    """Verify PNG integrity and reject a uniform/blank capture using stdlib only."""
    encoded = path.read_bytes()
    if encoded[:8] != b"\x89PNG\r\n\x1a\n":
        raise RuntimeError("Renderer export is not a PNG")
    offset, compressed, header, ended = 8, bytearray(), None, False
    while offset < len(encoded):
        if offset + 12 > len(encoded):
            raise RuntimeError("Truncated preview PNG")
        count = struct.unpack_from(">I", encoded, offset)[0]
        kind = encoded[offset+4:offset+8]
        data = encoded[offset+8:offset+8+count]
        if len(data) != count or offset+count+12 > len(encoded):
            raise RuntimeError("Truncated preview PNG chunk")
        crc = struct.unpack_from(">I", encoded, offset+8+count)[0]
        if zlib.crc32(kind+data) & 0xffffffff != crc:
            raise RuntimeError("Preview PNG checksum failed")
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", data)
        elif kind == b"IDAT":
            compressed.extend(data)
        offset += count + 12
        if kind == b"IEND":
            ended = True
            break
    if not ended:
        raise RuntimeError("Preview PNG has no complete IEND chunk")
    if not header or header[:2] != (width, height):
        raise RuntimeError("Preview dimensions differ from requested render target")
    _, _, bits, color, _, _, interlace = header
    if bits != 8 or color not in (2, 6) or interlace:
        raise RuntimeError("RGBA8 render target exported an unexpected PNG pixel layout")
    channels = 4 if color == 6 else 3
    row_size = width * channels
    raw = zlib.decompress(compressed)
    if len(raw) != height * (row_size+1):
        raise RuntimeError("Preview pixel payload length is invalid")
    previous = bytearray(row_size)
    minima, maxima, bins, first_rgb, different = [255]*3, [0]*3, set(), None, 0
    alpha_min, alpha_max = 255, 0
    for y in range(height):
        mode = raw[y*(row_size+1)]
        row = bytearray(raw[y*(row_size+1)+1:(y+1)*(row_size+1)])
        if mode > 4:
            raise RuntimeError("Unknown preview PNG scanline filter")
        for index in range(row_size):
            left = row[index-channels] if index >= channels else 0
            above = previous[index]
            upper_left = previous[index-channels] if index >= channels else 0
            if mode == 1:
                predictor = left
            elif mode == 2:
                predictor = above
            elif mode == 3:
                predictor = (left+above)//2
            elif mode == 4:
                p = left+above-upper_left
                distances = (abs(p-left), abs(p-above), abs(p-upper_left))
                predictor = (left, above, upper_left)[distances.index(min(distances))]
            else:
                predictor = 0
            row[index] = (row[index]+predictor) & 255
        for x in range(width):
            rgb = tuple(row[x*channels:x*channels+3])
            alpha = row[x*channels+3] if channels == 4 else 255
            alpha_min, alpha_max = min(alpha_min, alpha), max(alpha_max, alpha)
            if first_rgb is None:
                first_rgb = rgb
            different += rgb != first_rgb
            bins.add(tuple(value//16 for value in rgb))
            for channel in range(3):
                minima[channel] = min(minima[channel], rgb[channel])
                maxima[channel] = max(maxima[channel], rgb[channel])
        previous = row
    if different < max(100, width*height//1000) or max(maxima[i]-minima[i] for i in range(3)) < 8:
        raise RuntimeError("Preview is blank or nearly uniform; capture is not accepted")
    if alpha_max == 0:
        raise RuntimeError("Preview RGB contains a capture but its PNG is fully transparent")
    return {"width": width, "height": height, "bit_depth": bits, "color_type": color,
            "pixel_check": "nonuniform_rendered_pixels", "rgb_min": minima, "rgb_max": maxima,
            "quantized_rgb_bins": len(bins), "pixels_different_from_first": different,
            "alpha_min": alpha_min, "alpha_max": alpha_max,
            "validation_scope": "PNG integrity and nonblank pixels; subject appearance still requires visual review"}


def main():
    report = {"schema_version": 1, "status": "started", "recorded_utc": datetime.now(timezone.utc).isoformat(),
              "content_saved": False, "map_saved": False, "animation": "reference_pose",
              "limitations": [
                  "Stock three-light inspection with manual exposure, not the recovered source lighting or camera.",
                  "Converted materials are documented stock approximations; original private shaders are not recovered.",
                  "Reference-pose capture does not verify animation, cloth, viewer interaction, other characters or iOS.",
                  "An active Metal shader map and nonblank PNG do not establish original-game visual equivalence."]}
    created_actors = []
    render_target = None
    actors = None
    try:
        command_line = unreal.SystemLibrary.get_command_line()
        if re.search(r"(?:^|\s)-nullrhi(?:\s|$)", command_line, re.I):
            raise RuntimeError("Preview requires actual Metal rendering; -nullrhi is not accepted")
        if not re.search(r"(?:^|\s)-AllowCommandletRendering(?:\s|$)", command_line, re.I):
            raise RuntimeError("Preview commandlet must include -AllowCommandletRendering")
        request = option("PCPreviewMesh", DEFAULT_MESH)
        yaw = numeric("PCPreviewYaw", 35, -36000, 36000) % 360
        elevation = numeric("PCPreviewPitch", 8, -70, 70)
        width = int(numeric("PCPreviewWidth", 900, 128, 4096))
        height = int(numeric("PCPreviewHeight", 1200, 128, 4096))
        fov = numeric("PCPreviewFOV", 35, 10, 100)
        plan_path = ROOT / "Saved/PCAssetConversion/asset_plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8-sig"))
        rows = [row for row in plan["meshes"] if request == row["target"] or
                request == row.get("source_package") or request in row.get("source_packages", [])]
        if len(rows) != 1:
            raise ValueError("PCPreviewMesh must resolve to exactly one planned converted mesh")
        row = rows[0]
        mesh = unreal.EditorAssetLibrary.load_asset(row["target"])
        if not isinstance(mesh, unreal.SkeletalMesh):
            raise RuntimeError("Converted mesh did not load: " + row["target"])
        slots = list(mesh.get_editor_property("materials"))
        if len(slots) != len(row["slot_names"]):
            raise RuntimeError("Preview mesh slot count differs from its import plan")
        material_rows, material_packages = [], []
        for index, slot in enumerate(slots):
            material = slot.material_interface
            expected = row["material_targets"][index]
            actual = material.get_path_name().split(".", 1)[0] if material else None
            if actual != expected:
                raise RuntimeError(f"Preview slot {index} is not bound to its planned material; finish bindings first")
            if actual:
                material_packages.append(actual)
            material_rows.append({"slot": index, "slot_name": str(slot.material_slot_name),
                                  "source_package": row["material_packages"][index], "asset": actual})
        material_packages = sorted(set(material_packages))
        if not material_packages:
            raise RuntimeError("Preview has no converted material assignments to verify")
        report.update(source_package=row.get("source_package"), source_packages=row.get("source_packages", []),
                      source_file=row["file"], source_sha256=row["sha256"], mesh=row["target"],
                      source_channels=row["channels"], materials=material_rows,
                      plan_sha256=hashlib.sha256(plan_path.read_bytes()).hexdigest())
        shaders = json.loads(unreal.PCAssetImportLibrary.verify_material_shaders(material_packages))
        report["shaders"] = shaders
        report["rhi"] = {key: shaders.get(key) for key in
                         ("rhi", "using_null_rhi", "shader_platform", "shader_format", "feature_level")}
        if shaders.get("status") != "passed" or not shaders.get("success") or shaders.get("using_null_rhi") or "metal" not in str(shaders.get("rhi", "")).lower():
            raise RuntimeError("Actual Metal shader verification did not pass; see preview_report.json")
        # No saved project level is loaded, and no save API is called below.
        world = unreal.EditorLoadingAndSavingUtils.new_blank_map(False)
        if world is None:
            raise RuntimeError("Could not create a transient preview world")
        report["transient_world"] = world.get_path_name()
        actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)

        def spawn(cls, location, rotation):
            actor = actors.spawn_actor_from_class(cls, unreal.Vector(*location), rotation, transient=True)
            if actor is None:
                raise RuntimeError("Could not create transient " + cls.__name__)
            created_actors.append(actor)
            return actor

        preview_actor = spawn(unreal.SkeletalMeshActor, [0, 0, 0], unreal.Rotator())
        component = preview_actor.get_component_by_class(unreal.SkeletalMeshComponent)
        component.set_skinned_asset_and_update(mesh, True)
        component.set_animation_mode(unreal.AnimationMode.ANIMATION_SINGLE_NODE)
        component.set_update_animation_in_editor(False)
        component.set_cast_shadow(True)
        component.prestream_textures(60, True, 0)
        origin, extent, radius = unreal.SystemLibrary.get_component_bounds(component)
        if radius <= 0 or not all(math.isfinite(value) for value in xyz(origin)+xyz(extent)):
            raise RuntimeError("Preview component has invalid bounds")
        camera_location, camera_report = fit_camera(xyz(origin), xyz(extent), yaw, elevation, fov, width, height)
        report["bounds"] = {"origin_cm": xyz(origin), "box_extent_cm": xyz(extent), "sphere_radius_cm": radius}
        report["camera"] = camera_report
        report["lighting"] = []
        for label, pitch, light_yaw, intensity, color, shadows in (
            ("key", -35, yaw+155, 5.0, [1, .94, .88, 1], True),
            ("fill", -15, yaw-90, 1.8, [.83, .90, 1, 1], False),
            ("rim", -25, yaw-25, 3.0, [1, 1, 1, 1], False),
        ):
            light_actor = spawn(unreal.DirectionalLight, xyz(origin), unreal.Rotator(pitch=pitch, yaw=light_yaw, roll=0))
            light = light_actor.get_component_by_class(unreal.DirectionalLightComponent)
            light.set_mobility(unreal.ComponentMobility.MOVABLE)
            light.set_intensity(intensity)
            light.set_light_color(unreal.LinearColor(*color))
            light.set_cast_shadows(shadows)
            report["lighting"].append({"role": label, "type": "Directional", "intensity_lux": intensity,
                                       "rotation_degrees": {"pitch": pitch, "yaw": light_yaw},
                                       "linear_color": color, "shadows": shadows})
        capture_actor = spawn(unreal.SceneCapture2D, camera_location,
                              unreal.MathLibrary.find_look_at_rotation(unreal.Vector(*camera_location), origin))
        capture = capture_actor.get_component_by_class(unreal.SceneCaptureComponent2D)
        capture.set_editor_property("capture_every_frame", False)
        capture.set_editor_property("capture_on_movement", False)
        capture.set_editor_property("always_persist_rendering_state", True)
        capture.set_editor_property("use_ray_tracing_if_enabled", False)
        capture.set_editor_property("fov_angle", fov)
        capture.set_editor_property("capture_source", unreal.SceneCaptureSource.SCS_FINAL_COLOR_LDR)
        capture.set_editor_property("primitive_render_mode", unreal.SceneCapturePrimitiveRenderMode.PRM_USE_SHOW_ONLY_LIST)
        capture.clear_show_only_components()
        capture.show_only_actor_components(preview_actor, False)
        settings = unreal.PostProcessSettings()
        settings.set_editor_property("override_auto_exposure_method", True)
        settings.set_editor_property("auto_exposure_method", unreal.AutoExposureMethod.AEM_MANUAL)
        settings.set_editor_property("override_auto_exposure_apply_physical_camera_exposure", True)
        settings.set_editor_property("auto_exposure_apply_physical_camera_exposure", False)
        settings.set_editor_property("override_auto_exposure_bias", True)
        settings.set_editor_property("auto_exposure_bias", 0.0)
        settings.set_editor_property("override_motion_blur_amount", True)
        settings.set_editor_property("motion_blur_amount", 0.0)
        settings.set_editor_property("override_bloom_intensity", True)
        settings.set_editor_property("bloom_intensity", 0.1)
        capture.set_editor_property("post_process_settings", settings)
        capture.set_editor_property("post_process_blend_weight", 1.0)
        # Process-local inspection preferences, not saved project settings.
        for command in ("r.AntiAliasingMethod 1", "r.DynamicGlobalIlluminationMethod 0", "r.ReflectionMethod 0"):
            unreal.SystemLibrary.execute_console_command(world, command)
        report["preview_console_overrides"] = {"r.AntiAliasingMethod": 1,
                                                "r.DynamicGlobalIlluminationMethod": 0, "r.ReflectionMethod": 0}
        render_target = unreal.RenderingLibrary.create_render_target2d(world, width, height,
            unreal.TextureRenderTargetFormat.RTF_RGBA8, unreal.LinearColor(.025, .03, .045, 1), False)
        if render_target is None:
            raise RuntimeError("Could not allocate the RGBA8 preview render target")
        capture.set_editor_property("texture_target", render_target)
        # Native verification flushes pending shader/render work for these
        # actual materials. CaptureScene plus export/readback blocks on the
        # real render target; no UI or desktop screenshot is involved.
        shaders_after_setup = json.loads(unreal.PCAssetImportLibrary.verify_material_shaders(material_packages))
        report["shaders_after_scene_setup"] = shaders_after_setup
        if not shaders_after_setup.get("success"):
            raise RuntimeError("Material shaders became unavailable during scene setup")
        OUTPUT.mkdir(parents=True, exist_ok=True)
        filename = row["target"].rsplit("/", 1)[-1] + "_yaw" + str(round(yaw, 2)).replace(".", "_") + ".png"
        image_path = OUTPUT / filename
        if image_path.exists():
            image_path.unlink()
        for _ in range(3):
            capture.capture_scene()
        unreal.RenderingLibrary.export_render_target(world, render_target, str(OUTPUT), filename)
        if not image_path.is_file() or image_path.stat().st_size < 100:
            raise RuntimeError("Scene capture did not export an image")
        report["png"] = {"file": image_path.relative_to(ROOT).as_posix(),
                         "sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
                         "bytes": image_path.stat().st_size, **png_statistics(image_path, width, height)}
        report["status"] = "captured_real_Metal_reference_pose_requires_visual_review"
        unreal.log("PC_ASSET_PREVIEW " + json.dumps({"status": report["status"], "png": report["png"]["file"]}))
    except Exception:
        report["status"] = "failed"
        report["error"] = traceback.format_exc()
        raise
    finally:
        write_report(report)
        if actors is not None:
            for actor in reversed(created_actors):
                try:
                    actors.destroy_actor(actor)
                except Exception:
                    pass
        if render_target is not None:
            unreal.RenderingLibrary.release_render_target2d(render_target)


main()
