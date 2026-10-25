import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from ru_routes.agent.remote import decode_message, receive
from ru_routes.manager.ssh_transport import make_transfer, ship

RID = 'a' * 64


class SSHTransportTests(unittest.TestCase):
    def fixture(self, root):
        release = root / 'release'
        release.mkdir()
        (release / 'release.json').write_text('MANIFEST')
        (release / 'primary.bird.conf').write_text('CONFIG')
        (root / 'known_hosts').write_text('pinned')
        return release

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            release = self.fixture(root)
            with patch('ru_routes.manager.ssh_transport.verify_release', return_value=RID):
                ident, payload = make_transfer(release, 'primary')
            self.assertEqual(ident, RID)
            self.assertEqual(decode_message(payload), ('primary', b'MANIFEST', b'CONFIG'))
            with patch('ru_routes.agent.remote.stage_release', return_value=RID) as fake:
                response = receive(payload, 'primary', root / 'inbox')
            self.assertEqual(response['release_id'], RID)
            self.assertTrue(fake.called)

    def test_bad_role_and_b64_rejected(self):
        message = {'schema': 1, 'role': 'primary', 'manifest': '!', 'config': '!'}
        with self.assertRaises(ValueError):
            decode_message(json.dumps(message).encode())
        message['manifest'] = base64.b64encode(b'A').decode()
        message['config'] = base64.b64encode(b'B').decode()
        with self.assertRaisesRegex(ValueError, 'role mismatch'):
            receive(json.dumps(message).encode(), 'backup', '/tmp/example')

    def test_message_size_limit(self):
        with self.assertRaisesRegex(ValueError, '8 MB'):
            decode_message(b'x' * 8_000_001)

    def test_successful_ssh_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            release = self.fixture(root)
            response = {'role': 'primary', 'release_id': RID, 'status': 'prepared_not_activated'}
            with patch('ru_routes.manager.ssh_transport.verify_release', return_value=RID), patch(
                'ru_routes.manager.ssh_transport.subprocess.run',
                return_value=Mock(returncode=0, stdout=json.dumps(response).encode(), stderr=b'')
            ) as run:
                self.assertEqual(ship(release, 'primary', 'bird1.example',
                                      '/var/lib/routesync/inbox', root / 'known_hosts'), response)
            command = run.call_args.args[0]
            self.assertIn('StrictHostKeyChecking=yes', command)
            self.assertIn('BatchMode=yes', command)
            self.assertIn('routesync@bird1.example', command)

    def test_ssh_failure_and_false_confirmation_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            release = self.fixture(root)
            with patch('ru_routes.manager.ssh_transport.verify_release', return_value=RID), patch(
                'ru_routes.manager.ssh_transport.subprocess.run',
                return_value=Mock(returncode=255, stdout=b'', stderr=b'offline')
            ):
                with self.assertRaisesRegex(RuntimeError, 'offline'):
                    ship(release, 'primary', 'bird1', '/tmp/inbox', root / 'known_hosts')
            wrong = {'role': 'primary', 'release_id': 'b' * 64, 'status': 'prepared_not_activated'}
            with patch('ru_routes.manager.ssh_transport.verify_release', return_value=RID), patch(
                'ru_routes.manager.ssh_transport.subprocess.run',
                return_value=Mock(returncode=0, stdout=json.dumps(wrong).encode(), stderr=b'')
            ):
                with self.assertRaisesRegex(RuntimeError, 'did not confirm'):
                    ship(release, 'primary', 'bird1', '/tmp/inbox', root / 'known_hosts')

    def test_dedicated_ssh_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            release = self.fixture(root)
            identity = root / 'routesync_ed25519'
            identity.write_text('test private key placeholder')
            response = {'role': 'primary', 'release_id': RID, 'status': 'prepared_not_activated'}
            with patch('ru_routes.manager.ssh_transport.verify_release', return_value=RID), patch(
                'ru_routes.manager.ssh_transport.subprocess.run',
                return_value=Mock(returncode=0, stdout=json.dumps(response).encode(), stderr=b'')
            ) as run:
                ship(release, 'primary', 'bird1.example', '/var/lib/routesync/inbox',
                     root / 'known_hosts', identity_file=identity)
            command = run.call_args.args[0]
            self.assertEqual(command[1:3], ['-i', str(identity)])
            self.assertIn('IdentitiesOnly=yes', command)

    def test_reject_injection_and_unpinned_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            release = self.fixture(root)
            with self.assertRaisesRegex(ValueError, 'SSH host'):
                ship(release, 'primary', 'bird1;rm -rf /', '/tmp/inbox', root / 'known_hosts')
            with self.assertRaises(FileNotFoundError):
                ship(release, 'primary', 'bird1', '/tmp/inbox', root / 'absent')


if __name__ == '__main__':
    unittest.main()
