from __future__ import annotations

try:
    from tests.support import (
        Any,
        ROOT,
        json,
        load_module,
        mock,
        os,
        pathlib,
        subprocess,
        sys,
        tempfile,
        unittest,
    )
except ModuleNotFoundError:
    from support import (
        Any,
        ROOT,
        json,
        load_module,
        mock,
        os,
        pathlib,
        subprocess,
        sys,
        tempfile,
        unittest,
    )


class HookCaptureTests(unittest.TestCase):
    def test_goal_auto_stop_without_user_prompt_state_defers_lifecycle_scan(self) -> None:
        raw_segments = load_module("raw_segments_goal_auto_stop_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp:
            codex_dir = pathlib.Path(tmp) / ".codex"
            transcript = pathlib.Path(tmp) / "rollout.jsonl"
            session_id = "s-goal"
            turn_id = "t-goal"
            transcript.write_text(
                "\n".join(
                    json.dumps(row)
                    for row in [
                        {
                            "timestamp": "2026-05-31T10:00:00.000Z",
                            "type": "event_msg",
                            "payload": {"type": "task_started", "turn_id": turn_id, "started_at": 1780221600},
                        },
                        {
                            "timestamp": "2026-05-31T10:00:05.000Z",
                            "type": "event_msg",
                            "payload": {
                                "type": "token_count",
                                "info": {
                                    "last_token_usage": {
                                        "input_tokens": 100,
                                        "cached_input_tokens": 40,
                                        "output_tokens": 10,
                                        "reasoning_output_tokens": 2,
                                        "total_tokens": 110,
                                    },
                                    "total_token_usage": {
                                        "input_tokens": 1000,
                                        "cached_input_tokens": 400,
                                        "output_tokens": 100,
                                        "reasoning_output_tokens": 20,
                                        "total_tokens": 1100,
                                    },
                                },
                            },
                        },
                        {
                            "timestamp": "2026-05-31T10:00:08.000Z",
                            "type": "event_msg",
                            "payload": {
                                "type": "token_count",
                                "info": {
                                    "last_token_usage": {
                                        "input_tokens": 50,
                                        "cached_input_tokens": 20,
                                        "output_tokens": 5,
                                        "reasoning_output_tokens": 1,
                                        "total_tokens": 55,
                                    },
                                    "total_token_usage": {
                                        "input_tokens": 1050,
                                        "cached_input_tokens": 420,
                                        "output_tokens": 105,
                                        "reasoning_output_tokens": 21,
                                        "total_tokens": 1155,
                                    },
                                },
                            },
                        },
                        {
                            "timestamp": "2026-05-31T10:00:10.000Z",
                            "type": "event_msg",
                            "payload": {"type": "task_complete", "turn_id": turn_id, "completed_at": 1780221610},
                        },
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_dir), "BOLA_OUTPUT_DIR": str(codex_dir / "bola")}, clear=False):
                hook = load_module("hook_goal_auto_stop_test", ROOT / "scripts" / "hook.py")

            with (
                mock.patch.object(hook, "task_lifecycle_token_usage", side_effect=AssertionError("Stop hook must not scan full lifecycle")),
                mock.patch.object(hook, "latest_token_usage", side_effect=AssertionError("Stop hook must not scan latest token without start state")),
            ):
                hook.handle_stop(
                    {
                        "hook_event_name": "Stop",
                        "session_id": session_id,
                        "turn_id": turn_id,
                        "transcript_path": str(transcript),
                        "cwd": "/example/src/quant",
                        "model": "gpt-5.5",
                        "last_assistant_message": "done",
                    }
                )

            current = raw_segments.strict_read_current_pointer(codex_dir / "bola")["current"]["prompt_usage"]
            rows = [json.loads(line) for line in pathlib.Path(current["path"]).read_text(encoding="utf-8").splitlines()]

        self.assertEqual(len(rows), 1)
        record = rows[0]
        self.assertEqual(record["turn_status"], "incomplete")
        self.assertEqual(record["lifecycle_end_reason"], "missing_start_state")
        self.assertFalse(record["start_state_found"])
        self.assertTrue(record["estimated"])
        self.assertIsNone(record["started_at"])
        self.assertEqual(record["usage"]["total_tokens"], 0)
        self.assertEqual(record["end_token_snapshot"]["reason"], "missing_start_state_deferred")
        self.assertEqual(record["model_call_count"], 0)

    def test_stop_logs_raw_append_failure_for_missing_start_marker(self) -> None:
        hook = load_module("hook_stop_append_failure_goal_auto_test", ROOT / "scripts" / "hook.py")
        warnings: list[dict[str, Any]] = []

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(hook, "STATE_DIR", pathlib.Path(tmp) / "state"),
            mock.patch.object(hook, "task_lifecycle_token_usage", side_effect=AssertionError("Stop hook must not scan full lifecycle")),
            mock.patch.object(hook, "latest_token_usage", side_effect=AssertionError("Stop hook must not scan latest token without start state")),
            mock.patch.object(hook.turn_capture, "_append_prompt_usage_unlocked_result", return_value=False),
            mock.patch.object(hook, "safe_append_jsonl", side_effect=lambda _path, record: warnings.append(record) or True),
        ):
            hook.handle_stop({"session_id": "s-failed", "turn_id": "t-failed", "transcript_path": "/tmp/missing.jsonl"})
            marker_paths = sorted((pathlib.Path(tmp) / "state").glob("*.json"))
            marker = json.loads(marker_paths[0].read_text(encoding="utf-8")) if marker_paths else {}

        self.assertTrue(any(row.get("error") == "raw_append_failed" for row in warnings))
        self.assertEqual(len(marker_paths), 1)
        self.assertEqual(marker["record_type"], "turn_stop_missing_start")

    def test_stop_keeps_missing_start_marker_after_raw_append_succeeds(self) -> None:
        hook = load_module("hook_stop_success_marker_test", ROOT / "scripts" / "hook.py")
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = pathlib.Path(tmp) / "state"
            with (
                mock.patch.object(hook, "STATE_DIR", state_dir),
                mock.patch.object(hook.turn_capture, "_append_prompt_usage_unlocked_result", return_value=True),
            ):
                hook.handle_stop({"session_id": "s-success", "turn_id": "t-success", "transcript_path": "/tmp/rollout.jsonl"})
                marker = json.loads(hook.stop_missing_start_marker_path("s-success", "t-success").read_text(encoding="utf-8"))

        self.assertEqual(marker["record_type"], "turn_stop_missing_start")
        self.assertEqual(marker["pending_append_state"], "appended")

    def test_repeated_missing_start_stop_appends_pending_row_once(self) -> None:
        hook = load_module("hook_repeated_missing_start_stop_test", ROOT / "scripts" / "hook.py")
        appended: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = pathlib.Path(tmp) / "state"
            with (
                mock.patch.object(hook, "STATE_DIR", state_dir),
                mock.patch.object(
                    hook.turn_capture,
                    "_append_prompt_usage_unlocked_result",
                    side_effect=lambda record, **_kwargs: appended.append(record) or True,
                ),
            ):
                event = {"session_id": "s-repeat", "turn_id": "t-repeat", "transcript_path": "/tmp/rollout.jsonl"}
                hook.handle_stop(event)
                hook.handle_stop(event)
                marker = json.loads(
                    hook.stop_missing_start_marker_path("s-repeat", "t-repeat").read_text(encoding="utf-8")
                )

        self.assertEqual(len(appended), 1)
        self.assertEqual(marker["pending_append_state"], "appended")

    def test_repeated_normal_stop_creates_tombstone_without_pending_row(self) -> None:
        hook = load_module("hook_repeated_normal_stop_test", ROOT / "scripts" / "hook.py")
        raw_segments = load_module("raw_segments_repeated_normal_stop_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp) / "token-usage"
            state_dir = base / "state"
            transcript = pathlib.Path(tmp) / "rollout.jsonl"
            transcript.write_text("", encoding="utf-8")
            hook.BASE_DIR = base
            hook.STATE_DIR = state_dir
            hook.ERROR_LOG = base / "prompt-usage-errors.jsonl"
            hook.handle_start(
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "s-normal-repeat",
                    "turn_id": "t-normal-repeat",
                    "transcript_path": str(transcript),
                    "prompt": "hello",
                }
            )
            with transcript.open("a", encoding="utf-8") as handle:
                for row in (
                    {
                        "timestamp": "2026-09-02T10:00:01Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "info": {
                                "last_token_usage": {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
                                "total_token_usage": {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
                            },
                        },
                    },
                    {
                        "timestamp": "2026-09-02T10:00:02Z",
                        "type": "event_msg",
                        "payload": {"type": "task_complete", "turn_id": "t-normal-repeat"},
                    },
                ):
                    handle.write(json.dumps(row) + "\n")
            stop = {
                "hook_event_name": "Stop",
                "session_id": "s-normal-repeat",
                "turn_id": "t-normal-repeat",
                "transcript_path": str(transcript),
            }

            hook.handle_stop(stop)
            raw_segments.rotate_current_segment(
                base,
                kind="prompt_usage",
                source_name=raw_segments.PROMPT_RAW_NAME,
            )
            hook.handle_stop(stop)

            sources = [
                *raw_segments.manifest_segments(base, kind="prompt_usage"),
                *raw_segments.current_segment_paths(base, kind="prompt_usage"),
            ]
            records = [
                json.loads(line)
                for source in sources
                for line in pathlib.Path(source).read_text(encoding="utf-8").splitlines()
            ]
            tombstone_path = hook.stop_missing_start_marker_path("s-normal-repeat", "t-normal-repeat")
            tombstone = json.loads(tombstone_path.read_text(encoding="utf-8"))

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["token_resolution_status"], "resolved")
        self.assertEqual(tombstone["record_type"], "turn_finalized")
        self.assertEqual(tombstone["finalized_reason"], "existing_durable_terminal")

    def test_legacy_missing_start_marker_does_not_append_pending_row_again(self) -> None:
        hook = load_module("hook_legacy_missing_start_marker_test", ROOT / "scripts" / "hook.py")
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = pathlib.Path(tmp) / "state"
            marker_path = state_dir / f"{hook.safe_name('stop:s-legacy:t-legacy')}.json"
            marker_path.parent.mkdir(parents=True)
            marker_path.write_text(
                json.dumps(
                    {
                        "record_type": "turn_stop_missing_start",
                        "session_id": "s-legacy",
                        "turn_id": "t-legacy",
                    }
                ),
                encoding="utf-8",
            )
            with (
                mock.patch.object(hook, "STATE_DIR", state_dir),
                mock.patch.object(hook.turn_capture, "_append_prompt_usage_unlocked_result") as append_prompt_usage,
            ):
                hook.handle_stop({"session_id": "s-legacy", "turn_id": "t-legacy"})

        append_prompt_usage.assert_not_called()

    def test_stop_missing_start_writes_marker_when_raw_segment_manifest_is_corrupt(self) -> None:
        hook = load_module("hook_stop_manifest_error_marker_test", ROOT / "scripts" / "hook.py")
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = pathlib.Path(tmp) / "state"
            with (
                mock.patch.object(hook, "STATE_DIR", state_dir),
                mock.patch.object(hook.raw_segments, "ensure_current_segment", side_effect=hook.raw_segments.ManifestError("bad current pointer")),
            ):
                hook.handle_stop({"session_id": "s-corrupt", "turn_id": "t-corrupt", "transcript_path": "/tmp/rollout.jsonl"})

            marker_paths = sorted(state_dir.glob("*.json"))
            marker = json.loads(marker_paths[0].read_text(encoding="utf-8")) if marker_paths else {}

        self.assertEqual(len(marker_paths), 1)
        self.assertEqual(marker["record_type"], "turn_stop_missing_start")
        self.assertEqual(marker["pending_append_state"], "required")

    def test_stop_logs_raw_append_failure_for_missing_start_state_record(self) -> None:
        hook = load_module("hook_stop_append_failure_missing_start_test", ROOT / "scripts" / "hook.py")
        warnings: list[dict[str, Any]] = []

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(hook, "STATE_DIR", pathlib.Path(tmp) / "state"),
            mock.patch.object(hook, "task_lifecycle_token_usage", side_effect=AssertionError("Stop hook must not scan full lifecycle")),
            mock.patch.object(hook, "latest_token_usage", side_effect=AssertionError("Stop hook must not scan latest token without start state")),
            mock.patch.object(hook.turn_capture, "_append_prompt_usage_unlocked_result", return_value=False),
            mock.patch.object(hook, "safe_append_jsonl", side_effect=lambda _path, record: warnings.append(record) or True),
        ):
            hook.handle_stop({"session_id": "s-missing", "turn_id": "t-missing", "transcript_path": "/tmp/missing.jsonl"})

        self.assertTrue(any(row.get("error") == "raw_append_failed" for row in warnings))

    def test_start_hook_uses_tail_snapshot_without_forward_scan(self) -> None:
        hook = load_module("hook_start_tail_snapshot_test", ROOT / "scripts" / "hook.py")
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = pathlib.Path(tmp) / "state"
            transcript = pathlib.Path(tmp) / "rollout.jsonl"
            transcript.write_text(
                json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 7, "total_tokens": 7}}}})
                + "\n",
                encoding="utf-8",
            )
            transcript_size = transcript.stat().st_size

            with (
                mock.patch.object(hook, "STATE_DIR", state_dir),
                mock.patch.object(hook, "latest_token_usage", side_effect=AssertionError("UserPromptSubmit hook must not scan from transcript start")),
            ):
                hook.handle_start({"session_id": "s-start", "turn_id": "t-start", "transcript_path": str(transcript)})

            state = json.loads(next(state_dir.glob("*.json")).read_text(encoding="utf-8"))

        self.assertEqual(state["start_file_size"], transcript_size)
        self.assertEqual(state["start_token_usage"]["total_tokens"], 7)
        self.assertEqual(state["start_usage_source"], "tail_token_count")

    def test_stop_with_invalid_start_offset_defers_without_full_scan(self) -> None:
        hook = load_module("hook_invalid_start_offset_test", ROOT / "scripts" / "hook.py")
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = pathlib.Path(tmp) / "state"
            transcript = pathlib.Path(tmp) / "rollout.jsonl"
            transcript.write_text("", encoding="utf-8")
            session_id = "s-invalid-offset"
            turn_id = "t-invalid-offset"
            state_dir.mkdir(parents=True)
            state_path = state_dir / f"{hook.safe_name(session_id + ':' + turn_id)}.json"
            state_path.write_text(
                json.dumps(
                    {
                        "record_type": "turn_start",
                        "session_id": session_id,
                        "turn_id": turn_id,
                        "transcript_path": str(transcript),
                        "captured_at": "2026-05-31T10:00:00+00:00",
                        "start_file_size": 999,
                        "start_token_usage": {"input_tokens": 0, "total_tokens": 0},
                    }
                ),
                encoding="utf-8",
            )
            warnings: list[dict[str, Any]] = []

            with (
                mock.patch.object(hook, "STATE_DIR", state_dir),
                mock.patch.object(hook, "latest_token_usage", side_effect=AssertionError("Stop hook must not scan when offset is invalid")),
                mock.patch.object(hook, "safe_append_jsonl", side_effect=lambda _path, record: warnings.append(record) or True),
            ):
                hook.handle_stop({"session_id": session_id, "turn_id": turn_id, "transcript_path": str(transcript)})
                state_exists = state_path.exists()

        self.assertTrue(state_exists)
        self.assertTrue(any(row.get("warning") == "deferred_stop_recovery" and row.get("reason") == "invalid_start_file_size" for row in warnings))

    def test_stop_hook_bounds_token_usage_to_current_turn_terminal_event(self) -> None:
        raw_segments = load_module("raw_segments_stop_bounds_turn_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp:
            codex_dir = pathlib.Path(tmp) / ".codex"
            base = codex_dir / "bola"
            transcript = pathlib.Path(tmp) / "rollout.jsonl"

            def event(payload: dict[str, Any]) -> str:
                return json.dumps({"timestamp": "2026-05-31T10:00:00.000Z", "type": "event_msg", "payload": payload}) + "\n"

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
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_dir), "BOLA_OUTPUT_DIR": str(codex_dir / "bola")}, clear=False):
                hook = load_module("hook_stop_bounds_turn_test", ROOT / "scripts" / "hook.py")
            hook.handle_start({"session_id": "s1", "turn_id": "t1", "transcript_path": str(transcript), "cwd": "/tmp"})
            state_path = hook.state_path("s1", "t1")
            start_state = json.loads(state_path.read_text(encoding="utf-8"))
            start_state["prompt"]["instruction_excerpt"] = "old excerpt"
            start_state["prompt"]["instruction_excerpt_chars"] = 11
            state_path.write_text(json.dumps(start_state), encoding="utf-8")
            with transcript.open("a", encoding="utf-8") as handle:
                handle.write(event({"type": "task_started", "turn_id": "t1"}))
                handle.write(
                    event(
                        {
                            "type": "token_count",
                            "info": {
                                "total_token_usage": {"input_tokens": 100, "total_tokens": 100},
                                "last_token_usage": {"input_tokens": 100, "total_tokens": 100},
                            },
                        }
                    )
                )
                handle.write(event({"type": "task_complete", "turn_id": "t1"}))
                handle.write(event({"type": "task_started", "turn_id": "t2"}))
                handle.write(
                    event(
                        {
                            "type": "token_count",
                            "info": {
                                "total_token_usage": {"input_tokens": 150, "total_tokens": 150},
                                "last_token_usage": {"input_tokens": 50, "total_tokens": 50},
                            },
                        }
                    )
                )
            hook.handle_stop({"session_id": "s1", "turn_id": "t1", "transcript_path": str(transcript), "cwd": "/tmp"})

            current = raw_segments.strict_read_current_pointer(base)["current"]["prompt_usage"]
            rows = [json.loads(line) for line in pathlib.Path(current["path"]).read_text(encoding="utf-8").splitlines()]

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["usage"]["total_tokens"], 100)
        self.assertEqual(rows[0]["end_token_usage"]["total_tokens"], 100)
        self.assertEqual(rows[0]["model_call_count"], 1)
        self.assertNotIn("instruction_excerpt", rows[0]["prompt"])
        self.assertNotIn("instruction_excerpt_chars", rows[0]["prompt"])

    def test_stop_hook_preserves_abort_status_reason_and_timestamp(self) -> None:
        raw_segments = load_module("raw_segments_stop_abort_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp:
            codex_dir = pathlib.Path(tmp) / ".codex"
            base = codex_dir / "bola"
            transcript = pathlib.Path(tmp) / "rollout.jsonl"

            def event(payload: dict[str, Any], timestamp: str) -> str:
                return json.dumps({"timestamp": timestamp, "type": "event_msg", "payload": payload}) + "\n"

            transcript.write_text(
                event({"type": "token_count", "info": {"total_token_usage": {"total_tokens": 0}}}, "2026-05-31T10:00:00Z"),
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_dir), "BOLA_OUTPUT_DIR": str(base)}, clear=False):
                hook = load_module("hook_stop_abort_test", ROOT / "scripts" / "hook.py")
            hook.handle_start({"session_id": "s-abort", "turn_id": "t-abort", "transcript_path": str(transcript)})
            with transcript.open("a", encoding="utf-8") as handle:
                handle.write(event({"type": "task_started", "turn_id": "t-abort"}, "2026-05-31T10:00:01Z"))
                handle.write(event({"type": "token_count", "info": {"total_token_usage": {"input_tokens": 12, "total_tokens": 12}, "last_token_usage": {"input_tokens": 12, "total_tokens": 12}}}, "2026-05-31T10:00:02Z"))
                handle.write(event({"type": "task_aborted", "turn_id": "t-abort", "reason": "cancelled", "aborted_at": 1780221603}, "2026-05-31T10:00:03Z"))
            hook.handle_stop({"session_id": "s-abort", "turn_id": "t-abort", "transcript_path": str(transcript)})

            current = raw_segments.strict_read_current_pointer(base)["current"]["prompt_usage"]
            record = json.loads(pathlib.Path(current["path"]).read_text(encoding="utf-8"))

        self.assertEqual(record["turn_status"], "aborted")
        self.assertEqual(record["lifecycle_end_reason"], "cancelled")
        self.assertEqual(record["stopped_at"], "2026-05-31T10:00:03+00:00")

    def test_stop_hook_defers_when_token_count_exists_without_turn_end_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex_dir = pathlib.Path(tmp) / ".codex"
            base = codex_dir / "bola"
            transcript = pathlib.Path(tmp) / "rollout.jsonl"

            def event(payload: dict[str, Any]) -> str:
                return json.dumps({"timestamp": "2026-05-31T10:00:00.000Z", "type": "event_msg", "payload": payload}) + "\n"

            transcript.write_text(event({"type": "token_count", "info": {"total_token_usage": {"input_tokens": 0, "total_tokens": 0}}}), encoding="utf-8")
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_dir), "BOLA_OUTPUT_DIR": str(codex_dir / "bola")}, clear=False):
                hook = load_module("hook_stop_turn_end_missing_test", ROOT / "scripts" / "hook.py")
            hook.handle_start({"session_id": "s-no-end", "turn_id": "t-no-end", "transcript_path": str(transcript), "cwd": "/tmp"})
            state_path = hook.state_path("s-no-end", "t-no-end")
            with transcript.open("a", encoding="utf-8") as handle:
                handle.write(
                    event(
                        {
                            "type": "token_count",
                            "info": {
                                "total_token_usage": {"input_tokens": 100, "total_tokens": 100},
                                "last_token_usage": {"input_tokens": 100, "total_tokens": 100},
                            },
                        }
                    )
                )
            warnings: list[dict[str, Any]] = []

            with mock.patch.object(hook, "safe_append_jsonl", side_effect=lambda _path, record: warnings.append(record) or True):
                hook.handle_stop({"session_id": "s-no-end", "turn_id": "t-no-end", "transcript_path": str(transcript), "cwd": "/tmp"})

            raw_rows = list((base / "raw" / "current").glob("*.jsonl"))
            state_exists = state_path.exists()

        self.assertTrue(state_exists)
        self.assertEqual(raw_rows, [])
        self.assertTrue(any(row.get("warning") == "deferred_stop_recovery" and row.get("reason") == "turn_end_not_found" for row in warnings))

    def test_stop_hook_captures_when_turn_end_precedes_forward_scan_limit(self) -> None:
        raw_segments = load_module("raw_segments_stop_terminal_limit_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp:
            codex_dir = pathlib.Path(tmp) / ".codex"
            base = codex_dir / "bola"
            transcript = pathlib.Path(tmp) / "rollout.jsonl"

            def event(payload: dict[str, Any]) -> str:
                return json.dumps({"timestamp": "2026-05-31T10:00:00.000Z", "type": "event_msg", "payload": payload}) + "\n"

            transcript.write_text(event({"type": "token_count", "info": {"total_token_usage": {"input_tokens": 0, "total_tokens": 0}}}), encoding="utf-8")
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_dir), "BOLA_OUTPUT_DIR": str(codex_dir / "bola")}, clear=False):
                hook = load_module("hook_stop_terminal_limit_test", ROOT / "scripts" / "hook.py")
            hook.handle_start({"session_id": "s-limit", "turn_id": "t-limit", "transcript_path": str(transcript), "cwd": "/tmp"})
            state_path = hook.state_path("s-limit", "t-limit")
            with transcript.open("a", encoding="utf-8") as handle:
                handle.write(
                    event(
                        {
                            "type": "token_count",
                            "info": {
                                "total_token_usage": {"input_tokens": 42, "total_tokens": 42},
                                "last_token_usage": {"input_tokens": 42, "total_tokens": 42},
                            },
                        }
                    )
                )
                handle.write(event({"type": "task_complete", "turn_id": "t-limit"}))
                handle.write(
                    event(
                        {
                            "type": "token_count",
                            "info": {
                                "total_token_usage": {"input_tokens": 999, "total_tokens": 999},
                                "last_token_usage": {"input_tokens": 957, "total_tokens": 957},
                                "padding": "x" * 500,
                            },
                        }
                    )
                )

            with mock.patch.object(hook, "HOOK_FORWARD_SCAN_BYTES", 420):
                hook.handle_stop({"session_id": "s-limit", "turn_id": "t-limit", "transcript_path": str(transcript), "cwd": "/tmp"})

            current = raw_segments.strict_read_current_pointer(base)["current"]["prompt_usage"]
            rows = [json.loads(line) for line in pathlib.Path(current["path"]).read_text(encoding="utf-8").splitlines()]

        self.assertFalse(state_path.exists())
        self.assertEqual(rows[0]["usage"]["total_tokens"], 42)

    def test_stop_with_unavailable_start_usage_sums_post_start_model_calls(self) -> None:
        raw_segments = load_module("raw_segments_unavailable_start_usage_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp:
            codex_dir = pathlib.Path(tmp) / ".codex"
            base = codex_dir / "bola"
            state_dir = base / "state"
            transcript = pathlib.Path(tmp) / "rollout.jsonl"
            prefix = (
                json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 900, "total_tokens": 900}}}})
                + "\n"
            )
            transcript.write_text(prefix, encoding="utf-8")
            start_file_size = transcript.stat().st_size
            transcript.write_text(
                prefix
                + json.dumps({"type": "event_msg", "payload": {"type": "task_started", "turn_id": "t-unavailable"}})
                + "\n"
                + json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "info": {
                                "last_token_usage": {
                                    "input_tokens": 11,
                                    "cached_input_tokens": 4,
                                    "output_tokens": 3,
                                    "reasoning_output_tokens": 1,
                                    "total_tokens": 14,
                                },
                                "total_token_usage": {
                                    "input_tokens": 911,
                                    "cached_input_tokens": 4,
                                    "output_tokens": 3,
                                    "reasoning_output_tokens": 1,
                                    "total_tokens": 914,
                                },
                            },
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with transcript.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "t-unavailable"}}) + "\n")
            session_id = "s-unavailable"
            turn_id = "t-unavailable"
            state_dir.mkdir(parents=True)

            with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_dir), "BOLA_OUTPUT_DIR": str(codex_dir / "bola")}, clear=False):
                hook = load_module("hook_unavailable_start_usage_test", ROOT / "scripts" / "hook.py")
            hook.state_path(session_id, turn_id).write_text(
                json.dumps(
                    {
                        "record_type": "turn_start",
                        "session_id": session_id,
                        "turn_id": turn_id,
                        "transcript_path": str(transcript),
                        "captured_at": "2026-05-31T10:00:00+00:00",
                        "start_file_size": start_file_size,
                        "start_token_usage": {"input_tokens": 0, "total_tokens": 0},
                        "start_usage_source": "unavailable",
                    }
                ),
                encoding="utf-8",
            )

            hook.handle_stop({"session_id": session_id, "turn_id": turn_id, "transcript_path": str(transcript)})

            current = raw_segments.strict_read_current_pointer(base)["current"]["prompt_usage"]
            records = [json.loads(line) for line in pathlib.Path(current["path"]).read_text(encoding="utf-8").splitlines()]

        self.assertEqual(records[0]["usage"]["total_tokens"], 14)
        self.assertTrue(records[0]["estimated"])
        self.assertEqual(records[0]["token_source"], "transcript_path token_count.info.last_token_usage aggregate after start offset")

    def test_transcript_event_stream_tracks_offsets_and_parse_errors(self) -> None:
        parser = load_module("transcript_parser_offsets_test", ROOT / "scripts" / "transcript_parser.py")
        with tempfile.TemporaryDirectory() as tmp:
            transcript = pathlib.Path(tmp) / "rollout.jsonl"
            first = json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": {}}}) + "\n"
            bad = "{bad json\n"
            non_object = "[]\n"
            transcript.write_text(
                first + bad + non_object + json.dumps({"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "t1"}}) + "\n",
                encoding="utf-8",
            )

            stream, error = parser.transcript_event_stream(transcript, len(first))
            events = list(stream)

        self.assertIsNone(error)
        self.assertTrue(stream.parse_error_seen)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["line_start"], len(first) + len(bad) + len(non_object))
        self.assertEqual(events[0]["item"]["payload"]["type"], "task_complete")

    def test_latest_token_usage_respects_max_bytes_as_hard_cap(self) -> None:
        hook = load_module("hook_latest_token_hard_cap_test", ROOT / "scripts" / "hook.py")
        with tempfile.TemporaryDirectory() as tmp:
            transcript = pathlib.Path(tmp) / "rollout.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "info": {
                                "total_token_usage": {"input_tokens": 100, "total_tokens": 100},
                                "last_token_usage": {"input_tokens": 100, "total_tokens": 100},
                                "padding": "x" * 200,
                            },
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = hook.latest_token_usage(str(transcript), offset=0, max_bytes=20)

        self.assertFalse(result.get("found"))
        self.assertTrue(result.get("scan_limit_reached"))

    def test_hook_keeps_start_state_after_pending_token_count_record(self) -> None:
        raw_segments = load_module("raw_segments_pending_token_count_state_cleanup_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp:
            codex_dir = pathlib.Path(tmp) / ".codex"
            transcript = pathlib.Path(tmp) / "rollout.jsonl"
            transcript.write_text("", encoding="utf-8")
            session_id = "s-pending"
            turn_id = "t-pending"
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_dir), "BOLA_OUTPUT_DIR": str(codex_dir / "bola")}, clear=False):
                hook = load_module("hook_pending_token_count_state_cleanup_test", ROOT / "scripts" / "hook.py")
            hook.handle_start(
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "transcript_path": str(transcript),
                    "cwd": "/example/.codex/codex-token-bola",
                    "model": "gpt-5.5",
                    "prompt": "pending token count",
                }
            )
            state_path = hook.state_path(session_id, turn_id)
            self.assertTrue(state_path.exists())

            hook.handle_stop(
                {
                    "hook_event_name": "Stop",
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "transcript_path": str(transcript),
                    "cwd": "/example/.codex/codex-token-bola",
                    "model": "gpt-5.5",
                }
            )

            current = raw_segments.strict_read_current_pointer(codex_dir / "bola")["current"]["prompt_usage"]
            rows = [json.loads(line) for line in pathlib.Path(current["path"]).read_text(encoding="utf-8").splitlines()]
            pending_state = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertIsNone(rows[0]["lifecycle_end_reason"])
        self.assertEqual(rows[0]["token_resolution_status"], "pending")
        self.assertEqual(rows[0]["token_resolution_reason"], "turn_end_not_found")
        self.assertEqual(pending_state["token_resolution_status"], "pending")
        self.assertEqual(pending_state["token_resolution_reason"], "turn_end_not_found")

    def test_hook_keeps_start_state_when_stop_has_no_transcript_path(self) -> None:
        raw_segments = load_module("raw_segments_missing_transcript_state_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp:
            codex_dir = pathlib.Path(tmp) / ".codex"
            session_id = "s-missing-transcript"
            turn_id = "t-missing-transcript"
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_dir), "BOLA_OUTPUT_DIR": str(codex_dir / "bola")}, clear=False):
                hook = load_module("hook_missing_transcript_state_test", ROOT / "scripts" / "hook.py")
            hook.handle_start(
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "transcript_path": None,
                    "cwd": "/example/.codex/codex-token-bola",
                    "model": "gpt-5.5",
                    "prompt": "missing transcript",
                }
            )
            state_path = hook.state_path(session_id, turn_id)
            self.assertTrue(state_path.exists())

            hook.handle_stop(
                {
                    "hook_event_name": "Stop",
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "transcript_path": None,
                    "cwd": "/example/.codex/codex-token-bola",
                    "model": "gpt-5.5",
                }
            )
            hook.handle_stop(
                {
                    "hook_event_name": "Stop",
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "transcript_path": None,
                    "cwd": "/example/.codex/codex-token-bola",
                    "model": "gpt-5.5",
                }
            )
            base = codex_dir / "bola"
            current_paths = raw_segments.current_segment_paths(base, kind="prompt_usage")
            error_text = (base / "prompt-usage-errors.jsonl").read_text(encoding="utf-8")
            state_exists = state_path.exists()

        self.assertTrue(state_exists)
        self.assertEqual(current_paths, [])
        self.assertEqual(error_text.count("deferred_stop_recovery"), 2)
        self.assertEqual(error_text.count("invalid_start_file_size"), 2)

    def test_package_hook_module_writes_to_configured_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            output_dir = root / "data"
            env = {
                **os.environ,
                "XDG_CONFIG_HOME": str(root / "config"),
                "BOLA_OUTPUT_DIR": str(output_dir),
            }
            result = subprocess.run(
                [sys.executable, "-m", "codex_token_bola.hook", "--bola-hook"],
                cwd=ROOT,
                env=env,
                input=json.dumps({"hook_event_name": "Unsupported"}),
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {"continue": True, "suppressOutput": True})
            error_rows = [json.loads(line) for line in (output_dir / "prompt-usage-errors.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(error_rows[-1]["error"], "unsupported event")
