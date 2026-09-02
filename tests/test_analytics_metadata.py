from __future__ import annotations

try:
    from tests.support import ROOT, json, load_module, pathlib, sqlite3, tempfile, unittest
except ModuleNotFoundError:
    from support import ROOT, json, load_module, pathlib, sqlite3, tempfile, unittest


class AnalyticsMetadataTests(unittest.TestCase):
    def test_reads_json_values_and_preserves_plain_values(self) -> None:
        reader = load_module("analytics_metadata_values_test", ROOT / "scripts" / "analytics_metadata.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = pathlib.Path(tmp_dir) / "analytics.sqlite"
            connection = sqlite3.connect(path)
            connection.execute("create table run_metadata (key text primary key, value text)")
            connection.executemany(
                "insert into run_metadata values (?, ?)",
                [("count", json.dumps(7)), ("plain", "not-json")],
            )
            connection.commit()
            connection.close()

            self.assertEqual(reader.read_run_metadata(path), {"count": 7, "plain": "not-json"})

    def test_missing_unreadable_and_invalid_databases_are_empty(self) -> None:
        reader = load_module("analytics_metadata_invalid_test", ROOT / "scripts" / "analytics_metadata.py")
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            missing = root / "missing.sqlite"
            directory = root / "directory.sqlite"
            directory.mkdir()
            invalid = root / "invalid.sqlite"
            invalid.write_text("not sqlite", encoding="utf-8")
            without_metadata = root / "without-metadata.sqlite"
            sqlite3.connect(without_metadata).close()

            for path in (missing, directory, invalid, without_metadata):
                with self.subTest(path=path.name):
                    self.assertEqual(reader.read_run_metadata(path), {})


if __name__ == "__main__":
    unittest.main()
