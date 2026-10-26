import json
import tempfile
import unittest
from pathlib import Path

from ru_routes.agent.transition_journal import append, history

RID = 'a' * 64
TX = 'tx-' + 'b' * 64
BASE = 'c' * 64
TARGET = 'd' * 64


class TransitionJournalTests(unittest.TestCase):
    def add(self, root, state, **changes):
        kwargs = dict(txid=TX, release_id=RID, role='backup', state=state,
                      pid=7427, baseline_sha256=BASE, target_sha256=TARGET)
        kwargs.update(changes)
        return append(root, **kwargs)

    def test_full_happy_sequence(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'journal'
            self.assertEqual(history(root)['status'], 'no_transitions')
            for state in ('baseline_verified', 'apply_in_progress',
                          'target_verified', 'rollback_in_progress',
                          'baseline_restored'):
                self.add(root, state)
                self.assertEqual(history(root)['status'], state)
            self.assertEqual(len(history(root)['entries']), 5)

    def test_failure_requires_explicit_rollback(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'journal'
            self.add(root, 'baseline_verified')
            self.add(root, 'apply_in_progress')
            with self.assertRaisesRegex(ValueError, 'illegal transition'):
                self.add(root, 'baseline_verified')
            self.add(root, 'rollback_in_progress')
            self.add(root, 'baseline_restored')

    def test_cannot_claim_target_without_write_ahead(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError, 'illegal transition'):
                self.add(Path(d), 'target_verified')

    def test_cannot_start_second_transaction(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'journal'
            self.add(root, 'baseline_verified')
            with self.assertRaisesRegex(ValueError, 'changed'):
                self.add(root, 'apply_in_progress', txid='tx-' + 'f' * 64)

    def test_detects_corrupted_history(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'journal'
            self.add(root, 'baseline_verified')
            path = root / 'transitions.json'
            document = json.loads(path.read_text())
            document['entries'][0]['state'] = 'target_verified'
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                history(root)

    def test_missing_state_is_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'missing'
            self.assertEqual(history(root)['entries'], [])
            self.assertFalse(root.exists())

    def test_reject_invalid_pid(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError, 'invalid transition'):
                self.add(Path(d), 'baseline_verified', pid=0)


if __name__ == '__main__':
    unittest.main()
