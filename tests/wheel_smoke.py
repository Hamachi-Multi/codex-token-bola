#!/usr/bin/env python3
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import zipfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
PROBE = r'''
import importlib.resources
import pathlib

import codex_token_bola
import codex_token_bola.hook
import codex_token_bola._runtime.serve_dashboard

root = importlib.resources.files("codex_token_bola._runtime")
required = (
    "assets/dashboard.html",
    "assets/dashboard.css",
    "assets/dashboard.js",
    "assets/bola-icon.png",
    "assets/dashboard/app.js",
    "assets/dashboard/app-shell.js",
    "assets/dashboard/turns-controller.js",
    "assets/dashboard/components/dialog.js",
    "assets/dashboard/components/pager.js",
    "assets/fonts/PretendardVariable.woff2",
    "assets/fonts/JetBrainsMonoVariable.woff2",
)
missing = [name for name in required if not root.joinpath(name).is_file()]
if missing:
    raise SystemExit(f"missing installed resources: {missing}")
module_path = pathlib.Path(codex_token_bola.__file__).resolve()
if not module_path.is_relative_to(pathlib.Path(__import__("sys").prefix).resolve()):
    raise SystemExit(f"package was not imported from smoke venv: {module_path}")
'''


def run(*args: str, cwd: pathlib.Path, env: dict[str, str]) -> None:
    subprocess.run(args, cwd=cwd, env=env, check=True)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="bola-wheel-smoke-") as temporary:
        root = pathlib.Path(temporary)
        source = root / "codex-token-bola-0.0.0"
        dist = root / "dist"
        venv = root / "venv"
        work = root / "work"
        dist.mkdir()
        work.mkdir()
        source.mkdir()
        for directory in ("codex_token_bola", "scripts", "LICENSES"):
            shutil.copytree(
                ROOT / directory,
                source / directory,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        for filename in ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "pyproject.toml"):
            shutil.copy2(ROOT / filename, source / filename)
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        run(sys.executable, "-m", "build", "--wheel", "--outdir", str(dist), str(source), cwd=work, env=env)
        wheels = list(dist.glob("*.whl"))
        if len(wheels) != 1:
            raise SystemExit(f"expected one wheel, found {len(wheels)}")
        with zipfile.ZipFile(wheels[0]) as archive:
            names = archive.namelist()
        unexpected = [name for name in names if "__pycache__" in name or name.endswith((".pyc", ".pyo"))]
        if unexpected:
            raise SystemExit(f"wheel contains generated Python artifacts: {unexpected}")
        run(sys.executable, "-m", "venv", str(venv), cwd=work, env=env)
        python = venv / "bin" / "python"
        bola = venv / "bin" / "bola"
        run(str(python), "-m", "pip", "install", str(wheels[0]), cwd=work, env=env)
        run(str(bola), "--help", cwd=work, env=env)
        run(str(python), "-c", PROBE, cwd=work, env=env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
