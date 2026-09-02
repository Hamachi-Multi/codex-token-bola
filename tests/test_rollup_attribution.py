from __future__ import annotations

try:
    from tests.support import (
        Any,
        ROOT,
        _turn_raw,
        datetime,
        json,
        load_module,
        mock,
        pathlib,
        sqlite3,
        tempfile,
        unittest,
    )
except ModuleNotFoundError:
    from support import (
        Any,
        ROOT,
        _turn_raw,
        datetime,
        json,
        load_module,
        mock,
        pathlib,
        sqlite3,
        tempfile,
        unittest,
    )


class RollupAttributionTests(unittest.TestCase):
    def test_delete_affected_rollups_preserves_unrelated_rows(self) -> None:
        build = load_module("build_analytics_rollup_delete_test", ROOT / "scripts" / "build_analytics.py")
        con = sqlite3.connect(":memory:")
        try:
            con.execute(
                """
                create table task_rollups (
                  parent_session_id text,
                  parent_turn_id text,
                  child_session_id text,
                  child_agent_role text,
                  child_agent_nickname text,
                  child_started_at text,
                  child_started_unix real,
                  confidence text,
                  own_total_tokens integer,
                  child_total_tokens integer,
                  total_tokens integer,
                  own_weighted_credits real,
                  child_weighted_credits real,
                  total_weighted_credits real
                )
                """
            )
            con.executemany(
                "insert into task_rollups values (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    ("p1", "t1", "c1", None, None, None, 0, "x", 1, 2, 3, 1.0, 2.0, 3.0),
                    ("p2", "t2", "c2", None, None, None, 0, "x", 1, 2, 3, 1.0, 2.0, 3.0),
                ],
            )
            build.delete_affected_rollups(con, {"p1"})
            self.assertEqual(con.execute("select parent_session_id from task_rollups").fetchall(), [("p2",)])
        finally:
            con.close()

    def test_affected_rollup_sessions_closes_over_nested_edges(self) -> None:
        build = load_module("build_analytics_rollup_closure_test", ROOT / "scripts" / "build_analytics.py")
        with mock.patch.object(build, "read_edges", return_value=[("grandparent", "parent", "ok"), ("parent", "child", "ok")]):
            self.assertEqual(build.affected_rollup_sessions({("child", "t1")}), {"grandparent", "parent", "child"})

    def test_retained_child_with_pruned_parent_is_not_generic_orphan(self) -> None:
        build = load_module("build_analytics_retention_pruned_parent_test", ROOT / "scripts" / "build_analytics.py")
        con = sqlite3.connect(":memory:")
        try:
            con.execute(
                """
                create table task_rollups (
                  parent_session_id text,
                  parent_turn_id text,
                  child_session_id text,
                  child_agent_role text,
                  child_agent_nickname text,
                  child_started_at text,
                  child_started_unix real,
                  confidence text,
                  own_total_tokens integer,
                  child_total_tokens integer,
                  total_tokens integer,
                  own_weighted_credits real,
                  child_weighted_credits real,
                  total_weighted_credits real
                )
                """
            )
            with tempfile.TemporaryDirectory() as tmp_dir:
                retention_state = pathlib.Path(tmp_dir) / "retention-pruned-turns.json"
                retention_state.write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "cutoff_unix": datetime.fromisoformat("2026-01-08T00:00:00+00:00").timestamp(),
                            "pruned_turns": [
                                {
                                    "session_id": "parent",
                                    "turn_id": "old-parent",
                                    "captured_at_unix": datetime.fromisoformat("2026-01-01T00:00:00+00:00").timestamp(),
                                }
                            ],
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                previous = build.RETENTION_PRUNED_TURNS_FILE
                build.RETENTION_PRUNED_TURNS_FILE = retention_state
                try:
                    with mock.patch.object(build, "read_edges", return_value=[("parent", "child", "ok")]):
                        build.rebuild_task_rollups(
                            con,
                            {"child": {"created_at_ms": int(datetime.fromisoformat("2026-01-10T00:00:00+00:00").timestamp() * 1000)}},
                            {
                                ("parent", "child"): {
                                    "turn_id": "old-parent",
                                    "spawn_started_at": "2026-01-01T00:00:00Z",
                                    "spawn_completed_at": "2026-01-01T00:00:01Z",
                                }
                            },
                            {("child", "new-child"): {"total_tokens": 200, "weighted_credits": 2.0}},
                            {},
                        )
                finally:
                    build.RETENTION_PRUNED_TURNS_FILE = previous
            row = con.execute(
                "select parent_session_id, parent_turn_id, child_session_id, confidence, own_total_tokens, total_tokens from task_rollups"
            ).fetchone()
            self.assertEqual(row, ("parent", "old-parent", "child", "parent_pruned_by_retention", 0, 200))
        finally:
            con.close()

    def test_task_rollup_indexes_child_usage_once_for_many_edges(self) -> None:
        build = load_module("build_task_rollup_child_index_test", ROOT / "scripts" / "build_analytics.py")

        class CountingDict(dict):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)
                self.items_calls = 0

            def items(self):  # type: ignore[override]
                self.items_calls += 1
                return super().items()

        con = sqlite3.connect(":memory:")
        try:
            build.setup_db(con)
            turn_usage = CountingDict(
                {
                    ("child-a", "t1"): {"total_tokens": 100, "weighted_credits": 1.0},
                    ("child-b", "t1"): {"total_tokens": 200, "weighted_credits": 2.0},
                    ("child-c", "t1"): {"total_tokens": 300, "weighted_credits": 3.0},
                    ("noise", "t1"): {"total_tokens": 999, "weighted_credits": 9.0},
                }
            )
            turn_ranges = {
                "parent": [
                    {
                        "turn_id": "parent-turn",
                        "start_ts": datetime.fromisoformat("2026-01-10T00:00:00+00:00").timestamp(),
                        "stop_ts": datetime.fromisoformat("2026-01-10T00:10:00+00:00").timestamp(),
                    }
                ]
            }
            created_at_ms = int(datetime.fromisoformat("2026-01-10T00:01:00+00:00").timestamp() * 1000)
            build.rebuild_task_rollups(
                con,
                {
                    "child-a": {"created_at_ms": created_at_ms},
                    "child-b": {"created_at_ms": created_at_ms},
                    "child-c": {"created_at_ms": created_at_ms},
                },
                {},
                turn_usage,
                turn_ranges,
                edges=[
                    ("parent", "child-a", "ok"),
                    ("parent", "child-b", "ok"),
                    ("parent", "child-c", "ok"),
                ],
            )
            totals = con.execute("select child_session_id, child_total_tokens from task_rollups order by child_session_id").fetchall()

            self.assertEqual(turn_usage.items_calls, 1)
            self.assertEqual(totals, [("child-a", 100), ("child-b", 200), ("child-c", 300)])
        finally:
            con.close()

    def test_tool_call_rollups_accept_streamed_batches_without_collecting_all_rows(self) -> None:
        build = load_module("build_tool_call_stream_batches_test", ROOT / "scripts" / "build_analytics.py")
        con = sqlite3.connect(":memory:")
        try:
            build.setup_db(con)
            batches = (
                [
                    {
                        "session_id": "s1",
                        "turn_id": "t1",
                        "call_id": "call-1",
                        "tool_name": "shell",
                        "tool_namespace": "shell",
                        "output_chars": 10,
                        "output_reported_tokens": 4,
                        "duration_ms": 20,
                        "status": "completed",
                    }
                ],
                [
                    {
                        "session_id": "s1",
                        "turn_id": "t1",
                        "call_id": "call-2",
                        "tool_name": "shell",
                        "tool_namespace": "shell",
                        "output_chars": 5,
                        "output_reported_tokens": 2,
                        "duration_ms": 30,
                        "status": "failed",
                    }
                ],
            )
            build.replace_tool_call_rollups_from_batches(con, iter(batches))
            row = con.execute(
                "select calls, output_chars, output_reported_tokens, failed_calls, total_duration_ms, max_duration_ms from tool_call_summaries"
            ).fetchone()

            self.assertEqual(row, (2, 15, 6, 1, 50, 30))
        finally:
            con.close()

    def test_log_cleanup_retention_does_not_mutate_raw_when_pruned_state_write_fails(self) -> None:
        cleanup = load_module("dashboard_cleanup_state_first_test", ROOT / "scripts" / "dashboard_cleanup.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = pathlib.Path(tmp_dir) / "token-usage"
            current = cleanup._retention.raw_segments.ensure_current_segment(
                base,
                kind="prompt_usage",
                source_name="prompt-usage.raw.jsonl",
            )
            raw_prompt = pathlib.Path(current["path"])
            raw_prompt.write_text(
                json.dumps(_turn_raw("parent", "old-parent", 100) | {"captured_at": "2026-01-01T00:00:00Z"}) + "\n",
                encoding="utf-8",
            )
            before = raw_prompt.read_text(encoding="utf-8")
            cutoff_unix = datetime.fromisoformat("2026-01-08T00:00:00+00:00").timestamp()

            with mock.patch.object(cleanup._retention, "stage_pruned_turn_state", side_effect=OSError("state write failed")):
                with self.assertRaises(OSError):
                    cleanup.delete_logs_older_than(base, cutoff_unix)

            self.assertEqual(raw_prompt.read_text(encoding="utf-8"), before)

    def test_pruned_turn_state_uses_started_and_stopped_at_for_rollup_matching(self) -> None:
        build = load_module("build_analytics_pruned_time_range_test", ROOT / "scripts" / "build_analytics.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            retention_state = pathlib.Path(tmp_dir) / "retention-pruned-turns.json"
            retention_state.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "pruned_turns": [
                            {
                                "session_id": "parent",
                                "turn_id": "old-parent",
                                "captured_at_unix": datetime.fromisoformat("2026-01-02T00:00:00+00:00").timestamp(),
                                "started_at": "2026-01-01T00:00:00Z",
                                "stopped_at": "2026-01-01T00:00:10Z",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            previous = build.RETENTION_PRUNED_TURNS_FILE
            build.RETENTION_PRUNED_TURNS_FILE = retention_state
            try:
                rows = build.read_retention_pruned_turns()
            finally:
                build.RETENTION_PRUNED_TURNS_FILE = previous

        row = rows["parent"][0]
        self.assertEqual(row["start_ts"], datetime.fromisoformat("2026-01-01T00:00:00+00:00").timestamp())
        self.assertEqual(row["stop_ts"], datetime.fromisoformat("2026-01-01T00:00:10+00:00").timestamp())

    def test_pruned_turn_reader_includes_pending_retention_state(self) -> None:
        build = load_module("build_analytics_pending_pruned_time_range_test", ROOT / "scripts" / "build_analytics.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            retention_state = pathlib.Path(tmp_dir) / "retention-pruned-turns.json"
            pending_state = retention_state.with_name("retention-pruned-turns.pending.json")
            pending_state.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "pruned_turns": [
                            {
                                "session_id": "parent",
                                "turn_id": "pending-parent",
                                "captured_at": "2026-01-01T00:00:00+00:00",
                                "started_at": "2026-01-01T00:00:00+00:00",
                                "stopped_at": "2026-01-01T00:00:10+00:00",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            previous = build.RETENTION_PRUNED_TURNS_FILE
            build.RETENTION_PRUNED_TURNS_FILE = retention_state
            try:
                rows = build.read_retention_pruned_turns()
            finally:
                build.RETENTION_PRUNED_TURNS_FILE = previous

        self.assertEqual(rows["parent"][0]["turn_id"], "pending-parent")

    def test_incremental_build_filters_spawn_context_threads_to_affected_sessions(self) -> None:
        build = load_module("build_analytics_spawn_context_filter_test", ROOT / "scripts" / "build_analytics.py")
        threads = {
            "parent": {"rollout_path": "/tmp/parent.jsonl"},
            "child": {"rollout_path": "/tmp/child.jsonl"},
            "unrelated": {"rollout_path": "/tmp/unrelated.jsonl"},
        }

        filtered = build.spawn_context_threads_for_affected_sessions(threads, {"child", "parent"})

        self.assertEqual(set(filtered), {"parent", "child"})
        self.assertNotIn("unrelated", filtered)


