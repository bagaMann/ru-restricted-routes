"""Journal durability and fail-closed tests; no real BIRD access."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ru_routes import transaction


class TransactionTests(unittest.TestCase):
    def test_interrupted_apply_blocks_next_apply(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "transaction.json"
            transaction.record(path, "applying", "/tmp/routes", b"old", b"new", "/tmp/backup")
            with self.assertRaisesRegex(RuntimeError, "manual inspection"):
                transaction.require_clean(path)
            self.assertEqual(transaction.load(path)["old_sha256"], transaction.digest(b"old"))

    def test_clear_requires_explicit_call(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "transaction.json"
            transaction.record(path, "verified", "/tmp/routes", b"old", b"new", "/tmp/backup")
            with self.assertRaises(RuntimeError):
                transaction.require_clean(path)
            transaction.clear(path)
            transaction.require_clean(path)

    def test_reject_invalid_state(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "transaction.json"
            path.write_text('{"state": "unknown"}')
            with self.assertRaises(ValueError):
                transaction.require_clean(path)


if __name__ == "__main__":
    unittest.main()
