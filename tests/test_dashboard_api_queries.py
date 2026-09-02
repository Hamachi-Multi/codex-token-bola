from __future__ import annotations

import unittest

try:
    from tests.test_dashboard_freshness import DashboardFreshnessTests
    from tests.test_dashboard_http_policy import DashboardHttpPolicyTests
    from tests.test_dashboard_payload_queries import DashboardPayloadQueryTests
except ModuleNotFoundError:
    from test_dashboard_freshness import DashboardFreshnessTests
    from test_dashboard_http_policy import DashboardHttpPolicyTests
    from test_dashboard_payload_queries import DashboardPayloadQueryTests


class DashboardApiQueryTests(
    DashboardPayloadQueryTests,
    DashboardHttpPolicyTests,
    DashboardFreshnessTests,
):
    pass


def aggregate_suite(loader: unittest.TestLoader) -> unittest.TestSuite:
    return loader.loadTestsFromTestCase(DashboardApiQueryTests)


def load_tests(
    loader: unittest.TestLoader,
    tests: unittest.TestSuite,
    pattern: str | None,
) -> unittest.TestSuite:
    if pattern is None:
        return aggregate_suite(loader)
    return unittest.TestSuite()


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    raise SystemExit(0 if runner.run(aggregate_suite(unittest.defaultTestLoader)).wasSuccessful() else 1)
