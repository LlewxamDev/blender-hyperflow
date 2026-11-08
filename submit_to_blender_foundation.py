"""
Automated Gitea API Publisher for `https://projects.blender.org` (Official Blender Foundation Forge).

Because `github.com/blender/blender` is a read-only mirror, official Blender core Pull Requests
are hosted on `https://projects.blender.org/blender/blender` (powered by Gitea).

Usage:
  1. Log in to https://projects.blender.org (using your Blender ID).
  2. Create an Access Token at: https://projects.blender.org/user/settings/applications
     (enable `repo` read/write scope).
  3. Run:
       python submit_to_blender_foundation.py --token YOUR_GITEA_TOKEN
     (Add `--open-upstream-prs` to open the PRs directly against `blender/blender` instead of your fork.)
"""

import argparse
import base64
import json
import os
import urllib.error
import urllib.request
from build_verified_patches import build_modified_files
from publish_github_prs import build_pr_bodies

GITEA_API = "https://projects.blender.org/api/v1"


def gitea_request(method: str, endpoint: str, token: str, payload: dict = None) -> dict:
    url = f"{GITEA_API}/{endpoint.lstrip('/')}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"token {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "HyperFlow-Blender-PR-Publisher/1.0 (LlewxamDev)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gitea API {method} {url} failed ({e.code}): {err_body}") from e


def ensure_fork(token: str, username: str) -> str:
    try:
        info = gitea_request("GET", f"repos/{username}/blender", token)
        print(f"Found existing fork on projects.blender.org: {info['html_url']}")
        return f"{username}/blender"
    except RuntimeError:
        print("Forking blender/blender on projects.blender.org...")
        info = gitea_request("POST", "repos/blender/blender/forks", token, {"name": "blender"})
        print(f"Fork created: {info['html_url']}")
        return f"{username}/blender"


def push_branch_and_create_pr(
    token: str,
    fork_repo: str,
    target_repo: str,
    username: str,
    branch_name: str,
    commit_msg: str,
    files_dict: dict,
    pr_title: str,
    pr_body: str,
) -> str:
    # Check if branch exists on fork, create from main if not
    try:
        gitea_request("GET", f"repos/{fork_repo}/branches/{branch_name}", token)
    except RuntimeError:
        gitea_request(
            "POST",
            f"repos/{fork_repo}/branches",
            token,
            {"new_branch_name": branch_name, "old_branch_name": "main"},
        )

    for rel_path, new_content in files_dict.items():
        file_info = gitea_request("GET", f"repos/{fork_repo}/contents/{rel_path}?ref={branch_name}", token)
        file_sha = file_info["sha"]
        b64_content = base64.b64encode(new_content.encode("utf-8")).decode("ascii")
        gitea_request(
            "PUT",
            f"repos/{fork_repo}/contents/{rel_path}",
            token,
            {
                "branch": branch_name,
                "content": b64_content,
                "message": commit_msg,
                "sha": file_sha,
            },
        )

    head_ref = f"{username}:{branch_name}" if target_repo != fork_repo else branch_name
    pr_info = gitea_request(
        "POST",
        f"repos/{target_repo}/pulls",
        token,
        {
            "base": "main",
            "head": head_ref,
            "title": pr_title,
            "body": pr_body,
        },
    )
    return pr_info["html_url"]


def main():
    parser = argparse.ArgumentParser(description="Submit HyperFlow 3D C++ PRs to projects.blender.org")
    parser.add_argument("--token", required=True, help="Gitea Personal Access Token from projects.blender.org")
    parser.add_argument(
        "--open-upstream-prs",
        action="store_true",
        help="Open the Pull Requests directly on blender/blender instead of your fork",
    )
    args = parser.parse_args()

    user_info = gitea_request("GET", "user", args.token)
    username = user_info["login"]
    print(f"Authenticated on projects.blender.org as: {username}")

    fork_repo = ensure_fork(args.token, username)
    target_repo = "blender/blender" if args.open_upstream_prs else fork_repo

    files_map = build_modified_files()
    bodies = build_pr_bodies()

    pr1 = push_branch_and_create_pr(
        token=args.token,
        fork_repo=fork_repo,
        target_repo=target_repo,
        username=username,
        branch_name="feat-hyperflow-retopo-bake",
        commit_msg="Modeling: Automatic OpenVDB manifold proxy & surface reprojection in QuadriFlow",
        files_dict={
            "source/blender/editors/object/object_remesh.cc": files_map["source/blender/editors/object/object_remesh.cc"][1]
        },
        pr_title="Modeling: Automatic OpenVDB manifold proxy & surface reprojection in QuadriFlow",
        pr_body=bodies["p1"],
    )
    print("Created PR #1 on projects.blender.org:", pr1)

    pr2 = push_branch_and_create_pr(
        token=args.token,
        fork_repo=fork_repo,
        target_repo=target_repo,
        username=username,
        branch_name="feat-hyperflow-temporal-denoise",
        commit_msg="Compositor: 3x3 Neighborhood Variance-Clamped OIDN Temporal Boiling Stabilizer",
        files_dict={
            "source/blender/nodes/composite/nodes/node_composite_denoise.cc": files_map["source/blender/nodes/composite/nodes/node_composite_denoise.cc"][1]
        },
        pr_title="Compositor: 3x3 Neighborhood Variance-Clamped OIDN Temporal Boiling Stabilizer",
        pr_body=bodies["p2"],
    )
    print("Created PR #2 on projects.blender.org:", pr2)

    pr3 = push_branch_and_create_pr(
        token=args.token,
        fork_repo=fork_repo,
        target_repo=target_repo,
        username=username,
        branch_name="feat-hyperflow-cycles-micropoly",
        commit_msg="Cycles: Per-patch micropolygon dicing budget guard & Ptex UV index fix",
        files_dict={
            "intern/cycles/subd/dice.h": files_map["intern/cycles/subd/dice.h"][1],
            "intern/cycles/subd/dice.cpp": files_map["intern/cycles/subd/dice.cpp"][1],
        },
        pr_title="Cycles: Per-patch micropolygon dicing budget guard & Ptex UV index fix",
        pr_body=bodies["p3"],
    )
    print("Created PR #3 on projects.blender.org:", pr3)


if __name__ == "__main__":
    main()
