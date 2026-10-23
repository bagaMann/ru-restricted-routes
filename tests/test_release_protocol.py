import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from ru_routes.manager.release import build_release
from ru_routes.agent.stage import stage_release


class ReleaseTests(unittest.TestCase):
    def fixture(self, root):
        stage = root / 'stage'
        stage.mkdir()
        servers = {}
        common = {'demo': {'groups': ['ru'], 'prefix_count': 1,
                            'prefix_sha256': 'a' * 64}}
        for role in ('primary', 'backup'):
            data = f'# {role}\n'.encode()
            (stage / f'{role}.bird.conf').write_bytes(data)
            servers[role] = {
                'config_sha256': hashlib.sha256(data).hexdigest(),
                'routers': common}
        (stage / 'manifest.json').write_text(json.dumps(
            {'status': 'staged_not_applied', 'servers': servers}))
        return stage

    def test_two_role_release_and_stage(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            release = root / 'release'
            manifest = build_release(self.fixture(root), release)
            self.assertEqual(len(manifest['release_id']), 64)
            self.assertEqual(stage_release(release, 'primary', root / 'inbox'),
                             manifest['release_id'])
            self.assertTrue((root / 'inbox' / manifest['release_id'] / 'bird.conf').exists())
            with self.assertRaises(FileExistsError):
                stage_release(release, 'primary', root / 'inbox')

    def test_tampered_stage_config_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            stage = self.fixture(root)
            (stage / 'backup.bird.conf').write_text('tampered')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                build_release(stage, root / 'release')
            self.assertFalse((root / 'release').exists())

    def test_different_prefix_sets_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            stage = self.fixture(root)
            obj = json.loads((stage / 'manifest.json').read_text())
            obj['servers']['backup']['routers']['demo']['prefix_sha256'] = 'b' * 64
            # JSON deserialization ensures each role gets its own nested copy.
            (stage / 'manifest.json').write_text(json.dumps(obj))
            with self.assertRaisesRegex(ValueError, 'selections differ'):
                build_release(stage, root / 'release')

    def test_tampered_release_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            release = root / 'release'
            build_release(self.fixture(root), release)
            (release / 'primary.bird.conf').write_text('tampered')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                stage_release(release, 'primary', root / 'inbox')
            self.assertFalse((root / 'inbox').exists())

    def test_tampered_manifest_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            release = root / 'release'
            build_release(self.fixture(root), release)
            obj = json.loads((release / 'release.json').read_text())
            obj['servers']['primary']['routers']['demo']['prefix_count'] = 999
            (release / 'release.json').write_text(json.dumps(obj))
            with self.assertRaisesRegex(ValueError, 'manifest checksum'):
                stage_release(release, 'primary', root / 'inbox')

    def test_unknown_role_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unknown role'):
            stage_release('/tmp/unused', 'third', '/tmp/unused')


if __name__ == '__main__':
    unittest.main()
