import unittest
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory

from ru_routes.apply import atomic_bytes, validate_candidate, candidate_prefix_text, parse_bird_state, wait_for_routes
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

    def test_candidate_prefix_text_multiple_networks(self):
        with TemporaryDirectory() as directory:
            file = Path(directory) / "routes.conf"
            networks = parse_prefixes("8.8.8.0/24\n1.1.1.0/24\n9.9.9.0/24\n")
            file.write_text(render_bird(networks))
            expected = "".join(f"{network}\n" for network in networks)
            self.assertEqual(candidate_prefix_text(file), expected)
            self.assertEqual(len(candidate_prefix_text(file).splitlines()), 3)

    def test_parse_real_bird_2175_output(self):
        table = "BIRD 2.17.5 ready.\n613 of 613 routes for 613 networks in table ru_restricted_table\n"
        protocol = (
            "BIRD 2.17.5 ready.\n"
            "ru_to_mikrotik BGP --- up Established\n"
            "  BGP state:          Established\n"
            "    Routes:         0 imported, 613 exported, 0 preferred\n"
        )
        self.assertEqual(parse_bird_state(table, protocol), (613, 613))

    def test_parse_bird_missing_export_diagnostic(self):
        with self.assertRaisesRegex(RuntimeError, "BGP exported route count"):
            parse_bird_state(
                "613 of 613 routes for 613 networks in table ru_restricted_table",
                "BGP state: Established",
            )

    def test_atomic_write(self):
        with TemporaryDirectory() as directory:
            file = Path(directory) / "routes.conf"
            atomic_bytes(file, b"one")
            atomic_bytes(file, b"two")
            self.assertEqual(file.read_bytes(), b"two")


if __name__ == "__main__":
    unittest.main()
