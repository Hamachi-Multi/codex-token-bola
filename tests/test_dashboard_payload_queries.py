from __future__ import annotations

try:
    from tests.support import DashboardFixtureMixin, ROOT, dashboard_asset_bundle, json, load_module, mock, pathlib, sqlite3, tempfile, time, unittest
except ModuleNotFoundError:
    from support import DashboardFixtureMixin, ROOT, dashboard_asset_bundle, json, load_module, mock, pathlib, sqlite3, tempfile, time, unittest

DASHBOARD_ASSET_BUNDLE = dashboard_asset_bundle()


class DashboardPayloadQueryTests(DashboardFixtureMixin, unittest.TestCase):
    def test_int_query_falls_back_and_clamps(self) -> None:
        queries = load_module("dashboard_queries_test", ROOT / "scripts" / "dashboard_queries.py")
        self.assertEqual(queries.int_query({"days": ["bad"]}, "days", 7, 0, 3650), 7)
        self.assertEqual(queries.int_query({"page": ["-2"]}, "page", 1, 1, 100), 1)
        self.assertEqual(queries.int_query({"per_page": ["500"]}, "per_page", 25, 1, 100), 100)

    def test_cost_rates_payload_prioritizes_detected_models_and_reports_coverage(self) -> None:
        api = load_module("dashboard_cost_rates_payload_test", ROOT / "scripts" / "dashboard_cost_rates_api.py")
        schema = load_module("dashboard_cost_rates_schema_test", ROOT / "scripts" / "build_analytics_schema.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            db_path = root / "analytics.sqlite"
            config_path = root / "cost-rates.json"
            con = sqlite3.connect(db_path)
            schema.setup_db(con)
            con.executemany(
                """
                insert into turns (
                  session_id, turn_id, captured_at_unix, started_at_unix,
                  model, estimated, weighted_credits, cost_rate_status
                ) values (?, ?, ?, ?, ?, 0, ?, ?)
                """,
                [
                    ("s1", "t1", 1_775_001_600, 1_775_001_600, "gpt-5.5", None, "unconfigured"),
                    ("s2", "t2", 1_785_542_400, 1_785_542_400, "gpt-5.6-terra", 2.0, "configured"),
                    ("s3", "t3", 1_785_542_400, 1_785_542_400, None, None, "unconfigured"),
                ],
            )
            con.commit()
            con.close()

            payload = api.cost_rates_payload(config_path=config_path, db_path=db_path)
            by_model = {row["model_id"]: row for row in payload["models"]}

            self.assertEqual([row["model_id"] for row in payload["models"][:3]], ["gpt-5.5", "gpt-5.6-terra", "unknown"])
            self.assertEqual(by_model["gpt-5.5"]["status"], "configured")
            self.assertFalse(by_model["gpt-5.5"]["coverage_required"])
            self.assertTrue(by_model["gpt-5.5"]["current"]["is_default"])
            self.assertIsNone(by_model["gpt-5.5"]["current"]["effective_from"])
            self.assertEqual(by_model["gpt-5.6-terra"]["status"], "configured")
            self.assertFalse(by_model["gpt-5.6-terra"]["coverage_required"])
            self.assertEqual(by_model["unknown"]["status"], "unavailable")
            gpt_56_history = by_model["gpt-5.6"]["history"]
            self.assertFalse(next(rate for rate in gpt_56_history if rate["is_default"])["deletable"])
            self.assertTrue(next(rate for rate in gpt_56_history if rate["effective_from"] == "2026-08-21")["deletable"])
            self.assertTrue(payload["rebuild_required"])

            con = sqlite3.connect(db_path)
            con.execute(
                "insert or replace into run_metadata values (?, ?)",
                ("cost_rate_catalog_digest", json.dumps(payload["catalog_digest"])),
            )
            con.commit()
            con.close()
            current = api.cost_rates_payload(config_path=config_path, db_path=db_path)
            self.assertFalse(current["rebuild_required"])

    def test_dashboard_cost_totals_use_exact_per_turn_cost_units(self) -> None:
        queries = load_module("dashboard_queries_exact_cost_test", ROOT / "scripts" / "dashboard_queries.py")
        schema = load_module("dashboard_schema_exact_cost_test", ROOT / "scripts" / "build_analytics_schema.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = pathlib.Path(tmp_dir) / "analytics.sqlite"
            con = sqlite3.connect(db_path)
            schema.setup_db(con)
            con.executemany(
                """
                insert into turns (
                  session_id, turn_id, started_at_unix, estimated,
                  weighted_credits, cost_pico_usd, cost_rate_status
                ) values (?, ?, ?, 0, ?, ?, 'configured')
                """,
                [
                    ("s1", "t1", 1.0, 99.0, 1_000_001),
                    ("s1", "t2", 2.0, 88.0, 2_000_002),
                ],
            )
            con.commit()
            con.row_factory = sqlite3.Row
            payload = queries.DashboardQueries(con, {"days": ["0"]}).summary_payload()
            con.close()

        self.assertEqual(payload["weighted_credits"], 3.000003)
        self.assertTrue(payload["cost_complete"])

    def test_turn_date_scope_and_order_use_prompt_start_not_recovery_capture(self) -> None:
        queries = load_module("dashboard_queries_prompt_time_test", ROOT / "scripts" / "dashboard_queries.py")
        schema = load_module("dashboard_schema_prompt_time_test", ROOT / "scripts" / "build_analytics_schema.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = pathlib.Path(tmp_dir) / "analytics.sqlite"
            con = sqlite3.connect(db_path)
            schema.setup_db(con)
            now = int(time.time())
            con.executemany(
                """
                insert into turns (
                  session_id, turn_id, captured_at, captured_at_unix, started_at, started_at_unix,
                  turn_status, estimated, prompt_preview, weighted_credits, total_tokens, model_call_count
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("old", "recovered", "2026-08-23T07:57:02+00:00", now, "2026-07-19T17:39:06+00:00", now - 35 * 86400, "completed", 1, "old recovered prompt", 1.0, 100, 1),
                    ("new", "normal", "2026-08-20T00:00:00+00:00", now - 3 * 86400, "2026-08-23T06:57:02+00:00", now - 3600, "completed", 0, "recent prompt", 2.0, 200, 1),
                ],
            )
            con.commit()
            con.row_factory = sqlite3.Row
            scoped = queries.DashboardQueries(con, {"days": ["1"], "page": ["1"], "per_page": ["25"]}).turns_payload()
            ordered = queries.DashboardQueries(con, {"days": ["0"], "page": ["1"], "per_page": ["25"]}).turns_payload()
            con.close()

        self.assertEqual([row["turn_id"] for row in scoped["rows"]], ["normal"])
        self.assertEqual([row["turn_id"] for row in ordered["rows"]], ["normal", "recovered"])
        self.assertEqual(ordered["rows"][0]["started_at"], "2026-08-23T06:57:02+00:00")

    def test_turn_page_is_clamped_after_filter_count(self) -> None:
        queries = load_module("dashboard_queries_turn_page_clamp_test", ROOT / "scripts" / "dashboard_queries.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = pathlib.Path(tmp_dir) / "analytics.sqlite"
            self._write_dashboard_fixture(db_path)
            con = sqlite3.connect(db_path)
            con.row_factory = sqlite3.Row
            session_id, filtered_total = con.execute(
                "select session_id, count(*) from turns group by session_id order by count(*) asc limit 1"
            ).fetchone()
            payload = queries.DashboardQueries(
                con,
                {
                    "days": ["0"],
                    "session_id": [session_id],
                    "page": ["999"],
                    "per_page": ["1"],
                    "sort": ["date"],
                    "sort_dir": ["desc"],
                },
            ).turns_payload()
            con.close()

        self.assertEqual(payload["total"], filtered_total)
        self.assertEqual(payload["page"], filtered_total)
        self.assertEqual(payload["per_page"], 1)
        self.assertEqual(len(payload["rows"]), 1)

    def test_dashboard_payload_can_focus_a_specific_turn(self) -> None:
        queries = load_module("dashboard_queries_focus_turn_test", ROOT / "scripts" / "dashboard_queries.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = pathlib.Path(tmp_dir) / "analytics.sqlite"
            self._write_dashboard_fixture(db_path)
            con = sqlite3.connect(db_path)
            con.row_factory = sqlite3.Row
            payload = queries.DashboardQueries(
                con,
                {
                    "days": ["0"],
                    "limit": ["0"],
                    "focus_session_id": ["s2"],
                    "focus_turn_id": ["t2"],
                },
            ).dashboard_payload()
            con.close()

        self.assertEqual(payload["turns"]["total"], 1)
        self.assertEqual(payload["turns"]["rows"][0]["session_id"], "s2")
        self.assertEqual(payload["turns"]["rows"][0]["turn_id"], "t2")
        self.assertTrue(payload["turns"]["focused"])
        self.assertEqual(payload["summary"]["turns"], 1)
        self.assertEqual(payload["summary"]["total_tokens"], 900)
        self.assertEqual(payload["summary"]["weighted_credits"], 9.0)
        self.assertEqual(payload["summary"]["model_calls"], 2)

    def test_dashboard_focus_turn_keeps_summary_in_scope(self) -> None:
        queries = load_module("dashboard_queries_focus_scope_test", ROOT / "scripts" / "dashboard_queries.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = pathlib.Path(tmp_dir) / "analytics.sqlite"
            self._write_dashboard_fixture(db_path)
            con = sqlite3.connect(db_path)
            con.row_factory = sqlite3.Row
            payload = queries.DashboardQueries(
                con,
                {
                    "days": ["0"],
                    "session_id": ["s1"],
                    "focus_session_id": ["s2"],
                    "focus_turn_id": ["t2"],
                },
            ).dashboard_payload()
            con.close()

        self.assertTrue(payload["turns"]["focused"])
        self.assertEqual(payload["turns"]["total"], 0)
        self.assertEqual(payload["turns"]["rows"], [])
        self.assertEqual(payload["summary"]["turns"], 0)

    def test_empty_dashboard_summaries_return_zero_numbers(self) -> None:
        queries = load_module("dashboard_queries_empty_summary_zero_test", ROOT / "scripts" / "dashboard_queries.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = pathlib.Path(tmp_dir) / "analytics.sqlite"
            self._write_dashboard_fixture(db_path)
            con = sqlite3.connect(db_path)
            con.row_factory = sqlite3.Row
            dashboard = queries.DashboardQueries(con, {"days": ["0"], "session_id": ["missing"]}).dashboard_payload()
            summary = queries.DashboardQueries(con, {"days": ["0"], "session_id": ["missing"]}).summary_payload()
            session_detail = queries.DashboardQueries(con, {"days": ["0"], "selected_session_id": ["missing"]}).session_detail_payload()
            con.close()

        for payload in (dashboard["summary"], summary):
            self.assertEqual(payload["turns"], 0)
            self.assertEqual(payload["total_tokens"], 0)
            self.assertEqual(payload["input_tokens"], 0)
            self.assertEqual(payload["cached_input_tokens"], 0)
            self.assertEqual(payload["non_cached_input_tokens"], 0)
            self.assertEqual(payload["output_tokens"], 0)
            self.assertEqual(payload["reasoning_output_tokens"], 0)
            self.assertEqual(payload["model_calls"], 0)
            self.assertEqual(payload["tool_calls"], 0)
            self.assertEqual(payload["weighted_credits"], 0)
            self.assertEqual(payload["cached_ratio"], 0)

        for detail in (session_detail["summary"],):
            self.assertEqual(detail["turns"], 0)
            self.assertEqual(detail["raw"], 0)
            self.assertEqual(detail["credits"], 0)
            self.assertEqual(detail["model_calls"], 0)
            self.assertEqual(detail["non_cached_input_tokens"], 0)
            self.assertEqual(detail["cached_ratio"], 0)

    def test_session_detail_rollups_preserve_unpriced_costs(self) -> None:
        queries = load_module("dashboard_queries_session_detail_null_rollup_test", ROOT / "scripts" / "dashboard_queries.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = pathlib.Path(tmp_dir) / "analytics.sqlite"
            self._write_dashboard_fixture(db_path)
            con = sqlite3.connect(db_path)
            con.execute("update turns set total_tokens=null, weighted_credits=null where session_id='s2'")
            con.execute(
                """
                insert into tool_call_summaries values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("s2", "t2", "null-tool", "shell", None, None, None, None, 0, None, None, None),
            )
            con.commit()
            con.row_factory = sqlite3.Row
            detail = queries.DashboardQueries(con, {"days": ["0"], "selected_session_id": ["s2"]}).session_detail_payload()
            con.close()

        self.assertEqual(detail["workflows"][0]["raw"], 0)
        self.assertIsNone(detail["workflows"][0]["credits"])
        null_tool = next(row for row in detail["tools"] if row["tool_name"] == "null-tool")
        self.assertEqual(null_tool["calls"], 0)
        self.assertEqual(null_tool["output_tokens"], 0)

    def test_turn_payload_exposes_summaries_without_legacy_detail_arrays(self) -> None:
        queries = load_module("dashboard_queries_turn_contract_test", ROOT / "scripts" / "dashboard_queries.py")
        fixture = load_module("dashboard_fixture_data_turn_contract_test", ROOT / "scripts" / "dashboard_fixture_data.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            codex_dir = pathlib.Path(tmp_dir) / "codex-dir"
            db_path = fixture.write_dashboard_fixture(codex_dir)
            con = sqlite3.connect(db_path)
            con.row_factory = sqlite3.Row

            payload = queries.DashboardQueries(
                con,
                {
                    "days": ["0"],
                    "session_id": ["11111111-2222-3333-4444-555555555555"],
                    "turn_id": ["turn-00"],
                },
            ).payload("/api/turn")
            con.close()

        self.assertIn("model_call_summary", payload)
        self.assertIn("tool_call_summary", payload)
        self.assertIn("model_call_total", payload)
        self.assertIn("tool_call_total", payload)
        self.assertNotIn("model_calls", payload)
        self.assertNotIn("tool_calls", payload)
        self.assertNotIn("limited", payload)

    def test_dashboard_lite_payload_defers_heavy_rollup_lists(self) -> None:
        queries = load_module("dashboard_queries_lite_payload_test", ROOT / "scripts" / "dashboard_queries.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = pathlib.Path(tmp_dir) / "analytics.sqlite"
            self._write_dashboard_fixture(db_path)
            con = sqlite3.connect(db_path)
            con.row_factory = sqlite3.Row

            lite = queries.DashboardQueries(con, {"days": ["0"], "lite": ["1"], "page": ["1"], "per_page": ["2"]}).payload("/api/dashboard")
            full = queries.DashboardQueries(con, {"days": ["0"], "page": ["1"], "per_page": ["2"]}).payload("/api/dashboard")
            sessions = queries.DashboardQueries(con, {"days": ["0"], "sessions_page": ["1"], "per_page": ["1"]}).payload("/api/sessions")
            tools = queries.DashboardQueries(con, {"days": ["0"], "tools_page": ["1"], "per_page": ["1"]}).payload("/api/tools")
            con.close()

        self.assertEqual(lite["summary"]["turns"], 2)
        self.assertEqual(len(lite["turns"]["rows"]), 2)
        self.assertEqual(lite["projects"]["rows"], [])
        self.assertEqual(lite["sessions"]["rows"], [])
        self.assertEqual(lite["tools"]["rows"], [])
        self.assertEqual([row["rows"] for row in lite["subagents"]["rows"]], [0, 0, 0, 0, 0])
        self.assertEqual([row["session_id"] for row in full["sessions"]["rows"]], ["s2", "s1"])
        self.assertEqual([row["session_id"] for row in sessions["rows"]], ["s2"])
        self.assertEqual(sessions["total"], 2)
        self.assertEqual(sessions["page"], 1)
        self.assertEqual(sessions["per_page"], 1)
        self.assertEqual(len(tools["rows"]), 1)
        self.assertEqual(tools["total"], 1)
        self.assertGreater(tools["output_tokens_total"], 0)

    def test_tools_payload_materializes_selected_scope_once(self) -> None:
        queries = load_module("dashboard_queries_tools_scope_once_test", ROOT / "scripts" / "dashboard_queries.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = pathlib.Path(tmp_dir) / "analytics.sqlite"
            self._write_dashboard_fixture(db_path)
            con = sqlite3.connect(db_path)
            con.row_factory = sqlite3.Row
            query_builder = queries.DashboardQueries(con, {"days": ["0"], "tools_page": ["1"], "per_page": ["25"]})

            with mock.patch.object(query_builder, "create_selected_turns_temp", wraps=query_builder.create_selected_turns_temp) as create_scope:
                payload = query_builder.tools_payload()
            con.close()

        self.assertEqual(create_scope.call_count, 1)
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["rows"][0]["tool_name"], "exec_command")

    def test_tool_payload_materializes_selected_scope_once(self) -> None:
        queries = load_module("dashboard_queries_tool_scope_once_test", ROOT / "scripts" / "dashboard_queries.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = pathlib.Path(tmp_dir) / "analytics.sqlite"
            self._write_dashboard_fixture(db_path)
            con = sqlite3.connect(db_path)
            con.row_factory = sqlite3.Row
            con.executemany(
                "insert into tool_call_samples values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    ("s2", "t2", f"largest-{index}", "exec_command", "exec", "largest_output", 1, None, None, 20 + index, 400 + index, 0, 1000 + index, "completed", 0)
                    for index in range(12)
                ],
            )
            con.commit()
            query_builder = queries.DashboardQueries(con, {"days": ["0"], "tool_name": ["exec_command"]})

            with mock.patch.object(query_builder, "create_selected_turns_temp", wraps=query_builder.create_selected_turns_temp) as create_scope:
                payload = query_builder.tool_payload()
            temp_tables = {row[0] for row in con.execute("select name from sqlite_temp_master where type='table'")}
            session_plan = "\n".join(
                str(tuple(row))
                for row in con.execute(
                    """
                    explain query plan
                    select session_id, thread_name, cwd, calls, output_chars, reported_tokens, output_tokens
                    from selected_tool_detail_sessions
                    order by output_tokens desc, session_id desc
                    limit 12
                    """
                )
            )
            con.close()

        self.assertEqual(create_scope.call_count, 1)
        self.assertIn("selected_tool_detail_sessions", temp_tables)
        self.assertIn("idx_selected_tool_detail_sessions_output", session_plan)
        self.assertNotIn("USE TEMP B-TREE", session_plan)
        self.assertEqual(payload["summary"]["tool_name"], "exec_command")
        self.assertEqual(payload["summary"]["calls"], 2)
        self.assertEqual(payload["summary"]["output_tokens"], 110)
        self.assertEqual([row["session_id"] for row in payload["sessions"]], ["s2", "s1"])
        self.assertEqual(payload["sessions"][0]["project"], "beta")
        self.assertEqual(len(payload["calls"]), 10)
        self.assertTrue(all(row["project"] == "beta" for row in payload["calls"]))
        self.assertEqual([row["output_tokens"] for row in payload["calls"]], list(range(1011, 1001, -1)))

    def test_tool_payload_returns_zero_for_null_numeric_sums(self) -> None:
        queries = load_module("dashboard_queries_tool_null_sums_test", ROOT / "scripts" / "dashboard_queries.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = pathlib.Path(tmp_dir) / "analytics.sqlite"
            self._write_dashboard_fixture(db_path)
            con = sqlite3.connect(db_path)
            con.execute(
                """
                insert into tool_call_summaries values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("s2", "t2", "null-tool", "exec", None, None, None, None, 0, None, None, None),
            )
            con.commit()
            con.row_factory = sqlite3.Row

            dashboard = queries.DashboardQueries(con, {"days": ["0"]}).dashboard_payload()
            payload = queries.DashboardQueries(con, {"days": ["0"], "tool_name": ["null-tool"]}).tool_payload()
            con.close()

        dashboard_row = next(row for row in dashboard["tools"]["rows"] if row["tool_name"] == "null-tool")
        self.assertEqual(dashboard_row["calls"], 0)
        self.assertEqual(dashboard_row["output_chars"], 0)
        self.assertEqual(dashboard_row["reported_tokens"], 0)
        self.assertEqual(dashboard_row["output_tokens"], 0)
        self.assertEqual(payload["summary"]["tool_name"], "null-tool")
        self.assertEqual(payload["summary"]["calls"], 0)
        self.assertEqual(payload["summary"]["output_chars"], 0)
        self.assertEqual(payload["summary"]["reported_tokens"], 0)
        self.assertEqual(payload["summary"]["output_tokens"], 0)
        self.assertEqual(payload["summary"]["avg_output_chars"], 0)
        self.assertEqual(payload["summary"]["avg_output_tokens"], 0)
        self.assertEqual(payload["summary"]["avg_duration_ms"], 0)
        self.assertEqual(payload["sessions"][0]["calls"], 0)
        self.assertEqual(payload["sessions"][0]["output_chars"], 0)
        self.assertEqual(payload["sessions"][0]["reported_tokens"], 0)
        self.assertEqual(payload["sessions"][0]["output_tokens"], 0)

    def test_subagents_payload_keeps_all_confidence_methods(self) -> None:
        queries = load_module("dashboard_queries_subagents_complete_rows_test", ROOT / "scripts" / "dashboard_queries.py")
        fixture = load_module("dashboard_fixture_data_subagents_complete_rows_test", ROOT / "scripts" / "dashboard_fixture_data.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = fixture.write_dashboard_fixture(pathlib.Path(tmp_dir))
            con = sqlite3.connect(db_path)
            con.row_factory = sqlite3.Row

            payload = queries.DashboardQueries(con, {"days": ["0"]}).subagents_payload()
            con.close()

        self.assertEqual(
            [row["confidence"] for row in payload["rows"]],
            [
                "child_task_time_overlap",
                "orphan",
                "parent_pruned_by_retention",
                "spawn_call_turn_context",
                "spawn_edge_nearest_parent_turn",
            ],
        )
        self.assertEqual(len(payload["rows"]), 5)
        self.assertEqual(next(row for row in payload["rows"] if row["confidence"] == "child_task_time_overlap")["rows"], 1)

    def test_dashboard_ignores_removed_analysis_limit_percent(self) -> None:
        queries = load_module("dashboard_queries_removed_limit_percent_test", ROOT / "scripts" / "dashboard_queries.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = pathlib.Path(tmp_dir) / "analytics.sqlite"
            self._write_dashboard_fixture(db_path)
            con = sqlite3.connect(db_path)
            con.executemany(
                """
                insert into turns values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("s3", "t3", 3, "2026-01-01T00:00:03+00:00", "/example/src/gamma", "gamma", "", "medium prompt", "completed", 4.0, 400, 300, 200, 100, 100, 0, 1),
                    ("s4", "t4", 4, "2026-01-01T00:00:04+00:00", "/example/src/delta", "delta", "", "tiny prompt", "completed", 0.5, 50, 40, 20, 20, 10, 0, 1),
                ],
            )
            con.commit()
            con.row_factory = sqlite3.Row

            pct_50 = queries.DashboardQueries(con, {"days": ["0"], "limit_percent": ["50"]}).dashboard_payload()
            pct_25 = queries.DashboardQueries(con, {"days": ["0"], "limit_percent": ["25"]}).dashboard_payload()
            pct_100 = queries.DashboardQueries(con, {"days": ["0"], "limit_percent": ["100"]}).dashboard_payload()
            con.close()

        self.assertEqual(pct_50["summary"]["turns"], 4)
        self.assertEqual(pct_50["summary"]["weighted_credits"], 14.5)
        self.assertEqual(pct_50["summary"]["tool_calls"], 2)
        self.assertEqual(pct_25["summary"], pct_50["summary"])
        self.assertEqual(pct_100["summary"], pct_50["summary"])

    def test_dashboard_first_column_lists_are_not_fixed_to_twenty_rows(self) -> None:
        queries = load_module("dashboard_queries_first_column_limit_test", ROOT / "scripts" / "dashboard_queries.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = pathlib.Path(tmp_dir) / "analytics.sqlite"
            self._write_dashboard_fixture(db_path)
            con = sqlite3.connect(db_path)
            extra_turns = []
            extra_tools = []
            for index in range(3, 28):
                session_id = f"s{index}"
                turn_id = f"t{index}"
                extra_turns.append(
                    (
                        session_id,
                        turn_id,
                        index,
                        f"2026-01-01T00:00:{index:02d}+00:00",
                        f"/example/src/session-{index}",
                        "many",
                        f"thread {index}",
                        f"prompt {index}",
                        "completed",
                        float(index),
                        index * 100,
                        index * 80,
                        index * 50,
                        index * 30,
                        index * 20,
                        0,
                        1,
                    )
                )
                extra_tools.append(
                    (
                        session_id,
                        turn_id,
                        f"tool_{index:02d}",
                        "exec",
                        1,
                        index * 10,
                        0,
                        index * 10,
                        0,
                        10,
                        10,
                        index * 10,
                    )
                )
            con.executemany("insert into turns values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", extra_turns)
            con.executemany("insert into tool_call_summaries values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", extra_tools)
            con.commit()
            con.row_factory = sqlite3.Row
            payload = queries.DashboardQueries(
                con,
                {
                    "days": ["0"],
                    "per_page": ["10"],
                    "sessions_page": ["2"],
                    "tools_page": ["2"],
                },
            ).dashboard_payload()
            con.close()

        self.assertEqual(len(payload["sessions"]["rows"]), 10)
        self.assertGreater(payload["sessions"]["total"], 20)
        self.assertEqual(payload["sessions"]["page"], 2)
        self.assertEqual(payload["sessions"]["per_page"], 10)
        self.assertEqual(len(payload["tools"]["rows"]), 10)
        self.assertGreater(payload["tools"]["total"], 20)
        self.assertEqual(payload["tools"]["page"], 2)
        self.assertEqual(payload["tools"]["per_page"], 10)
        self.assertGreater(payload["tools"]["output_tokens_total"], 0)

    def test_rollup_payloads_apply_column_sort_parameters_before_pagination(self) -> None:
        queries = load_module("dashboard_queries_rollup_sort_test", ROOT / "scripts" / "dashboard_queries.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = pathlib.Path(tmp_dir) / "analytics.sqlite"
            self._write_dashboard_fixture(db_path)
            con = sqlite3.connect(db_path)
            con.row_factory = sqlite3.Row
            con.execute(
                "insert into tool_call_summaries values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("s1", "t1", "apply_patch", "functions", 5, 300, 0, 50, 0, 25, 25, 50),
            )
            con.execute(
                "insert into tool_call_summaries values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("s2", "t2", "view_image", "functions", 2, 900, 0, 150, 0, 30, 30, 150),
            )
            con.commit()

            sessions_by_raw = queries.DashboardQueries(
                con,
                {"days": ["0"], "session_sort": ["raw"], "session_sort_dir": ["asc"], "sessions_page": ["1"], "per_page": ["1"]},
            ).sessions_payload()
            sessions_by_name = queries.DashboardQueries(
                con,
                {"days": ["0"], "session_label_mode": ["project"], "session_sort": ["session"], "session_sort_dir": ["asc"], "sessions_page": ["1"], "per_page": ["2"]},
            ).sessions_payload()
            sessions_by_thread = queries.DashboardQueries(
                con,
                {"days": ["0"], "session_label_mode": ["thread"], "session_sort": ["session"], "session_sort_dir": ["asc"], "sessions_page": ["1"], "per_page": ["2"]},
            ).sessions_payload()
            tools_by_calls = queries.DashboardQueries(
                con,
                {"days": ["0"], "tool_sort": ["calls"], "tool_sort_dir": ["desc"], "tools_page": ["1"], "per_page": ["3"]},
            ).tools_payload()
            tools_by_share = queries.DashboardQueries(
                con,
                {"days": ["0"], "tool_sort": ["share"], "tool_sort_dir": ["asc"], "tools_page": ["1"], "per_page": ["3"]},
            ).tools_payload()
            con.close()

        self.assertEqual([row["session_id"] for row in sessions_by_raw["rows"]], ["s1"])
        self.assertEqual(sessions_by_raw["total"], 2)
        self.assertEqual([row["session_id"] for row in sessions_by_name["rows"]], ["s1", "s2"])
        self.assertEqual([row["session_id"] for row in sessions_by_thread["rows"]], ["s2", "s1"])
        self.assertEqual([row["tool_name"] for row in tools_by_calls["rows"]], ["apply_patch", "exec_command", "view_image"])
        self.assertEqual([row["tool_name"] for row in tools_by_share["rows"]], ["apply_patch", "exec_command", "view_image"])

    def test_subagent_payload_applies_column_sort_after_completing_methods(self) -> None:
        queries = load_module("dashboard_queries_subagent_sort_test", ROOT / "scripts" / "dashboard_queries.py")
        fixture = load_module("dashboard_fixture_data_subagent_sort_test", ROOT / "scripts" / "dashboard_fixture_data.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = fixture.write_dashboard_fixture(pathlib.Path(tmp_dir))
            con = sqlite3.connect(db_path)
            con.row_factory = sqlite3.Row

            default_rows = queries.DashboardQueries(con, {"days": ["0"]}).subagents_payload()["rows"]
            confidence_rows = queries.DashboardQueries(
                con,
                {"days": ["0"], "subagent_sort": ["confidence"], "subagent_sort_dir": ["asc"]},
            ).subagents_payload()["rows"]
            credits_asc = queries.DashboardQueries(
                con,
                {"days": ["0"], "subagent_sort": ["child_credits"], "subagent_sort_dir": ["asc"]},
            ).subagents_payload()["rows"]
            con.close()

        self.assertEqual(default_rows[0]["confidence"], "child_task_time_overlap")
        self.assertEqual(confidence_rows[0]["confidence"], "child_task_time_overlap")
        self.assertEqual(confidence_rows[-1]["confidence"], "spawn_edge_nearest_parent_turn")
        self.assertEqual(credits_asc[0]["child_credits"], 0.0)
        self.assertEqual(credits_asc[-1]["confidence"], "child_task_time_overlap")

    def test_analysis_scope_percent_control_is_removed(self) -> None:
        self.assertNotIn('id="rows"', DASHBOARD_ASSET_BUNDLE)
        self.assertNotIn("Analysis rollup scope", DASHBOARD_ASSET_BUNDLE)
        self.assertNotIn("Top 10%", DASHBOARD_ASSET_BUNDLE)
        self.assertNotIn("Top 25%", DASHBOARD_ASSET_BUNDLE)
        self.assertNotIn("custom-percent", DASHBOARD_ASSET_BUNDLE)
        self.assertNotIn("limit_percent", DASHBOARD_ASSET_BUNDLE)
        self.assertNotIn("analysisPercentValue", DASHBOARD_ASSET_BUNDLE)
        self.assertNotIn("appliedRowsMode", DASHBOARD_ASSET_BUNDLE)
    def test_overview_uses_real_session_rows_not_inferred_projects(self) -> None:
        queries = load_module("dashboard_queries_overview_session_test", ROOT / "scripts" / "dashboard_queries.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = pathlib.Path(tmp_dir) / "analytics.sqlite"
            self._write_dashboard_fixture(db_path)
            con = sqlite3.connect(db_path)
            con.row_factory = sqlite3.Row
            payload = queries.DashboardQueries(con, {"days": ["0"]}).dashboard_payload()
            detail = queries.DashboardQueries(con, {"days": ["0"], "selected_session_id": ["s2"]}).session_detail_payload()
            con.close()

        self.assertEqual([row["session_id"] for row in payload["sessions"]["rows"]], ["s2", "s1"])
        self.assertEqual(payload["sessions"]["rows"][0]["thread_name"], "")
        self.assertEqual(payload["sessions"]["rows"][0]["cwd"], "/example/.codex/codex-token-bola")
        self.assertEqual(payload["sessions"]["rows"][0]["project"], "beta")
        self.assertEqual(payload["sessions"]["rows"][1]["thread_name"], "zulu")
        self.assertEqual(payload["turns"]["rows"][0]["cwd"], "/example/.codex/codex-token-bola")
        self.assertEqual(detail["summary"]["session_id"], "s2")
        self.assertEqual(detail["summary"]["thread_name"], "")
        self.assertEqual(detail["summary"]["project"], "beta")
        self.assertEqual(detail["summary"]["cwd"], "/example/.codex/codex-token-bola")
        self.assertEqual(detail["summary"]["turns"], 1)
        self.assertIn("<h2>Overview</h2>", DASHBOARD_ASSET_BUNDLE)
        self.assertIn("<h2>Session Detail</h2>", DASHBOARD_ASSET_BUNDLE)
        self.assertNotIn("<h2>Sessions</h2>", DASHBOARD_ASSET_BUNDLE)
        self.assertNotIn("<h2>Session Cost</h2>", DASHBOARD_ASSET_BUNDLE)
        self.assertNotIn("<h2>Project Cost</h2>", DASHBOARD_ASSET_BUNDLE)
        self.assertNotIn("<h2>Project Detail</h2>", DASHBOARD_ASSET_BUNDLE)
        self.assertIn("q.set('lite', '1');", DASHBOARD_ASSET_BUNDLE)
        self.assertIn("const { summary, turns } = dashboard;", DASHBOARD_ASSET_BUNDLE)
        self.assertIn("async function loadOverviewData(seq = state.requestSeq, page = state.listPages.projects || 1, busy = false)", DASHBOARD_ASSET_BUNDLE)
        self.assertIn("const path = sessionsPath(page);", DASHBOARD_ASSET_BUNDLE)
        self.assertIn("async function loadToolsData(seq = state.requestSeq, page = state.listPages.tools || 1, busy = false)", DASHBOARD_ASSET_BUNDLE)
        self.assertIn("const path = toolsPath(page);", DASHBOARD_ASSET_BUNDLE)
        self.assertIn("async function loadSubagentData(seq = state.requestSeq)", DASHBOARD_ASSET_BUNDLE)
        self.assertIn("async function loadRollupData({", DASHBOARD_ASSET_BUNDLE)
        self.assertEqual(DASHBOARD_ASSET_BUNDLE.count("return loadRollupData({"), 3)
        self.assertIn("const path = subagentsPath();", DASHBOARD_ASSET_BUNDLE)
        self.assertIn("loadVisibleRollupData(seq);", DASHBOARD_ASSET_BUNDLE)
        self.assertIn("return prepareDetail(key, detailRoutes.session(key));", DASHBOARD_ASSET_BUNDLE)
        self.assertNotIn("/api/project-detail", DASHBOARD_ASSET_BUNDLE)
        self.assertNotIn("project_detail_payload", (ROOT / "scripts" / "dashboard_queries.py").read_text(encoding="utf-8"))
        self.assertIn("{label:'Session', sort:'session'}, {label:'Cost Units', sort:'credits', cls:'num'}, {label:'Total Tokens', sort:'raw', cls:'num'}, {label:'Turns', sort:'turns', cls:'num'}", DASHBOARD_ASSET_BUNDLE)
        self.assertNotIn("data-project-key", DASHBOARD_ASSET_BUNDLE)
    def test_session_filter_payload_and_options(self) -> None:
        queries = load_module("dashboard_queries_session_filter_payload_test", ROOT / "scripts" / "dashboard_queries.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = pathlib.Path(tmp_dir) / "analytics.sqlite"
            self._write_dashboard_fixture(db_path)
            con = sqlite3.connect(db_path)
            con.row_factory = sqlite3.Row
            filtered = queries.DashboardQueries(con, {"days": ["0"], "session_id": ["s1"], "page": ["1"], "per_page": ["10"]}).turns_payload()
            options = queries.DashboardQueries(con, {}).session_options_payload()
            con.close()

        self.assertEqual(filtered["total"], 1)
        self.assertEqual(filtered["rows"][0]["session_id"], "s1")
        self.assertEqual([row["session_id"] for row in options["rows"]], ["s2", "s1"])
        self.assertEqual(options["rows"][1]["thread_name"], "zulu")
        self.assertEqual(options["rows"][1]["project"], "alpha")
        self.assertEqual(options["limit"], 50)
        self.assertFalse(options["has_more"])
    def test_session_options_are_server_filtered_and_limited(self) -> None:
        queries = load_module("dashboard_queries_session_options_search_test", ROOT / "scripts" / "dashboard_queries.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = pathlib.Path(tmp_dir) / "analytics.sqlite"
            self._write_dashboard_fixture(db_path)
            con = sqlite3.connect(db_path)
            con.executemany(
                "insert into turns values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    ("s3", "t3", 3, "2026-01-01T00:00:03+00:00", "/example/src/alpha", "alpha", "quant", "prompt", "completed", 3.0, 300, 200, 100, 100, 100, 0, 1),
                    ("s4", "t4", 4, "2026-01-01T00:00:04+00:00", "/example/src/beta", "beta", "research", "prompt", "completed", 4.0, 400, 300, 200, 100, 100, 0, 1),
                    ("s5", "t5", 5, "2026-01-01T00:00:05+00:00", "/example/src/quant-tools", "quant-tools", "", "prompt", "completed", 5.0, 500, 400, 300, 100, 100, 0, 1),
                ],
            )
            con.commit()
            con.row_factory = sqlite3.Row
            limited = queries.DashboardQueries(con, {"limit": ["2"]}).session_options_payload()
            searched = queries.DashboardQueries(con, {"q": ["quant"], "limit": ["50"]}).session_options_payload()
            con.close()

        self.assertEqual([row["session_id"] for row in limited["rows"]], ["s5", "s4"])
        self.assertEqual(limited["limit"], 2)
        self.assertTrue(limited["has_more"])
        self.assertEqual({row["session_id"] for row in searched["rows"]}, {"s3", "s5"})
        self.assertFalse(searched["has_more"])
    def test_subagent_detail_includes_rollups_whose_parent_turn_was_pruned(self) -> None:
        queries = load_module("dashboard_queries_subagent_pruned_detail_test", ROOT / "scripts" / "dashboard_queries.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = pathlib.Path(tmp_dir) / "analytics.sqlite"
            con = sqlite3.connect(db_path)
            con.row_factory = sqlite3.Row
            con.executescript(
                """
                create table turns (
                  session_id text,
                  turn_id text,
                  captured_at_unix real,
                  cwd text,
                  project text,
                  thread_name text,
                  prompt_preview text,
                  weighted_credits real,
                  started_at_unix real generated always as (captured_at_unix) virtual
                );
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
                );
                create table tool_call_summaries (
                  session_id text,
                  turn_id text,
                  tool_name text,
                  tool_namespace text,
                  calls integer,
                  output_chars integer,
                  output_reported_tokens integer,
                  output_tokens integer,
                  failed_calls integer,
                  total_duration_ms integer,
                  max_duration_ms integer,
                  max_output_tokens integer
                );
                """
            )
            con.execute("insert into turns values (?, ?, ?, ?, ?, ?, ?, ?)", ("child", "child-turn", 10.0, "/example/.codex/codex-token-bola", "alpha", "child thread", "child prompt", 2.0))
            con.execute(
                "insert into task_rollups values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "parent",
                    "pruned-parent",
                    "child",
                    "reviewer",
                    "r1",
                    "2026-01-10T00:00:00",
                    10.0,
                    "parent_pruned_by_retention",
                    0,
                    200,
                    200,
                    0.0,
                    2.0,
                    2.0,
                ),
            )
            con.commit()

            payload = queries.DashboardQueries(
                con,
                {"days": ["0"], "limit": ["0"], "confidence": ["parent_pruned_by_retention"]},
            ).subagent_payload()
            con.close()

        self.assertEqual(payload["summary"]["rows"], 1)
        self.assertEqual(payload["sessions"][0]["session_id"], "parent")
        self.assertEqual(payload["sessions"][0]["thread_name"], "")
        self.assertEqual(payload["sessions"][0]["cwd"], "/example/.codex/codex-token-bola")
        self.assertEqual(payload["sessions"][0]["project"], "alpha")
        self.assertEqual(payload["rows"][0]["parent_turn_id"], "pruned-parent")
        self.assertEqual(payload["rows"][0]["session_id"], "parent")
        self.assertEqual(payload["rows"][0]["thread_name"], "")
        self.assertEqual(payload["rows"][0]["cwd"], "/example/.codex/codex-token-bola")
        self.assertEqual(payload["rows"][0]["project"], "alpha")
        self.assertEqual(payload["rows"][0]["prompt_preview"], "")

    def test_subagent_payload_uses_full_filtered_scope(self) -> None:
        queries = load_module("dashboard_queries_subagent_full_scope_test", ROOT / "scripts" / "dashboard_queries.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = pathlib.Path(tmp_dir) / "analytics.sqlite"
            con = sqlite3.connect(db_path)
            con.row_factory = sqlite3.Row
            con.executescript(
                """
                create table turns (
                  session_id text,
                  turn_id text,
                  captured_at_unix real,
                  captured_at text,
                  cwd text,
                  project text,
                  thread_name text,
                  prompt_preview text,
                  turn_status text,
                  weighted_credits real,
                  total_tokens integer,
                  input_tokens integer,
                  cached_input_tokens integer,
                  non_cached_input_tokens integer,
                  output_tokens integer,
                  reasoning_output_tokens integer,
                  model_call_count integer,
                  started_at_unix real generated always as (captured_at_unix) virtual,
                  started_at text generated always as (captured_at) virtual
                );
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
                );
                create table tool_call_summaries (
                  session_id text,
                  turn_id text,
                  tool_name text,
                  tool_namespace text,
                  calls integer,
                  output_chars integer,
                  output_reported_tokens integer,
                  output_tokens integer,
                  failed_calls integer,
                  total_duration_ms integer,
                  max_duration_ms integer,
                  max_output_tokens integer
                );
                """
            )
            con.executemany(
                "insert into turns values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    ("selected", "turn", 2.0, "2026-01-01T00:00:02+00:00", "/tmp", "p", "", "selected", "completed", 100.0, 100, 80, 0, 80, 20, 0, 1),
                    ("excluded", "turn", 1.0, "2026-01-01T00:00:01+00:00", "/tmp", "p", "", "excluded", "completed", 1.0, 100, 80, 0, 80, 20, 0, 1),
                ],
            )
            con.executemany(
                "insert into task_rollups values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    ("selected", "turn", "child-a", "reviewer", "a", "2026-01-01T00:00:03+00:00", 3.0, "child_task_time_overlap", 0, 10, 10, 0.0, 10.0, 10.0),
                    ("excluded", "turn", "child-b", "reviewer", "b", "2026-01-01T00:00:04+00:00", 4.0, "child_task_time_overlap", 0, 1000, 1000, 0.0, 1000.0, 1000.0),
                ],
            )
            con.commit()

            dashboard = queries.DashboardQueries(con, {"days": ["0"], "limit": ["1"]}).dashboard_payload()
            subagents = queries.DashboardQueries(con, {"days": ["0"], "limit": ["1"]}).subagents_payload()
            con.close()

        dashboard_row = next(row for row in dashboard["subagents"]["rows"] if row["confidence"] == "child_task_time_overlap")
        subagent_row = next(row for row in subagents["rows"] if row["confidence"] == "child_task_time_overlap")
        self.assertEqual(dashboard_row["child_credits"], 1010.0)
        self.assertEqual(subagent_row["child_credits"], 1010.0)

    def test_tool_detail_selected_tool_includes_description(self) -> None:
        self.assertIn("function toolDescription(value)", DASHBOARD_ASSET_BUNDLE)
        self.assertIn("exec_command: 'shell command execution output captured from terminal runs'", DASHBOARD_ASSET_BUNDLE)
        self.assertIn("toolDisplay(toolName)", DASHBOARD_ASSET_BUNDLE)
        self.assertIn('<span class="method-name">${esc(toolName)}</span><span class="method-desc">${esc(toolDescription(toolName))}</span>', DASHBOARD_ASSET_BUNDLE)
        self.assertIn(".tool-name-cell .value.attribution-method-value {\n      display: grid;\n      gap: 4px;", DASHBOARD_ASSET_BUNDLE)
        self.assertIn(".attribution-method-value .method-name {\n      display: block;", DASHBOARD_ASSET_BUNDLE)
        self.assertIn(".attribution-method-value .method-desc {\n      display: block;", DASHBOARD_ASSET_BUNDLE)
        self.assertNotIn('method-desc"> - ', DASHBOARD_ASSET_BUNDLE)
        self.assertNotIn('<div class="label">Selected tool</div>', DASHBOARD_ASSET_BUNDLE)
