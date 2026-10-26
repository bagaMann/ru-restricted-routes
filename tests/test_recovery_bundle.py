import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ru_routes.agent.recovery_bundle import capture, verify_bundle

RID = 'a' * 64
TX = 'tx-' + 'b' * 64
CONFIG = ('# ISOLATED STATIC TEST ONLY - NO BGP OR KERNEL PROTOCOL\n'
          'router id 127.0.0.1;\n'
          'ipv4 table rt_demo;\n'
          'protocol static st_demo {\n'
          '    ipv4 { table rt_demo; };\n'
          '    route 8.8.8.8/32 blackhole;\n}\n')


class RecoveryBundleTests(unittest.TestCase):
    def fixture(self):
        return {'status': 'agent_prepared_not_active',
                'txid': TX, 'release_id': RID, 'role': 'backup'}

    def capture_fixture(self, root):
        def make(_inbox, _release_id, _role, path):
            Path(path).write_text(CONFIG)
        with patch('ru_routes.agent.recovery_bundle.agent_status',
                   return_value=self.fixture()), \
             patch('ru_routes.agent.recovery_bundle.inspect',
                   return_value={'routers': {'demo': {'prefix_count': 1,
                                                    'prefix_sha256': 'c' * 64}}}), \
             patch('ru_routes.agent.recovery_bundle.make_isolated',
                   side_effect=make):
            result = capture('/tmp/inbox', RID, 'backup', TX,
                             '/tmp/agent', root / 'snapshots')
        return result

    def test_capture_verify_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            receipt = self.capture_fixture(root)
            self.assertEqual(receipt['status'],
                             'static_only_recovery_bundle_not_active')
            storage = root / 'snapshots'
            self.assertEqual(verify_bundle(storage, TX, 'backup'), receipt)
            self.assertNotIn('route 8.8.8.8', (
                storage / f'{TX}.backup' / 'baseline.bird.conf').read_text())
            with self.assertRaises(FileExistsError):
                self.capture_fixture(root)

    def test_tampered_target_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.capture_fixture(root)
            target = root / 'snapshots' / f'{TX}.backup' / 'target.bird.conf'
            target.write_text(target.read_text().replace('8.8.8.8', '9.9.9.9'))
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                verify_bundle(root / 'snapshots', TX, 'backup')

    def test_tampered_baseline_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.capture_fixture(root)
            baseline = root / 'snapshots' / f'{TX}.backup' / 'baseline.bird.conf'
            baseline.write_text(baseline.read_text() + 'route 1.1.1.1/32 blackhole;\n')
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                verify_bundle(root / 'snapshots', TX, 'backup')

    def test_reject_wrong_transaction(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.capture_fixture(root)
            with self.assertRaisesRegex(ValueError, 'identity'):
                verify_bundle(root / 'snapshots', TX, 'backup', release_id='f' * 64)

    def test_reject_without_prepared_intent(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            wrong = dict(self.fixture(), status='agent_aborted_not_active')
            with patch('ru_routes.agent.recovery_bundle.agent_status',
                       return_value=wrong):
                with self.assertRaisesRegex(ValueError, 'prepared'):
                    capture('/tmp/inbox', RID, 'backup', TX, '/tmp/agent',
                            root / 'snapshots')
            self.assertFalse((root / 'snapshots').exists())

    def test_symlink_bundle_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            base = root / 'snapshots'
            base.mkdir()
            (base / f'{TX}.backup').symlink_to(root)
            with self.assertRaisesRegex(ValueError, 'unavailable'):
                verify_bundle(base, TX, 'backup')


if __name__ == '__main__':
    unittest.main()
