import unittest
from unittest.mock import patch

from ru_routes.agent.sandbox_switch import (
    build_empty_baseline, verify_empty_listing, run_sandbox_switch,
)

CONFIG = """# isolated
router id 127.0.0.1;
ipv4 table rt_demo;
protocol static st_demo {
    ipv4 { table rt_demo; };
    route 8.8.8.8/32 blackhole;
    route 9.9.9.0/24 blackhole;
}
"""


class SandboxSwitchTests(unittest.TestCase):
    def test_empty_baseline_retains_tables_but_removes_routes(self):
        result = build_empty_baseline(CONFIG)
        self.assertIn('protocol static st_demo', result)
        self.assertIn('ipv4 table rt_demo;', result)
        self.assertNotIn('route 8.8.8.8', result)
        self.assertNotIn('route 9.9.9.0', result)

    def test_empty_baseline_rejects_bgp(self):
        with self.assertRaisesRegex(ValueError, 'network protocols'):
            build_empty_baseline(CONFIG + 'protocol bgp peer {}')

    def test_empty_baseline_rejects_unexpected_routes(self):
        with self.assertRaisesRegex(ValueError, 'unexpected route'):
            build_empty_baseline(CONFIG + '\nroute 1.1.1.1/32 via 10.0.0.1;')

    def test_empty_listing_accepts_restricted_banner(self):
        verify_empty_listing('BIRD 2.17.5 ready.\nAccess restricted\n'
                             'Table rt_demo:\n', 'rt_demo')

    def test_empty_listing_rejects_actual_route(self):
        with self.assertRaisesRegex(ValueError, 'not empty'):
            verify_empty_listing('BIRD 2.17.5 ready.\nTable rt_demo:\n'
                                 '8.8.8.8/32 blackhole [st_demo 12:31:16] * (200)\n',
                                 'rt_demo')

    def test_empty_listing_rejects_wrong_table(self):
        with self.assertRaisesRegex(ValueError, 'not empty'):
            verify_empty_listing('BIRD 2.17.5 ready.\nTable rt_other:\n', 'rt_demo')

    def test_sandbox_runner_refuses_invalid_staged_release(self):
        with patch('ru_routes.agent.sandbox_switch.inspect',
                   side_effect=ValueError('invalid release')):
            with self.assertRaisesRegex(ValueError, 'invalid release'):
                run_sandbox_switch('/tmp/inbox', 'a' * 64, 'primary')


if __name__ == '__main__':
    unittest.main()
