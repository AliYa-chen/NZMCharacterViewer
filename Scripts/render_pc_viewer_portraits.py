"""Render and import PC outfit portraits for all twelve viewer characters.

Run in the Editor commandlet with -AllowCommandletRendering -run=pythonscript
-script=<this file>. Never use -nullrhi. Optional arguments:
-PCPortraitCharacters=bella,bunny -PCPortraitYaw=75 -PCPortraitPitch=3
-PCPortraitWidth=512 -PCPortraitHeight=640 -PCPortraitTime=0.4.
Use -PCPortraitFraming=face for eye/material inspection before the final bust
portraits; a different framing/time invalidates retained portrait rows.
Keep the default AnimationData plugin enabled; include it explicitly when
launching with -NoEnginePlugins.

Consumes the frozen viewer plan and completed animation report. Captures the
first configured UI outfit on real Metal with neutral inspection lights and
samples its adapted clips through the native preview-pose helper. Only twelve
/Game/PCAssets/Portraits/T_PC_<character> Texture2D packages are saved. The
Catalog, existing five portraits and project maps are never saved or edited.
The PNGs and provenance report are written to Saved/PCViewerIntegration.
"""
from datetime import datetime, timezone
import hashlib
import itertools
import json
import math
from pathlib import Path
import re
import struct
import sys
import traceback
import zlib


ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "Saved/PCViewerIntegration"
OUTPUT = WORK / "Portraits"
REPORT_PATH = WORK / "portraits_report.json"
PLAN_PATH = WORK / "viewer_plan.json"
ANIMATION_PATH = WORK / "animation_report.json"
BASELINE_PATH = WORK / "baseline_catalog.json"
INDEX_PATH = ROOT / "Docs/PCAssetConversion/asset_index.json"
TARGET_ROOT = "/Game/PCAssets/Portraits"
PORTRAIT_CHARACTERS = ("angel", "angela", "bella", "bunny", "emma", "lilith",
                       "molly", "naimei", "queen", "shirley", "tina", "widow")
LOCAL_ACCESSORIES = {"10056010007", "10054010006", "10054010007"}


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_report(report):
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = REPORT_PATH.with_suffix(".json.new")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(REPORT_PATH)


def package(reference):
    return str(reference).split(".", 1)[0]


def asset_file(reference):
    name = package(reference)
    if not re.fullmatch(r"/Game/[A-Za-z0-9_/]+", name):
        raise ValueError("Expected a project asset package: " + name)
    return ROOT / "Content" / (name[len("/Game/"):] + ".uasset")


def fingerprint(reference):
    path = asset_file(reference)
    if not path.is_file():
        raise RuntimeError("Missing saved asset: " + str(path))
    return {"package": package(reference), "file": path.relative_to(ROOT).as_posix(),
            "bytes": path.stat().st_size, "sha256": digest(path)}


def xyz(value):
    return [float(value.x), float(value.y), float(value.z)]


def fit_camera(origin, extent, yaw_degrees, pitch_degrees, horizontal_fov, width, height):
    """Fit an anatomical head/shoulders box, never the full character bounds."""
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
    distance = max(max(required) * 1.06, 30)
    position = [origin[i]+direction[i]*distance for i in range(3)]
    return position, {"location_cm": position, "target_cm": origin,
                      "framing_extent_cm": extent, "yaw_degrees": yaw_degrees,
                      "elevation_degrees": pitch_degrees, "distance_cm": distance,
                      "horizontal_fov_degrees": horizontal_fov,
                      "fit": "Head bone and upper torso; head/hair/hat geometry determines top padding."}


def png_statistics(path, width, height):
    """The same strict PNG/filter integrity check as the PC mesh preview."""
    encoded = path.read_bytes()
    if encoded[:8] != b"\x89PNG\r\n\x1a\n":
        raise RuntimeError("Capture export is not a PNG")
    offset, compressed, header, ended = 8, bytearray(), None, False
    while offset < len(encoded):
        if offset + 12 > len(encoded):
            raise RuntimeError("Truncated portrait PNG")
        count = struct.unpack_from(">I", encoded, offset)[0]
        kind, data = encoded[offset+4:offset+8], encoded[offset+8:offset+8+count]
        if len(data) != count or offset+count+12 > len(encoded):
            raise RuntimeError("Truncated portrait PNG chunk")
        crc = struct.unpack_from(">I", encoded, offset+8+count)[0]
        if zlib.crc32(kind+data) & 0xffffffff != crc:
            raise RuntimeError("Portrait PNG checksum failed")
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", data)
        elif kind == b"IDAT":
            compressed.extend(data)
        offset += count + 12
        if kind == b"IEND":
            ended = True
            break
    if not ended or not header or header[:2] != (width, height):
        raise RuntimeError("Incomplete PNG or portrait dimensions differ from render target")
    _, _, bits, color, _, _, interlace = header
    if bits != 8 or color not in (2, 6) or interlace:
        raise RuntimeError("RGBA8 render target exported an unexpected PNG layout")
    channels = 4 if color == 6 else 3
    row_size = width * channels
    raw = zlib.decompress(compressed)
    if len(raw) != height * (row_size+1):
        raise RuntimeError("Portrait PNG pixel payload length is invalid")
    previous = bytearray(row_size)
    minima, maxima, bins, first_rgb, different = [255]*3, [0]*3, set(), None, 0
    alpha_min, alpha_max, center_bins = 255, 0, set()
    for y in range(height):
        mode = raw[y*(row_size+1)]
        row = bytearray(raw[y*(row_size+1)+1:(y+1)*(row_size+1)])
        if mode > 4:
            raise RuntimeError("Unknown portrait PNG scanline filter")
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
            quantized = tuple(value//16 for value in rgb)
            bins.add(quantized)
            if width//4 <= x < 3*width//4 and height//8 <= y < 7*height//8:
                center_bins.add(quantized)
            for channel in range(3):
                minima[channel] = min(minima[channel], rgb[channel])
                maxima[channel] = max(maxima[channel], rgb[channel])
        previous = row
    if different < max(100, width*height//1000) or max(maxima[i]-minima[i] for i in range(3)) < 8:
        raise RuntimeError("Portrait is blank or nearly uniform")
    if alpha_max == 0 or len(center_bins) < 8:
        raise RuntimeError("Portrait has no visible, detailed central subject")
    return {"width": width, "height": height, "bit_depth": bits, "color_type": color,
            "pixel_check": "nonuniform_rendered_pixels_and_nonuniform_center",
            "rgb_min": minima, "rgb_max": maxima, "quantized_rgb_bins": len(bins),
            "central_quantized_rgb_bins": len(center_bins), "pixels_different_from_first": different,
            "alpha_min": alpha_min, "alpha_max": alpha_max,
            "validation_scope": "PNG integrity and nonblank pixels; portrait composition requires visual review."}


class PortraitRenderer:
    def __init__(self, unreal, world, settings, animation_rows, index, material_helper, material_report):
        self.ue, self.world, self.settings = unreal, world, settings
        self.animation_rows, self.index = animation_rows, index
        self.material_helper, self.material_report = material_helper, material_report
        self.actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
        self.created, self.render_target = [], None

    def spawn(self, cls, location, rotation=None):
        rotation = rotation or self.ue.Rotator()
        actor = self.actors.spawn_actor_from_class(cls, self.ue.Vector(*location), rotation, transient=True)
        if actor is None:
            raise RuntimeError("Could not spawn transient " + cls.__name__)
        self.created.append(actor)
        return actor

    def spawn_part(self):
        """Use the runtime component's pose-aware bounds, including seated clips."""
        ue = self.ue
        actor = self.spawn(ue.Actor, [0, 0, 0])
        # AddComponentByClass is a reflected UFUNCTION marked ScriptNoExport;
        # call_method reaches it without inventing component registration APIs.
        component = actor.call_method("AddComponentByClass", args=(
            ue.ViewerSkeletalMeshComponent.static_class(), False, ue.Transform(), False))
        if not isinstance(component, ue.ViewerSkeletalMeshComponent):
            raise RuntimeError("Could not create the runtime viewer mesh component")
        component.set_secondary_motion_enabled(False)
        return actor, component

    def cleanup(self):
        failures = []
        for actor in reversed(self.created):
            try:
                if not self.actors.destroy_actor(actor):
                    failures.append("Could not destroy a transient portrait actor")
            except Exception:
                failures.append(traceback.format_exc())
        self.created.clear()
        if self.render_target is not None:
            try:
                self.ue.RenderingLibrary.release_render_target2d(self.render_target)
            except Exception:
                failures.append(traceback.format_exc())
            self.render_target = None
        if failures:
            raise RuntimeError("Portrait scene cleanup failed: " + "\n".join(failures))

    def converted_asset(self, reference):
        actual = fingerprint(reference)
        expected = self.index.get(package(reference))
        if expected is None or any(actual[key] != expected[key] for key in ("file", "bytes", "sha256")):
            raise RuntimeError("Converted asset differs from verified index: " + reference)
        return actual

    def render(self, character):
        ue, settings = self.ue, self.settings
        outfit = character["outfits"][0]
        row = {"character_id": character["id"], "outfit_id": outfit["id"], "status": "started",
               "target": TARGET_ROOT + "/T_PC_" + character["id"], "parts": [],
               "missing_default_parts": outfit.get("missing_default_parts", []),
               "source_scene_reconstructed": False}
        components, material_packages, shown_actors, reference_framing = {}, set(), [], {}
        for part in outfit["parts"]:
            mesh_target = part.get("mesh_target", part.get("mesh"))
            mesh = ue.load_asset(mesh_target)
            if not isinstance(mesh, ue.SkeletalMesh):
                raise RuntimeError("Portrait mesh did not load: " + mesh_target)
            mesh_fingerprint = self.converted_asset(mesh_target)
            if package(mesh.get_editor_property("skeleton").get_path_name()) != part["skeleton_target"]:
                raise RuntimeError("Portrait mesh is not on its planned native skeleton")
            slots = list(mesh.get_editor_property("materials"))
            native_materials = [package(slot.material_interface.get_path_name()) if slot.material_interface else None for slot in slots]
            if native_materials != part["materials"]:
                raise RuntimeError("Portrait mesh material bindings differ from UI plan")
            for material in native_materials:
                if material:
                    self.converted_asset(material)
            effective_paths = self.material_helper.effective_material_paths(mesh_target, native_materials, self.material_report)
            effective_materials = self.material_helper.bound_materials(ue, mesh, self.material_report)
            if [package(value.get_path_name()) if value else None for value in effective_materials] != effective_paths:
                raise RuntimeError("Viewer material helper returned a different approved material array")
            material_packages.update(value for value in effective_paths if value)
            actor, component = self.spawn_part()
            shown_actors.append(actor)
            component.set_skinned_asset_and_update(mesh, True)
            for slot, material in enumerate(effective_materials):
                component.set_material(slot, material)
            component.set_cast_shadow(True)
            component.set_update_animation_in_editor(False)
            component.prestream_textures(60, True, 0)
            components[part["id"]] = component
            transform = part["transform"]
            component.set_relative_transform(ue.Transform(
                location=ue.Vector(*transform["translation"]), rotation=ue.Quat(*transform["rotation_xyzw"]).rotator(),
                scale=ue.Vector(*transform["scale"])), False, True)
            if component.does_socket_exist("b_Head"):
                reference_bounds = mesh.get_bounds()
                reference_top = float(reference_bounds.origin.z + reference_bounds.box_extent.z)
                reference_head = xyz(component.get_socket_location("b_Head"))
                reference_framing[part["id"]] = {
                    "head_bone_cm": reference_head,
                    "geometry_top_above_reference_head_cm": reference_top - reference_head[2]}
            animation = self.animation_rows[character["id"], outfit["id"], part["id"]]
            part_row = {"part_id": part["id"], "source_part_id": part["source_part_id"],
                        "mesh": mesh_fingerprint, "skeleton": self.converted_asset(part["skeleton_target"]),
                        "native_materials": native_materials, "materials": effective_paths,
                        "material_fingerprints": [fingerprint(value) if value else None for value in effective_paths],
                        "material_override_count": sum(a != b for a, b in zip(native_materials, effective_paths)),
                        "component_class": component.get_class().get_name(),
                        "bounds_policy": "Runtime UViewerSkeletalMeshComponent unions imported and sampled component-space bone bounds.",
                        "animation_status": animation["status"],
                        "source_kind": animation.get("source_kind"), "animation": None}
            if animation["status"] in ("adapted", "reused"):
                clip_path = animation["animation"]
                clip = ue.load_asset(clip_path)
                if not isinstance(clip, ue.AnimSequence) or clip.get_editor_property("skeleton") != mesh.get_editor_property("skeleton"):
                    raise RuntimeError("Portrait animation is not on the mesh's exact skeleton")
                clip_fingerprint = fingerprint(clip_path)
                if clip_fingerprint["sha256"] != animation["asset_sha256"] or clip_fingerprint["bytes"] != animation["asset_bytes"]:
                    raise RuntimeError("Portrait animation differs from completed animation report")
                time = min(settings["time_seconds"], float(clip.get_play_length()))
                if not ue.PCViewerAnimationLibrary.sample_preview_pose(component, clip, time):
                    raise RuntimeError("Native animation pose sampling failed")
                part_row.update(animation=clip_fingerprint, sampled_time_seconds=time)
            elif animation["status"] == "attached_reference_pose" and str(part["source_part_id"]) in LOCAL_ACCESSORIES:
                part_row["pose"] = "local reference geometry follows the animated body socket"
            elif animation["status"] == "reference_pose_incompatible" and animation.get("reason"):
                part_row["pose"] = "declared incompatible part remains at reference pose"
                part_row["reason"] = animation["reason"]
            else:
                raise RuntimeError("Unsupported portrait part animation state")
            bounds_origin, bounds_extent, _ = ue.SystemLibrary.get_component_bounds(component)
            part_row["sampled_component_bounds"] = {"origin_cm": xyz(bounds_origin), "extent_cm": xyz(bounds_extent)}
            part_row["sampled_anatomical_bones"] = {bone: xyz(component.get_socket_location(bone))
                for bone in ("b_Root", "b_Hips", "b_Neck", "b_Head") if component.does_socket_exist(bone)}
            if part.get("slot") == "EAvatarSlot::Head":
                eye_poses = {}
                for bone in ("R_Eye_JNT", "L_Eye_JNT"):
                    if component.does_socket_exist(bone):
                        pose = component.get_socket_transform(bone)
                        eye_poses[bone] = {"world_location_cm": xyz(pose.translation),
                            "world_rotation_xyzw": [float(getattr(pose.rotation, axis)) for axis in "xyzw"]}
                part_row["sampled_eye_bone_poses"] = eye_poses
            row["parts"].append(part_row)
        bodies = [part for part in outfit["parts"] if part.get("slot") in ("EAvatarSlot::Body", "EAvatarSlot::WholeBody")]
        if len(bodies) != 1:
            raise RuntimeError("Portrait requires one configured body component")
        body = components[bodies[0]["id"]]
        for part in outfit["parts"]:
            if str(part["source_part_id"]) not in LOCAL_ACCESSORIES:
                continue
            socket = part["source_attachment"]["socket_name"]
            if not body.does_socket_exist(socket):
                raise RuntimeError("Body lacks configured accessory socket: " + socket)
            component = components[part["id"]]
            if not component.attach_to_component(body, socket, ue.AttachmentRule.KEEP_RELATIVE,
                                                  ue.AttachmentRule.KEEP_RELATIVE, ue.AttachmentRule.KEEP_RELATIVE, False):
                raise RuntimeError("Could not attach local portrait accessory")
            row["parts"][next(i for i, x in enumerate(row["parts"]) if x["part_id"] == part["id"])]["attachment"] = {
                "parent_part_id": bodies[0]["id"], "socket": socket}
        head_parts = [part for part in outfit["parts"] if part.get("slot") == "EAvatarSlot::Head"
                      and components[part["id"]].does_socket_exist("b_Head")]
        framing_part = head_parts[0] if head_parts else bodies[0]
        framing_anchor = components[framing_part["id"]]
        if not framing_anchor.does_socket_exist("b_Head"):
            raise RuntimeError("Portrait framing requires the actual b_Head anatomical landmark")
        # GetBoneLocation has no UFUNCTION in this engine build. The inherited
        # socket API also resolves skeletal bones and is exposed to Python.
        head = xyz(framing_anchor.get_socket_location("b_Head"))
        if not all(math.isfinite(v) for v in head) or head[2] < 40:
            raise RuntimeError("Portrait anatomical landmark is invalid")
        top = head[2] + 19
        framing_parts = []
        for part in outfit["parts"]:
            if part.get("slot") not in ("EAvatarSlot::Head", "EAvatarSlot::Hair", "EAvatarSlot::Hat", "EAvatarSlot::Glasses"):
                continue
            origin, extent, radius = ue.SystemLibrary.get_component_bounds(components[part["id"]])
            if not math.isfinite(radius) or radius <= 0:
                raise RuntimeError("Portrait head component has invalid bounds")
            candidate = float(origin.z + extent.z)
            basis = "current component bounds for local accessory"
            reference = reference_framing.get(part["id"])
            if reference is not None and str(part["source_part_id"]) not in LOCAL_ACCESSORIES:
                # Imported skinned bounds can stay at the reference-pose head
                # height during seated motion. Follow the sampled head bone,
                # retaining the geometry's measured padding above that bone.
                candidate = float(components[part["id"]].get_socket_location("b_Head").z) + (
                    reference["geometry_top_above_reference_head_cm"])
                basis = "reference geometry top offset from sampled head bone"
            if not head[2] - 20 <= candidate <= head[2] + 80:
                raise RuntimeError("Portrait head component bounds require an explicit framing review")
            top = max(top, candidate + 4)
            framing_parts.append({"part_id": part["id"], "origin_cm": xyz(origin), "extent_cm": xyz(extent),
                                  "framing_top_z_cm": candidate, "basis": basis})
        face_frame = settings["framing"] == "face"
        bottom = head[2] - (10 if face_frame else 38)
        focus = [head[0], head[1], (top+bottom)/2]
        extent = [16, 8, (top-bottom)/2] if face_frame else [23, 12, (top-bottom)/2]
        location, camera = fit_camera(focus, extent, settings["yaw_degrees"], settings["pitch_degrees"],
                                      settings["fov_degrees"], settings["width"], settings["height"])
        row["camera"] = {**camera, "head_bone_cm": head, "head_anchor_part_id": framing_part["id"],
                         "head_component_bounds": framing_parts,
                         "crop_bottom_z_cm": bottom, "crop_top_z_cm": top}
        shaders = json.loads(ue.PCAssetImportLibrary.verify_material_shaders(sorted(material_packages)))
        row["shaders"] = shaders
        if shaders.get("status") != "passed" or not shaders.get("success") or shaders.get("using_null_rhi") or "metal" not in str(shaders.get("rhi", "")).lower():
            raise RuntimeError("Portrait materials did not pass actual Metal shader verification")
        row["lighting"] = []
        for label, pitch, light_yaw, intensity, color, shadows in (
            ("key", -35, settings["yaw_degrees"]+155, 5.0, [1, .94, .88, 1], True),
            ("fill", -15, settings["yaw_degrees"]-90, 1.8, [.83, .90, 1, 1], False),
            ("rim", -25, settings["yaw_degrees"]-25, 3.0, [1, 1, 1, 1], False),
        ):
            actor = self.spawn(ue.DirectionalLight, focus, ue.Rotator(pitch=pitch, yaw=light_yaw, roll=0))
            light = actor.get_component_by_class(ue.DirectionalLightComponent)
            light.set_mobility(ue.ComponentMobility.MOVABLE)
            light.set_intensity(intensity)
            light.set_light_color(ue.LinearColor(*color))
            light.set_cast_shadows(shadows)
            row["lighting"].append({"role": label, "type": "Directional", "intensity_lux": intensity,
                                    "rotation_degrees": {"pitch": pitch, "yaw": light_yaw}, "linear_color": color})
        actor = self.spawn(ue.SceneCapture2D, location,
                           ue.MathLibrary.find_look_at_rotation(ue.Vector(*location), ue.Vector(*focus)))
        capture = actor.get_component_by_class(ue.SceneCaptureComponent2D)
        for name, value in (("capture_every_frame", False), ("capture_on_movement", False),
                            ("always_persist_rendering_state", True), ("use_ray_tracing_if_enabled", False),
                            ("fov_angle", settings["fov_degrees"]),
                            ("capture_source", ue.SceneCaptureSource.SCS_FINAL_COLOR_LDR),
                            ("primitive_render_mode", ue.SceneCapturePrimitiveRenderMode.PRM_USE_SHOW_ONLY_LIST)):
            capture.set_editor_property(name, value)
        capture.clear_show_only_components()
        for actor in shown_actors:
            capture.show_only_actor_components(actor, False)
        post = ue.PostProcessSettings()
        for name, value in (("auto_exposure_method", ue.AutoExposureMethod.AEM_MANUAL),
                            ("auto_exposure_apply_physical_camera_exposure", False), ("auto_exposure_bias", 0.0),
                            ("motion_blur_amount", 0.0), ("bloom_intensity", 0.1)):
            post.set_editor_property("override_" + name, True)
            post.set_editor_property(name, value)
        capture.set_editor_property("post_process_settings", post)
        capture.set_editor_property("post_process_blend_weight", 1.0)
        self.render_target = ue.RenderingLibrary.create_render_target2d(self.world, settings["width"], settings["height"],
            ue.TextureRenderTargetFormat.RTF_RGBA8, ue.LinearColor(.025, .03, .045, 1), False)
        if self.render_target is None:
            raise RuntimeError("Could not allocate portrait render target")
        capture.set_editor_property("texture_target", self.render_target)
        after = json.loads(ue.PCAssetImportLibrary.verify_material_shaders(sorted(material_packages)))
        if not after.get("success") or after.get("using_null_rhi"):
            raise RuntimeError("Portrait shaders became unavailable after scene setup")
        image_path = OUTPUT / ("T_PC_" + character["id"] + ".png")
        if image_path.exists():
            image_path.unlink()
        for _ in range(3):
            capture.capture_scene()
        ue.RenderingLibrary.export_render_target(self.world, self.render_target, str(OUTPUT), image_path.name)
        row["png"] = {"file": image_path.relative_to(ROOT).as_posix(), "sha256": digest(image_path),
                      "bytes": image_path.stat().st_size,
                      **png_statistics(image_path, settings["width"], settings["height"])}
        if settings.get("capture_only", False):
            row.update(status="captured_transient_diagnostic_requires_visual_review", content_saved=False)
            return row
        task = ue.AssetImportTask()
        for name, value in (("filename", str(image_path)), ("destination_path", TARGET_ROOT),
                            ("destination_name", image_path.stem), ("automated", True),
                            ("replace_existing", True), ("replace_existing_settings", True), ("save", False)):
            task.set_editor_property(name, value)
        task.set_editor_property("factory", ue.TextureFactory())
        ue.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
        imported = [value for value in task.get_objects() if value is not None]
        if not imported:
            imported = [ue.load_asset(path) for path in task.get_editor_property("imported_object_paths")]
        if len(imported) != 1 or not isinstance(imported[0], ue.Texture2D) or package(imported[0].get_path_name()) != row["target"]:
            raise RuntimeError("Portrait import did not produce the exact requested Texture2D")
        texture = imported[0]
        source_data = texture.get_editor_property("asset_import_data")
        if source_data is None or Path(source_data.get_first_filename()).resolve() != image_path.resolve():
            raise RuntimeError("Portrait texture source does not identify this capture PNG")
        for name, value in (("srgb", True), ("compression_settings", ue.TextureCompressionSettings.TC_EDITOR_ICON),
                            ("lod_group", ue.TextureGroup.TEXTUREGROUP_UI), ("never_stream", True),
                            ("mip_gen_settings", ue.TextureMipGenSettings.TMGS_NO_MIPMAPS)):
            texture.set_editor_property(name, value)
        for name, value in (("PCViewerPortrait.Owner", "PCViewerPortraits.v1"),
                            ("PCViewerPortrait.Character", character["id"]),
                            ("PCViewerPortrait.Outfit", outfit["id"]),
                            ("PCViewerPortrait.PNG.SHA256", row["png"]["sha256"])):
            ue.EditorAssetLibrary.set_metadata_tag(texture, name, value)
        if not ue.EditorAssetLibrary.save_loaded_asset(texture, False):
            raise RuntimeError("Generated portrait Texture2D did not save")
        dimensions = [int(texture.blueprint_get_size_x()), int(texture.blueprint_get_size_y())]
        if dimensions != [settings["width"], settings["height"]]:
            raise RuntimeError("Saved portrait texture dimensions differ from its PNG")
        row.update(status="passed", texture={**fingerprint(row["target"]), "dimensions": dimensions,
                    "srgb": bool(texture.get_editor_property("srgb")),
                    "compression_settings": str(texture.get_editor_property("compression_settings")),
                    "lod_group": str(texture.get_editor_property("lod_group")),
                    "never_stream": bool(texture.get_editor_property("never_stream"))},
                   visual_review="required: inspect face direction, crop and converted material appearance")
        return row


def main():
    import unreal as ue
    report = {"schema_version": 1, "status": "started", "started_at_utc": datetime.now(timezone.utc).isoformat(),
              "portraits": [], "catalog_saved": False, "map_saved": False,
              "content_scope": TARGET_ROOT + "/T_PC_<all twelve character ids>",
              "limitations": [
                  "Neutral inspection lighting and camera framing; source-game presentation is not reconstructed.",
                  "Existing viewer idle is adapted; missing original character-specific animation is not recovered.",
                  "Stock converted materials retain their documented private-shader limitations.",
                  "Nonblank PNG checks do not replace visual review of faces and composition."]}
    renderer = None
    protected = {}
    try:
        command_line = ue.SystemLibrary.get_command_line()
        if re.search(r"(?:^|\s)-nullrhi(?:\s|$)", command_line, re.I) or not re.search(r"(?:^|\s)-AllowCommandletRendering(?:\s|$)", command_line, re.I):
            raise RuntimeError("Portrait generation requires real Metal and -AllowCommandletRendering")
        if not hasattr(ue.PCViewerAnimationLibrary, "sample_preview_pose"):
            raise RuntimeError("Compile PCAssetImporter with SamplePreviewPose before rendering portraits")
        if ue.load_class(None, "/Script/AnimationData.AnimationSequencerDataModel") is None:
            raise RuntimeError("Enable the AnimationData plugin before loading adapted animation data models")
        def option(name, default):
            matches = re.findall(r"(?:^|\s)-" + re.escape(name) + r"=(\"[^\"]*\"|[^\s]+)", command_line)
            return matches[-1].strip('"') if matches else default
        def numeric(name, default, minimum, maximum):
            value = float(option(name, str(default)))
            if not math.isfinite(value) or not minimum <= value <= maximum:
                raise ValueError(name + " is outside its permitted range")
            return value
        settings = {"yaw_degrees": numeric("PCPortraitYaw", 75, -36000, 36000) % 360,
                    "pitch_degrees": numeric("PCPortraitPitch", 3, -25, 25), "fov_degrees": 32,
                    "width": int(numeric("PCPortraitWidth", 512, 128, 2048)),
                    "height": int(numeric("PCPortraitHeight", 640, 128, 2048)),
                    "time_seconds": numeric("PCPortraitTime", .4, 0, 3600),
                    "framing": option("PCPortraitFraming", "bust")}
        if settings["framing"] not in ("bust", "face"):
            raise ValueError("PCPortraitFraming must be bust or face")
        requested = option("PCPortraitCharacters", ",".join(PORTRAIT_CHARACTERS)).split(",")
        if not requested or len(requested) != len(set(requested)) or set(requested) - set(PORTRAIT_CHARACTERS):
            raise ValueError("PCPortraitCharacters must contain unique ids from the twelve planned characters")
        plan, animations, baseline, index = (read_json(path) for path in (PLAN_PATH, ANIMATION_PATH, BASELINE_PATH, INDEX_PATH))
        if str(ROOT / "Scripts") not in sys.path:
            sys.path.insert(0, str(ROOT / "Scripts"))
        import pc_viewer_materials
        material_report = pc_viewer_materials.load_report(ROOT)
        inputs = {"plan_sha256": digest(PLAN_PATH), "animation_report_sha256": digest(ANIMATION_PATH),
                  "baseline_catalog_sha256": digest(BASELINE_PATH), "asset_index_sha256": digest(INDEX_PATH),
                  "renderer_script_sha256": digest(Path(__file__)),
                  "preview_plugin_sha256": digest(ROOT / "Plugins/PCAssetImporter/Binaries/Mac/libUnrealEditor-PCAssetImporter.dylib"),
                  "runtime_module_sha256": digest(ROOT / "Binaries/Mac/libUnrealEditor-NZMCharacterViewer.dylib"),
                  "material_overrides_report_sha256": digest(pc_viewer_materials.REPORT_PATH),
                  "material_helper_sha256": digest(ROOT / "Scripts/pc_viewer_materials.py")}
        report.update(inputs=inputs, settings=settings, requested_characters=requested)
        if animations.get("status") != "passed" or animations.get("plan_sha256") != inputs["plan_sha256"] or animations.get("baseline_catalog_sha256") != inputs["baseline_catalog_sha256"]:
            raise RuntimeError("Portraits require a complete, current animation report")
        if plan.get("asset_index_sha256") != inputs["asset_index_sha256"]:
            raise RuntimeError("Viewer plan uses a different converted-asset index")
        characters = {character["id"]: character for character in plan["characters"]}
        if set(characters) != set(PORTRAIT_CHARACTERS):
            raise RuntimeError("Expected exactly the twelve planned portrait characters")
        for character in baseline["characters"]:
            for portrait in character["portrait"]:
                protected[package(portrait)] = fingerprint(portrait)
        protected["/Game/Viewer/Data/DA_ViewerCatalog"] = fingerprint("/Game/Viewer/Data/DA_ViewerCatalog")
        animation_rows = {(row["character_id"], row["outfit_id"], row["part_id"]): row for row in animations["parts"]}
        if len(animation_rows) != len(animations["parts"]):
            raise RuntimeError("Animation report contains duplicate part identities")
        expected = {(c["id"], o["id"], p["id"]) for c in plan["characters"] for o in c["outfits"] for p in o["parts"]}
        if set(animation_rows) != expected or animations.get("processed_parts") != len(expected):
            raise RuntimeError("Animation report does not cover the exact viewer-plan parts")
        previous = read_json(REPORT_PATH) if REPORT_PATH.exists() else {}
        retained = {row["character_id"]: row for row in previous.get("portraits", [])
                    if previous.get("inputs") == inputs and previous.get("settings") == settings and row.get("status") == "passed"}
        OUTPUT.mkdir(parents=True, exist_ok=True)
        world = ue.EditorLoadingAndSavingUtils.new_blank_map(False)
        if world is None:
            raise RuntimeError("Could not create a transient portrait world")
        report["transient_world"] = world.get_path_name()
        for command in ("r.AntiAliasingMethod 1", "r.DynamicGlobalIlluminationMethod 0", "r.ReflectionMethod 0"):
            ue.SystemLibrary.execute_console_command(world, command)
        report["process_console_overrides"] = {"r.AntiAliasingMethod": 1, "r.DynamicGlobalIlluminationMethod": 0,
                                               "r.ReflectionMethod": 0}
        renderer = PortraitRenderer(ue, world, settings, animation_rows, {row["target"]: row for row in index["assets"]},
                                    pc_viewer_materials, material_report)
        failures = False
        for key in requested:
            ue.log("PC_VIEWER_PORTRAIT " + key)
            try:
                retained[key] = renderer.render(characters[key])
            except Exception:
                retained[key] = {"character_id": key, "status": "failed", "error": traceback.format_exc()}
                failures = True
            finally:
                renderer.cleanup()
                report["portraits"] = [retained[key] for key in PORTRAIT_CHARACTERS if key in retained]
                write_report(report)
        for row in report["portraits"]:
            if row.get("status") == "passed":
                if fingerprint(row["target"]) != {key: row["texture"][key] for key in ("package", "file", "bytes", "sha256")} or digest(ROOT / row["png"]["file"]) != row["png"]["sha256"]:
                    raise RuntimeError("A retained portrait or PNG no longer matches its recorded fingerprint")
        report["status"] = "failed" if failures else "passed" if len(retained) == len(PORTRAIT_CHARACTERS) else "partial"
        if failures:
            raise RuntimeError("One or more portraits failed; inspect portraits_report.json")
    except Exception:
        report["status"] = "failed"
        report["error"] = traceback.format_exc()
        raise
    finally:
        if renderer is not None:
            try:
                renderer.cleanup()
            except Exception:
                report["status"] = "failed"
                report["cleanup_error"] = traceback.format_exc()
        report["protected_assets"] = []
        for reference, before in protected.items():
            try:
                after = fingerprint(reference)
                protected_row = {"package": reference, "sha256_before": before["sha256"],
                                 "sha256_after": after["sha256"], "unchanged": after == before}
            except Exception:
                protected_row = {"package": reference, "unchanged": False, "error": traceback.format_exc()}
            report["protected_assets"].append(protected_row)
        if any(not row["unchanged"] for row in report["protected_assets"]):
            report["status"] = "failed"
            report["error"] = "An existing portrait or Catalog changed during portrait rendering"
        report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        report["completed_portraits"] = sum(row.get("status") == "passed" for row in report["portraits"])
        write_report(report)
        ue.log("PC_VIEWER_PORTRAITS_REPORT " + json.dumps({key: report.get(key) for key in ("status", "completed_portraits")}, ensure_ascii=False))
    if report["status"] == "failed":
        raise RuntimeError("Portrait generation failed; see " + str(REPORT_PATH))


if __name__ == "__main__":
    main()
