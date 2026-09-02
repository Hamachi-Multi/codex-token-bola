from __future__ import annotations

try:
    from tests.support import ROOT, argparse, io, json, load_module, mock, os, pathlib, tempfile
except ModuleNotFoundError:
    from support import ROOT, argparse, io, json, load_module, mock, os, pathlib, tempfile

try:
    from tests.cli_test_support import CliTestCase
except ModuleNotFoundError:
    from cli_test_support import CliTestCase


class CliDoctorTests(CliTestCase):
    def test_doctor_reports_current_segments_and_hook_registration(self) -> None:
        cli = load_module("doctor_runtime_current_segments_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            codex_dir = pathlib.Path(tmp_dir) / ".codex"
            base = codex_dir / "bola"
            raw_segments = cli.raw_segments
            current = raw_segments.ensure_current_segment(base, kind="prompt_usage", source_name="prompt-usage.raw.jsonl")
            pathlib.Path(current["path"]).write_text("{}\n", encoding="utf-8")
            self.initialize_codex_dir(codex_dir)
            (codex_dir / "hooks.json").write_text(
                json.dumps(
                    {
                        "hooks": {
                            "UserPromptSubmit": [{"hooks": [{"type": "command", "command": cli.hook_command()}]}],
                            "Stop": [{"hooks": [{"type": "command", "command": cli.hook_command()}]}],
                        }
                    }
                ),
                encoding="utf-8",
            )
            before_files = {str(path.relative_to(codex_dir)): (path.read_bytes(), path.stat().st_mtime_ns) for path in codex_dir.rglob("*") if path.is_file()}
            captured = io.StringIO()
            with (
                mock.patch.dict(cli.os.environ, {"XDG_CONFIG_HOME": str(pathlib.Path(tmp_dir) / "config")}, clear=False),
                mock.patch.object(cli, "codex_cli_status", return_value=self.valid_codex_cli_status()),
                mock.patch.object(cli.sys, "stdout", captured),
            ):
                cli.service_paths.write_config({"codex_dir": codex_dir, "output_dir": base})
                code = cli.doctor(argparse.Namespace(codex_dir=str(codex_dir), output_dir=str(base), json_output=True))
            after_files = {str(path.relative_to(codex_dir)): (path.read_bytes(), path.stat().st_mtime_ns) for path in codex_dir.rglob("*") if path.is_file()}

        report = json.loads(captured.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(report["health"], {"status": "healthy", "exit_code": 0, "issues": []})
        self.assertEqual(report["runtime"]["current_segments"]["prompt_usage"]["rows"], 1)
        self.assertTrue(report["runtime"]["hooks_json"]["events"]["UserPromptSubmit"]["registered"])
        self.assertTrue(report["runtime"]["hooks_json"]["events"]["Stop"]["registered"])
        self.assertEqual(before_files, after_files)

    def test_doctor_defaults_to_human_summary_with_actions(self) -> None:
        cli = load_module("doctor_human_output_test", ROOT / "scripts" / "bola.py")
        report = {
            "codex_dir": {"path": "/tmp/codex", "valid": True},
            "codex_cli": {"valid": True, "version": "codex-cli 1.0.0"},
            "output_dir": {"path": "/tmp/output", "exists": True},
            "analytics_db": {"path": "/tmp/output/analytics/bola.sqlite", "exists": True, "bytes": 1024},
            "runtime": {
                "hooks_json": {
                    "events": {
                        "UserPromptSubmit": {"registered": True},
                        "Stop": {"registered": True},
                    }
                },
                "recovery": {
                    "last_error": {"code": "error:raw_append_failed", "age_seconds": 3600},
                },
            },
            "health": {
                "status": "degraded",
                "exit_code": 1,
                "issues": [
                    {
                        "code": "recent_hook_errors",
                        "severity": "degraded",
                        "count": 2,
                        "errors": {"error:raw_append_failed": 2},
                    },
                    {
                        "code": "unacknowledged_quarantine",
                        "severity": "degraded",
                        "count": 1,
                        "occurrences": 3,
                        "by_kind": {"invalid_json": 1},
                    },
                ],
            },
        }
        captured = io.StringIO()
        result = cli.doctor_service.DoctorResult(report=report, exit_code=1)

        with (
            mock.patch.object(cli.doctor_service, "run_doctor", return_value=result),
            mock.patch.object(cli.sys, "stdout", captured),
        ):
            code = cli.doctor(argparse.Namespace(codex_dir=None, output_dir=None, json_output=False))

        output = captured.getvalue()
        self.assertEqual(code, 1)
        self.assertIn("BOLA Doctor: DEGRADED", output)
        self.assertIn("[OK] Codex hooks: Stop, UserPromptSubmit", output)
        self.assertIn("[WARN] Recent hook writes failed", output)
        self.assertIn("Errors: raw_append_failed (2)", output)
        self.assertIn("Last occurrence: raw_append_failed, 1h ago", output)
        self.assertIn("Run: bola quarantine list", output)
        self.assertIn("Full report: bola doctor --json", output)
        self.assertNotIn('"runtime":', output)
        self.assertNotIn("\x1b", output)

    def test_doctor_json_preserves_complete_report(self) -> None:
        cli = load_module("doctor_json_output_test", ROOT / "scripts" / "bola.py")
        report = {
            "runtime": {"detail": {"nested": True}},
            "health": {"status": "healthy", "exit_code": 0, "issues": []},
        }
        captured = io.StringIO()
        result = cli.doctor_service.DoctorResult(report=report, exit_code=0)

        with (
            mock.patch.object(cli.doctor_service, "run_doctor", return_value=result),
            mock.patch.object(cli.sys, "stdout", captured),
        ):
            code = cli.doctor(argparse.Namespace(codex_dir=None, output_dir=None, json_output=True))

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(captured.getvalue()), report)

    def test_doctor_renderer_covers_all_health_issue_codes_and_unknown_fallback(self) -> None:
        cli = load_module("doctor_issue_rendering_contract_test", ROOT / "scripts" / "bola.py")
        expected = {
            "runtime_config_missing",
            "codex_dir_invalid",
            "codex_cli_invalid",
            "output_dir_not_directory",
            "output_dir_unwritable",
            "runtime_status_invalid",
            "current_segment_state_invalid",
            "hooks_config_invalid",
            "hook_registration_missing",
            "stale_hook_registration",
            "normalize_pending_publish_recovery_required",
            "pending_recovery_state",
            "recent_hook_errors",
            "stale_analytics_temp_files",
            "retention_pruned_store_invalid",
            "cleanup_retention_job_invalid",
            "retention_checkpoint_invalid",
            "service_lock_state_invalid",
            "path_transition_invalid",
            "retention_pruned_store_migration_required",
            "retention_pruned_state_recovery_ready",
            "retention_pruned_state_resolution_required",
            "retention_pruned_state_pending",
            "retention_pruned_state_orphaned",
            "stale_retention_checkpoints",
            "quarantine_state_invalid",
            "unacknowledged_quarantine",
        }
        self.assertEqual(cli.doctor_renderer.known_issue_codes(), expected)

        output = cli.doctor_renderer.render_doctor_report(
            {
                "health": {
                    "status": "failed",
                    "issues": [{"code": "future_health_signal", "severity": "failed", "count": 7}],
                }
            }
        )
        self.assertIn("Future health signal (future_health_signal)", output)
        self.assertIn("Count: 7", output)

    def test_doctor_rejects_output_path_that_is_not_a_directory(self) -> None:
        cli = load_module("doctor_output_file_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = pathlib.Path(tmp_dir) / "output"
            output.write_text("not a directory", encoding="utf-8")
            status = cli.doctor_service.output_dir_status(output)
            health = cli.doctor_service.doctor_health(
                {"codex_dir": {"valid": True}, "codex_cli": {"valid": True}, "output_dir": status, "runtime": {}}
            )

        self.assertFalse(status["is_directory"])
        self.assertEqual(health["status"], "failed")
        self.assertIn("output_dir_not_directory", [issue["code"] for issue in health["issues"]])

    def test_doctor_reports_write_probe_failure_without_leaving_probe(self) -> None:
        cli = load_module("doctor_output_write_probe_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = pathlib.Path(tmp_dir) / "output"
            output.mkdir()
            with mock.patch.object(cli.doctor_service.os, "open", side_effect=PermissionError(13, "blocked")):
                status = cli.doctor_service.output_dir_status(output)
            probes = list(output.glob(".bola-doctor-write-probe-*"))

        self.assertFalse(status["writable"])
        self.assertEqual(status["write_probe_error"], {"type": "PermissionError", "errno": 13})
        self.assertEqual(probes, [])

    def test_doctor_parser_exposes_explicit_json_mode(self) -> None:
        cli = load_module("doctor_json_parser_test", ROOT / "scripts" / "bola.py")

        self.assertFalse(cli.parse_args(["doctor"]).json_output)
        self.assertTrue(cli.parse_args(["doctor", "--json"]).json_output)

    def test_doctor_classifies_retention_pending_lifecycle(self) -> None:
        cli = load_module("doctor_retention_lifecycle_test", ROOT / "scripts" / "bola.py")

        def report(*, job: dict[str, object] | None, held: bool = False) -> dict[str, object]:
            return {
                "codex_dir": {"valid": True},
                "codex_cli": {"valid": True},
                "runtime": {
                    "current_segments": {},
                    "hooks_json": {},
                    "recovery": {},
                    "analytics_tmp_files": {},
                    "quarantine": {},
                    "retention_pruned_store": {
                        "valid": True,
                        "pending_rows": 2,
                        "pending_job_ids": ["retention:test"],
                        "migration_required": False,
                    },
                    "cleanup_retention_job": {"valid": True, "job": job},
                    "retention_checkpoints": {"valid": True, "count": 0},
                    "service_lock": {"valid": True, "held": held},
                    "path_transition": {"valid": True, "transition": None},
                },
            }

        pending = cli.doctor_health(report(job={"operation_job_id": "retention:test"}))
        ready = cli.doctor_health(report(job={"pruned_state_job_id": "retention:test", "pruned_state_commit_ready": True}))
        unresolved = cli.doctor_health(report(job={"phase": "failed", "pruned_state_job_id": "retention:test"}))
        orphaned = cli.doctor_health(report(job=None))

        self.assertEqual((pending["status"], pending["exit_code"]), ("degraded", 1))
        self.assertEqual(pending["issues"][0]["code"], "retention_pruned_state_pending")
        self.assertEqual((ready["status"], ready["exit_code"]), ("degraded", 1))
        self.assertEqual(ready["issues"][0]["code"], "retention_pruned_state_recovery_ready")
        self.assertEqual((unresolved["status"], unresolved["exit_code"]), ("failed", 2))
        self.assertEqual(unresolved["issues"][0]["code"], "retention_pruned_state_resolution_required")
        self.assertEqual((orphaned["status"], orphaned["exit_code"]), ("failed", 2))
        self.assertEqual(orphaned["issues"][0]["code"], "retention_pruned_state_orphaned")

    def test_doctor_reports_recovery_state_errors_and_analytics_temp_files(self) -> None:
        cli = load_module("doctor_recovery_state_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            codex_dir = pathlib.Path(tmp_dir) / ".codex"
            base = codex_dir / "bola"
            state_dir = base / "state"
            analytics_dir = base / "analytics"
            normalized_dir = base / "normalized"
            state_dir.mkdir(parents=True)
            analytics_dir.mkdir(parents=True)
            normalized_dir.mkdir(parents=True)
            (state_dir / "pending-turn.json").write_text(
                json.dumps({"record_type": "turn_stop_missing_start", "session_id": "s1", "turn_id": "t1"}),
                encoding="utf-8",
            )
            (state_dir / "current-raw-segments.json").write_text("{}", encoding="utf-8")
            (base / "prompt-usage-errors.jsonl").write_text(
                json.dumps({"warning": "deferred_stop_recovery", "reason": "hook_scan_limit_reached"})
                + "\n"
                + json.dumps({"error": "raw_append_failed"})
                + "\n",
                encoding="utf-8",
            )
            tmp_db = analytics_dir / ".bola.sqlite.123.tmp"
            tmp_db.write_bytes(b"abc")
            tmp_journal = analytics_dir / ".bola.sqlite.123.tmp-journal"
            tmp_journal.write_bytes(b"de")
            pending_publish = normalized_dir / "normalize-state.json.pending"
            pending_publish.write_text("{broken", encoding="utf-8")
            self.initialize_codex_dir(codex_dir)
            captured = io.StringIO()

            with (
                mock.patch.object(cli, "codex_cli_status", return_value=self.valid_codex_cli_status()),
                mock.patch.object(cli.sys, "stdout", captured),
            ):
                code = cli.doctor(argparse.Namespace(codex_dir=str(codex_dir), output_dir=str(base), json_output=True))

        report = json.loads(captured.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(report["health"]["status"], "failed")
        self.assertIn("normalize_pending_publish_recovery_required", {issue["code"] for issue in report["health"]["issues"]})
        self.assertEqual(report["runtime"]["recovery"]["pending_state_files"], 1)
        self.assertEqual(report["runtime"]["recovery"]["error_log_counts"]["warning:deferred_stop_recovery"], 1)
        self.assertEqual(report["runtime"]["recovery"]["error_log_counts"]["error:raw_append_failed"], 1)
        self.assertTrue(report["runtime"]["normalize_pending_publish"]["exists"])
        self.assertTrue(report["runtime"]["normalize_pending_publish"]["recovery_required"])
        self.assertFalse(report["runtime"]["normalize_pending_publish"]["valid"])
        self.assertEqual(report["runtime"]["normalize_pending_publish"]["path"], str(pending_publish))
        self.assertEqual(report["runtime"]["analytics_tmp_files"]["count"], 2)
        self.assertEqual(report["runtime"]["analytics_tmp_files"]["bytes"], 5)
        self.assertEqual({item["sidecar"] for item in report["runtime"]["analytics_tmp_files"]["files"]}, {None, "journal"})

    def test_doctor_exits_degraded_for_unresolved_runtime_signals(self) -> None:
        cli = load_module("doctor_degraded_runtime_test", ROOT / "scripts" / "bola.py")
        now = 2_000_000_000.0
        with tempfile.TemporaryDirectory() as tmp_dir:
            codex_dir = pathlib.Path(tmp_dir) / ".codex"
            base = codex_dir / "bola"
            state_dir = base / "state"
            analytics_dir = base / "analytics"
            state_dir.mkdir(parents=True)
            analytics_dir.mkdir(parents=True)
            (state_dir / "pending-stop.json").write_text(
                json.dumps({"record_type": "turn_stop_missing_start", "session_id": "s1", "turn_id": "t1"}),
                encoding="utf-8",
            )
            (base / "prompt-usage-errors.jsonl").write_text(
                json.dumps(
                    {
                        "captured_at": cli.datetime.fromtimestamp(now - 60, cli.timezone.utc).isoformat(),
                        "error": "raw_append_failed",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            tmp_db = analytics_dir / ".bola.sqlite.123.tmp"
            tmp_db.write_bytes(b"abc")
            stale_mtime = now - cli.DOCTOR_STALE_ANALYTICS_TMP_SECONDS - 1
            os.utime(tmp_db, (stale_mtime, stale_mtime))
            self.initialize_codex_dir(codex_dir)
            (codex_dir / "hooks.json").write_text(
                json.dumps(
                    {
                        "hooks": {
                            "UserPromptSubmit": [{"hooks": [{"type": "command", "command": cli.hook_command()}]}],
                            "Stop": [{"hooks": [{"type": "command", "command": cli.hook_command()}]}],
                        }
                    }
                ),
                encoding="utf-8",
            )
            captured = io.StringIO()

            with (
                mock.patch.dict(cli.os.environ, {"XDG_CONFIG_HOME": str(pathlib.Path(tmp_dir) / "config")}, clear=False),
                mock.patch.object(cli, "codex_cli_status", return_value=self.valid_codex_cli_status()),
                mock.patch.object(cli.time, "time", return_value=now),
                mock.patch.object(cli.sys, "stdout", captured),
            ):
                cli.service_paths.write_config({"codex_dir": codex_dir, "output_dir": base})
                code = cli.doctor(argparse.Namespace(codex_dir=str(codex_dir), output_dir=str(base), json_output=True))

        report = json.loads(captured.getvalue())
        issue_codes = {issue["code"] for issue in report["health"]["issues"]}
        self.assertEqual(code, 1)
        self.assertEqual(report["health"]["status"], "degraded")
        self.assertEqual(report["health"]["exit_code"], 1)
        self.assertEqual(
            issue_codes,
            {"pending_recovery_state", "recent_hook_errors", "stale_analytics_temp_files"},
        )
        self.assertEqual(report["runtime"]["recovery"]["recovery_required_state_files"], 1)
        self.assertEqual(report["runtime"]["recovery"]["recent_error_log_counts"]["error:raw_append_failed"], 1)
        self.assertEqual(report["runtime"]["analytics_tmp_files"]["stale_count"], 1)

    def test_doctor_runtime_windows_ignore_active_and_historical_artifacts(self) -> None:
        cli = load_module("doctor_runtime_windows_test", ROOT / "scripts" / "bola.py")
        now = 2_000_000_000.0
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            state_dir = base / "state"
            analytics_dir = base / "analytics"
            state_dir.mkdir()
            analytics_dir.mkdir()
            active_state = state_dir / "active-turn.json"
            active_state.write_text(json.dumps({"record_type": "turn_start"}), encoding="utf-8")
            os.utime(active_state, (now - 60, now - 60))
            (base / "prompt-usage-errors.jsonl").write_text(
                json.dumps(
                    {
                        "captured_at": cli.datetime.fromtimestamp(
                            now - cli.DOCTOR_RECENT_ERROR_WINDOW_SECONDS - 1,
                            cli.timezone.utc,
                        ).isoformat(),
                        "error": "raw_append_failed",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            active_tmp = analytics_dir / ".bola.sqlite.123.tmp"
            active_tmp.write_bytes(b"abc")
            os.utime(active_tmp, (now - 60, now - 60))

            pending = cli.pending_recovery_state_summary(base, now_unix=now)
            errors = cli.error_log_summary(base, now_unix=now)
            analytics_tmp = cli.analytics_tmp_file_summary(base, now_unix=now)

        self.assertEqual(pending["pending_state_files"], 1)
        self.assertEqual(pending["recovery_required_state_files"], 0)
        self.assertEqual(errors["counts"]["error:raw_append_failed"], 1)
        self.assertEqual(errors["recent_error_counts"], {})
        self.assertEqual(analytics_tmp["count"], 1)
        self.assertEqual(analytics_tmp["stale_count"], 0)

    def test_doctor_reports_invalid_codex_dir_and_cli_with_failure_exit(self) -> None:
        cli = load_module("doctor_invalid_codex_environment_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            codex_dir = root / "empty-codex-dir"
            codex_dir.mkdir()
            captured = io.StringIO()
            invalid_cli = {"valid": False, "path": None, "version": None, "reason": "not_found", "message": "Codex CLI was not found in PATH"}
            with (
                mock.patch.object(cli, "codex_cli_status", return_value=invalid_cli),
                mock.patch.object(cli.sys, "stdout", captured),
            ):
                code = cli.doctor(argparse.Namespace(codex_dir=str(codex_dir), output_dir=str(root / "output"), json_output=True))

        report = json.loads(captured.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(report["health"]["status"], "failed")
        self.assertEqual(report["health"]["exit_code"], 2)
        self.assertFalse(report["codex_dir"]["valid"])
        self.assertEqual(report["codex_dir"]["reason"], "not_initialized")
        self.assertEqual(report["codex_cli"]["reason"], "not_found")

