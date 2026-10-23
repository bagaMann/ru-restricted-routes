import unittest
from ipaddress import ip_network
from pathlib import Path
from tempfile import TemporaryDirectory
from ru_routes.update import atomic_write, check_update, read_sources, fetch_sources
from unittest.mock import patch
from io import BytesIO


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

    def test_sources_file(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "sources.txt"
            path.write_text("# comment\\nhttps://example.org/a\\n\\nhttps://example.org/b\\n")
            self.assertEqual(read_sources(path), ["https://example.org/a", "https://example.org/b"])
            path.write_text("https://example.org/a\\nhttps://example.org/a\\n")
            with self.assertRaises(ValueError):
                read_sources(path)
            path.write_text("http://example.org/a\\n")
            with self.assertRaises(ValueError):
                read_sources(path)

    def test_merge_and_fail_closed(self):
        from ru_routes.generate import parse_prefixes
        payloads = [b"8.8.8.0/24\\n", b"8.8.8.0/24\\n9.9.9.0/24\\n"]
        with patch("ru_routes.update.urlopen") as mock:
            mock.side_effect = [BytesIO(p) for p in payloads]
            feeds = fetch_sources(["https://example.org/a", "https://example.org/b"])
        self.assertEqual(len(parse_prefixes("\\n".join(p.decode() for _, p, _ in feeds))), 2)
        with patch("ru_routes.update.urlopen", side_effect=OSError("source unavailable")):
            with self.assertRaises(OSError):
                fetch_sources(["https://example.org/a"])

    def test_atomic_write(self):
        with TemporaryDirectory() as folder:
            target = Path(folder) / "state" / "candidate.txt"
            atomic_write(target, "hello\n")
            self.assertEqual(target.read_text(), "hello\n")


if __name__ == "__main__":
    unittest.main()
