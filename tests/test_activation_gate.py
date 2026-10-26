import unittest
from unittest.mock import patch

from ru_routes.manager.activation_gate import inspect_gate

RID = 'a' * 64
TX = 'tx-' + 'b' * 64


def fixture():
    preflight = {'status': 'both_remote_preflights_verified_not_active',
                 'txid': TX, 'release_id': RID,
                 'agents': {r: {'role': r, 'release_id': RID} for r in ('backup', 'primary')}}
    intents = {'status': 'all_three_intents_match_not_active',
               'txid': TX, 'release_id': RID,
               'agents': {r: {'role': r, 'txid': TX, 'release_id': RID}
                          for r in ('backup', 'primary')}}
    transitions = {'status': 'both_transition_journals_restored_not_live_attested',
                   'txid': TX, 'release_id': RID, 'activation_authorized': False,
                   'reports': {r: {'role': r, 'txid': TX, 'release_id': RID,
                                   'status': 'baseline_restored'}
                               for r in ('backup', 'primary')}}
    runtime = {'status': 'both_recent_live_baselines_verified_not_authorized',
               'txid': TX, 'release_id': RID, 'live_runtime_checked': True,
               'activation_authorized': False,
               'reports': {r: {'status': 'live_baseline_verified',
                               'journal_state': 'baseline_restored'}
                           for r in ('backup', 'primary')}}
    return preflight, intents, transitions, runtime


class ActivationGateTests(unittest.TestCase):
    def run_gate(self, responses):
        names = ['check_agents', 'reconcile', 'check_transitions', 'check_runtime']
        patches = [patch('ru_routes.manager.activation_gate.' + name,
                         return_value=value) for name, value in zip(names, responses)]
        with patches[0], patches[1], patches[2], patches[3]:
            return inspect_gate('/tmp/release', '/tmp/journal',
                                '10.110.11.201', '10.110.27.201',
                                known_hosts='/tmp/known', identity_file='/tmp/key')

    def test_gate_success_is_never_authorization(self):
        result = self.run_gate(fixture())
        self.assertEqual(result['status'],
                         'static_only_activation_gate_passed_no_write_authority')
        self.assertFalse(result['activation_authorized'])
        self.assertEqual(result['proposed_order'], ['backup', 'primary'])

    def test_reject_wrong_remote_tx(self):
        data = fixture()
        data[2]['reports']['backup']['txid'] = 'tx-' + 'f' * 64
        with self.assertRaisesRegex(ValueError, 'backup: remote transaction'):
            self.run_gate(data)

    def test_reject_runtime_not_verified(self):
        data = fixture()
        data[3]['reports']['backup']['status'] = 'live_not_verified'
        with self.assertRaisesRegex(ValueError, 'backup: remote attestations'):
            self.run_gate(data)

    def test_reject_mismatched_release(self):
        data = fixture()
        data[1]['release_id'] = 'f' * 64
        with self.assertRaisesRegex(ValueError, 'cross-check'):
            self.run_gate(data)

    def test_reject_missing_live_runtime_confirmation(self):
        data = fixture()
        data[3]['live_runtime_checked'] = False
        with self.assertRaisesRegex(ValueError, 'runtime attestation'):
            self.run_gate(data)

    def test_reject_bad_transition_state(self):
        data = fixture()
        data[2]['status'] = 'incomplete'
        with self.assertRaisesRegex(ValueError, 'transitions: failed gate'):
            self.run_gate(data)


if __name__ == '__main__':
    unittest.main()
