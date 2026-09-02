from __future__ import annotations

import contextlib

try:
    from tests.support import ROOT, argparse, io, json, load_module, mock, os, pathlib, sqlite3, stat, tempfile
except ModuleNotFoundError:
    from support import ROOT, argparse, io, json, load_module, mock, os, pathlib, sqlite3, stat, tempfile

from scripts import service_paths

try:
    from tests.cli_test_support import CliTestCase
except ModuleNotFoundError:
    from cli_test_support import CliTestCase


class CliPathsTests(CliTestCase):
    def test_migration_evidence_replace_fsyncs_parent_directory(self) -> None:
        cli = load_module("cli_paths_migration_evidence_fsync_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "evidence.json"
            with mock.patch.object(cli.paths_service.atomic_io, "fsync_directory") as fsync_directory:
                cli.paths_service.write_text_atomic_owner_only(path, "{}\n")

        fsync_directory.assert_called_once_with(path.parent)

    def test_service_paths_separate_codex_dir_and_default_user_output_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            codex_dir = pathlib.Path(tmp_dir) / ".codex"
            project_root = pathlib.Path(tmp_dir) / "checkout"
            data_home = pathlib.Path(tmp_dir) / "data-home"
            paths = service_paths.resolve_runtime_paths(
                codex_dir=codex_dir,
                env={"XDG_DATA_HOME": str(data_home)},
                config={},
                project_root=project_root,
            )

            self.assertEqual(paths.codex_dir, codex_dir)
            self.assertEqual(paths.output_dir, data_home / "bola")

    def test_output_layout_is_fixed_lazy_and_owner_only_for_temporary_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir) / "output"
            layout = service_paths.OutputLayout(root)

            self.assertEqual(layout.analytics_db, root / "analytics" / "bola.sqlite")
            self.assertEqual(layout.normalized_log, root / "normalized" / "prompt-usage.normalized.jsonl")
            self.assertEqual(layout.error_log, root / "prompt-usage-errors.jsonl")
            self.assertFalse(root.exists())

            tmp_path = service_paths.ensure_output_tmp_dir(root)

            self.assertEqual(tmp_path, root / "tmp")
            self.assertEqual(stat.S_IMODE(tmp_path.stat().st_mode), 0o700)

    def test_runtime_path_precedence_is_cli_environment_config_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            env = {
                "XDG_CONFIG_HOME": str(root / "config-home"),
                "CODEX_HOME": str(root / "env-codex"),
                "BOLA_OUTPUT_DIR": str(root / "env-data"),
            }
            config_path = service_paths.runtime_config_path(env)
            service_paths.write_config(
                {"codex_dir": root / "config-codex", "output_dir": root / "config-data"},
                config_path,
            )
            configured = service_paths.read_config(config_path)
            from_environment = service_paths.resolve_runtime_paths(env=env, config=configured, project_root=root / "project")
            from_cli = service_paths.resolve_runtime_paths(
                codex_dir=root / "cli-codex",
                output_dir=root / "cli-data",
                env=env,
                config=configured,
                project_root=root / "project",
            )

        self.assertEqual(from_environment.codex_dir, root / "env-codex")
        self.assertEqual(from_environment.output_dir, root / "env-data")
        self.assertEqual(from_cli.codex_dir, root / "cli-codex")
        self.assertEqual(from_cli.output_dir, root / "cli-data")

    def test_invalid_config_schema_fails_instead_of_using_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = pathlib.Path(tmp_dir) / "runtime.conf"
            path.write_text("schema_version=99\ncodex_dir=/tmp/codex\noutput_dir=/tmp/output\n", encoding="utf-8")

            with self.assertRaises(service_paths.ConfigurationError):
                service_paths.read_config(path)

    def test_runtime_config_parser_accepts_comments_and_canonicalizes_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            path = root / "runtime.conf"
            path.write_text(
                "# BOLA runtime paths\n\n schema_version = 1 \n"
                f"codex_dir={root}/codex\noutput_dir={root}/output\n",
                encoding="utf-8",
            )

            configured = service_paths.read_config(path)

        self.assertEqual(configured["schema_version"], 1)
        self.assertEqual(configured["codex_dir"], str(root / "codex"))
        self.assertEqual(configured["output_dir"], str(root / "output"))

    def test_runtime_config_parser_rejects_invalid_structure(self) -> None:
        cases = {
            "missing": "schema_version=1\ncodex_dir=/tmp/codex\n",
            "duplicate": "schema_version=1\ncodex_dir=/tmp/a\ncodex_dir=/tmp/b\noutput_dir=/tmp/output\n",
            "unknown": "schema_version=1\ncodex_dir=/tmp/codex\noutput_dir=/tmp/output\nextra=true\n",
            "relative": "schema_version=1\ncodex_dir=relative\noutput_dir=/tmp/output\n",
            "malformed": "schema_version=1\ncodex_dir=/tmp/codex\noutput_dir\n",
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = pathlib.Path(tmp_dir) / "runtime.conf"
            for name, text in cases.items():
                with self.subTest(name=name):
                    path.write_text(text, encoding="utf-8")
                    with self.assertRaises(service_paths.ConfigurationError):
                        service_paths.read_config(path)

    def test_runtime_config_write_is_complete_and_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            path = root / "config" / "bola" / "runtime.conf"
            service_paths.write_config(
                {"codex_dir": root / "codex", "output_dir": root / "output"},
                path,
            )
            text = path.read_text(encoding="utf-8")
            directory_mode = stat.S_IMODE(path.parent.stat().st_mode)
            file_mode = stat.S_IMODE(path.stat().st_mode)

        self.assertEqual(
            text,
            f"schema_version=1\ncodex_dir={root / 'codex'}\noutput_dir={root / 'output'}\n",
        )
        self.assertEqual(directory_mode, 0o700)
        self.assertEqual(file_mode, 0o600)

    def test_runtime_config_failed_replace_preserves_previous_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            path = root / "runtime.conf"
            service_paths.write_config({"codex_dir": root / "a", "output_dir": root / "output-a"}, path)
            before = path.read_bytes()

            with (
                mock.patch.object(service_paths.os, "replace", side_effect=OSError("simulated replace failure")),
                self.assertRaises(OSError),
            ):
                service_paths.write_config({"codex_dir": root / "b", "output_dir": root / "output-b"}, path)

            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(root.glob(".runtime.conf.*.tmp")), [])

    def test_paths_show_names_runtime_config_explicitly(self) -> None:
        cli = load_module("paths_runtime_config_name_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_home = pathlib.Path(tmp_dir) / "config"
            with mock.patch.dict(cli.os.environ, {"XDG_CONFIG_HOME": str(config_home)}, clear=True):
                report = cli.paths_report()

        self.assertEqual(report["runtime_config_path"], str(config_home / "bola" / "runtime.conf"))
        self.assertEqual(report["effective"]["runtime_config_path"], str(config_home / "bola" / "runtime.conf"))
        self.assertFalse(report["exists"])

    def test_legacy_environment_name_fails_closed_with_mapping(self) -> None:
        cli = load_module("legacy_environment_name_test", ROOT / "scripts" / "bola.py")
        captured = io.StringIO()
        with (
            mock.patch.dict(cli.os.environ, {"CODEX_TOKEN_USAGE_DATA_ROOT": "/tmp/legacy"}, clear=True),
            mock.patch.object(cli.sys, "argv", ["bola", "paths", "show"]),
            mock.patch.object(cli.sys, "stdout", captured),
        ):
            code = cli.main()

        payload = json.loads(captured.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(payload["error"], "legacy_name_unsupported")
        self.assertEqual(payload["mappings"], {"CODEX_TOKEN_USAGE_DATA_ROOT": "BOLA_OUTPUT_DIR"})

    def test_legacy_config_fails_closed_without_touching_neighbor_files(self) -> None:
        cli = load_module("legacy_config_name_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_home = pathlib.Path(tmp_dir) / "config"
            legacy = config_home / "codex-token-bola" / "config.json"
            neighbor = config_home / "codex-token-bola" / "github-app-keys" / "private.pem"
            legacy.parent.mkdir(parents=True)
            legacy.write_text('{"schema_version":2,"data_root":"/tmp/legacy"}\n', encoding="utf-8")
            neighbor.parent.mkdir(parents=True)
            neighbor.write_text("do-not-touch\n", encoding="utf-8")
            captured = io.StringIO()
            with (
                mock.patch.dict(cli.os.environ, {"XDG_CONFIG_HOME": str(config_home)}, clear=True),
                mock.patch.object(cli.sys, "argv", ["bola", "paths", "show"]),
                mock.patch.object(cli.sys, "stdout", captured),
            ):
                code = cli.main()

            payload = json.loads(captured.getvalue())
            self.assertEqual(code, 2)
            self.assertEqual(payload["error"], "legacy_config_unsupported")
            self.assertEqual(payload["mappings"], {str(legacy): str(config_home / "bola" / "runtime.conf")})
            self.assertEqual(neighbor.read_text(encoding="utf-8"), "do-not-touch\n")

    def test_paths_set_switches_output_and_records_pending_migration(self) -> None:
        cli = load_module("paths_output_transition_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            source = root / "source"
            target = root / "target"
            (source / "raw").mkdir(parents=True)
            (source / "raw" / "event.jsonl").write_text("{}\n", encoding="utf-8")
            with mock.patch.dict(cli.os.environ, {"XDG_CONFIG_HOME": str(root / "config")}, clear=True):
                cli.service_paths.write_config({"output_dir": source})
                code = cli.paths_set(argparse.Namespace(codex_dir=None, output_dir=str(target)), emit=False)
                configured = cli.service_paths.read_config()
                transition = cli.service_paths.read_path_transition()

        self.assertEqual(code, 0)
        self.assertEqual(pathlib.Path(str(configured["output_dir"])), target)
        self.assertEqual(pathlib.Path(str(transition["source_output_dir"])), source)
        self.assertEqual(pathlib.Path(str(transition["active_output_dir"])), target)

    def test_paths_set_hands_off_recovery_state_before_switch(self) -> None:
        cli = load_module("paths_state_handoff_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            source = root / "A"
            target = root / "B"
            state_dir = source / "state"
            state_dir.mkdir(parents=True)
            state_path = state_dir / ("a" * 32 + ".json")
            state_path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "record_type": "turn_start",
                        "session_id": "session-1",
                        "turn_id": "turn-1",
                        "captured_at": "2026-08-24T00:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            persisted_phases = []
            finish_handoff = cli.paths_service.finish_transferred_state_handoff

            def inspect_persisted_phase(transition):
                persisted = cli.service_paths.load_path_transition()
                persisted_phases.append(persisted.phase.value if persisted is not None else None)
                return finish_handoff(transition)

            with mock.patch.dict(cli.os.environ, {"XDG_CONFIG_HOME": str(root / "config")}, clear=True):
                cli.service_paths.write_config({"output_dir": source})
                with mock.patch.object(cli.paths_service, "finish_transferred_state_handoff", side_effect=inspect_persisted_phase):
                    code = cli.paths_set(argparse.Namespace(codex_dir=None, output_dir=str(target)), emit=False)
                configured = cli.service_paths.read_config()

            self.assertEqual(code, 0)
            self.assertEqual(persisted_phases, ["preparing"])
            self.assertFalse(state_path.exists())
            self.assertTrue((target / "state" / state_path.name).exists())
            self.assertEqual(pathlib.Path(str(configured["output_dir"])), target)

    def test_turn_started_in_old_output_completes_in_new_output(self) -> None:
        cli = load_module("paths_live_turn_handoff_cli_test", ROOT / "scripts" / "bola.py")
        hook = load_module("paths_live_turn_handoff_hook_test", ROOT / "scripts" / "hook.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            source = root / "A"
            target = root / "B"
            transcript = root / "rollout.jsonl"
            session_id = "session-live"
            turn_id = "turn-live"

            def event(payload: dict[str, object]) -> str:
                return json.dumps({"timestamp": "2026-08-24T00:00:00.000Z", "type": "event_msg", "payload": payload}) + "\n"

            transcript.write_text(
                event(
                    {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {"input_tokens": 0, "total_tokens": 0},
                            "last_token_usage": {"input_tokens": 0, "total_tokens": 0},
                        },
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.dict(cli.os.environ, {"XDG_CONFIG_HOME": str(root / "config")}, clear=True):
                cli.service_paths.write_config({"output_dir": source})
                hook.configure_runtime_paths()
                hook.handle_start(
                    {
                        "hook_event_name": "UserPromptSubmit",
                        "session_id": session_id,
                        "turn_id": turn_id,
                        "transcript_path": str(transcript),
                        "prompt": "continue live turn",
                    }
                )
                cli.paths_set(argparse.Namespace(codex_dir=None, output_dir=str(target)), emit=False)
                with transcript.open("a", encoding="utf-8") as handle:
                    handle.write(event({"type": "task_started", "turn_id": turn_id}))
                    handle.write(
                        event(
                            {
                                "type": "token_count",
                                "info": {
                                    "total_token_usage": {"input_tokens": 10, "output_tokens": 3, "total_tokens": 13},
                                    "last_token_usage": {"input_tokens": 10, "output_tokens": 3, "total_tokens": 13},
                                },
                            }
                        )
                    )
                    handle.write(event({"type": "task_complete", "turn_id": turn_id}))
                hook.configure_runtime_paths()
                hook.handle_stop(
                    {
                        "hook_event_name": "Stop",
                        "session_id": session_id,
                        "turn_id": turn_id,
                        "transcript_path": str(transcript),
                    }
                )
                current = hook.raw_segments.strict_read_current_pointer(target)["current"]["prompt_usage"]
                rows = [json.loads(line) for line in pathlib.Path(current["path"]).read_text(encoding="utf-8").splitlines()]

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["turn_status"], "completed")
            self.assertTrue(rows[0]["start_state_found"])
            self.assertNotEqual(rows[0]["lifecycle_end_reason"], "missing_start_state")
            self.assertEqual(rows[0]["usage"]["total_tokens"], 13)

    def test_paths_set_recovers_preparing_transition_on_either_side_of_config_commit(self) -> None:
        cli = load_module("paths_preparing_recovery_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            source = root / "A"
            target = root / "B"
            name = "turn.json"
            for directory in (source / "state", target / "state"):
                directory.mkdir(parents=True)
                (directory / name).write_text('{"record_type":"turn_start"}\n', encoding="utf-8")
            transition = cli.output_transition_payload(source, target, phase="preparing")
            transition["transferred_state_files"] = [name]
            transition["created_state_files"] = [name]
            with mock.patch.dict(cli.os.environ, {"XDG_CONFIG_HOME": str(root / "config")}, clear=True):
                cli.service_paths.write_config({"output_dir": source})
                cli.service_paths.write_path_transition(transition)
                self.assertIsNone(cli.recover_preparing_path_transition())
                self.assertFalse((target / "state" / name).exists())

                (target / "state" / name).write_text((source / "state" / name).read_text(encoding="utf-8"), encoding="utf-8")
                cli.service_paths.write_path_transition(transition)
                cli.service_paths.write_config({"output_dir": target})
                recovered = cli.recover_preparing_path_transition()

            self.assertEqual(recovered["phase"], "pending")
            self.assertFalse((source / "state" / name).exists())
            self.assertTrue((target / "state" / name).exists())

    def test_paths_set_recovery_retries_pending_persist_after_handoff_cleanup(self) -> None:
        cli = load_module("paths_handoff_pending_persist_recovery_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            source = root / "A"
            target = root / "B"
            name = "turn.json"
            for directory in (source / "state", target / "state"):
                directory.mkdir(parents=True)
                (directory / name).write_text('{"record_type":"turn_start"}\n', encoding="utf-8")
            transition = cli.output_transition_payload(source, target, phase="preparing")
            transition["transferred_state_files"] = [name]
            transition["created_state_files"] = [name]

            with mock.patch.dict(cli.os.environ, {"XDG_CONFIG_HOME": str(root / "config")}, clear=True):
                cli.service_paths.write_config({"output_dir": target})
                cli.service_paths.write_path_transition(transition)
                write_transition = cli.paths_service.service_paths.write_path_transition

                def fail_pending_persist(payload, path=None):
                    typed = payload if isinstance(payload, cli.service_paths.PathTransition) else cli.service_paths.PathTransition.from_payload(payload)
                    if typed.phase is cli.service_paths.PathTransitionPhase.PENDING:
                        raise OSError("simulated pending persist failure")
                    return write_transition(payload, path)

                with mock.patch.object(cli.paths_service.service_paths, "write_path_transition", side_effect=fail_pending_persist):
                    with self.assertRaisesRegex(OSError, "simulated pending persist failure"):
                        cli.recover_preparing_path_transition()

                persisted = cli.service_paths.load_path_transition()
                self.assertEqual(persisted.phase, cli.service_paths.PathTransitionPhase.PREPARING)
                self.assertFalse((source / "state" / name).exists())
                recovered = cli.recover_preparing_path_transition()

            self.assertEqual(recovered["phase"], "pending")
            self.assertFalse((source / "state" / name).exists())
            self.assertTrue((target / "state" / name).exists())

    def test_paths_set_rejects_invalid_output_target_without_mutating_config(self) -> None:
        cli = load_module("paths_invalid_output_target_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            source = root / "A"
            invalid = root / "not-a-directory"
            invalid.write_text("file\n", encoding="utf-8")
            with mock.patch.dict(cli.os.environ, {"XDG_CONFIG_HOME": str(root / "config")}, clear=True):
                cli.service_paths.write_config({"output_dir": source})
                with self.assertRaises(cli.service_paths.ConfigurationError):
                    cli.paths_set(argparse.Namespace(codex_dir=None, output_dir=str(invalid)), emit=False)
                configured = cli.service_paths.read_config()

            self.assertEqual(pathlib.Path(str(configured["output_dir"])), source)

    def test_paths_set_refuses_to_switch_while_source_service_is_locked(self) -> None:
        cli = load_module("paths_source_service_lock_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            source = root / "A"
            target = root / "B"
            with mock.patch.dict(cli.os.environ, {"XDG_CONFIG_HOME": str(root / "config")}, clear=True):
                cli.service_paths.write_config({"output_dir": source})
                with cli.service_lock.acquire_service_lock(output_dir=source):
                    with self.assertRaises(cli.service_lock.ServiceLockBusy):
                        cli.paths_set(argparse.Namespace(codex_dir=None, output_dir=str(target)), emit=False)
                configured = cli.service_paths.read_config()

            self.assertEqual(pathlib.Path(str(configured["output_dir"])), source)

    def test_paths_set_restores_handed_off_state_when_post_commit_step_fails(self) -> None:
        cli = load_module("paths_state_handoff_rollback_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            source = root / "A"
            target = root / "B"
            state_path = source / "state" / "turn.json"
            state_path.parent.mkdir(parents=True)
            state_path.write_text(
                json.dumps({"record_type": "turn_start", "session_id": "session", "turn_id": "turn"}),
                encoding="utf-8",
            )
            with mock.patch.dict(cli.os.environ, {"XDG_CONFIG_HOME": str(root / "config")}, clear=True):
                cli.service_paths.write_config({"output_dir": source})
                with mock.patch.object(cli, "managed_content_files", side_effect=OSError("scan failed")):
                    with self.assertRaises(OSError):
                        cli.paths_set(argparse.Namespace(codex_dir=None, output_dir=str(target)), emit=False)
                configured = cli.service_paths.read_config()
                transition = cli.service_paths.read_path_transition()

            self.assertEqual(pathlib.Path(str(configured["output_dir"])), source)
            self.assertTrue(state_path.exists())
            self.assertFalse((target / "state" / state_path.name).exists())
            self.assertIsNone(transition)

    def test_paths_set_allows_direct_rollback_but_rejects_third_output(self) -> None:
        cli = load_module("paths_direct_rollback_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            source = root / "A"
            active = root / "B"
            third = root / "C"
            (source / "raw").mkdir(parents=True)
            (source / "raw" / "old.jsonl").write_text("{}\n", encoding="utf-8")
            with mock.patch.dict(cli.os.environ, {"XDG_CONFIG_HOME": str(root / "config")}, clear=True):
                cli.service_paths.write_config({"output_dir": source})
                cli.paths_set(argparse.Namespace(codex_dir=None, output_dir=str(active)), emit=False)
                with self.assertRaises(cli.service_paths.ConfigurationError):
                    cli.paths_set(argparse.Namespace(codex_dir=None, output_dir=str(third)), emit=False)
                (active / "raw").mkdir(parents=True)
                (active / "raw" / "new.jsonl").write_text("{}\n", encoding="utf-8")
                cli.paths_set(argparse.Namespace(codex_dir=None, output_dir=str(source)), emit=False)
                transition = cli.service_paths.read_path_transition()
                configured = cli.service_paths.read_config()

            self.assertEqual(pathlib.Path(str(configured["output_dir"])), source)
            self.assertEqual(pathlib.Path(str(transition["source_output_dir"])), active)
            self.assertEqual(pathlib.Path(str(transition["active_output_dir"])), source)

    def test_paths_migrate_imports_raw_into_nonempty_active_output(self) -> None:
        cli = load_module("paths_merge_migration_test", ROOT / "scripts" / "bola.py")
        row = {
            "schema_version": 2,
            "record_type": "turn_usage_raw",
            "session_id": "session-old",
            "turn_id": "turn-old",
            "captured_at": "2026-08-24T00:00:00+00:00",
            "started_at": "2026-08-24T00:00:00+00:00",
            "stopped_at": "2026-08-24T00:01:00+00:00",
            "turn_status": "completed",
            "estimated": False,
            "usage": {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 2, "reasoning_output_tokens": 0, "total_tokens": 3},
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            source = root / "A"
            active = root / "B"
            (source / "raw").mkdir(parents=True)
            (source / "raw" / "old.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
            (active / "reports").mkdir(parents=True)
            (active / "reports" / "new.txt").write_text("keep\n", encoding="utf-8")
            with mock.patch.dict(cli.os.environ, {"XDG_CONFIG_HOME": str(root / "config")}, clear=True):
                cli.service_paths.write_config({"output_dir": source})
                cli.paths_set(argparse.Namespace(codex_dir=None, output_dir=str(active)), emit=False)
                with mock.patch.object(cli, "run_script_json", return_value=(0, {"status": "healthy"}, "", "")):
                    code, result = cli.apply_output_migration(source, active, cli.service_paths.read_path_transition())
                manifest = cli.raw_segments.strict_read_manifest(active)
                transition = cli.service_paths.read_path_transition()

            self.assertEqual(code, 0)
            self.assertEqual(result["imported_rows"], 1)
            self.assertEqual(len(manifest["segments"]), 1)
            self.assertTrue((active / "reports" / "new.txt").exists())
            self.assertFalse((source / "raw").exists())
            self.assertIsNone(transition)

    def test_paths_migrate_excludes_root_error_log_from_raw_sources(self) -> None:
        cli = load_module("paths_migration_source_filter_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = pathlib.Path(tmp_dir) / "A"
            source.mkdir(parents=True)
            raw_log = source / "prompt-usage.raw.jsonl"
            error_log = source / "prompt-usage-errors.jsonl"
            raw_log.write_text("{}\n", encoding="utf-8")
            error_log.write_text('{"error":"append_failed"}\n', encoding="utf-8")

            sources = cli.raw_migration_sources(source)

        self.assertEqual(sources, [raw_log.resolve()])

    def test_paths_migrate_accepts_only_explicit_degraded_exit_one(self) -> None:
        cli = load_module("paths_migration_process_result_test", ROOT / "scripts" / "bola.py")

        degraded = cli.ProcessResult(
            command=cli.RuntimeCommand.RECONCILE,
            exit_code=1,
            payload={"status": "degraded"},
        )
        self.assertEqual(
            cli.paths_service.require_migration_process_result(degraded, allow_degraded=True),
            {"status": "degraded"},
        )

        for result in (
            cli.ProcessResult(command=cli.RuntimeCommand.RECONCILE, exit_code=1, payload={"status": "failed"}),
            cli.ProcessResult(command=cli.RuntimeCommand.RECONCILE, exit_code=1, payload=None, parse_error="stdout_empty"),
            cli.ProcessResult(command=cli.RuntimeCommand.RECONCILE, exit_code=0, payload={"status": "failed"}),
        ):
            with self.subTest(result=result):
                with self.assertRaises(cli.service_paths.ConfigurationError):
                    cli.paths_service.require_migration_process_result(result, allow_degraded=True)

    def test_paths_migrate_failed_reconcile_preserves_source_and_requires_recovery(self) -> None:
        cli = load_module("paths_failed_reconcile_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            source = root / "A"
            active = root / "B"
            source_raw = source / "raw" / "old.jsonl"
            source_raw.parent.mkdir(parents=True)
            source_raw.write_text("{}\n", encoding="utf-8")
            with mock.patch.dict(cli.os.environ, {"XDG_CONFIG_HOME": str(root / "config")}, clear=True):
                cli.service_paths.write_config({"output_dir": source})
                cli.paths_set(argparse.Namespace(codex_dir=None, output_dir=str(active)), emit=False)
                with mock.patch.object(
                    cli,
                    "run_script_json",
                    return_value=(1, {"status": "failed", "error": "write_failed"}, "", ""),
                ):
                    with self.assertRaises(cli.service_paths.ConfigurationError):
                        cli.apply_output_migration(source, active, cli.service_paths.read_path_transition())
                transition = cli.service_paths.read_path_transition()

            self.assertTrue(source_raw.exists())
            self.assertFalse((active / "raw").exists())
            self.assertEqual(transition["phase"], "recovery_required")

    def test_paths_migrate_failed_normalize_skips_build_and_preserves_source(self) -> None:
        cli = load_module("paths_failed_normalize_test", ROOT / "scripts" / "bola.py")
        calls: list[str] = []

        def fail_normalize(script_name: str, _args: list[str], **_kwargs: object) -> tuple[int, dict[str, object], str, str]:
            calls.append(script_name)
            if script_name == "normalize.py":
                return 1, {"status": "failed", "error": "normalize_failed"}, "", ""
            return 0, {"status": "healthy"}, "", ""

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            source = root / "A"
            active = root / "B"
            source_raw = source / "raw" / "old.jsonl"
            source_raw.parent.mkdir(parents=True)
            source_raw.write_text("{}\n", encoding="utf-8")
            with mock.patch.dict(cli.os.environ, {"XDG_CONFIG_HOME": str(root / "config")}, clear=True):
                cli.service_paths.write_config({"output_dir": source})
                cli.paths_set(argparse.Namespace(codex_dir=None, output_dir=str(active)), emit=False)
                with mock.patch.object(cli, "run_script_json", side_effect=fail_normalize):
                    with self.assertRaises(cli.service_paths.ConfigurationError):
                        cli.apply_output_migration(source, active, cli.service_paths.read_path_transition())
                transition = cli.service_paths.read_path_transition()

            self.assertTrue(source_raw.exists())
            self.assertNotIn("build_analytics.py", calls)
            self.assertEqual(transition["phase"], "recovery_required")

    def test_paths_migrate_blocks_unresolved_physical_retention_deletion(self) -> None:
        cli = load_module("paths_pending_physical_delete_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = pathlib.Path(tmp_dir) / "A"
            pending_segments = [{"id": f"old-{index}", "path": str(source / "raw" / "archive" / f"old-{index}.jsonl.gz")} for index in range(25)]
            sweep = {
                "deleted_files": 0,
                "pending_files": len(pending_segments),
                "pending_source_segments": pending_segments,
                "errors": [{"path": item["path"], "error": "busy"} for item in pending_segments],
            }
            marker = {"phase": "unlink_pending", "unlink_pending_segments": pending_segments}
            with (
                mock.patch.object(cli.raw_segments, "sweep_apply_marker", return_value=sweep),
                mock.patch.object(cli.raw_segments, "read_apply_marker", return_value=marker),
            ):
                with self.assertRaises(cli.PathMigrationBlocked) as raised:
                    cli.raw_migration_sources(source)

        payload = raised.exception.payload()
        self.assertEqual(payload["error"], "source_physical_delete_pending")
        self.assertEqual(payload["pending_files"], 25)
        self.assertEqual(len(payload["pending_paths"]), 20)
        self.assertTrue(payload["pending_paths_truncated"])
        self.assertTrue(payload["retryable"])

    def test_paths_migrate_physical_delete_blocker_keeps_pending_transition(self) -> None:
        cli = load_module("paths_pending_transition_retention_delete_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            source = root / "A"
            active = root / "B"
            (source / "raw").mkdir(parents=True)
            (source / "raw" / "old.jsonl").write_text("{}\n", encoding="utf-8")
            blocker = cli.PathMigrationBlocked(
                {
                    "status": "blocked",
                    "migrated": False,
                    "error": "source_physical_delete_pending",
                    "pending_files": 1,
                    "retryable": True,
                }
            )
            with mock.patch.dict(cli.os.environ, {"XDG_CONFIG_HOME": str(root / "config")}, clear=True):
                cli.service_paths.write_config({"output_dir": source})
                cli.paths_set(argparse.Namespace(codex_dir=None, output_dir=str(active)), emit=False)
                transition = cli.service_paths.read_path_transition()
                with mock.patch.object(cli, "resolve_source_physical_deletes", side_effect=blocker):
                    with self.assertRaises(cli.PathMigrationBlocked):
                        cli.apply_output_migration(source, active, transition)
                current = cli.service_paths.read_path_transition()

            self.assertEqual(current["phase"], "pending")
            self.assertTrue((source / "raw" / "old.jsonl").exists())
            self.assertFalse((active / "raw").exists())

    def test_paths_migrate_prints_structured_physical_delete_blocker(self) -> None:
        cli = load_module("paths_pending_delete_payload_test", ROOT / "scripts" / "bola.py")
        blocker = cli.PathMigrationBlocked(
            {
                "status": "blocked",
                "migrated": False,
                "error": "source_physical_delete_pending",
                "pending_files": 1,
                "pending_paths": ["/old/segment.jsonl.gz"],
                "pending_paths_truncated": False,
                "retryable": True,
            }
        )
        with (
            mock.patch.object(cli, "pending_output_migration", return_value=(None, pathlib.Path("/old"), pathlib.Path("/new"))),
            mock.patch.object(cli.paths_service, "output_migration_preview", side_effect=blocker),
            contextlib.redirect_stdout(io.StringIO()) as stdout,
        ):
            code = cli.paths_migrate(argparse.Namespace(output_dir=True, apply=False))

        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stdout.getvalue())["error"], "source_physical_delete_pending")

    def test_paths_migrate_apply_recovers_retention_before_preview(self) -> None:
        cli = load_module("paths_apply_recovery_order_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            source = root / "A"
            active = root / "B"
            source_raw = source / "raw" / "old.jsonl"
            source_raw.parent.mkdir(parents=True)
            source_raw.write_text("{}\n", encoding="utf-8")
            recovered: list[pathlib.Path] = []

            def recover(output_dir: pathlib.Path) -> dict[str, object]:
                recovered.append(output_dir.resolve(strict=False))
                if output_dir.resolve(strict=False) == source.resolve(strict=False):
                    source_raw.unlink(missing_ok=True)
                return {"status": "healthy"}

            def preview(
                preview_source: pathlib.Path,
                preview_destination: pathlib.Path,
                transition: object,
            ) -> dict[str, object]:
                expected_roots = {source.resolve(strict=False), active.resolve(strict=False)}
                if set(recovered) != expected_roots:
                    raise cli.PathMigrationBlocked(
                        {
                            "status": "blocked",
                            "migrated": False,
                            "error": "retention_pruned_state_pending",
                            "retryable": True,
                        }
                    )
                return {
                    "transition_id": transition.transition_id,
                    "source_output_dir": str(preview_source),
                    "active_output_dir": str(preview_destination),
                    "source_file_count": 0,
                    "source_bytes": 0,
                    "raw_source_count": 0,
                    "retention_pruned_turns": {
                        "source_rows": 0,
                        "destination_rows": 0,
                        "merged_rows": 0,
                        "deduplicated_rows": 0,
                    },
                    "derived_rebuild": True,
                    "source_evidence_incomplete": False,
                }

            with mock.patch.dict(cli.os.environ, {"XDG_CONFIG_HOME": str(root / "config")}, clear=True):
                cli.service_paths.write_config({"output_dir": source})
                cli.paths_set(argparse.Namespace(codex_dir=None, output_dir=str(active)), emit=False)
                with (
                    mock.patch.object(cli.dashboard_cleanup, "recover_retention_cleanup", side_effect=recover),
                    mock.patch.object(cli.paths_service, "output_migration_preview", side_effect=preview),
                    contextlib.redirect_stdout(io.StringIO()) as stdout,
                ):
                    code = cli.paths_migrate(argparse.Namespace(output_dir=True, apply=True))
                transition_after = cli.service_paths.read_path_transition()

        self.assertEqual(code, 0, stdout.getvalue())
        self.assertEqual(json.loads(stdout.getvalue())["status"], "noop")
        self.assertEqual(set(recovered), {source.resolve(strict=False), active.resolve(strict=False)})
        self.assertIsNone(transition_after)

    def test_paths_migrate_preview_does_not_recover_retention(self) -> None:
        cli = load_module("paths_preview_no_recovery_test", ROOT / "scripts" / "bola.py")
        preview = {
            "transition_id": None,
            "source_output_dir": "/same",
            "active_output_dir": "/same",
            "source_file_count": 0,
            "source_bytes": 0,
            "raw_source_count": 0,
            "retention_pruned_turns": {
                "source_rows": 0,
                "destination_rows": 0,
                "merged_rows": 0,
                "deduplicated_rows": 0,
            },
            "derived_rebuild": True,
            "source_evidence_incomplete": False,
        }
        dependencies = cli.paths_service.MigrationDependencies(
            run_command=mock.Mock(),
            resolve_physical_deletes=mock.Mock(),
            recover_retention_cleanup=mock.Mock(side_effect=AssertionError("dry-run must not recover retention")),
        )
        with (
            mock.patch.object(
                cli.paths_service,
                "pending_output_migration",
                return_value=(None, pathlib.Path("/same"), pathlib.Path("/same")),
            ),
            mock.patch.object(cli.paths_service, "output_migration_preview", return_value=preview),
        ):
            result = cli.paths_service.run_paths_migrate(
                cli.paths_service.PathsMigrateOptions(output_dir=True, apply=False),
                dependencies,
            )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.payload["status"], "noop")
        dependencies.recover_retention_cleanup.assert_not_called()

    def test_paths_migrate_apply_blocks_incomplete_source_before_applying(self) -> None:
        cli = load_module("paths_apply_incomplete_source_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            source = root / "A"
            active = root / "B"
            normalized = source / "normalized" / "prompt-usage.normalized.jsonl"
            normalized.parent.mkdir(parents=True)
            normalized.write_text("{}\n", encoding="utf-8")
            with mock.patch.dict(cli.os.environ, {"XDG_CONFIG_HOME": str(root / "config")}, clear=True):
                cli.service_paths.write_config({"output_dir": source})
                cli.paths_set(argparse.Namespace(codex_dir=None, output_dir=str(active)), emit=False)
                with (
                    mock.patch.object(cli.dashboard_cleanup, "recover_retention_cleanup", return_value={}),
                    mock.patch.object(cli, "run_typed_script_json") as run_command,
                    contextlib.redirect_stdout(io.StringIO()) as stdout,
                ):
                    code = cli.paths_migrate(argparse.Namespace(output_dir=True, apply=True))
                transition_after = cli.service_paths.load_path_transition()
                source_preserved = normalized.exists()

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(payload["error"], "source_evidence_incomplete")
        self.assertEqual(transition_after.phase, cli.service_paths.PathTransitionPhase.PENDING)
        self.assertTrue(source_preserved)
        run_command.assert_not_called()

    def test_paths_migrate_merges_retention_pruned_turn_state(self) -> None:
        cli = load_module("paths_retention_pruned_merge_test", ROOT / "scripts" / "bola.py")

        def state_payload(session_id: str, turn_id: str, cutoff: float) -> dict[str, object]:
            return {
                "schema_version": 1,
                "cutoff_unix": cutoff,
                "updated_at_unix": cutoff,
                "pruned_turns": [{"session_id": session_id, "turn_id": turn_id, "captured_at_unix": cutoff}],
            }

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            source = root / "A"
            active = root / "B"
            (source / "raw").mkdir(parents=True)
            (source / "raw" / "old.jsonl").write_text("{}\n", encoding="utf-8")
            (source / "state").mkdir(parents=True)
            (active / "state").mkdir(parents=True)
            (source / "state" / "retention-pruned-turns.json").write_text(
                json.dumps(state_payload("source-session", "source-turn", 10.0)) + "\n",
                encoding="utf-8",
            )
            (active / "state" / "retention-pruned-turns.json").write_text(
                json.dumps(state_payload("active-session", "active-turn", 20.0)) + "\n",
                encoding="utf-8",
            )
            with mock.patch.dict(cli.os.environ, {"XDG_CONFIG_HOME": str(root / "config")}, clear=True):
                cli.service_paths.write_config({"output_dir": source})
                cli.paths_set(argparse.Namespace(codex_dir=None, output_dir=str(active)), emit=False)
                with mock.patch.object(cli, "run_script_json", return_value=(0, {"status": "healthy"}, "", "")):
                    code, result = cli.apply_output_migration(source, active, cli.service_paths.read_path_transition())
                retention_db = active / "state" / "retention-pruned-turns.sqlite"
                with sqlite3.connect(retention_db) as con:
                    merged_rows = con.execute("select session_id, turn_id, state from pruned_turns order by session_id, turn_id").fetchall()
                legacy_exists = (active / "state" / "retention-pruned-turns.json").exists()

        self.assertEqual(code, 0)
        self.assertEqual(result["retention_pruned_turns"], {"source_rows": 1, "destination_rows": 1, "merged_rows": 2, "deduplicated_rows": 0})
        self.assertEqual(
            merged_rows,
            [("active-session", "active-turn", "committed"), ("source-session", "source-turn", "committed")],
        )
        self.assertFalse(legacy_exists)

    def test_paths_migrate_preview_blocks_legacy_pending_state_in_either_root(self) -> None:
        cli = load_module("paths_retention_legacy_pending_preview_test", ROOT / "scripts" / "bola.py")
        payload = {
            "schema_version": 1,
            "cutoff_unix": 20.0,
            "updated_at_unix": 20.0,
            "pruned_turns": [{"session_id": "legacy-session", "turn_id": "legacy-turn", "captured_at_unix": 20.0}],
        }

        for pending_root_name in ("source", "destination"):
            with self.subTest(pending_root=pending_root_name), tempfile.TemporaryDirectory() as tmp_dir:
                root = pathlib.Path(tmp_dir)
                source = root / "source"
                destination = root / "destination"
                (source / "raw").mkdir(parents=True)
                (source / "raw" / "old.jsonl").write_text("{}\n", encoding="utf-8")
                pending_root = source if pending_root_name == "source" else destination
                pending_path = pending_root / "state" / "retention-pruned-turns.pending.json"
                pending_path.parent.mkdir(parents=True)
                pending_bytes = (json.dumps(payload) + "\n").encode()
                pending_path.write_bytes(pending_bytes)

                with (
                    mock.patch.object(
                        cli.paths_service.retention_pruned_store,
                        "snapshot_rows",
                        wraps=cli.paths_service.retention_pruned_store.snapshot_rows,
                    ) as snapshot_rows,
                    self.assertRaises(cli.PathMigrationBlocked) as raised,
                ):
                    cli.paths_service.output_migration_preview(source, destination, None)

                blocked = raised.exception.payload()
                self.assertEqual(blocked["error"], "retention_pruned_state_pending")
                self.assertTrue(blocked["retryable"])
                self.assertEqual(blocked["pending"], [{
                    "output_dir": str(pending_root),
                    "job_ids": ["legacy:retention-pruned-turns.pending.json"],
                    "rows": 1,
                }])
                self.assertNotIn(mock.call(pending_root), snapshot_rows.call_args_list)
                self.assertEqual(pending_path.read_bytes(), pending_bytes)
                self.assertFalse((pending_root / "state" / "retention-pruned-turns.sqlite").exists())

    def test_paths_migrate_apply_blocks_legacy_pending_state_in_either_root_without_mutation(self) -> None:
        cli = load_module("paths_retention_legacy_pending_apply_test", ROOT / "scripts" / "bola.py")
        payload = {
            "schema_version": 1,
            "cutoff_unix": 20.0,
            "updated_at_unix": 20.0,
            "pruned_turns": [{"session_id": "legacy-session", "turn_id": "legacy-turn", "captured_at_unix": 20.0}],
        }

        for pending_root_name in ("source", "destination"):
            with self.subTest(pending_root=pending_root_name), tempfile.TemporaryDirectory() as tmp_dir:
                root = pathlib.Path(tmp_dir)
                source = root / "source"
                destination = root / "destination"
                source_raw = source / "raw" / "old.jsonl"
                source_raw.parent.mkdir(parents=True)
                source_raw.write_text("{}\n", encoding="utf-8")
                pending_root = source if pending_root_name == "source" else destination
                pending_path = pending_root / "state" / "retention-pruned-turns.pending.json"
                pending_path.parent.mkdir(parents=True)
                pending_bytes = (json.dumps(payload) + "\n").encode()
                pending_path.write_bytes(pending_bytes)
                run_command = mock.Mock(side_effect=AssertionError("blocked migration must not run pipeline commands"))
                dependencies = cli.paths_service.MigrationDependencies(
                    run_command=run_command,
                    resolve_physical_deletes=mock.Mock(),
                    recover_retention_cleanup=mock.Mock(return_value={}),
                )

                with mock.patch.dict(cli.os.environ, {"XDG_CONFIG_HOME": str(root / "config")}, clear=True):
                    cli.service_paths.write_config({"output_dir": source})
                    cli.paths_set(argparse.Namespace(codex_dir=None, output_dir=str(destination)), emit=False)
                    transition_before = cli.service_paths.read_path_transition()
                    result = cli.paths_service.run_paths_migrate(
                        cli.paths_service.PathsMigrateOptions(output_dir=True, apply=True),
                        dependencies,
                    )
                    transition_after = cli.service_paths.read_path_transition()

                self.assertEqual(result.exit_code, 2)
                self.assertEqual(result.payload["error"], "retention_pruned_state_pending")
                self.assertEqual(result.payload["pending"][0]["output_dir"], str(pending_root))
                self.assertEqual(transition_after, transition_before)
                self.assertEqual(transition_after["phase"], "pending")
                self.assertEqual(pending_path.read_bytes(), pending_bytes)
                self.assertFalse((pending_root / "state" / "retention-pruned-turns.sqlite").exists())
                self.assertTrue(source_raw.exists())
                run_command.assert_not_called()

    def test_paths_migrate_blocks_legacy_pending_even_when_sqlite_has_matching_committed_row(self) -> None:
        cli = load_module("paths_retention_overlapping_legacy_pending_test", ROOT / "scripts" / "bola.py")
        row = {"session_id": "same-session", "turn_id": "same-turn", "captured_at_unix": 20.0}

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            source = root / "source"
            destination = root / "destination"
            (source / "raw").mkdir(parents=True)
            (source / "raw" / "old.jsonl").write_text("{}\n", encoding="utf-8")
            committed_job = cli.paths_service.retention_pruned_store.stage_rows(destination, [row], job_id="retention:done")
            cli.paths_service.retention_pruned_store.commit_stage(destination, committed_job)
            pending_path = destination / "state" / "retention-pruned-turns.pending.json"
            pending_bytes = (json.dumps({
                "schema_version": 1,
                "cutoff_unix": 20.0,
                "updated_at_unix": 20.0,
                "pruned_turns": [row],
            }) + "\n").encode()
            pending_path.write_bytes(pending_bytes)

            with self.assertRaises(cli.PathMigrationBlocked) as raised:
                cli.paths_service.output_migration_preview(source, destination, None)
            committed_rows = cli.paths_service.retention_pruned_store.snapshot_rows(destination)
            pending_after = pending_path.read_bytes()

        blocked = raised.exception.payload()
        self.assertEqual(blocked["error"], "retention_pruned_state_pending")
        self.assertEqual(blocked["pending"][0]["job_ids"], ["legacy:retention-pruned-turns.pending.json"])
        self.assertEqual(pending_after, pending_bytes)
        self.assertEqual(committed_rows[("same-session", "same-turn")]["state"], "committed")

    def test_paths_migrate_blocks_conflicting_retention_pruned_turn_state(self) -> None:
        cli = load_module("paths_retention_pruned_conflict_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            source = root / "A"
            active = root / "B"
            for directory, captured_at in ((source, 10.0), (active, 20.0)):
                (directory / "state").mkdir(parents=True)
                (directory / "state" / "retention-pruned-turns.json").write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "cutoff_unix": captured_at,
                            "updated_at_unix": captured_at,
                            "pruned_turns": [{"session_id": "same-session", "turn_id": "same-turn", "captured_at_unix": captured_at}],
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
            (source / "raw").mkdir(parents=True)
            (source / "raw" / "old.jsonl").write_text("{}\n", encoding="utf-8")
            with mock.patch.dict(cli.os.environ, {"XDG_CONFIG_HOME": str(root / "config")}, clear=True):
                cli.service_paths.write_config({"output_dir": source})
                cli.paths_set(argparse.Namespace(codex_dir=None, output_dir=str(active)), emit=False)
                transition = cli.service_paths.read_path_transition()
                with self.assertRaises(cli.PathMigrationBlocked) as raised:
                    cli.apply_output_migration(source, active, transition)
                current = cli.service_paths.read_path_transition()
                source_raw_exists = (source / "raw" / "old.jsonl").exists()
                active_raw_exists = (active / "raw").exists()

        payload = raised.exception.payload()
        self.assertEqual(payload["error"], "retention_pruned_turn_conflict")
        self.assertEqual(payload["conflicts"], 1)
        self.assertFalse(payload["retryable"])
        self.assertEqual(current["phase"], "pending")
        self.assertTrue(source_raw_exists)
        self.assertFalse(active_raw_exists)

    def test_paths_migrate_blocks_foreign_retention_pending_state(self) -> None:
        cli = load_module("paths_retention_pending_owner_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            source = root / "A"
            active = root / "B"
            (source / "raw").mkdir(parents=True)
            (source / "raw" / "old.jsonl").write_text("{}\n", encoding="utf-8")
            pending_job = cli.paths_service.retention_pruned_store.stage_rows(
                active,
                [{"session_id": "active-session", "turn_id": "active-turn", "captured_at_unix": 20.0}],
                job_id="retention:active-job",
            )
            with mock.patch.dict(cli.os.environ, {"XDG_CONFIG_HOME": str(root / "config")}, clear=True):
                cli.service_paths.write_config({"output_dir": source})
                cli.paths_set(argparse.Namespace(codex_dir=None, output_dir=str(active)), emit=False)
                transition = cli.service_paths.read_path_transition()
                with self.assertRaises(cli.PathMigrationBlocked) as raised:
                    cli.apply_output_migration(source, active, transition)
                current = cli.service_paths.read_path_transition()

        payload = raised.exception.payload()
        self.assertEqual(payload["error"], "retention_pruned_state_pending")
        self.assertTrue(payload["retryable"])
        self.assertEqual(payload["pending"][0]["job_ids"], [pending_job])
        self.assertEqual(current["phase"], "pending")

    def test_paths_migrate_retries_staged_retention_pruned_turn_state_after_build_failure(self) -> None:
        cli = load_module("paths_retention_pruned_retry_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            source = root / "A"
            active = root / "B"
            (source / "raw").mkdir(parents=True)
            (source / "raw" / "old.jsonl").write_text("{}\n", encoding="utf-8")
            (source / "state").mkdir(parents=True)
            (source / "state" / "retention-pruned-turns.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "cutoff_unix": 10.0,
                        "updated_at_unix": 10.0,
                        "pruned_turns": [{"session_id": "source", "turn_id": "turn", "captured_at_unix": 10.0}],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            def fail_build(script_name: str, _args: list[str], **_kwargs: object) -> tuple[int, dict[str, object], str, str]:
                if script_name == "build_analytics.py":
                    return 2, {"status": "failed"}, "", ""
                return 0, {"status": "healthy"}, "", ""

            with mock.patch.dict(cli.os.environ, {"XDG_CONFIG_HOME": str(root / "config")}, clear=True):
                cli.service_paths.write_config({"output_dir": source})
                cli.paths_set(argparse.Namespace(codex_dir=None, output_dir=str(active)), emit=False)
                with mock.patch.object(cli, "run_script_json", side_effect=fail_build):
                    with self.assertRaises(cli.service_paths.ConfigurationError):
                        cli.apply_output_migration(source, active, cli.service_paths.read_path_transition())
                retention_db = active / "state" / "retention-pruned-turns.sqlite"
                with sqlite3.connect(retention_db) as con:
                    pending_after_failure = con.execute("select count(*) from pruned_turns where state='pending'").fetchone()[0]
                    pending_job_after_failure = con.execute(
                        "select distinct job_id from pruned_turns where state='pending'"
                    ).fetchone()[0]
                transition_after_failure = cli.service_paths.read_path_transition()

                with mock.patch.object(cli, "run_script_json", return_value=(0, {"status": "healthy"}, "", "")):
                    code, _result = cli.apply_output_migration(source, active, transition_after_failure)
                final_after_retry = retention_db.exists()
                with sqlite3.connect(retention_db) as con:
                    pending_after_retry = con.execute("select count(*) from pruned_turns where state='pending'").fetchone()[0]
                transition_after_retry = cli.service_paths.read_path_transition()

        self.assertEqual(pending_after_failure, 1)
        self.assertEqual(transition_after_failure["phase"], "recovery_required")
        self.assertEqual(pending_job_after_failure, f"migration:{transition_after_failure['transition_id']}")
        self.assertEqual(code, 0)
        self.assertTrue(final_after_retry)
        self.assertEqual(pending_after_retry, 0)
        self.assertIsNone(transition_after_retry)

    def test_paths_migrate_recovers_persisted_applying_transition(self) -> None:
        cli = load_module("paths_interrupted_apply_retry_test", ROOT / "scripts" / "bola.py")
        row = {
            "schema_version": 2,
            "record_type": "turn_usage_raw",
            "session_id": "session-old",
            "turn_id": "turn-old",
            "captured_at": "2026-08-24T00:00:00+00:00",
            "started_at": "2026-08-24T00:00:00+00:00",
            "stopped_at": "2026-08-24T00:01:00+00:00",
            "turn_status": "completed",
            "estimated": False,
            "usage": {
                "input_tokens": 1,
                "cached_input_tokens": 0,
                "output_tokens": 2,
                "reasoning_output_tokens": 0,
                "total_tokens": 3,
            },
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            source = root / "A"
            active = root / "B"
            (source / "raw").mkdir(parents=True)
            (source / "raw" / "old.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
            with mock.patch.dict(cli.os.environ, {"XDG_CONFIG_HOME": str(root / "config")}, clear=True):
                cli.service_paths.write_config({"output_dir": source})
                cli.paths_set(argparse.Namespace(codex_dir=None, output_dir=str(active)), emit=False)
                interrupted = cli.service_paths.load_path_transition().begin_migration()
                cli.service_paths.write_path_transition(interrupted)
                with mock.patch.object(cli, "run_script_json", return_value=(0, {"status": "healthy"}, "", "")):
                    code, result = cli.apply_output_migration(source, active, interrupted)
                current = cli.service_paths.load_path_transition()

        self.assertEqual(code, 0)
        self.assertEqual(result["transition_id"], interrupted.transition_id)
        self.assertIsNone(current)
        self.assertFalse(source.exists())

    def test_paths_migrate_does_not_reclassify_live_applying_transition(self) -> None:
        cli = load_module("paths_live_apply_lock_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            source = root / "A"
            active = root / "B"
            (source / "raw").mkdir(parents=True)
            (source / "raw" / "old.jsonl").write_text("{}\n", encoding="utf-8")
            with mock.patch.dict(cli.os.environ, {"XDG_CONFIG_HOME": str(root / "config")}, clear=True):
                cli.service_paths.write_config({"output_dir": source})
                cli.paths_set(argparse.Namespace(codex_dir=None, output_dir=str(active)), emit=False)
                interrupted = cli.service_paths.load_path_transition().begin_migration()
                cli.service_paths.write_path_transition(interrupted)
                with cli.paths_service.service_lock.acquire_service_lock(reason="live-migration", output_dir=source):
                    with self.assertRaises(cli.paths_service.service_lock.ServiceLockBusy):
                        cli.apply_output_migration(source, active, interrupted)
                current = cli.service_paths.load_path_transition()

        self.assertEqual(current.phase, cli.service_paths.PathTransitionPhase.APPLYING)

    def test_paths_migrate_rejects_applying_transition_root_mismatch(self) -> None:
        cli = load_module("paths_apply_root_mismatch_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            source = root / "A"
            active = root / "B"
            unexpected = root / "C"
            (source / "raw").mkdir(parents=True)
            (source / "raw" / "old.jsonl").write_text("{}\n", encoding="utf-8")
            with mock.patch.dict(cli.os.environ, {"XDG_CONFIG_HOME": str(root / "config")}, clear=True):
                cli.service_paths.write_config({"output_dir": source})
                cli.paths_set(argparse.Namespace(codex_dir=None, output_dir=str(active)), emit=False)
                requested = cli.service_paths.load_path_transition().begin_migration()
                persisted = cli.service_paths.PathTransition(
                    transition_id=requested.transition_id,
                    source_output_dir=unexpected,
                    active_output_dir=active,
                    created_at_ns=requested.created_at_ns,
                    phase=cli.service_paths.PathTransitionPhase.APPLYING,
                )
                cli.service_paths.write_path_transition(persisted)
                with self.assertRaisesRegex(cli.service_paths.ConfigurationError, "persisted output transition paths"):
                    cli.apply_output_migration(source, active, requested)
                current = cli.service_paths.load_path_transition()

        self.assertEqual(current, persisted)

    def test_temporary_migration_entrypoints_are_removed(self) -> None:
        cli = load_module("removed_temporary_migration_test", ROOT / "scripts" / "bola.py")

        self.assertFalse(hasattr(cli, "migrate_data"))
        self.assertFalse(hasattr(cli, "migrate_path"))

    def test_paths_show_ignores_legacy_data(self) -> None:
        cli = load_module("paths_legacy_migration_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            codex_dir = pathlib.Path(tmp_dir) / ".codex"
            (codex_dir / "token-usage" / "state").mkdir(parents=True)
            (codex_dir / "token-usage" / "state" / "pending.json").write_text("{}\n", encoding="utf-8")
            output_dir = pathlib.Path(tmp_dir) / "data"
            with mock.patch.dict(
                cli.os.environ,
                {"CODEX_HOME": str(codex_dir), "BOLA_OUTPUT_DIR": str(output_dir)},
                clear=True,
            ):
                report = cli.paths_report()

            self.assertFalse(report["output_transition"]["pending"])

    def test_hook_keeps_writing_active_output_when_legacy_data_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            codex_dir = root / ".codex"
            output_dir = root / "data"
            (codex_dir / "token-usage" / "state").mkdir(parents=True)
            stderr = io.StringIO()
            stdout = io.StringIO()
            with mock.patch.dict(
                os.environ,
                {"CODEX_HOME": str(codex_dir), "BOLA_OUTPUT_DIR": str(output_dir)},
                clear=False,
            ):
                hook = load_module("hook_migration_required_test", ROOT / "scripts" / "hook.py")
                with (
                    mock.patch.object(
                        hook.sys,
                        "stdin",
                        io.StringIO(
                            json.dumps(
                                {
                                    "hook_event_name": "Stop",
                                    "session_id": "s1",
                                    "turn_id": "t1",
                                    "transcript_path": str(root / "missing.jsonl"),
                                }
                            )
                        ),
                    ),
                    mock.patch.object(hook.sys, "stderr", stderr),
                    mock.patch.object(hook.sys, "stdout", stdout),
                ):
                    code = hook.main()

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertTrue(hook.service_paths.has_managed_data(output_dir))
            self.assertFalse((codex_dir / "codex-token-bola-migration-required.jsonl").exists())
