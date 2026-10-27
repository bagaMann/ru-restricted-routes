import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ru_routes.agent.control_journal import prepare, status

RID = 'a' * 64
TX = 'tx-' + 'b' * 64
DIGEST = 'c' * 64


def request():
    return json.dumps({'schema': 1, 'operation': 'prepare_check',
                       'txid': TX, 'release_id': RID, 'role': 'backup'}).encode()


class ControlJournalTests(unittest.TestCase):
    def invoke(self, root, *, precheck=None):
        validated = precheck or {
            'txid': TX, 'release_id': RID, 'role': 'backup',
            'target_sha256': DIGEST, 'baseline_sha256': DIGEST,
            'managed_pid': 7816,
            'status': 'local_control_precheck_verified_not_authorized',
        }
        with patch('ru_routes.agent.control_journal.process', return_value=validated):
            return prepare(request(), state_dir=root,
                           storage='/tmp/storage', role='backup',
                           release_id=RID, agent_state_dir='/tmp/intent',
                           runtime_dir='/tmp/runtime',
                           transition_dir='/tmp/transitions')

    def test_first_prepare_is_write_once_and_not_activation(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'control'
            self.assertEqual(status(root)['status'], 'no_control_preparation')
            receipt = self.invoke(root)
            self.assertEqual(receipt['state'],
                             'control_prepared_not_authorized_not_active')
            self.assertFalse(receipt['activation_authorized'])
            self.assertFalse(receipt['activation_performed'])
            self.assertEqual(status(root)['managed_pid'], 7816)
            self.assertTrue((root / 'control-state.json').is_file())

    def test_replay_refused(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'control'
            self.invoke(root)
            with patch('ru_routes.agent.control_journal.process') as verify:
                with self.assertRaisesRegex(ValueError, 'no replay'):
                    prepare(request(), state_dir=root,
                            storage='/tmp/storage', role='backup',
                            release_id=RID, agent_state_dir='/tmp/intent',
                            runtime_dir='/tmp/runtime',
                            transition_dir='/tmp/transitions')
                verify.assert_not_called()

    def test_corruption_blocks_future_operation(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'control'
            self.invoke(root)
            file = root / 'control-state.json'
            obj = json.loads(file.read_text())
            obj['record']['role'] = 'primary'
            file.write_text(json.dumps(obj))
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                status(root)
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                self.invoke(root)

    def test_failed_precheck_does_not_create_journal(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'control'
            with patch('ru_routes.agent.control_journal.process',
                       side_effect=ValueError('baseline not verified')):
                with self.assertRaisesRegex(ValueError, 'baseline not verified'):
                    prepare(request(), state_dir=root,
                            storage='/tmp/storage', role='backup',
                            release_id=RID, agent_state_dir='/tmp/agent',
                            runtime_dir='/tmp/runtime',
                            transition_dir='/tmp/transitions')
            self.assertEqual(status(root)['status'], 'no_control_preparation')

    def test_symlink_file_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'control'
            root.mkdir()
            (root / 'control-state.json').symlink_to('/etc/passwd')
            with self.assertRaisesRegex(ValueError, 'symlinked'):
                status(root)

    def test_identity_is_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'control'
            record = self.invoke(root)
            self.assertEqual(record['txid'], TX)
            self.assertEqual(record['release_id'], RID)
            self.assertEqual(record['target_sha256'], DIGEST)


if __name__ == '__main__':
    unittest.main()
