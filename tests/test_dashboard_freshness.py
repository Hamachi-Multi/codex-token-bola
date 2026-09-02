from __future__ import annotations

try:
    from tests.support import DashboardFixtureMixin, ROOT, _raw_segment, concurrent, json, load_module, mock, pathlib, sqlite3, tempfile, time, types, unittest
except ModuleNotFoundError:
    from support import DashboardFixtureMixin, ROOT, _raw_segment, concurrent, json, load_module, mock, pathlib, sqlite3, tempfile, time, types, unittest

from normalize_contract import NORMALIZE_LOGIC_VERSION


class DashboardFreshnessTests(DashboardFixtureMixin, unittest.TestCase):
    def write_empty_freshness_fixture(self, base: pathlib.Path) -> pathlib.Path:
        state_dir = base / "state"
        normalized_dir = base / "normalized"
        analytics_dir = base / "analytics"
        state_dir.mkdir(parents=True, exist_ok=True)
        normalized_dir.mkdir(parents=True, exist_ok=True)
        analytics_dir.mkdir(parents=True, exist_ok=True)
        (normalized_dir / "normalize-state.json").write_text(
            json.dumps({"logic_version": NORMALIZE_LOGIC_VERSION, "sources": {}, "processed_segments": {}}),
            encoding="utf-8",
        )
        (state_dir / "current-raw-segments.json").write_text(
            json.dumps({"schema_version": 1, "base": str(base.resolve()), "current": {}}),
            encoding="utf-8",
        )
        (state_dir / "raw-segments-manifest.json").write_text(
            json.dumps({"schema_version": 1, "base": str(base.resolve()), "segments": []}),
            encoding="utf-8",
        )
        db_path = analytics_dir / "bola.sqlite"
        db_path.write_text("", encoding="utf-8")
        return db_path

    def test_missing_analytics_db_serves_empty_initial_dashboard_payload(self) -> None:
        serve = load_module("serve_dashboard_missing_db_test", ROOT / "scripts" / "serve_dashboard.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = pathlib.Path(tmp_dir) / "analytics" / "bola.sqlite"
            serve.OUTPUT_DIR = pathlib.Path(tmp_dir)
            handler = serve.Handler.__new__(serve.Handler)
            handler.server = types.SimpleNamespace(db_path=db_path)
            captured: dict[str, object] = {}

            def send_json(data, status=200):
                captured["status"] = status
                captured["data"] = data

            handler.send_json = send_json
            handler.handle_api("/api/dashboard", {"days": ["7"], "limit": ["100"], "page": ["2"], "per_page": ["5"]})

        self.assertEqual(captured["status"], 200)
        payload = captured["data"]
        self.assertEqual(payload["summary"]["turns"], 0)
        self.assertEqual(payload["summary"]["total_tokens"], 0)
        self.assertEqual(payload["summary"]["tool_calls"], 0)
        self.assertEqual(payload["projects"]["rows"], [])
        self.assertEqual(payload["sessions"]["rows"], [])
        self.assertEqual(payload["turns"], {"rows": [], "total": 0, "page": 1, "per_page": 5, "focused": False})
        self.assertEqual(payload["tools"]["rows"], [])
        self.assertEqual([row["rows"] for row in payload["subagents"]["rows"]], [0, 0, 0, 0, 0])
        self.assertEqual(payload["freshness"]["status"], "missing_db")
        self.assertFalse(payload["freshness"]["needs_analyze"])

    def test_stale_analytics_db_serves_empty_dashboard_payload_instead_of_500(self) -> None:
        serve = load_module("serve_dashboard_stale_db_dashboard_test", ROOT / "scripts" / "serve_dashboard.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            db_path = self.write_empty_freshness_fixture(base)
            con = sqlite3.connect(db_path)
            try:
                con.executescript(
                    """
                    create table turns (
                      session_id text,
                      turn_id text,
                      captured_at_unix real,
                      total_tokens integer
                    );
                    create table run_metadata (key text primary key, value text);
                    """
                )
                con.commit()
            finally:
                con.close()
            serve.OUTPUT_DIR = base
            handler = serve.Handler.__new__(serve.Handler)
            handler.server = types.SimpleNamespace(db_path=db_path)
            sent: list[tuple[dict[str, object], int]] = []
            handler.send_json = lambda payload, status=200: sent.append((payload, status))

            handler.handle_api("/api/dashboard", {"days": ["7"], "page": ["1"], "per_page": ["25"]})

        self.assertEqual(sent[0][1], 200)
        payload = sent[0][0]
        self.assertEqual(payload["summary"]["turns"], 0)
        self.assertEqual(payload["turns"]["rows"], [])
        self.assertEqual(payload["freshness"]["data_health"], "degraded")
        self.assertIn("analytics_schema_stale", [warning["code"] for warning in payload["freshness"]["warnings"]])

    def test_stale_analytics_db_serves_empty_turns_payload_instead_of_500(self) -> None:
        serve = load_module("serve_dashboard_stale_db_turns_test", ROOT / "scripts" / "serve_dashboard.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            db_path = self.write_empty_freshness_fixture(base)
            con = sqlite3.connect(db_path)
            try:
                con.executescript(
                    """
                    create table turns (
                      session_id text,
                      turn_id text,
                      captured_at_unix real,
                      total_tokens integer
                    );
                    create table run_metadata (key text primary key, value text);
                    """
                )
                con.commit()
            finally:
                con.close()
            serve.OUTPUT_DIR = base
            handler = serve.Handler.__new__(serve.Handler)
            handler.server = types.SimpleNamespace(db_path=db_path)
            sent: list[tuple[dict[str, object], int]] = []
            handler.send_json = lambda payload, status=200: sent.append((payload, status))

            handler.handle_api("/api/turns", {"page": ["2"], "per_page": ["5"]})

        self.assertEqual(sent, [({"rows": [], "total": 0, "page": 1, "per_page": 5, "focused": False}, 200)])

    def test_dashboard_freshness_counts_pending_raw_rows_since_normalize_state(self) -> None:
        freshness = load_module("dashboard_freshness_pending_rows_test", ROOT / "scripts" / "dashboard_freshness.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            current_dir = base / "raw" / "current"
            normalized_dir = base / "normalized"
            analytics_dir = base / "analytics"
            current_dir.mkdir(parents=True)
            normalized_dir.mkdir(parents=True)
            analytics_dir.mkdir(parents=True)
            raw_path = current_dir / "prompt-usage.raw.jsonl.current.1.jsonl"
            first = json.dumps({"record_type": "turn_usage_raw", "turn_id": "t1"}) + "\n"
            second = json.dumps({"record_type": "turn_usage_raw", "turn_id": "t2"}) + "\n"
            third = json.dumps({"record_type": "turn_usage_raw", "turn_id": "t3"}) + "\n"
            raw_path.write_text(first + second + third, encoding="utf-8")
            (normalized_dir / "normalize-state.json").write_text(
                json.dumps({"logic_version": NORMALIZE_LOGIC_VERSION, "sources": {str(raw_path): len(first)}, "processed_segments": {}}),
                encoding="utf-8",
            )
            db_path = analytics_dir / "bola.sqlite"
            db_path.write_text("", encoding="utf-8")

            payload = freshness.freshness_payload(base, db_path)

        self.assertEqual(payload["status"], "needs_analyze")
        self.assertTrue(payload["needs_analyze"])
        self.assertEqual(payload["pending_raw_rows"], 2)
        self.assertEqual(payload["pending_raw_files"], 1)
        self.assertGreater(payload["latest_raw_mtime_unix"], 0)
        self.assertGreater(payload["analytics_db_mtime_unix"], 0)

    def test_dashboard_freshness_degrades_when_current_raw_cannot_be_read(self) -> None:
        freshness = load_module("dashboard_freshness_unreadable_raw_test", ROOT / "scripts" / "dashboard_freshness.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            raw_dir = base / "raw" / "current"
            state_dir = base / "state"
            normalized_dir = base / "normalized"
            analytics_dir = base / "analytics"
            raw_dir.mkdir(parents=True)
            state_dir.mkdir()
            normalized_dir.mkdir()
            analytics_dir.mkdir()
            raw_path = raw_dir / "prompt-usage.raw.jsonl.current.unreadable.jsonl"
            raw_path.write_text(json.dumps({"record_type": "turn_usage_raw", "turn_id": "pending"}) + "\n", encoding="utf-8")
            (state_dir / "current-raw-segments.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "base": str(base.resolve()),
                        "current": {
                            "prompt_usage": {
                                "id": "unreadable",
                                "kind": "prompt_usage",
                                "path": str(raw_path),
                                "source_name": "prompt-usage.raw.jsonl",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            (state_dir / "raw-segments-manifest.json").write_text(
                json.dumps({"schema_version": 1, "base": str(base.resolve()), "segments": []}),
                encoding="utf-8",
            )
            (normalized_dir / "normalize-state.json").write_text(
                json.dumps({"logic_version": NORMALIZE_LOGIC_VERSION, "sources": {}, "processed_segments": {}}),
                encoding="utf-8",
            )
            db_path = analytics_dir / "bola.sqlite"
            db_path.write_text("", encoding="utf-8")
            raw_path.chmod(0)
            try:
                payload = freshness.freshness_payload(base, db_path)
            finally:
                raw_path.chmod(0o600)

        self.assertEqual(payload["status"], "degraded")
        self.assertFalse(payload["needs_analyze"])
        self.assertEqual(payload["data_health"], "degraded")
        self.assertEqual(payload["pending_raw_rows"], 0)
        self.assertIn("freshness_source_read_error", [warning["code"] for warning in payload["warnings"]])

    def test_dashboard_freshness_degrades_when_normalized_log_cannot_be_read(self) -> None:
        freshness = load_module("dashboard_freshness_unreadable_normalized_test", ROOT / "scripts" / "dashboard_freshness.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            db_path = self.write_empty_freshness_fixture(base)
            normalized_path = base / "normalized" / "prompt-usage.normalized.jsonl"
            normalized_path.write_text(json.dumps({"turn_id": "pending"}) + "\n", encoding="utf-8")
            normalized_path.chmod(0)
            try:
                payload = freshness.freshness_payload(base, db_path)
            finally:
                normalized_path.chmod(0o600)

        self.assertEqual(payload["status"], "degraded")
        self.assertFalse(payload["needs_analyze"])
        self.assertEqual(payload["data_health"], "degraded")
        self.assertEqual(payload["pending_normalized_rows"], 0)
        self.assertIn("freshness_source_read_error", [warning["code"] for warning in payload["warnings"]])

    def test_dashboard_freshness_counts_missing_start_recovery_state_as_analyze_needed(self) -> None:
        freshness = load_module("dashboard_freshness_pending_recovery_test", ROOT / "scripts" / "dashboard_freshness.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            state_dir = base / "state"
            normalized_dir = base / "normalized"
            analytics_dir = base / "analytics"
            state_dir.mkdir(parents=True)
            normalized_dir.mkdir(parents=True)
            analytics_dir.mkdir(parents=True)
            (state_dir / "pending-turn.json").write_text(
                json.dumps({"record_type": "turn_stop_missing_start", "session_id": "s1", "turn_id": "t1"}),
                encoding="utf-8",
            )
            (state_dir / "rebuild-progress.1.1.json").write_text(
                json.dumps({"status": "running"}),
                encoding="utf-8",
            )
            (normalized_dir / "normalize-state.json").write_text(
                json.dumps({"logic_version": NORMALIZE_LOGIC_VERSION, "sources": {}, "processed_segments": {}}),
                encoding="utf-8",
            )
            (state_dir / "current-raw-segments.json").write_text(
                json.dumps({"schema_version": 1, "base": str(base.resolve()), "current": {}}),
                encoding="utf-8",
            )
            (state_dir / "raw-segments-manifest.json").write_text(
                json.dumps({"schema_version": 1, "base": str(base.resolve()), "segments": []}),
                encoding="utf-8",
            )
            db_path = analytics_dir / "bola.sqlite"
            db_path.write_text("", encoding="utf-8")

            payload = freshness.freshness_payload(base, db_path)

        self.assertEqual(payload["status"], "needs_analyze")
        self.assertTrue(payload["needs_analyze"])
        self.assertEqual(payload["pending_recovery_files"], 1)
        self.assertEqual(payload["pending_analysis_rows"], 0)

    def test_dashboard_freshness_ignores_stale_turn_start_without_terminal_evidence(self) -> None:
        freshness = load_module("dashboard_freshness_stale_active_turn_test", ROOT / "scripts" / "dashboard_freshness.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            db_path = self.write_empty_freshness_fixture(base)
            (base / "state" / "active-turn.json").write_text(
                json.dumps(
                    {
                        "record_type": "turn_start",
                        "session_id": "s-active",
                        "turn_id": "t-active",
                        "captured_at": "2000-01-01T00:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )

            payload = freshness.freshness_payload(base, db_path)

        self.assertEqual(payload["status"], "current")
        self.assertFalse(payload["needs_analyze"])
        self.assertEqual(payload["pending_recovery_files"], 0)

    def test_dashboard_freshness_counts_turn_start_with_terminal_event_as_recovery_pending(self) -> None:
        freshness = load_module("dashboard_freshness_terminal_turn_start_test", ROOT / "scripts" / "dashboard_freshness.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            db_path = self.write_empty_freshness_fixture(base)
            transcript = base / "rollout.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "type": "event_msg",
                        "timestamp": "2026-06-22T00:00:00+00:00",
                        "payload": {"type": "task_complete", "turn_id": "t-terminal"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (base / "state" / "pending-turn.json").write_text(
                json.dumps(
                    {
                        "record_type": "turn_start",
                        "session_id": "s-terminal",
                        "turn_id": "t-terminal",
                        "transcript_path": str(transcript),
                        "captured_at": "2026-06-21T00:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )

            payload = freshness.freshness_payload(base, db_path)

        self.assertEqual(payload["status"], "needs_analyze")
        self.assertTrue(payload["needs_analyze"])
        self.assertEqual(payload["pending_recovery_files"], 1)

    def test_dashboard_freshness_does_not_match_terminal_event_before_turn_start_offset(self) -> None:
        freshness = load_module("dashboard_freshness_terminal_turn_start_fallback_test", ROOT / "scripts" / "dashboard_freshness.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            db_path = self.write_empty_freshness_fixture(base)
            transcript = base / "rollout.jsonl"
            terminal = json.dumps({"type": "event_msg", "timestamp": "2026-06-22T00:00:00+00:00", "payload": {"type": "task_complete", "turn_id": "t-terminal"}}) + "\n"
            transcript.write_text(terminal + json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": {}}}) + "\n", encoding="utf-8")
            (base / "state" / "pending-turn.json").write_text(
                json.dumps(
                    {
                        "record_type": "turn_start",
                        "session_id": "s-terminal",
                        "turn_id": "t-terminal",
                        "transcript_path": str(transcript),
                        "start_file_size": len(terminal),
                        "captured_at": "2026-06-21T00:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )

            payload = freshness.freshness_payload(base, db_path)

        self.assertEqual(payload["status"], "current")
        self.assertFalse(payload["needs_analyze"])
        self.assertEqual(payload["pending_recovery_files"], 0)

    def test_dashboard_freshness_ignores_malformed_transcript_bytes_for_recovery(self) -> None:
        freshness = load_module("dashboard_freshness_malformed_recovery_test", ROOT / "scripts" / "dashboard_freshness.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            db_path = self.write_empty_freshness_fixture(base)
            transcript = base / "rollout.jsonl"
            transcript.write_bytes(b"\xff\n")
            (base / "state" / "pending-turn.json").write_text(
                json.dumps(
                    {
                        "record_type": "turn_start",
                        "session_id": "s-bad",
                        "turn_id": "t-bad",
                        "transcript_path": str(transcript),
                        "start_file_size": 0,
                        "captured_at": "2026-06-21T00:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )

            payload = freshness.freshness_payload(base, db_path)

        self.assertEqual(payload["status"], "current")
        self.assertFalse(payload["needs_analyze"])
        self.assertEqual(payload["pending_recovery_files"], 0)

    def test_dashboard_freshness_caches_terminal_scan_per_transcript(self) -> None:
        freshness = load_module("dashboard_freshness_terminal_scan_cache_test", ROOT / "scripts" / "dashboard_freshness.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            db_path = self.write_empty_freshness_fixture(base)
            transcript = base / "rollout.jsonl"
            prefix = json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": {}}}) + "\n"
            terminal_one = json.dumps({"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "t-one"}}) + "\n"
            terminal_two = json.dumps({"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "t-two"}}) + "\n"
            transcript.write_text(prefix + terminal_one + terminal_two, encoding="utf-8")
            start_file_size = len(prefix.encode("utf-8"))
            for name, turn_id in (("one", "t-one"), ("two", "t-two")):
                (base / "state" / f"pending-{name}.json").write_text(
                    json.dumps(
                        {
                            "record_type": "turn_start",
                            "session_id": f"s-{name}",
                            "turn_id": turn_id,
                            "transcript_path": str(transcript),
                            "start_file_size": start_file_size,
                            "captured_at": "2026-06-21T00:00:00+00:00",
                        }
                    ),
                    encoding="utf-8",
                )
            original_open = pathlib.Path.open
            transcript_opens = 0

            def counting_open(path: pathlib.Path, *args: object, **kwargs: object):
                nonlocal transcript_opens
                if pathlib.Path(path) == transcript:
                    transcript_opens += 1
                return original_open(path, *args, **kwargs)

            with mock.patch.object(pathlib.Path, "open", counting_open):
                payload = freshness.freshness_payload(base, db_path)

        self.assertEqual(payload["pending_recovery_files"], 2)
        self.assertLessEqual(transcript_opens, 2)

    def test_dashboard_freshness_scans_only_new_complete_transcript_suffixes(self) -> None:
        freshness = load_module("dashboard_freshness_terminal_suffix_cache_test", ROOT / "scripts" / "dashboard_freshness.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            transcript = pathlib.Path(tmp_dir) / "rollout.jsonl"
            prefix = json.dumps({"type": "event_msg", "payload": {"type": "token_count"}}) + "\n"
            terminal_one = json.dumps({"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "t-one"}}) + "\n"
            transcript.write_text(prefix + terminal_one, encoding="utf-8")
            states = [{"turn_id": "t-one", "start_file_size": len(prefix.encode("utf-8"))}]

            with mock.patch.object(freshness, "_scan_terminal_turn_ids", wraps=freshness._scan_terminal_turn_ids) as scan:
                self.assertEqual(freshness._terminal_turn_ids_for_pending_states(transcript, states), {"t-one"})
                self.assertEqual(freshness._terminal_turn_ids_for_pending_states(transcript, states), {"t-one"})
                self.assertEqual(scan.call_count, 1)

                partial = json.dumps({"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "t-two"}})
                with transcript.open("ab") as handle:
                    handle.write(partial.encode("utf-8"))
                self.assertEqual(freshness._terminal_turn_ids_for_pending_states(transcript, states), {"t-one"})
                self.assertEqual(scan.call_count, 1)

                with transcript.open("ab") as handle:
                    handle.write(b"\n")
                states.append({"turn_id": "t-two", "start_file_size": len(prefix.encode("utf-8"))})
                self.assertEqual(freshness._terminal_turn_ids_for_pending_states(transcript, states), {"t-one", "t-two"})
                self.assertEqual(scan.call_count, 2)

    def test_dashboard_freshness_invalidates_same_size_transcript_rewrite(self) -> None:
        freshness = load_module("dashboard_freshness_terminal_rewrite_cache_test", ROOT / "scripts" / "dashboard_freshness.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            transcript = pathlib.Path(tmp_dir) / "rollout.jsonl"
            one = json.dumps({"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "t-one"}}) + "\n"
            two = json.dumps({"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "t-two"}}) + "\n"
            self.assertEqual(len(one), len(two))
            transcript.write_text(one, encoding="utf-8")
            states = [{"turn_id": "t-one", "start_file_size": 0}]
            self.assertEqual(freshness._terminal_turn_ids_for_pending_states(transcript, states), {"t-one"})

            time.sleep(0.002)
            transcript.write_text(two, encoding="utf-8")
            self.assertEqual(
                freshness._terminal_turn_ids_for_pending_states(transcript, [{"turn_id": "t-two", "start_file_size": 0}]),
                {"t-two"},
            )

    def test_dashboard_freshness_coalesces_concurrent_terminal_scans(self) -> None:
        freshness = load_module("dashboard_freshness_terminal_singleflight_test", ROOT / "scripts" / "dashboard_freshness.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            transcript = pathlib.Path(tmp_dir) / "rollout.jsonl"
            transcript.write_text(
                json.dumps({"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "t-one"}}) + "\n",
                encoding="utf-8",
            )
            states = [{"turn_id": "t-one", "start_file_size": 0}]
            original_scan = freshness._scan_terminal_turn_ids

            def slow_scan(*args, **kwargs):
                time.sleep(0.02)
                return original_scan(*args, **kwargs)

            with mock.patch.object(freshness, "_scan_terminal_turn_ids", side_effect=slow_scan) as scan:
                with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                    results = list(executor.map(lambda _index: freshness._terminal_turn_ids_for_pending_states(transcript, states), range(4)))

            self.assertEqual(results, [{"t-one"}] * 4)
            self.assertEqual(scan.call_count, 1)

    def test_dashboard_freshness_ignores_recent_turn_start_recovery_state(self) -> None:
        freshness = load_module("dashboard_freshness_recent_turn_start_test", ROOT / "scripts" / "dashboard_freshness.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            db_path = self.write_empty_freshness_fixture(base)
            (base / "state" / "active-turn.json").write_text(
                json.dumps(
                    {
                        "record_type": "turn_start",
                        "session_id": "s-active",
                        "turn_id": "t-active",
                        "captured_at_ns": 9_999_999_999_999_999_999,
                    }
                ),
                encoding="utf-8",
            )

            payload = freshness.freshness_payload(base, db_path)

        self.assertEqual(payload["status"], "current")
        self.assertFalse(payload["needs_analyze"])
        self.assertEqual(payload["pending_recovery_files"], 0)

    def test_dashboard_freshness_counts_missing_start_but_not_stale_unknown_turn_start_recovery_state(self) -> None:
        freshness = load_module("dashboard_freshness_stale_turn_start_test", ROOT / "scripts" / "dashboard_freshness.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            db_path = self.write_empty_freshness_fixture(base)
            state_dir = base / "state"
            (state_dir / "stale-turn.json").write_text(
                json.dumps(
                    {
                        "record_type": "turn_start",
                        "session_id": "s-stale",
                        "turn_id": "t-stale",
                        "captured_at": "2000-01-01T00:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            (state_dir / "unknown-age-turn.json").write_text(
                json.dumps({"record_type": "turn_start", "session_id": "s-unknown", "turn_id": "t-unknown"}),
                encoding="utf-8",
            )
            (state_dir / "missing-start.json").write_text(
                json.dumps(
                    {
                        "record_type": "turn_stop_missing_start",
                        "session_id": "s-missing",
                        "turn_id": "t-missing",
                        "captured_at_ns": 9_999_999_999_999_999_999,
                    }
                ),
                encoding="utf-8",
            )

            payload = freshness.freshness_payload(base, db_path)

        self.assertEqual(payload["status"], "needs_analyze")
        self.assertTrue(payload["needs_analyze"])
        self.assertEqual(payload["pending_recovery_files"], 1)

    def test_dashboard_freshness_excludes_closed_current_segments_from_pending_rows(self) -> None:
        freshness = load_module("dashboard_freshness_closed_current_segments_test", ROOT / "scripts" / "dashboard_freshness.py")
        raw_segments = load_module("dashboard_freshness_closed_current_raw_segments_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            normalized_dir = base / "normalized"
            analytics_dir = base / "analytics"
            normalized_dir.mkdir(parents=True)
            analytics_dir.mkdir(parents=True)
            closed_payload = json.dumps({"record_type": "turn_usage_raw", "turn_id": "closed"}) + "\n"
            active_first = json.dumps({"record_type": "turn_usage_raw", "turn_id": "active-1"}) + "\n"
            active_second = json.dumps({"record_type": "turn_usage_raw", "turn_id": "active-2"}) + "\n"

            closed_current = raw_segments.ensure_current_segment(base, kind="prompt_usage", source_name="prompt-usage.raw.jsonl")
            pathlib.Path(closed_current["path"]).write_text(closed_payload, encoding="utf-8")
            rotation = raw_segments.rotate_all_current_segments(base)
            closed_segment = rotation["prompt_usage"]["closed_segment"]
            active_segment = rotation["prompt_usage"]["current_segment"]
            closed_path = pathlib.Path(closed_segment["path"])
            active_path = pathlib.Path(active_segment["path"])
            active_path.write_text(active_first + active_second, encoding="utf-8")
            raw_segments.strict_read_manifest(base)
            raw_segments.validate_current_pointer_entries(base)
            (normalized_dir / "normalize-state.json").write_text(
                json.dumps(
                    {
                        "logic_version": NORMALIZE_LOGIC_VERSION,
                        "sources": {str(active_path): len(active_first)},
                        "processed_segments": {closed_segment["id"]: {"path": str(closed_path), "bytes": closed_segment["bytes"], "rows": closed_segment["rows"]}},
                    }
                ),
                encoding="utf-8",
            )
            db_path = analytics_dir / "bola.sqlite"
            db_path.write_text("", encoding="utf-8")

            payload = freshness.freshness_payload(base, db_path)
            active_mtime = active_path.stat().st_mtime

        self.assertEqual(payload["status"], "needs_analyze")
        self.assertTrue(payload["needs_analyze"])
        self.assertEqual(payload["pending_raw_rows"], 1)
        self.assertEqual(payload["pending_raw_files"], 1)
        self.assertEqual(payload["pending_analysis_rows"], 1)
        self.assertEqual(payload["data_health"], "ok")
        self.assertEqual(payload["warnings"], [])
        self.assertGreaterEqual(payload["latest_raw_mtime_unix"], active_mtime)

    def test_dashboard_freshness_missing_pointer_falls_back_to_orphan_current(self) -> None:
        freshness = load_module("dashboard_freshness_missing_pointer_fallback_test", ROOT / "scripts" / "dashboard_freshness.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            raw_dir = base / "raw" / "current"
            analytics_dir = base / "analytics"
            raw_dir.mkdir(parents=True)
            analytics_dir.mkdir(parents=True)
            raw_path = raw_dir / "prompt-usage.raw.jsonl.current.orphan.jsonl"
            raw_path.write_text(json.dumps({"record_type": "turn_usage_raw", "turn_id": "orphan"}) + "\n", encoding="utf-8")
            db_path = analytics_dir / "bola.sqlite"
            db_path.write_text("", encoding="utf-8")

            payload = freshness.freshness_payload(base, db_path)

        self.assertEqual(payload["status"], "needs_analyze")
        self.assertTrue(payload["needs_analyze"])
        self.assertEqual(payload["data_health"], "degraded")
        self.assertEqual(payload["pending_raw_rows"], 1)
        self.assertEqual(payload["pending_raw_files"], 1)
        self.assertEqual(payload["pending_analysis_rows"], 1)
        self.assertEqual([warning["code"] for warning in payload["warnings"]], ["current_pointer_missing", "normalize_state_missing", "raw_manifest_missing"])
        self.assertGreater(payload["latest_raw_mtime_unix"], 0)

    def test_dashboard_freshness_corrupt_pointer_reports_degraded_fallback(self) -> None:
        freshness = load_module("dashboard_freshness_corrupt_pointer_fallback_test", ROOT / "scripts" / "dashboard_freshness.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            raw_dir = base / "raw" / "current"
            state_dir = base / "state"
            analytics_dir = base / "analytics"
            raw_dir.mkdir(parents=True)
            state_dir.mkdir(parents=True)
            analytics_dir.mkdir(parents=True)
            raw_path = raw_dir / "prompt-usage.raw.jsonl.current.orphan.jsonl"
            raw_path.write_text(json.dumps({"record_type": "turn_usage_raw", "turn_id": "orphan"}) + "\n", encoding="utf-8")
            (state_dir / "current-raw-segments.json").write_text("{bad\n", encoding="utf-8")
            db_path = analytics_dir / "bola.sqlite"
            db_path.write_text("", encoding="utf-8")

            payload = freshness.freshness_payload(base, db_path)

        self.assertEqual(payload["status"], "needs_analyze")
        self.assertEqual(payload["data_health"], "degraded")
        self.assertEqual(payload["pending_raw_rows"], 1)
        self.assertIn("current_pointer_invalid_json", [warning["code"] for warning in payload["warnings"]])

    def test_dashboard_freshness_pointer_base_mismatch_is_degraded_fallback(self) -> None:
        freshness = load_module("dashboard_freshness_pointer_base_mismatch_test", ROOT / "scripts" / "dashboard_freshness.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            raw_dir = base / "raw" / "current"
            state_dir = base / "state"
            normalized_dir = base / "normalized"
            analytics_dir = base / "analytics"
            raw_dir.mkdir(parents=True)
            state_dir.mkdir(parents=True)
            normalized_dir.mkdir(parents=True)
            analytics_dir.mkdir(parents=True)
            raw_path = raw_dir / "prompt-usage.raw.jsonl.current.orphan.jsonl"
            raw_path.write_text(json.dumps({"record_type": "turn_usage_raw", "turn_id": "orphan"}) + "\n", encoding="utf-8")
            (state_dir / "current-raw-segments.json").write_text(json.dumps({"schema_version": 1, "base": "/old/wrong", "current": {}}), encoding="utf-8")
            (state_dir / "raw-segments-manifest.json").write_text(json.dumps({"schema_version": 1, "base": str(base.resolve()), "segments": []}), encoding="utf-8")
            (normalized_dir / "normalize-state.json").write_text(json.dumps({"logic_version": NORMALIZE_LOGIC_VERSION, "sources": {}, "processed_segments": {}}), encoding="utf-8")
            db_path = analytics_dir / "bola.sqlite"
            db_path.write_text("", encoding="utf-8")

            payload = freshness.freshness_payload(base, db_path)

        self.assertEqual(payload["status"], "needs_analyze")
        self.assertEqual(payload["data_health"], "degraded")
        self.assertEqual(payload["pending_raw_rows"], 1)
        self.assertIn("current_pointer_base_mismatch", [warning["code"] for warning in payload["warnings"]])

    def test_dashboard_freshness_manifest_base_mismatch_does_not_exclude_fallback_current(self) -> None:
        freshness = load_module("dashboard_freshness_manifest_base_mismatch_test", ROOT / "scripts" / "dashboard_freshness.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            raw_dir = base / "raw" / "current"
            state_dir = base / "state"
            normalized_dir = base / "normalized"
            analytics_dir = base / "analytics"
            raw_dir.mkdir(parents=True)
            state_dir.mkdir(parents=True)
            normalized_dir.mkdir(parents=True)
            analytics_dir.mkdir(parents=True)
            raw_path = raw_dir / "prompt-usage.raw.jsonl.current.orphan.jsonl"
            payload = (json.dumps({"record_type": "turn_usage_raw", "turn_id": "orphan"}) + "\n").encode("utf-8")
            raw_path.write_bytes(payload)
            segment_id = raw_path.name.removesuffix(".jsonl")
            (state_dir / "raw-segments-manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "base": "/old/wrong",
                        "segments": [_raw_segment(raw_path, payload=payload, min_time=None, max_time=None, rows=1, segment_id=segment_id)],
                    }
                ),
                encoding="utf-8",
            )
            (normalized_dir / "normalize-state.json").write_text(json.dumps({"logic_version": NORMALIZE_LOGIC_VERSION, "sources": {}, "processed_segments": {segment_id: {"path": str(raw_path)}}}), encoding="utf-8")
            db_path = analytics_dir / "bola.sqlite"
            db_path.write_text("", encoding="utf-8")

            result = freshness.freshness_payload(base, db_path)

        self.assertEqual(result["status"], "needs_analyze")
        self.assertEqual(result["data_health"], "degraded")
        self.assertEqual(result["pending_raw_rows"], 1)
        self.assertIn("raw_manifest_base_mismatch", [warning["code"] for warning in result["warnings"]])

    def test_dashboard_freshness_stale_pointer_missing_segment_falls_back_to_orphan_current(self) -> None:
        freshness = load_module("dashboard_freshness_stale_pointer_missing_segment_test", ROOT / "scripts" / "dashboard_freshness.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            raw_dir = base / "raw" / "current"
            state_dir = base / "state"
            normalized_dir = base / "normalized"
            analytics_dir = base / "analytics"
            raw_dir.mkdir(parents=True)
            state_dir.mkdir(parents=True)
            normalized_dir.mkdir(parents=True)
            analytics_dir.mkdir(parents=True)
            missing_path = raw_dir / "prompt-usage.raw.jsonl.current.missing.jsonl"
            orphan_path = raw_dir / "prompt-usage.raw.jsonl.current.orphan.jsonl"
            orphan_path.write_text(json.dumps({"record_type": "turn_usage_raw", "turn_id": "orphan"}) + "\n", encoding="utf-8")
            (state_dir / "current-raw-segments.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "base": str(base.resolve()),
                        "current": {"prompt_usage": {"id": "missing", "kind": "prompt_usage", "path": str(missing_path), "source_name": "prompt-usage.raw.jsonl"}},
                    }
                ),
                encoding="utf-8",
            )
            (state_dir / "raw-segments-manifest.json").write_text(json.dumps({"schema_version": 1, "base": str(base.resolve()), "segments": []}), encoding="utf-8")
            (normalized_dir / "normalize-state.json").write_text(json.dumps({"logic_version": NORMALIZE_LOGIC_VERSION, "sources": {}, "processed_segments": {}}), encoding="utf-8")
            db_path = analytics_dir / "bola.sqlite"
            db_path.write_text("", encoding="utf-8")

            result = freshness.freshness_payload(base, db_path)

        self.assertEqual(result["status"], "needs_analyze")
        self.assertEqual(result["data_health"], "degraded")
        self.assertEqual(result["pending_raw_rows"], 1)
        self.assertIn("current_pointer_segment_missing", [warning["code"] for warning in result["warnings"]])

    def test_dashboard_freshness_stale_normalize_state_uses_source_from_zero_offset(self) -> None:
        freshness = load_module("dashboard_freshness_stale_normalize_state_test", ROOT / "scripts" / "dashboard_freshness.py")
        raw_segments = load_module("dashboard_freshness_stale_normalize_raw_segments_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            normalized_dir = base / "normalized"
            analytics_dir = base / "analytics"
            normalized_dir.mkdir(parents=True)
            analytics_dir.mkdir(parents=True)
            raw_path = pathlib.Path(raw_segments.ensure_current_segment(base, kind="prompt_usage", source_name="prompt-usage.raw.jsonl")["path"])
            first = json.dumps({"record_type": "turn_usage_raw", "turn_id": "first"}) + "\n"
            second = json.dumps({"record_type": "turn_usage_raw", "turn_id": "second"}) + "\n"
            raw_path.write_text(first + second, encoding="utf-8")
            raw_segments.write_manifest(base, raw_segments.empty_manifest(base))
            (normalized_dir / "normalize-state.json").write_text(json.dumps({"logic_version": NORMALIZE_LOGIC_VERSION - 1, "sources": {str(raw_path): len(first)}, "processed_segments": {}}), encoding="utf-8")
            db_path = analytics_dir / "bola.sqlite"
            db_path.write_text("", encoding="utf-8")

            result = freshness.freshness_payload(base, db_path)

        self.assertEqual(result["status"], "needs_analyze")
        self.assertEqual(result["data_health"], "degraded")
        self.assertEqual(result["pending_raw_rows"], 2)
        self.assertIn("normalize_state_logic_version_mismatch", [warning["code"] for warning in result["warnings"]])

    def test_dashboard_freshness_valid_empty_pointer_does_not_scan_orphan_current_glob(self) -> None:
        freshness = load_module("dashboard_freshness_empty_pointer_no_glob_test", ROOT / "scripts" / "dashboard_freshness.py")
        raw_segments = load_module("dashboard_freshness_empty_pointer_raw_segments_test", ROOT / "scripts" / "raw_segments.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            raw_dir = base / "raw" / "current"
            analytics_dir = base / "analytics"
            raw_dir.mkdir(parents=True)
            analytics_dir.mkdir(parents=True)
            raw_path = raw_dir / "prompt-usage.raw.jsonl.current.orphan.jsonl"
            payload = (json.dumps({"record_type": "turn_usage_raw", "turn_id": "closed"}) + "\n").encode("utf-8")
            raw_path.write_bytes(payload)
            raw_segments.write_current_pointer(base, raw_segments.empty_current_pointer(base))
            raw_segments.write_manifest(base, raw_segments.empty_manifest(base) | {"segments": [_raw_segment(raw_path, payload=payload, min_time=None, max_time=None, rows=1)]})
            (base / "normalized").mkdir(parents=True)
            (base / "normalized" / "normalize-state.json").write_text(
                json.dumps({"logic_version": NORMALIZE_LOGIC_VERSION, "sources": {}, "processed_segments": {raw_path.name.removesuffix(".jsonl"): {"path": str(raw_path)}}}),
                encoding="utf-8",
            )
            db_path = analytics_dir / "bola.sqlite"
            db_path.write_text("", encoding="utf-8")

            result = freshness.freshness_payload(base, db_path)

        self.assertEqual(result["status"], "current")
        self.assertFalse(result["needs_analyze"])
        self.assertEqual(result["pending_raw_rows"], 0)
        self.assertEqual(result["pending_raw_files"], 0)
        self.assertEqual(result["data_health"], "ok")
        self.assertEqual(result["warnings"], [])

    def test_dashboard_api_injects_freshness_health(self) -> None:
        serve = load_module("serve_dashboard_freshness_health_test", ROOT / "scripts" / "serve_dashboard.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            raw_dir = base / "raw" / "current"
            analytics_dir = base / "analytics"
            raw_dir.mkdir(parents=True)
            analytics_dir.mkdir(parents=True)
            raw_path = raw_dir / "prompt-usage.raw.jsonl.current.orphan.jsonl"
            raw_path.write_text(json.dumps({"record_type": "turn_usage_raw", "turn_id": "orphan"}) + "\n", encoding="utf-8")
            db_path = analytics_dir / "bola.sqlite"
            db_path.write_text("", encoding="utf-8")
            serve.OUTPUT_DIR = base
            handler = serve.Handler.__new__(serve.Handler)
            handler.server = types.SimpleNamespace(db_path=db_path)

            payload = handler.with_freshness("/api/dashboard", {"summary": {"turns": 0}})

        self.assertEqual(payload["freshness"]["status"], "needs_analyze")
        self.assertEqual(payload["freshness"]["data_health"], "degraded")
        self.assertEqual(payload["freshness"]["pending_raw_rows"], 1)
        self.assertIn("warnings", payload["freshness"])
        self.assertIn("current_pointer_missing", [warning["code"] for warning in payload["freshness"]["warnings"]])

    def test_dynamic_dashboard_uses_new_output_on_next_request(self) -> None:
        serve = load_module("serve_dashboard_dynamic_paths_test", ROOT / "scripts" / "serve_dashboard.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            first = root / "A"
            second = root / "B"
            handler = serve.Handler.__new__(serve.Handler)
            handler.server = types.SimpleNamespace(dynamic_runtime_paths=True, db_override=None)
            with mock.patch.dict(serve.os.environ, {"XDG_CONFIG_HOME": str(root / "config")}, clear=True):
                serve.service_paths.write_config({"output_dir": first})
                first_snapshot = handler.dashboard_output_dir()
                serve.service_paths.write_config({"output_dir": second})
                same_request_snapshot = handler.dashboard_output_dir()
                handler._runtime_paths_snapshot = None
                next_request_snapshot = handler.dashboard_output_dir()

        self.assertEqual(first_snapshot, first)
        self.assertEqual(same_request_snapshot, first)
        self.assertEqual(next_request_snapshot, second)

    def test_dashboard_freshness_detects_normalized_rows_not_built_into_db(self) -> None:
        freshness = load_module("dashboard_freshness_pending_normalized_test", ROOT / "scripts" / "dashboard_freshness.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir)
            normalized_dir = base / "normalized"
            analytics_dir = base / "analytics"
            normalized_dir.mkdir(parents=True)
            analytics_dir.mkdir(parents=True)
            normalized = normalized_dir / "prompt-usage.normalized.jsonl"
            first = json.dumps({"record_type": "turn_usage_normalized", "turn_id": "t1"}) + "\n"
            second = json.dumps({"record_type": "turn_usage_normalized", "turn_id": "t2"}) + "\n"
            normalized.write_text(first + second, encoding="utf-8")
            db_path = analytics_dir / "bola.sqlite"
            con = sqlite3.connect(db_path)
            con.execute("create table run_metadata(key text primary key, value text not null)")
            con.execute("insert into run_metadata values (?,?)", ("applied_normalized_turns_size", json.dumps(len(first))))
            con.commit()
            con.close()

            payload = freshness.freshness_payload(base, db_path)

        self.assertEqual(payload["status"], "needs_analyze")
        self.assertTrue(payload["needs_analyze"])
        self.assertEqual(payload["pending_raw_rows"], 0)
        self.assertEqual(payload["pending_normalized_rows"], 1)
        self.assertEqual(payload["pending_analysis_rows"], 1)

