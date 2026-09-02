from __future__ import annotations

try:
    from tests.support import DashboardFixtureMixin, ROOT, io, load_module, mock, pathlib, tempfile, types, unittest
except ModuleNotFoundError:
    from support import DashboardFixtureMixin, ROOT, io, load_module, mock, pathlib, tempfile, types, unittest


class DashboardHttpPolicyTests(DashboardFixtureMixin, unittest.TestCase):
    def secure_post_handler(self, serve, body: bytes = b"{}"):
        handler = serve.Handler.__new__(serve.Handler)
        handler.server = types.SimpleNamespace(
            allowed_authority="127.0.0.1:8766",
            allowed_origin="http://127.0.0.1:8766",
        )
        handler.path = "/api/rebuild"
        handler.headers = {
            "Host": "127.0.0.1:8766",
            "Origin": "http://127.0.0.1:8766",
            "Sec-Fetch-Site": "same-origin",
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
        }
        handler.rfile = io.BytesIO(body)
        return handler

    def test_server_rejects_non_loopback_host(self) -> None:
        serve = load_module("serve_dashboard_network_guard_test", ROOT / "scripts" / "serve_dashboard.py")

        def fail_server(*_args, **_kwargs):
            raise AssertionError("server must not bind before network policy check")

        with (
            mock.patch.object(serve.sys, "argv", ["serve_dashboard.py", "--host", "0.0.0.0"]),
            mock.patch.object(serve, "ThreadingHTTPServer", side_effect=fail_server),
        ):
            result = serve.main()

        self.assertEqual(result, 2)

    def test_server_rejects_ipv6_loopback_before_bind(self) -> None:
        serve = load_module("serve_dashboard_ipv6_guard_test", ROOT / "scripts" / "serve_dashboard.py")

        def fail_server(*_args, **_kwargs):
            raise AssertionError("server must not bind before IPv4 loopback policy check")

        stderr = io.StringIO()
        with (
            mock.patch.object(serve.sys, "argv", ["serve_dashboard.py", "--host", "::1"]),
            mock.patch.object(serve, "ThreadingHTTPServer", side_effect=fail_server),
            mock.patch.object(serve.sys, "stderr", stderr),
        ):
            result = serve.main()

        self.assertEqual(result, 2)
        self.assertIn("use localhost or an IPv4 loopback address", stderr.getvalue())

    def test_server_accepts_only_supported_ipv4_loopback_hosts(self) -> None:
        serve = load_module("serve_dashboard_loopback_policy_test", ROOT / "scripts" / "serve_dashboard.py")

        self.assertTrue(serve.is_loopback_host("127.0.0.1"))
        self.assertTrue(serve.is_loopback_host("127.1.2.3"))
        self.assertTrue(serve.is_loopback_host("localhost"))
        self.assertFalse(serve.is_loopback_host("::1"))
        self.assertFalse(serve.is_loopback_host("0.0.0.0"))

    def test_server_rejects_removed_allow_network_option(self) -> None:
        serve = load_module("serve_dashboard_network_option_test", ROOT / "scripts" / "serve_dashboard.py")
        with (
            mock.patch.object(serve.sys, "argv", ["serve_dashboard.py", "--host", "0.0.0.0", "--allow-network"]),
            self.assertRaises(SystemExit),
        ):
            serve.main()

    def test_server_ignores_removed_analytics_db_environment_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            codex_dir = pathlib.Path(tmp_dir) / ".codex"
            output_dir = pathlib.Path(tmp_dir) / "output"
            external_db = pathlib.Path(tmp_dir) / "outside.sqlite"
            with mock.patch.dict(
                "os.environ",
                {
                    "CODEX_HOME": str(codex_dir),
                    "BOLA_OUTPUT_DIR": str(output_dir),
                    "BOLA_ANALYTICS_DB": str(external_db),
                },
                clear=False,
            ):
                serve = load_module("serve_dashboard_external_db_guard_test", ROOT / "scripts" / "serve_dashboard.py")

        self.assertEqual(serve.DB_PATH, output_dir / "analytics" / "bola.sqlite")

    def test_terminate_rebuild_process_kills_after_grace_timeout(self) -> None:
        serve = load_module("serve_dashboard_cancel_kill_test", ROOT / "scripts" / "serve_dashboard.py")
        calls: list[str] = []

        class StubbornProcess:
            def poll(self):
                return None

            def terminate(self):
                calls.append("terminate")

            def wait(self, timeout=None):
                calls.append(f"wait:{timeout}")
                raise serve.dashboard_rebuild_api.subprocess.TimeoutExpired("cmd", timeout)

            def kill(self):
                calls.append("kill")

        result = serve.terminate_rebuild_process(StubbornProcess(), grace_seconds=0.01)

        self.assertEqual(result, "killed")
        self.assertEqual(calls, ["terminate", "wait:0.01", "kill"])

    def test_late_cancel_request_does_not_override_successful_rebuild(self) -> None:
        serve = load_module("serve_dashboard_late_cancel_result_test", ROOT / "scripts" / "serve_dashboard.py")
        success = types.SimpleNamespace(exit_code=0, payload={"status": "healthy"})
        acknowledged = types.SimpleNamespace(exit_code=serve.cancel_control.CANCEL_EXIT_CODE, payload={})

        self.assertFalse(serve.dashboard_rebuild_api.rebuild_was_cancelled(success, cancel_enforced=False))
        self.assertTrue(serve.dashboard_rebuild_api.rebuild_was_cancelled(success, cancel_enforced=True))
        self.assertTrue(serve.dashboard_rebuild_api.rebuild_was_cancelled(acknowledged, cancel_enforced=False))

    def test_post_api_errors_are_returned_as_json(self) -> None:
        serve = load_module("serve_dashboard_post_error_json_test", ROOT / "scripts" / "serve_dashboard.py")
        handler = self.secure_post_handler(serve)
        sent: list[tuple[dict[str, object], int]] = []
        handler.handle_rebuild = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        handler.send_json = lambda payload, status=200: sent.append((payload, status))

        handler.do_POST()

        self.assertEqual(sent, [({"error": "internal_error"}, 500)])

    def test_post_api_rejects_malformed_json_body_before_mutation(self) -> None:
        serve = load_module("serve_dashboard_invalid_json_body_test", ROOT / "scripts" / "serve_dashboard.py")
        handler = self.secure_post_handler(serve, b"{bad\n")
        sent: list[tuple[dict[str, object], int]] = []
        handler.handle_rebuild = lambda: (_ for _ in ()).throw(AssertionError("mutation must not start"))
        handler.send_json = lambda payload, status=200: sent.append((payload, status))

        handler.do_POST()

        self.assertEqual(sent, [({"error": "invalid_json"}, 400)])

    def test_post_security_headers_block_before_mutation(self) -> None:
        serve = load_module("serve_dashboard_post_security_test", ROOT / "scripts" / "serve_dashboard.py")
        cases = (
            ("Host", "attacker.example", "request_host_forbidden", 403),
            ("Origin", "https://attacker.example", "request_origin_forbidden", 403),
            ("Sec-Fetch-Site", "cross-site", "cross_site_request_forbidden", 403),
            ("Content-Type", "text/plain", "application_json_required", 415),
        )

        for header, value, error, status in cases:
            with self.subTest(header=header):
                handler = self.secure_post_handler(serve, b'{"confirm_all_logs":true}')
                sent: list[tuple[dict[str, object], int]] = []
                mutations: list[bool] = []
                handler.headers[header] = value
                handler.handle_rebuild = lambda: mutations.append(True)
                handler.send_json = lambda payload, response_status=200: sent.append((payload, response_status))

                handler.do_POST()

                self.assertEqual(mutations, [])
                self.assertEqual(sent, [({"error": error}, status)])

    def test_post_requires_origin_even_without_sec_fetch_site(self) -> None:
        serve = load_module("serve_dashboard_post_origin_required_test", ROOT / "scripts" / "serve_dashboard.py")
        handler = self.secure_post_handler(serve)
        sent: list[tuple[dict[str, object], int]] = []
        mutations: list[bool] = []
        del handler.headers["Origin"]
        del handler.headers["Sec-Fetch-Site"]
        handler.handle_rebuild = lambda: mutations.append(True)
        handler.send_json = lambda payload, status=200: sent.append((payload, status))

        handler.do_POST()

        self.assertEqual(mutations, [])
        self.assertEqual(sent, [({"error": "request_origin_forbidden"}, 403)])

    def test_post_accepts_json_charset_and_missing_sec_fetch_site(self) -> None:
        serve = load_module("serve_dashboard_post_json_charset_test", ROOT / "scripts" / "serve_dashboard.py")
        handler = self.secure_post_handler(serve)
        sent: list[tuple[dict[str, object], int]] = []
        mutations: list[bool] = []
        del handler.headers["Sec-Fetch-Site"]
        handler.headers["Content-Type"] = "application/json; charset=UTF-8"
        handler.handle_rebuild = lambda: mutations.append(True)
        handler.send_json = lambda payload, status=200: sent.append((payload, status))

        handler.do_POST()

        self.assertEqual(mutations, [True])
        self.assertEqual(sent, [])

    def test_post_rejects_oversized_body_before_read_or_mutation(self) -> None:
        serve = load_module("serve_dashboard_post_body_limit_test", ROOT / "scripts" / "serve_dashboard.py")
        handler = self.secure_post_handler(serve)
        sent: list[tuple[dict[str, object], int]] = []
        mutations: list[bool] = []
        handler.headers["Content-Length"] = str(serve.MAX_JSON_BODY_BYTES + 1)
        handler.rfile = None
        handler.handle_rebuild = lambda: mutations.append(True)
        handler.send_json = lambda payload, status=200: sent.append((payload, status))

        handler.do_POST()

        self.assertEqual(mutations, [])
        self.assertEqual(sent, [({"error": "request_body_too_large"}, 413)])

    def test_post_rejects_missing_and_invalid_content_length_before_mutation(self) -> None:
        serve = load_module("serve_dashboard_post_content_length_test", ROOT / "scripts" / "serve_dashboard.py")
        cases = (
            (None, "content_length_required", 411),
            ("not-a-number", "invalid_content_length", 400),
            ("-1", "invalid_content_length", 400),
        )

        for length, error, status in cases:
            with self.subTest(length=length):
                handler = self.secure_post_handler(serve)
                sent: list[tuple[dict[str, object], int]] = []
                mutations: list[bool] = []
                if length is None:
                    del handler.headers["Content-Length"]
                else:
                    handler.headers["Content-Length"] = length
                handler.handle_rebuild = lambda: mutations.append(True)
                handler.send_json = lambda payload, response_status=200: sent.append((payload, response_status))

                handler.do_POST()

                self.assertEqual(mutations, [])
                self.assertEqual(sent, [({"error": error}, status)])

    def test_dashboard_post_helper_always_sends_json_object(self) -> None:
        source = (ROOT / "scripts" / "assets" / "dashboard" / "api.js").read_text(encoding="utf-8")
        self.assertIn("headers: { 'Content-Type': 'application/json' }", source)
        self.assertIn("body: JSON.stringify(body === null ? {} : body)", source)

    def test_root_dashboard_html_is_not_cached(self) -> None:
        serve = load_module("serve_dashboard_root_cache_test", ROOT / "scripts" / "serve_dashboard.py")
        handler = serve.Handler.__new__(serve.Handler)
        headers: list[tuple[str, str]] = []
        handler.server = types.SimpleNamespace(allowed_authority="127.0.0.1:8766")
        handler.headers = {"Host": "127.0.0.1:8766"}
        handler.path = "/"
        handler.send_response = lambda status: None
        handler.send_header = lambda name, value: headers.append((name, value))
        handler.end_headers = lambda: None
        handler.wfile = io.BytesIO()

        handler.do_GET()

        self.assertIn(("Cache-Control", "no-cache"), headers)

    def test_get_rejects_unexpected_host_before_routing(self) -> None:
        serve = load_module("serve_dashboard_get_host_test", ROOT / "scripts" / "serve_dashboard.py")
        handler = serve.Handler.__new__(serve.Handler)
        sent: list[tuple[dict[str, object], int]] = []
        handler.server = types.SimpleNamespace(allowed_authority="127.0.0.1:8766")
        handler.headers = {"Host": "localhost:8766"}
        handler.path = "/api/dashboard"
        handler.handle_api = lambda *_args: (_ for _ in ()).throw(AssertionError("route must not run"))
        handler.send_json = lambda payload, status=200: sent.append((payload, status))

        handler.do_GET()

        self.assertEqual(sent, [({"error": "request_host_forbidden"}, 403)])
    def test_analyze_endpoint_runs_incremental_pipeline(self) -> None:
        serve_source = (ROOT / "scripts" / "serve_dashboard.py").read_text(encoding="utf-8")
        rebuild_source = (ROOT / "scripts" / "dashboard_rebuild_api.py").read_text(encoding="utf-8")
        cleanup_source = (ROOT / "scripts" / "dashboard_cleanup_api.py").read_text(encoding="utf-8")
        state_source = (ROOT / "scripts" / "dashboard_operation_state.py").read_text(encoding="utf-8")
        self.assertIn('"--incremental",', rebuild_source)
        self.assertIn('"--recover",', rebuild_source)
        self.assertIn('if parsed.path == "/api/rebuild/cancel":', serve_source)
        self.assertIn('env["BOLA_PROGRESS_FILE"] = str(progress_file)', rebuild_source)
        self.assertIn('if path == "/api/rebuild/progress":', serve_source)
        self.assertIn("def handle_rebuild_progress(self):", rebuild_source)
        self.assertIn('if path == "/api/log-cleanup/progress":', serve_source)
        self.assertIn("def handle_cleanup_progress(self):", cleanup_source)
        self.assertIn('env[progress_control.PROGRESS_ENV] = str(progress_file)', cleanup_source)
        self.assertIn("class DashboardOperationManager", state_source)
        self.assertIn("operation_id", state_source)
        self.assertIn("ManagedProcess.start", rebuild_source)
        self.assertIn('metadata["analysis_elapsed_ms"] = metadata.pop("elapsed_ms")', rebuild_source)
        self.assertIn("AUTO_COMPACT_MIN_BYTES = 64 * 1024 * 1024", rebuild_source)
        self.assertIn('metadata["pre_analysis_rotate"]', rebuild_source)
        self.assertNotIn("self.run_compact_command(output, AUTO_COMPACT_MIN_BYTES)", rebuild_source)
        self.assertIn("dashboard_cleanup.refresh_retention_index_for_current_sources(self.dashboard_output_dir())", rebuild_source)
        self.assertIn('degraded = result.exit_code == 1 and metadata.get("status") == "degraded"', rebuild_source)
        self.assertIn('"data_health": "degraded" if degraded else "ok"', rebuild_source)
        self.assertIn('"ok": True,', rebuild_source)
        for operation_source in (rebuild_source, cleanup_source, state_source):
            self.assertNotIn("import serve_dashboard", operation_source)
            self.assertNotIn("from serve_dashboard", operation_source)
