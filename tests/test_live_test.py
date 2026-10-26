import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ru_routes.agent.live_test import validate_tables, run_isolated


class LiveTestTests(unittest.TestCase):
    def fixture(self):
        net = '8.8.8.0/24'
        digest = hashlib.sha256((net + '\n').encode()).hexdigest()
        return {'demo': {'prefix_count': 1, 'prefix_sha256': digest}}

    def listing(self, net='8.8.8.0/24', proto='st_demo'):
        return ('BIRD 2.17.5 ready.\nTable rt_demo:\n'
                f'{net} blackhole [{proto} 2026-10-26] * (200)\n')

    def test_table_verifies_exact_prefixes(self):
        result = validate_tables(self.fixture(), lambda table: self.listing())
        self.assertEqual(result['demo']['prefix_count'], 1)

    def test_same_count_wrong_prefix_rejected(self):
        with self.assertRaisesRegex(ValueError, 'differs'):
            validate_tables(self.fixture(), lambda table: self.listing('9.9.9.0/24'))

    def test_wrong_protocol_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unexpected route attributes'):
            validate_tables(self.fixture(), lambda table: self.listing(proto='another'))

    def test_missing_table_rejected(self):
        with self.assertRaisesRegex(ValueError, 'missing'):
            validate_tables(self.fixture(), lambda table: 'BIRD 2.17.5 ready.\n')

    def test_runner_stops_child_after_verified_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Mock()
            proc.poll.return_value = None
            proc.wait.return_value = 0
            def create_config(inbox, rid, role, output):
                Path(output).write_text('router id 127.0.0.1;\n')
            def query(cmd, **kwargs):
                if cmd[1] == '-p':
                    return Mock(returncode=0)
                return Mock(returncode=0, stdout=self.listing(), stderr='')
            with patch('ru_routes.agent.live_test.inspect', return_value={'routers': self.fixture()}), \
                 patch('ru_routes.agent.live_test.make_isolated', side_effect=create_config), \
                 patch('ru_routes.agent.live_test.subprocess.run', side_effect=query) as run, \
                 patch('ru_routes.agent.live_test.subprocess.Popen', return_value=proc), \
                 patch('pathlib.Path.exists', return_value=True):
                result = run_isolated(tmp, 'a' * 64, 'primary')
            self.assertEqual(result['status'], 'isolated_live_routes_verified')
            self.assertEqual(result['routers']['demo']['prefix_count'], 1)
            self.assertTrue(proc.terminate.called)
            args = run.call_args_list[1].args[0]
            self.assertIn('-r', args)
            self.assertIn('-s', args)

    def test_runner_stops_child_on_failed_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Mock()
            proc.poll.return_value = None
            proc.wait.return_value = 0
            def create_config(inbox, rid, role, output):
                Path(output).write_text('router id 127.0.0.1;\n')
            def query(cmd, **kwargs):
                if cmd[1] == '-p':
                    return Mock(returncode=0)
                return Mock(returncode=1, stdout='', stderr='denied')
            with patch('ru_routes.agent.live_test.inspect', return_value={'routers': self.fixture()}), \
                 patch('ru_routes.agent.live_test.make_isolated', side_effect=create_config), \
                 patch('ru_routes.agent.live_test.subprocess.run', side_effect=query), \
                 patch('ru_routes.agent.live_test.subprocess.Popen', return_value=proc), \
                 patch('pathlib.Path.exists', return_value=True), \
                 patch('ru_routes.agent.live_test.time.monotonic', side_effect=[0, 0, 20]):
                with self.assertRaises(RuntimeError):
                    run_isolated(tmp, 'a' * 64, 'backup')
            proc.terminate.assert_called_once()


if __name__ == '__main__':
    unittest.main()
