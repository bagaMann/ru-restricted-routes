import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ru_routes.agent.intent import prepare, status, abort

RID = 'a' * 64
TXID = 'tx-' + 'b' * 64
RECEIPT = {'status': 'preflight_receipt_verified_not_active',
           'release_id': RID, 'role': 'backup'}


class AgentIntentTests(unittest.TestCase):
    def test_prepare_and_read(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / 'agent'
            with patch('ru_routes.agent.intent.inspect_receipt', return_value=RECEIPT):
                saved = prepare('/tmp/inbox', RID, 'backup', '/tmp/receipts', state, TXID)
            self.assertEqual(saved, status(state))
            self.assertEqual(saved['status'], 'agent_prepared_not_active')
            self.assertIsNone(saved['closed_at_utc'])

    def test_second_prepare_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / 'agent'
            with patch('ru_routes.agent.intent.inspect_receipt', return_value=RECEIPT):
                prepare('/tmp/inbox', RID, 'backup', '/tmp/receipts', state, TXID)
                with self.assertRaisesRegex(ValueError, 'already exists'):
                    prepare('/tmp/inbox', RID, 'backup', '/tmp/receipts', state, TXID)

    def test_abort_closes_only_matching_intent(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / 'agent'
            with patch('ru_routes.agent.intent.inspect_receipt', return_value=RECEIPT):
                prepare('/tmp/inbox', RID, 'backup', '/tmp/receipts', state, TXID)
            with self.assertRaisesRegex(ValueError, 'no matching'):
                abort(state, 'tx-' + 'c' * 64)
            result = abort(state, TXID)
            self.assertEqual(result['status'], 'agent_aborted_not_active')
            self.assertEqual(status(state), result)
            with self.assertRaisesRegex(ValueError, 'no matching'):
                abort(state, TXID)

    def test_invalid_receipt_never_records_intent(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / 'agent'
            with patch('ru_routes.agent.intent.inspect_receipt',
                       return_value={'status': 'unverified'}):
                with self.assertRaisesRegex(ValueError, 'unverified'):
                    prepare('/tmp/inbox', RID, 'backup', '/tmp/receipts', state, TXID)
            self.assertFalse(state.exists())

    def test_detect_tampered_state(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / 'agent'
            with patch('ru_routes.agent.intent.inspect_receipt', return_value=RECEIPT):
                prepare('/tmp/inbox', RID, 'backup', '/tmp/receipts', state, TXID)
            file = state / 'agent-state.json'
            obj = json.loads(file.read_text())
            obj['record']['release_id'] = 'f' * 64
            file.write_text(json.dumps(obj))
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                status(state)

    def test_status_empty_and_invalid_tx(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / 'agent'
            self.assertEqual(status(state), {'status': 'no_agent_intent'})
            self.assertFalse(state.exists())
            with self.assertRaisesRegex(ValueError, 'invalid transaction'):
                prepare('/tmp/inbox', RID, 'backup', '/tmp/receipts',
                        state, 'bad-id')


if __name__ == '__main__':
    unittest.main()
