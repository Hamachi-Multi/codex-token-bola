#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
from typing import Any


SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import public_snapshot_commit_policy


PUBLIC_OPS_PREFIX = "chore(public-ops): "
TEMPORARY_BASELINE_TAG = "v0.0.0"
INITIAL_RELEASE_VERSION = "0.1.0"
INITIAL_RELEASE_TAG = f"v{INITIAL_RELEASE_VERSION}"
VERSION_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+(?:[-+].+)?$")


class InputError(Exception):
    pass


def run_git(repo_root: pathlib.Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo_root, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise InputError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def commit_message(repo_root: pathlib.Path, sha: str) -> str:
    return run_git(repo_root, "show", "-s", "--format=%B", sha)


def version_tags(repo_root: pathlib.Path) -> list[str]:
    tags = run_git(repo_root, "tag", "--list", "v*").splitlines()
    return sorted(tag for tag in tags if VERSION_TAG_RE.fullmatch(tag))


def validate_initial_history(repo_root: pathlib.Path, product_sha: str) -> str:
    head_sha = run_git(repo_root, "rev-parse", "HEAD")
    product_sha = run_git(repo_root, "rev-parse", f"{product_sha}^{{commit}}")
    run_git(repo_root, "merge-base", "--is-ancestor", product_sha, head_sha)
    history = run_git(repo_root, "rev-list", "--reverse", head_sha).splitlines()
    if product_sha not in history:
        raise InputError("initial product SHA is not reachable from public main")

    product_commits: list[str] = []
    for sha in history:
        if not commit_message(repo_root, sha).splitlines()[0].startswith(PUBLIC_OPS_PREFIX):
            product_commits.append(sha)
    if product_commits != [product_sha]:
        raise InputError("initial release history must contain exactly one product snapshot")

    policy = public_snapshot_commit_policy.validate_snapshot_commit_message(commit_message(repo_root, product_sha))
    if not policy["ok"]:
        raise InputError("initial product snapshot does not satisfy the public snapshot policy")
    if policy["type"] != "feat" or policy["breaking"]:
        raise InputError("initial v0.1.0 release requires a non-breaking feat snapshot")

    parents = run_git(repo_root, "show", "-s", "--format=%P", product_sha).split()
    if len(parents) != 1:
        raise InputError("initial product snapshot must have exactly one bootstrap parent")
    baseline_sha = parents[0]
    if not commit_message(repo_root, baseline_sha).splitlines()[0].startswith(PUBLIC_OPS_PREFIX):
        raise InputError("initial product parent must be a public ops bootstrap commit")
    return baseline_sha


def prepare_initial_release(*, repo_root: pathlib.Path | str, product_sha: str) -> dict[str, Any]:
    repo_root = pathlib.Path(repo_root)
    if not repo_root.is_dir():
        raise InputError(f"repo root is not a directory: {repo_root}")
    tags = version_tags(repo_root)
    if TEMPORARY_BASELINE_TAG in tags:
        raise InputError("remote or pre-existing v0.0.0 is forbidden; the baseline must be runner-local")
    if tags:
        return {
            "ok": True,
            "errors": [],
            "mode": "established",
            "temporary_baseline_tag": None,
            "expected_release_tag": None,
        }

    baseline_sha = validate_initial_history(repo_root, product_sha)
    run_git(repo_root, "tag", TEMPORARY_BASELINE_TAG, baseline_sha)
    return {
        "ok": True,
        "errors": [],
        "mode": "initial",
        "temporary_baseline_tag": TEMPORARY_BASELINE_TAG,
        "temporary_baseline_sha": baseline_sha,
        "expected_release_tag": INITIAL_RELEASE_TAG,
    }


def write_github_output(path: pathlib.Path, result: dict[str, Any]) -> None:
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"initial_release={'true' if result['mode'] == 'initial' else 'false'}\n")
            handle.write(f"expected_release_tag={result['expected_release_tag'] or ''}\n")
    except OSError as exc:
        raise InputError(f"cannot write GitHub output: {path}: {exc}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare the runner-local semantic-release baseline for v0.1.0.")
    parser.add_argument("--repo-root", default=pathlib.Path("."), type=pathlib.Path)
    parser.add_argument("--product-sha", required=True)
    parser.add_argument("--github-output", type=pathlib.Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = prepare_initial_release(repo_root=args.repo_root, product_sha=args.product_sha)
        if args.github_output is not None:
            write_github_output(args.github_output, result)
    except InputError as exc:
        result = {"ok": False, "errors": [str(exc)], "mode": "invalid"}
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
