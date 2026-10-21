import unittest
from ipaddress import ip_network
from pathlib import Path
from tempfile import TemporaryDirectory
from ru_routes.update import atomic_write, check_update


class UpdateTests(unittest.TestCase):
    def test_large_removal_rejected(self):
        old = [ip_network(f"8.8.{i}.0/24") for i in range(10)]
        with self.assertRaises(ValueError):
            check_update(old, old[:3])

    def test_small_removal_allowed(self):
        old = [ip_network(f"8.8.{i}.0/24") for i in range(10)]
        check_update(old, old[:9])

    def test_empty_rejected(self):
        with self.assertRaises(ValueError):
            check_update([], [])

    def test_atomic_write(self):
        with TemporaryDirectory() as folder:
            target = Path(folder) / "state" / "candidate.txt"
            atomic_write(target, "hello\n")
            self.assertEqual(target.read_text(), "hello\n")


if __name__ == "__main__":
    unittest.main()
