"""
HyperFlow 3D — Blender 5.2+ Next-Gen Pipeline Suite
1. Non-Destructive Anatomical/Curvature-Guided Quad Retopology + Multi-Map PBR Baker
2. Motion-Vector Temporal AI Denoiser (Zero Flashing / Boiling)
3. Camera-Frustum & Distance-Adaptive Micropolygon Displacement (Nanite-Style Memory Guard)
4. High-Performance Light Transport & Refractive Caustics (RIS Path Guiding + MNEE + Jacobian Shader)
"""

bl_info = {
    "name": "HyperFlow 3D: Auto-Retopo, Temporal AI Denoise, Nanite Displacement & Guided Caustics",
    "author": "Evandro & HyperFlow Open Source Contributors",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > HyperFlow 3D",
    "description": "One-click curvature-guided quad retopology + multi-map baking & next-gen Cycles/EEVEE rendering",
    "category": "Mesh",
}

import bpy
from bpy.props import IntProperty, FloatProperty, StringProperty

from . import retopo_bake
from . import temporal_denoiser
from . import micropolygon_nanite
from . import caustics_guiding


class HYPERFLOW_OT_auto_retopo_bake(bpy.types.Operator):
    bl_idname = "hyperflow.auto_retopo_bake"
    bl_label = "1-Click Auto-Retopo & Multi-Map Bake"
    bl_description = "Non-destructively retopologizes active high-poly sculpt into clean quads and bakes Normal, AO, Displacement & Curvature maps"
    bl_options = {'REGISTER', 'UNDO'}

    target_faces: IntProperty(name="Target Quad Faces", default=1200, min=100, max=250000)
    bake_resolution: IntProperty(name="Bake Texture Size", default=1024, min=128, max=8192)
    crease_angle_deg: FloatProperty(name="Feature Crease Angle", default=32.0, min=5.0, max=85.0)
    output_dir: StringProperty(name="Output Directory", default="", subtype='DIR_PATH')

    def execute(self, context):
        obj = context.active_object
        if obj is None or obj.type != 'MESH':
            self.report({'ERROR'}, "Select a High-Poly Mesh object first.")
            return {'CANCELLED'}
        out_dir = bpy.path.abspath(self.output_dir) if self.output_dir else None
        low_obj, metrics = retopo_bake.execute_auto_retopo_and_bake(
            high_obj=obj,
            target_faces=self.target_faces,
            bake_resolution=self.bake_resolution,
            output_dir=out_dir,
            crease_angle_deg=self.crease_angle_deg,
        )
        self.report(
            {'INFO'},
            f"Retopo Done: {metrics.highpoly_faces} -> {metrics.lowpoly_faces} faces "
            f"({metrics.quad_ratio*100:.1f}% quads, -{metrics.reduction_percent:.1f}% polys) in {metrics.elapsed_seconds:.2f}s"
        )
        return {'FINISHED'}


class HYPERFLOW_OT_configure_temporal_denoise(bpy.types.Operator):
    bl_idname = "hyperflow.configure_temporal_denoise"
    bl_label = "Enable Temporal Motion-Vector AI Denoiser"
    bl_description = "Configures Cycles Motion Vector, Depth, Normal, and Albedo passes + Temporal Variance-Clamped OIDN pipeline"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        temporal_denoiser.configure_temporal_denoise_passes(context.scene)
        self.report({'INFO'}, "HyperFlow Motion-Vector Temporal AI Denoiser configured on active scene.")
        return {'FINISHED'}


class HYPERFLOW_OT_micropolygon_displacement(bpy.types.Operator):
    bl_idname = "hyperflow.micropolygon_displacement"
    bl_label = "Apply Nanite Camera-Adaptive Displacement"
    bl_description = "Applies Camera-Frustum & Distance-Adaptive Micropolygon Dicing with strict RAM/Polygon budget guard"
    bl_options = {'REGISTER', 'UNDO'}

    max_face_budget: IntProperty(name="Max Polygon Budget", default=45000, min=1000, max=2000000)
    adaptive_pixel_size: FloatProperty(name="Target Dicing Pixel Size", default=1.25, min=0.25, max=8.0)
    displacement_amplitude: FloatProperty(name="Displacement Amplitude", default=0.42, min=0.01, max=10.0)

    def execute(self, context):
        obj = context.active_object
        if obj is None or obj.type != 'MESH':
            self.report({'ERROR'}, "Select a Terrain / Mesh object first.")
            return {'CANCELLED'}
        metrics = micropolygon_nanite.apply_camera_frustum_adaptive_dicing(
            obj=obj,
            cam_obj=context.scene.camera,
            max_face_budget=self.max_face_budget,
            adaptive_pixel_size=self.adaptive_pixel_size,
            displacement_amplitude=self.displacement_amplitude,
        )
        self.report(
            {'INFO'},
            f"Nanite Dicing: {metrics.adaptive_actual_faces} faces vs {metrics.uniform_equivalent_faces} uniform "
            f"({metrics.memory_savings_percent:.1f}% RAM saved)"
        )
        return {'FINISHED'}


class HYPERFLOW_OT_high_perf_caustics(bpy.types.Operator):
    bl_idname = "hyperflow.high_perf_caustics"
    bl_label = "Enable High-Performance Guided Caustics"
    bl_description = "Configures Parallax-Aware VMM Path Guiding, MNEE Shadow Caustics, and Jacobian Wavefront Caustic Shader"
    bl_options = {'REGISTER', 'UNDO'}

    ior: FloatProperty(name="Refractive IOR", default=1.48, min=1.01, max=2.6)
    dispersion: FloatProperty(name="Chromatic Dispersion", default=0.025, min=0.0, max=0.15)

    def execute(self, context):
        metrics = caustics_guiding.configure_high_performance_caustics(
            ior=self.ior,
            dispersion=self.dispersion,
        )
        self.report(
            {'INFO'},
            f"Guided Caustics active ({metrics.guiding_distribution}): "
            f"{len(metrics.casters_configured)} casters, {len(metrics.receivers_configured)} receivers."
        )
        return {'FINISHED'}


class VIEW3D_PT_hyperflow_main(bpy.types.Panel):
    bl_label = "HyperFlow 3D Engine"
    bl_idname = "VIEW3D_PT_hyperflow_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'HyperFlow 3D'

    def draw(self, context):
        layout = self.layout
        box1 = layout.box()
        box1.label(text="1. Auto-Retopology & Multi-Map Baker", icon='MOD_REMESH')
        box1.operator(HYPERFLOW_OT_auto_retopo_bake.bl_idname, icon='SHADING_WIRE')

        box2 = layout.box()
        box2.label(text="2. Motion-Vector Temporal AI Denoise", icon='RENDER_ANIMATION')
        box2.operator(HYPERFLOW_OT_configure_temporal_denoise.bl_idname, icon='NODE_COMPOSITING')

        box3 = layout.box()
        box3.label(text="3. Nanite Micropolygon Displacement", icon='RNDCURVE')
        box3.operator(HYPERFLOW_OT_micropolygon_displacement.bl_idname, icon='MOD_SUBSURF')

        box4 = layout.box()
        box4.label(text="4. Guided Light Transport & Caustics", icon='LIGHT_SUN')
        box4.operator(HYPERFLOW_OT_high_perf_caustics.bl_idname, icon='SHADING_RENDERED')


CLASSES = (
    HYPERFLOW_OT_auto_retopo_bake,
    HYPERFLOW_OT_configure_temporal_denoise,
    HYPERFLOW_OT_micropolygon_displacement,
    HYPERFLOW_OT_high_perf_caustics,
    VIEW3D_PT_hyperflow_main,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
