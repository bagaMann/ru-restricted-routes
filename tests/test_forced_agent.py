import io
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from ru_routes.agent.forced import handle
from ru_routes.agent.remote import MAX_MESSAGE_BYTES


class ForcedAgentTests(unittest.TestCase):
    def test_fixed_role_and_inbox(self):
        with patch('ru_routes.agent.forced.receive', return_value={'status': 'prepared_not_activated'}) as receive:
            result = handle(io.BytesIO(b'hello'), 'primary', '/tmp/agent-primary')
        self.assertEqual(result['status'], 'prepared_not_activated')
        receive.assert_called_once_with(b'hello', 'primary', Path('/tmp/agent-primary'))

    def test_original_ssh_command_never_used(self):
        with patch.dict(os.environ, {'SSH_ORIGINAL_COMMAND': 'rm -rf /'}), patch(
            'ru_routes.agent.forced.receive', return_value={'status': 'prepared_not_activated'}
        ) as receive:
            handle(io.BytesIO(b'data'), 'backup', '/tmp/agent-backup')
        self.assertEqual(receive.call_args.args[1:], ('backup', Path('/tmp/agent-backup')))

    def test_invalid_role_fails_before_reading(self):
        stream = io.BytesIO(b'data')
        with self.assertRaisesRegex(ValueError, 'role'):
            handle(stream, 'other', '/tmp/inbox')
        self.assertEqual(stream.tell(), 0)

    def test_request_is_size_bounded(self):
        stream = io.BytesIO(b'x' * (MAX_MESSAGE_BYTES + 100))
        with patch('ru_routes.agent.forced.receive', side_effect=ValueError('transfer exceeds 8 MB')):
            with self.assertRaises(ValueError):
                handle(stream, 'primary', '/tmp/inbox')
        self.assertEqual(stream.tell(), MAX_MESSAGE_BYTES + 1)


if __name__ == '__main__':
    unittest.main()
