"""Recovery tests use temporary files only."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ru_routes import transaction
from ru_routes.recover import inspect, restore_file


class RecoverTests(unittest.TestCase):
    def prepare(self, root):
        root = Path(root)
        backup, target, journal = (root / "backup", root / "routes", root / "transaction.json")
        backup.write_bytes(b"original")
        target.write_bytes(b"replacement")
        transaction.record(journal, "applying", target, b"original", b"replacement", backup)
        return backup, target, journal

    def test_inspect_only_does_not_restore(self):
        with TemporaryDirectory() as root:
            _, target, journal = self.prepare(root)
            self.assertIn("Recovery available", restore_file(journal))
            self.assertEqual(target.read_bytes(), b"replacement")

    def test_explicit_restore_keeps_journal(self):
        with TemporaryDirectory() as root:
            _, target, journal = self.prepare(root)
            self.assertIn("journal retained", restore_file(journal, confirm=True))
            self.assertEqual(target.read_bytes(), b"original")
            self.assertEqual(transaction.load(journal)["state"], "recovery_required")
            with self.assertRaises(RuntimeError):
                transaction.require_clean(journal)

    def test_corrupt_backup_rejected(self):
        with TemporaryDirectory() as root:
            backup, target, journal = self.prepare(root)
            backup.write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                restore_file(journal, confirm=True)
            self.assertEqual(target.read_bytes(), b"replacement")

    def test_unknown_target_rejected(self):
        with TemporaryDirectory() as root:
            _, target, journal = self.prepare(root)
            target.write_bytes(b"unknown")
            with self.assertRaisesRegex(ValueError, "differs"):
                restore_file(journal, confirm=True)


if __name__ == "__main__":
    unittest.main()
