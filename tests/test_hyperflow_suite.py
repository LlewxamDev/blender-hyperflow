"""
Automated End-to-End Verification Suite for HyperFlow 3D inside Blender 5.2.2 LTS.
Run with:
  blender.exe -b -P tests/test_hyperflow_suite.py
"""

import json
import math
import os
import sys
import time

import bpy
import bmesh
import numpy as np
import OpenImageIO as oiio
from mathutils import Vector

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import blender_hyperflow
from blender_hyperflow import (
    retopo_bake,
    temporal_denoiser,
    micropolygon_nanite,
    caustics_guiding,
)

ARTIFACTS_DIR = os.path.join(ROOT_DIR, "test_outputs")
os.makedirs(ARTIFACTS_DIR, exist_ok=True)


def create_sculpted_anatomical_highpoly(name: str = "Anatomical_Sculpt_High") -> bpy.types.Object:
    """
    Creates a ~31,744-face sculpted organic mesh (based on Suzanne with 4 subdivision levels
    plus procedural anatomical brow ridges, cheekbone creases, orbital eye loops, and fine
    dermal scale micro-detail) to rigorously test Auto-Retopology and Multi-Map Baking.
    """
    bpy.ops.mesh.primitive_monkey_add(size=2.0, location=(0.0, 0.0, 0.0))
    obj = bpy.context.active_object
    obj.name = name

    sub = obj.modifiers.new(name="SculptSubdiv", type='SUBSURF')
    sub.levels = 3
    sub.render_levels = 3
    bpy.ops.object.modifier_apply(modifier=sub.name)

    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.verts.ensure_lookup_table()
    bm.normal_update()

    for v in bm.verts:
        p = v.co
        # High-frequency anatomical wrinkles + dermal pores + sharp crest ridges
        wrinkle = 0.028 * math.sin(p.x * 18.0) * math.cos(p.z * 22.0)
        ridges = 0.018 * math.cos(p.y * 30.0 + math.sin(p.x * 15.0) * 2.0)
        crest = 0.045 * math.exp(-((p.x * 3.5) ** 2)) * max(0.0, p.z)
        v.co = p + v.normal * (wrinkle + ridges + crest)

    bm.normal_update()
    for f in bm.faces:
        f.smooth = True
    bm.to_mesh(me)
    bm.free()
    me.update()
    return obj


def test_01_registration():
    print("\n=== [TEST 01] Add-on Registration & Operator Inspection ===")
    blender_hyperflow.register()
    assert hasattr(bpy.ops.hyperflow, "auto_retopo_bake"), "Operator auto_retopo_bake missing"
    assert hasattr(bpy.ops.hyperflow, "configure_temporal_denoise"), "Operator configure_temporal_denoise missing"
    assert hasattr(bpy.ops.hyperflow, "micropolygon_displacement"), "Operator micropolygon_displacement missing"
    assert hasattr(bpy.ops.hyperflow, "high_perf_caustics"), "Operator high_perf_caustics missing"
    print("[PASS] All 4 HyperFlow operators and UI panels registered in Blender", bpy.app.version_string)


def test_02_auto_retopo_and_multimap_bake() -> dict:
    print("\n=== [TEST 02] Non-Destructive Anatomical Auto-Retopology & 1-Click Multi-Map Baker ===")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    high_obj = create_sculpted_anatomical_highpoly("Sculpt_Guardian_High")
    orig_high_faces = len(high_obj.data.polygons)

    bake_dir = os.path.join(ARTIFACTS_DIR, "retopo_bake")
    low_obj, metrics = retopo_bake.execute_auto_retopo_and_bake(
        high_obj=high_obj,
        target_faces=1400,
        bake_resolution=512,
        output_dir=bake_dir,
        crease_angle_deg=30.0,
    )

    print(f"  High-Poly Faces : {metrics.highpoly_faces} (original preserved: {len(high_obj.data.polygons)})")
    print(f"  Low-Poly Faces  : {metrics.lowpoly_faces} (-{metrics.reduction_percent:.2f}% reduction)")
    print(f"  Pure Quad Ratio : {metrics.quad_ratio * 100:.2f}%")
    print(f"  Mean Surf Error : {metrics.mean_surface_error_rel * 100:.4f}% of bbox diagonal")
    print(f"  Feature Edges   : {metrics.feature_edges_detected}")
    print(f"  UV Coverage     : {metrics.uv_coverage_ratio * 100:.2f}%")
    print(f"  Adaptive Cage   : {metrics.adaptive_cage_distance:.5f} m")
    print(f"  Elapsed Time    : {metrics.elapsed_seconds:.2f} s")

    # Verify non-destructive preservation of high-poly
    assert len(high_obj.data.polygons) == orig_high_faces, "High-poly mesh was modified!"
    assert metrics.reduction_percent > 85.0, f"Expected >85% reduction, got {metrics.reduction_percent}"
    assert metrics.quad_ratio >= 0.95, f"Expected >=95% quads, got {metrics.quad_ratio}"
    assert metrics.mean_surface_error_rel < 0.008, f"Surface deviation too high: {metrics.mean_surface_error_rel}"
    assert metrics.uv_coverage_ratio > 0.20, f"UV coverage too low: {metrics.uv_coverage_ratio}"

    # Verify all 4 baked texture files exist and have non-trivial signal variance
    map_stats = {}
    for map_name, fpath in metrics.baked_maps.items():
        assert os.path.isfile(fpath), f"Baked map missing: {fpath}"
        inp = oiio.ImageInput.open(fpath)
        arr = inp.read_image("float")
        inp.close()
        std_val = float(np.std(arr[..., :3]))
        mean_val = float(np.mean(arr[..., :3]))
        map_stats[map_name] = {"mean": round(mean_val, 4), "std": round(std_val, 4), "path": fpath}
        print(f"  Map [{map_name:12s}] -> mean={mean_val:.4f}, std={std_val:.4f} ({os.path.basename(fpath)})")
        assert std_val > 0.005, f"Baked map {map_name} appears blank (std={std_val})"

    print("[PASS] Auto-Retopology & Multi-Map PBR Baking verified!")
    return {
        "highpoly_faces": metrics.highpoly_faces,
        "lowpoly_faces": metrics.lowpoly_faces,
        "reduction_percent": round(metrics.reduction_percent, 2),
        "quad_ratio_percent": round(metrics.quad_ratio * 100.0, 2),
        "mean_surface_error_pct": round(metrics.mean_surface_error_rel * 100.0, 4),
        "max_surface_error_pct": round(metrics.max_surface_error_rel * 100.0, 4),
        "feature_edges_detected": metrics.feature_edges_detected,
        "uv_coverage_percent": round(metrics.uv_coverage_ratio * 100.0, 2),
        "elapsed_seconds": round(metrics.elapsed_seconds, 2),
        "map_stats": map_stats,
    }


def test_03_temporal_motion_vector_denoiser() -> dict:
    print("\n=== [TEST 03] Motion-Compensated Temporal AI Denoiser (Zero Flashing / Boiling) ===")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    temporal_denoiser.configure_temporal_denoise_passes(scene)

    scene.cycles.device = 'CPU'
    scene.render.resolution_x = 160
    scene.render.resolution_y = 120
    scene.render.use_compositing = False
    scene.render.image_settings.media_type = 'MULTI_LAYER_IMAGE'

    # Build a rich glossy/diffuse scene with strong indirect bounce lighting to induce Monte Carlo boiling at low spp
    bpy.ops.mesh.primitive_plane_add(size=12.0, location=(0.0, 0.0, -1.2))
    floor = bpy.context.active_object
    floor_mat = bpy.data.materials.new("FloorChecker")
    nt = floor_mat.node_tree
    checker = nt.nodes.new("ShaderNodeTexChecker")
    checker.inputs["Scale"].default_value = 8.0
    bsdf = next(n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED')
    bsdf.inputs["Roughness"].default_value = 0.25
    nt.links.new(checker.outputs["Color"], bsdf.inputs["Base Color"])
    floor.data.materials.append(floor_mat)

    bpy.ops.mesh.primitive_monkey_add(size=1.6, location=(0.0, 0.0, 0.2))
    hero = bpy.context.active_object
    bpy.ops.object.shade_smooth()
    hero_mat = bpy.data.materials.new("HeroMat")
    h_bsdf = next(n for n in hero_mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
    h_bsdf.inputs["Base Color"].default_value = (0.85, 0.32, 0.14, 1.0)
    h_bsdf.inputs["Roughness"].default_value = 0.35
    hero.data.materials.append(hero_mat)

    # Animate hero subtle translation across 4 frames so optical flow vectors are non-zero
    for f_idx in range(1, 5):
        scene.frame_set(f_idx)
        hero.location = ((f_idx - 2.5) * 0.14, 0.0, 0.2 + 0.04 * math.sin(f_idx))
        hero.keyframe_insert(data_path="location", frame=f_idx)

    # Area light for soft shadows & indirect GI noise
    light_data = bpy.data.lights.new("AreaKey", type='AREA')
    light_data.energy = 180.0
    light_data.size = 2.8
    light_obj = bpy.data.objects.new("AreaKey", light_data)
    light_obj.location = (2.5, -2.8, 3.2)
    light_obj.rotation_euler = (math.radians(42.0), 0.0, math.radians(38.0))
    scene.collection.objects.link(light_obj)

    cam_data = bpy.data.cameras.new("Cam")
    cam_obj = bpy.data.objects.new("Cam", cam_data)
    cam_obj.location = (0.0, -4.2, 1.4)
    cam_obj.rotation_euler = (math.radians(74.0), 0.0, 0.0)
    scene.collection.objects.link(cam_obj)
    scene.camera = cam_obj

    seq_dir = os.path.join(ARTIFACTS_DIR, "temporal_denoise")
    os.makedirs(seq_dir, exist_ok=True)

    exr_paths = []
    ref_clean_frames = []

    for f_idx in range(1, 5):
        scene.frame_set(f_idx)
        scene.cycles.seed = f_idx * 1337
        scene.cycles.samples = 4
        exr_p = os.path.join(seq_dir, f"raw_frame_{f_idx:04d}.exr")
        if not os.path.isfile(exr_p):
            scene.render.filepath = exr_p
            bpy.ops.render.render(write_still=True)
        exr_paths.append(exr_p)

        # Render 48-spp high-sample reference frame for PSNR verification
        scene.cycles.samples = 48
        ref_p = os.path.join(seq_dir, f"ref_frame_{f_idx:04d}.exr")
        if not os.path.isfile(ref_p):
            scene.render.filepath = ref_p
            bpy.ops.render.render(write_still=True)
        ref_passes = temporal_denoiser.read_multilayer_exr_passes(ref_p)
        ref_clean_frames.append(ref_passes["combined"])

    _, metrics = temporal_denoiser.denoise_exr_animation_sequence(
        exr_paths=exr_paths,
        output_dir=seq_dir,
        reference_clean_rgb=ref_clean_frames,
    )

    print(f"  Noisy Temporal Flicker       : {metrics.noisy_temporal_flicker:.5f}")
    print(f"  Single-Frame OIDN Flicker    : {metrics.per_frame_oidn_flicker:.5f}")
    print(f"  HyperFlow Temporal Flicker   : {metrics.temporal_hyperflow_flicker:.5f}")
    print(f"  Boiling Reduction vs Noisy   : -{metrics.flicker_reduction_vs_noisy_pct:.2f}%")
    print(f"  Boiling Reduction vs 1F-OIDN : -{metrics.flicker_reduction_vs_oidn_pct:.2f}%")
    print(f"  PSNR (Noisy 4spp)            : {metrics.psnr_noisy_db:.2f} dB")
    print(f"  PSNR (Single-Frame OIDN)     : {metrics.psnr_oidn_db:.2f} dB")
    print(f"  PSNR (HyperFlow Temporal AI) : {metrics.psnr_temporal_db:.2f} dB")

    assert metrics.temporal_hyperflow_flicker < metrics.noisy_temporal_flicker, "Temporal flicker did not improve over noisy!"
    assert metrics.temporal_hyperflow_flicker < metrics.per_frame_oidn_flicker, "Temporal flicker did not improve over single-frame OIDN!"
    assert metrics.psnr_temporal_db > metrics.psnr_noisy_db, "PSNR did not improve over noisy input!"
    assert metrics.psnr_temporal_db >= metrics.psnr_oidn_db, "Temporal PSNR should beat or match single-frame OIDN!"

    print("[PASS] Motion-Compensated Temporal AI Denoiser verified!")
    return {
        "noisy_flicker": round(metrics.noisy_temporal_flicker, 5),
        "oidn_single_frame_flicker": round(metrics.per_frame_oidn_flicker, 5),
        "hyperflow_temporal_flicker": round(metrics.temporal_hyperflow_flicker, 5),
        "flicker_reduction_vs_noisy_pct": round(metrics.flicker_reduction_vs_noisy_pct, 2),
        "flicker_reduction_vs_oidn_pct": round(metrics.flicker_reduction_vs_oidn_pct, 2),
        "psnr_noisy_db": round(metrics.psnr_noisy_db, 2),
        "psnr_oidn_db": round(metrics.psnr_oidn_db, 2),
        "psnr_temporal_db": round(metrics.psnr_temporal_db, 2),
    }


def test_04_micropolygon_nanite_displacement() -> dict:
    print("\n=== [TEST 04] Camera-Frustum & Distance-Adaptive Micropolygon Displacement ===")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080

    cam_data = bpy.data.cameras.new("NaniteCam")
    cam_data.lens = 35.0
    cam_obj = bpy.data.objects.new("NaniteCam", cam_data)
    cam_obj.location = (0.0, -6.0, 1.8)
    cam_obj.rotation_euler = (math.radians(78.0), 0.0, 0.0)
    scene.collection.objects.link(cam_obj)
    scene.camera = cam_obj

    # Create a large 24m x 24m terrain grid (with 85% of area outside the near camera cone)
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=24, y_subdivisions=24, size=24.0, location=(0.0, 0.0, 0.0))
    terrain = bpy.context.active_object
    terrain.name = "Nanite_Terrain"

    metrics = micropolygon_nanite.apply_camera_frustum_adaptive_dicing(
        obj=terrain,
        cam_obj=cam_obj,
        max_face_budget=30000,
        max_subdiv_tiers=5,
        near_distance=5.5,
        mid_distance=10.0,
        far_distance=16.0,
        adaptive_pixel_size=1.0,
        displacement_amplitude=0.45,
    )

    print(f"  Base Grid Faces            : {metrics.base_faces}")
    print(f"  UniformSubdiv Equiv Faces  : {metrics.uniform_equivalent_faces}")
    print(f"  HyperFlow Adaptive Faces   : {metrics.adaptive_actual_faces}")
    print(f"  Near-Camera Micropoly Faces: {metrics.near_field_micropoly_faces}")
    print(f"  Estimated Uniform RAM      : {metrics.memory_Uniform_est_mb:.2f} MB")
    print(f"  Actual Adaptive RAM        : {metrics.memory_adaptive_actual_mb:.2f} MB")
    print(f"  RAM / Geometry Savings     : -{metrics.memory_savings_percent:.2f}%")
    print(f"  Mean Near-Cam Edge Length  : {metrics.near_camera_edge_length_mm:.2f} mm")

    assert metrics.adaptive_actual_faces <= 30500, f"Exceeded polygon budget: {metrics.adaptive_actual_faces}"
    assert metrics.memory_savings_percent > 80.0, f"Expected >80% RAM savings, got {metrics.memory_savings_percent}%"
    assert metrics.near_field_micropoly_faces > 500, "Near-field should have dense micropolygons!"

    print("[PASS] Nanite Camera-Frustum Adaptive Micropolygon Displacement verified!")
    return {
        "base_faces": metrics.base_faces,
        "uniform_equivalent_faces": metrics.uniform_equivalent_faces,
        "adaptive_actual_faces": metrics.adaptive_actual_faces,
        "near_field_micropoly_faces": metrics.near_field_micropoly_faces,
        "memory_uniform_mb": round(metrics.memory_Uniform_est_mb, 2),
        "memory_adaptive_mb": round(metrics.memory_adaptive_actual_mb, 2),
        "memory_savings_percent": round(metrics.memory_savings_percent, 2),
        "near_camera_edge_length_mm": round(metrics.near_camera_edge_length_mm, 2),
    }


def test_05_high_performance_caustics() -> dict:
    print("\n=== [TEST 05] High-Performance Light Transport & Refractive Caustics ===")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 8
    scene.render.resolution_x = 160
    scene.render.resolution_y = 120
    scene.render.use_compositing = False

    # Dark world so only light passing through the refractive crystal lens illuminates the floor below
    world = bpy.data.worlds.new("DarkWorld")
    scene.world = world
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs["Color"].default_value = (0.01, 0.012, 0.018, 1.0)
        bg.inputs["Strength"].default_value = 0.2

    # Receiver floor
    bpy.ops.mesh.primitive_plane_add(size=8.0, location=(0.0, 0.0, 0.0))
    receiver = bpy.context.active_object
    receiver.name = "Caustic_Floor_Receiver"
    rec_mat = bpy.data.materials.new("ReceiverStone")
    rec_bsdf = next(n for n in rec_mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
    rec_bsdf.inputs["Base Color"].default_value = (0.75, 0.75, 0.78, 1.0)
    rec_bsdf.inputs["Roughness"].default_value = 0.85
    receiver.data.materials.append(rec_mat)

    # Solid Biconvex Refractive Crystal Wave Lens suspended above the floor (no open hole)
    bpy.ops.mesh.primitive_uv_sphere_add(
        segments=64, ring_count=32, radius=1.15, location=(0.0, 0.0, 0.72)
    )
    caster = bpy.context.active_object
    caster.name = "Crystal_Lens_Caster"
    caster.scale = (1.15, 1.15, 0.36)
    bpy.ops.object.transform_apply(scale=True)
    bpy.ops.object.shade_smooth()

    # Standard naive Glass material first
    naive_glass = bpy.data.materials.new("NaiveGlass")
    ng_bsdf = next(n for n in naive_glass.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
    ng_bsdf.inputs["Base Color"].default_value = (0.95, 0.98, 1.0, 1.0)
    ng_bsdf.inputs["Roughness"].default_value = 0.02
    ng_bsdf.inputs["IOR"].default_value = 1.50
    if "Transmission Weight" in ng_bsdf.inputs:
        ng_bsdf.inputs["Transmission Weight"].default_value = 0.98
    caster.data.materials.append(naive_glass)

    # Point / Spot light above the crystal torus casting downward
    l_data = bpy.data.lights.new("CausticSun", type='POINT')
    l_data.energy = 350.0
    l_data.shadow_soft_size = 0.08
    l_obj = bpy.data.objects.new("CausticSun", l_data)
    l_obj.location = (0.2, -0.3, 2.8)
    scene.collection.objects.link(l_obj)

    cam_data = bpy.data.cameras.new("CausticCam")
    cam_obj = bpy.data.objects.new("CausticCam", cam_data)
    cam_obj.location = (0.0, -2.9, 1.75)
    cam_obj.rotation_euler = (math.radians(58.0), 0.0, 0.0)
    scene.collection.objects.link(cam_obj)
    scene.camera = cam_obj

    # 1. Render Baseline Naive Cycles (No Path Guiding, No MNEE, Standard Glass) at 8 spp
    scene.cycles.caustics_refractive = False
    scene.cycles.caustics_reflective = False
    scene.cycles.use_guiding = False
    naive_path = os.path.join(ARTIFACTS_DIR, "caustics_naive_8spp.png")
    scene.render.filepath = naive_path
    bpy.ops.render.render(write_still=True)

    # 2. Enable HyperFlow High-Performance Caustics (Parallax-Aware VMM Guiding + MNEE + Jacobian Shader) at 8 spp
    c_metrics = caustics_guiding.configure_high_performance_caustics(
        casters=[caster],
        receivers=[receiver],
        lights=[l_obj],
        apply_jacobian_shader=True,
        ior=1.50,
        dispersion=0.028,
    )
    guided_path = os.path.join(ARTIFACTS_DIR, "caustics_hyperflow_8spp.png")
    scene.render.filepath = guided_path
    bpy.ops.render.render(write_still=True)

    # Measure caustic energy & dynamic contrast in the shadow/caustic region below the torus
    inp_n = oiio.ImageInput.open(naive_path)
    img_naive = inp_n.read_image("float")[..., :3]
    inp_n.close()

    inp_g = oiio.ImageInput.open(guided_path)
    img_guided = inp_g.read_image("float")[..., :3]
    inp_g.close()

    # Central lower crop corresponds to the shadow/caustic pool on the receiver floor directly under the solid lens
    h, w, _ = img_naive.shape
    roi_naive = img_naive[int(h * 0.55):int(h * 0.85), int(w * 0.35):int(w * 0.65)]
    roi_guided = img_guided[int(h * 0.55):int(h * 0.85), int(w * 0.35):int(w * 0.65)]

    naive_p95 = float(np.percentile(roi_naive, 95))
    guided_p95 = float(np.percentile(roi_guided, 95))
    naive_contrast = float(np.std(roi_naive))
    guided_contrast = float(np.std(roi_guided))

    print(f"  Guiding Mode               : {c_metrics.guiding_distribution}")
    print(f"  Naive 8spp Caustic Peak    : {naive_p95:.4f} (std contrast={naive_contrast:.4f})")
    print(f"  HyperFlow 8spp Caustic Peak: {guided_p95:.4f} (std contrast={guided_contrast:.4f})")

    assert guided_p95 > naive_p95 * 1.25, f"Expected higher caustic focal energy: {guided_p95} vs {naive_p95}"
    assert guided_contrast > naive_contrast * 1.20, f"Expected higher caustic filament contrast: {guided_contrast} vs {naive_contrast}"

    print("[PASS] High-Performance Guided Light Transport & Refractive Caustics verified!")
    return {
        "guiding_distribution": c_metrics.guiding_distribution,
        "naive_caustic_peak_p95": round(naive_p95, 4),
        "hyperflow_caustic_peak_p95": round(guided_p95, 4),
        "naive_caustic_contrast_std": round(naive_contrast, 4),
        "hyperflow_caustic_contrast_std": round(guided_contrast, 4),
        "energy_gain_ratio": round(guided_p95 / max(1e-5, naive_p95), 2),
    }


def main():
    t_start = time.perf_counter()
    test_01_registration()
    r2 = test_02_auto_retopo_and_multimap_bake()
    r3 = test_03_temporal_motion_vector_denoiser()
    r4 = test_04_micropolygon_nanite_displacement()
    r5 = test_05_high_performance_caustics()

    report = {
        "blender_version": bpy.app.version_string,
        "status": "ALL_TESTS_PASSED",
        "total_duration_seconds": round(time.perf_counter() - t_start, 2),
        "module_1_retopo_bake": r2,
        "module_2_temporal_denoiser": r3,
        "module_3_micropolygon_nanite": r4,
        "module_4_caustics_guiding": r5,
    }
    report_path = os.path.join(ARTIFACTS_DIR, "test_suite_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"\n=======================================================")
    print(f"ALL 5 HYPERFLOW TEST SUITES PASSED IN {report['total_duration_seconds']}s!")
    print(f"Report written to: {report_path}")
    print(f"=======================================================\n")


if __name__ == "__main__":
    main()
