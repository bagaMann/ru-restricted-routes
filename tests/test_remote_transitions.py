import json
import unittest
from unittest.mock import patch

from ru_routes.agent.status_remote import respond
from ru_routes.manager.check_transitions import check_pair

RID = 'a' * 64
TX = 'tx-' + 'b' * 64
SHA = 'c' * 64


class TransitionSSHTests(unittest.TestCase):
    def test_read_only_endpoint_reports_transition(self):
        record = {'release_id': RID, 'role': 'backup', 'txid': TX,
                  'pid': 7427, 'baseline_sha256': SHA,
                  'target_sha256': SHA, 'recorded_at_utc': 'now'}
        request = json.dumps({'schema': 1, 'release_id': RID,
                              'query': 'transition'}).encode()
        with patch('ru_routes.agent.status_remote.transition_history',
                   return_value={'status': 'baseline_restored',
                                 'entries': [record]}):
            result = respond(request, role='backup', inbox='/tmp/inbox',
                             state_dir='/tmp/preflight',
                             transition_dir='/tmp/transitions')
        self.assertEqual(result['status'], 'baseline_restored')
        self.assertEqual(result['source'],
                         'durable_transition_journal_not_live_runtime')

    def test_wrong_role_rejected(self):
        record = {'release_id': RID, 'role': 'primary'}
        request = json.dumps({'schema': 1, 'release_id': RID,
                              'query': 'transition'}).encode()
        with patch('ru_routes.agent.status_remote.transition_history',
                   return_value={'status': 'baseline_restored',
                                 'entries': [record]}):
            with self.assertRaises(ValueError):
                respond(request, role='backup', inbox='/tmp/inbox',
                        state_dir='/tmp/preflight',
                        transition_dir='/tmp/transitions')

    def test_pair_accepts_completed_journals_but_not_authorized(self):
        pending = {'transactions': [{'id': TX, 'release_id': RID,
                                     'state': 'intent_prepared_not_active'}]}
        def reply(host, role, release_id, **kwargs):
            return {'role': role, 'release_id': release_id,
                    'txid': TX, 'status': 'baseline_restored'}
        with patch('ru_routes.manager.check_transitions.verify_release',
                   return_value=RID), patch(
                   'ru_routes.manager.check_transitions.journal_status',
                   return_value=pending), patch(
                   'ru_routes.manager.check_transitions.fetch_transition',
                   side_effect=reply):
            result = check_pair('/tmp/release', '/tmp/journal',
                                'primary', 'backup',
                                known_hosts='/tmp/key', identity_file='/tmp/identity')
        self.assertFalse(result['activation_authorized'])
        self.assertFalse(result['live_runtime_checked'])

    def test_pair_blocks_incomplete_journal(self):
        pending = {'transactions': [{'id': TX, 'release_id': RID,
                                     'state': 'intent_prepared_not_active'}]}
        def reply(host, role, release_id, **kwargs):
            return {'role': role, 'release_id': release_id, 'txid': TX,
                    'status': 'rollback_in_progress'}
        with patch('ru_routes.manager.check_transitions.verify_release',
                   return_value=RID), patch(
                   'ru_routes.manager.check_transitions.journal_status',
                   return_value=pending), patch(
                   'ru_routes.manager.check_transitions.fetch_transition',
                   side_effect=reply):
            with self.assertRaisesRegex(ValueError, 'backup: transition'):
                check_pair('/tmp/release', '/tmp/journal',
                           'primary', 'backup',
                           known_hosts='/tmp/key', identity_file='/tmp/identity')


if __name__ == '__main__':
    unittest.main()
