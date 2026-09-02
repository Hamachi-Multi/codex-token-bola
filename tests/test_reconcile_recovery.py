from __future__ import annotations

try:
    from tests.support import (
        ROOT,
        _turn_raw,
        json,
        load_module,
        mock,
        pathlib,
        tempfile,
        unittest,
    )
except ModuleNotFoundError:
    from support import (
        ROOT,
        _turn_raw,
        json,
        load_module,
        mock,
        pathlib,
        tempfile,
        unittest,
    )


class ReconcileRecoveryTests(unittest.TestCase):
    def test_reconcile_quarantines_non_object_json_state(self) -> None:
        reconcile = load_module("reconcile_non_object_state_test", ROOT / "scripts" / "reconcile.py")
        with tempfile.TemporaryDirectory() as tmp:
            pending = pathlib.Path(tmp) / "pending.json"
            pending.write_text("[]\n", encoding="utf-8")
            with mock.patch.object(reconcile, "move_bad_state") as move_bad:
                result = reconcile.reconcile_one(pending, set())

        self.assertEqual(result, "bad")
        move_bad.assert_called_once()

    def test_reconcile_completed_index_reads_current_segments(self) -> None:
        reconcile = load_module("reconcile_current_segment_index_test", ROOT / "scripts" / "reconcile.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp) / "token-usage"
            raw_dir = base / "raw"
            current_dir = raw_dir / "current"
            state_dir = base / "state"
            current_dir.mkdir(parents=True)
            state_dir.mkdir(parents=True)
            current_path = current_dir / "prompt-usage.raw.jsonl.current.1779235200000000000.jsonl"
            current_path.write_text(json.dumps(_turn_raw("s-current", "t-current", total=100)) + "\n", encoding="utf-8")
            pointer = {
                "schema_version": 1,
                "base": str(base.resolve()),
                "current": {
                    "prompt_usage": {
                        "id": "prompt-usage.raw.jsonl.current.1779235200000000000",
                        "kind": "prompt_usage",
                        "path": str(current_path),
                        "source_name": "prompt-usage.raw.jsonl",
                        "created_at_unix": 1779235200.0,
                    }
                },
            }
            (state_dir / "current-raw-segments.json").write_text(json.dumps(pointer) + "\n", encoding="utf-8")
            reconcile.BASE_DIR = base
            reconcile.STATE_DIR = state_dir
            reconcile.RAW_LOG = raw_dir / "prompt-usage.raw.jsonl"
            reconcile.ARCHIVE_DIR = raw_dir / "archive"

            completed = reconcile.completed_turn_index()

        self.assertIn(("s-current", "t-current"), completed)

    def test_reconcile_iter_jsonl_raises_on_read_error(self) -> None:
        reconcile = load_module("reconcile_iter_jsonl_read_error_test", ROOT / "scripts" / "reconcile.py")
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "prompt-usage.raw.jsonl"
            path.write_text(json.dumps(_turn_raw("s1", "t1", total=1)) + "\n", encoding="utf-8")

            with mock.patch("builtins.open", side_effect=OSError("read blocked")):
                with self.assertRaises(OSError):
                    list(reconcile.iter_jsonl(path))

    def test_reconcile_writes_recovered_turn_to_current_segment(self) -> None:
        reconcile = load_module("reconcile_append_current_segment_test", ROOT / "scripts" / "reconcile.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp) / "token-usage"
            state_dir = base / "state"
            state_dir.mkdir(parents=True)
            transcript = pathlib.Path(tmp) / "rollout.jsonl"
            transcript.write_text(
                "\n".join(
                    json.dumps(row)
                    for row in [
                        {
                            "timestamp": "2026-05-31T10:00:00.000Z",
                            "type": "event_msg",
                            "payload": {
                                "type": "token_count",
                                "info": {
                                    "total_token_usage": {"input_tokens": 15, "total_tokens": 15},
                                    "last_token_usage": {"input_tokens": 15, "total_tokens": 15},
                                },
                            },
                        },
                        {
                            "timestamp": "2026-05-31T10:00:01.000Z",
                            "type": "event_msg",
                            "payload": {"type": "task_complete", "turn_id": "t-recovered", "completed_at": 1780221601},
                        },
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            pending = state_dir / "pending.json"
            pending.write_text(
                json.dumps(
                    {
                        "record_type": "turn_start",
                        "session_id": "s-recovered",
                        "turn_id": "t-recovered",
                        "cwd": str(pathlib.Path(tmp)),
                        "transcript_path": str(transcript),
                        "captured_at": "2026-05-31T10:00:00+00:00",
                        "start_token_usage": {"input_tokens": 0, "total_tokens": 0},
                        "prompt": {
                            "prompt_preview": "old prompt",
                            "instruction_excerpt": "old excerpt",
                            "instruction_excerpt_chars": 11,
                        },
                    }
                ),
                encoding="utf-8",
            )
            reconcile.CODEX_DIR = pathlib.Path(tmp)
            reconcile.BASE_DIR = base
            reconcile.STATE_DIR = state_dir
            reconcile.RAW_LOG = base / "raw" / "prompt-usage.raw.jsonl"
            reconcile.ARCHIVE_DIR = base / "raw" / "archive"

            completed = set()
            result = reconcile.reconcile_one(pending, completed)
            current_paths = reconcile.raw_segments.current_segment_paths(base, kind="prompt_usage")
            current_payload = current_paths[0].read_text(encoding="utf-8") if current_paths else ""

            self.assertEqual(result, "completed")
            self.assertIn(("s-recovered", "t-recovered"), completed)
            self.assertFalse((base / "raw" / "prompt-usage.raw.jsonl").exists())
            self.assertEqual(len(current_paths), 1)
            self.assertIn('"session_id":"s-recovered"', current_payload)
            recovered = json.loads(current_payload)
            self.assertNotIn("instruction_excerpt", recovered["prompt"])
            self.assertNotIn("instruction_excerpt_chars", recovered["prompt"])

    def test_reconcile_with_unavailable_start_usage_uses_model_call_delta(self) -> None:
        reconcile = load_module("reconcile_unavailable_start_usage_test", ROOT / "scripts" / "reconcile.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp) / "token-usage"
            state_dir = base / "state"
            state_dir.mkdir(parents=True)
            pending = state_dir / "pending.json"
            pending.write_text(
                json.dumps(
                    {
                        "record_type": "turn_start",
                        "session_id": "s-unavailable",
                        "turn_id": "t-unavailable",
                        "transcript_path": str(pathlib.Path(tmp) / "rollout.jsonl"),
                        "captured_at": "2026-05-31T10:00:00+00:00",
                        "start_token_usage": {"input_tokens": 0, "total_tokens": 0},
                        "start_usage_source": "unavailable",
                    }
                ),
                encoding="utf-8",
            )
            reconcile.BASE_DIR = base
            reconcile.STATE_DIR = state_dir
            snapshot = {
                "found": True,
                "total_token_usage": {"input_tokens": 911, "cached_input_tokens": 4, "output_tokens": 3, "total_tokens": 914},
                "model_calls": [
                    {"usage": {"input_tokens": 11, "cached_input_tokens": 4, "output_tokens": 3, "total_tokens": 14}}
                ],
            }
            with mock.patch.object(
                reconcile,
                "latest_token_until_turn_end",
                return_value=(snapshot, {"type": "task_complete", "turn_id": "t-unavailable"}),
            ):
                result = reconcile.reconcile_one(pending, set())
            current = reconcile.raw_segments.current_segment_paths(base, kind="prompt_usage")
            record = json.loads(current[0].read_text(encoding="utf-8"))

        self.assertEqual(result, "completed")
        self.assertEqual(record["usage"]["total_tokens"], 14)
        self.assertTrue(record["estimated"])
        self.assertEqual(record["token_source"], "transcript_path token_count.info.last_token_usage aggregate after start offset")

    def test_reconcile_rechecks_current_segments_before_append(self) -> None:
        reconcile = load_module("reconcile_append_race_recheck_test", ROOT / "scripts" / "reconcile.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp) / "token-usage"
            state_dir = base / "state"
            state_dir.mkdir(parents=True)
            current = reconcile.raw_segments.ensure_current_segment(base, kind="prompt_usage", source_name="prompt-usage.raw.jsonl")
            pathlib.Path(current["path"]).write_text(json.dumps(_turn_raw("s-race", "t-race", total=10)) + "\n", encoding="utf-8")
            pending = state_dir / "pending.json"
            pending.write_text(
                json.dumps(
                    {
                        "record_type": "turn_start",
                        "session_id": "s-race",
                        "turn_id": "t-race",
                        "cwd": str(pathlib.Path(tmp)),
                        "transcript_path": str(pathlib.Path(tmp) / "rollout.jsonl"),
                        "captured_at": "2026-05-31T10:00:00+00:00",
                        "start_token_usage": {"input_tokens": 0, "total_tokens": 0},
                    }
                ),
                encoding="utf-8",
            )
            reconcile.BASE_DIR = base
            reconcile.STATE_DIR = state_dir
            with (
                mock.patch.object(
                    reconcile,
                    "latest_token_until_turn_end",
                    return_value=(
                        {"found": True, "total_token_usage": {"input_tokens": 10, "total_tokens": 10}, "model_calls": []},
                        {"type": "task_complete"},
                    ),
                ),
                mock.patch.object(reconcile, "completed_turn_index", side_effect=AssertionError("reconcile_one must not rebuild the full completed index")),
                mock.patch.object(
                    reconcile.turn_capture,
                    "append_prompt_usage",
                    side_effect=AssertionError("duplicate turn must not append"),
                ),
            ):
                completed = set()
                result = reconcile.reconcile_one(pending, completed)

        self.assertEqual(result, "duplicate")
        self.assertIn(("s-race", "t-race"), completed)

    def test_reconcile_recovers_missing_start_stop_marker(self) -> None:
        reconcile = load_module("reconcile_missing_start_stop_marker_test", ROOT / "scripts" / "reconcile.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp) / "token-usage"
            state_dir = base / "state"
            state_dir.mkdir(parents=True)
            transcript = pathlib.Path(tmp) / "rollout.jsonl"
            transcript.write_text(
                "\n".join(
                    json.dumps(row)
                    for row in [
                        {"timestamp": "2026-05-31T10:00:00.000Z", "type": "event_msg", "payload": {"type": "task_started", "turn_id": "t-marker"}},
                        {
                            "timestamp": "2026-05-31T10:00:01.000Z",
                            "type": "event_msg",
                            "payload": {
                                "type": "token_count",
                                "info": {
                                    "last_token_usage": {
                                        "input_tokens": 10,
                                        "cached_input_tokens": 3,
                                        "output_tokens": 2,
                                        "reasoning_output_tokens": 1,
                                        "total_tokens": 12,
                                    }
                                },
                            },
                        },
                        {"timestamp": "2026-05-31T10:00:02.000Z", "type": "event_msg", "payload": {"type": "task_complete", "turn_id": "t-marker"}},
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            marker = state_dir / "marker.json"
            marker.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "record_type": "turn_stop_missing_start",
                        "captured_at": "2026-05-31T10:00:03+00:00",
                        "session_id": "s-marker",
                        "turn_id": "t-marker",
                        "transcript_path": str(transcript),
                        "cwd": str(pathlib.Path(tmp)),
                        "model": "gpt-5.5",
                        "hook_input": {"hook_event_name": "Stop"},
                    }
                ),
                encoding="utf-8",
            )
            reconcile.CODEX_DIR = pathlib.Path(tmp)
            reconcile.BASE_DIR = base
            reconcile.STATE_DIR = state_dir
            reconcile.RAW_LOG = base / "raw" / "prompt-usage.raw.jsonl"
            reconcile.ARCHIVE_DIR = base / "raw" / "archive"

            result = reconcile.reconcile_one(marker, set())
            current_paths = reconcile.raw_segments.current_segment_paths(base, kind="prompt_usage")
            records = [json.loads(line) for line in current_paths[0].read_text(encoding="utf-8").splitlines()]
            marker_exists = marker.exists()
            finalized_marker = json.loads(marker.read_text(encoding="utf-8"))

        self.assertEqual(result, "completed")
        self.assertTrue(marker_exists)
        self.assertEqual(finalized_marker["record_type"], "turn_finalized")
        self.assertEqual(finalized_marker["terminal_append_state"], "appended")
        self.assertEqual(records[0]["turn_status"], "completed")
        self.assertEqual(records[0]["lifecycle_end_reason"], "goal_auto_completed")
        self.assertFalse(records[0]["start_state_found"])
        self.assertEqual(records[0]["usage"]["total_tokens"], 12)
        self.assertEqual(records[0]["token_resolution_status"], "resolved")

    def test_reconcile_marks_missing_start_terminal_without_tokens_unavailable(self) -> None:
        reconcile = load_module("reconcile_missing_start_without_tokens_test", ROOT / "scripts" / "reconcile.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp) / "token-usage"
            state_dir = base / "state"
            state_dir.mkdir(parents=True)
            transcript = pathlib.Path(tmp) / "rollout.jsonl"
            transcript.write_text(
                "\n".join(
                    json.dumps(row)
                    for row in [
                        {
                            "timestamp": "2026-05-31T10:00:00.000Z",
                            "type": "event_msg",
                            "payload": {"type": "task_started", "turn_id": "t-no-tokens"},
                        },
                        {
                            "timestamp": "2026-05-31T10:00:01.000Z",
                            "type": "event_msg",
                            "payload": {"type": "token_count", "info": {}},
                        },
                        {
                            "timestamp": "2026-05-31T10:00:02.000Z",
                            "type": "event_msg",
                            "payload": {"type": "task_complete", "turn_id": "t-no-tokens"},
                        },
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            marker = state_dir / "marker.json"
            marker.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "record_type": "turn_stop_missing_start",
                        "pending_append_state": "appended",
                        "session_id": "s-no-tokens",
                        "turn_id": "t-no-tokens",
                        "transcript_path": str(transcript),
                    }
                ),
                encoding="utf-8",
            )
            reconcile.BASE_DIR = base
            reconcile.STATE_DIR = state_dir
            reconcile.QUARANTINE_RESULTS.clear()
            quarantine_result = {"event_id": "unavailable", "new_event": True, "acknowledged": False}
            with mock.patch.object(
                reconcile.quarantine_health,
                "record_unavailable",
                return_value=quarantine_result,
            ) as record_unavailable:
                result = reconcile.reconcile_one(marker, set())
            current_paths = reconcile.raw_segments.current_segment_paths(base, kind="prompt_usage")
            records = [json.loads(line) for line in current_paths[0].read_text(encoding="utf-8").splitlines()]

        self.assertEqual(result, "unavailable")
        self.assertEqual(records[0]["token_resolution_status"], "unavailable")
        self.assertEqual(records[0]["token_resolution_reason"], "no_token_count_before_task_complete")
        self.assertEqual(records[0]["model_call_count"], 0)
        record_unavailable.assert_called_once()
        self.assertEqual(reconcile.QUARANTINE_RESULTS, [quarantine_result])

    def test_missing_start_without_transcript_keeps_excluded_tombstone_against_late_stop(self) -> None:
        hook = load_module("hook_missing_start_excluded_tombstone_test", ROOT / "scripts" / "hook.py")
        reconcile = load_module("reconcile_missing_start_excluded_tombstone_test", ROOT / "scripts" / "reconcile.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp) / "token-usage"
            state_dir = base / "state"
            hook.BASE_DIR = base
            hook.STATE_DIR = state_dir
            hook.ERROR_LOG = base / "prompt-usage-errors.jsonl"
            reconcile.BASE_DIR = base
            reconcile.STATE_DIR = state_dir
            event = {"hook_event_name": "Stop", "session_id": "s-excluded", "turn_id": "t-excluded"}

            hook.handle_stop(event)
            marker = hook.stop_missing_start_marker_path("s-excluded", "t-excluded")
            result = reconcile.reconcile_one(marker, set())
            hook.handle_stop(event)

            current_paths = reconcile.raw_segments.current_segment_paths(base, kind="prompt_usage")
            records = [json.loads(line) for line in current_paths[0].read_text(encoding="utf-8").splitlines()]
            tombstone = json.loads(marker.read_text(encoding="utf-8"))

        self.assertEqual(result, "excluded_missing_transcript_path")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["lifecycle_end_reason"], "missing_start_state")
        self.assertEqual(tombstone["record_type"], "turn_finalized")
        self.assertEqual(tombstone["terminal_append_state"], "excluded")
        self.assertEqual(tombstone["finalized_reason"], "excluded_missing_transcript_path")
        self.assertEqual(tombstone["session_id"], "s-excluded")
        self.assertEqual(tombstone["turn_id"], "t-excluded")
        self.assertTrue(tombstone["finalized_at"])

    def test_reconcile_does_not_use_token_count_before_turn_start_when_offset_missing(self) -> None:
        reconcile = load_module("reconcile_bounds_token_counts_to_turn_test", ROOT / "scripts" / "reconcile.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp) / "token-usage"
            state_dir = base / "state"
            state_dir.mkdir(parents=True)
            transcript = pathlib.Path(tmp) / "rollout.jsonl"
            transcript.write_text(
                "\n".join(
                    json.dumps(row)
                    for row in [
                        {
                            "timestamp": "2026-05-31T09:59:59.000Z",
                            "type": "event_msg",
                            "payload": {
                                "type": "token_count",
                                "info": {
                                    "total_token_usage": {"input_tokens": 999, "total_tokens": 999},
                                    "last_token_usage": {"input_tokens": 999, "total_tokens": 999},
                                },
                            },
                        },
                        {"timestamp": "2026-05-31T10:00:00.000Z", "type": "event_msg", "payload": {"type": "task_started", "turn_id": "t-new"}},
                        {
                            "timestamp": "2026-05-31T10:00:02.000Z",
                            "type": "event_msg",
                            "payload": {"type": "task_aborted", "turn_id": "t-new", "reason": "cancelled"},
                        },
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            pending = state_dir / "pending.json"
            pending.write_text(
                json.dumps(
                    {
                        "record_type": "turn_start",
                        "session_id": "s-new",
                        "turn_id": "t-new",
                        "transcript_path": str(transcript),
                        "captured_at": "2026-05-31T10:00:00+00:00",
                        "start_token_usage": {"input_tokens": 0, "total_tokens": 0},
                        "prompt": {"prompt_preview": "new turn"},
                    }
                ),
                encoding="utf-8",
            )
            reconcile.CODEX_DIR = pathlib.Path(tmp)
            reconcile.BASE_DIR = base
            reconcile.STATE_DIR = state_dir
            reconcile.RAW_LOG = base / "raw" / "prompt-usage.raw.jsonl"
            reconcile.ARCHIVE_DIR = base / "raw" / "archive"

            result = reconcile.reconcile_one(pending, set())
            current_paths = reconcile.raw_segments.current_segment_paths(base, kind="prompt_usage")
            records = [json.loads(line) for line in current_paths[0].read_text(encoding="utf-8").splitlines()]

        self.assertEqual(result, "unavailable")
        self.assertEqual(records[0]["usage"]["total_tokens"], 0)
        self.assertEqual(records[0]["end_token_snapshot"]["reason"], "no_token_count_before_task_aborted")
        self.assertEqual(records[0]["token_resolution_status"], "unavailable")

    def test_reconcile_recovers_task_aborted_turns(self) -> None:
        reconcile = load_module("reconcile_task_aborted_test", ROOT / "scripts" / "reconcile.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp) / "token-usage"
            state_dir = base / "state"
            state_dir.mkdir(parents=True)
            transcript = pathlib.Path(tmp) / "rollout.jsonl"
            transcript.write_text(
                "\n".join(
                    json.dumps(row)
                    for row in [
                        {"timestamp": "2026-05-31T10:00:00.000Z", "type": "event_msg", "payload": {"type": "task_started", "turn_id": "t-abort"}},
                        {
                            "timestamp": "2026-05-31T10:00:01.000Z",
                            "type": "event_msg",
                            "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 10, "total_tokens": 10}}},
                        },
                        {
                            "timestamp": "2026-05-31T10:00:02.000Z",
                            "type": "event_msg",
                            "payload": {"type": "task_aborted", "turn_id": "t-abort", "reason": "cancelled"},
                        },
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            pending = state_dir / "pending.json"
            pending.write_text(
                json.dumps(
                    {
                        "record_type": "turn_start",
                        "session_id": "s-abort",
                        "turn_id": "t-abort",
                        "transcript_path": str(transcript),
                        "captured_at": "2026-05-31T10:00:00+00:00",
                        "start_file_size": 0,
                        "start_token_usage": {},
                        "start_token_snapshot": {},
                        "prompt": {"prompt_preview": "abort me"},
                    }
                ),
                encoding="utf-8",
            )
            reconcile.CODEX_DIR = pathlib.Path(tmp)
            reconcile.BASE_DIR = base
            reconcile.STATE_DIR = state_dir
            reconcile.RAW_LOG = base / "raw" / "prompt-usage.raw.jsonl"
            reconcile.ARCHIVE_DIR = base / "raw" / "archive"

            result = reconcile.reconcile_one(pending, set())
            current_paths = reconcile.raw_segments.current_segment_paths(base, kind="prompt_usage")
            records = [json.loads(line) for line in current_paths[0].read_text(encoding="utf-8").splitlines()]

        self.assertEqual(result, "aborted")
        self.assertFalse(pending.exists())
        self.assertEqual(records[0]["turn_status"], "aborted")
        self.assertEqual(records[0]["token_resolution_status"], "resolved")
        self.assertEqual(records[0]["lifecycle_end_reason"], "cancelled")
        self.assertEqual(records[0]["stopped_at"], "2026-05-31T10:00:02+00:00")

    def test_reconcile_excludes_missing_transcript_state(self) -> None:
        reconcile = load_module("reconcile_missing_transcript_test", ROOT / "scripts" / "reconcile.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp) / "token-usage"
            state_dir = base / "state"
            state_dir.mkdir(parents=True)
            pending = state_dir / "pending.json"
            pending.write_text(
                json.dumps(
                    {
                        "record_type": "turn_start",
                        "session_id": "s-missing-transcript",
                        "turn_id": "t-missing-transcript",
                        "cwd": str(pathlib.Path(tmp)),
                        "transcript_path": None,
                        "captured_at": "2026-05-31T10:00:00+00:00",
                        "start_token_usage": {"input_tokens": 12, "total_tokens": 12},
                        "start_token_snapshot": {"found": False, "reason": "missing_transcript_path"},
                        "prompt": {"prompt_preview": "missing transcript"},
                    }
                ),
                encoding="utf-8",
            )
            reconcile.CODEX_DIR = pathlib.Path(tmp)
            reconcile.BASE_DIR = base
            reconcile.STATE_DIR = state_dir
            reconcile.RAW_LOG = base / "raw" / "prompt-usage.raw.jsonl"
            reconcile.ARCHIVE_DIR = base / "raw" / "archive"

            result = reconcile.reconcile_one(pending, set())
            current_paths = reconcile.raw_segments.current_segment_paths(base, kind="prompt_usage")

        self.assertEqual(result, "excluded_missing_transcript_path")
        self.assertFalse(pending.exists())
        self.assertEqual(current_paths, [])

    def test_reconcile_ignores_service_state_json_files(self) -> None:
        reconcile = load_module("reconcile_ignores_service_state_test", ROOT / "scripts" / "reconcile.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp) / "token-usage"
            state_dir = base / "state"
            state_dir.mkdir(parents=True)
            service_state = state_dir / "current-raw-segments.json"
            service_state.write_text(
                json.dumps({"schema_version": 1, "current": {"prompt_usage": {"id": "p1"}}}),
                encoding="utf-8",
            )
            reconcile.CODEX_DIR = pathlib.Path(tmp)
            reconcile.BASE_DIR = base
            reconcile.STATE_DIR = state_dir

            result = reconcile.reconcile_one(service_state, set())
            service_state_exists = service_state.exists()

        self.assertEqual(result, "ignored")
        self.assertTrue(service_state_exists)

    def test_reconcile_completed_index_reads_pending_rotation_segment_before_recovery(self) -> None:
        reconcile = load_module("reconcile_pending_rotation_index_test", ROOT / "scripts" / "reconcile.py")
        raw_segments = reconcile.raw_segments
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp) / "token-usage"
            old_segment = raw_segments.new_current_segment(base, kind="prompt_usage", source_name="prompt-usage.raw.jsonl")
            new_segment = raw_segments.new_current_segment(base, kind="prompt_usage", source_name="prompt-usage.raw.jsonl")
            pathlib.Path(old_segment["path"]).write_text(json.dumps(_turn_raw("s-old", "t-old", total=100)) + "\n", encoding="utf-8")
            pathlib.Path(new_segment["path"]).write_text("", encoding="utf-8")
            raw_segments.write_current_pointer(base, raw_segments.empty_current_pointer(base) | {"current": {"prompt_usage": new_segment}})
            raw_segments.write_pending_rotation(
                base,
                {
                    "operation": "rotate_current_segment",
                    "phase": "manifest_pending",
                    "kind": "prompt_usage",
                    "old_segment": old_segment,
                    "new_segment": new_segment,
                    "created_at_unix": 1.0,
                },
            )
            reconcile.BASE_DIR = base
            reconcile.STATE_DIR = base / "state"
            reconcile.RAW_LOG = base / "raw" / "prompt-usage.raw.jsonl"
            reconcile.ARCHIVE_DIR = base / "raw" / "archive"

            completed = reconcile.completed_turn_index()

        self.assertIn(("s-old", "t-old"), completed)

    def test_reconcile_completed_index_fails_on_corrupt_current_pointer(self) -> None:
        reconcile = load_module("reconcile_corrupt_current_segment_index_test", ROOT / "scripts" / "reconcile.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp) / "token-usage"
            raw_dir = base / "raw"
            current_dir = raw_dir / "current"
            state_dir = base / "state"
            current_dir.mkdir(parents=True)
            state_dir.mkdir(parents=True)
            current_path = current_dir / "prompt-usage.raw.jsonl.current.1779235200000000000.jsonl"
            current_path.write_text(json.dumps(_turn_raw("s-current", "t-current", total=100)) + "\n", encoding="utf-8")
            pointer = {
                "schema_version": 1,
                "base": str((base / "other").resolve()),
                "current": {
                    "prompt_usage": {
                        "id": "prompt-usage.raw.jsonl.current.1779235200000000000",
                        "kind": "prompt_usage",
                        "path": str(current_path),
                        "source_name": "prompt-usage.raw.jsonl",
                        "created_at_unix": 1779235200.0,
                    }
                },
            }
            (state_dir / "current-raw-segments.json").write_text(json.dumps(pointer) + "\n", encoding="utf-8")
            reconcile.BASE_DIR = base
            reconcile.STATE_DIR = state_dir
            reconcile.RAW_LOG = raw_dir / "prompt-usage.raw.jsonl"
            reconcile.ARCHIVE_DIR = raw_dir / "archive"

            with self.assertRaises(reconcile.raw_segments.ManifestError):
                reconcile.completed_turn_index()
