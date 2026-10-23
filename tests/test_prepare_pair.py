import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_release_protocol import ReleaseTests
from ru_routes.manager.release import build_release
from ru_routes.manager.prepare import prepare_pair


class PreparePairTests(unittest.TestCase):
    def fixture(self, root):
        release = root / 'release'
        build_release(ReleaseTests().fixture(root), release)
        return release

    def test_both_prepared_same_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = self.fixture(root)
            result = prepare_pair(release, root / 'a', root / 'b')
            self.assertEqual(result['status'], 'prepared_not_activated')
            self.assertEqual(result['primary'], 'prepared')
            self.assertEqual(result['backup'], 'prepared')
            for name in ('a', 'b'):
                self.assertTrue((root / name / result['release_id'] / 'bird.conf').is_file())

    def test_tampered_backup_preflight_prevents_primary_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = self.fixture(root)
            (release / 'backup.bird.conf').write_text('unexpected')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                prepare_pair(release, root / 'a', root / 'b')
            self.assertFalse((root / 'a').exists())

    def test_same_inbox_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = self.fixture(root)
            with self.assertRaisesRegex(ValueError, 'distinct'):
                prepare_pair(release, root / 'a', root / 'a')

    def test_duplicate_refused_without_touching_other_agent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = self.fixture(root)
            prepare_pair(release, root / 'a', root / 'b')
            with self.assertRaises(FileExistsError):
                prepare_pair(release, root / 'a', root / 'c')
            self.assertFalse((root / 'c').exists())

    def test_second_failure_removes_first_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release = self.fixture(root)
            import ru_routes.manager.prepare as mod
            real = mod.stage_release
            def failing(release_dir, role, inbox):
                if role == 'backup':
                    raise OSError('simulated outage')
                return real(release_dir, role, inbox)
            with patch.object(mod, 'stage_release', side_effect=failing):
                with self.assertRaisesRegex(OSError, 'outage'):
                    prepare_pair(release, root / 'a', root / 'b')
            self.assertEqual(list((root / 'a').iterdir()), [])


if __name__ == '__main__':
    unittest.main()
