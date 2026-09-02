from __future__ import annotations

try:
    from tests.support import ROOT, load_module, mock, pathlib, stat, tempfile, unittest
except ModuleNotFoundError:
    from support import ROOT, load_module, mock, pathlib, stat, tempfile, unittest


atomic_io = load_module("atomic_io_test", ROOT / "scripts" / "atomic_io.py")


class AtomicIOTests(unittest.TestCase):
    def test_write_replaces_owner_only_file_and_syncs_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "state.json"
            with mock.patch.object(atomic_io, "fsync_directory") as fsync_directory:
                atomic_io.write_text_owner_only(path, "payload\n")

            self.assertEqual(path.read_text(encoding="utf-8"), "payload\n")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            fsync_directory.assert_called_once_with(path.parent)

    def test_write_propagates_parent_sync_failure_after_replace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "state.json"
            with (
                mock.patch.object(atomic_io, "fsync_directory", side_effect=OSError("fsync failed")),
                self.assertRaisesRegex(OSError, "fsync failed"),
            ):
                atomic_io.write_text_owner_only(path, "payload\n")

            self.assertEqual(path.read_text(encoding="utf-8"), "payload\n")

    def test_unlink_syncs_parent_and_missing_file_is_a_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "state.json"
            path.write_text("payload\n", encoding="utf-8")
            with mock.patch.object(atomic_io, "fsync_directory") as fsync_directory:
                self.assertTrue(atomic_io.unlink_durable(path))
                self.assertFalse(atomic_io.unlink_durable(path))

            fsync_directory.assert_called_once_with(path.parent)


if __name__ == "__main__":
    unittest.main()
