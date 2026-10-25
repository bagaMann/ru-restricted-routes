import json
import tempfile
import unittest
from pathlib import Path

from tests import test_release_protocol as fixtures
from ru_routes.manager.release import build_release
from ru_routes.manager.prepare import prepare_pair
from ru_routes.manager.inspect import inspect, inspect_pair


class InspectPreparedTests(unittest.TestCase):
    def fixture(self, root):
        release = root / 'release'
        build_release(fixtures.ReleaseTests().fixture(root), release)
        result = prepare_pair(release, root / 'primary', root / 'backup')
        return result['release_id']

    def test_both_agents_verified(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            ident=self.fixture(root)
            result=inspect_pair(root/'primary', root/'backup', ident)
            self.assertEqual(result['status'], 'both_prepared_verified_not_active')
            self.assertEqual(result['release_id'], ident)

    def test_staged_config_tamper_is_detected(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            ident=self.fixture(root)
            (root/'backup'/ident/'bird.conf').write_text('tampered')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                inspect_pair(root/'primary', root/'backup', ident)

    def test_manifest_tamper_is_detected(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            ident=self.fixture(root)
            path=root/'primary'/ident/'release.json'
            data=json.loads(path.read_text())
            data['servers']['primary']['routers']['demo']['prefix_count']=42
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'identity'):
                inspect(root/'primary', ident, 'primary')

    def test_bad_release_id_rejected(self):
        with self.assertRaisesRegex(ValueError, 'identifier'):
            inspect('/tmp/example', '../escape', 'primary')

    def test_symlink_config_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            ident=self.fixture(root)
            target=root/'primary'/ident/'bird.conf'
            other=root/'other.conf'
            target.rename(other)
            target.symlink_to(other)
            with self.assertRaisesRegex(ValueError, 'symlink'):
                inspect(root/'primary', ident, 'primary')


if __name__ == '__main__':
    unittest.main()
