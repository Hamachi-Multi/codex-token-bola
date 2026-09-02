from __future__ import annotations

import unittest

try:
    from tests.test_cost_rate_analytics import CostRateAnalyticsTests
    from tests.test_hook_capture import HookCaptureTests
    from tests.test_incremental_pipeline import IncrementalPipelineTests
    from tests.test_raw_segment_runtime import RawSegmentRuntimeTests
    from tests.test_reconcile_recovery import ReconcileRecoveryTests
    from tests.test_rollup_attribution import RollupAttributionTests
except ModuleNotFoundError:
    from test_cost_rate_analytics import CostRateAnalyticsTests
    from test_hook_capture import HookCaptureTests
    from test_incremental_pipeline import IncrementalPipelineTests
    from test_raw_segment_runtime import RawSegmentRuntimeTests
    from test_reconcile_recovery import ReconcileRecoveryTests
    from test_rollup_attribution import RollupAttributionTests


class ToolTimingTests(
    CostRateAnalyticsTests,
    RawSegmentRuntimeTests,
    HookCaptureTests,
    IncrementalPipelineTests,
    RollupAttributionTests,
    ReconcileRecoveryTests,
):
    pass


def aggregate_suite(loader: unittest.TestLoader) -> unittest.TestSuite:
    return loader.loadTestsFromTestCase(ToolTimingTests)


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
