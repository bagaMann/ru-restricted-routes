import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ru_routes.agent.status_remote import respond
from ru_routes.manager.check_control import check_pair, fetch_control

RELEASE = 'a' * 64
TXID = 'tx-' + 'b' * 64
SHA = 'c' * 64


def receipt(role):
    return {'state': 'control_prepared_not_authorized_not_active',
            'txid': TXID, 'release_id': RELEASE, 'role': role,
            'target_sha256': SHA, 'baseline_sha256': SHA,
            'managed_pid': 7816, 'prepared_at_utc': '2026-10-27T16:24:34+00:00',
            'activation_authorized': False, 'activation_performed': False}


class ReadOnlyControlTests(unittest.TestCase):
    def test_agent_exposes_prepared_receipt_without_authorization(self):
        raw = json.dumps({'schema': 1, 'release_id': RELEASE,
                          'query': 'control'}).encode()
        with patch('ru_routes.agent.status_remote.control_status',
                   return_value={key: value for key, value in
                                 receipt('backup').items()
                                 if key not in ('activation_authorized',
                                                'activation_performed')}):
            result = respond(raw, role='backup', inbox='/tmp/inbox',
                             state_dir='/tmp/receipts',
                             control_state_dir='/tmp/control')
        self.assertFalse(result['activation_authorized'])
        self.assertEqual(result['txid'], TXID)

    def test_agent_rejects_wrong_fixed_role(self):
        raw = json.dumps({'schema': 1, 'release_id': RELEASE,
                          'query': 'control'}).encode()
        with patch('ru_routes.agent.status_remote.control_status',
                   return_value=receipt('primary')):
            with self.assertRaisesRegex(ValueError, 'no matching'):
                respond(raw, role='backup', inbox='/tmp/inbox',
                        state_dir='/tmp/receipt', control_state_dir='/tmp/control')

    def test_manager_remote_receipt_is_strict(self):
        with tempfile.TemporaryDirectory() as d:
            known = Path(d) / 'known'
            key = Path(d) / 'identity'
            known.touch()
            key.touch()
            with patch('ru_routes.manager.check_control.subprocess.run',
                       return_value=Mock(returncode=0, stderr=b'',
                                         stdout=json.dumps(receipt('backup')).encode())):
                item = fetch_control('10.110.27.201', 'backup', RELEASE,
                                     known_hosts=known, identity_file=key)
            self.assertEqual(item['txid'], TXID)

    def test_manager_refuses_unauthorized_true(self):
        bad = receipt('backup')
        bad['activation_authorized'] = True
        with tempfile.TemporaryDirectory() as d:
            known, key = Path(d) / 'known', Path(d) / 'key'
            known.touch()
            key.touch()
            with patch('ru_routes.manager.check_control.subprocess.run',
                       return_value=Mock(returncode=0, stderr=b'',
                                         stdout=json.dumps(bad).encode())):
                with self.assertRaisesRegex(ValueError, 'invalid control'):
                    fetch_control('10.110.27.201', 'backup', RELEASE,
                                  known_hosts=known, identity_file=key)

    def test_both_match_manager_pending(self):
        journal = {'transactions': [{'id': TXID, 'release_id': RELEASE,
                                     'state': 'intent_prepared_not_active'}]}
        with patch('ru_routes.manager.check_control.verify_release',
                   return_value=RELEASE), patch(
                   'ru_routes.manager.check_control.journal_status',
                   return_value=journal), patch(
                   'ru_routes.manager.check_control.fetch_control',
                   side_effect=lambda host, role, release_id, **kw: receipt(role)):
            result = check_pair('/tmp/release', '/tmp/journal',
                                '10.110.11.201', '10.110.27.201',
                                known_hosts='/tmp/known',
                                identity_file='/tmp/key')
        self.assertEqual(result['status'],
                         'both_control_preparations_match_not_authorized')
        self.assertFalse(result['activation_performed'])

    def test_wrong_control_txid_fails_closed(self):
        journal = {'transactions': [{'id': TXID, 'release_id': RELEASE,
                                     'state': 'intent_prepared_not_active'}]}
        with patch('ru_routes.manager.check_control.verify_release',
                   return_value=RELEASE), patch(
                   'ru_routes.manager.check_control.journal_status',
                   return_value=journal), patch(
                   'ru_routes.manager.check_control.fetch_control',
                   side_effect=lambda host, role, release_id, **kw:
                   dict(receipt(role), txid='tx-' + 'f' * 64)):
            with self.assertRaisesRegex(ValueError, 'txid mismatch'):
                check_pair('/tmp/release', '/tmp/journal', 'primary', 'backup',
                           known_hosts='/tmp/known', identity_file='/tmp/key')


if __name__ == '__main__':
    unittest.main()
