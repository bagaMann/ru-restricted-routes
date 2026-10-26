import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ru_routes.agent.managed_runtime import audit

RID = 'a' * 64
TXID = 'tx-' + 'b' * 64


class ManagedRuntimeTests(unittest.TestCase):
    def context(self, root):
        runtime = root / 'runtime'
        runtime.mkdir()
        sock = runtime / 'managed.ctl'
        pid = runtime / 'managed.pid'
        pid.write_text('12345\n')
        args = ['bird', '-f', '-c', '/fake/empty.conf', '-s',
                str(sock), '-P', str(pid)]
        spec = {'argv': args, 'control_socket': str(sock)}
        return runtime, spec

    def invoke(self, root, *, proc_args=None, pid='12345', output=None):
        runtime, spec = self.context(root)
        (runtime / 'managed.pid').write_text(pid)
        binary = '\0'.join(proc_args or spec['argv']).encode() + b'\0'
        original = Path.read_bytes

        def fake_read(path):
            if str(path) == '/proc/12345/cmdline':
                return binary
            return original(path)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.bind(str(runtime / 'managed.ctl'))
            with patch('ru_routes.agent.managed_runtime.startup_spec',
                       return_value=spec), \
                 patch('ru_routes.agent.recovery_bundle.verify_bundle',
                       return_value={'baseline_sha256': 'c' * 64,
                                     'routers': {'demo': {}}}), \
                 patch.object(Path, 'read_bytes', fake_read), \
                 patch('ru_routes.agent.managed_runtime.subprocess.run',
                       return_value=Mock(
                           returncode=0,
                           stdout=output if output is not None
                           else 'BIRD 2.17.5 ready.\nAccess restricted\nTable rt_demo:\n',
                           stderr='')):
                return audit('/tmp/storage', TXID, 'backup', RID, '/tmp/state', runtime)

    def test_valid_empty_managed_runtime(self):
        with tempfile.TemporaryDirectory() as d:
            result = self.invoke(Path(d))
            self.assertEqual(result['status'], 'managed_runtime_verified_empty_not_active')
            self.assertFalse(result['activation_performed'])
            self.assertEqual(result['routers']['demo']['prefix_count'], 0)

    def test_reject_wrong_pid_arguments(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError, 'not the expected'):
                self.invoke(Path(d), proc_args=['bird', '-f', '-c', '/etc/bird/bird.conf'])

    def test_reject_nonempty_table(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError, 'not empty'):
                self.invoke(Path(d), output='BIRD 2.17.5 ready.\nTable rt_demo:\n'
                                            '8.8.8.8/32 blackhole [st_demo 18:00] * (200)\n')

    def test_reject_invalid_pid(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError, 'invalid managed BIRD PID'):
                self.invoke(Path(d), pid='../../12345')

    def test_reject_wrong_table_name(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError, 'not empty'):
                self.invoke(Path(d), output='BIRD 2.17.5 ready.\nTable rt_other:\n')


if __name__ == '__main__':
    unittest.main()
