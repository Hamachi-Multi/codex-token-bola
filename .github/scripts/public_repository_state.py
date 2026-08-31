#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
from typing import Any


PUBLIC_OPS_PREFIX = "chore(public-ops): "
MANAGED_PATHS_FILE = pathlib.PurePosixPath(".github/public-ops-managed-paths.json")
REQUIRED_OPS_ONLY_PATHS = {
    ".github/public-ops-managed-paths.json",
    ".github/scripts/public_repository_state.py",
    ".github/scripts/public_initial_release_cleanup.mjs",
    ".github/workflows/release.yml",
    ".releaserc.json",
    "package-lock.json",
    "package.json",
}


class InputError(Exception):
    pass


def read_json(path: pathlib.Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise InputError(f"cannot read {label}: {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise InputError(f"invalid {label} json: {path}: {exc}") from exc


def run_git(repo_root: pathlib.Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo_root, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise InputError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def first_line(message: str) -> str:
    return message.splitlines()[0].strip() if message.splitlines() else ""


def managed_paths(repo_root: pathlib.Path) -> set[str]:
    payload = read_json(repo_root / MANAGED_PATHS_FILE, "public ops managed paths")
    if not isinstance(payload, list) or any(not isinstance(value, str) or not value for value in payload):
        raise InputError("public ops managed paths must be a list of non-empty strings")
    if payload != sorted(set(payload)):
        raise InputError("public ops managed paths must be sorted and unique")
    return set(payload)


def classify_repository_state(*, repo_root: pathlib.Path | str, ref: str, message: str) -> dict[str, Any]:
    repo_root = pathlib.Path(repo_root)
    if not repo_root.is_dir():
        raise InputError(f"repo root is not a directory: {repo_root}")
    if (repo_root / "pyproject.toml").is_file():
        return {"ok": True, "errors": [], "repository_state": "product_present"}

    errors: list[str] = []
    subject = first_line(message)
    if ref != "refs/heads/main" and not ref.startswith("refs/heads/public-ops/"):
        errors.append("an ops-only repository is allowed only on main or public-ops branches")
    if not subject.startswith(PUBLIC_OPS_PREFIX):
        errors.append("an ops-only repository requires a chore(public-ops): commit subject")

    allowed = managed_paths(repo_root)
    tracked = set(run_git(repo_root, "ls-files").splitlines())
    missing = sorted(REQUIRED_OPS_ONLY_PATHS - tracked)
    unexpected = sorted(tracked - allowed)
    if missing:
        errors.append("ops-only repository is missing required paths: " + ", ".join(missing))
    if unexpected:
        errors.append("ops-only repository contains unmanaged paths: " + ", ".join(unexpected))
    return {
        "ok": not errors,
        "errors": errors,
        "repository_state": "ops_only" if not errors else "invalid",
    }


def read_message(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InputError(f"cannot read commit message: {path}: {exc}") from exc


def write_github_output(path: pathlib.Path, result: dict[str, Any]) -> None:
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"repository_state={result['repository_state']}\n")
    except OSError as exc:
        raise InputError(f"cannot write GitHub output: {path}: {exc}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Classify a public checkout as ops-only or product-present.")
    parser.add_argument("--repo-root", default=pathlib.Path("."), type=pathlib.Path)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--message-file", required=True, type=pathlib.Path)
    parser.add_argument("--github-output", type=pathlib.Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = classify_repository_state(
            repo_root=args.repo_root,
            ref=args.ref,
            message=read_message(args.message_file),
        )
        if args.github_output is not None and result["ok"]:
            write_github_output(args.github_output, result)
    except InputError as exc:
        result = {"ok": False, "errors": [str(exc)], "repository_state": "invalid"}
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
