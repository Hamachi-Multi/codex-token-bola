from __future__ import annotations

import threading

try:
    from tests.support import (
        Any,
        ROOT,
        _turn_raw,
        concurrent,
        datetime,
        hashlib,
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
        unittest,
    )
except ModuleNotFoundError:
    from support import (
        Any,
        ROOT,
        _turn_raw,
        concurrent,
        datetime,
        hashlib,
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
        unittest,
    )


class RawSegmentRuntimeTests(unittest.TestCase):
    def test_resolved_turn_usage_falls_back_when_cumulative_counter_resets(self) -> None:
        normalize = load_module("turn_capture_counter_reset_test", ROOT / "scripts" / "normalize.py")
        turn_capture = normalize.turn_capture
        resolved = turn_capture.resolved_turn_usage(
            {
                "start_token_usage": {
                    "input_tokens": 1_000,
                    "cached_input_tokens": 800,
                    "output_tokens": 100,
                    "reasoning_output_tokens": 20,
                    "total_tokens": 1_100,
                },
                "start_usage_source": "tail_token_count",
            },
            {
                "total_token_usage": {
                    "input_tokens": 30,
                    "cached_input_tokens": 20,
                    "output_tokens": 5,
                    "reasoning_output_tokens": 1,
                    "total_tokens": 35,
                },
                "model_calls": [
                    {
                        "usage": {
                            "input_tokens": 30,
                            "cached_input_tokens": 20,
                            "output_tokens": 5,
                            "reasoning_output_tokens": 1,
                            "total_tokens": 35,
                        }
                    }
                ],
            },
        )

        self.assertEqual(resolved.usage["total_tokens"], 35)
        self.assertEqual(resolved.start_usage_source, "counter_reset")
        self.assertTrue(resolved.estimated)

    def test_turn_rows_index_prompt_start_time_separately_from_capture_time(self) -> None:
        build = load_module("build_analytics_prompt_time_test", ROOT / "scripts" / "build_analytics.py")
        con = sqlite3.connect(":memory:")
        try:
            build.setup_db(con)
            build.upsert_turn_row(
                con,
                {
                    "session_id": "s1",
                    "turn_id": "t1",
                    "captured_at": "2026-08-23T07:57:02+00:00",
                    "started_at": "2026-07-19T17:39:06+00:00",
                    "usage": {},
                },
                {},
            )
            stored = con.execute("select captured_at_unix, started_at_unix from turns").fetchone()
        finally:
            con.close()

        self.assertEqual(stored[0], datetime.fromisoformat("2026-08-23T07:57:02+00:00").timestamp())
        self.assertEqual(stored[1], datetime.fromisoformat("2026-07-19T17:39:06+00:00").timestamp())

    def test_raw_segment_time_prefers_prompt_start_over_recovery_capture(self) -> None:
        raw_segments = load_module("raw_segments_prompt_time_test", ROOT / "scripts" / "raw_segments.py")
        row = {
            "started_at": "2026-07-19T17:39:06+00:00",
            "captured_at": "2026-08-23T07:57:02+00:00",
        }

        self.assertEqual(
            raw_segments.row_time(row, kind="prompt_usage"),
            datetime.fromisoformat("2026-07-19T17:39:06+00:00").timestamp(),
        )

    def test_raw_segment_manifest_round_trips_owner_only(self) -> None:
        raw_segments = load_module("raw_segments_manifest_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            manifest_path = base / "state" / "raw-segments-manifest.json"
            segment = {
                "id": "prompt-usage.raw.jsonl.20260524000000.20260524010000.1",
                "kind": "prompt_usage",
                "path": str(base / "raw" / "archive" / "prompt-usage.raw.jsonl.20260524000000.20260524010000.1.jsonl.gz"),
                "format": "jsonl.gz",
                "source_name": "prompt-usage.raw.jsonl",
                "created_at_unix": 1779552000.0,
                "min_time_unix": 1779552000.0,
                "max_time_unix": 1779555600.0,
                "rows": 2,
                "bytes": 100,
                "uncompressed_bytes": 200,
                "sha256": None,
                "status": "closed",
            }
            raw_segments.write_manifest(
                base,
                {"schema_version": 1, "base": str(base.resolve()), "updated_at_unix": 1.0, "segments": [segment]},
            )
            loaded = raw_segments.read_manifest(base)
            self.assertEqual(loaded["segments"][0]["id"], segment["id"])
            self.assertEqual(stat.S_IMODE(manifest_path.stat().st_mode), 0o600)

    def test_current_segment_handoff_closes_old_segment_without_rewriting_it(self) -> None:
        raw_segments = load_module("raw_segments_pointer_handoff_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            current = raw_segments.ensure_current_segment(base, kind="prompt_usage", source_name="prompt-usage.raw.jsonl")
            old_path = pathlib.Path(current["path"])
            old_path.write_text(json.dumps(_turn_raw("s1", "t1", total=100) | {"captured_at": "2026-05-20T00:00:00+00:00"}) + "\n", encoding="utf-8")
            before = old_path.read_bytes()

            result = raw_segments.rotate_current_segment(base=base, kind="prompt_usage", source_name="prompt-usage.raw.jsonl")

            self.assertEqual(old_path.read_bytes(), before)
            self.assertEqual(result["closed_segment"]["path"], str(old_path))
            self.assertTrue(pathlib.Path(result["current_segment"]["path"]).exists())
            self.assertNotEqual(result["closed_segment"]["path"], result["current_segment"]["path"])
            manifest = raw_segments.read_manifest(base)
            self.assertEqual(len(manifest["segments"]), 1)
            self.assertEqual(manifest["segments"][0]["rows"], 1)
            self.assertEqual(manifest["segments"][0]["kind"], "prompt_usage")

    def test_current_segment_handoff_rejects_corrupt_manifest_before_pointer_change(self) -> None:
        raw_segments = load_module("raw_segments_pointer_corrupt_manifest_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            current = raw_segments.ensure_current_segment(base, kind="prompt_usage", source_name="prompt-usage.raw.jsonl")
            pointer_path = base / "state" / "current-raw-segments.json"
            before_pointer = pointer_path.read_bytes()
            manifest = base / "state" / "raw-segments-manifest.json"
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text("{broken-json", encoding="utf-8")

            with self.assertRaises(raw_segments.ManifestError):
                raw_segments.rotate_current_segment(base=base, kind="prompt_usage", source_name="prompt-usage.raw.jsonl")

            self.assertEqual(pointer_path.read_bytes(), before_pointer)
            self.assertEqual(raw_segments.read_current_pointer(base)["current"]["prompt_usage"]["path"], current["path"])

    def test_current_segment_handoff_writes_pointer_before_manifest(self) -> None:
        raw_segments = load_module("raw_segments_pointer_first_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            current = raw_segments.ensure_current_segment(base, kind="prompt_usage", source_name="prompt-usage.raw.jsonl")
            old_path = pathlib.Path(current["path"])
            old_path.write_text(json.dumps(_turn_raw("s1", "t1", total=100) | {"captured_at": "2026-05-20T00:00:00+00:00"}) + "\n", encoding="utf-8")
            observed: list[str] = []
            original_write_current_pointer = raw_segments._rotation.write_current_pointer
            original_write_manifest = raw_segments._rotation.write_manifest

            def spy_pointer(base_arg: pathlib.Path, pointer: dict[str, Any]) -> None:
                observed.append("pointer")
                original_write_current_pointer(base_arg, pointer)

            def spy_manifest(base_arg: pathlib.Path, manifest: dict[str, Any]) -> None:
                observed.append("manifest")
                current_pointer = raw_segments.strict_read_current_pointer(base_arg)
                self.assertNotEqual(current_pointer["current"]["prompt_usage"]["path"], str(old_path))
                original_write_manifest(base_arg, manifest)

            with (
                mock.patch.object(raw_segments._rotation, "write_current_pointer", side_effect=spy_pointer),
                mock.patch.object(raw_segments._rotation, "write_manifest", side_effect=spy_manifest),
            ):
                raw_segments.rotate_current_segment(base=base, kind="prompt_usage", source_name="prompt-usage.raw.jsonl")

            first_pointer = observed.index("pointer")
            self.assertNotIn("manifest", observed[:first_pointer])
            self.assertEqual(observed[-2:], ["pointer", "manifest"])

    def test_current_segment_handoff_leaves_marker_when_manifest_write_fails_after_pointer(self) -> None:
        raw_segments = load_module("raw_segments_pointer_marker_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            current = raw_segments.ensure_current_segment(base, kind="prompt_usage", source_name="prompt-usage.raw.jsonl")
            old_path = pathlib.Path(current["path"])
            old_path.write_text(json.dumps(_turn_raw("s1", "t1", total=100) | {"captured_at": "2026-05-20T00:00:00+00:00"}) + "\n", encoding="utf-8")

            with mock.patch.object(raw_segments._rotation, "write_manifest", side_effect=OSError("manifest write failed")):
                with self.assertRaises(OSError):
                    raw_segments.rotate_current_segment(base=base, kind="prompt_usage", source_name="prompt-usage.raw.jsonl")

            pointer = raw_segments.strict_read_current_pointer(base)
            self.assertNotEqual(pointer["current"]["prompt_usage"]["path"], str(old_path))
            marker = raw_segments.read_pending_rotation(base)
            self.assertEqual(marker["phase"], "manifest_pending")
            self.assertEqual(marker["old_segment"]["path"], str(old_path))
            self.assertEqual(raw_segments.strict_read_manifest(base)["segments"], [])

            raw_segments.reconcile_pending_rotation(base)
            manifest = raw_segments.strict_read_manifest(base)
            self.assertEqual(manifest["segments"][0]["path"], str(old_path))
            self.assertFalse(raw_segments.pending_rotation_path(base).exists())

    def test_pending_rotation_pointer_pending_unlinks_empty_new_segment_on_rollback(self) -> None:
        raw_segments = load_module("raw_segments_pointer_pending_orphan_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            old_segment = raw_segments.ensure_current_segment(base, kind="prompt_usage", source_name="prompt-usage.raw.jsonl")
            new_segment = raw_segments.new_current_segment(base, kind="prompt_usage", source_name="prompt-usage.raw.jsonl")
            new_path = pathlib.Path(new_segment["path"])
            raw_segments.write_pending_rotation(
                base,
                {
                    "operation": "rotate_current_segment",
                    "phase": "pointer_pending",
                    "kind": "prompt_usage",
                    "old_segment": old_segment,
                    "new_segment": new_segment,
                    "created_at_unix": 1.0,
                },
            )

            raw_segments.reconcile_pending_rotation(base)

            self.assertFalse(new_path.exists())
            self.assertIsNone(raw_segments.read_pending_rotation(base))

    def test_current_segment_handoff_keeps_marker_when_old_segment_missing(self) -> None:
        raw_segments = load_module("raw_segments_pointer_missing_old_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            current = raw_segments.ensure_current_segment(base, kind="prompt_usage", source_name="prompt-usage.raw.jsonl")
            old_path = pathlib.Path(current["path"])
            result = raw_segments.rotate_current_segment(base=base, kind="prompt_usage", source_name="prompt-usage.raw.jsonl")
            marker = {
                "operation": "rotate_current_segment",
                "phase": "manifest_pending",
                "kind": "prompt_usage",
                "old_segment": current,
                "new_segment": result["current_segment"],
                "created_at_unix": 1.0,
            }
            raw_segments.write_pending_rotation(base, marker)
            old_path.unlink()

            with self.assertRaises(raw_segments.ManifestError):
                raw_segments.reconcile_pending_rotation(base)

            self.assertTrue(raw_segments.pending_rotation_path(base).exists())

    def test_current_segment_scan_does_not_hold_raw_lock_after_pointer_handoff(self) -> None:
        raw_segments = load_module("raw_segments_short_lock_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            current = raw_segments.ensure_current_segment(base, kind="prompt_usage", source_name="prompt-usage.raw.jsonl")
            pathlib.Path(current["path"]).write_text(
                json.dumps(_turn_raw("s1", "t1", total=100) | {"captured_at": "2026-05-20T00:00:00+00:00"}) + "\n", encoding="utf-8"
            )
            raw_lock_released_before_scan = False
            original_scan = raw_segments._rotation.scan_segment_file

            def delayed_scan(path: pathlib.Path, *, kind: str) -> dict[str, Any]:
                nonlocal raw_lock_released_before_scan
                raw_lock_released_before_scan = raw_segments.raw_segment_lock_available(base)
                return original_scan(path, kind=kind)

            with mock.patch.object(raw_segments._rotation, "scan_segment_file", side_effect=delayed_scan):
                raw_segments.rotate_current_segment(base=base, kind="prompt_usage", source_name="prompt-usage.raw.jsonl")
            self.assertTrue(raw_lock_released_before_scan)

    def test_current_pointer_rejects_existing_path_outside_raw_current(self) -> None:
        raw_segments = load_module("raw_segments_current_pointer_validate_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            outside = base / "raw" / "prompt-usage.raw.jsonl"
            outside.parent.mkdir(parents=True)
            outside.write_text("", encoding="utf-8")
            raw_segments.write_current_pointer(
                base,
                {
                    "current": {
                        "prompt_usage": {
                            "id": "prompt-usage.raw.jsonl.current.bad",
                            "kind": "prompt_usage",
                            "source_name": "prompt-usage.raw.jsonl",
                            "path": str(outside),
                        }
                    }
                },
            )
            with self.assertRaises(raw_segments.ManifestError):
                raw_segments.ensure_current_segment(base, kind="prompt_usage", source_name="prompt-usage.raw.jsonl")

    def test_current_pointer_rejects_missing_existing_segment_file(self) -> None:
        raw_segments = load_module("raw_segments_current_pointer_missing_file_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            missing = base / "raw" / "current" / "prompt-usage.raw.jsonl.current.1777593600000000000.jsonl"
            missing.parent.mkdir(parents=True)
            pointer = {
                "current": {
                    "prompt_usage": {
                        "id": "prompt-usage.raw.jsonl.current.1777593600000000000",
                        "kind": "prompt_usage",
                        "source_name": "prompt-usage.raw.jsonl",
                        "path": str(missing),
                    }
                }
            }
            raw_segments.write_current_pointer(base, pointer)
            before_pointer = raw_segments.current_pointer_path(base).read_bytes()

            with self.assertRaises(raw_segments.ManifestError):
                raw_segments.ensure_current_segment(base, kind="prompt_usage", source_name="prompt-usage.raw.jsonl")

            self.assertEqual(raw_segments.current_pointer_path(base).read_bytes(), before_pointer)
            self.assertFalse(missing.exists())

    def test_current_pointer_rejects_missing_kind(self) -> None:
        raw_segments = load_module("raw_segments_current_pointer_missing_kind_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            segment = base / "raw" / "current" / "prompt-usage.raw.jsonl.current.1777593600000000000.jsonl"
            segment.parent.mkdir(parents=True)
            segment.write_text("", encoding="utf-8")
            raw_segments.write_current_pointer(
                base,
                {
                    "current": {
                        "prompt_usage": {
                            "id": "prompt-usage.raw.jsonl.current.1777593600000000000",
                            "source_name": "prompt-usage.raw.jsonl",
                            "path": str(segment),
                        }
                    }
                },
            )

            with self.assertRaises(raw_segments.ManifestError):
                raw_segments.ensure_current_segment(base, kind="prompt_usage", source_name="prompt-usage.raw.jsonl")

    def test_current_pointer_rejects_symlinked_raw_current_parent(self) -> None:
        raw_segments = load_module("raw_segments_current_symlink_parent_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp) / "service"
            outside = pathlib.Path(tmp) / "outside-current"
            outside.mkdir(parents=True)
            raw_dir = base / "raw"
            raw_dir.mkdir(parents=True)
            (raw_dir / "current").symlink_to(outside, target_is_directory=True)
            segment = {
                "id": "prompt-usage.raw.jsonl.current.1777593600000000000",
                "kind": "prompt_usage",
                "source_name": "prompt-usage.raw.jsonl",
                "path": str(outside / "prompt-usage.raw.jsonl.current.1777593600000000000.jsonl"),
            }

            with self.assertRaises(raw_segments.ManifestError):
                raw_segments.validate_current_segment_entry(base, segment, kind="prompt_usage")

    def test_hook_append_uses_raw_segment_lock_without_service_lock(self) -> None:
        hook = load_module("hook_current_segment_append_test", ROOT / "scripts" / "hook.py")
        raw_segments = load_module("raw_segments_hook_append_test", ROOT / "scripts" / "raw_segments.py")
        service_lock = load_module("service_lock_hook_append_test", ROOT / "scripts" / "service_lock.py")
        with tempfile.TemporaryDirectory() as tmp:
            codex_dir = pathlib.Path(tmp) / ".codex"
            base = codex_dir / "bola"
            with service_lock.acquire_service_lock(reason="test", output_dir=base):
                hook.append_prompt_usage(
                    {"session_id": "s1", "turn_id": "t1", "captured_at": "2026-05-20T00:00:00+00:00"},
                    base_dir=base,
                )
            current = raw_segments.strict_read_current_pointer(base)["current"]["prompt_usage"]
            self.assertIn('"turn_id":"t1"', pathlib.Path(current["path"]).read_text(encoding="utf-8").replace(" ", ""))

    def test_hook_append_uses_raw_segment_lock_for_default_current_segment(self) -> None:
        hook = load_module("hook_default_current_append_lock_test", ROOT / "scripts" / "hook.py")
        raw_segments = load_module("raw_segments_default_current_append_lock_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp:
            codex_dir = pathlib.Path(tmp) / ".codex"
            base = codex_dir / "bola"
            record = {"session_id": "s1", "turn_id": "t1", "captured_at": "2026-05-20T00:00:00+00:00"}
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                with raw_segments.acquire_raw_segment_lock(base):
                    future = executor.submit(hook.append_prompt_usage, record, base_dir=base)
                    time.sleep(0.1)
                    self.assertFalse(future.done())
                self.assertTrue(future.result(timeout=5))
            current = raw_segments.strict_read_current_pointer(base)["current"]["prompt_usage"]
            self.assertIn('"turn_id":"t1"', pathlib.Path(current["path"]).read_text(encoding="utf-8").replace(" ", ""))

    def test_terminal_turn_finalization_appends_only_once(self) -> None:
        capture = load_module("hook_terminal_finalization_test", ROOT / "scripts" / "hook.py").turn_capture
        raw_segments = load_module("raw_segments_terminal_finalization_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp) / "service"
            state = base / "state" / "turn.json"
            state.parent.mkdir(parents=True)
            state.write_text(json.dumps({"record_type": "turn_start", "session_id": "s1", "turn_id": "t1"}), encoding="utf-8")
            record = {
                "record_type": "turn_usage_raw",
                "session_id": "s1",
                "turn_id": "t1",
                "turn_status": "completed",
                "token_resolution_status": "resolved",
            }
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(capture.finalize_prompt_usage_result, record, state_path=state, base_dir=base)
                    for _index in range(2)
                ]
                results = [future.result(timeout=5) for future in futures]
            current = raw_segments.strict_read_current_pointer(base)["current"]["prompt_usage"]
            rows = pathlib.Path(current["path"]).read_text(encoding="utf-8").splitlines()

        self.assertEqual(sorted(result.status for result in results), ["appended", "duplicate"])
        self.assertEqual(len(rows), 1)
        self.assertFalse(state.exists())

    def test_missing_start_pending_append_claims_marker_once(self) -> None:
        capture = load_module("missing_start_pending_claim_test", ROOT / "scripts" / "hook.py").turn_capture
        with tempfile.TemporaryDirectory() as tmp:
            state = pathlib.Path(tmp) / "state" / "marker.json"
            created = capture.create_json_state_exclusive_result(
                state,
                {
                    "record_type": "turn_stop_missing_start",
                    "session_id": "s1",
                    "turn_id": "t1",
                    "pending_append_state": "required",
                },
            )
            appended: list[str] = []

            def append_record():
                time.sleep(0.05)
                appended.append("row")
                return capture.AppendResult(True)

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                results = list(
                    executor.map(
                        lambda _index: capture.append_missing_start_pending_result(
                            state_path=state,
                            append_record=append_record,
                        ),
                        range(2),
                    )
                )
            marker = json.loads(state.read_text(encoding="utf-8"))

        self.assertTrue(created)
        self.assertEqual(sorted(result.status for result in results), ["appended", "duplicate"])
        self.assertEqual(appended, ["row"])
        self.assertEqual(marker["pending_append_state"], "appended")

    def test_missing_start_pending_append_failure_releases_claim_for_retry(self) -> None:
        capture = load_module("missing_start_pending_retry_test", ROOT / "scripts" / "hook.py").turn_capture
        with tempfile.TemporaryDirectory() as tmp:
            state = pathlib.Path(tmp) / "state" / "marker.json"
            capture.create_json_state_exclusive_result(
                state,
                {
                    "record_type": "turn_stop_missing_start",
                    "session_id": "s1",
                    "turn_id": "t1",
                    "pending_append_state": "required",
                },
            )
            failure = capture.AppendResult(False, failure_stage="append", failure_reason="append_write_failed")
            first = capture.append_missing_start_pending_result(
                state_path=state,
                append_record=lambda: failure,
            )
            after_failure = json.loads(state.read_text(encoding="utf-8"))
            second = capture.append_missing_start_pending_result(
                state_path=state,
                append_record=lambda: capture.AppendResult(True),
            )
            after_retry = json.loads(state.read_text(encoding="utf-8"))

        self.assertEqual(first.status, "failed")
        self.assertEqual(first.append_result, failure)
        self.assertEqual(after_failure["pending_append_state"], "required")
        self.assertEqual(second.status, "appended")
        self.assertEqual(after_retry["pending_append_state"], "appended")

    def test_missing_start_pending_claimed_crash_omits_provisional_retry(self) -> None:
        capture = load_module("missing_start_pending_claimed_crash_test", ROOT / "scripts" / "hook.py").turn_capture
        with tempfile.TemporaryDirectory() as tmp:
            state = pathlib.Path(tmp) / "state" / "marker.json"
            capture.create_json_state_exclusive_result(
                state,
                {
                    "record_type": "turn_stop_missing_start",
                    "session_id": "s1",
                    "turn_id": "t1",
                    "pending_append_state": "claimed",
                },
            )
            append_record = mock.Mock(return_value=capture.AppendResult(True))
            result = capture.append_missing_start_pending_result(
                state_path=state,
                append_record=append_record,
            )

        self.assertEqual(result.status, "duplicate")
        append_record.assert_not_called()

    def test_missing_start_terminal_claim_blocks_late_required_pending_append(self) -> None:
        capture = load_module("missing_start_terminal_claim_blocks_pending_test", ROOT / "scripts" / "hook.py").turn_capture
        with tempfile.TemporaryDirectory() as tmp:
            state = pathlib.Path(tmp) / "state" / "marker.json"
            capture.create_json_state_exclusive_result(
                state,
                {
                    "record_type": "turn_stop_missing_start",
                    "session_id": "s1",
                    "turn_id": "t1",
                    "pending_append_state": "required",
                    "terminal_append_state": "claimed",
                },
            )
            append_record = mock.Mock(return_value=capture.AppendResult(True))
            result = capture.append_missing_start_pending_result(
                state_path=state,
                append_record=append_record,
            )

        self.assertEqual(result.status, "duplicate")
        append_record.assert_not_called()

    def test_missing_start_hook_before_reconcile_writes_pending_then_terminal(self) -> None:
        capture = load_module("missing_start_hook_before_reconcile_test", ROOT / "scripts" / "hook.py").turn_capture
        raw_segments = load_module("missing_start_hook_before_reconcile_segments_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp) / "service"
            state = base / "state" / "marker.json"
            pending = {"record_type": "turn_usage_raw", "session_id": "s1", "turn_id": "t1", "token_resolution_status": "pending"}
            terminal = {"record_type": "turn_usage_raw", "session_id": "s1", "turn_id": "t1", "token_resolution_status": "resolved", "turn_status": "completed"}
            pending_result = capture.append_missing_start_pending_result(
                state_path=state,
                initial_state={
                    "record_type": "turn_stop_missing_start",
                    "session_id": "s1",
                    "turn_id": "t1",
                    "pending_append_state": "required",
                },
                append_record=lambda: capture.append_prompt_usage_result(pending, base_dir=base),
            )
            terminal_result = capture.finalize_missing_start_terminal_result(
                terminal,
                state_path=state,
                base_dir=base,
                terminal_exists=lambda: False,
            )
            current = raw_segments.strict_read_current_pointer(base)["current"]["prompt_usage"]
            rows = [json.loads(line) for line in pathlib.Path(current["path"]).read_text(encoding="utf-8").splitlines()]
            marker = json.loads(state.read_text(encoding="utf-8"))

        self.assertEqual(pending_result.status, "appended")
        self.assertEqual(terminal_result.status, "appended")
        self.assertEqual([row["token_resolution_status"] for row in rows], ["pending", "resolved"])
        self.assertEqual(marker["record_type"], "turn_finalized")

    def test_missing_start_reconcile_before_hook_blocks_late_pending_append(self) -> None:
        capture = load_module("missing_start_reconcile_before_hook_test", ROOT / "scripts" / "hook.py").turn_capture
        raw_segments = load_module("missing_start_reconcile_before_hook_segments_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp) / "service"
            state = base / "state" / "marker.json"
            capture.create_json_state_exclusive_result(
                state,
                {
                    "record_type": "turn_stop_missing_start",
                    "session_id": "s1",
                    "turn_id": "t1",
                    "pending_append_state": "required",
                },
            )
            terminal = {"record_type": "turn_usage_raw", "session_id": "s1", "turn_id": "t1", "token_resolution_status": "resolved", "turn_status": "completed"}
            terminal_result = capture.finalize_missing_start_terminal_result(
                terminal,
                state_path=state,
                base_dir=base,
                terminal_exists=lambda: False,
            )
            pending_append = mock.Mock(return_value=capture.AppendResult(True))
            late_result = capture.append_missing_start_pending_result(
                state_path=state,
                initial_state={
                    "record_type": "turn_stop_missing_start",
                    "session_id": "s1",
                    "turn_id": "t1",
                    "pending_append_state": "required",
                },
                append_record=pending_append,
            )
            current = raw_segments.strict_read_current_pointer(base)["current"]["prompt_usage"]
            rows = pathlib.Path(current["path"]).read_text(encoding="utf-8").splitlines()

        self.assertEqual(terminal_result.status, "appended")
        self.assertEqual(late_result.status, "duplicate")
        pending_append.assert_not_called()
        self.assertEqual(len(rows), 1)

    def test_missing_start_terminal_scan_covers_pending_rotation_old_segment(self) -> None:
        capture = load_module("missing_start_pending_rotation_scan_test", ROOT / "scripts" / "hook.py").turn_capture
        raw_segments = load_module("missing_start_pending_rotation_scan_segments_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp) / "service"
            state = base / "state" / "marker.json"
            terminal = {
                "record_type": "turn_usage_raw",
                "session_id": "s1",
                "turn_id": "t1",
                "turn_status": "completed",
                "token_resolution_status": "resolved",
            }
            capture.append_prompt_usage_result(terminal, base_dir=base)
            with raw_segments.acquire_raw_segment_lock(base):
                rotation = raw_segments.begin_rotate_all_current_segments_unlocked(base)
            pending = {
                "record_type": "turn_usage_raw",
                "session_id": "s1",
                "turn_id": "t1",
                "token_resolution_status": "pending",
            }

            result = capture.append_missing_start_pending_result(
                state_path=state,
                initial_state={
                    "record_type": "turn_stop_missing_start",
                    "session_id": "s1",
                    "turn_id": "t1",
                    "pending_append_state": "required",
                },
                record=pending,
                base_dir=base,
            )
            tombstone = json.loads(state.read_text(encoding="utf-8"))
            raw_segments.finish_rotate_all_current_segments(base, rotation)
            sources = [
                *raw_segments.manifest_segments(base, kind="prompt_usage"),
                *raw_segments.current_segment_paths(base, kind="prompt_usage"),
            ]
            rows = [
                line
                for source in sources
                for line in pathlib.Path(source).read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(result.status, "duplicate")
        self.assertEqual(tombstone["record_type"], "turn_finalized")
        self.assertEqual(tombstone["finalized_reason"], "existing_durable_terminal")
        self.assertEqual(len(rows), 1)

    def test_missing_start_terminal_payload_scan_releases_raw_lock(self) -> None:
        capture = load_module("missing_start_scan_releases_raw_lock_test", ROOT / "scripts" / "hook.py").turn_capture
        raw_segments = load_module("missing_start_scan_releases_raw_lock_segments_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp) / "service"
            state = base / "state" / "marker.json"
            scan_started = concurrent.futures.Future()
            release_scan = concurrent.futures.Future()

            def blocking_scan(*_args):
                scan_started.set_result(True)
                release_scan.result(timeout=5)
                return False

            with (
                mock.patch.object(capture, "_scan_terminal_snapshots", side_effect=blocking_scan),
                concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor,
            ):
                future = executor.submit(
                    capture.append_missing_start_pending_result,
                    state_path=state,
                    initial_state={
                        "record_type": "turn_stop_missing_start",
                        "session_id": "s1",
                        "turn_id": "t1",
                        "pending_append_state": "required",
                    },
                    record={"record_type": "turn_usage_raw", "session_id": "s1", "turn_id": "t1"},
                    base_dir=base,
                )
                scan_started.result(timeout=5)
                with raw_segments.acquire_raw_segment_lock(base):
                    raw_lock_acquired_during_scan = True
                release_scan.set_result(True)
                result = future.result(timeout=5)

        self.assertTrue(raw_lock_acquired_during_scan)
        self.assertEqual(result.status, "appended")

    def test_missing_start_terminal_snapshot_loss_fails_closed_without_pending_append(self) -> None:
        capture = load_module("missing_start_snapshot_loss_test", ROOT / "scripts" / "hook.py").turn_capture
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp) / "service"
            state = base / "state" / "marker.json"
            with mock.patch.object(
                capture,
                "_scan_terminal_snapshots",
                side_effect=FileNotFoundError("segment disappeared"),
            ):
                result = capture.append_missing_start_pending_result(
                    state_path=state,
                    initial_state={
                        "record_type": "turn_stop_missing_start",
                        "session_id": "s1",
                        "turn_id": "t1",
                        "pending_append_state": "required",
                    },
                    record={"record_type": "turn_usage_raw", "session_id": "s1", "turn_id": "t1"},
                    base_dir=base,
                )
            marker = json.loads(state.read_text(encoding="utf-8"))

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.append_result.failure_reason, "segment_io_error")
        self.assertEqual(marker["pending_append_state"], "required")

    def test_missing_start_truncated_gzip_fails_closed_without_pending_append(self) -> None:
        capture = load_module("missing_start_truncated_gzip_test", ROOT / "scripts" / "hook.py").turn_capture
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp) / "service"
            state = base / "state" / "marker.json"
            damaged = base / "raw" / "closed.jsonl.gz"
            damaged.parent.mkdir(parents=True)
            damaged.write_bytes(b"\x1f\x8b\x08\x00")
            metadata = damaged.stat()
            snapshot = capture.RawSegmentSnapshot(damaged, metadata.st_dev, metadata.st_ino)
            with mock.patch.object(capture, "_snapshot_prompt_segments_locked", return_value=[snapshot]):
                result = capture.append_missing_start_pending_result(
                    state_path=state,
                    initial_state={
                        "record_type": "turn_stop_missing_start",
                        "session_id": "s1",
                        "turn_id": "t1",
                        "pending_append_state": "required",
                    },
                    record={"record_type": "turn_usage_raw", "session_id": "s1", "turn_id": "t1"},
                    base_dir=base,
                )
            marker = json.loads(state.read_text(encoding="utf-8"))
            current_segments = capture.raw_segments.current_segment_paths(base, kind="prompt_usage")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.append_result.failure_stage, "segment")
        self.assertEqual(result.append_result.failure_reason, "segment_io_error")
        self.assertEqual(marker["pending_append_state"], "required")
        self.assertEqual(current_segments, [])

    def test_missing_start_raw_and_manifest_lock_waits_share_bounded_deadline(self) -> None:
        capture = load_module("missing_start_bounded_lock_wait_test", ROOT / "scripts" / "hook.py").turn_capture
        for lock_kind in ("raw", "manifest"):
            with self.subTest(lock_kind=lock_kind), tempfile.TemporaryDirectory() as tmp:
                base = pathlib.Path(tmp) / "service"
                state = base / "state" / "marker.json"
                lock_path = (
                    capture.raw_segments.raw_segment_lock_path(base)
                    if lock_kind == "raw"
                    else capture.raw_segment_manifest_lock_path(base)
                )
                held_descriptor, failure = capture._acquire_file_lock_until(
                    lock_path,
                    time.monotonic() + 1,
                    failure_stage="lock",
                    timeout_reason="test_timeout",
                )
                self.assertIsNone(failure)
                started = time.monotonic()
                try:
                    result = capture.append_missing_start_pending_result(
                        state_path=state,
                        initial_state={
                            "record_type": "turn_stop_missing_start",
                            "session_id": "s1",
                            "turn_id": "t1",
                            "pending_append_state": "required",
                        },
                        record={"record_type": "turn_usage_raw", "session_id": "s1", "turn_id": "t1"},
                        base_dir=base,
                        lock_timeout_ms=25,
                    )
                finally:
                    capture._release_file_lock(held_descriptor)
                elapsed = time.monotonic() - started
                marker = json.loads(state.read_text(encoding="utf-8"))

                self.assertEqual(result.status, "failed")
                self.assertLess(elapsed, 0.1)
                self.assertIn("lock_timeout", str(result.append_result.failure_reason))
                self.assertEqual(marker["pending_append_state"], "required")

    def test_missing_start_second_raw_reacquire_uses_original_deadline(self) -> None:
        capture = load_module("missing_start_second_raw_deadline_test", ROOT / "scripts" / "hook.py").turn_capture
        raw_segments = load_module("missing_start_second_raw_deadline_segments_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp) / "service"
            state = base / "state" / "marker.json"
            scan_started = concurrent.futures.Future()
            raw_held = concurrent.futures.Future()

            def wait_for_raw_holder(*_args):
                scan_started.set_result(True)
                raw_held.result(timeout=5)
                return False

            with (
                mock.patch.object(capture, "_scan_terminal_snapshots", side_effect=wait_for_raw_holder),
                concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor,
            ):
                future = executor.submit(
                    capture.append_missing_start_pending_result,
                    state_path=state,
                    initial_state={
                        "record_type": "turn_stop_missing_start",
                        "session_id": "s1",
                        "turn_id": "t1",
                        "pending_append_state": "required",
                    },
                    record={"record_type": "turn_usage_raw", "session_id": "s1", "turn_id": "t1"},
                    base_dir=base,
                    lock_timeout_ms=80,
                )
                scan_started.result(timeout=5)
                with raw_segments.acquire_raw_segment_lock(base):
                    raw_held.set_result(True)
                    result = future.result(timeout=5)
            marker = json.loads(state.read_text(encoding="utf-8"))

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.append_result.failure_reason, "lock_timeout")
        self.assertEqual(marker["pending_append_state"], "required")

    def test_different_missing_start_turns_scan_concurrently(self) -> None:
        capture = load_module("missing_start_per_turn_sidecar_test", ROOT / "scripts" / "hook.py").turn_capture
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp) / "service"
            barrier = threading.Barrier(2)

            def concurrent_scan(*_args):
                barrier.wait(timeout=5)
                return False

            def append_turn(turn_id: str):
                return capture.append_missing_start_pending_result(
                    state_path=base / "state" / f"{turn_id}.json",
                    initial_state={
                        "record_type": "turn_stop_missing_start",
                        "session_id": "s1",
                        "turn_id": turn_id,
                        "pending_append_state": "required",
                    },
                    record={"record_type": "turn_usage_raw", "session_id": "s1", "turn_id": turn_id},
                    base_dir=base,
                    lock_timeout_ms=1000,
                )

            with (
                mock.patch.object(capture, "_scan_terminal_snapshots", side_effect=concurrent_scan),
                concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor,
            ):
                results = list(executor.map(append_turn, ("t1", "t2")))

        self.assertEqual([result.status for result in results], ["appended", "appended"])

    def test_missing_start_terminal_scan_honors_expired_deadline(self) -> None:
        capture = load_module("missing_start_scan_deadline_test", ROOT / "scripts" / "hook.py").turn_capture

        with self.assertRaises(TimeoutError):
            capture._scan_terminal_snapshots([], "s1", "t1", time.monotonic() - 1)

    def test_missing_start_terminal_claimed_crash_uses_existing_terminal(self) -> None:
        capture = load_module("missing_start_terminal_claimed_recovery_test", ROOT / "scripts" / "hook.py").turn_capture
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp) / "service"
            state = base / "state" / "marker.json"
            capture.create_json_state_exclusive_result(
                state,
                {
                    "record_type": "turn_stop_missing_start",
                    "session_id": "s1",
                    "turn_id": "t1",
                    "pending_append_state": "appended",
                    "terminal_append_state": "claimed",
                },
            )
            with mock.patch.object(capture, "append_prompt_usage_result") as append_prompt_usage:
                result = capture.finalize_missing_start_terminal_result(
                    {"session_id": "s1", "turn_id": "t1"},
                    state_path=state,
                    base_dir=base,
                    terminal_exists=lambda: True,
                )
            marker = json.loads(state.read_text(encoding="utf-8"))

        self.assertEqual(result.status, "duplicate")
        append_prompt_usage.assert_not_called()
        self.assertEqual(marker["record_type"], "turn_finalized")
        self.assertEqual(marker["terminal_append_state"], "appended")

    def test_missing_start_terminal_claimed_crash_retries_missing_terminal(self) -> None:
        capture = load_module("missing_start_terminal_claimed_retry_test", ROOT / "scripts" / "hook.py").turn_capture
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp) / "service"
            state = base / "state" / "marker.json"
            capture.create_json_state_exclusive_result(
                state,
                {
                    "record_type": "turn_stop_missing_start",
                    "session_id": "s1",
                    "turn_id": "t1",
                    "pending_append_state": "claimed",
                    "terminal_append_state": "claimed",
                },
            )
            order: list[str] = []
            terminal = {"record_type": "turn_usage_raw", "session_id": "s1", "turn_id": "t1"}
            with mock.patch.object(
                capture,
                "append_prompt_usage_result",
                side_effect=lambda *_args, **_kwargs: order.append("raw") or capture.AppendResult(True),
            ):
                result = capture.finalize_missing_start_terminal_result(
                    terminal,
                    state_path=state,
                    base_dir=base,
                    terminal_exists=lambda: False,
                    before_terminal_append=lambda: order.append("health"),
                )
            marker = json.loads(state.read_text(encoding="utf-8"))

        self.assertEqual(result.status, "appended")
        self.assertEqual(order, ["health", "raw"])
        self.assertEqual(marker["record_type"], "turn_finalized")

    def test_missing_start_atomic_claim_failure_keeps_valid_required_marker(self) -> None:
        capture = load_module("missing_start_atomic_claim_failure_test", ROOT / "scripts" / "hook.py").turn_capture
        with tempfile.TemporaryDirectory() as tmp:
            state = pathlib.Path(tmp) / "state" / "marker.json"
            capture.create_json_state_exclusive_result(
                state,
                {
                    "record_type": "turn_stop_missing_start",
                    "session_id": "s1",
                    "turn_id": "t1",
                    "pending_append_state": "required",
                },
            )
            failure = capture.AppendResult(False, failure_stage="state_write", failure_reason="state_replace_failed")
            append_record = mock.Mock(return_value=capture.AppendResult(True))
            with mock.patch.object(capture, "replace_json_state_atomic_result", return_value=failure):
                result = capture.append_missing_start_pending_result(
                    state_path=state,
                    append_record=append_record,
                )
            marker = json.loads(state.read_text(encoding="utf-8"))

        self.assertEqual(result.status, "failed")
        append_record.assert_not_called()
        self.assertEqual(marker["pending_append_state"], "required")

    def test_missing_start_tombstone_failure_recovers_without_second_terminal_append(self) -> None:
        capture = load_module("missing_start_tombstone_failure_test", ROOT / "scripts" / "hook.py").turn_capture
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp) / "service"
            state = base / "state" / "marker.json"
            capture.create_json_state_exclusive_result(
                state,
                {
                    "record_type": "turn_stop_missing_start",
                    "session_id": "s1",
                    "turn_id": "t1",
                    "pending_append_state": "appended",
                },
            )
            real_replace = capture.replace_json_state_atomic_result
            failure = capture.AppendResult(False, failure_stage="state_write", failure_reason="state_replace_failed")
            replace_results = [None, failure]

            def replace_then_fail(path, payload):
                result = replace_results.pop(0)
                return real_replace(path, payload) if result is None else result

            with mock.patch.object(capture, "replace_json_state_atomic_result", side_effect=replace_then_fail):
                first = capture.finalize_missing_start_terminal_result(
                    {"session_id": "s1", "turn_id": "t1"},
                    state_path=state,
                    base_dir=base,
                    terminal_exists=lambda: False,
                )
            claimed = json.loads(state.read_text(encoding="utf-8"))
            with mock.patch.object(capture, "append_prompt_usage_result") as append_prompt_usage:
                second = capture.finalize_missing_start_terminal_result(
                    {"session_id": "s1", "turn_id": "t1"},
                    state_path=state,
                    base_dir=base,
                    terminal_exists=lambda: True,
                )
            tombstone = json.loads(state.read_text(encoding="utf-8"))

        self.assertEqual(first.status, "appended_state_cleanup_pending")
        self.assertEqual(claimed["terminal_append_state"], "claimed")
        self.assertEqual(second.status, "duplicate")
        append_prompt_usage.assert_not_called()
        self.assertEqual(tombstone["record_type"], "turn_finalized")

    def test_terminal_turn_finalization_preserves_state_after_append_failure(self) -> None:
        capture = load_module("hook_terminal_append_failure_test", ROOT / "scripts" / "hook.py").turn_capture
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp) / "service"
            state = base / "state" / "turn.json"
            state.parent.mkdir(parents=True)
            state.write_text(json.dumps({"record_type": "turn_start", "session_id": "s1", "turn_id": "t1"}), encoding="utf-8")
            failure = capture.AppendResult(False, failure_stage="append", failure_reason="append_write_failed")
            with mock.patch.object(capture, "append_prompt_usage_result", return_value=failure):
                result = capture.finalize_prompt_usage_result({}, state_path=state, base_dir=base)
            state_exists = state.exists()

        self.assertEqual(result.status, "failed")
        self.assertTrue(state_exists)

    def test_terminal_turn_finalization_preserves_state_after_sync_failure(self) -> None:
        capture = load_module("hook_terminal_sync_failure_test", ROOT / "scripts" / "hook.py").turn_capture
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp) / "service"
            state = base / "state" / "turn.json"
            state.parent.mkdir(parents=True)
            state.write_text(json.dumps({"record_type": "turn_start", "session_id": "s1", "turn_id": "t1"}), encoding="utf-8")
            record = {"record_type": "turn_usage_raw", "session_id": "s1", "turn_id": "t1", "turn_status": "completed"}
            with mock.patch.object(capture.os, "fdatasync", side_effect=OSError("sync blocked")):
                result = capture.finalize_prompt_usage_result(record, state_path=state, base_dir=base)

            state_exists = state.exists()

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.append_result.failure_reason, "append_sync_failed")
        self.assertTrue(state_exists)

    def test_new_current_segment_syncs_parent_directory_once(self) -> None:
        raw_segments = load_module("raw_segments_new_segment_sync_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp) / "service"
            with mock.patch.object(raw_segments._rotation, "fsync_dir") as sync_dir:
                first = raw_segments.ensure_current_segment(base, kind="prompt_usage", source_name="prompt-usage.raw.jsonl")
                second = raw_segments.ensure_current_segment(base, kind="prompt_usage", source_name="prompt-usage.raw.jsonl")

        self.assertEqual(first, second)
        sync_dir.assert_called_once_with(base / "raw" / "current")

    def test_terminal_turn_finalization_marks_failed_state_cleanup_as_finalized(self) -> None:
        capture = load_module("hook_terminal_cleanup_failure_test", ROOT / "scripts" / "hook.py").turn_capture
        raw_segments = load_module("raw_segments_terminal_cleanup_failure_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp) / "service"
            state = base / "state" / "turn.json"
            state.parent.mkdir(parents=True)
            state.write_text(json.dumps({"record_type": "turn_start", "session_id": "s1", "turn_id": "t1"}), encoding="utf-8")
            record = {"record_type": "turn_usage_raw", "session_id": "s1", "turn_id": "t1", "turn_status": "completed"}
            original_unlink = pathlib.Path.unlink

            def fail_state_unlink(path, *args, **kwargs):
                if path == state:
                    raise OSError("blocked")
                return original_unlink(path, *args, **kwargs)

            with mock.patch.object(pathlib.Path, "unlink", fail_state_unlink):
                first = capture.finalize_prompt_usage_result(record, state_path=state, base_dir=base)
                second = capture.finalize_prompt_usage_result(record, state_path=state, base_dir=base)
            current = raw_segments.strict_read_current_pointer(base)["current"]["prompt_usage"]
            rows = pathlib.Path(current["path"]).read_text(encoding="utf-8").splitlines()
            marker = json.loads(state.read_text(encoding="utf-8"))

        self.assertEqual(first.status, "appended_state_cleanup_pending")
        self.assertEqual(second.status, "duplicate")
        self.assertEqual(marker["record_type"], "turn_finalized")
        self.assertEqual(len(rows), 1)

    def test_hook_append_survives_current_segment_rotation(self) -> None:
        hook = load_module("hook_current_segment_rotation_survival_test", ROOT / "scripts" / "hook.py")
        raw_segments = load_module("raw_segments_hook_rotation_survival_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp:
            codex_dir = pathlib.Path(tmp) / ".codex"
            base = codex_dir / "bola"
            record = {"session_id": "s1", "turn_id": "t1", "captured_at": "2026-05-20T00:00:00+00:00"}

            self.assertTrue(hook.append_prompt_usage(record, base_dir=base))
            result = raw_segments.rotate_current_segment(base=base, kind="prompt_usage", source_name="prompt-usage.raw.jsonl")

            closed_text = pathlib.Path(result["closed_segment"]["path"]).read_text(encoding="utf-8")
            current_text = pathlib.Path(result["current_segment"]["path"]).read_text(encoding="utf-8")
            self.assertEqual((closed_text + current_text).count('"turn_id":"t1"'), 1)

    def test_hook_append_result_classifies_lock_timeout(self) -> None:
        hook = load_module("hook_append_lock_timeout_result_test", ROOT / "scripts" / "hook.py")
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(hook, "HOOK_APPEND_LOCK_TIMEOUT_MS", 0),
            mock.patch.object(hook.turn_capture.fcntl, "flock", side_effect=BlockingIOError),
        ):
            result = hook.append_prompt_usage({"turn_id": "timeout"}, base_dir=pathlib.Path(tmp), detailed=True)

        self.assertFalse(result)
        self.assertEqual(result.failure_stage, "lock")
        self.assertEqual(result.failure_reason, "lock_timeout")
        self.assertIsNone(result.error_number)

    def test_hook_append_lock_timeout_defaults_to_one_second(self) -> None:
        hook = load_module("hook_append_lock_timeout_default_test", ROOT / "scripts" / "hook.py")

        self.assertEqual(hook.HOOK_APPEND_LOCK_TIMEOUT_MS, 1000)

    def test_hook_logs_structured_raw_append_failure_without_exception_text(self) -> None:
        hook = load_module("hook_structured_append_failure_test", ROOT / "scripts" / "hook.py")
        warnings: list[dict[str, Any]] = []
        append_result = hook.turn_capture.AppendResult(
            False,
            failure_stage="segment",
            failure_reason="segment_manifest_error",
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(hook, "STATE_DIR", pathlib.Path(tmp) / "state"),
            mock.patch.object(hook.turn_capture, "_append_prompt_usage_unlocked_result", return_value=append_result),
            mock.patch.object(hook, "safe_append_jsonl", side_effect=lambda _path, record: warnings.append(record) or True),
        ):
            hook.handle_stop({"session_id": "s-failed", "turn_id": "t-failed", "transcript_path": "/tmp/missing.jsonl"})

        failure = next(row for row in warnings if row.get("error") == "raw_append_failed")
        self.assertEqual(failure["failure_stage"], "segment")
        self.assertEqual(failure["failure_reason"], "segment_manifest_error")
        self.assertNotIn("exception", failure)

    def test_all_current_segment_handoff_writes_one_prompt_pointer(self) -> None:
        raw_segments = load_module("raw_segments_all_kind_atomic_pointer_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            prompt = raw_segments.ensure_current_segment(base, kind="prompt_usage", source_name="prompt-usage.raw.jsonl")
            observed: list[dict[str, Any]] = []
            original_write_current_pointer = raw_segments._rotation.write_current_pointer

            def spy_pointer(base_arg: pathlib.Path, pointer: dict[str, Any]) -> None:
                observed.append(json.loads(json.dumps(pointer)))
                original_write_current_pointer(base_arg, pointer)

            with mock.patch.object(raw_segments._rotation, "write_current_pointer", side_effect=spy_pointer):
                raw_segments.rotate_all_current_segments(base)

            self.assertEqual(len(observed), 1)
            current = observed[0]["current"]
            self.assertNotEqual(current["prompt_usage"]["path"], prompt["path"])
            self.assertNotIn("model_calls", current)

    def test_pending_rotation_reconciles_empty_unlinked_old_segment_recorded_in_marker(self) -> None:
        raw_segments = load_module("raw_segments_empty_unlinked_marker_reconcile_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp) / "token-usage"
            old_segment = raw_segments.new_current_segment(base, kind="prompt_usage", source_name="prompt-usage.raw.jsonl")
            new_segment = raw_segments.new_current_segment(base, kind="prompt_usage", source_name="prompt-usage.raw.jsonl")
            raw_segments.write_current_pointer(base, raw_segments.empty_current_pointer(base) | {"current": {"prompt_usage": new_segment}})
            closed_segment = raw_segments.closed_segment_from_current(
                old_segment,
                {
                    "rows": 0,
                    "undated_rows": 0,
                    "corrupt_rows": 0,
                    "unknown_rows": 0,
                    "days": [],
                    "bytes": 0,
                    "uncompressed_bytes": 0,
                    "sha256": hashlib.sha256(b"").hexdigest(),
                    "min_time_unix": None,
                    "max_time_unix": None,
                },
                kind="prompt_usage",
            )
            pathlib.Path(old_segment["path"]).unlink()
            raw_segments.write_pending_rotation(
                base,
                {
                    "operation": "rotate_current_segments",
                    "phase": "manifest_pending",
                    "segments": {"prompt_usage": {"old_segment": old_segment, "new_segment": new_segment}},
                    "closed_segments": {"prompt_usage": closed_segment},
                    "created_at_unix": 1.0,
                },
            )

            raw_segments.reconcile_pending_rotation(base)

        self.assertFalse(raw_segments.pending_rotation_path(base).exists())

    def test_compact_can_rotate_current_segments_without_active_rewrite(self) -> None:
        compact = load_module("compact_current_segment_no_rewrite_test", ROOT / "scripts" / "compact_raw.py")
        raw_segments = load_module("raw_segments_compact_no_rewrite_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp) / "token-usage"
            raw_dir = base / "raw"
            raw_dir.mkdir(parents=True)
            prompt_raw = raw_dir / "prompt-usage.raw.jsonl"
            prompt_raw.write_text("flat prompt\n", encoding="utf-8")
            current = raw_segments.ensure_current_segment(base, kind="prompt_usage", source_name="prompt-usage.raw.jsonl")
            pathlib.Path(current["path"]).write_text("current prompt\n", encoding="utf-8")
            result = compact.rotate_current_logs(base)
            self.assertIn("prompt_usage", result)
            self.assertNotIn("model_calls", result)
            self.assertEqual(prompt_raw.read_text(encoding="utf-8"), "flat prompt\n")

    def test_compact_rotate_current_cli_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex_dir = pathlib.Path(tmp) / ".codex"
            base = codex_dir / "bola"
            raw_dir = base / "raw"
            raw_dir.mkdir(parents=True)
            (raw_dir / "prompt-usage.raw.jsonl").write_text("flat prompt\n", encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "compact_raw.py"),
                    "--rotate-current",
                ],
                check=True,
                capture_output=True,
                text=True,
                env={**os.environ, "CODEX_HOME": str(codex_dir)},
            )
            parsed = json.loads(result.stdout)
            self.assertIn("prompt_usage", parsed)
            self.assertNotIn("model_calls", parsed)
            self.assertEqual((raw_dir / "prompt-usage.raw.jsonl").read_text(encoding="utf-8"), "flat prompt\n")

    def test_compact_rotate_current_removes_empty_closed_segments(self) -> None:
        compact = load_module("compact_empty_current_cleanup_test", ROOT / "scripts" / "compact_raw.py")
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp) / "token-usage"
            current_dir = base / "raw" / "current"
            state_dir = base / "state"
            current_dir.mkdir(parents=True)
            state_dir.mkdir(parents=True)
            prompt_old = current_dir / "prompt-usage.raw.jsonl.current.1.jsonl"
            prompt_old.write_text("", encoding="utf-8")
            (state_dir / "current-raw-segments.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "base": str(base.resolve()),
                        "current": {
                            "prompt_usage": {
                                "id": "prompt-usage.raw.jsonl.current.1",
                                "kind": "prompt_usage",
                                "path": str(prompt_old),
                                "source_name": "prompt-usage.raw.jsonl",
                            },
                        },
                    },
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )

            result = compact.rotate_current_logs(base)

            self.assertFalse(prompt_old.exists())
            self.assertTrue(pathlib.Path(result["prompt_usage"]["current_segment"]["path"]).exists())

    def test_compact_help_describes_current_segment_rotation(self) -> None:
        compact_help = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "compact_raw.py"), "--help"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        cli_help = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "bola.py"), "--help"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout

        self.assertIn("Rotate current raw segments", compact_help)
        self.assertNotIn("Archive DB-applied raw JSONL prefixes", compact_help)
        self.assertIn("Rotate current raw segments", cli_help)
