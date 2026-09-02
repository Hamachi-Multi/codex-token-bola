from __future__ import annotations

import pathlib
import unittest


def assert_order(testcase: unittest.TestCase, text: str, *needles: str) -> None:
    cursor = -1
    for needle in needles:
        position = text.find(needle)
        testcase.assertNotEqual(position, -1, f"missing fragment: {needle}")
        testcase.assertGreater(position, cursor, f"fragment out of order: {needle}")
        cursor = position


class CliTestCase(unittest.TestCase):
    @staticmethod
    def initialize_codex_dir(path: pathlib.Path) -> pathlib.Path:
        path.mkdir(parents=True, exist_ok=True)
        (path / "config.toml").write_text("\n", encoding="utf-8")
        return path

    @staticmethod
    def valid_codex_cli_status() -> dict[str, object]:
        return {
            "valid": True,
            "path": "/usr/bin/codex",
            "version": "codex-cli 1.0.0",
            "reason": None,
            "message": None,
        }

    @staticmethod
    def valid_hook_runtime_status() -> dict[str, object]:
        return {
            "valid": True,
            "interpreter": "/usr/bin/python3",
            "module": "codex_token_bola.hook",
            "reason": None,
            "message": None,
        }
