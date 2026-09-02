from __future__ import annotations

try:
    from tests.support import (
        Any,
        ROOT,
        _turn_normalized,
        _turn_raw,
        argparse,
        gzip,
        io,
        json,
        load_module,
        mock,
        os,
        pathlib,
        sqlite3,
        stat,
        subprocess,
        sys,
        tempfile,
        time,
        types,
        unittest,
    )
except ModuleNotFoundError:
    from support import (
        Any,
        ROOT,
        _turn_normalized,
        _turn_raw,
        argparse,
        gzip,
        io,
        json,
        load_module,
        mock,
        os,
        pathlib,
        sqlite3,
        stat,
        subprocess,
        sys,
        tempfile,
        time,
        types,
        unittest,
    )


class IncrementalPipelineTests(unittest.TestCase):
    def test_tool_call_records_issuing_and_consuming_steps(self) -> None:
        build = load_module("build_analytics_test", ROOT / "scripts" / "build_analytics.py")
        rows = [
            {"type": "session_meta", "payload": {"id": "s1"}},
            {"type": "turn_context", "payload": {"turn_id": "t1"}},
            {"type": "event_msg", "timestamp": "2026-01-01T00:00:00Z", "payload": {"type": "token_count", "info": {}}},
            {
                "type": "response_item",
                "timestamp": "2026-01-01T00:00:01Z",
                "payload": {"type": "function_call", "call_id": "c1", "name": "exec_command"},
            },
            {"type": "event_msg", "timestamp": "2026-01-01T00:00:02Z", "payload": {"type": "token_count", "info": {}}},
            {
                "type": "response_item",
                "timestamp": "2026-01-01T00:00:03Z",
                "payload": {"type": "function_call_output", "call_id": "c1", "output": "Original token count: 42"},
            },
            {"type": "event_msg", "timestamp": "2026-01-01T00:00:04Z", "payload": {"type": "token_count", "info": {}}},
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = pathlib.Path(tmp_dir) / "transcript.jsonl"
            tmp.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            calls = build.extract_tool_calls({str(tmp)}, {"s1": {"t1"}})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["issued_by_model_call_index"], 2)
        self.assertEqual(calls[0]["consumed_by_model_call_index"], 3)
        self.assertEqual(calls[0]["output_reported_tokens"], 42)
        self.assertNotIn("output_preview", calls[0])

    def test_project_root_can_be_configured(self) -> None:
        build = load_module("build_analytics_project_test", ROOT / "scripts" / "build_analytics.py")
        previous = build.PROJECT_ROOTS
        try:
            build.PROJECT_ROOTS = [pathlib.Path("/workspace")]
            self.assertEqual(build.project_from_cwd("/workspace/my-app/service"), "my-app")
        finally:
            build.PROJECT_ROOTS = previous

    def test_session_index_latest_thread_name_wins(self) -> None:
        build = load_module("build_analytics_session_index_test", ROOT / "scripts" / "build_analytics.py")
        previous = build.SESSION_INDEX
        with tempfile.TemporaryDirectory() as tmp_dir:
            index_path = pathlib.Path(tmp_dir) / "session_index.jsonl"
            index_path.write_text(
                "\n".join(
                    [
                        json.dumps({"id": "s1", "thread_name": "old", "updated_at": "2026-01-01T00:00:00Z"}),
                        json.dumps({"id": "s2", "thread_name": "", "updated_at": "2026-01-01T00:00:01Z"}),
                        json.dumps({"id": "s1", "thread_name": "new", "updated_at": "2026-01-01T00:00:02Z"}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            try:
                build.SESSION_INDEX = index_path
                self.assertEqual(build.read_session_index(), {"s1": "new"})
            finally:
                build.SESSION_INDEX = previous

    def test_normalize_incremental_appends_only_new_raw_rows(self) -> None:
        normalize = load_module("normalize_incremental_test", ROOT / "scripts" / "normalize.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            normalize.RAW_LOG = base / "raw" / "prompt-usage.raw.jsonl"
            normalize.NORMALIZED_LOG = base / "normalized" / "prompt-usage.normalized.jsonl"
            normalize.ARCHIVE_DIR = base / "raw" / "archive"
            normalize.BAD_LOG = base / "bad" / "prompt-usage.bad.jsonl"
            normalize.STATE_FILE = base / "normalized" / "normalize-state.json"
            current = normalize.raw_segments.ensure_current_segment(base, kind="prompt_usage", source_name="prompt-usage.raw.jsonl")
            current_path = pathlib.Path(current["path"])
            current_path.write_text(json.dumps(_turn_raw("s1", "t1", total=100)) + "\n", encoding="utf-8")

            first = normalize.incremental_normalize()
            current_path.write_text(
                current_path.read_text(encoding="utf-8") + json.dumps(_turn_raw("s2", "t2", total=200)) + "\n",
                encoding="utf-8",
            )
            second = normalize.incremental_normalize()
            third = normalize.incremental_normalize()

            self.assertEqual(first["mode"], "full")
            self.assertEqual(first["new_rows"], 1)
            self.assertEqual(second["mode"], "incremental")
            self.assertEqual(second["new_rows"], 1)
            self.assertEqual(third["new_rows"], 0)
            with normalize.NORMALIZED_LOG.open(encoding="utf-8") as handle:
                self.assertEqual(sum(1 for _ in handle), 2)
            self.assertEqual(third["normalized_turns_size"], normalize.NORMALIZED_LOG.stat().st_size)

    def test_normalize_incremental_retries_eof_partial_jsonl_without_advancing_offset(self) -> None:
        normalize = load_module("normalize_incremental_partial_tail_retry_test", ROOT / "scripts" / "normalize.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            normalize.RAW_LOG = base / "raw" / "prompt-usage.raw.jsonl"
            normalize.NORMALIZED_LOG = base / "normalized" / "prompt-usage.normalized.jsonl"
            normalize.ARCHIVE_DIR = base / "raw" / "archive"
            normalize.BAD_LOG = base / "bad" / "prompt-usage.bad.jsonl"
            normalize.STATE_FILE = base / "normalized" / "normalize-state.json"
            current = normalize.raw_segments.ensure_current_segment(base, kind="prompt_usage", source_name="prompt-usage.raw.jsonl")
            current_path = pathlib.Path(current["path"])
            current_path.write_text(json.dumps(_turn_raw("s1", "t1", total=100)) + "\n", encoding="utf-8")
            normalize.incremental_normalize()
            committed_size = current_path.stat().st_size

            complete = json.dumps(_turn_raw("s2", "t2", total=200))
            current_path.write_text(current_path.read_text(encoding="utf-8") + complete[:-3], encoding="utf-8")
            second = normalize.incremental_normalize()
            state_after_partial = json.loads(normalize.STATE_FILE.read_text(encoding="utf-8"))

            current_path.write_text(current_path.read_text(encoding="utf-8") + complete[-3:] + "\n", encoding="utf-8")
            third = normalize.incremental_normalize()
            rows = [json.loads(line) for line in normalize.NORMALIZED_LOG.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(second["new_rows"], 0)
        self.assertFalse(normalize.BAD_LOG.exists())
        self.assertEqual(state_after_partial["sources"][str(current_path)], committed_size)
        self.assertEqual(third["new_rows"], 1)
        self.assertEqual([(row["session_id"], row["turn_id"]) for row in rows], [("s1", "t1"), ("s2", "t2")])

    def test_normalize_incremental_rolls_back_published_tail_after_state_write_failure(self) -> None:
        normalize = load_module("normalize_incremental_publish_recovery_test", ROOT / "scripts" / "normalize.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            normalize.RAW_LOG = base / "raw" / "prompt-usage.raw.jsonl"
            normalize.NORMALIZED_LOG = base / "normalized" / "prompt-usage.normalized.jsonl"
            normalize.ARCHIVE_DIR = base / "raw" / "archive"
            normalize.BAD_LOG = base / "bad" / "prompt-usage.bad.jsonl"
            normalize.STATE_FILE = base / "normalized" / "normalize-state.json"
            first_turn = _turn_raw("s1", "t1", total=100) | {
                "model_calls": [
                    {
                        "index": 1,
                        "timestamp": "2026-01-01T00:00:01Z",
                        "usage": {"input_tokens": 90, "cached_input_tokens": 0, "output_tokens": 10, "reasoning_output_tokens": 0, "total_tokens": 100},
                    }
                ]
            }
            second_turn = _turn_raw("s2", "t2", total=200) | {
                "model_calls": [
                    {
                        "index": 1,
                        "timestamp": "2026-01-01T00:00:01Z",
                        "usage": {"input_tokens": 190, "cached_input_tokens": 0, "output_tokens": 10, "reasoning_output_tokens": 0, "total_tokens": 200},
                    }
                ]
            }
            current = normalize.raw_segments.ensure_current_segment(base, kind="prompt_usage", source_name="prompt-usage.raw.jsonl")
            current_path = pathlib.Path(current["path"])
            current_path.write_text(json.dumps(first_turn) + "\n", encoding="utf-8")
            normalize.incremental_normalize()
            current_path.write_text(
                current_path.read_text(encoding="utf-8") + json.dumps(second_turn) + "\n",
                encoding="utf-8",
            )
            original_write_state = normalize.write_state
            failed_once = False

            def fail_after_publish(state: dict[str, Any]) -> None:
                nonlocal failed_once
                if not failed_once:
                    failed_once = True
                    raise OSError("state commit failed")
                original_write_state(state)

            with mock.patch.object(normalize, "write_state", side_effect=fail_after_publish):
                with self.assertRaises(OSError):
                    normalize.incremental_normalize()

            normalize.incremental_normalize()
            turn_ids = [json.loads(line)["turn_id"] for line in normalize.NORMALIZED_LOG.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(turn_ids, ["t1", "t2"])

    def test_full_normalize_publish_failure_forces_safe_recovery(self) -> None:
        normalize = load_module("normalize_full_publish_recovery_test", ROOT / "scripts" / "normalize.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            normalize.RAW_LOG = base / "raw" / "prompt-usage.raw.jsonl"
            normalize.NORMALIZED_LOG = base / "normalized" / "prompt-usage.normalized.jsonl"
            normalize.ARCHIVE_DIR = base / "raw" / "archive"
            normalize.BAD_LOG = base / "bad" / "prompt-usage.bad.jsonl"
            normalize.STATE_FILE = base / "normalized" / "normalize-state.json"
            normalize.NORMALIZED_LOG.parent.mkdir(parents=True)
            current = normalize.raw_segments.ensure_current_segment(base, kind="prompt_usage", source_name="prompt-usage.raw.jsonl")
            pathlib.Path(current["path"]).write_text(json.dumps(_turn_raw("s1", "t1", total=100)) + "\n", encoding="utf-8")
            normalize.NORMALIZED_LOG.write_text(json.dumps(_turn_normalized("stale", "stale", total=1)) + "\n", encoding="utf-8")
            normalize.STATE_FILE.write_text(
                json.dumps(
                    {
                        "logic_version": normalize.NORMALIZE_LOGIC_VERSION,
                        "sources": {str(current["path"]): 0},
                        "normalized_log_size": normalize.NORMALIZED_LOG.stat().st_size,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            original_write_state = normalize.write_state
            with mock.patch.object(normalize, "write_state", side_effect=OSError("state write failed")):
                with self.assertRaises(OSError):
                    normalize.full_normalize()

            normalize.write_state = original_write_state
            result = normalize.incremental_normalize()
            turn_ids = [json.loads(line)["turn_id"] for line in normalize.NORMALIZED_LOG.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(result["mode"], "full")
        self.assertEqual(turn_ids, ["t1"])

    def test_normalize_recovery_fails_on_corrupt_pending_publish_marker(self) -> None:
        normalize = load_module("normalize_corrupt_pending_publish_test", ROOT / "scripts" / "normalize.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            normalize.STATE_FILE = base / "normalized" / "normalize-state.json"
            normalize.STATE_FILE.parent.mkdir(parents=True)
            pending = normalize.pending_publish_file()
            pending.write_text("{bad\n", encoding="utf-8")

            with self.assertRaises(normalize.PendingPublishRecoveryError):
                normalize.recover_pending_publish()

            self.assertTrue(pending.exists())

    def test_normalize_recovery_truncates_when_processed_segments_differ(self) -> None:
        normalize = load_module("normalize_processed_segment_recovery_test", ROOT / "scripts" / "normalize.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            normalize.NORMALIZED_LOG = base / "normalized" / "prompt-usage.normalized.jsonl"
            normalize.STATE_FILE = base / "normalized" / "normalize-state.json"
            normalize.NORMALIZED_LOG.parent.mkdir(parents=True)
            source_path = base / "raw" / "current" / "prompt-usage.raw.jsonl.current.1.jsonl"
            source_path.parent.mkdir(parents=True)
            sources = {str(source_path): 128}
            old_row = json.dumps(_turn_normalized("s-old", "t-old", total=10)) + "\n"
            crash_row = json.dumps(_turn_normalized("s-new", "t-new", total=20)) + "\n"
            normalize.NORMALIZED_LOG.write_text(old_row, encoding="utf-8")
            old_size = normalize.NORMALIZED_LOG.stat().st_size
            normalize.write_state(normalize.normalize_state(sources, {}))
            pending_state = normalize.normalize_state(
                sources,
                {
                    "closed-segment": {
                        "path": str(source_path),
                        "bytes": 128,
                        "sha256": "abc",
                        "rows": 1,
                    }
                },
            )
            normalize.write_pending_publish(old_size, pending_state)
            with normalize.NORMALIZED_LOG.open("a", encoding="utf-8") as handle:
                handle.write(crash_row)

            normalize.recover_pending_publish()

            payload = normalize.NORMALIZED_LOG.read_text(encoding="utf-8")
            self.assertEqual(payload, old_row)
            self.assertFalse(normalize.pending_publish_file().exists())

    def test_normalize_recovery_keeps_output_after_state_commit(self) -> None:
        normalize = load_module("normalize_committed_publish_recovery_test", ROOT / "scripts" / "normalize.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            normalize.NORMALIZED_LOG = base / "normalized" / "prompt-usage.normalized.jsonl"
            normalize.STATE_FILE = base / "normalized" / "normalize-state.json"
            normalize.NORMALIZED_LOG.parent.mkdir(parents=True)
            old_row = json.dumps(_turn_normalized("s-old", "t-old", total=10)) + "\n"
            new_row = json.dumps(_turn_normalized("s-new", "t-new", total=20)) + "\n"
            normalize.NORMALIZED_LOG.write_text(old_row, encoding="utf-8")
            identity = normalize.normalize_identity({"/tmp/raw.jsonl": 128}, {})
            normalize.write_pending_publish(
                len(old_row.encode("utf-8")),
                identity.to_state(normalized_log_size=len(old_row.encode("utf-8"))),
            )
            with normalize.NORMALIZED_LOG.open("a", encoding="utf-8") as handle:
                handle.write(new_row)
            normalize.write_state(identity.to_state(normalized_log_size=normalize.NORMALIZED_LOG.stat().st_size))

            normalize.recover_pending_publish()

            self.assertEqual(normalize.NORMALIZED_LOG.read_text(encoding="utf-8"), old_row + new_row)
            self.assertFalse(normalize.pending_publish_file().exists())

    def test_normalize_main_reports_corrupt_pending_publish_marker_as_json(self) -> None:
        normalize = load_module("normalize_corrupt_pending_publish_main_test", ROOT / "scripts" / "normalize.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            normalize.STATE_FILE = base / "normalized" / "normalize-state.json"
            normalize.STATE_FILE.parent.mkdir(parents=True)
            pending = normalize.pending_publish_file()
            pending.write_text("{bad\n", encoding="utf-8")
            captured = io.StringIO()

            with (
                mock.patch.object(
                    normalize.service_lock, "acquire_service_lock", return_value=mock.MagicMock(__enter__=lambda _self: None, __exit__=lambda *_args: None)
                ),
                mock.patch.object(normalize.sys, "argv", ["normalize.py", "--incremental"]),
                mock.patch.object(normalize.sys, "stdout", captured),
            ):
                code = normalize.main()

        payload = json.loads(captured.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(payload["error"], "normalize_pending_publish_recovery_failed")
        self.assertTrue(payload["recovery_required"])
        self.assertEqual(payload["marker_path"], str(pending))

    def test_normalize_cancel_checkpoint_stops_before_publishing_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            cancel_file = base / "cancel.json"
            cancel_file.write_text("{}", encoding="utf-8")
            with mock.patch.dict(os.environ, {"BOLA_CANCEL_FILE": str(cancel_file)}, clear=False):
                normalize = load_module("normalize_cancel_checkpoint_test", ROOT / "scripts" / "normalize.py")
            normalize.RAW_LOG = base / "raw" / "prompt-usage.raw.jsonl"
            normalize.NORMALIZED_LOG = base / "normalized" / "prompt-usage.normalized.jsonl"
            normalize.ARCHIVE_DIR = base / "raw" / "archive"
            normalize.BAD_LOG = base / "bad" / "prompt-usage.bad.jsonl"
            normalize.STATE_FILE = base / "normalized" / "normalize-state.json"
            current = normalize.raw_segments.ensure_current_segment(base, kind="prompt_usage", source_name="prompt-usage.raw.jsonl")
            pathlib.Path(current["path"]).write_text(json.dumps(_turn_raw("s1", "t1", total=100)) + "\n", encoding="utf-8")

            with mock.patch.dict(os.environ, {"BOLA_CANCEL_FILE": str(cancel_file)}, clear=False):
                with self.assertRaises(normalize.cancel_control.Cancelled):
                    normalize.full_normalize()

            self.assertFalse(normalize.NORMALIZED_LOG.exists())
            self.assertFalse(normalize.STATE_FILE.exists())

    def test_progress_control_writes_bounded_progress_snapshot(self) -> None:
        progress = load_module("progress_control_test", ROOT / "scripts" / "progress_control.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = pathlib.Path(tmp_dir) / "progress.json"
            payload = progress.write_progress_to_path(
                path,
                status="running",
                phase="build",
                phase_index=1,
                checkpoint="turns:100",
                processed=100,
                total=200,
            )
            loaded = progress.read_progress(path)

        self.assertEqual(payload["phase_progress"], 0.5)
        self.assertEqual(payload["overall_progress"], 55.0)
        self.assertEqual(loaded["checkpoint"], "turns:100")

    def test_progress_control_throttles_running_snapshots_but_writes_terminal_status(self) -> None:
        progress = load_module("progress_control_throttle_test", ROOT / "scripts" / "progress_control.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = pathlib.Path(tmp_dir) / "progress.json"
            with mock.patch.object(progress.time, "monotonic", side_effect=[10.0, 10.01, 10.02]):
                first = progress.write_progress_to_path(path, status="running", phase="build", checkpoint="first")
                skipped = progress.write_progress_to_path(path, status="running", phase="build", checkpoint="second")
                terminal = progress.write_progress_to_path(path, status="completed", phase="build", checkpoint="done")
            loaded = progress.read_progress(path)

        self.assertEqual(first["checkpoint"], "first")
        self.assertIsNone(skipped)
        self.assertEqual(terminal["checkpoint"], "done")
        self.assertEqual(loaded["checkpoint"], "done")

    def test_progress_control_maps_cleanup_phases(self) -> None:
        progress = load_module("progress_control_cleanup_phase_test", ROOT / "scripts" / "progress_control.py")
        payload = progress.progress_payload(phase="cleanup-delete", phase_index=1, phase_count=4, phase_progress=0.5)

        self.assertEqual(payload["overall_progress"], 42.5)

    def test_normalize_full_reads_manifest_prompt_segments_before_active(self) -> None:
        normalize = load_module("normalize_manifest_segments_test", ROOT / "scripts" / "normalize.py")
        raw_segments = load_module("raw_segments_manifest_sources_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            normalize.RAW_LOG = base / "raw" / "prompt-usage.raw.jsonl"
            normalize.NORMALIZED_LOG = base / "normalized" / "prompt-usage.normalized.jsonl"
            normalize.ARCHIVE_DIR = base / "raw" / "archive"
            normalize.BAD_LOG = base / "bad" / "prompt-usage.bad.jsonl"
            normalize.STATE_FILE = base / "normalized" / "normalize-state.json"
            normalize.ARCHIVE_DIR.mkdir(parents=True)
            segment_path = normalize.ARCHIVE_DIR / "prompt-usage.raw.jsonl.20260520000000.20260520000000.1.jsonl.gz"
            untracked_segment_path = normalize.ARCHIVE_DIR / "prompt-usage.raw.jsonl.20260519000000.20260519000000.untracked.jsonl.gz"
            with gzip.open(segment_path, "wt", encoding="utf-8") as handle:
                handle.write(json.dumps(_turn_raw("s1", "t1", total=100) | {"captured_at": "2026-05-20T00:00:00+00:00"}) + "\n")
            with gzip.open(untracked_segment_path, "wt", encoding="utf-8") as handle:
                handle.write(json.dumps(_turn_raw("untracked", "archive", total=50) | {"captured_at": "2026-05-19T00:00:00+00:00"}) + "\n")
            current = raw_segments.ensure_current_segment(base, kind="prompt_usage", source_name="prompt-usage.raw.jsonl")
            pathlib.Path(current["path"]).write_text(
                json.dumps(_turn_raw("s2", "t2", total=200) | {"captured_at": "2026-05-21T00:00:00+00:00"}) + "\n", encoding="utf-8"
            )
            raw_segments.write_manifest(
                base,
                raw_segments.empty_manifest(base)
                | {
                    "segments": [
                        {
                            "id": "prompt-usage.raw.jsonl.20260520000000.20260520000000.1",
                            "kind": "prompt_usage",
                            "path": str(segment_path),
                            "format": "jsonl.gz",
                            "source_name": normalize.RAW_LOG.name,
                            "min_time_unix": 1779235200.0,
                            "max_time_unix": 1779235200.0,
                            "rows": 1,
                            "bytes": segment_path.stat().st_size,
                            "uncompressed_bytes": 100,
                            "status": "closed",
                        }
                    ]
                },
            )

            result = normalize.full_normalize()

            self.assertEqual(result["rows"], 2)
            rows = [json.loads(line) for line in normalize.NORMALIZED_LOG.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["turn_id"] for row in rows], ["t1", "t2"])

    def test_normalize_ignores_root_prompt_usage_jsonl(self) -> None:
        normalize = load_module("normalize_manifest_jsonl_priority_test", ROOT / "scripts" / "normalize.py")
        raw_segments = load_module("raw_segments_manifest_jsonl_priority_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            normalize.RAW_LOG = base / "raw" / "prompt-usage.raw.jsonl"
            normalize.NORMALIZED_LOG = base / "normalized" / "prompt-usage.normalized.jsonl"
            normalize.ARCHIVE_DIR = base / "raw" / "archive"
            normalize.BAD_LOG = base / "bad" / "prompt-usage.bad.jsonl"
            normalize.STATE_FILE = base / "normalized" / "normalize-state.json"
            current_dir = base / "raw" / "current"
            current_dir.mkdir(parents=True)
            segment_path = current_dir / "prompt-usage.raw.jsonl.current.1779235200000000000.jsonl"
            segment_path.write_text(json.dumps(_turn_raw("s1", "t1", total=100) | {"captured_at": "2026-05-20T00:00:00+00:00"}) + "\n", encoding="utf-8")
            (base / "prompt-usage.jsonl").write_text(
                json.dumps(_turn_raw("old-root", "ignored", total=50) | {"captured_at": "2026-05-19T00:00:00+00:00"}) + "\n", encoding="utf-8"
            )
            raw_segments.write_manifest(
                base,
                raw_segments.empty_manifest(base)
                | {
                    "segments": [
                        {
                            "id": "prompt-usage.raw.jsonl.current.1779235200000000000",
                            "kind": "prompt_usage",
                            "path": str(segment_path),
                            "format": "jsonl",
                            "source_name": normalize.RAW_LOG.name,
                            "min_time_unix": 1779235200.0,
                            "max_time_unix": 1779235200.0,
                            "rows": 1,
                            "bytes": segment_path.stat().st_size,
                            "uncompressed_bytes": segment_path.stat().st_size,
                            "status": "closed",
                        }
                    ]
                },
            )

            normalize.full_normalize()

            rows = [json.loads(line) for line in normalize.NORMALIZED_LOG.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([(row["session_id"], row["turn_id"], row["usage"]["total_tokens"]) for row in rows], [("s1", "t1", 100)])

    def test_normalize_fails_before_archive_discovery_when_apply_marker_reconcile_fails(self) -> None:
        normalize = load_module("normalize_apply_marker_fail_test", ROOT / "scripts" / "normalize.py")
        with (
            mock.patch.object(
                normalize.raw_segments,
                "reconcile_apply_marker",
                side_effect=normalize.raw_segments.ManifestError("bad marker"),
            ),
            mock.patch.object(normalize, "archived_prompt_logs", side_effect=AssertionError("archive discovery must not run")),
        ):
            with self.assertRaises(normalize.raw_segments.ManifestError):
                normalize.full_normalize()

    def test_normalize_fails_before_archive_discovery_when_rotation_reconcile_fails(self) -> None:
        normalize = load_module("normalize_rotation_marker_fail_test", ROOT / "scripts" / "normalize.py")
        with (
            mock.patch.object(
                normalize.raw_segments,
                "reconcile_pending_rotation",
                side_effect=normalize.raw_segments.ManifestError("bad rotation marker"),
            ),
            mock.patch.object(normalize, "archived_prompt_logs", side_effect=AssertionError("archive discovery must not run")),
        ):
            with self.assertRaises(normalize.raw_segments.ManifestError):
                normalize.full_normalize()

    def test_normalize_rejects_manifest_segment_outside_raw_roots_before_reading(self) -> None:
        normalize = load_module("normalize_manifest_path_validation_test", ROOT / "scripts" / "normalize.py")
        raw_segments = load_module("raw_segments_normalize_path_validation_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            outside = base / "outside.jsonl.gz"
            with gzip.open(outside, "wt", encoding="utf-8") as handle:
                handle.write(json.dumps(_turn_raw("s1", "t1", total=100)) + "\n")
            normalize.RAW_LOG = base / "raw" / "prompt-usage.raw.jsonl"
            normalize.ARCHIVE_DIR = base / "raw" / "archive"
            normalize.STATE_FILE = base / "normalized" / "normalize-state.json"
            normalize.RAW_LOG.parent.mkdir(parents=True)
            raw_segments.write_manifest(
                base,
                raw_segments.empty_manifest(base)
                | {
                    "segments": [
                        {
                            "id": "prompt-usage.raw.jsonl.20260520000000.20260520000000.1",
                            "kind": "prompt_usage",
                            "path": str(outside),
                            "format": "jsonl.gz",
                            "source_name": "prompt-usage.raw.jsonl",
                            "status": "closed",
                        }
                    ]
                },
            )

            with self.assertRaises(normalize.raw_segments.ManifestError):
                normalize.archived_prompt_logs()

    def test_build_incremental_upserts_new_turn_without_rebuilding_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            turns = base / "turns.jsonl"
            output_dir = base / "output"
            db_path = output_dir / "analytics" / "bola.sqlite"
            state_db = base / "missing-state.sqlite"
            turns.write_text(json.dumps(_turn_normalized("s1", "t1", total=100)) + "\n", encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "build_analytics.py"),
                    "--normalized-log",
                    str(turns),
                    "--state-db",
                    str(state_db),
                ],
                cwd=ROOT,
                check=True,
                text=True,
                capture_output=True,
                env={**os.environ, "BOLA_OUTPUT_DIR": str(output_dir)},
            )
            turns_offset = turns.stat().st_size
            turns.write_text(turns.read_text(encoding="utf-8") + json.dumps(_turn_normalized("s2", "t2", total=200)) + "\n", encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "build_analytics.py"),
                    "--normalized-log",
                    str(turns),
                    "--state-db",
                    str(state_db),
                    "--incremental",
                    "--turns-offset",
                    str(turns_offset),
                ],
                cwd=ROOT,
                check=True,
                text=True,
                capture_output=True,
                env={**os.environ, "BOLA_OUTPUT_DIR": str(output_dir)},
            )
            metadata = json.loads(result.stdout)
            con = sqlite3.connect(db_path)
            try:
                self.assertEqual(con.execute("select count(*) from turns").fetchone()[0], 2)
                self.assertEqual(json.loads(con.execute("select value from run_metadata where key='analysis_mode'").fetchone()[0]), "incremental")
                self.assertEqual(
                    json.loads(con.execute("select value from run_metadata where key='applied_normalized_turns_size'").fetchone()[0]), turns.stat().st_size
                )
                self.assertEqual(metadata["new_turn_rows"], 1)
            finally:
                con.close()

    def test_build_incremental_keeps_existing_higher_rank_turn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            turns = base / "turns.jsonl"
            output_dir = base / "output"
            db_path = output_dir / "analytics" / "bola.sqlite"
            state_db = base / "missing-state.sqlite"
            turns.write_text(json.dumps(_turn_normalized("s1", "t1", total=200)) + "\n", encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "build_analytics.py"),
                    "--normalized-log",
                    str(turns),
                    "--state-db",
                    str(state_db),
                ],
                cwd=ROOT,
                check=True,
                text=True,
                capture_output=True,
                env={**os.environ, "BOLA_OUTPUT_DIR": str(output_dir)},
            )
            turns_offset = turns.stat().st_size
            stale = _turn_normalized("s1", "t1", total=10) | {"turn_status": "incomplete", "estimated": True}
            turns.write_text(turns.read_text(encoding="utf-8") + json.dumps(stale) + "\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "build_analytics.py"),
                    "--normalized-log",
                    str(turns),
                    "--state-db",
                    str(state_db),
                    "--incremental",
                    "--turns-offset",
                    str(turns_offset),
                ],
                cwd=ROOT,
                check=True,
                text=True,
                capture_output=True,
                env={**os.environ, "BOLA_OUTPUT_DIR": str(output_dir)},
            )
            metadata = json.loads(result.stdout)
            con = sqlite3.connect(db_path)
            try:
                stored = con.execute("select turn_status, estimated, total_tokens from turns where session_id='s1' and turn_id='t1'").fetchone()
            finally:
                con.close()

        self.assertEqual(stored, ("completed", 0, 200))
        self.assertEqual(metadata["new_turn_rows"], 0)

    def test_build_incremental_replaces_equal_rank_turn_with_later_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            turns = base / "turns.jsonl"
            output_dir = base / "output"
            db_path = output_dir / "analytics" / "bola.sqlite"
            state_db = base / "missing-state.sqlite"
            turns.write_text(json.dumps(_turn_normalized("s1", "t1", total=10)) + "\n", encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "build_analytics.py"),
                    "--normalized-log",
                    str(turns),
                    "--state-db",
                    str(state_db),
                ],
                cwd=ROOT,
                check=True,
                text=True,
                capture_output=True,
                env={**os.environ, "BOLA_OUTPUT_DIR": str(output_dir)},
            )
            turns_offset = turns.stat().st_size
            turns.write_text(turns.read_text(encoding="utf-8") + json.dumps(_turn_normalized("s1", "t1", total=20)) + "\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "build_analytics.py"),
                    "--normalized-log",
                    str(turns),
                    "--state-db",
                    str(state_db),
                    "--incremental",
                    "--turns-offset",
                    str(turns_offset),
                ],
                cwd=ROOT,
                check=True,
                text=True,
                capture_output=True,
                env={**os.environ, "BOLA_OUTPUT_DIR": str(output_dir)},
            )
            metadata = json.loads(result.stdout)
            con = sqlite3.connect(db_path)
            try:
                total = con.execute("select total_tokens from turns where session_id='s1' and turn_id='t1'").fetchone()[0]
            finally:
                con.close()

        self.assertEqual(total, 20)
        self.assertEqual(metadata["new_turn_rows"], 1)

    def test_build_incremental_replacement_without_transcript_path_deletes_stale_tool_rollups(self) -> None:
        build = load_module("build_incremental_empty_transcript_tool_cleanup_test", ROOT / "scripts" / "build_analytics.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            db_path = base / "analytics.sqlite"
            turns = base / "normalized" / "turns.jsonl"
            state_db = base / "state.sqlite"
            transcript = base / "rollout.jsonl"
            turns.parent.mkdir(parents=True)
            transcript.write_text("", encoding="utf-8")
            old_row = _turn_normalized("s1", "t1", total=10) | {"transcript_path": str(transcript)}
            new_row = _turn_normalized("s1", "t1", total=20)
            turns.write_text(json.dumps(old_row) + "\n", encoding="utf-8")
            turns_offset = turns.stat().st_size
            turns.write_text(turns.read_text(encoding="utf-8") + json.dumps(new_row) + "\n", encoding="utf-8")
            build.NORMALIZED_LOG = turns
            build.STATE_DB = state_db
            build.ANALYTICS_DB = db_path
            build.SESSION_INDEX = base / "session_index.jsonl"
            build.RETENTION_PRUNED_TURNS_FILE = base / "state" / "retention-pruned-turns.json"
            con = sqlite3.connect(db_path)
            try:
                build.setup_db(con)
                build.upsert_turn_row(con, old_row, {})
                build.replace_tool_call_rollups_from_batches(
                    con,
                    [
                        [
                            {
                                "session_id": "s1",
                                "turn_id": "t1",
                                "tool_name": "exec_command",
                                "tool_provider": "exec",
                                "call_id": "c1",
                                "output_tokens": 5,
                                "total_tokens": 10,
                            }
                        ]
                    ],
                )
                build.write_metadata(con, {"applied_normalized_turns_size": turns_offset, "applied_input_fingerprint": "old"})
                build.write_metadata(
                    con,
                    {
                        "context_snapshot_version": build.build_analytics_context.CONTEXT_SNAPSHOT_VERSION,
                        "analytics_schema_version": build.ANALYTICS_SCHEMA_VERSION,
                        "cost_rate_catalog_digest": build.COST_RATE_CATALOG.digest,
                    },
                )
                con.commit()
            finally:
                con.close()

            result = build.incremental_build(argparse.Namespace(turns_offset=turns_offset))
            con = sqlite3.connect(db_path)
            try:
                tool_rows = con.execute("select count(*) from tool_call_summaries where session_id='s1' and turn_id='t1'").fetchone()[0]
                total = con.execute("select total_tokens from turns where session_id='s1' and turn_id='t1'").fetchone()[0]
            finally:
                con.close()

        self.assertIsNotNone(result)
        self.assertEqual(total, 20)
        self.assertEqual(tool_rows, 0)

    def test_schema_v1_incremental_build_rebuilds_and_purges_tool_output_preview(self) -> None:
        build = load_module("build_schema_v1_preview_purge_test", ROOT / "scripts" / "build_analytics.py")
        sentinel = "removed-tool-output-preview-sentinel"
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            normalized = base / "normalized.jsonl"
            database = base / "analytics.sqlite"
            normalized.write_text("", encoding="utf-8")
            con = sqlite3.connect(database)
            try:
                con.executescript(
                    """
                    create table turns (session_id text);
                    create table run_metadata (key text primary key, value text);
                    create table tool_call_samples (output_preview text);
                    """
                )
                con.execute("insert into tool_call_samples values (?)", (sentinel,))
                con.executemany(
                    "insert into run_metadata values (?, ?)",
                    [
                        ("context_snapshot_version", json.dumps(build.build_analytics_context.CONTEXT_SNAPSHOT_VERSION)),
                        ("analytics_schema_version", json.dumps(1)),
                    ],
                )
                con.commit()
            finally:
                con.close()

            build.NORMALIZED_LOG = normalized
            build.ANALYTICS_DB = database

            def full_rebuild() -> dict[str, object]:
                with build.full_build_connection(database) as rebuilt:
                    build.setup_db(rebuilt)
                    build.write_metadata(rebuilt, {"analytics_schema_version": build.ANALYTICS_SCHEMA_VERSION})
                return {"output": str(database), "analytics_schema_version": build.ANALYTICS_SCHEMA_VERSION}

            lock_context = mock.MagicMock(__enter__=lambda _self: None, __exit__=lambda *_args: None)
            args = argparse.Namespace(incremental=True, turns_offset=0)
            with (
                mock.patch.object(build, "parse_args", return_value=args),
                mock.patch.object(build, "configure_paths"),
                mock.patch.object(build.service_lock, "acquire_service_lock", return_value=lock_context),
                mock.patch.object(build, "build", side_effect=full_rebuild) as rebuild,
                mock.patch.object(build.sys, "stdout", io.StringIO()),
            ):
                exit_code = build.main()

            con = sqlite3.connect(database)
            try:
                columns = {str(row[1]) for row in con.execute("pragma table_info(tool_call_samples)")}
                stored_version = json.loads(
                    con.execute("select value from run_metadata where key='analytics_schema_version'").fetchone()[0]
                )
            finally:
                con.close()

            self.assertEqual(exit_code, 0)
            rebuild.assert_called_once_with()
            self.assertEqual(stored_version, build.ANALYTICS_SCHEMA_VERSION)
            self.assertNotIn("output_preview", columns)
            self.assertNotIn(sentinel.encode(), database.read_bytes())

    def test_build_full_replaces_equal_rank_turn_with_later_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            turns = base / "turns.jsonl"
            output_dir = base / "output"
            db_path = output_dir / "analytics" / "bola.sqlite"
            state_db = base / "missing-state.sqlite"
            turns.write_text(
                json.dumps(_turn_normalized("s1", "t1", total=10)) + "\n" + json.dumps(_turn_normalized("s1", "t1", total=20)) + "\n",
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "build_analytics.py"),
                    "--normalized-log",
                    str(turns),
                    "--state-db",
                    str(state_db),
                ],
                cwd=ROOT,
                check=True,
                text=True,
                capture_output=True,
                env={**os.environ, "BOLA_OUTPUT_DIR": str(output_dir)},
            )
            con = sqlite3.connect(db_path)
            try:
                total = con.execute("select total_tokens from turns where session_id='s1' and turn_id='t1'").fetchone()[0]
            finally:
                con.close()

        self.assertEqual(total, 20)

    def test_build_incremental_rejects_turns_offset_beyond_normalized_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            turns = base / "turns.jsonl"
            output_dir = base / "output"
            state_db = base / "missing-state.sqlite"
            turns.write_text(json.dumps(_turn_normalized("s1", "t1", total=100)) + "\n", encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "build_analytics.py"),
                    "--normalized-log",
                    str(turns),
                    "--state-db",
                    str(state_db),
                ],
                cwd=ROOT,
                check=True,
                text=True,
                capture_output=True,
                env={**os.environ, "BOLA_OUTPUT_DIR": str(output_dir)},
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "build_analytics.py"),
                    "--normalized-log",
                    str(turns),
                    "--state-db",
                    str(state_db),
                    "--incremental",
                    "--turns-offset",
                    str(turns.stat().st_size + 1),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                env={**os.environ, "BOLA_OUTPUT_DIR": str(output_dir)},
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("turns_offset_beyond_normalized_size", result.stdout)

    def test_analytics_schema_includes_latest_turn_order_indexes(self) -> None:
        build = load_module("build_analytics_index_test", ROOT / "scripts" / "build_analytics.py")
        con = sqlite3.connect(":memory:")
        try:
            build.setup_db(con)
            tables = {row[0] for row in con.execute("select name from sqlite_master where type='table'")}
            self.assertNotIn("model_calls", tables)
            self.assertNotIn("tool_calls", tables)
            self.assertIn("model_call_summaries", tables)
            self.assertIn("tool_call_summaries", tables)
            self.assertIn("tool_call_samples", tables)
            self.assertIn("source_context_threads", tables)
            self.assertIn("source_context_edges", tables)
            columns = {row[1] for row in con.execute("pragma table_info(turns)")}
            indexes = {row[0] for row in con.execute("select name from sqlite_master where type='index' and tbl_name='turns'")}
            self.assertNotIn("thread_title", columns)
            self.assertIn("thread_name", columns)
            self.assertIn("started_at_unix", columns)
            self.assertIn("model_from_context", columns)
            self.assertIn("idx_turns_started_at_unix", indexes)
            self.assertIn("idx_turns_latest_order", indexes)
            self.assertIn("idx_turns_project_latest_order", indexes)
            self.assertIn("idx_turns_weighted_order", indexes)
            self.assertIn("idx_turns_weighted_order_asc", indexes)
            self.assertNotIn("idx_turns_thread_title", indexes)
            self.assertIn("idx_turns_thread_name", indexes)
            plan = "\n".join(
                str(row)
                for row in con.execute(
                    """
                    explain query plan
                    select session_id, turn_id
                    from turns
                    order by started_at_unix desc, session_id desc, turn_id desc
                    limit 25
                    """
                )
            )
            self.assertIn("idx_turns_latest_order", plan)
            self.assertNotIn("USE TEMP B-TREE", plan)
            weighted_plan = "\n".join(
                str(row)
                for row in con.execute(
                    """
                    explain query plan
                    select session_id, turn_id
                    from turns
                    order by weighted_credits desc, started_at_unix desc, session_id desc, turn_id desc
                    limit 25
                    """
                )
            )
            self.assertIn("idx_turns_weighted_order", weighted_plan)
            self.assertNotIn("USE TEMP B-TREE", weighted_plan)
            weighted_asc_plan = "\n".join(
                str(row)
                for row in con.execute(
                    """
                    explain query plan
                    select session_id, turn_id
                    from turns
                    order by weighted_credits asc, started_at_unix desc, session_id desc, turn_id desc
                    limit 25
                    """
                )
            )
            self.assertIn("idx_turns_weighted_order_asc", weighted_asc_plan)
            self.assertNotIn("USE TEMP B-TREE", weighted_asc_plan)
        finally:
            con.close()

    def test_build_fails_before_archive_discovery_when_apply_marker_reconcile_fails(self) -> None:
        build = load_module("build_apply_marker_fail_test", ROOT / "scripts" / "build_analytics.py")
        with tempfile.TemporaryDirectory() as tmp:
            output = pathlib.Path(tmp) / "bola.sqlite"
            with (
                mock.patch.object(build, "ANALYTICS_DB", output),
                mock.patch.object(
                    build.raw_segments,
                    "reconcile_apply_marker",
                    side_effect=build.raw_segments.ManifestError("bad marker"),
                ),
            ):
                with self.assertRaises(build.raw_segments.ManifestError):
                    build.build()

    def test_build_fails_before_archive_discovery_when_rotation_reconcile_fails(self) -> None:
        build = load_module("build_rotation_marker_fail_test", ROOT / "scripts" / "build_analytics.py")
        with tempfile.TemporaryDirectory() as tmp:
            output = pathlib.Path(tmp) / "bola.sqlite"
            with (
                mock.patch.object(build, "ANALYTICS_DB", output),
                mock.patch.object(
                    build.raw_segments,
                    "reconcile_pending_rotation",
                    side_effect=build.raw_segments.ManifestError("bad rotation marker"),
                ),
            ):
                with self.assertRaises(build.raw_segments.ManifestError):
                    build.build()

    def test_full_build_failure_preserves_database_and_removes_temporary_artifacts(self) -> None:
        build = load_module("build_temp_cleanup_failure_test", ROOT / "scripts" / "build_analytics.py")
        with tempfile.TemporaryDirectory() as tmp:
            output = pathlib.Path(tmp) / "bola.sqlite"
            output.write_bytes(b"previous database")

            with (
                mock.patch.object(build, "ANALYTICS_DB", output),
                mock.patch.object(build.raw_segments, "reconcile_apply_marker", return_value=None),
                mock.patch.object(build.raw_segments, "reconcile_pending_rotation", return_value=None),
                mock.patch.object(build, "scan_normalized_build_inputs", return_value=(1, set())),
                mock.patch.object(build, "read_threads", return_value={}),
                mock.patch.object(build, "read_edges", return_value=[]),
                mock.patch.object(build, "spawn_turn_contexts", return_value={}),
                mock.patch.object(build, "iter_jsonl", side_effect=RuntimeError("turn scan failed")),
            ):
                with self.assertRaisesRegex(RuntimeError, "turn scan failed"):
                    build.build()

            leftovers = list(output.parent.glob(f".{output.name}.*.tmp*"))
            preserved = output.read_bytes()

        self.assertEqual(preserved, b"previous database")
        self.assertEqual(leftovers, [])

    def test_full_build_sweeps_stale_temp_database_and_sidecars(self) -> None:
        build = load_module("build_stale_temp_sweep_test", ROOT / "scripts" / "build_analytics.py")
        with tempfile.TemporaryDirectory() as tmp:
            output = pathlib.Path(tmp) / "bola.sqlite"
            stale = output.with_name(f".{output.name}.123.tmp")
            stale.write_bytes(b"stale")
            pathlib.Path(str(stale) + "-journal").write_bytes(b"journal")

            with build.full_build_connection(output) as con:
                con.execute("create table ready (id integer primary key)")

            leftovers = list(output.parent.glob(f".{output.name}.*.tmp*"))
            output_exists = output.exists()

        self.assertTrue(output_exists)
        self.assertEqual(leftovers, [])

    def test_full_build_rejects_symlinked_stale_temp_artifact(self) -> None:
        build = load_module("build_stale_temp_symlink_test", ROOT / "scripts" / "build_analytics.py")
        with tempfile.TemporaryDirectory() as tmp:
            output = pathlib.Path(tmp) / "bola.sqlite"
            external = pathlib.Path(tmp) / "external.sqlite"
            external.write_bytes(b"external")
            stale = output.with_name(f".{output.name}.123.tmp")
            stale.symlink_to(external)

            with self.assertRaises(build.BuildInputError) as raised:
                with build.full_build_connection(output):
                    pass

            self.assertEqual(raised.exception.payload["error"], "analytics_temp_artifact_unsafe")
            self.assertEqual(external.read_bytes(), b"external")

    def test_build_reconciles_raw_segments_from_configured_output_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            default_dir = pathlib.Path(tmp) / "default-dir"
            target_dir = pathlib.Path(tmp) / "target-dir"
            with mock.patch.dict(
                os.environ,
                {"CODEX_HOME": str(default_dir), "BOLA_OUTPUT_DIR": str(default_dir / "bola")},
                clear=False,
            ):
                build = load_module("build_configured_raw_root_test", ROOT / "scripts" / "build_analytics.py")
            normalized = target_dir / "bola" / "normalized" / "prompt-usage.normalized.jsonl"
            args = argparse.Namespace(
                normalized_log=str(normalized),
                state_db=str(target_dir / "state_5.sqlite"),
                project_root=[],
            )
            with mock.patch.dict(
                os.environ,
                {"CODEX_HOME": str(default_dir), "BOLA_OUTPUT_DIR": str(default_dir / "bola")},
                clear=False,
            ):
                build.configure_paths(args)
            observed: list[pathlib.Path] = []

            def record_reconcile(base: pathlib.Path) -> None:
                observed.append(pathlib.Path(base))

            with (
                mock.patch.object(build.raw_segments, "reconcile_apply_marker", side_effect=record_reconcile),
                mock.patch.object(build.raw_segments, "reconcile_pending_rotation", side_effect=record_reconcile),
                mock.patch.object(build, "scan_normalized_build_inputs", return_value=(0, set())),
                mock.patch.object(build, "read_threads", return_value={}),
                mock.patch.object(build, "read_edges", return_value=[]),
                mock.patch.object(build, "spawn_turn_contexts", return_value=[]),
                mock.patch.object(build, "iter_jsonl", return_value=[]),
                mock.patch.object(build, "extract_tool_calls", return_value=[]),
            ):
                build.build()

        self.assertEqual(observed, [default_dir / "bola", default_dir / "bola"])

    def test_incremental_pipeline_builds_when_normalized_is_ahead_of_db(self) -> None:
        normalize = load_module("normalize_pipeline_version_test", ROOT / "scripts" / "normalize.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            codex_dir = pathlib.Path(tmp_dir) / ".codex"
            base = codex_dir / "bola"
            normalized = base / "normalized" / "prompt-usage.normalized.jsonl"
            state_file = base / "normalized" / "normalize-state.json"
            db_path = base / "analytics" / "bola.sqlite"
            current_dir = base / "raw" / "current"
            state_dir = base / "state"
            current_dir.mkdir(parents=True)
            state_dir.mkdir(parents=True)
            normalized.parent.mkdir(parents=True)
            current_path = current_dir / "prompt-usage.raw.jsonl.current.1779235200000000000.jsonl"
            current_path.write_text("", encoding="utf-8")
            pointer = {
                "schema_version": 1,
                "base": str(base.resolve()),
                "current": {
                    "prompt_usage": {
                        "id": "prompt-usage.raw.jsonl.current.1779235200000000000",
                        "kind": "prompt_usage",
                        "path": str(current_path),
                        "source_name": "prompt-usage.raw.jsonl",
                    }
                },
            }
            (state_dir / "current-raw-segments.json").write_text(json.dumps(pointer, separators=(",", ":")) + "\n", encoding="utf-8")
            first_row = json.dumps(_turn_normalized("s1", "t1", total=100)) + "\n"
            second_row = json.dumps(_turn_normalized("s2", "t2", total=200)) + "\n"
            normalized.write_text(first_row, encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "build_analytics.py"),
                    "--normalized-log",
                    str(normalized),
                    "--state-db",
                    str(codex_dir / "missing-state.sqlite"),
                ],
                cwd=ROOT,
                check=True,
                text=True,
                capture_output=True,
                env={**os.environ, "CODEX_HOME": str(codex_dir), "BOLA_OUTPUT_DIR": str(base)},
            )
            normalized.write_text(first_row + second_row, encoding="utf-8")
            state_file.write_text(
                json.dumps(
                    {
                        "logic_version": normalize.NORMALIZE_LOGIC_VERSION,
                        "sources": {
                            str(current_path): current_path.stat().st_size,
                        },
                        "processed_segments": {},
                        "normalized_log_size": normalized.stat().st_size,
                    },
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "bola.py"),
                    "pipeline",
                    "--codex-dir",
                    str(codex_dir),
                    "--output-dir",
                    str(base),
                    "--state-db",
                    str(codex_dir / "missing-state.sqlite"),
                    "--incremental",
                ],
                cwd=ROOT,
                check=True,
                text=True,
                capture_output=True,
            )
            metadata = json.loads(result.stdout.splitlines()[-1])
            con = sqlite3.connect(db_path)
            try:
                self.assertEqual(con.execute("select count(*) from turns").fetchone()[0], 2)
                self.assertEqual(metadata["processed_turn_log_rows"], 1)
            finally:
                con.close()

    def test_pipeline_recovery_is_explicit(self) -> None:
        cli = load_module("bola_recovery_test", ROOT / "scripts" / "bola.py")
        calls: list[tuple[str, tuple[str, ...]]] = []

        def fake_run_script(name, extra_args, env=None):
            calls.append((name, tuple(extra_args)))
            return 0

        def fake_run_script_json(name, extra_args, env=None):
            calls.append((name, tuple(extra_args)))
            if name == "normalize.py":
                return 0, {"mode": "incremental", "normalized_turns_size": 0}, "{}", ""
            return 0, {}, "{}", ""

        args = types.SimpleNamespace(codex_dir=None, state_db=None, output=None, project_root=None, incremental=True, recover=False)
        with (
            mock.patch.object(cli, "run_script", fake_run_script),
            mock.patch.object(cli, "run_script_json", fake_run_script_json),
            mock.patch.object(cli, "read_analytics_metadata", return_value={}),
        ):
            self.assertEqual(cli.pipeline(args), 0)
        self.assertNotIn(("reconcile.py", ()), calls)

        calls.clear()
        args.recover = True
        with (
            mock.patch.object(cli, "run_script", fake_run_script),
            mock.patch.object(cli, "run_script_json", fake_run_script_json),
            mock.patch.object(cli, "read_analytics_metadata", return_value={}),
        ):
            self.assertEqual(cli.pipeline(args), 0)
        self.assertEqual(calls[0], ("reconcile.py", ()))

    def test_service_lock_rejects_concurrent_owner(self) -> None:
        service_lock = load_module("service_lock_exclusive_test", ROOT / "scripts" / "service_lock.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            lock_path = pathlib.Path(tmp_dir) / "token-usage.lock"
            with service_lock.acquire_service_lock(lock_path=lock_path, reason="outer"):
                with self.assertRaises(service_lock.ServiceLockBusy):
                    with service_lock.acquire_service_lock(lock_path=lock_path, reason="inner"):
                        pass
            self.assertEqual(stat.S_IMODE(lock_path.stat().st_mode), 0o600)

    def test_service_lock_ignores_stale_inherited_env_without_valid_fd(self) -> None:
        service_lock = load_module("service_lock_stale_env_test", ROOT / "scripts" / "service_lock.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            lock_path = pathlib.Path(tmp_dir) / "token-usage.lock"
            with mock.patch.dict(
                os.environ,
                {
                    "BOLA_LOCK_HELD": "1",
                    "BOLA_LOCK_PATH": str(lock_path),
                },
                clear=False,
            ):
                with service_lock.acquire_service_lock(lock_path=lock_path, reason="owner") as lock:
                    self.assertIsNotNone(lock.fd)

    def test_service_lock_ignores_inherited_env_for_different_requested_lock_path(self) -> None:
        service_lock = load_module("service_lock_mismatched_env_test", ROOT / "scripts" / "service_lock.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            outer_path = pathlib.Path(tmp_dir) / "outer.lock"
            requested_path = pathlib.Path(tmp_dir) / "requested.lock"
            with service_lock.acquire_service_lock(lock_path=outer_path, reason="outer") as outer:
                inherited_env = service_lock.child_lock_env(lock_path=outer.path, lock_fd=outer.fd)
                with mock.patch.dict(os.environ, inherited_env, clear=False):
                    with service_lock.acquire_service_lock(lock_path=requested_path, reason="requested") as lock:
                        self.assertEqual(lock.path, requested_path)
                        self.assertNotEqual(os.fstat(lock.fd).st_ino, os.fstat(outer.fd).st_ino)

    def test_pipeline_passes_held_service_lock_to_children(self) -> None:
        cli = load_module("bola_lock_env_test", ROOT / "scripts" / "bola.py")
        child_envs: list[dict[str, str]] = []

        def fake_run_script_json(name, extra_args, env=None):
            child_envs.append(dict(env or {}))
            if name == "normalize.py":
                return 0, {"mode": "incremental", "normalized_turns_size": 1}, "{}", ""
            return 0, {}, "{}", ""

        args = types.SimpleNamespace(codex_dir=None, state_db=None, output=None, project_root=None, incremental=True, recover=False)
        with tempfile.TemporaryDirectory() as tmp_dir:
            lock_path = pathlib.Path(tmp_dir) / "token-usage.lock"
            with (
                mock.patch.object(cli.service_lock, "default_lock_path", return_value=lock_path),
                mock.patch.object(cli, "run_script_json", fake_run_script_json),
                mock.patch.object(cli, "read_analytics_metadata", return_value={}),
            ):
                self.assertEqual(cli.pipeline(args), 0)

        self.assertTrue(child_envs)
        self.assertTrue(all(env.get("BOLA_LOCK_HELD") == "1" for env in child_envs))
        self.assertTrue(all(env.get("BOLA_LOCK_PATH") for env in child_envs))
        self.assertTrue(all(env.get("BOLA_LOCK_FD") for env in child_envs))

    def test_reconcile_cli_runs_under_service_lock(self) -> None:
        cli = load_module("bola_reconcile_lock_test", ROOT / "scripts" / "bola.py")
        calls: list[tuple[str, list[str], dict[str, str]]] = []

        def fake_run_script(name, extra_args, env=None):
            calls.append((name, list(extra_args), dict(env or {})))
            return 0

        with tempfile.TemporaryDirectory() as tmp_dir:
            lock_path = pathlib.Path(tmp_dir) / "token-usage.lock"
            with mock.patch.object(cli.service_lock, "default_lock_path", return_value=lock_path), mock.patch.object(cli, "run_script", fake_run_script):
                with mock.patch.object(cli.sys, "argv", ["bola.py", "reconcile", "--flag"]):
                    self.assertEqual(cli.main(), 0)

        self.assertEqual(calls[0][0:2], ("reconcile.py", ["--flag"]))
        self.assertEqual(calls[0][2].get("BOLA_LOCK_HELD"), "1")

    def test_incremental_analyze_rotates_current_segment_before_build(self) -> None:
        cli = load_module("cli_analyze_rotate_test", ROOT / "scripts" / "bola.py")
        calls: list[tuple[str, list[str]]] = []
        lock_path = pathlib.Path(tempfile.gettempdir()) / f"token-usage-{time.time_ns()}.lock"
        codex_dir = pathlib.Path(tempfile.gettempdir()) / f"codex-dir-{time.time_ns()}"
        output_path = codex_dir / "bola" / "analytics" / f"out-{time.time_ns()}.sqlite"

        def fake_run_script_json(name, extra_args, env=None):
            calls.append((name, list(extra_args)))
            if name == "normalize.py":
                return 0, {"mode": "incremental", "normalized_turns_size": 10}, "{}", ""
            if name == "build_analytics.py":
                return 0, {"turn_rows": 1}, "{}", ""
            if name == "compact_raw.py":
                return 0, {"prompt_usage": {"closed_segment": {"id": "p1"}, "current_segment": {"id": "p2"}}}, "{}", ""
            raise AssertionError(name)

        args = argparse.Namespace(
            incremental=True,
            recover=False,
            skip_rotate=False,
            output=str(output_path),
            codex_dir=str(codex_dir),
            output_dir=str(output_path.parents[1]),
            state_db=None,
            project_root=None,
        )
        with (
            mock.patch.object(cli, "run_script_json", fake_run_script_json),
            mock.patch.object(cli, "read_analytics_metadata", return_value={"applied_normalized_turns_size": 0}),
            mock.patch.object(cli.service_lock, "default_lock_path", return_value=lock_path),
        ):
            result = cli.pipeline(args)
        self.assertEqual(result, 0)
        self.assertLess(calls.index(("compact_raw.py", ["--rotate-current"])), calls.index(("normalize.py", ["--incremental"])))

    def test_incremental_analyze_keeps_incremental_build_after_non_empty_rotation(self) -> None:
        cli = load_module("cli_analyze_non_empty_rotate_incremental_test", ROOT / "scripts" / "bola.py")
        calls: list[tuple[str, list[str]]] = []
        lock_path = pathlib.Path(tempfile.gettempdir()) / f"token-usage-{time.time_ns()}.lock"
        codex_dir = pathlib.Path(tempfile.gettempdir()) / f"codex-dir-{time.time_ns()}"
        output_path = codex_dir / "bola" / "analytics" / f"out-{time.time_ns()}.sqlite"

        def fake_run_script_json(name, extra_args, env=None):
            calls.append((name, list(extra_args)))
            if name == "compact_raw.py":
                return 0, {"prompt_usage": {"closed_segment": {"id": "p1", "rows": 1}, "current_segment": {"id": "p2"}}}, "{}", ""
            if name == "normalize.py":
                return 0, {"mode": "incremental", "normalized_turns_size": 20}, "{}", ""
            if name == "build_analytics.py":
                return 0, {"analysis_mode": "incremental", "turn_rows": 2}, "{}", ""
            raise AssertionError(name)

        args = argparse.Namespace(
            incremental=True,
            recover=False,
            skip_rotate=False,
            output=str(output_path),
            codex_dir=str(codex_dir),
            output_dir=str(output_path.parents[1]),
            state_db=None,
            project_root=None,
        )
        with (
            mock.patch.object(cli, "run_script_json", fake_run_script_json),
            mock.patch.object(cli, "read_analytics_metadata", return_value={"applied_normalized_turns_size": 10, "applied_input_fingerprint": "same"}),
            mock.patch.object(cli, "analysis_input_fingerprint", return_value="same"),
            mock.patch.object(cli.service_lock, "default_lock_path", return_value=lock_path),
        ):
            result = cli.pipeline(args)

        self.assertEqual(result, 0)
        self.assertIn(("normalize.py", ["--incremental"]), calls)
        self.assertIn(("build_analytics.py", ["--incremental", "--turns-offset", "10"]), calls)

    def test_noop_incremental_analyze_rotates_current_segment_before_noop_check(self) -> None:
        cli = load_module("cli_noop_analyze_rotate_test", ROOT / "scripts" / "bola.py")
        calls: list[tuple[str, list[str]]] = []
        lock_path = pathlib.Path(tempfile.gettempdir()) / f"token-usage-{time.time_ns()}.lock"
        codex_dir = pathlib.Path(tempfile.gettempdir()) / f"codex-dir-{time.time_ns()}"
        output_path = codex_dir / "bola" / "analytics" / f"out-{time.time_ns()}.sqlite"

        def fake_run_script_json(name, extra_args, env=None):
            calls.append((name, list(extra_args)))
            if name == "normalize.py":
                return 0, {"mode": "incremental", "normalized_turns_size": 10}, "{}", ""
            if name == "compact_raw.py":
                return 0, {"prompt_usage": {"closed_segment": {"id": "p1"}, "current_segment": {"id": "p2"}}}, "{}", ""
            if name == "build_analytics.py":
                return 0, {"turn_rows": 1, "analysis_mode": "incremental"}, "{}", ""
            raise AssertionError(name)

        args = argparse.Namespace(
            incremental=True,
            recover=False,
            skip_rotate=False,
            output=str(output_path),
            codex_dir=str(codex_dir),
            output_dir=str(output_path.parents[1]),
            state_db=None,
            project_root=None,
        )
        with (
            mock.patch.object(cli, "run_script_json", fake_run_script_json),
            mock.patch.object(cli, "read_analytics_metadata", return_value={"applied_normalized_turns_size": 10, "applied_input_fingerprint": "same"}),
            mock.patch.object(cli, "analysis_input_fingerprint", return_value="same"),
            mock.patch.object(cli.service_lock, "default_lock_path", return_value=lock_path),
        ):
            result = cli.pipeline(args)
        self.assertEqual(result, 0)
        self.assertEqual(calls[0], ("compact_raw.py", ["--rotate-current"]))
        self.assertIn(("build_analytics.py", ["--incremental", "--turns-offset", "10"]), calls)

    def test_noop_incremental_analyze_uses_context_snapshot_when_fingerprint_changes(self) -> None:
        cli = load_module("cli_noop_context_fingerprint_test", ROOT / "scripts" / "bola.py")
        calls: list[tuple[str, list[str]]] = []
        lock_path = pathlib.Path(tempfile.gettempdir()) / f"token-usage-{time.time_ns()}.lock"
        codex_dir = pathlib.Path(tempfile.gettempdir()) / f"codex-dir-{time.time_ns()}"
        output_path = codex_dir / "bola" / "analytics" / f"out-{time.time_ns()}.sqlite"

        def fake_run_script_json(name, extra_args, env=None):
            calls.append((name, list(extra_args)))
            if name == "compact_raw.py":
                return 0, {}, "{}", ""
            if name == "normalize.py":
                return 0, {"mode": "incremental", "normalized_turns_size": 10}, "{}", ""
            if name == "build_analytics.py":
                return 0, {"turn_rows": 1}, "{}", ""
            raise AssertionError(name)

        args = argparse.Namespace(
            incremental=True,
            recover=False,
            skip_rotate=False,
            output=str(output_path),
            codex_dir=str(codex_dir),
            output_dir=str(output_path.parents[1]),
            state_db=None,
            project_root=None,
        )
        with (
            mock.patch.object(cli, "run_script_json", fake_run_script_json),
            mock.patch.object(cli, "read_analytics_metadata", return_value={"applied_normalized_turns_size": 10, "applied_input_fingerprint": "old"}),
            mock.patch.object(cli, "analysis_input_fingerprint", return_value="new"),
            mock.patch.object(cli.service_lock, "default_lock_path", return_value=lock_path),
        ):
            result = cli.pipeline(args)

        self.assertEqual(result, 0)
        self.assertIn(("build_analytics.py", ["--incremental", "--turns-offset", "10"]), calls)

    def test_incremental_analyze_rebuilds_full_when_applied_offset_exceeds_normalized_size(self) -> None:
        cli = load_module("cli_oversized_applied_offset_test", ROOT / "scripts" / "bola.py")
        calls: list[tuple[str, list[str]]] = []
        lock_path = pathlib.Path(tempfile.gettempdir()) / f"token-usage-{time.time_ns()}.lock"
        codex_dir = pathlib.Path(tempfile.gettempdir()) / f"codex-dir-{time.time_ns()}"
        output_path = codex_dir / "bola" / "analytics" / f"out-{time.time_ns()}.sqlite"

        def fake_run_script_json(name, extra_args, env=None):
            calls.append((name, list(extra_args)))
            if name == "compact_raw.py":
                return 0, {}, "{}", ""
            if name == "normalize.py":
                return 0, {"mode": "incremental", "normalized_turns_size": 10}, "{}", ""
            if name == "build_analytics.py":
                return 0, {"turn_rows": 1}, "{}", ""
            raise AssertionError(name)

        args = argparse.Namespace(
            incremental=True,
            recover=False,
            skip_rotate=False,
            output=str(output_path),
            codex_dir=str(codex_dir),
            output_dir=str(output_path.parents[1]),
            state_db=None,
            project_root=None,
        )
        with (
            mock.patch.object(cli, "run_script_json", fake_run_script_json),
            mock.patch.object(cli, "read_analytics_metadata", return_value={"applied_normalized_turns_size": 20, "applied_input_fingerprint": "same"}),
            mock.patch.object(cli, "analysis_input_fingerprint", return_value="same"),
            mock.patch.object(cli.service_lock, "default_lock_path", return_value=lock_path),
        ):
            result = cli.pipeline(args)

        self.assertEqual(result, 0)
        self.assertIn(("build_analytics.py", []), calls)

    def test_analysis_input_fingerprint_uses_shared_path_digest(self) -> None:
        helper = load_module("analysis_inputs_shared_digest_test", ROOT / "scripts" / "analysis_inputs.py")
        cli = load_module("cli_shared_digest_test", ROOT / "scripts" / "bola.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            codex_dir = pathlib.Path(tmp_dir) / "codex-dir"
            state_db = codex_dir / "state_5.sqlite"
            session_index = codex_dir / "session_index.jsonl"
            pruned = codex_dir / "bola" / "state" / "retention-pruned-turns.json"
            pruned.parent.mkdir(parents=True)
            state_db.parent.mkdir(parents=True, exist_ok=True)
            state_db.write_text("state\n", encoding="utf-8")
            session_index.write_text("session\n", encoding="utf-8")
            pruned.write_text("{}\n", encoding="utf-8")

            with mock.patch.dict(
                os.environ,
                {
                    "CODEX_HOME": str(codex_dir),
                    "BOLA_OUTPUT_DIR": str(codex_dir / "bola"),
                    "BOLA_STATE_DB": str(state_db),
                },
            ):
                build = load_module("build_shared_digest_test", ROOT / "scripts" / "build_analytics.py")

            expected = helper.analysis_input_fingerprint(codex_dir, state_db, codex_dir / "bola")

            self.assertEqual(cli.analysis_input_fingerprint(str(codex_dir), str(state_db), str(codex_dir / "bola")), expected)
            self.assertEqual(build.analysis_input_fingerprint(), expected)

    def test_analysis_input_fingerprint_tracks_retention_sqlite_store(self) -> None:
        helper = load_module("analysis_inputs_retention_store_test", ROOT / "scripts" / "analysis_inputs.py")
        store = load_module("retention_pruned_store_fingerprint_test", ROOT / "scripts" / "retention_pruned_store.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            codex_dir = root / "codex-dir"
            state_db = codex_dir / "state_5.sqlite"
            output_dir = root / "output"
            state_db.parent.mkdir(parents=True)
            state_db.write_text("state\n", encoding="utf-8")

            before = helper.analysis_input_fingerprint(codex_dir, state_db, output_dir)
            job_id = store.stage_rows(
                output_dir,
                [
                    {
                        "session_id": "session",
                        "turn_id": "turn",
                        "start_ts": 1.0,
                        "stop_ts": 2.0,
                        "captured_at_unix": 2.0,
                    }
                ],
                pruned_at_unix=3.0,
                job_id="job",
            )
            staged = helper.analysis_input_fingerprint(codex_dir, state_db, output_dir)
            store.commit_stage(output_dir, job_id)
            committed = helper.analysis_input_fingerprint(codex_dir, state_db, output_dir)

            self.assertNotEqual(before, staged)
            self.assertNotEqual(staged, committed)
            self.assertEqual(committed, helper.analysis_input_fingerprint(codex_dir, state_db, output_dir))
            self.assertEqual(
                helper.analysis_input_paths(codex_dir, state_db, output_dir)[2:],
                store.fingerprint_paths(output_dir),
            )

    def test_incremental_pipeline_data_root_controls_default_output(self) -> None:
        cli = load_module("cli_codex_dir_default_output_test", ROOT / "scripts" / "bola.py")
        calls: list[tuple[str, list[str]]] = []
        observed_metadata_outputs: list[str | None] = []
        lock_path = pathlib.Path(tempfile.gettempdir()) / f"token-usage-{time.time_ns()}.lock"
        codex_dir = pathlib.Path(tempfile.gettempdir()) / f"codex-dir-{time.time_ns()}"
        output_dir = pathlib.Path(tempfile.gettempdir()) / f"token-data-{time.time_ns()}"
        expected_output = str(output_dir / "analytics" / "bola.sqlite")

        def fake_run_script_json(name, extra_args, env=None):
            calls.append((name, list(extra_args)))
            if name == "compact_raw.py":
                return 0, {}, "{}", ""
            if name == "normalize.py":
                return 0, {"mode": "incremental", "normalized_turns_size": 0}, "{}", ""
            if name == "build_analytics.py":
                return 0, {"turn_rows": 0, "analysis_mode": "incremental"}, "{}", ""
            raise AssertionError(name)

        def fake_read_metadata(output):
            observed_metadata_outputs.append(output)
            return {"applied_normalized_turns_size": 0, "applied_input_fingerprint": "same"}

        args = argparse.Namespace(
            incremental=True,
            recover=False,
            skip_rotate=False,
            output=None,
            codex_dir=str(codex_dir),
            output_dir=str(output_dir),
            state_db=None,
            project_root=None,
        )
        with (
            mock.patch.object(cli, "run_script_json", fake_run_script_json),
            mock.patch.object(cli, "read_analytics_metadata", fake_read_metadata),
            mock.patch.object(cli, "analysis_input_fingerprint", return_value="same"),
            mock.patch.object(cli.service_lock, "default_lock_path", return_value=lock_path),
        ):
            result = cli.pipeline(args)

        self.assertEqual(result, 0)
        self.assertEqual(observed_metadata_outputs, [expected_output])
        self.assertIn(
            ("build_analytics.py", ["--incremental", "--turns-offset", "0"]),
            calls,
        )

    def test_retention_prune_data_root_defaults_match_pipeline(self) -> None:
        cli = load_module("cli_retention_codex_dir_default_test", ROOT / "scripts" / "bola.py")
        codex_dir = pathlib.Path(tempfile.gettempdir()) / f"codex-dir-{time.time_ns()}"
        output_dir = pathlib.Path(tempfile.gettempdir()) / f"token-data-{time.time_ns()}"
        expected_output = output_dir / "analytics" / "bola.sqlite"

        with mock.patch.dict(os.environ, {"CODEX_HOME": str(codex_dir), "BOLA_OUTPUT_DIR": str(output_dir)}, clear=False):
            retention_args = cli.parse_args(["retention-prune", "--cutoff", "0"])

            self.assertIsNone(retention_args.codex_dir)
            self.assertEqual(cli.pipeline_output_path(None, None), expected_output)
            self.assertEqual(cli.retention_db_path(retention_args.codex_dir, None), expected_output)

    def test_full_normalize_reads_current_prompt_segment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex_dir = pathlib.Path(tmp) / ".codex"
            base = codex_dir / "bola"
            current_dir = base / "raw" / "current"
            state_dir = base / "state"
            current_dir.mkdir(parents=True)
            state_dir.mkdir(parents=True)
            current_path = current_dir / "prompt-usage.raw.jsonl.current.1779235200000000000.jsonl"
            current_path.write_text(
                json.dumps(_turn_raw("s-current", "t-current", total=123) | {"captured_at": "2026-05-20T00:00:00+00:00"}) + "\n", encoding="utf-8"
            )
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

            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "normalize.py")],
                env={**os.environ, "CODEX_HOME": str(codex_dir), "BOLA_OUTPUT_DIR": str(base)},
                check=True,
                capture_output=True,
                text=True,
            )
            normalized = base / "normalized" / "prompt-usage.normalized.jsonl"
            normalized_text = normalized.read_text(encoding="utf-8").replace(" ", "")

        self.assertIn('"rows":1', result.stdout)
        self.assertIn('"turn_id":"t-current"', normalized_text)

    def test_skip_rotate_incremental_pipeline_reads_current_segments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex_dir = pathlib.Path(tmp) / ".codex"
            base = codex_dir / "bola"
            raw_dir = base / "raw"
            current_dir = raw_dir / "current"
            state_dir = base / "state"
            current_dir.mkdir(parents=True)
            state_dir.mkdir(parents=True)
            prompt_path = current_dir / "prompt-usage.raw.jsonl.current.1779235200000000000.jsonl"
            prompt_path.write_text(
                json.dumps(_turn_raw("s-current", "t-current", total=123) | {"captured_at": "2026-05-20T00:00:00+00:00"}) + "\n", encoding="utf-8"
            )
            pointer = {
                "schema_version": 1,
                "base": str(base.resolve()),
                "current": {
                    "prompt_usage": {
                        "id": "prompt-usage.raw.jsonl.current.1779235200000000000",
                        "kind": "prompt_usage",
                        "path": str(prompt_path),
                        "source_name": "prompt-usage.raw.jsonl",
                        "created_at_unix": 1779235200.0,
                    },
                },
            }
            (state_dir / "current-raw-segments.json").write_text(json.dumps(pointer) + "\n", encoding="utf-8")
            db_path = base / "analytics" / "bola.sqlite"

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "bola.py"),
                    "pipeline",
                    "--incremental",
                    "--skip-rotate",
                    "--codex-dir",
                    str(codex_dir),
                    "--output-dir",
                    str(base),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            con = sqlite3.connect(db_path)
            try:
                total = con.execute("select total_tokens from turns where session_id='s-current' and turn_id='t-current'").fetchone()[0]
            finally:
                con.close()

        self.assertIn('"analysis_mode":"full"', result.stdout)
        self.assertEqual(total, 123)

    def test_full_analyze_rotates_current_segment_before_normalize(self) -> None:
        cli = load_module("cli_full_analyze_rotate_test", ROOT / "scripts" / "bola.py")
        calls: list[tuple[str, list[str]]] = []
        lock_path = pathlib.Path(tempfile.gettempdir()) / f"token-usage-{time.time_ns()}.lock"
        codex_dir = pathlib.Path(tempfile.gettempdir()) / f"codex-dir-{time.time_ns()}"
        output_path = codex_dir / "bola" / "analytics" / f"out-{time.time_ns()}.sqlite"

        def fake_run_script_json(name, extra_args, env=None):
            calls.append((name, list(extra_args)))
            if name == "compact_raw.py":
                return 0, {"prompt_usage": {"closed_segment": {"id": "p1"}, "current_segment": {"id": "p2"}}}, "{}", ""
            if name == "normalize.py":
                return 0, {"mode": "full", "normalized_turns_size": 2}, "{}", ""
            if name == "build_analytics.py":
                return 0, {"analysis_mode": "full", "turn_rows": 2}, "{}", ""
            raise AssertionError(name)

        def fake_run_script(name, extra_args, env=None):
            calls.append((name, list(extra_args)))
            return 0

        args = argparse.Namespace(
            incremental=False,
            recover=False,
            skip_rotate=False,
            output=str(output_path),
            codex_dir=str(codex_dir),
            output_dir=str(output_path.parents[1]),
            state_db=None,
            project_root=None,
        )
        stdout = io.StringIO()
        with (
            mock.patch.object(cli, "run_script_json", fake_run_script_json),
            mock.patch.object(cli, "run_script", fake_run_script),
            mock.patch.object(cli.service_lock, "default_lock_path", return_value=lock_path),
            mock.patch("sys.stdout", stdout),
        ):
            result = cli.pipeline(args)
        self.assertEqual(result, 0)
        self.assertLess(calls.index(("compact_raw.py", ["--rotate-current"])), calls.index(("normalize.py", [])))
        output = stdout.getvalue().strip()
        self.assertTrue(output, "full pipeline should print final JSON")
        payload = json.loads(output)
        self.assertEqual(payload["normalize"]["mode"], "full")
        self.assertEqual(payload["analysis_mode"], "full")
        self.assertIn("pre_analysis_rotate", payload)
        self.assertEqual(payload["pre_analysis_rotate"]["prompt_usage"]["closed_segment"]["id"], "p1")

    def test_current_segment_row_is_visible_after_one_analyze(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex_dir = pathlib.Path(tmp) / ".codex"
            base = codex_dir / "bola"
            current_dir = base / "raw" / "current"
            current_dir.mkdir(parents=True)
            current_path = current_dir / "prompt-usage.raw.jsonl.current.1779235200000000000.jsonl"
            current_path.write_text(
                json.dumps(_turn_raw("s-current", "t-current", total=123) | {"captured_at": "2026-05-20T00:00:00+00:00"}) + "\n", encoding="utf-8"
            )
            pointer = {
                "current": {
                    "prompt_usage": {
                        "id": "prompt-usage.raw.jsonl.current.1779235200000000000",
                        "kind": "prompt_usage",
                        "path": str(current_path),
                        "source_name": "prompt-usage.raw.jsonl",
                        "created_at_unix": 1779235200.0,
                    }
                }
            }
            (base / "state").mkdir(parents=True)
            (base / "state" / "current-raw-segments.json").write_text(
                json.dumps({"schema_version": 1, "base": str(base.resolve()), **pointer}) + "\n", encoding="utf-8"
            )
            db_path = base / "analytics" / "bola.sqlite"
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "bola.py"),
                    "pipeline",
                    "--incremental",
                    "--codex-dir",
                    str(codex_dir),
                    "--output-dir",
                    str(base),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn('"turn_rows":1', result.stdout)
            con = sqlite3.connect(db_path)
            try:
                self.assertEqual(con.execute("select total_tokens from turns where session_id='s-current' and turn_id='t-current'").fetchone()[0], 123)
            finally:
                con.close()

    def test_current_segment_row_is_visible_after_existing_incremental_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex_dir = pathlib.Path(tmp) / ".codex"
            base = codex_dir / "bola"
            raw_dir = base / "raw"
            normalized_dir = base / "normalized"
            analytics_dir = base / "analytics"
            current_dir = raw_dir / "current"
            current_dir.mkdir(parents=True)
            raw_dir.mkdir(parents=True, exist_ok=True)
            normalized_dir.mkdir(parents=True)
            analytics_dir.mkdir(parents=True)
            (raw_dir / "prompt-usage.raw.jsonl").write_text("", encoding="utf-8")
            (normalized_dir / "prompt-usage.normalized.jsonl").write_text(
                json.dumps(_turn_normalized("s-existing", "t-existing", total=111)) + "\n", encoding="utf-8"
            )
            normalize = load_module("normalize_existing_incremental_state_test", ROOT / "scripts" / "normalize.py")
            (normalized_dir / "normalize-state.json").write_text(
                json.dumps(
                    {
                        "logic_version": normalize.NORMALIZE_LOGIC_VERSION,
                        "sources": {str(raw_dir / "prompt-usage.raw.jsonl"): 0},
                        "processed_segments": {},
                        "normalized_log_size": (normalized_dir / "prompt-usage.normalized.jsonl").stat().st_size,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            db_path = analytics_dir / "bola.sqlite"
            test_env = {**os.environ, "CODEX_HOME": str(codex_dir), "BOLA_OUTPUT_DIR": str(base)}
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "build_analytics.py"),
                    "--normalized-log",
                    str(normalized_dir / "prompt-usage.normalized.jsonl"),
                ],
                check=True,
                capture_output=True,
                text=True,
                env=test_env,
            )
            current_path = current_dir / "prompt-usage.raw.jsonl.current.1779235200000000000.jsonl"
            current_path.write_text(
                json.dumps(_turn_raw("s-current", "t-current", total=123) | {"captured_at": "2026-05-20T00:00:00+00:00"}) + "\n", encoding="utf-8"
            )
            pointer = {
                "current": {
                    "prompt_usage": {
                        "id": "prompt-usage.raw.jsonl.current.1779235200000000000",
                        "kind": "prompt_usage",
                        "path": str(current_path),
                        "source_name": "prompt-usage.raw.jsonl",
                        "created_at_unix": 1779235200.0,
                    }
                }
            }
            (base / "state").mkdir(parents=True, exist_ok=True)
            (base / "state" / "current-raw-segments.json").write_text(
                json.dumps({"schema_version": 1, "base": str(base.resolve()), **pointer}) + "\n", encoding="utf-8"
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "bola.py"),
                    "pipeline",
                    "--incremental",
                    "--codex-dir",
                    str(codex_dir),
                    "--output-dir",
                    str(base),
                ],
                check=True,
                capture_output=True,
                text=True,
                env=test_env,
            )
            payload = json.loads(result.stdout)
            self.assertEqual(payload["normalize"]["mode"], "incremental")
            self.assertEqual(payload["turn_rows"], 2)
            con = sqlite3.connect(db_path)
            try:
                self.assertEqual(con.execute("select total_tokens from turns where session_id='s-current' and turn_id='t-current'").fetchone()[0], 123)
                self.assertEqual(con.execute("select total_tokens from turns where session_id='s-existing' and turn_id='t-existing'").fetchone()[0], 111)
            finally:
                con.close()

    def test_compact_custom_raw_paths_rotate_current_segments_without_active_rewrite(self) -> None:
        compact = load_module("compact_raw_selected_sources_test", ROOT / "scripts" / "compact_raw.py")
        raw_segments = load_module("raw_segments_compact_selected_sources_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir) / "token-usage"
            compact.BASE_DIR = base
            current = raw_segments.ensure_current_segment(base, kind="prompt_usage", source_name="prompt-usage.raw.jsonl")
            pathlib.Path(current["path"]).write_text('{"p":1}\n{"p":2}\n', encoding="utf-8")
            args = types.SimpleNamespace(rotate_current=True)
            result = compact.compact(args)

            self.assertEqual(result["metadata"]["raw_rotation_mode"], "current_segment_pointer")
            self.assertIn("current_segment", result["prompt_usage"])
            self.assertNotIn("model_calls", result)
            self.assertEqual(pathlib.Path(result["prompt_usage"]["closed_segment"]["path"]).read_text(encoding="utf-8"), '{"p":1}\n{"p":2}\n')

