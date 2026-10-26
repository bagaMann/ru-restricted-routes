import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ru_routes.agent.runtime_report import publish, read_report
from ru_routes.manager.check_runtime import check_pair

RID = 'a' * 64
TX = 'tx-' + 'b' * 64


class RuntimeReportTests(unittest.TestCase):
    def evidence(self, reconciled=True):
        return {'status': 'baseline_verified_after_managed_bird_restart',
                'reconciled': reconciled, 'baseline_empty_verified': True,
                'runtime_checked': True, 'journal_state': 'baseline_restored',
                'txid': TX, 'release_id': RID, 'role': 'backup',
                'current_pid': 7816}

    def test_publish_and_read_same_identity(self):
        with tempfile.TemporaryDirectory() as d:
            with patch('ru_routes.agent.runtime_report.reconcile_runtime',
                       return_value=self.evidence()):
                result = publish('/tmp/storage', TX, 'backup', RID,
                                 '/tmp/agent', '/tmp/runtime',
                                 '/tmp/journal', Path(d) / 'report')
            self.assertEqual(result['status'], 'live_baseline_verified')
            loaded = read_report(Path(d) / 'report', RID, 'backup', txid=TX)
            self.assertEqual(loaded['sha256'], result['sha256'])

    def test_corruption_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'report'
            with patch('ru_routes.agent.runtime_report.reconcile_runtime',
                       return_value=self.evidence()):
                publish('/tmp/storage', TX, 'backup', RID,
                        '/tmp/agent', '/tmp/runtime', '/tmp/journal', path)
            file = path / 'runtime-status.json'
            doc = json.loads(file.read_text())
            doc['status'] = 'live_not_verified'
            file.write_text(json.dumps(doc))
            with self.assertRaisesRegex(ValueError, 'checksum'):
                read_report(path, RID, 'backup')

    def test_negative_runtime_never_positive(self):
        with tempfile.TemporaryDirectory() as d:
            with patch('ru_routes.agent.runtime_report.reconcile_runtime',
                       return_value=self.evidence(False)):
                result = publish('/tmp/storage', TX, 'backup', RID,
                                 '/tmp/agent', '/tmp/runtime', '/tmp/journal',
                                 Path(d) / 'report')
            self.assertEqual(result['status'], 'live_not_verified')

    def test_wrong_role_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'report'
            with patch('ru_routes.agent.runtime_report.reconcile_runtime',
                       return_value=self.evidence()):
                publish('/tmp/storage', TX, 'backup', RID, '/tmp/agent',
                        '/tmp/runtime', '/tmp/journal', path)
            with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                read_report(path, RID, 'primary')

    def test_manager_rejects_wrong_remote_transaction(self):
        pending = {'transactions': [{'state': 'intent_prepared_not_active',
                                     'release_id': RID, 'id': TX}]}
        def fetch(host, role, rid, **kwargs):
            return {'txid': 'tx-' + 'f' * 64,
                    'status': 'live_baseline_verified',
                    'checked_at_utc': '2026-10-26T15:00:00+00:00',
                    'evidence': {'current_pid': 7816,
                                 'journal_state': 'baseline_restored'}}
        with patch('ru_routes.manager.check_runtime.verify_release',
                   return_value=RID), patch(
                   'ru_routes.manager.check_runtime.journal_status',
                   return_value=pending), patch(
                   'ru_routes.manager.check_runtime.fetch_runtime',
                   side_effect=fetch):
            with self.assertRaisesRegex(ValueError, 'txid mismatch'):
                check_pair('/tmp/release', '/tmp/journal',
                           'primary', 'backup', known_hosts='/tmp/known',
                           identity_file='/tmp/key')


if __name__ == '__main__':
    unittest.main()
