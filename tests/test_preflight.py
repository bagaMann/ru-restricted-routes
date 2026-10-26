import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ru_routes.agent.preflight import preflight


RID = 'a' * 64


class PreflightTests(unittest.TestCase):
    def fixture(self):
        return {'release_id': RID, 'role': 'primary',
                'status': 'prepared_verified_not_active',
                'config_sha256': 'b' * 64,
                'routers': {'demo': {'prefix_count': 1, 'prefix_sha256': 'c' * 64}}}

    def live(self):
        return {'status': 'isolated_live_routes_verified',
                'release_id': RID, 'role': 'primary',
                'routers': {'demo': {'prefix_count': 1, 'prefix_sha256': 'c' * 64}}}

    def test_durable_receipt_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / 'receipts'
            with patch('ru_routes.agent.preflight.inspect', return_value=self.fixture()), \
                 patch('ru_routes.agent.preflight.run_isolated', return_value=self.live()) as run:
                result = preflight('/tmp/inbox', RID, 'primary', state)
                self.assertEqual(result['status'], 'preflight_verified_not_activated')
                self.assertEqual(json.loads((state / f'{RID}.primary.preflight.json').read_text()), result)
                with self.assertRaises(FileExistsError):
                    preflight('/tmp/inbox', RID, 'primary', state)
                run.assert_called_once()

    def test_mismatched_route_digest_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            wrong = self.live()
            wrong['routers']['demo']['prefix_sha256'] = '0' * 64
            with patch('ru_routes.agent.preflight.inspect', return_value=self.fixture()), \
                 patch('ru_routes.agent.preflight.run_isolated', return_value=wrong):
                with self.assertRaisesRegex(ValueError, 'differs'):
                    preflight('/tmp/inbox', RID, 'primary', Path(d) / 'receipts')
            self.assertEqual(list(Path(d).rglob('*.json')), [])

    def test_mismatched_release_and_role_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            wrong = self.live()
            wrong['release_id'] = 'f' * 64
            with patch('ru_routes.agent.preflight.inspect', return_value=self.fixture()), \
                 patch('ru_routes.agent.preflight.run_isolated', return_value=wrong):
                with self.assertRaisesRegex(ValueError, 'failed'):
                    preflight('/tmp/inbox', RID, 'primary', Path(d) / 'receipts')

    def test_mutation_during_preflight_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            modified = self.fixture()
            modified['config_sha256'] = 'e' * 64
            with patch('ru_routes.agent.preflight.inspect',
                       side_effect=[self.fixture(), modified]), \
                 patch('ru_routes.agent.preflight.run_isolated', return_value=self.live()):
                with self.assertRaisesRegex(ValueError, 'changed'):
                    preflight('/tmp/inbox', RID, 'primary', Path(d) / 'receipts')
            self.assertEqual(list(Path(d).rglob('*.json')), [])

    def test_forbid_live_bird_path(self):
        with self.assertRaisesRegex(ValueError, 'unsafe'):
            preflight('/tmp/inbox', RID, 'primary', '/etc/bird/receipts')

    def test_reject_bad_live_status(self):
        with tempfile.TemporaryDirectory() as d:
            wrong = self.live()
            wrong['status'] = 'prepared_not_activated'
            with patch('ru_routes.agent.preflight.inspect', return_value=self.fixture()), \
                 patch('ru_routes.agent.preflight.run_isolated', return_value=wrong):
                with self.assertRaisesRegex(ValueError, 'failed'):
                    preflight('/tmp/inbox', RID, 'primary', Path(d) / 'receipts')


if __name__ == '__main__':
    unittest.main()
