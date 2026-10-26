import json
import unittest
from unittest.mock import patch

from ru_routes.agent.control_precheck import process, validate_request

RID = 'a' * 64
TXID = 'tx-' + 'b' * 64
SHA = 'c' * 64


def request(**kwargs):
    item = {'schema': 1, 'operation': 'prepare_check', 'txid': TXID,
            'release_id': RID, 'role': 'backup'}
    item.update(kwargs)
    return json.dumps(item).encode()


class ControlPrecheckTests(unittest.TestCase):
    def run_process(self, raw=None, *, intent=None, runtime=None):
        prepared = intent or {'status': 'agent_prepared_not_active',
                              'txid': TXID, 'role': 'backup', 'release_id': RID}
        live = runtime or {'reconciled': True, 'baseline_empty_verified': True,
                           'runtime_checked': True, 'journal_state': 'baseline_restored',
                           'current_pid': 7816}
        with patch('ru_routes.agent.control_precheck.intent_status',
                   return_value=prepared), patch(
                   'ru_routes.agent.control_precheck.verify_bundle',
                   return_value={'target_sha256': SHA,
                                 'baseline_sha256': SHA}), patch(
                   'ru_routes.agent.control_precheck.reconcile_runtime',
                   return_value=live):
            return process(raw or request(), storage='/tmp/snapshot',
                           role='backup', release_id=RID,
                           agent_state_dir='/tmp/agent',
                           runtime_dir='/tmp/runtime',
                           transition_dir='/tmp/transitions')

    def test_read_only_check(self):
        result = self.run_process()
        self.assertEqual(result['status'],
                         'local_control_precheck_verified_not_authorized')
        self.assertFalse(result['activation_authorized'])
        self.assertFalse(result['activation_performed'])
        self.assertEqual(result['managed_pid'], 7816)

    def test_reject_unknown_operation(self):
        with self.assertRaisesRegex(ValueError, 'unsupported read-only operation'):
            validate_request(request(operation='apply'))

    def test_reject_arbitrary_path_field(self):
        with self.assertRaisesRegex(ValueError, 'fields'):
            validate_request(request(path='/etc/bird/bird.conf'))

    def test_reject_wrong_agent_role(self):
        with self.assertRaisesRegex(ValueError, 'fixed agent identity'):
            self.run_process(request(role='primary'))

    def test_reject_wrong_prepared_txid(self):
        with self.assertRaisesRegex(ValueError, 'matching prepared intent'):
            self.run_process(intent={'status': 'agent_prepared_not_active',
                                     'txid': 'tx-' + 'f' * 64,
                                     'role': 'backup', 'release_id': RID})

    def test_reject_nonempty_runtime(self):
        with self.assertRaisesRegex(ValueError, 'baseline not verified'):
            self.run_process(runtime={'reconciled': False,
                                      'runtime_checked': True,
                                      'baseline_empty_verified': False})

    def test_reject_oversized_request(self):
        with self.assertRaisesRegex(ValueError, 'oversized'):
            validate_request(b'X' * 1025)

    def test_reject_array_operation_without_type_error(self):
        with self.assertRaisesRegex(ValueError, 'unsupported read-only operation'):
            validate_request(request(operation=['apply']))

    def test_reject_malformed_json(self):
        with self.assertRaisesRegex(ValueError, 'invalid JSON'):
            validate_request(b'{broken')


if __name__ == '__main__':
    unittest.main()
