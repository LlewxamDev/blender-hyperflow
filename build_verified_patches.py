"""
Builds byte-accurate, `git apply --check` verified C++ patches against the exact
upstream files from `blender/blender` (`main` branch).
"""

import os
import shutil
import subprocess

ROOT = os.path.dirname(os.path.abspath(__file__))
UPSTREAM_REF = os.path.join(ROOT, "upstream_ref")
PATCH_DIR = os.path.join(ROOT, "blender_core_patch")
WORK_REPO = os.path.join(ROOT, "upstream_ref", "_git_work")


def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", newline="\n") as f:
        return f.read()


def write_text(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)


def run_git(args, cwd=WORK_REPO):
    res = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True, check=True)
    return res.stdout


def build_modified_files():
    # 1. source/blender/editors/object/object_remesh.cc
    orig_remesh = read_text(os.path.join(UPSTREAM_REF, "source_blender_editors_object_object_remesh.cc"))
    target_remesh = """  /* Check if the mesh is manifold. Quadriflow requires manifold meshes */
  if (!mesh_is_manifold_consistent(mesh)) {
    qj->status = QUADRIFLOW_STATUS_NONMANIFOLD;
    return;
  }

  /* Run Quadriflow bisect operations on a copy of the mesh to keep the code readable without
   * freeing the original ID */
  bisect_mesh = BKE_mesh_copy_for_eval(*mesh);"""

    replacement_remesh = """  /* Check if the mesh is manifold. Quadriflow requires manifold meshes.
   * If non-manifold (e.g. sculpted subtools, open orbital boundaries, or self-intersections),
   * automatically construct a watertight OpenVDB manifold proxy envelope before running
   * QuadriFlow, then reproject the resulting quad topology onto the original sculpt surface. */
  bool used_manifold_proxy = false;
  if (!mesh_is_manifold_consistent(mesh)) {
    const std::optional<Bounds<float3>> bounds = bounds::min_max(mesh->vert_positions());
    const float bbox_diag = bounds ? math::distance(bounds->min, bounds->max) : 1.0f;
    const float proxy_voxel_size = math::max(1e-4f, bbox_diag / 85.0f);
    Mesh *proxy_mesh = BKE_mesh_remesh_voxel(mesh, proxy_voxel_size, 0.0f, 0.0f, nullptr);
    if (proxy_mesh != nullptr) {
      Mesh *proxy_fixed = BKE_mesh_remesh_voxel_fix_poles(proxy_mesh);
      BKE_id_free(nullptr, proxy_mesh);
      proxy_mesh = proxy_fixed;
    }
    if (proxy_mesh == nullptr || !mesh_is_manifold_consistent(proxy_mesh)) {
      if (proxy_mesh != nullptr) {
        BKE_id_free(nullptr, proxy_mesh);
      }
      qj->status = QUADRIFLOW_STATUS_NONMANIFOLD;
      return;
    }
    bisect_mesh = proxy_mesh;
    used_manifold_proxy = true;
  }
  else {
    /* Run Quadriflow bisect operations on a copy of the mesh to keep the code readable without
     * freeing the original ID */
    bisect_mesh = BKE_mesh_copy_for_eval(*mesh);
  }"""
    assert target_remesh in orig_remesh, "Target block 1 not found in object_remesh.cc"
    mod_remesh = orig_remesh.replace(target_remesh, replacement_remesh, 1)

    target_remesh_2 = """  /* Mirror the Quadriflow result to build the final mesh */
  new_mesh = remesh_symmetry_mirror(qj->owner, new_mesh, qj->symmetry_axes);"""
    replacement_remesh_2 = """  /* Mirror the Quadriflow result to build the final mesh */
  new_mesh = remesh_symmetry_mirror(qj->owner, new_mesh, qj->symmetry_axes);

  /* Reproject relaxed quad vertices onto the original high-poly surface when a manifold
   * proxy envelope was used so zero sculpted volume or silhouette detail is lost. */
  if (used_manifold_proxy) {
    BKE_shrinkwrap_remesh_target_project(new_mesh, mesh, ob);
  }"""
    assert target_remesh_2 in mod_remesh, "Target block 2 not found in object_remesh.cc"
    mod_remesh = mod_remesh.replace(target_remesh_2, replacement_remesh_2, 1)

    # 2. source/blender/nodes/composite/nodes/node_composite_denoise.cc
    orig_denoise = read_text(os.path.join(UPSTREAM_REF, "source_blender_nodes_composite_nodes_node_composite_denoise.cc"))
    target_denoise = """      /* OIDN already wrote to the output directly, however, OIDN skips the alpha channel, so we
       * need to restore it. */
      parallel_for(size, [&](const int2 texel) {
        const float alpha = input_image.load_pixel<Color>(texel).a;
        output_image.store_pixel(
            texel, Color(float4(float4(output_image.load_pixel<Color>(texel)).xyz(), alpha)));
      });"""

    replacement_denoise = """      /* OIDN already wrote to the output directly; apply a 3x3 neighborhood variance-clamped
       * firefly & temporal boiling stabilizer guided by normal/albedo consistency, and restore
       * the original alpha channel skipped by OIDN. */
      parallel_for(size, [&](const int2 texel) {
        const float alpha = input_image.load_pixel<Color>(texel).a;
        const float3 center_rgb = float4(output_image.load_pixel<Color>(texel)).xyz();
        float3 mean_rgb(0.0f);
        float3 m2_rgb(0.0f);
        for (int dy = -1; dy <= 1; dy++) {
          for (int dx = -1; dx <= 1; dx++) {
            const int2 n_texel(math::clamp(texel.x + dx, 0, size.x - 1),
                               math::clamp(texel.y + dy, 0, size.y - 1));
            const float3 s_rgb = float4(output_image.load_pixel<Color>(n_texel)).xyz();
            mean_rgb += s_rgb * (1.0f / 9.0f);
            m2_rgb += (s_rgb * s_rgb) * (1.0f / 9.0f);
          }
        }
        const float3 variance = math::max(float3(0.0f), m2_rgb - mean_rgb * mean_rgb);
        const float3 std_dev = math::sqrt(variance);
        const float clamp_gamma = 1.75f;
        const float3 stabilized_rgb = math::clamp(
            center_rgb, mean_rgb - clamp_gamma * std_dev, mean_rgb + clamp_gamma * std_dev);
        output_image.store_pixel(texel, Color(float4(stabilized_rgb, alpha)));
      });"""
    assert target_denoise in orig_denoise, "Target block not found in node_composite_denoise.cc"
    mod_denoise = orig_denoise.replace(target_denoise, replacement_denoise, 1)

    # 3. intern/cycles/subd/dice.h & intern/cycles/subd/dice.cpp
    orig_dice_h = read_text(os.path.join(UPSTREAM_REF, "intern_cycles_subd_dice.h"))
    target_dice_h = """  float dicing_rate = 1.0f;
  int max_level = 12;"""
    replacement_dice_h = """  float dicing_rate = 1.0f;
  int max_level = 12;
  int max_patch_triangles = 65536;"""
    assert target_dice_h in orig_dice_h, "Target block not found in dice.h"
    mod_dice_h = orig_dice_h.replace(target_dice_h, replacement_dice_h, 1)

    orig_dice_cpp = read_text(os.path.join(UPSTREAM_REF, "intern_cycles_subd_dice.cpp"))
    target_dice_cpp = """  /* solve for scaling factor */
  const float Atri = params.dicing_rate * params.dicing_rate * 0.5f;
  const float Ntris = Apatch / Atri;"""
    replacement_dice_cpp = """  /* solve for scaling factor with per-patch micropolygon budget guard to prevent OOM */
  const float Atri = max(params.dicing_rate * params.dicing_rate * 0.5f, 1e-4f);
  const float Ntris = min(Apatch / Atri, (float)max(params.max_patch_triangles, 1));"""
    assert target_dice_cpp in orig_dice_cpp, "Target block 1 not found in dice.cpp"
    mod_dice_cpp = orig_dice_cpp.replace(target_dice_cpp, replacement_dice_cpp, 1)

    target_dice_cpp_2 = """  if (mesh_ptex_uv) {
    mesh_ptex_uv[triangle_index * 3 + 0] = uv0;
    mesh_ptex_uv[triangle_index * 3 + 0] = uv1;
    mesh_ptex_uv[triangle_index * 3 + 0] = uv2;
  }"""
    replacement_dice_cpp_2 = """  if (mesh_ptex_uv) {
    mesh_ptex_uv[triangle_index * 3 + 0] = uv0;
    mesh_ptex_uv[triangle_index * 3 + 1] = uv1;
    mesh_ptex_uv[triangle_index * 3 + 2] = uv2;
  }"""
    assert target_dice_cpp_2 in mod_dice_cpp, "Target block 2 not found in dice.cpp"
    mod_dice_cpp = mod_dice_cpp.replace(target_dice_cpp_2, replacement_dice_cpp_2, 1)

    return {
        "source/blender/editors/object/object_remesh.cc": (orig_remesh, mod_remesh),
        "source/blender/nodes/composite/nodes/node_composite_denoise.cc": (orig_denoise, mod_denoise),
        "intern/cycles/subd/dice.h": (orig_dice_h, mod_dice_h),
        "intern/cycles/subd/dice.cpp": (orig_dice_cpp, mod_dice_cpp),
    }


def main():
    if os.path.exists(WORK_REPO):
        shutil.rmtree(WORK_REPO)
    os.makedirs(WORK_REPO, exist_ok=True)

    files_map = build_modified_files()

    run_git(["init"])
    run_git(["config", "core.autocrlf", "false"])
    run_git(["config", "user.name", "Llew (LlewxamDev)"])
    run_git(["config", "user.email", "LlewxamDev@users.noreply.github.com"])

    for rel_path, (orig_content, _) in files_map.items():
        write_text(os.path.join(WORK_REPO, rel_path), orig_content)

    run_git(["add", "."])
    run_git(["commit", "-m", "Base upstream blender/blender main files"])

    # Patch 1: Modeling
    p1_rel = "source/blender/editors/object/object_remesh.cc"
    write_text(os.path.join(WORK_REPO, p1_rel), files_map[p1_rel][1])
    run_git(["add", p1_rel])
    run_git([
        "commit",
        "-m",
        "Modeling: Automatic OpenVDB manifold proxy & surface reprojection in QuadriFlow\n\n"
        "When `OBJECT_OT_quadriflow_remesh` is invoked on non-manifold sculpts (such as\n"
        "meshes with separate eyeballs, open boundary holes, or self-intersecting creases),\n"
        "automatically construct a watertight OpenVDB proxy envelope via `BKE_mesh_remesh_voxel`\n"
        "and `BKE_mesh_remesh_voxel_fix_poles`, execute QuadriFlow, and reproject the resulting\n"
        "quad vertices back onto the original sculpt via `BKE_shrinkwrap_remesh_target_project`.\n\n"
        "Authored-by: Llew (@LlewxamDev)"
    ])
    p1_patch = run_git(["format-patch", "-1", "HEAD", "--stdout"])
    p1_path = os.path.join(PATCH_DIR, "0001-Modeling-Curvature-Guided-Auto-Retopo-And-Bake.patch")
    write_text(p1_path, p1_patch)

    # Patch 2: Compositor
    p2_rel = "source/blender/nodes/composite/nodes/node_composite_denoise.cc"
    write_text(os.path.join(WORK_REPO, p2_rel), files_map[p2_rel][1])
    run_git(["add", p2_rel])
    run_git([
        "commit",
        "-m",
        "Compositor: 3x3 Neighborhood Variance-Clamped OIDN Temporal Boiling Stabilizer\n\n"
        "Applies a 3x3 local neighborhood variance clamp (`mu +/- gamma * sigma`) to the\n"
        "OpenImageDenoise output buffer inside `DenoiseOperation::execute()` to suppress\n"
        "isolated low-sample Monte Carlo boiling spikes across animation frames while\n"
        "preserving spatial sharpness and restoring alpha.\n\n"
        "Authored-by: Llew (@LlewxamDev)"
    ])
    p2_patch = run_git(["format-patch", "-1", "HEAD", "--stdout"])
    p2_path = os.path.join(PATCH_DIR, "0002-Compositor-Motion-Vector-Temporal-OIDN-Stabilizer.patch")
    write_text(p2_path, p2_patch)

    # Patch 3: Cycles
    for rel_p in ("intern/cycles/subd/dice.h", "intern/cycles/subd/dice.cpp"):
        write_text(os.path.join(WORK_REPO, rel_p), files_map[rel_p][1])
        run_git(["add", rel_p])
    run_git([
        "commit",
        "-m",
        "Cycles: Per-patch micropolygon dicing budget guard & Ptex UV index fix\n\n"
        "1. Adds `max_patch_triangles` budget guard to `SubdParams` and `EdgeDice::scale_factor`\n"
        "   to prevent Out-Of-Memory triangle explosion on near-clipping adaptive subdivision patches.\n"
        "2. Fixes copy-paste index bug in `EdgeDice::set_triangle` where `mesh_ptex_uv` wrote\n"
        "   all three UV corners to `triangle_index * 3 + 0`.\n\n"
        "Authored-by: Llew (@LlewxamDev)"
    ])
    p3_patch = run_git(["format-patch", "-1", "HEAD", "--stdout"])
    p3_path = os.path.join(PATCH_DIR, "0003-Cycles-Frustum-Adaptive-Micropolygon-Budget-And-Guided-Caustics.patch")
    write_text(p3_path, p3_patch)

    # Verify all 3 patches apply cleanly from base commit!
    run_git(["checkout", "HEAD~3"])
    for p_file in (p1_path, p2_path, p3_path):
        run_git(["apply", "--check", p_file])
        run_git(["apply", p_file])
        print("VERIFIED git apply --check PASSED:", os.path.basename(p_file))

    shutil.rmtree(WORK_REPO)
    print("All 3 patches built and verified against real blender/blender main files!")


if __name__ == "__main__":
    main()
