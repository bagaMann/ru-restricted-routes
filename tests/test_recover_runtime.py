import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ru_routes.agent.recover_runtime import reconcile_runtime

RID = 'a' * 64
TXID = 'tx-' + 'b' * 64
BASE = 'c' * 64
TARGET = 'd' * 64


class RecoverRuntimeTests(unittest.TestCase):
    def invoke(self, journal_state, runtime_pid=7427, recorded_pid=7427,
               *, audit_error=None, different_digest=False):
        entries = [] if journal_state == 'no_transitions' else [{
            'txid': TXID, 'role': 'backup', 'release_id': RID,
            'baseline_sha256': BASE,
            'target_sha256': ('f' * 64 if different_digest else TARGET),
            'pid': recorded_pid, 'state': journal_state}]
        history = {'entries': entries, 'status': journal_state}
        running = {'managed_pid': runtime_pid, 'routers': {
            'demo_all': {'prefix_count': 0}}}
        with patch('ru_routes.agent.recover_runtime.verify_bundle',
                   return_value={'baseline_sha256': BASE,
                                 'target_sha256': TARGET}), \
             patch('ru_routes.agent.recover_runtime.history',
                   return_value=history), \
             patch('ru_routes.agent.recover_runtime.audit',
                   side_effect=audit_error if audit_error else None,
                   return_value=running) as audit:
            result = reconcile_runtime('/tmp/storage', TXID, 'backup', RID,
                                       '/tmp/intent', '/tmp/runtime',
                                       '/tmp/transitions')
        return result, audit

    def test_same_process_complete(self):
        result, audit = self.invoke('baseline_restored')
        self.assertEqual(result['status'], 'baseline_verified_same_managed_bird_process')
        self.assertTrue(result['reconciled'])
        audit.assert_called_once()

    def test_restarted_process_complete(self):
        result, _ = self.invoke('baseline_restored', runtime_pid=7555)
        self.assertEqual(result['status'], 'baseline_verified_after_managed_bird_restart')
        self.assertEqual(result['current_pid'], 7555)
        self.assertTrue(result['reconciled'])

    def test_crashed_during_apply_never_auto_completes(self):
        result, _ = self.invoke('apply_in_progress', runtime_pid=7555)
        self.assertEqual(result['status'], 'incomplete_journal_runtime_empty_requires_review')
        self.assertFalse(result['reconciled'])

    def test_crashed_during_rollback_never_auto_completes(self):
        result, _ = self.invoke('rollback_in_progress', runtime_pid=7555)
        self.assertEqual(result['status'], 'incomplete_journal_runtime_empty_requires_review')
        self.assertFalse(result['reconciled'])

    def test_nonempty_or_unavailable_runtime_rejected(self):
        result, _ = self.invoke('baseline_restored', audit_error=ValueError('nonempty table'))
        self.assertEqual(result['status'], 'runtime_not_verified_manual_review')
        self.assertFalse(result['reconciled'])

    def test_different_digest_rejected(self):
        with self.assertRaisesRegex(ValueError, 'differ'):
            self.invoke('baseline_restored', different_digest=True)

    def test_no_history_requires_manual_review(self):
        result, audit = self.invoke('no_transitions')
        self.assertEqual(result['status'], 'no_transition_history_requires_review')
        self.assertFalse(result['reconciled'])
        audit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
