import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ru_routes.agent.control_remote import respond
from ru_routes.manager.probe_control import probe, check_pair

RID = 'a' * 64
TX = 'tx-' + 'b' * 64
SHA = 'c' * 64


def request(**changes):
    obj = {'schema': 1, 'operation': 'probe', 'txid': TX,
           'release_id': RID, 'role': 'backup'}
    obj.update(changes)
    return json.dumps(obj).encode()


def prepared():
    return {'state': 'control_prepared_not_authorized_not_active',
            'txid': TX, 'release_id': RID, 'role': 'backup',
            'target_sha256': SHA, 'baseline_sha256': SHA}


def reply(role):
    return {'schema': 1, 'operation': 'probe',
            'status': 'control_channel_probe_only_not_authorized',
            'txid': TX, 'release_id': RID, 'role': role,
            'target_sha256': SHA, 'baseline_sha256': SHA,
            'activation_authorized': False, 'activation_performed': False}


class ControlRemoteTests(unittest.TestCase):
    def test_probe_valid(self):
        with patch('ru_routes.agent.control_remote.control_status',
                   return_value=prepared()):
            result = respond(request(), role='backup',
                             release_id=RID, state_dir='/tmp/control')
        self.assertEqual(result['status'],
                         'control_channel_probe_only_not_authorized')
        self.assertFalse(result['activation_authorized'])

    def test_reject_extra_parameters(self):
        with self.assertRaisesRegex(ValueError, 'fields'):
            respond(request(command='birdc configure'), role='backup',
                    release_id=RID, state_dir='/tmp/control')

    def test_reject_write_operation(self):
        with self.assertRaisesRegex(ValueError, 'unsupported control'):
            respond(request(operation='apply'), role='backup',
                    release_id=RID, state_dir='/tmp/control')

    def test_reject_wrong_role(self):
        with self.assertRaisesRegex(ValueError, 'fixed agent'):
            respond(request(role='primary'), role='backup',
                    release_id=RID, state_dir='/tmp/control')

    def test_reject_wrong_transaction(self):
        with patch('ru_routes.agent.control_remote.control_status',
                   return_value=prepared()):
            with self.assertRaisesRegex(ValueError, 'matching one-shot'):
                respond(request(txid='tx-' + 'f' * 64), role='backup',
                        release_id=RID, state_dir='/tmp/control')

    def test_reject_array_operation(self):
        with self.assertRaisesRegex(ValueError, 'unsupported control'):
            respond(request(operation=['apply']), role='backup',
                    release_id=RID, state_dir='/tmp/control')

    def test_reject_large_request(self):
        with self.assertRaisesRegex(ValueError, 'oversized'):
            respond(b'X' * 1025, role='backup',
                    release_id=RID, state_dir='/tmp/control')

    def test_manager_probe_uses_independent_ssh_key(self):
        with tempfile.TemporaryDirectory() as d:
            key, known = Path(d) / 'key', Path(d) / 'known'
            key.touch()
            known.touch()
            with patch('ru_routes.manager.probe_control.subprocess.run',
                       return_value=Mock(returncode=0, stderr=b'',
                                         stdout=json.dumps(reply('backup')).encode())) as ssh:
                result = probe('10.110.27.201', 'backup', TX, RID,
                               known_hosts=known, identity_file=key)
            self.assertEqual(result['role'], 'backup')
            self.assertIn('routectl@10.110.27.201', ssh.call_args.args[0])
            self.assertEqual(json.loads(ssh.call_args.kwargs['input'])['operation'], 'probe')

    def test_manager_rejects_forged_authorization(self):
        forged = reply('backup')
        forged['activation_authorized'] = True
        with tempfile.TemporaryDirectory() as d:
            key, known = Path(d) / 'key', Path(d) / 'known'
            key.touch()
            known.touch()
            with patch('ru_routes.manager.probe_control.subprocess.run',
                       return_value=Mock(returncode=0, stderr=b'',
                                         stdout=json.dumps(forged).encode())):
                with self.assertRaisesRegex(ValueError, 'incorrect probe'):
                    probe('10.110.27.201', 'backup', TX, RID,
                          known_hosts=known, identity_file=key)

    def test_pair_reports_only_no_write(self):
        pending = {'transactions': [{'id': TX, 'release_id': RID,
                                     'state': 'intent_prepared_not_active'}]}
        with patch('ru_routes.manager.probe_control.verify_release',
                   return_value=RID), patch(
                   'ru_routes.manager.probe_control.journal_status',
                   return_value=pending), patch(
                   'ru_routes.manager.probe_control.probe',
                   side_effect=lambda host, role, txid, release, **kwargs: reply(role)):
            report = check_pair('/tmp/release', '/tmp/journal',
                                '10.110.11.201', '10.110.27.201',
                                known_hosts='/tmp/known',
                                identity_file='/tmp/key')
        self.assertEqual(report['status'],
                         'both_separate_control_ssh_probes_verified_no_write')
        self.assertFalse(report['activation_performed'])


if __name__ == '__main__':
    unittest.main()
