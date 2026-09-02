from __future__ import annotations

import importlib
import pathlib
import sys
from types import SimpleNamespace
import unittest


SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
try:
    diagnostics_module = importlib.import_module("playwright_dashboard_check")
finally:
    sys.path.remove(str(SCRIPTS))


class FakePage:
    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}

    def on(self, event: str, handler) -> None:
        self.handlers[event] = handler


def response(
    *,
    status: int,
    url: str = "http://127.0.0.1:8766/api/cost-rates?token=secret&days=7",
    payload: object = None,
):
    request = SimpleNamespace(method="POST", url=url, resource_type="fetch")
    return SimpleNamespace(status=status, request=request, json=lambda: payload)


class PlaywrightDiagnosticsTests(unittest.TestCase):
    def test_http_error_keeps_safe_api_context(self) -> None:
        label = diagnostics_module.safe_http_error(
            response(
                status=400,
                payload={"error": "cost_rate_invalid", "message": "Invalid price"},
            )
        )

        self.assertEqual(
            label,
            "POST /api/cost-rates?days,token 400 "
            "error=cost_rate_invalid message=Invalid price",
        )
        self.assertNotIn("secret", label or "")

    def test_http_error_ignores_success_and_non_api_responses(self) -> None:
        self.assertIsNone(diagnostics_module.safe_http_error(response(status=200)))
        self.assertIsNone(
            diagnostics_module.safe_http_error(
                response(
                    status=404,
                    url="http://127.0.0.1:8766/assets/dashboard.js",
                )
            )
        )

    def test_http_error_detail_is_bounded(self) -> None:
        label = diagnostics_module.safe_http_error(
            response(status=500, payload={"error": "x" * 2_000})
        )

        self.assertIsNotNone(label)
        assert label is not None
        detail = label.split(" 500 ", 1)[1]
        self.assertEqual(
            len(detail),
            diagnostics_module.MAX_HTTP_ERROR_DETAIL_CHARS,
        )

    def test_page_diagnostics_keeps_only_recent_http_errors(self) -> None:
        page = FakePage()
        diagnostics = diagnostics_module.attach_page_diagnostics(page)
        handler = page.handlers["response"]
        assert callable(handler)

        for index in range(diagnostics_module.MAX_HTTP_ERRORS + 3):
            handler(
                response(
                    status=400,
                    url=f"http://127.0.0.1:8766/api/example?index={index}",
                    payload={"error": f"error-{index}"},
                )
            )

        self.assertEqual(
            len(diagnostics.http_errors),
            diagnostics_module.MAX_HTTP_ERRORS,
        )
        self.assertIn("error-3", diagnostics.http_errors[0])
        self.assertIn(
            f"error-{diagnostics_module.MAX_HTTP_ERRORS + 2}",
            diagnostics.http_errors[-1],
        )
