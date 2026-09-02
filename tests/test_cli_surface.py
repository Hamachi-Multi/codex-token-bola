from __future__ import annotations

import contextlib

try:
    from tests.support import ROOT, argparse, io, json, load_module, mock, pathlib, tempfile
except ModuleNotFoundError:
    from support import ROOT, argparse, io, json, load_module, mock, pathlib, tempfile

from scripts import service_paths

try:
    from tests.cli_test_support import CliTestCase, assert_order
except ModuleNotFoundError:
    from cli_test_support import CliTestCase, assert_order


class CliSurfaceTests(CliTestCase):
    def test_retention_preview_command_emits_machine_readable_signature(self) -> None:
        cli = load_module("retention_preview_command_test", ROOT / "scripts" / "bola.py")
        paths = mock.Mock(output_dir=pathlib.Path("/tmp/bola-preview"))
        preview = {
            "preview_signature": "fresh-signature",
            "scanned_rows": 12,
            "deletable_rows": 7,
            "deletable_bytes": 900,
            "affected_files": 2,
            "files": [
                {"affected": True, "scanned_rows": 4, "deletable_rows": 4},
                {"affected": True, "scanned_rows": 8, "deletable_rows": 3},
            ],
        }
        stdout = io.StringIO()
        with (
            mock.patch.object(cli, "runtime_paths", return_value=paths),
            mock.patch.object(cli.dashboard_cleanup, "retention_preview_with_signature", return_value=preview) as preview_call,
            contextlib.redirect_stdout(stdout),
        ):
            code = cli.retention_preview_command(argparse.Namespace(codex_dir=None, output_dir=None, cutoff="2026-05-20"))

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["preview_signature"], "fresh-signature")
        self.assertEqual(payload["delete_files"], 1)
        self.assertEqual(payload["rewrite_files"], 1)
        preview_call.assert_called_once()
        self.assertFalse(preview_call.call_args.kwargs["refresh_index"])

    def test_output_migration_import_streams_segment_payload(self) -> None:
        cli = load_module("migration_segment_stream_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            source = root / "source.jsonl"
            destination = root / "destination"
            source.write_text(
                json.dumps(
                    {
                        "record_type": "turn_usage_raw",
                        "session_id": "session",
                        "turn_id": "turn",
                        "captured_at": "2026-05-01T00:00:00+00:00",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with mock.patch.object(pathlib.Path, "read_bytes", side_effect=AssertionError("full payload read must not run")):
                result = cli.import_raw_segment(source, destination)

        self.assertEqual(result["rows"], 1)
        self.assertGreater(result["bytes"], 0)

    def test_readme_uses_supported_hook_install_and_verification_commands(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("bola install-hook\nbola doctor", readme)
        self.assertIn("Register the hook with the effective paths", readme)
        self.assertIn("#### `install-hook` options", readme)
        self.assertIn("| `--codex-dir` |", readme)
        self.assertIn("| `--output-dir` |", readme)
        self.assertIn("bola install-hook \\\n  --codex-dir ~/private/codex-dir \\\n  --output-dir ~/private/codex-token-data", readme)
        self.assertNotIn('${CODEX_HOME:-$HOME/.codex}', readme)
        assert_order(
            self,
            readme,
            "python3 -m venv .venv",
            "source .venv/bin/activate",
            "python -m pip install .",
            "bola --version",
            "bola install-hook\nbola doctor",
            "#### `install-hook` options",
            "| `--codex-dir` |",
            "| `--output-dir` |",
            "### 3. Capture a Codex turn",
            "### 4. Open the dashboard",
            "bola serve\n```",
            "bola serve --host 127.0.0.1 --port 9000",
        )
        self.assertIn("![Codex Token Bola dashboard overview with sample data](./docs/assets/dashboard/overview.png)", readme)
        self.assertIn("*All screenshots use synthetic sample data.*", readme)
        self.assertIn("<summary>Internal processing details</summary>", readme)
        self.assertIn("<summary>Internal processing details</summary>\n\n<br>\n\n| Step", readme)
        assert_order(
            self,
            readme,
            "## How it works",
            "<summary>Internal processing details</summary>",
            "| 1 | Prompt start |",
            "| 2 | Turn stop |",
            "| 3 | Segment handoff |",
            "| 4 | Reconcile |",
            "| 5 | Normalize |",
            "| 6 | Build |",
            "| 7 | Query |",
            "| 8 | Rebuild |",
            "Execution constraints:",
            "## Command guide",
        )
        self.assertNotIn("python3 -m pip install .", readme)
        self.assertNotIn("CODEX_HOME=~/private/codex-dir codex", readme)
        self.assertNotIn("The default command uses `~/.codex`", readme)
        self.assertIn("#### Codex hooks", readme)
        self.assertIn("BOLA uses `UserPromptSubmit` for the turn baseline and `Stop`", readme)
        self.assertNotIn("Codex hook behavior:", readme)
        self.assertIn("> [!IMPORTANT]\n> Run `bola install-hook` again after moving the checkout or replacing its", readme)
        self.assertIn("| Option | Purpose | Environment override | Default on WSL/Linux |", readme)
        self.assertNotIn("| Requirement |", readme)
        self.assertIn("| `--codex-dir` | Codex state input and hook registration | `CODEX_HOME` | `~/.codex` |", readme)
        self.assertIn("| `--output-dir` | BOLA-generated data | `BOLA_OUTPUT_DIR` |", readme)
        self.assertEqual(readme.count("Codex state input and hook registration"), 1)
        self.assertEqual(readme.count("BOLA-generated data"), 1)
        self.assertIn("`doctor`, `quarantine list`, and `quarantine acknowledge` support `--json`", readme)
        self.assertIn("bola quarantine list --include-acknowledged", readme)
        self.assertIn("`list` exits with `1` when records need review", readme)
        self.assertNotIn("XDG_DATA_HOME", readme)
        self.assertNotIn("XDG_CONFIG_HOME", readme)
        self.assertNotIn("Hook scan and append tuning", readme)
        self.assertNotIn("BOLA_HOOK_TAIL_SCAN_BYTES", readme)
        self.assertNotIn("BOLA_HOOK_FORWARD_SCAN_BYTES", readme)
        self.assertNotIn("BOLA_HOOK_APPEND_LOCK_TIMEOUT_MS", readme)
        self.assertNotIn("BOLA-owned file permissions", readme)
        self.assertIn("## Change paths later", readme)
        self.assertNotIn("#### Change paths later", readme)
        self.assertIn("Migrate only while Codex is stopped and no BOLA data operation is running", readme)
        self.assertIn("bola paths migrate --output-dir --apply", readme)
        self.assertNotIn("\n## Runtime paths\n", readme)
        self.assertNotIn("### Path change recommendation", readme)
        self.assertNotIn("Output migration safety guarantees", readme)
        self.assertIn("## Measured storage footprint", readme)
        self.assertIn("6,318 analyzed turns", readme)
        self.assertIn("**10.26 KiB per analyzed turn**", readme)
        self.assertIn("| 100,000 | about 0.98 GiB |", readme)
        self.assertIn("observations, not a storage guarantee", readme)
        assert_order(
            self,
            readme,
            "### 2. Register and verify the hook",
            "#### Codex hooks",
            "Register the hook with the effective paths",
            "bola install-hook\nbola doctor",
            "### 3. Capture a Codex turn",
            "## Command guide",
            "## Change paths later",
            "## Privacy and capture policy",
        )
        assert_order(
            self,
            readme,
            "## Privacy and capture policy",
            "## Measured storage footprint",
            "## Operations and analytics",
        )
        self.assertNotIn("cp hooks/token-usage.py", readme)

    def test_root_help_groups_common_and_advanced_commands(self) -> None:
        cli = load_module("root_help_command_spacing_test", ROOT / "scripts" / "bola.py")
        help_text = cli.build_parser().format_help()

        self.assertIn("usage: bola [-h] [--version] COMMAND ...", help_text)
        self.assertIn("COMMAND", help_text)
        self.assertNotIn("{reconcile, normalize, compact", help_text)
        self.assertIn("Common commands:\n  install-hook", help_text)
        self.assertIn("Advanced and recovery commands:\n  quarantine", help_text)
        self.assertEqual(help_text.count("Register the BOLA hook in a Codex directory"), 1)
        self.assertNotIn("[ install-hook,", help_text)

    def test_nested_command_help_uses_required_action_metavars(self) -> None:
        cli = load_module("nested_help_action_metavar_test", ROOT / "scripts" / "bola.py")
        parser = cli.build_parser()
        command_parsers = parser._subparsers._group_actions[0].choices

        quarantine_help = command_parsers["quarantine"].format_help()
        paths_help = command_parsers["paths"].format_help()

        self.assertIn("usage: bola quarantine", quarantine_help)
        self.assertIn("ACTION ...", quarantine_help)
        self.assertIn("usage: bola paths [-h] ACTION ...", paths_help)
        self.assertNotIn("{list,acknowledge}", quarantine_help)
        self.assertNotIn("{show,set,migrate}", paths_help)
        self.assertNotIn("[ list,", quarantine_help)
        self.assertNotIn("[ show,", paths_help)

    def test_cli_serve_default_port_matches_makefile(self) -> None:
        cli = load_module("serve_default_port_test", ROOT / "scripts" / "bola.py")
        args = cli.parse_args(["serve"])
        self.assertEqual(args.port, "8766")

    def test_output_dir_is_canonical_and_data_root_is_rejected(self) -> None:
        cli = load_module("output_dir_cli_contract_test", ROOT / "scripts" / "bola.py")
        canonical = cli.parse_args(["install-hook", "--output-dir", "/tmp/output"])
        with self.assertRaises(SystemExit):
            cli.parse_args(["install-hook", "--data-root", "/tmp/legacy"])
        install = next(action for action in cli.build_parser()._actions if isinstance(action, argparse._SubParsersAction)).choices["install-hook"]
        help_text = install.format_help()

        self.assertEqual(canonical.output_dir, "/tmp/output")
        self.assertIn("--output-dir OUTPUT_DIR", help_text)
        self.assertNotIn("--data-root", help_text)

        with self.assertRaises(SystemExit):
            cli.parse_args(["install-hook", "--output-dir", "/tmp/output", "--data-root", "/tmp/legacy"])

    def test_codex_dir_is_the_public_runtime_path_option(self) -> None:
        cli = load_module("codex_dir_cli_contract_test", ROOT / "scripts" / "bola.py")
        parsed = cli.parse_args(["install-hook", "--codex-dir", "/tmp/codex"])
        install = next(action for action in cli.build_parser()._actions if isinstance(action, argparse._SubParsersAction)).choices["install-hook"]

        self.assertEqual(parsed.codex_dir, "/tmp/codex")
        self.assertIn("--codex-dir CODEX_DIR", install.format_help())

    def test_config_schema_persists_codex_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            path = root / "runtime.conf"
            service_paths.write_config({"codex_dir": root / "codex"}, path)
            payload = service_paths.read_config(path)

        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["codex_dir"], str(root / "codex"))

    def test_cli_serve_rejects_removed_allow_network_option(self) -> None:
        cli = load_module("serve_allow_network_removed_test", ROOT / "scripts" / "bola.py")
        with self.assertRaises(SystemExit):
            cli.parse_args(["serve", "--host", "0.0.0.0", "--allow-network"])

    def test_cli_serve_rejects_db_override(self) -> None:
        cli = load_module("serve_rejects_db_override_test", ROOT / "scripts" / "bola.py")
        with self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["serve", "--db", "/tmp/custom.sqlite"])

    def test_high_level_cli_commands_reject_unknown_options(self) -> None:
        cli = load_module("high_level_unknown_args_test", ROOT / "scripts" / "bola.py")
        cases = [
            ["pipeline", "--codex-dri", "/tmp/nope"],
            ["retention-prune", "--cutoff", "0", "--outptu", "/tmp/nope"],
            ["doctor", "--codex-dri", "/tmp/nope"],
            ["install-hook", "--codex-dri", "/tmp/nope"],
            ["migrate-path", "--aply"],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                with mock.patch.object(cli.sys, "argv", ["bola.py", *argv]):
                    with self.assertRaises(SystemExit) as raised:
                        cli.main()
                self.assertEqual(raised.exception.code, 2)

    def test_pipeline_help_makes_recovery_explicit(self) -> None:
        cli = load_module("pipeline_help_recovery_contract_test", ROOT / "scripts" / "bola.py")
        help_text = cli.build_parser().format_help()

        self.assertIn("pipeline", help_text)
        self.assertNotIn("Run reconcile, normalize, then build.", help_text)

    def test_retention_prune_invalid_cutoff_returns_structured_error(self) -> None:
        cli = load_module("retention_invalid_cutoff_test", ROOT / "scripts" / "bola.py")
        captured = io.StringIO()

        with mock.patch.object(cli.sys, "stdout", captured):
            code = cli.retention_prune(argparse.Namespace(codex_dir=None, output_dir=None, cutoff="not-a-date", preview_signature="sig"))

        payload = json.loads(captured.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(payload["error"], "cutoff_date_invalid")
        self.assertEqual(payload["stage"], "preview")

    def test_parse_cutoff_date_only_uses_utc_midnight(self) -> None:
        cli = load_module("retention_cutoff_date_only_utc_test", ROOT / "scripts" / "bola.py")

        self.assertEqual(cli.parse_cutoff("2026-05-20"), cli.parse_cutoff("2026-05-20T00:00:00+00:00"))

    def test_per_file_analytics_output_options_are_not_public(self) -> None:
        cli = load_module("analytics_output_option_contract_test", ROOT / "scripts" / "bola.py")
        for command in ("build", "pipeline", "retention-prune"):
            arguments = [command]
            if command == "retention-prune":
                arguments.extend(("--cutoff", "2026-05-20"))
            with self.subTest(command=command):
                with self.assertRaises(SystemExit) as raised:
                    cli.parse_args([*arguments, "--output", "/tmp/external.sqlite"])
                self.assertEqual(raised.exception.code, 2)

    def test_retention_prune_outputs_partial_mutation_envelope_last_after_normalize_failure(self) -> None:
        cli = load_module("retention_prune_partial_mutation_last_json_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            codex_dir = pathlib.Path(tmp_dir) / ".codex"
            base = codex_dir / "bola"
            base.mkdir(parents=True)
            captured = io.StringIO()
            lock_context = mock.MagicMock(__enter__=lambda _self: mock.Mock(path=base / "state" / "service.lock", fd=None), __exit__=lambda *_args: None)
            applied_job: dict[str, object] = {}

            def fake_apply(plan):
                job = cli.retention_service.RetentionJob.create_at(
                    cli.retention_service.RetentionPhase.PHYSICAL_DELETE_PENDING,
                    operation_job_id=plan["operation_job_id"],
                    cutoff_unix=1779235200.0,
                    deleted_rows=1,
                    physical_delete_pending=True,
                    derived_rebuild_required=True,
                    recovery_required=True,
                    pending_files=2,
                )
                applied_job["job"] = job
                return cli.dashboard_cleanup.RetentionApplyResult(
                    summary={"deleted_rows": 1, "scanned_rows": 1, "physical_delete_pending": True, "pending_files": 2},
                    marker_state="persisted",
                    job=job,
                )

            with (
                mock.patch.object(cli.service_lock, "acquire_service_lock", return_value=lock_context),
                mock.patch.object(cli.service_lock, "child_lock_env", return_value={}),
                mock.patch.object(cli.dashboard_cleanup, "retention_preview_signature", return_value="sig"),
                mock.patch.object(cli.dashboard_cleanup, "preflight_delete_logs_older_than", return_value=None),
                mock.patch.object(cli, "raw_segment_state_checkpoint", return_value={"checkpoint": True}),
                mock.patch.object(
                    cli.dashboard_cleanup,
                    "plan_delete_logs_older_than",
                    side_effect=lambda *_args, **kwargs: {
                        "base": str(base),
                        "cutoff": 1779235200.0,
                        "operation_job_id": kwargs["operation_job_id"],
                        "segments": {"deleted_rows": 1},
                        "untracked": [],
                    },
                ),
                mock.patch.object(cli.dashboard_cleanup, "validate_delete_logs_older_than_plan", return_value=None),
                mock.patch.object(cli.dashboard_cleanup, "reset_derived_outputs", return_value={"reset": True}),
                mock.patch.object(
                    cli.dashboard_cleanup,
                    "apply_delete_logs_older_than_plan_result",
                    side_effect=fake_apply,
                ),
                mock.patch.object(
                    cli.dashboard_cleanup,
                    "read_cleanup_retention_job_model",
                    side_effect=lambda _base: applied_job.get("job"),
                ),
                mock.patch.object(cli.dashboard_cleanup, "write_cleanup_retention_job", return_value=None),
                mock.patch.object(
                    cli,
                    "run_script_json",
                    return_value=(
                        2,
                        {"error": "normalize_pending_publish_recovery_failed", "recovery_required": True},
                        '{"error":"normalize_pending_publish_recovery_failed","recovery_required":true}\n',
                        "",
                    ),
                ),
                mock.patch.object(cli.sys, "stdout", captured),
            ):
                code = cli.retention_prune(argparse.Namespace(codex_dir=str(codex_dir), output=None, cutoff="2026-05-20", preview_signature="sig"))

        json_lines = [json.loads(line) for line in captured.getvalue().splitlines() if line.startswith("{")]
        self.assertEqual(code, 2)
        self.assertGreaterEqual(len(json_lines), 2)
        self.assertEqual(json_lines[-1]["error"], "retention_rebuild_failed")
        self.assertTrue(json_lines[-1]["partial_mutation"])
        self.assertEqual(json_lines[-1]["stage"], "normalize")
        self.assertEqual(json_lines[-1]["deleted_rows"], 1)
        self.assertTrue(json_lines[-1]["physical_delete_pending"])
        self.assertEqual(json_lines[-1]["pending_files"], 2)

    def test_pipeline_rejects_success_without_required_child_json(self) -> None:
        cli = load_module("pipeline_missing_child_json_test", ROOT / "scripts" / "bola.py")
        valid_payloads = {
            "reconcile.py": {"status": "healthy"},
            "compact_raw.py": {},
            "normalize.py": {"mode": "full", "normalized_turns_size": 0},
            "build_analytics.py": {},
        }
        for target, operation in (
            ("reconcile.py", "reconcile"),
            ("compact_raw.py", "compact"),
            ("normalize.py", "normalize"),
            ("build_analytics.py", "build"),
        ):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp_dir:
                root = pathlib.Path(tmp_dir)
                paths = mock.Mock(codex_dir=root / "codex", output_dir=root / "output")
                lock_context = mock.MagicMock(
                    __enter__=lambda _self: mock.Mock(path=root / "service.lock", fd=None),
                    __exit__=lambda *_args: None,
                )
                captured = io.StringIO()

                def fake_run_script_json(name, _extra_args, env=None):
                    del env
                    if name == target:
                        return 0, None, "", ""
                    payload = valid_payloads[name]
                    return 0, payload, json.dumps(payload), ""

                with (
                    mock.patch.object(cli, "runtime_paths", return_value=paths),
                    mock.patch.object(cli, "pipeline_output_path", return_value=paths.output_dir / "analytics" / "bola.sqlite"),
                    mock.patch.object(cli.service_lock, "acquire_service_lock", return_value=lock_context),
                    mock.patch.object(cli.service_lock, "child_lock_env", return_value={}),
                    mock.patch.object(cli.pipeline_service.dashboard_cleanup_recovery, "recover_retention_cleanup", return_value={}),
                    mock.patch.object(cli.pipeline_service.dashboard_cleanup, "complete_retention_derived_rebuild", return_value={}),
                    mock.patch.object(cli, "run_script_json", side_effect=fake_run_script_json),
                    mock.patch.object(cli.sys, "stdout", captured),
                ):
                    code = cli.pipeline(
                        argparse.Namespace(
                            codex_dir=None,
                            output_dir=None,
                            state_db=None,
                            project_root=None,
                            incremental=False,
                            recover=target == "reconcile.py",
                            skip_rotate=False,
                        )
                    )

                payloads = [json.loads(line) for line in captured.getvalue().splitlines() if line.startswith("{")]
                self.assertEqual(code, 2)
                self.assertEqual(payloads[-1]["error"], "child_output_contract_failed")
                self.assertEqual(payloads[-1]["operation"], operation)
                self.assertEqual(payloads[-1]["parse_error"], "stdout_empty")

    def test_retention_prune_keeps_recovery_marker_when_normalize_json_is_missing(self) -> None:
        cli = load_module("retention_prune_missing_child_json_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            codex_dir = pathlib.Path(tmp_dir) / ".codex"
            base = codex_dir / "bola"
            base.mkdir(parents=True)
            captured = io.StringIO()
            lock_context = mock.MagicMock(
                __enter__=lambda _self: mock.Mock(path=base / "state" / "service.lock", fd=None),
                __exit__=lambda *_args: None,
            )
            applied_job: dict[str, object] = {}
            written_jobs: list[object] = []

            def fake_apply(plan):
                job = cli.retention_service.RetentionJob.create_at(
                    cli.retention_service.RetentionPhase.PHYSICAL_DELETE_PENDING,
                    operation_job_id=plan["operation_job_id"],
                    cutoff_unix=1779235200.0,
                    deleted_rows=1,
                    physical_delete_pending=True,
                    derived_rebuild_required=True,
                    recovery_required=True,
                    pending_files=2,
                )
                applied_job["job"] = job
                return cli.dashboard_cleanup.RetentionApplyResult(
                    summary={"deleted_rows": 1, "scanned_rows": 1, "physical_delete_pending": True, "pending_files": 2},
                    marker_state="persisted",
                    job=job,
                )

            with (
                mock.patch.object(cli.service_lock, "acquire_service_lock", return_value=lock_context),
                mock.patch.object(cli.service_lock, "child_lock_env", return_value={}),
                mock.patch.object(cli.dashboard_cleanup, "retention_preview_signature", return_value="sig"),
                mock.patch.object(cli.dashboard_cleanup, "preflight_delete_logs_older_than", return_value=None),
                mock.patch.object(cli, "raw_segment_state_checkpoint", return_value={"checkpoint": True}),
                mock.patch.object(
                    cli.dashboard_cleanup,
                    "plan_delete_logs_older_than",
                    side_effect=lambda *_args, **kwargs: {
                        "base": str(base),
                        "cutoff": 1779235200.0,
                        "operation_job_id": kwargs["operation_job_id"],
                        "segments": {"deleted_rows": 1},
                        "untracked": [],
                    },
                ),
                mock.patch.object(cli.dashboard_cleanup, "validate_delete_logs_older_than_plan", return_value=None),
                mock.patch.object(cli.dashboard_cleanup, "reset_derived_outputs", return_value={"reset": True}),
                mock.patch.object(cli.dashboard_cleanup, "apply_delete_logs_older_than_plan_result", side_effect=fake_apply),
                mock.patch.object(
                    cli.dashboard_cleanup,
                    "read_cleanup_retention_job_model",
                    side_effect=lambda _base: applied_job.get("job"),
                ),
                mock.patch.object(
                    cli.dashboard_cleanup,
                    "write_cleanup_retention_job",
                    side_effect=lambda _base, job: written_jobs.append(job),
                ),
                mock.patch.object(cli, "run_script_json", return_value=(0, None, "", "")),
                mock.patch.object(cli.sys, "stdout", captured),
            ):
                code = cli.retention_prune(
                    argparse.Namespace(
                        codex_dir=str(codex_dir),
                        output_dir=None,
                        cutoff="2026-05-20",
                        preview_signature="sig",
                    )
                )

        payloads = [json.loads(line) for line in captured.getvalue().splitlines() if line.startswith("{")]
        self.assertEqual(code, 2)
        self.assertEqual(payloads[-1]["error"], "retention_rebuild_failed")
        self.assertEqual(payloads[-1]["stage"], "normalize")
        self.assertEqual(payloads[-1]["child_output_contract"]["error"], "child_output_contract_failed")
        self.assertTrue(payloads[-1]["recovery_required"])
        self.assertEqual(written_jobs[-1].phase, cli.retention_service.RetentionPhase.FAILED)
        self.assertTrue(written_jobs[-1].derived_rebuild_required)

    def test_release_check_command_is_removed(self) -> None:
        cli = load_module("release_check_removed_test", ROOT / "scripts" / "bola.py")
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

        with self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["release-check"])

        self.assertFalse((ROOT / "scripts" / "check_release.py").exists())
        self.assertNotIn("release-check", makefile)

    def test_build_cli_rejects_removed_model_call_inputs(self) -> None:
        build = load_module("build_rejects_model_call_inputs_test", ROOT / "scripts" / "build_analytics.py")
        with self.assertRaises(SystemExit):
            with mock.patch.object(build.sys, "argv", ["build_analytics.py", "--model-calls-log", "/tmp/model-calls.jsonl"]):
                build.parse_args()
        with self.assertRaises(SystemExit):
            with mock.patch.object(build.sys, "argv", ["build_analytics.py", "--raw-model-calls-log", "/tmp/raw-model-calls.jsonl"]):
                build.parse_args()
        with self.assertRaises(SystemExit):
            with mock.patch.object(build.sys, "argv", ["build_analytics.py", "--model-calls-offset", "1"]):
                build.parse_args()

    def test_dashboard_rebuild_does_not_buffer_child_output_in_pipes(self) -> None:
        for relative in ("scripts/dashboard_rebuild_api.py", "scripts/dashboard_cleanup_api.py"):
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn("stdout=subprocess.PIPE", source)
            self.assertNotIn("stderr=subprocess.PIPE", source)
            self.assertIn("tempfile.TemporaryFile", source)
            self.assertIn("dir=tmp_dir", source)

    def test_compat_facades_do_not_mutate_submodule_globals(self) -> None:
        for relative in ("scripts/raw_segments.py", "scripts/dashboard_cleanup.py"):
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn("import *", source)
            self.assertNotIn("_sync_module_bindings", source)
            self.assertNotIn("_restore_module_bindings", source)
            self.assertNotIn("setattr(", source)

        cleanup = load_module("dashboard_cleanup_exports_test", ROOT / "scripts" / "dashboard_cleanup.py")
        raw_segments = load_module("raw_segments_exports_test", ROOT / "scripts" / "raw_segments.py")
        self.assertEqual(
            set(cleanup.__all__),
            {
                "ManifestError",
                "RetentionApplyResult",
                "RetentionPreviewStale",
                "apply_delete_logs_older_than_plan",
                "apply_delete_logs_older_than_plan_result",
                "cleanup_detail_payload",
                "cleanup_payload",
                "cleanup_retention_job_path",
                "clear_cleanup_retention_job",
                "clear_retention_preview_cache",
                "complete_retention_derived_rebuild",
                "delete_all_logs",
                "delete_logs_older_than",
                "discard_delete_logs_older_than_plan",
                "ensure_service_owned_output",
                "plan_delete_logs_older_than",
                "preflight_delete_logs_older_than",
                "read_cleanup_retention_job",
                "rebuild_retention_index",
                "refresh_retention_index_for_current_sources",
                "reset_derived_outputs",
                "retention_preview",
                "retention_preview_signature",
                "retention_preview_with_signature",
                "validate_delete_logs_older_than_plan",
                "write_cleanup_retention_job",
            },
        )
        self.assertNotIn("raw_segments", cleanup.__all__)
        self.assertNotIn("time", cleanup.__all__)
        self.assertNotIn("RETENTION_PREVIEW_CACHE", cleanup.__all__)
        self.assertEqual(
            set(raw_segments.__all__),
            {
                "ApplyMarkerPhase",
                "ApplyMarkerStatus",
                "JsonlScanAccumulator",
                "ManifestError",
                "PROMPT_RAW_NAME",
                "RotationPhase",
                "SegmentApplyState",
                "acquire_raw_segment_lock",
                "append_closed_segment",
                "apply_segment_plans",
                "begin_rotate_all_current_segments_unlocked",
                "clear_apply_marker",
                "closed_segment_from_current",
                "current_pointer_path",
                "current_segment_paths",
                "discard_segment_plan_artifacts",
                "empty_current_pointer",
                "empty_manifest",
                "ensure_current_segment",
                "finish_rotate_all_current_segments",
                "fsync_dir",
                "inspect_segment_apply_state",
                "manifest_path",
                "manifest_segments",
                "manifest_signature",
                "load_pending_rotation",
                "new_current_segment",
                "open_segment_payload",
                "pending_rotation_path",
                "plan_segments_older_than",
                "preflight_segments_older_than",
                "raw_segment_lock_available",
                "raw_segment_lock_path",
                "read_apply_marker",
                "read_apply_status",
                "read_current_pointer",
                "read_manifest",
                "read_pending_rotation",
                "reconcile_apply_marker",
                "reconcile_apply_marker_unlocked",
                "reconcile_pending_rotation",
                "retention_preview_from_current",
                "retention_preview_from_manifest",
                "rotate_all_current_segments",
                "rotate_current_segment",
                "row_time",
                "segment_apply_marker_path",
                "strict_read_current_pointer",
                "strict_read_manifest",
                "sweep_apply_marker",
                "unlink_empty_closed_segment",
                "validate_current_pointer_entries",
                "validate_current_segment_entry",
                "validate_segment_path",
                "validate_segment_plans",
                "write_apply_marker",
                "write_current_pointer",
                "write_json_atomic",
                "write_manifest",
                "write_pending_rotation",
            },
        )

        retention_sources = "\n".join(
            (ROOT / relative).read_text(encoding="utf-8")
            for relative in (
                "scripts/dashboard_cleanup_retention.py",
                "scripts/dashboard_retention_index.py",
                "scripts/dashboard_retention_preview.py",
            )
        )
        for removed_name in (
            "plan_jsonl_for_retention",
            "apply_retention_plan",
            "rewrite_jsonl_for_retention",
            "write_retained_jsonl_for_retention",
        ):
            self.assertNotIn(removed_name, retention_sources)

    def test_ui_check_defaults_to_fixture_and_live_is_explicit(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        runner = (ROOT / "scripts" / "playwright_dashboard_check.py").read_text(encoding="utf-8")
        self.assertIn("ui-check:\n\t$(PYTHON) scripts/playwright_dashboard_check.py", makefile)
        self.assertIn("ui-check-live:\n\t$(PYTHON) scripts/playwright_dashboard_check.py --url http://127.0.0.1:8766", makefile)
        self.assertIn("write_dashboard_fixture", runner)
        self.assertIn("Omit to run an isolated fixture server", runner)

    def test_ui_check_fails_on_browser_runtime_errors(self) -> None:
        runner = (ROOT / "scripts" / "playwright_dashboard_check.py").read_text(encoding="utf-8")
        self.assertIn('page.on("pageerror"', runner)
        self.assertIn('page.on("console"', runner)
        self.assertIn('page.on("requestfailed"', runner)
        self.assertIn('raise RuntimeError("browser runtime errors detected', runner)

    def test_ui_check_runs_named_scenarios_in_isolated_contexts(self) -> None:
        runner = (ROOT / "scripts" / "playwright_dashboard_check.py").read_text(encoding="utf-8")
        self.assertIn('BrowserScenario("desktop-tools-subagents"', runner)
        self.assertIn('BrowserScenario("analyze-cancel"', runner)
        self.assertIn("context = browser.new_context(", runner)
        self.assertIn('parser.add_argument("--repeat"', runner)
        self.assertIn('"--scenario",', runner)

    def test_cleanup_ui_contract_reads_asset_files_not_server_bundle(self) -> None:
        for relative in (
            "tests/test_dashboard_cleanup_ui.py",
            "tests/test_dashboard_payload_queries.py",
            "tests/test_dashboard_ui_contract.py",
        ):
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("dashboard_asset_bundle", source)
            self.assertNotIn("DASHBOARD_SOURCE_BUNDLE", source)

    def test_cleanup_row_groups_are_explicit_contract(self) -> None:
        contract = load_module("dashboard_cleanup_contract_explicit_test", ROOT / "scripts" / "dashboard_cleanup_contract.py")
        definitions = contract.cleanup_row_definitions()
        labels = [row["label"] for row in definitions]
        group_ids = [row["group_id"] for row in definitions]

        self.assertEqual(len(labels), len(set(labels)))
        self.assertEqual(len(group_ids), len(set(group_ids)))
        self.assertNotIn("Raw Usage Logs", labels)
        self.assertIn("Raw Current Segments", labels)
        self.assertNotIn("Raw Model Calls", labels)
        with self.assertRaises(KeyError):
            contract.cleanup_group_for_label("Made Up Cleanup Group")

    def test_playwright_desktop_checks_are_split_by_area(self) -> None:
        desktop = (ROOT / "scripts" / "playwright_dashboard_desktop.py").read_text(encoding="utf-8")
        runner = (ROOT / "scripts" / "playwright_dashboard_check.py").read_text(encoding="utf-8")
        self.assertLessEqual(len(desktop.splitlines()), 80)
        for module_name, function_name, scenario_function in (
            ("playwright_dashboard_toolbar.py", "check_toolbar", "check_desktop_toolbar"),
            ("playwright_dashboard_turns.py", "check_turns_and_selected_turn", "check_desktop_turns"),
            ("playwright_dashboard_cleanup.py", "check_cleanup_desktop", "check_desktop_cleanup"),
            ("playwright_dashboard_tools.py", "check_tools_and_subagents", "check_desktop_tools"),
        ):
            source = (ROOT / "scripts" / module_name).read_text(encoding="utf-8")
            self.assertIn(f"def {function_name}", source)
            self.assertIn(function_name, desktop)
            self.assertIn(f"def {scenario_function}", desktop)
            self.assertIn(scenario_function, runner)

        cleanup_source = (ROOT / "scripts" / "playwright_dashboard_cleanup.py").read_text(encoding="utf-8")
        for function_name in (
            "check_cleanup_table_contract",
            "check_cleanup_selection_state",
            "check_cleanup_all_preset",
            "check_cleanup_retention_preset",
            "check_cleanup_detail_modal",
            "check_cleanup_refresh_stability",
        ):
            self.assertIn(f"def {function_name}", cleanup_source)
