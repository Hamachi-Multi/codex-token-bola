from __future__ import annotations

import unittest

try:
    from tests.test_cli_doctor import CliDoctorTests
    from tests.test_cli_hook import CliHookTests
    from tests.test_cli_paths import CliPathsTests
    from tests.test_cli_surface import CliSurfaceTests
except ModuleNotFoundError:
    from test_cli_doctor import CliDoctorTests
    from test_cli_hook import CliHookTests
    from test_cli_paths import CliPathsTests
    from test_cli_surface import CliSurfaceTests


class CliContractTests(
    CliSurfaceTests,
    CliPathsTests,
    CliHookTests,
    CliDoctorTests,
):
    pass


def aggregate_suite(loader: unittest.TestLoader) -> unittest.TestSuite:
    return loader.loadTestsFromTestCase(CliContractTests)


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
