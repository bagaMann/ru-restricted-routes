import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ru_routes.manager.transaction_journal import initialize, abort, journal_status

RID = 'a' * 64


class TransactionJournalTests(unittest.TestCase):
    def test_initialize_and_status(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / 'journal'
            with patch('ru_routes.manager.transaction_journal.verify_release', return_value=RID):
                tx = initialize(state, '/tmp/fake-release')
            self.assertEqual(tx['state'], 'intent_prepared_not_active')
            status = journal_status(state)
            self.assertEqual(status['transactions'][0], tx)
            self.assertEqual(status['status'], 'ready')

    def test_refuse_second_pending_transaction(self):
        with tempfile.TemporaryDirectory() as d:
            with patch('ru_routes.manager.transaction_journal.verify_release', return_value=RID):
                initialize(Path(d), '/tmp/release')
                with self.assertRaisesRegex(ValueError, 'pending intent'):
                    initialize(Path(d), '/tmp/release')
            self.assertEqual(len(journal_status(d)['transactions']), 1)

    def test_abort_explicit_then_new_intent(self):
        with tempfile.TemporaryDirectory() as d:
            with patch('ru_routes.manager.transaction_journal.verify_release', return_value=RID):
                old = initialize(d, '/tmp/release')
                aborted = abort(d, old['id'])
                new = initialize(d, '/tmp/release')
            self.assertEqual(aborted['state'], 'aborted_not_active')
            self.assertNotEqual(old['id'], new['id'])
            self.assertEqual(len(journal_status(d)['transactions']), 2)

    def test_wrong_transaction_abort_refused(self):
        with tempfile.TemporaryDirectory() as d:
            with patch('ru_routes.manager.transaction_journal.verify_release', return_value=RID):
                initialize(d, '/tmp/release')
            with self.assertRaisesRegex(ValueError, 'missing or not pending'):
                abort(d, 'tx-' + 'f' * 64)

    def test_detect_tampered_record(self):
        with tempfile.TemporaryDirectory() as d:
            with patch('ru_routes.manager.transaction_journal.verify_release', return_value=RID):
                initialize(d, '/tmp/release')
            file = Path(d) / 'journal.json'
            obj = json.loads(file.read_text())
            obj['transactions'][0]['release_id'] = 'e' * 64
            file.write_text(json.dumps(obj))
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                journal_status(d)

    def test_bad_two_role_release_blocks_journal(self):
        with tempfile.TemporaryDirectory() as d:
            with patch('ru_routes.manager.transaction_journal.verify_release',
                       side_effect=[RID, 'b' * 64]):
                with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                    initialize(Path(d) / 'journal', '/tmp/release')
            self.assertFalse((Path(d) / 'journal').exists())

    def test_reject_bird_and_symlink_directory(self):
        with self.assertRaisesRegex(ValueError, 'journal must not'):
            journal_status('/etc/bird/manager')
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'real').mkdir()
            (root / 'alias').symlink_to(root / 'real')
            with self.assertRaisesRegex(ValueError, 'nonsymlink'):
                journal_status(root / 'alias')

    def test_status_empty_does_not_create_directory(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / 'absent'
            result = journal_status(state)
            self.assertEqual(result['status'], 'empty')
            self.assertFalse(state.exists())


if __name__ == '__main__':
    unittest.main()
