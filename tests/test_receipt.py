import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ru_routes.agent.receipt import inspect_receipt

RID = 'a' * 64


class ReceiptTests(unittest.TestCase):
    def fixture(self, root):
        state = root / 'state'
        state.mkdir()
        expected = {
            'config_sha256': 'b' * 64,
            'routers': {'demo': {'prefix_count': 1, 'prefix_sha256': 'c' * 64,
                                 'groups': ['ru']}}
        }
        receipt = {'schema': 1, 'status': 'preflight_verified_not_activated',
                   'release_id': RID, 'role': 'primary',
                   'config_sha256': 'b' * 64,
                   'routers': {'demo': {'prefix_count': 1, 'prefix_sha256': 'c' * 64}},
                   'verified_at_utc': '2026-10-26T09:43:01+00:00'}
        path = state / f'{RID}.primary.preflight.json'
        path.write_text(json.dumps(receipt))
        return state, path, expected, receipt

    def test_valid_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            state, _, expected, _ = self.fixture(Path(d))
            with patch('ru_routes.agent.receipt.inspect', return_value=expected):
                result = inspect_receipt('/tmp/inbox', RID, 'primary', state)
            self.assertEqual(result['status'], 'preflight_receipt_verified_not_active')
            self.assertEqual(result['routers']['demo']['prefix_count'], 1)

    def test_tampered_prefix_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            state, path, expected, receipt = self.fixture(Path(d))
            receipt['routers']['demo']['prefix_sha256'] = 'e' * 64
            path.write_text(json.dumps(receipt))
            with patch('ru_routes.agent.receipt.inspect', return_value=expected):
                with self.assertRaisesRegex(ValueError, 'prefixes mismatch'):
                    inspect_receipt('/tmp/inbox', RID, 'primary', state)

    def test_mismatched_role_and_config_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            state, path, expected, receipt = self.fixture(Path(d))
            receipt['config_sha256'] = 'e' * 64
            path.write_text(json.dumps(receipt))
            with patch('ru_routes.agent.receipt.inspect', return_value=expected):
                with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                    inspect_receipt('/tmp/inbox', RID, 'primary', state)

    def test_symlink_and_missing_receipt_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            state, path, expected, _ = self.fixture(root)
            with patch('ru_routes.agent.receipt.inspect', return_value=expected):
                path.unlink()
                with self.assertRaisesRegex(ValueError, 'missing'):
                    inspect_receipt('/tmp/inbox', RID, 'primary', state)
                other = root / 'other.json'
                other.write_text('{}')
                path.symlink_to(other)
                with self.assertRaisesRegex(ValueError, 'symlinked'):
                    inspect_receipt('/tmp/inbox', RID, 'primary', state)

    def test_invalid_timestamp_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            state, path, expected, receipt = self.fixture(Path(d))
            receipt['verified_at_utc'] = '2026-10-26T09:43:01'
            path.write_text(json.dumps(receipt))
            with patch('ru_routes.agent.receipt.inspect', return_value=expected):
                with self.assertRaisesRegex(ValueError, 'UTC'):
                    inspect_receipt('/tmp/inbox', RID, 'primary', state)


if __name__ == '__main__':
    unittest.main()
