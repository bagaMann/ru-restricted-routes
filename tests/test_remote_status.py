import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ru_routes.agent.status_remote import respond
from ru_routes.manager.check_agents import fetch_status, check_agents

RID = 'a' * 64


class RemoteStatusTests(unittest.TestCase):
    def test_agent_status_requires_only_release_id(self):
        reply = {'status': 'preflight_receipt_verified_not_active',
                 'release_id': RID, 'role': 'backup'}
        with patch('ru_routes.agent.status_remote.inspect_receipt', return_value=reply) as read:
            result = respond(json.dumps({'schema': 1, 'release_id': RID}).encode(),
                             role='backup', inbox='/tmp/inbox', state_dir='/tmp/state')
        self.assertEqual(result, reply)
        read.assert_called_once_with('/tmp/inbox', RID, 'backup', '/tmp/state')

    def test_agent_rejects_extra_fields_and_invalid_release(self):
        for data in ({'schema': 1, 'release_id': RID, 'action': 'apply'},
                     {'schema': 1, 'release_id': '../etc'},
                     {'schema': 2, 'release_id': RID}):
            with self.assertRaises(ValueError):
                respond(json.dumps(data).encode(), role='primary',
                        inbox='/tmp/inbox', state_dir='/tmp/state')

    def test_agent_request_size_limit(self):
        with self.assertRaisesRegex(ValueError, 'too long'):
            respond(b'x' * 1025, role='primary', inbox='/tmp/inbox',
                    state_dir='/tmp/state')

    def test_ssh_fetch_fixed_identity_and_strict_pinning(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            known, identity = root / 'known', root / 'key'
            known.touch()
            identity.touch()
            reply = {'status': 'preflight_receipt_verified_not_active',
                     'role': 'primary', 'release_id': RID, 'routers': {}}
            with patch('ru_routes.manager.check_agents.subprocess.run',
                       return_value=Mock(returncode=0, stdout=json.dumps(reply).encode(),
                                         stderr=b'')) as run:
                self.assertEqual(fetch_status('10.110.11.201', 'primary', RID,
                    known_hosts=known, identity_file=identity), reply)
            command = run.call_args.args[0]
            self.assertIn('StrictHostKeyChecking=yes', command)
            self.assertIn('IdentitiesOnly=yes', command)
            self.assertIn('routesync@10.110.11.201', command)
            self.assertIn('status', command)

    def test_remote_false_status_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            known, identity = Path(d) / 'known', Path(d) / 'key'
            known.touch()
            identity.touch()
            reply = {'status': 'preflight_receipt_verified_not_active',
                     'role': 'backup', 'release_id': RID}
            with patch('ru_routes.manager.check_agents.subprocess.run',
                       return_value=Mock(returncode=0, stdout=json.dumps(reply).encode(),
                                         stderr=b'')):
                with self.assertRaisesRegex(ValueError, 'unexpected'):
                    fetch_status('10.110.11.201', 'primary', RID,
                                 known_hosts=known, identity_file=identity)

    def test_pair_rejects_route_digest_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            release = root / 'release'
            release.mkdir()
            route = {'prefix_count': 1, 'prefix_sha256': 'c' * 64}
            manifest = {'servers': {
                'primary': {'routers': {'demo': route}},
                'backup': {'routers': {'demo': route}}}}
            (release / 'release.json').write_text(json.dumps(manifest))
            def fetch(host, role, rid, **kwargs):
                wrong = dict(route)
                if role == 'backup':
                    wrong['prefix_sha256'] = 'f' * 64
                return {'role': role, 'release_id': rid,
                        'status': 'preflight_receipt_verified_not_active',
                        'routers': {'demo': wrong}}
            pending = {'transactions': [{'state': 'intent_prepared_not_active',
                                          'release_id': RID, 'id': 'tx-123'}]}
            with patch('ru_routes.agent.stage.verify_release', return_value=RID), \
                 patch('ru_routes.manager.transaction_journal.journal_status',
                       return_value=pending), \
                 patch('ru_routes.manager.check_agents.fetch_status', side_effect=fetch):
                with self.assertRaisesRegex(ValueError, 'backup: remote routes'):
                    check_agents(release, '/tmp/journal', '10.110.11.201', '10.110.27.201',
                                 known_hosts='/tmp/known', identity_file='/tmp/key')

    def test_distinct_ssh_hosts_required(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            release = root / 'release'
            release.mkdir()
            pending = {'transactions': [{'state': 'intent_prepared_not_active',
                                         'release_id': RID, 'id': 'tx-123'}]}
            with patch('ru_routes.agent.stage.verify_release', return_value=RID), \
                 patch('ru_routes.manager.transaction_journal.journal_status',
                       return_value=pending):
                with self.assertRaisesRegex(ValueError, 'different'):
                    check_agents(release, '/tmp/journal', 'same', 'same',
                                 known_hosts='/tmp/known', identity_file='/tmp/key')


if __name__ == '__main__':
    unittest.main()
