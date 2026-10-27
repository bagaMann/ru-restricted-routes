import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ru_routes.agent.execution_journal import inspect
from ru_routes.agent.local_executor import execute

RID = 'a' * 64
TX = 'tx-' + 'b' * 64
SHA = 'c' * 64


class LocalExecutorTests(unittest.TestCase):
    def fixture(self, root):
        root = Path(root)
        (root / 'historical').mkdir()
        prepared = {'state': 'control_prepared_not_authorized_not_active',
                    'txid': TX, 'role': 'backup', 'release_id': RID,
                    'managed_pid': 7816,
                    'baseline_sha256': SHA, 'target_sha256': SHA}
        bundle = {'baseline_sha256': SHA, 'target_sha256': SHA,
                  'routers': {'demo': {'prefix_count': 1,
                                       'prefix_sha256': SHA}}}
        return root / 'historical', root / 'execution', prepared, bundle

    def run_mocked(self, historical, execution, prepared, bundle, *,
                   configure_effect=None, pid=True, table_failure=False):
        with patch('ru_routes.agent.local_executor.control_status',
                   return_value=prepared), patch(
                   'ru_routes.agent.local_executor.verify_bundle',
                   return_value=bundle), patch(
                   'ru_routes.agent.local_executor.audit',
                   return_value={'managed_pid': 7816,
                                 'control_socket': '/run/managed.ctl'}), patch(
                   'ru_routes.agent.local_executor.audit_process_identity',
                   return_value=pid), patch(
                   'ru_routes.agent.local_executor._configure',
                   side_effect=configure_effect) as cfg, patch(
                   'ru_routes.agent.local_executor._check_until',
                   side_effect=lambda fn: fn()), patch(
                   'ru_routes.agent.local_executor.validate_tables',
                   side_effect=(ValueError('routes mismatch') if table_failure
                                else lambda routers, show: {'demo': {'prefix_count': 1}})), patch(
                   'ru_routes.agent.local_executor._query',
                   return_value='BIRD 2 ready\nTable rt_demo:\n') as query:
            result = execute('/tmp/recovery', TX, 'backup', RID,
                             '/tmp/agent', '/tmp/runtime',
                             historical, '/tmp/prepared', execution)
            return result, cfg, query

    def test_success_and_durable_states(self):
        with tempfile.TemporaryDirectory() as d:
            historical, execution, prepared, bundle = self.fixture(d)
            result, configures, _ = self.run_mocked(
                historical, execution, prepared, bundle)
            self.assertTrue(result['rollback_verified'])
            self.assertFalse(result['active'])
            self.assertEqual(configures.call_count, 2)
            states = [e['state'] for e in inspect(execution)['entries']]
            self.assertEqual(states, ['execution_prepared', 'apply_in_progress',
                                      'target_verified', 'rollback_in_progress',
                                      'baseline_restored'])

    def test_replay_refused_before_all_runtime_io(self):
        with tempfile.TemporaryDirectory() as d:
            h, e, p, b = self.fixture(d)
            self.run_mocked(h, e, p, b)
            with patch('ru_routes.agent.local_executor.audit') as audit:
                with self.assertRaisesRegex(ValueError, 'do not replay'):
                    execute('/tmp/recovery', TX, 'backup', RID,
                            '/tmp/agent', '/tmp/runtime', h,
                            '/tmp/prepared', e)
                audit.assert_not_called()

    def test_wrong_prepared_digest_refused(self):
        with tempfile.TemporaryDirectory() as d:
            h, e, p, b = self.fixture(d)
            p['target_sha256'] = 'f' * 64
            with self.assertRaisesRegex(ValueError, 'checksum'):
                self.run_mocked(h, e, p, b)
            self.assertEqual(inspect(e)['status'], 'no_execution')

    def test_pid_mismatch_refused_before_apply(self):
        with tempfile.TemporaryDirectory() as d:
            h, e, p, b = self.fixture(d)
            p['managed_pid'] = 7
            with self.assertRaisesRegex(ValueError, 'PID differs'):
                self.run_mocked(h, e, p, b)
            self.assertEqual(inspect(e)['status'], 'no_execution')

    def test_reconfiguration_failure_attempts_rollback(self):
        with tempfile.TemporaryDirectory() as d:
            h, e, p, b = self.fixture(d)
            counter = []
            def config(*args):
                counter.append(args)
                if len(counter) == 1:
                    raise RuntimeError('target rejected')
            with self.assertRaisesRegex(RuntimeError, 'target rejected'):
                self.run_mocked(h, e, p, b, configure_effect=config)
            self.assertEqual(len(counter), 2)
            self.assertEqual(inspect(e)['status'], 'baseline_restored')

    def test_failed_rollback_leaves_unresolved(self):
        with tempfile.TemporaryDirectory() as d:
            h, e, p, b = self.fixture(d)
            with self.assertRaisesRegex(RuntimeError, 'socket down'):
                self.run_mocked(h, e, p, b,
                                configure_effect=RuntimeError('socket down'))
            self.assertEqual(inspect(e)['status'], 'rollback_in_progress')
            self.assertTrue(inspect(e)['unresolved'])

    def test_historical_cycle_lock_is_checked(self):
        import fcntl
        import os
        with tempfile.TemporaryDirectory() as d:
            h, e, p, b = self.fixture(d)
            file = os.open(h / '.cycle.lock', os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaisesRegex(RuntimeError, 'historical managed cycle'):
                    self.run_mocked(h, e, p, b)
                self.assertEqual(inspect(e)['status'], 'no_execution')
            finally:
                os.close(file)


if __name__ == '__main__':
    unittest.main()
