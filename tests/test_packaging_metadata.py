from __future__ import annotations

import hashlib
import importlib.metadata
import os
import pathlib
import subprocess
import sys
import tempfile
from unittest import mock

try:
    from tests.support import ROOT, unittest
except ModuleNotFoundError:
    from support import ROOT, unittest


class PackagingMetadataTests(unittest.TestCase):
    def fake_package_env(self, root: pathlib.Path) -> dict[str, str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(root)
        return env

    def test_pyproject_declares_installable_bola_package(self) -> None:
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertIn('license = "MIT"', text)
        self.assertIn('"LICENSES/*.txt"', text)
        self.assertIn('"THIRD_PARTY_NOTICES.md"', text)
        self.assertIn("[tool.setuptools]", text)
        self.assertIn('"codex_token_bola._runtime"', text)
        self.assertIn('bola = "codex_token_bola.cli:main"', text)
        for requirement in (
            '"setuptools==84.0.0"',
            '"setuptools-scm==9.2.2"',
            '"platformdirs==4.11.4"',
            '"tzdata==2026.3"',
            '"build==1.5.0"',
            '"ruff==0.16.5"',
            '"playwright==1.62.0"',
        ):
            self.assertIn(requirement, text)
        self.assertIn('dynamic = ["version"]', text)
        self.assertNotIn('version = "0.1.0"', text)
        self.assertIn('parentdir_prefix_version = "codex-token-bola-"', text)

    def test_package_version_uses_distribution_metadata(self) -> None:
        source = (ROOT / "codex_token_bola" / "__init__.py").read_text(encoding="utf-8")

        with mock.patch("importlib.metadata.version", return_value="1.2.3") as metadata_version:
            namespace: dict[str, object] = {"__name__": "installed_version_test"}
            exec(compile(source, "codex_token_bola/__init__.py", "exec"), namespace)

        self.assertEqual(namespace["__version__"], "1.2.3")
        metadata_version.assert_called_once_with("codex-token-bola")

    def test_package_version_has_safe_source_checkout_fallback(self) -> None:
        source = (ROOT / "codex_token_bola" / "__init__.py").read_text(encoding="utf-8")

        with mock.patch("importlib.metadata.version", side_effect=importlib.metadata.PackageNotFoundError):
            namespace: dict[str, object] = {"__name__": "version_fallback_test"}
            exec(compile(source, "codex_token_bola/__init__.py", "exec"), namespace)

        self.assertEqual(namespace["__version__"], "0+unknown")

    def test_bundled_fonts_have_distribution_notices(self) -> None:
        notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")

        fonts = {
            "PretendardVariable.woff2": "9599f12fd42fc0bce1cd50b47a0c022e108d7aa64dd0d1bb0ed44f3282d900b4",
            "JetBrainsMonoVariable.woff2": "18be452724bfdc236c074ca94a249a7f41a86752c7d04ab258ce9ed5651f6a7e",
        }
        for filename, expected_digest in fonts.items():
            with self.subTest(filename=filename):
                digest = hashlib.sha256((ROOT / "scripts" / "assets" / "fonts" / filename).read_bytes()).hexdigest()
                self.assertEqual(digest, expected_digest)
                self.assertIn(expected_digest, notices)

        licenses = {
            "Pretendard-OFL-1.1.txt": "Copyright (c) 2021, Kil Hyung-jin",
            "JetBrainsMono-OFL-1.1.txt": "Copyright 2020 The JetBrains Mono Project Authors",
        }
        for filename, copyright_notice in licenses.items():
            with self.subTest(filename=filename):
                text = (ROOT / "LICENSES" / filename).read_text(encoding="utf-8")
                self.assertIn(copyright_notice, text)
                self.assertIn("SIL OPEN FONT LICENSE Version 1.1", text)

    def test_module_entrypoint_matches_runtime_cli_help(self) -> None:
        module = subprocess.run(
            [sys.executable, "-m", "codex_token_bola", "--help"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        runtime = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "bola.py"), "--help"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertEqual(module.stdout, runtime.stdout)

    def test_module_entrypoint_matches_runtime_cli_version(self) -> None:
        module = subprocess.run(
            [sys.executable, "-m", "codex_token_bola", "--version"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        runtime = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "bola.py"), "--version"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertEqual(module.stdout, runtime.stdout)
        self.assertRegex(module.stdout, r"^bola \S+\n$")

    def test_source_facades_fall_back_only_when_bundled_runtime_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            package = root / "codex_token_bola"
            scripts = root / "scripts"
            package.mkdir()
            scripts.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")
            (scripts / "__init__.py").write_text("", encoding="utf-8")
            for facade, runtime in (("cli", "bola"), ("hook", "hook")):
                (package / f"{facade}.py").write_text(
                    (ROOT / "codex_token_bola" / f"{facade}.py").read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
                (scripts / f"{runtime}.py").write_text(
                    f"def main():\n    return {facade!r}\n",
                    encoding="utf-8",
                )

            for facade in ("cli", "hook"):
                with self.subTest(facade=facade):
                    result = subprocess.run(
                        [sys.executable, "-c", f"from codex_token_bola.{facade} import main; print(main())"],
                        cwd=root,
                        env=self.fake_package_env(root),
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(result.stdout, f"{facade}\n")

    def test_source_facades_preserve_bundled_runtime_dependency_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            package = root / "codex_token_bola"
            runtime_package = package / "_runtime"
            runtime_package.mkdir(parents=True)
            (package / "__init__.py").write_text("", encoding="utf-8")
            (runtime_package / "__init__.py").write_text("", encoding="utf-8")
            for facade, runtime in (("cli", "bola"), ("hook", "hook")):
                (package / f"{facade}.py").write_text(
                    (ROOT / "codex_token_bola" / f"{facade}.py").read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
                (runtime_package / f"{runtime}.py").write_text(
                    "import bola_missing_runtime_dependency\n",
                    encoding="utf-8",
                )

            for facade in ("cli", "hook"):
                with self.subTest(facade=facade):
                    result = subprocess.run(
                        [sys.executable, "-c", f"import codex_token_bola.{facade}"],
                        cwd=root,
                        env=self.fake_package_env(root),
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("bola_missing_runtime_dependency", result.stderr)
                    self.assertNotIn("No module named 'scripts'", result.stderr)


if __name__ == "__main__":
    unittest.main()
