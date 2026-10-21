import unittest
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory

from ru_routes.apply import atomic_bytes, validate_candidate, wait_for_routes
from ru_routes.generate import parse_prefixes, render_bird


class ApplyTests(unittest.TestCase):
    def test_valid_candidate(self):
        with TemporaryDirectory() as directory:
            file = Path(directory) / "routes.conf"
            file.write_text(render_bird(parse_prefixes("8.8.8.0/24")))
            self.assertEqual(validate_candidate(file), 1)

    def test_reject_injected_bird_commands(self):
        with TemporaryDirectory() as directory:
            file = Path(directory) / "routes.conf"
            file.write_text(render_bird(parse_prefixes("8.8.8.0/24")) + "protocol kernel evil {}\n")
            with self.assertRaises(ValueError):
                validate_candidate(file)

    def test_wait_for_convergence(self):
        with patch("ru_routes.apply.read_bird_state", side_effect=[(2, 1), (3, 3)]):
            with patch("ru_routes.apply.time.sleep"):
                wait_for_routes(3, timeout=5)

    def test_reject_incorrect_export_count(self):
        with patch("ru_routes.apply.read_bird_state", return_value=(3, 2)):
            with patch("ru_routes.apply.time.monotonic", side_effect=[0, 1]):
                with self.assertRaises(RuntimeError):
                    wait_for_routes(3, timeout=0)

    def test_atomic_write(self):
        with TemporaryDirectory() as directory:
            file = Path(directory) / "routes.conf"
            atomic_bytes(file, b"one")
            atomic_bytes(file, b"two")
            self.assertEqual(file.read_bytes(), b"two")


if __name__ == "__main__":
    unittest.main()
