import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ru_routes.agent.managed_cycle import cycle

RID = 'a' * 64
TXID = 'tx-' + 'b' * 64
SHA = 'c' * 64


class ManagedCycleTests(unittest.TestCase):
    def fixture(self, root):
        storage = root / 'storage'
        directory = storage / f'{TXID}.backup'
        directory.mkdir(parents=True)
        return storage, root / 'journal'

    def mocks(self):
        return {'baseline_sha256': SHA, 'target_sha256': SHA,
                'routers': {'demo': {'prefix_count': 1, 'prefix_sha256': SHA}}}

    def test_success_records_every_transition(self):
        with tempfile.TemporaryDirectory() as d:
            storage, journal = self.fixture(Path(d))
            commands = []
            def command(_birdc, _socket, *args):
                commands.append(args)
                if args[0] == 'configure':
                    return 'Reconfigured\n'
                return 'BIRD 2.17.5 ready.\nTable rt_demo:\n'
            with patch('ru_routes.agent.managed_cycle.audit',
                       return_value={'control_socket': '/run/managed.ctl',
                                     'managed_pid': 7427}), \
                 patch('ru_routes.agent.managed_cycle.verify_bundle',
                       return_value=self.mocks()), \
                 patch('ru_routes.agent.managed_cycle._query',
                       side_effect=command), \
                 patch('ru_routes.agent.managed_cycle.audit_process_identity',
                       return_value=True), \
                 patch('ru_routes.agent.managed_cycle.validate_tables',
                       return_value={'demo': {'prefix_count': 1,
                                              'prefix_sha256': SHA}}):
                result = cycle(storage, TXID, 'backup', RID, '/tmp/agent',
                               '/tmp/runtime', journal)
            self.assertTrue(result['rollback_verified'])
            from ru_routes.agent.transition_journal import history
            states = [x['state'] for x in history(journal)['entries']]
            self.assertEqual(states, ['baseline_verified', 'apply_in_progress',
                                      'target_verified', 'rollback_in_progress',
                                      'baseline_restored'])
            self.assertEqual(len([x for x in commands if x[0] == 'configure']), 2)

    def test_second_cycle_refused_without_io(self):
        with tempfile.TemporaryDirectory() as d:
            storage, journal = self.fixture(Path(d))
            from ru_routes.agent.transition_journal import append
            append(journal, txid=TXID, release_id=RID, role='backup',
                   state='baseline_verified', pid=7427,
                   baseline_sha256=SHA, target_sha256=SHA)
            with patch('ru_routes.agent.managed_cycle.audit') as audit:
                with self.assertRaisesRegex(ValueError, 'already exists'):
                    cycle(storage, TXID, 'backup', RID,
                          '/tmp/agent', '/tmp/runtime', journal)
                audit.assert_not_called()

    def test_failed_target_attempts_rollback(self):
        with tempfile.TemporaryDirectory() as d:
            storage, journal = self.fixture(Path(d))
            configured = []
            def configure(birdc, socket, path):
                configured.append(path)
            with patch('ru_routes.agent.managed_cycle.audit',
                       return_value={'control_socket': '/run/managed.ctl',
                                     'managed_pid': 7427}), \
                 patch('ru_routes.agent.managed_cycle.verify_bundle',
                       return_value=self.mocks()), \
                 patch('ru_routes.agent.managed_cycle._configure',
                       side_effect=configure), \
                 patch('ru_routes.agent.managed_cycle._check_until',
                       side_effect=[RuntimeError('routes did not converge'), True]), \
                 patch('ru_routes.agent.managed_cycle.audit_process_identity',
                       return_value=True):
                with self.assertRaisesRegex(RuntimeError, 'did not converge'):
                    cycle(storage, TXID, 'backup', RID, '/tmp/agent',
                          '/tmp/runtime', journal)
            self.assertEqual(len(configured), 2)
            from ru_routes.agent.transition_journal import history
            self.assertEqual(history(journal)['status'], 'baseline_restored')

    def test_failed_rollback_remains_unresolved(self):
        with tempfile.TemporaryDirectory() as d:
            storage, journal = self.fixture(Path(d))
            with patch('ru_routes.agent.managed_cycle.audit',
                       return_value={'control_socket': '/run/managed.ctl',
                                     'managed_pid': 7427}), \
                 patch('ru_routes.agent.managed_cycle.verify_bundle',
                       return_value=self.mocks()), \
                 patch('ru_routes.agent.managed_cycle._configure',
                       side_effect=RuntimeError('socket down')), \
                 patch('ru_routes.agent.managed_cycle.audit_process_identity',
                       return_value=False):
                with self.assertRaisesRegex(RuntimeError, 'socket down'):
                    cycle(storage, TXID, 'backup', RID, '/tmp/agent',
                          '/tmp/runtime', journal)
            from ru_routes.agent.transition_journal import history
            self.assertEqual(history(journal)['status'], 'rollback_in_progress')


if __name__ == '__main__':
    unittest.main()
