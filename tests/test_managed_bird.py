import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from ru_routes.agent.managed_bird import startup_spec, start

RID = 'a' * 64
TXID = 'tx-' + 'b' * 64


class ManagedBirdTests(unittest.TestCase):
    def pending(self):
        return {'status': 'agent_prepared_not_active', 'role': 'backup',
                'txid': TXID, 'release_id': RID}

    def create_spec(self, root, *, state=None):
        storage = root / 'storage'
        storage.mkdir(exist_ok=True)
        runtime = root / 'runtime'
        runtime.mkdir(exist_ok=True)
        with patch('ru_routes.agent.managed_bird.intent_status',
                   return_value=state or self.pending()), \
             patch('ru_routes.agent.managed_bird.verify_bundle',
                   return_value={'baseline_sha256': 'f' * 64}):
            return startup_spec(storage, TXID, 'backup', RID,
                                root / 'agent-state', runtime)

    def test_startup_uses_only_baseline_and_separate_socket(self):
        with tempfile.TemporaryDirectory() as d:
            spec = self.create_spec(Path(d))
            self.assertEqual(spec['status'], 'managed_baseline_startup_only')
            self.assertTrue(spec['config'].endswith('baseline.bird.conf'))
            self.assertIn('managed.ctl', spec['control_socket'])
            self.assertNotIn('/etc/bird', ' '.join(spec['argv']))
            self.assertNotIn('target.bird.conf', ' '.join(spec['argv']))

    def test_refuse_wrong_intent(self):
        with tempfile.TemporaryDirectory() as d:
            wrong = dict(self.pending(), txid='tx-' + 'c' * 64)
            with self.assertRaisesRegex(ValueError, 'matching pending'):
                self.create_spec(Path(d), state=wrong)

    def test_refuse_missing_runtime_directory(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with patch('ru_routes.agent.managed_bird.intent_status',
                       return_value=self.pending()), \
                 patch('ru_routes.agent.managed_bird.verify_bundle',
                       return_value={'baseline_sha256': 'f' * 64}):
                with self.assertRaisesRegex(ValueError, 'must exist'):
                    startup_spec(root / 'storage', TXID, 'backup', RID,
                                 root / 'agent', root / 'missing')

    def test_refuse_runtime_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'real').mkdir()
            (root / 'link').symlink_to(root / 'real')
            with patch('ru_routes.agent.managed_bird.intent_status',
                       return_value=self.pending()), \
                 patch('ru_routes.agent.managed_bird.verify_bundle',
                       return_value={'baseline_sha256': 'f' * 64}):
                with self.assertRaisesRegex(ValueError, 'not symlinked'):
                    startup_spec(root / 'storage', TXID, 'backup', RID,
                                 root / 'agent', root / 'link')

    def test_start_execs_only_after_syntax_validation(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            spec = self.create_spec(root)
            with patch('ru_routes.agent.managed_bird.startup_spec',
                       return_value=spec), \
                 patch('ru_routes.agent.managed_bird.subprocess.run',
                       return_value=Mock(returncode=0)) as parse, \
                 patch('ru_routes.agent.managed_bird.os.execvp') as execvp:
                start('/tmp/storage', TXID, 'backup', RID,
                      '/tmp/agent', '/tmp/runtime')
            self.assertIn('-p', parse.call_args.args[0])
            self.assertEqual(execvp.call_args.args[0], 'bird')
            self.assertTrue(execvp.call_args.args[0][1][-3].endswith('managed.ctl'))

    def test_syntax_failure_does_not_exec(self):
        with patch('ru_routes.agent.managed_bird.startup_spec',
                   return_value={'config': '/tmp/baseline',
                                 'argv': ['bird']}), \
             patch('ru_routes.agent.managed_bird.subprocess.run',
                   side_effect=RuntimeError('syntax check failed')), \
             patch('ru_routes.agent.managed_bird.os.execvp') as execvp:
            with self.assertRaisesRegex(RuntimeError, 'syntax'):
                start('/tmp/storage', TXID, 'backup', RID,
                      '/tmp/agent', '/tmp/runtime')
            execvp.assert_not_called()


if __name__ == '__main__':
    unittest.main()
