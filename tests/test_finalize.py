"""Finalization tests use temporary files and mocked BIRD readers."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from ru_routes import transaction
from ru_routes.finalize import finalize
from ru_routes.generate import parse_prefixes, render_bird


class FinalizeTests(unittest.TestCase):
    def prepare(self, root):
        root = Path(root)
        target, backup, journal = root / "routes.conf", root / "backup.conf", root / "transaction.json"
        original = render_bird(parse_prefixes("1.1.1.0/24\n8.8.8.0/24\n"))
        updated = render_bird(parse_prefixes("1.1.1.0/24\n8.8.8.0/24\n9.9.9.0/24\n"))
        target.write_text(original)
        backup.write_text(original)
        transaction.record(journal, "recovery_required", target, original.encode(), updated.encode(), backup)
        return target, backup, journal

    def test_dry_run_retains_journal(self):
        with TemporaryDirectory() as root:
            _, _, journal = self.prepare(root)
            result = finalize(journal, verifier=lambda text: 2, state_reader=lambda: (2, 2))
            self.assertIn("use --confirm", result)
            self.assertTrue(journal.exists())

    def test_explicit_finalize_removes_journal(self):
        with TemporaryDirectory() as root:
            _, _, journal = self.prepare(root)
            result = finalize(journal, confirm=True, verifier=lambda text: 2, state_reader=lambda: (2, 2))
            self.assertIn("closed", result)
            self.assertFalse(journal.exists())

    def test_reject_mismatched_export(self):
        with TemporaryDirectory() as root:
            _, _, journal = self.prepare(root)
            verifier = Mock(return_value=2)
            with self.assertRaisesRegex(ValueError, "counts"):
                finalize(journal, confirm=True, verifier=verifier, state_reader=lambda: (2, 1))
            self.assertTrue(journal.exists())
            verifier.assert_not_called()

    def test_reject_changed_file(self):
        with TemporaryDirectory() as root:
            target, _, journal = self.prepare(root)
            target.write_text(render_bird(parse_prefixes("9.9.9.0/24\n")))
            with self.assertRaisesRegex(ValueError, "differs"):
                finalize(journal, confirm=True, verifier=lambda text: 2, state_reader=lambda: (2, 2))
            self.assertTrue(journal.exists())

    def test_reject_wrong_journal_state(self):
        with TemporaryDirectory() as root:
            _, _, journal = self.prepare(root)
            entry = transaction.load(journal)
            import json
            entry["state"] = "applying"
            transaction.durable_write(journal, (json.dumps(entry) + "\n").encode())
            with self.assertRaisesRegex(ValueError, "recovery_required"):
                finalize(journal, confirm=True, verifier=lambda text: 2, state_reader=lambda: (2, 2))
            self.assertTrue(journal.exists())


if __name__ == "__main__":
    unittest.main()
