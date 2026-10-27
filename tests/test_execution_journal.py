import json
import tempfile
import unittest
from pathlib import Path

from ru_routes.agent.execution_journal import append, inspect

TX = 'tx-' + 'b' * 64
RID = 'a' * 64
SHA = 'c' * 64


def advance(directory, state, **override):
    item = dict(txid=TX, release_id=RID, role='backup',
                baseline_sha256=SHA, target_sha256=SHA,
                managed_pid=7816, state=state)
    item.update(override)
    return append(directory, **item)


class ExecutionJournalTests(unittest.TestCase):
    def test_empty_status_is_not_authorized(self):
        with tempfile.TemporaryDirectory() as d:
            output = inspect(Path(d) / 'new')
            self.assertEqual(output['status'], 'no_execution')
            self.assertFalse(output['activation_authorized'])

    def test_happy_path_restoration_only(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'journal'
            for state in ('execution_prepared', 'apply_in_progress',
                          'target_verified', 'rollback_in_progress',
                          'baseline_restored'):
                advance(p, state)
            report = inspect(p)
            self.assertEqual(report['status'], 'baseline_restored')
            self.assertFalse(report['unresolved'])
            self.assertFalse(report['activation_authorized'])

    def test_crash_between_write_and_apply_is_unresolved(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'journal'
            advance(p, 'execution_prepared')
            advance(p, 'apply_in_progress')
            self.assertTrue(inspect(p)['unresolved'])
            with self.assertRaisesRegex(ValueError, 'illegal execution transition'):
                advance(p, 'apply_in_progress')

    def test_target_verified_is_still_unresolved(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'journal'
            for state in ('execution_prepared', 'apply_in_progress', 'target_verified'):
                advance(p, state)
            self.assertTrue(inspect(p)['unresolved'])

    def test_crash_during_rollback_is_unresolved(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'journal'
            for state in ('execution_prepared', 'apply_in_progress',
                          'rollback_in_progress'):
                advance(p, state)
            self.assertTrue(inspect(p)['unresolved'])

    def test_replay_after_terminal_restoration_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'journal'
            for state in ('execution_prepared', 'apply_in_progress',
                          'rollback_in_progress', 'baseline_restored'):
                advance(p, state)
            with self.assertRaisesRegex(ValueError, 'illegal execution transition'):
                advance(p, 'execution_prepared')

    def test_manual_review_is_terminal(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'journal'
            advance(p, 'execution_prepared')
            advance(p, 'manual_review_required')
            self.assertTrue(inspect(p)['unresolved'])
            with self.assertRaisesRegex(ValueError, 'illegal execution transition'):
                advance(p, 'apply_in_progress')

    def test_mismatching_pid_rejected_without_writing(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'journal'
            advance(p, 'execution_prepared')
            with self.assertRaisesRegex(ValueError, 'identity changed'):
                advance(p, 'apply_in_progress', managed_pid=9999)
            self.assertEqual(len(inspect(p)['entries']), 1)

    def test_corruption_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'journal'
            advance(p, 'execution_prepared')
            file = p / 'execution.json'
            content = json.loads(file.read_text())
            content['entries'][0]['managed_pid'] = 9
            file.write_text(json.dumps(content))
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                inspect(p)
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                advance(p, 'apply_in_progress')

    def test_symlink_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'journal'
            p.symlink_to('/tmp', target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'nonsymlink|symlink'):
                inspect(p)


if __name__ == '__main__':
    unittest.main()
