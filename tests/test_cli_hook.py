from __future__ import annotations

import shlex

try:
    from tests.support import ROOT, argparse, io, json, load_module, mock, pathlib, stat, subprocess, tempfile
except ModuleNotFoundError:
    from support import ROOT, argparse, io, json, load_module, mock, pathlib, stat, subprocess, tempfile

try:
    from tests.cli_test_support import CliTestCase
except ModuleNotFoundError:
    from cli_test_support import CliTestCase


class CliHookTests(CliTestCase):
    def test_codex_dir_status_requires_existing_initialized_writable_directory(self) -> None:
        cli = load_module("codex_dir_status_contract_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            missing = cli.codex_dir_status(root / "missing")
            regular_file = root / "regular-file"
            regular_file.write_text("not a directory\n", encoding="utf-8")
            wrong_type = cli.codex_dir_status(regular_file)
            empty = root / "empty"
            empty.mkdir()
            uninitialized = cli.codex_dir_status(empty)
            initialized = self.initialize_codex_dir(root / "initialized")
            valid = cli.codex_dir_status(initialized)
            with mock.patch.object(cli.os, "access", return_value=False):
                unwritable = cli.codex_dir_status(initialized)

        self.assertEqual(missing["reason"], "not_found")
        self.assertEqual(wrong_type["reason"], "not_directory")
        self.assertEqual(uninitialized["reason"], "not_initialized")
        self.assertTrue(valid["valid"])
        self.assertEqual(valid["markers"], ["config.toml"])
        self.assertEqual(unwritable["reason"], "not_writable")

    def test_codex_dir_status_accepts_supported_markers_and_dir_symlink(self) -> None:
        cli = load_module("codex_dir_marker_contract_test", ROOT / "scripts" / "bola.py")
        markers = ("config.toml", "auth.json", "state_5.sqlite", "history.jsonl", "sessions")
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            for marker in markers:
                with self.subTest(marker=marker):
                    codex_dir = root / marker.replace(".", "-")
                    codex_dir.mkdir()
                    target = codex_dir / marker
                    if marker == "sessions":
                        target.mkdir()
                    else:
                        target.write_text("{}\n", encoding="utf-8")
                    status = cli.codex_dir_status(codex_dir)
                    self.assertTrue(status["valid"])
                    self.assertEqual(status["markers"], [marker])

            symlink = root / "linked-dir"
            symlink.symlink_to(root / "config-toml", target_is_directory=True)
            self.assertTrue(cli.codex_dir_status(symlink)["valid"])

    def test_codex_dir_status_rejects_invalid_hooks_json(self) -> None:
        cli = load_module("codex_dir_hooks_contract_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            codex_dir = self.initialize_codex_dir(pathlib.Path(tmp_dir) / ".codex")
            hooks_path = codex_dir / "hooks.json"
            hooks_path.write_text("{broken", encoding="utf-8")
            malformed = cli.codex_dir_status(codex_dir)
            hooks_path.write_text("[]\n", encoding="utf-8")
            wrong_shape = cli.codex_dir_status(codex_dir)

        self.assertEqual(malformed["reason"], "hooks_json_invalid")
        self.assertEqual(wrong_shape["reason"], "hooks_json_invalid")

    def test_codex_cli_status_reports_missing_timeout_and_execution_failure(self) -> None:
        cli = load_module("codex_cli_status_contract_test", ROOT / "scripts" / "bola.py")
        with mock.patch.object(cli.shutil, "which", return_value=None):
            missing = cli.codex_cli_status()
        with (
            mock.patch.object(cli.shutil, "which", return_value="/usr/bin/codex"),
            mock.patch.object(cli.subprocess, "run", side_effect=subprocess.TimeoutExpired("codex", 5)),
        ):
            timeout = cli.codex_cli_status()
        failed_result = subprocess.CompletedProcess(["codex", "--version"], 1, stdout="", stderr="broken\n")
        with (
            mock.patch.object(cli.shutil, "which", return_value="/usr/bin/codex"),
            mock.patch.object(cli.subprocess, "run", return_value=failed_result),
        ):
            failed = cli.codex_cli_status()

        self.assertEqual(missing["reason"], "not_found")
        self.assertEqual(timeout["reason"], "timeout")
        self.assertEqual(failed["reason"], "execution_failed")

    def test_install_hook_rejects_invalid_home_before_any_mutation(self) -> None:
        cli = load_module("install_hook_invalid_home_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            codex_dir = root / "missing-codex-dir"
            output_dir = root / "output"
            config_home = root / "config"
            captured = io.StringIO()
            with (
                mock.patch.dict(
                    cli.os.environ,
                    {"XDG_CONFIG_HOME": str(config_home), "BOLA_OUTPUT_DIR": str(output_dir)},
                    clear=True,
                ),
                mock.patch.object(cli.sys, "argv", ["bola.py", "install-hook", "--codex-dir", str(codex_dir), "--output-dir", str(output_dir)]),
                mock.patch.object(cli.sys, "stdout", captured),
            ):
                code = cli.main()

            payload = json.loads(captured.getvalue())
            self.assertEqual(code, 2)
            self.assertEqual(payload["error"], "codex_dir_invalid")
            self.assertEqual(payload["reason"], "not_found")
            self.assertFalse(codex_dir.exists())
            self.assertFalse(output_dir.exists())
            self.assertFalse((config_home / "bola" / "runtime.conf").exists())

    def test_install_hook_rejects_missing_codex_cli_before_any_mutation(self) -> None:
        cli = load_module("install_hook_invalid_cli_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            codex_dir = self.initialize_codex_dir(root / ".codex")
            config_home = root / "config"
            captured = io.StringIO()
            with (
                mock.patch.dict(cli.os.environ, {"XDG_CONFIG_HOME": str(config_home)}, clear=True),
                mock.patch.object(cli.shutil, "which", return_value=None),
                mock.patch.object(cli.sys, "argv", ["bola.py", "install-hook", "--codex-dir", str(codex_dir)]),
                mock.patch.object(cli.sys, "stdout", captured),
            ):
                code = cli.main()

            payload = json.loads(captured.getvalue())
            self.assertEqual(code, 2)
            self.assertEqual(payload["error"], "codex_cli_invalid")
            self.assertEqual(payload["reason"], "not_found")
            self.assertFalse((codex_dir / "hooks.json").exists())
            self.assertFalse((config_home / "bola" / "runtime.conf").exists())

    def test_hook_runtime_status_checks_import_outside_checkout_without_pythonpath(self) -> None:
        cli = load_module("hook_runtime_status_test", ROOT / "scripts" / "bola.py")
        completed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="not importable")
        with (
            mock.patch.dict(cli.os.environ, {"PYTHONPATH": str(ROOT)}, clear=False),
            mock.patch.object(cli.subprocess, "run", return_value=completed) as run,
        ):
            status = cli.hook_runtime_status()

        call = run.call_args
        self.assertEqual(call.args[0], [cli.os.path.abspath(cli.os.path.expanduser(cli.sys.executable)), "-c", "import codex_token_bola.hook"])
        self.assertNotIn("PYTHONPATH", call.kwargs["env"])
        self.assertNotEqual(pathlib.Path(call.kwargs["cwd"]).resolve(), ROOT.resolve())
        self.assertFalse(status["valid"])
        self.assertEqual(status["reason"], "module_not_importable")

    def test_install_hook_rejects_unimportable_runtime_before_any_mutation(self) -> None:
        cli = load_module("install_hook_invalid_runtime_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            codex_dir = self.initialize_codex_dir(root / ".codex")
            config_home = root / "config"
            captured = io.StringIO()
            invalid_runtime = {
                "valid": False,
                "interpreter": cli.sys.executable,
                "module": "codex_token_bola.hook",
                "reason": "module_not_importable",
                "message": "install the package first",
            }
            with (
                mock.patch.dict(cli.os.environ, {"XDG_CONFIG_HOME": str(config_home)}, clear=True),
                mock.patch.object(cli, "codex_cli_status", return_value=self.valid_codex_cli_status()),
                mock.patch.object(cli, "hook_runtime_status", return_value=invalid_runtime),
                mock.patch.object(cli.sys, "argv", ["bola.py", "install-hook", "--codex-dir", str(codex_dir)]),
                mock.patch.object(cli.sys, "stdout", captured),
            ):
                code = cli.main()

            payload = json.loads(captured.getvalue())
            self.assertEqual(code, 2)
            self.assertEqual(payload["error"], "hook_runtime_invalid")
            self.assertEqual(payload["reason"], "module_not_importable")
            self.assertFalse((codex_dir / "hooks.json").exists())
            self.assertFalse((config_home / "bola" / "runtime.conf").exists())

    def test_paths_set_rejects_uninitialized_codex_dir_without_writing_config(self) -> None:
        cli = load_module("paths_set_invalid_codex_dir_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            codex_dir = root / ".codex"
            codex_dir.mkdir()
            source = root / "source"
            target = root / "target"
            (source / "raw").mkdir(parents=True)
            (source / "raw" / "event.jsonl").write_text("{}\n", encoding="utf-8")
            config_home = root / "config"
            captured = io.StringIO()
            with (
                mock.patch.dict(cli.os.environ, {"XDG_CONFIG_HOME": str(config_home)}, clear=True),
                mock.patch.object(
                    cli.sys,
                    "argv",
                    [
                        "bola.py",
                        "paths",
                        "set",
                        "--codex-dir",
                        str(codex_dir),
                        "--output-dir",
                        str(target),
                    ],
                ),
                mock.patch.object(cli.sys, "stdout", captured),
            ):
                cli.service_paths.write_config({"output_dir": source})
                code = cli.main()
                configured = cli.service_paths.read_config()

            payload = json.loads(captured.getvalue())
            self.assertEqual(code, 2)
            self.assertEqual(payload["error"], "codex_dir_invalid")
            self.assertEqual(payload["reason"], "not_initialized")
            self.assertEqual(pathlib.Path(str(configured["output_dir"])), source)
            self.assertFalse(target.exists())

    def test_install_hook_accepts_missing_output_dir_and_persists_it(self) -> None:
        cli = load_module("install_hook_output_dir_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            codex_dir = self.initialize_codex_dir(root / ".codex")
            output_dir = root / "not-created-yet"
            config_home = root / "config"
            captured = io.StringIO()
            with (
                mock.patch.dict(
                    cli.os.environ,
                    {"XDG_CONFIG_HOME": str(config_home), "BOLA_OUTPUT_DIR": str(output_dir)},
                    clear=True,
                ),
                mock.patch.object(cli, "codex_cli_status", return_value=self.valid_codex_cli_status()),
                mock.patch.object(cli, "hook_runtime_status", return_value=self.valid_hook_runtime_status()),
                mock.patch.object(cli.sys, "argv", ["bola.py", "install-hook", "--codex-dir", str(codex_dir), "--output-dir", str(output_dir)]),
                mock.patch.object(cli.sys, "stdout", captured),
            ):
                code = cli.main()

            configured = cli.service_paths.read_config(config_home / "bola" / "runtime.conf")
            self.assertEqual(code, 0)
            self.assertEqual(configured["codex_dir"], str(codex_dir))
            self.assertEqual(configured["output_dir"], str(output_dir))
            self.assertFalse(output_dir.exists())
            self.assertTrue((codex_dir / "hooks.json").exists())

    def test_install_hook_restores_hooks_when_runtime_config_write_fails(self) -> None:
        cli = load_module("install_hook_config_rollback_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            codex_dir = self.initialize_codex_dir(root / ".codex")
            output_dir = root / "output"
            config_home = root / "config"
            hooks_path = codex_dir / "hooks.json"
            original = '{"hooks":{"Notification":[{"command":"keep"}]}}\n'
            hooks_path.write_text(original, encoding="utf-8")
            captured = io.StringIO()
            with (
                mock.patch.dict(
                    cli.os.environ,
                    {
                        "XDG_CONFIG_HOME": str(config_home),
                        "CODEX_HOME": str(codex_dir),
                        "BOLA_OUTPUT_DIR": str(output_dir),
                    },
                    clear=True,
                ),
                mock.patch.object(cli, "codex_cli_status", return_value=self.valid_codex_cli_status()),
                mock.patch.object(cli, "hook_runtime_status", return_value=self.valid_hook_runtime_status()),
                mock.patch.object(
                    cli.service_paths,
                    "write_config",
                    side_effect=cli.service_paths.ConfigurationError("simulated config write failure"),
                ),
                mock.patch.object(cli.sys, "argv", ["bola.py", "install-hook"]),
                mock.patch.object(cli.sys, "stdout", captured),
            ):
                code = cli.main()
            final_hooks = hooks_path.read_text(encoding="utf-8")
            config_exists = (config_home / "bola" / "runtime.conf").exists()

        self.assertEqual(code, 2)
        self.assertEqual(final_hooks, original)
        self.assertFalse(config_exists)

    def test_install_hook_registers_repo_hook_and_keeps_hooks_json_owner_only(self) -> None:
        cli = load_module("install_hook_cli_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            codex_dir = self.initialize_codex_dir(pathlib.Path(tmp_dir) / ".codex")
            with (
                mock.patch.object(cli, "codex_cli_status", return_value=self.valid_codex_cli_status()),
                mock.patch.object(cli, "hook_runtime_status", return_value=self.valid_hook_runtime_status()),
            ):
                result = cli.install_hook(argparse.Namespace(codex_dir=str(codex_dir)))
            hooks_json = json.loads((codex_dir / "hooks.json").read_text(encoding="utf-8"))
            status = cli.hooks_json_status(codex_dir)

            self.assertEqual(result["installed_hook"], "codex_token_bola.hook")
            self.assertEqual(shlex.split(result["command"]), [cli.sys.executable, "-m", "codex_token_bola.hook", "--bola-hook"])
            self.assertEqual(stat.S_IMODE((codex_dir / "hooks.json").stat().st_mode), 0o600)
            self.assertIn("hooks_json", result)
            self.assertTrue(status["events"]["UserPromptSubmit"]["registered"])
            self.assertTrue(status["events"]["Stop"]["registered"])
            self.assertIn("hooks", hooks_json)

    def test_install_hook_preserves_existing_hooks_and_deduplicates_registration(self) -> None:
        cli = load_module("install_hook_merge_hooks_json_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            codex_dir = self.initialize_codex_dir(pathlib.Path(tmp_dir) / ".codex")
            hooks_path = codex_dir / "hooks.json"
            hooks_path.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "python3 /tmp/existing.py"}]}],
                            "Stop": [{"hooks": [{"type": "command", "command": "python3 /tmp/existing-stop.py"}]}],
                        }
                    }
                ),
                encoding="utf-8",
            )

            with (
                mock.patch.object(cli, "codex_cli_status", return_value=self.valid_codex_cli_status()),
                mock.patch.object(cli, "hook_runtime_status", return_value=self.valid_hook_runtime_status()),
            ):
                first = cli.install_hook(argparse.Namespace(codex_dir=str(codex_dir)))
                second = cli.install_hook(argparse.Namespace(codex_dir=str(codex_dir)))
            parsed = json.loads(hooks_path.read_text(encoding="utf-8"))

        self.assertTrue(first["hooks_json"]["updated"])
        self.assertFalse(second["hooks_json"]["updated"])
        user_commands = [
            nested["command"]
            for entry in parsed["hooks"]["UserPromptSubmit"]
            for nested in entry.get("hooks", [])
            if isinstance(nested, dict) and nested.get("command")
        ]
        stop_commands = [
            nested["command"] for entry in parsed["hooks"]["Stop"] for nested in entry.get("hooks", []) if isinstance(nested, dict) and nested.get("command")
        ]
        self.assertIn("python3 /tmp/existing.py", user_commands)
        self.assertIn("python3 /tmp/existing-stop.py", stop_commands)
        self.assertEqual(sum("codex_token_bola.hook" in command for command in user_commands), 1)
        self.assertEqual(sum("codex_token_bola.hook" in command for command in stop_commands), 1)

    def test_install_hook_replaces_stale_owned_checkout_registration(self) -> None:
        cli = load_module("install_hook_stale_checkout_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            codex_dir = self.initialize_codex_dir(pathlib.Path(tmp_dir) / ".codex")
            hooks_path = codex_dir / "hooks.json"
            stale = "python3 /old/checkout/hooks/token-usage.py --codex-token-bola-hook"
            unrelated = "python3 /tmp/unrelated.py"
            hooks_path.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "Stop": [
                                {"hooks": [{"type": "command", "command": stale}]},
                                {"hooks": [{"type": "command", "command": unrelated}]},
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )

            with (
                mock.patch.object(cli, "codex_cli_status", return_value=self.valid_codex_cli_status()),
                mock.patch.object(cli, "hook_runtime_status", return_value=self.valid_hook_runtime_status()),
            ):
                result = cli.install_hook(argparse.Namespace(codex_dir=str(codex_dir)))
            commands = result["hooks_json"]["events"]["Stop"]["commands"]

        self.assertNotIn(stale, commands)
        self.assertIn(unrelated, commands)
        self.assertIn(cli.hook_command(), commands)

    def test_install_hook_uses_exact_interpreter_and_module(self) -> None:
        cli = load_module("install_hook_quoted_command_test", ROOT / "scripts" / "bola.py")
        command = cli.hook_command()

        self.assertEqual(shlex.split(command), [cli.sys.executable, "-m", "codex_token_bola.hook", "--bola-hook"])

    def test_install_hook_does_not_dedupe_unrelated_command_containing_hook_path(self) -> None:
        cli = load_module("install_hook_substring_dedupe_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            codex_dir = self.initialize_codex_dir(pathlib.Path(tmp_dir) / ".codex")
            installed = codex_dir / "hooks" / "token-usage.py"
            hooks_path = codex_dir / "hooks.json"
            hooks_path.write_text(
                json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": f"echo {installed}"}]}]}}),
                encoding="utf-8",
            )

            with (
                mock.patch.object(cli, "codex_cli_status", return_value=self.valid_codex_cli_status()),
                mock.patch.object(cli, "hook_runtime_status", return_value=self.valid_hook_runtime_status()),
            ):
                result = cli.install_hook(argparse.Namespace(codex_dir=str(codex_dir)))
            commands = result["hooks_json"]["events"]["Stop"]["commands"]

        self.assertTrue(result["hooks_json"]["updated"])
        self.assertIn(f"echo {installed}", commands)
        self.assertIn(cli.hook_command(), commands)


