import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ru_routes.agent.status_remote import respond
from ru_routes.manager.reconcile_intents import reconcile, fetch_intent

RID = 'a' * 64
TXID = 'tx-' + 'b' * 64


def record(role='primary', txid=TXID):
    return {'txid': txid, 'release_id': RID, 'role': role,
            'status': 'agent_prepared_not_active',
            'created_at_utc': '2026-10-26T11:03:02+00:00',
            'closed_at_utc': None}


class ReconcileIntentsTests(unittest.TestCase):
    def test_agent_remote_intent_read(self):
        with patch('ru_routes.agent.status_remote.intent_status',
                   return_value=record()) as status:
            result = respond(json.dumps({'schema': 1, 'release_id': RID,
                                         'query': 'intent'}).encode(),
                             role='primary', inbox='/tmp/inbox',
                             state_dir='/tmp/receipts', agent_state_dir='/tmp/agent')
        self.assertEqual(result['txid'], TXID)
        status.assert_called_once_with('/tmp/agent')

    def test_agent_rejects_intent_mismatch_or_unconfigured(self):
        request = json.dumps({'schema': 1, 'release_id': RID,
                              'query': 'intent'}).encode()
        with self.assertRaisesRegex(ValueError, 'unsupported'):
            respond(request, role='primary', inbox='/tmp/inbox', state_dir='/tmp/receipts')
        with patch('ru_routes.agent.status_remote.intent_status',
                   return_value=record(role='backup')):
            with self.assertRaisesRegex(ValueError, 'no matching'):
                respond(request, role='primary', inbox='/tmp/inbox',
                        state_dir='/tmp/receipts', agent_state_dir='/tmp/agent')

    def test_agent_rejects_nonreadonly_query(self):
        request = json.dumps({'schema': 1, 'release_id': RID,
                              'query': 'activate'}).encode()
        with self.assertRaisesRegex(ValueError, 'unsupported'):
            respond(request, role='primary', inbox='/tmp/inbox',
                    state_dir='/tmp/receipts', agent_state_dir='/tmp/agent')

    def test_fetch_intent_pinned_ssh(self):
        with tempfile.TemporaryDirectory() as d:
            known = Path(d) / 'known'
            identity = Path(d) / 'key'
            known.touch()
            identity.touch()
            with patch('ru_routes.manager.reconcile_intents.subprocess.run',
                       return_value=Mock(returncode=0,
                                         stdout=json.dumps(record()).encode(),
                                         stderr=b'')) as run:
                result = fetch_intent('10.110.11.201', 'primary', RID,
                                      known_hosts=known, identity_file=identity)
            self.assertEqual(result['txid'], TXID)
            command = run.call_args.args[0]
            self.assertIn('StrictHostKeyChecking=yes', command)
            self.assertIn('IdentitiesOnly=yes', command)
            request = json.loads(run.call_args.kwargs['input'])
            self.assertEqual(request['query'], 'intent')

    def test_fetch_intent_rejects_wrong_role(self):
        with tempfile.TemporaryDirectory() as d:
            known = Path(d) / 'known'
            identity = Path(d) / 'key'
            known.touch()
            identity.touch()
            with patch('ru_routes.manager.reconcile_intents.subprocess.run',
                       return_value=Mock(returncode=0,
                                         stdout=json.dumps(record(role='backup')).encode(),
                                         stderr=b'')):
                with self.assertRaisesRegex(ValueError, 'unexpected'):
                    fetch_intent('10.110.11.201', 'primary', RID,
                                 known_hosts=known, identity_file=identity)

    def test_pair_matching_intents(self):
        pending = {'transactions': [{'id': TXID, 'release_id': RID,
                                      'state': 'intent_prepared_not_active'}]}
        with patch('ru_routes.manager.reconcile_intents.verify_release', return_value=RID), \
             patch('ru_routes.manager.reconcile_intents.journal_status',
                   return_value=pending), \
             patch('ru_routes.manager.reconcile_intents.fetch_intent',
                   side_effect=lambda host, role, rid, **kw: record(role)):
            result = reconcile('/tmp/release', '/tmp/journal',
                               '10.110.11.201', '10.110.27.201',
                               known_hosts='/tmp/known', identity_file='/tmp/key')
        self.assertEqual(result['status'], 'all_three_intents_match_not_active')
        self.assertEqual(len(result['agents']), 2)

    def test_pair_rejects_wrong_transaction(self):
        pending = {'transactions': [{'id': TXID, 'release_id': RID,
                                      'state': 'intent_prepared_not_active'}]}
        with patch('ru_routes.manager.reconcile_intents.verify_release', return_value=RID), \
             patch('ru_routes.manager.reconcile_intents.journal_status',
                   return_value=pending), \
             patch('ru_routes.manager.reconcile_intents.fetch_intent',
                   side_effect=lambda host, role, rid, **kw: record(
                       role, txid='tx-' + 'f' * 64 if role == 'backup' else TXID)):
            with self.assertRaisesRegex(ValueError, 'backup: remote agent txid'):
                reconcile('/tmp/release', '/tmp/journal', '10.110.11.201',
                          '10.110.27.201', known_hosts='/tmp/known', identity_file='/tmp/key')

    def test_pair_rejects_same_host(self):
        with self.assertRaisesRegex(ValueError, 'must differ'):
            reconcile('/tmp/release', '/tmp/journal', 'same', 'same',
                      known_hosts='/tmp/known', identity_file='/tmp/key')


if __name__ == '__main__':
    unittest.main()
