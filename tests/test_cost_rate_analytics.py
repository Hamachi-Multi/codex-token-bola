from __future__ import annotations

try:
    from tests.support import (
        ROOT,
        argparse,
        io,
        json,
        load_module,
        mock,
        pathlib,
        sqlite3,
        stat,
        tempfile,
        unittest,
    )
except ModuleNotFoundError:
    from support import (
        ROOT,
        argparse,
        io,
        json,
        load_module,
        mock,
        pathlib,
        sqlite3,
        stat,
        tempfile,
        unittest,
    )


class CostRateAnalyticsTests(unittest.TestCase):
    def test_priced_usage_uses_effective_model_rate(self) -> None:
        build = load_module("build_analytics_weighted_units_test", ROOT / "scripts" / "build_analytics.py")
        cost_pico_usd, cost_units, rate = build.priced_usage(
            model="gpt-5.5",
            started_at_unix=build.parse_time("2026-06-01T00:00:00Z"),
            non_cached_input=2_000_000,
            cached_input=1_000_000,
            output=100_000,
        )
        self.assertEqual(cost_pico_usd, 13_500_000_000_000)
        self.assertEqual(cost_units, 13_500_000.0)
        self.assertIsNotNone(rate)
        assert rate is not None
        self.assertIsNone(rate.effective_from)
        self.assertTrue(rate.is_default)

    def test_turn_rows_store_effective_model_cost(self) -> None:
        build = load_module("build_analytics_turn_weighted_units_test", ROOT / "scripts" / "build_analytics.py")
        con = sqlite3.connect(":memory:")
        try:
            build.setup_db(con)
            row = {
                "session_id": "s1",
                "turn_id": "t1",
                "captured_at": "2026-06-01T00:00:00Z",
                "model": "gpt-5.5",
                "cwd": "/example/.codex/codex-token-bola",
                "prompt": {"prompt_preview": "inspect usage"},
                "usage": {
                    "input_tokens": 3_000_000,
                    "cached_input_tokens": 1_000_000,
                    "non_cached_input_tokens": 2_000_000,
                    "output_tokens": 100_000,
                    "reasoning_output_tokens": 0,
                    "total_tokens": 3_100_000,
                },
            }
            turn = build.upsert_turn_row(con, row, {})
            stored = con.execute(
                "select weighted_credits, cost_pico_usd, cost_rate_status, cost_rate_effective_from from turns"
            ).fetchone()
        finally:
            con.close()
        self.assertIsNotNone(turn)
        assert turn is not None
        self.assertEqual(turn["usage"]["weighted_credits"], 13_500_000.0)
        self.assertEqual(stored, (13_500_000.0, 13_500_000_000_000, "configured", None))

    def test_cost_rate_catalog_selects_the_latest_effective_period(self) -> None:
        build = load_module("build_analytics_cost_rate_boundary_test", ROOT / "scripts" / "build_analytics.py")
        catalog, _revision = build.cost_rates.load_catalog(pathlib.Path("/nonexistent/cost-rates.json"))

        before = catalog.resolve("gpt-5.6-terra", build.parse_time("2026-07-29T23:59:59Z"))
        after = catalog.resolve("gpt-5.6-terra", build.parse_time("2026-07-30T00:00:00Z"))
        unavailable = catalog.resolve("new-model", build.parse_time("2026-08-29T00:00:00Z"))

        self.assertIsNotNone(before)
        self.assertIsNotNone(after)
        assert before is not None and after is not None
        self.assertTrue(before.is_default)
        self.assertIsNone(before.effective_from)
        self.assertEqual(build.cost_rates.decimal_text(before.input_price), "2.5")
        self.assertFalse(after.is_default)
        self.assertEqual(after.effective_from, "2026-07-30")
        self.assertEqual(build.cost_rates.decimal_text(after.input_price), "2")
        self.assertIsNone(unavailable)

    def test_cost_rate_overrides_are_atomic_and_revision_checked(self) -> None:
        build = load_module("build_analytics_cost_rate_update_test", ROOT / "scripts" / "build_analytics.py")
        rates = build.cost_rates
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = pathlib.Path(tmp_dir) / "cost-rates.json"
            _catalog, revision = rates.load_catalog(path)
            override = {
                "model_id": "gpt-5.5",
                "effective_from": None,
                "is_default": True,
                "input_price": "4.25",
                "cached_input_price": "0.425",
                "output_price": "25.5",
            }
            catalog, updated_revision = rates.update_custom_rates(
                action="upsert",
                expected_revision=revision,
                rate_payload=override,
                path=path,
            )
            selected = catalog.resolve("gpt-5.5", build.parse_time("2026-06-01T00:00:00Z"))
            self.assertIsNotNone(selected)
            assert selected is not None
            self.assertEqual(rates.decimal_text(selected.input_price), "4.25")
            self.assertTrue(selected.is_default)
            self.assertNotEqual(updated_revision, revision)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

            with self.assertRaises(rates.CostRateRevisionConflict):
                rates.update_custom_rates(
                    action="upsert",
                    expected_revision=revision,
                    rate_payload=override,
                    path=path,
                )

            catalog, reset_revision = rates.update_custom_rates(
                action="reset",
                expected_revision=updated_revision,
                rate_payload=override,
                path=path,
            )
            selected = catalog.resolve("gpt-5.5", build.parse_time("2026-06-01T00:00:00Z"))
            self.assertIsNotNone(selected)
            assert selected is not None
            self.assertEqual(rates.decimal_text(selected.input_price), "5")
            self.assertNotEqual(reset_revision, updated_revision)

    def test_all_cost_rate_overrides_can_be_reset_atomically(self) -> None:
        build = load_module("build_analytics_cost_rate_reset_all_test", ROOT / "scripts" / "build_analytics.py")
        rates = build.cost_rates
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = pathlib.Path(tmp_dir) / "cost-rates.json"
            _catalog, revision = rates.load_catalog(path)
            override = {
                "model_id": "gpt-5.5",
                "effective_from": None,
                "is_default": True,
                "input_price": "4.25",
                "cached_input_price": "0.425",
                "output_price": "25.5",
            }
            _catalog, updated_revision = rates.update_custom_rates(
                action="upsert",
                expected_revision=revision,
                rate_payload=override,
                path=path,
            )

            catalog, reset_revision = rates.reset_all_custom_rates(
                expected_revision=updated_revision,
                path=path,
            )
            custom, stored_revision = rates.read_custom_rates(path)
            selected = catalog.resolve("gpt-5.5", build.parse_time("2026-06-01T00:00:00Z"))

            self.assertEqual(custom, [])
            self.assertEqual(stored_revision, reset_revision)
            self.assertIsNotNone(selected)
            assert selected is not None
            self.assertEqual(rates.decimal_text(selected.input_price), "5")
            with self.assertRaises(rates.CostRateRevisionConflict):
                rates.reset_all_custom_rates(expected_revision=updated_revision, path=path)

    def test_dated_built_in_rate_can_be_deleted_and_default_cannot(self) -> None:
        build = load_module("build_analytics_cost_rate_delete_test", ROOT / "scripts" / "build_analytics.py")
        rates = build.cost_rates
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = pathlib.Path(tmp_dir) / "cost-rates.json"
            _catalog, revision = rates.load_catalog(path)
            dated = {
                "model_id": "gpt-5.6",
                "effective_from": "2026-08-21",
                "is_default": False,
                "input_price": "4",
                "cached_input_price": "0.4",
                "output_price": "20",
            }
            catalog, deleted_revision = rates.update_custom_rates(
                action="delete",
                expected_revision=revision,
                rate_payload=dated,
                path=path,
            )

            selected = catalog.resolve("gpt-5.6", build.parse_time("2026-08-22T00:00:00Z"))
            self.assertIsNotNone(selected)
            assert selected is not None
            self.assertTrue(selected.is_default)
            self.assertEqual(rates.decimal_text(selected.input_price), "5")

            restored, stored_revision = rates.load_catalog(path)
            self.assertEqual(stored_revision, deleted_revision)
            restored_selected = restored.resolve("gpt-5.6", build.parse_time("2026-08-22T00:00:00Z"))
            self.assertIsNotNone(restored_selected)
            assert restored_selected is not None
            self.assertTrue(restored_selected.is_default)

            default = {
                "model_id": "gpt-5.6",
                "effective_from": None,
                "is_default": True,
                "input_price": "5",
                "cached_input_price": "0.5",
                "output_price": "30",
            }
            with self.assertRaises(rates.CostRateError) as forbidden:
                rates.update_custom_rates(
                    action="delete",
                    expected_revision=deleted_revision,
                    rate_payload=default,
                    path=path,
                )
            self.assertEqual(forbidden.exception.error, "cost_rate_delete_forbidden")

            restored_catalog, _reset_revision = rates.reset_all_custom_rates(
                expected_revision=deleted_revision,
                path=path,
            )
            restored_dated = restored_catalog.resolve("gpt-5.6", build.parse_time("2026-08-22T00:00:00Z"))
            self.assertIsNotNone(restored_dated)
            assert restored_dated is not None
            self.assertEqual(restored_dated.effective_from, "2026-08-21")

    def test_cost_rate_validation_rejects_unknown_and_excess_precision(self) -> None:
        build = load_module("build_analytics_cost_rate_validation_test", ROOT / "scripts" / "build_analytics.py")
        rates = build.cost_rates
        with self.assertRaises(rates.CostRateError) as unknown:
            rates.parse_rate(
                {
                    "model_id": "unknown",
                    "effective_from": "2026-08-29",
                    "input_price": "1",
                    "cached_input_price": "0.1",
                    "output_price": "6",
                }
            )
        self.assertEqual(unknown.exception.field, "model_id")

        with self.assertRaises(rates.CostRateError) as precision:
            rates.parse_rate(
                {
                    "model_id": "future-model",
                    "effective_from": "2026-08-29",
                    "input_price": "0.0000001",
                    "cached_input_price": "0",
                    "output_price": "1",
                }
            )
        self.assertEqual(precision.exception.field, "input_price")

        with self.assertRaises(rates.CostRateError) as missing_period:
            rates.parse_rate(
                {
                    "model_id": "future-model",
                    "input_price": "1",
                    "cached_input_price": "0.1",
                    "output_price": "6",
                }
            )
        self.assertEqual(missing_period.exception.field, "effective_from")

        default_rate = rates.parse_rate(
            {
                "model_id": "future-model",
                "effective_from": None,
                "is_default": True,
                "input_price": "1",
                "cached_input_price": "0.1",
                "output_price": "6",
            }
        )
        self.assertTrue(default_rate.is_default)
        self.assertIsNone(default_rate.effective_from)

    def test_build_main_reports_invalid_cost_rate_config_as_json(self) -> None:
        build = load_module("build_analytics_cost_rate_error_test", ROOT / "scripts" / "build_analytics.py")
        args = argparse.Namespace()
        error = build.cost_rates.CostRateError("cost_rates_config_invalid", "Invalid cost rates config")
        output = io.StringIO()
        with (
            mock.patch.object(build, "parse_args", return_value=args),
            mock.patch.object(build, "configure_paths", side_effect=error),
            mock.patch.object(build.sys, "stdout", output),
        ):
            exit_code = build.main()

        self.assertEqual(exit_code, 2)
        self.assertEqual(json.loads(output.getvalue()), error.payload())


