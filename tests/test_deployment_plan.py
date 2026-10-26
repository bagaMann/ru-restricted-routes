import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ru_routes.manager.deployment_plan import build_plan

RID = 'a' * 64
TXID = 'tx-' + 'b' * 64
ROUTES = {'demo': {'prefix_count': 617, 'prefix_sha256': 'c' * 64}}


class DeploymentPlanTests(unittest.TestCase):
    def setup_case(self, root):
        release = root / 'release'
        release.mkdir()
        (release / 'release.json').write_text(json.dumps({'servers': {
            'primary': {'routers': ROUTES}, 'backup': {'routers': ROUTES}}}))
        receipts = {'status': 'both_remote_preflights_verified_not_active',
                    'txid': TXID, 'release_id': RID,
                    'agents': {'primary': {'routers': ROUTES},
                               'backup': {'routers': ROUTES}}}
        intents = {'status': 'all_three_intents_match_not_active',
                   'txid': TXID, 'release_id': RID,
                   'agents': {'primary': {'txid': TXID},
                              'backup': {'txid': TXID}}}
        return release, receipts, intents

    def run_case(self, release, receipts, intents, ids=None):
        ids = ids or [RID] * 4
        with patch('ru_routes.manager.deployment_plan.verify_release', side_effect=ids), \
             patch('ru_routes.manager.deployment_plan.check_agents', return_value=receipts), \
             patch('ru_routes.manager.deployment_plan.reconcile', return_value=intents):
            return build_plan(release, '/tmp/journal', 'primary', 'backup',
                              known_hosts='/tmp/known', identity_file='/tmp/key')

    def test_nonactivating_plan(self):
        with tempfile.TemporaryDirectory() as d:
            release, receipts, intents = self.setup_case(Path(d))
            result = self.run_case(release, receipts, intents)
            self.assertEqual(result['proposed_order'], ['backup', 'primary'])
            self.assertFalse(result['activation_performed'])
            self.assertEqual(result['routes']['backup'], ROUTES)

    def test_wrong_transaction_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            release, receipts, intents = self.setup_case(Path(d))
            intents['txid'] = 'tx-' + 'f' * 64
            with self.assertRaisesRegex(ValueError, 'disagree'):
                self.run_case(release, receipts, intents)

    def test_wrong_route_digest_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            release, receipts, intents = self.setup_case(Path(d))
            receipts['agents']['backup']['routers'] = {'demo': {
                'prefix_count': 617, 'prefix_sha256': 'f' * 64}}
            with self.assertRaisesRegex(ValueError, 'backup: receipt routes'):
                self.run_case(release, receipts, intents)

    def test_changed_release_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            release, receipts, intents = self.setup_case(Path(d))
            with self.assertRaisesRegex(ValueError, 'mutated'):
                self.run_case(release, receipts, intents,
                              [RID, RID, 'f' * 64, RID])

    def test_missing_remote_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            release, receipts, intents = self.setup_case(Path(d))
            with patch('ru_routes.manager.deployment_plan.verify_release',
                       return_value=RID), \
                 patch('ru_routes.manager.deployment_plan.check_agents',
                       side_effect=RuntimeError('backup offline')):
                with self.assertRaisesRegex(RuntimeError, 'offline'):
                    build_plan(release, '/tmp/journal', 'primary', 'backup',
                               known_hosts='/tmp/known', identity_file='/tmp/key')


if __name__ == '__main__':
    unittest.main()
