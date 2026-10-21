"""Read-only BIRD route identity verification tests."""
import unittest
from unittest.mock import patch
from ru_routes.verify_routes import parse_listing, verify_listings

TABLE = """BIRD 2.17.5 ready.
Table ru_restricted_table:
8.8.8.0/24 blackhole [ru_restricted_static 17:20:05.116] * (200)
1.1.1.0/24 blackhole [ru_restricted_static 17:20:05.116] * (200)
"""
EXPECTED = "1.1.1.0/24\n8.8.8.0/24\n"


class VerifyRouteTests(unittest.TestCase):
    def test_same_prefixes_different_order(self):
        self.assertEqual(verify_listings(EXPECTED, TABLE, TABLE), 2)

    def test_same_count_different_prefix_rejected(self):
        modified = TABLE.replace("8.8.8.0/24", "9.9.9.0/24")
        with self.assertRaisesRegex(ValueError, "prefix mismatch"):
            verify_listings(EXPECTED, modified, TABLE)

    def test_export_mismatch_rejected(self):
        modified = TABLE.replace("8.8.8.0/24", "9.9.9.0/24")
        with self.assertRaisesRegex(ValueError, "export prefix mismatch"):
            verify_listings(EXPECTED, TABLE, modified)

    def test_wrong_protocol_rejected(self):
        with self.assertRaisesRegex(ValueError, "unexpected route attributes"):
            parse_listing(TABLE.replace("ru_restricted_static", "other_static"))

    def test_wrong_type_rejected(self):
        with self.assertRaisesRegex(ValueError, "unexpected route attributes"):
            parse_listing(TABLE.replace("blackhole", "unicast"))

    def test_duplicate_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            parse_listing(TABLE + TABLE.splitlines()[-1] + "\n")

    def test_truncated_output_rejected(self):
        with self.assertRaisesRegex(ValueError, "prefix mismatch"):
            verify_listings(EXPECTED, TABLE, TABLE.rsplit("\n", 2)[0] + "\n")

    def test_missing_header_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing expected table"):
            parse_listing("BIRD 2.17.5 ready.\n")


if __name__ == "__main__":
    unittest.main()
