import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ru_routes.agent.isolated import make_isolated


class IsolatedTests(unittest.TestCase):
    def fixture(self, root, routes=None):
        rid = 'a' * 64
        folder = root / 'inbox' / rid
        folder.mkdir(parents=True)
        routes = routes or ['8.8.8.8/32', '9.9.9.0/24']
        prefix_text = ''.join(f'{n}\n' for n in routes)
        manifest = {
            'routers': {'demo': {'prefix_count': len(routes),
                                 'prefix_sha256': hashlib.sha256(prefix_text.encode()).hexdigest()}},
        }
        text = ('router id 10.0.0.1;\nipv4 table rt_demo;\n'
                'protocol static st_demo {\n    ipv4 { table rt_demo; };\n' +
                ''.join(f'    route {n} blackhole;\n' for n in routes) +
                '}\nprotocol bgp bgp_demo {\n    local 10.0.0.1 as 64002;\n'
                '    neighbor 10.0.0.2 as 64018;\n'
                '    ipv4 { table rt_demo; import none; export all; };\n}\n')
        (folder / 'bird.conf').write_text(text)
        return rid, manifest, folder

    def test_generates_only_static_no_bgp(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            rid, data, _ = self.fixture(root)
            dest = root / 'isolated.conf'
            with patch('ru_routes.agent.isolated.inspect', return_value=data):
                result = make_isolated(root / 'inbox', rid, 'primary', dest)
            config = dest.read_text()
            self.assertEqual(result['routers'], {'demo': 2})
            self.assertIn('route 8.8.8.8/32 blackhole;', config)
            self.assertNotIn('protocol bgp', config)
            self.assertNotIn('protocol kernel', config)
            self.assertNotIn('neighbor ', config)
            self.assertIn('router id 127.0.0.1;', config)

    def test_rejects_wrong_manifest_prefix_hash(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            rid, data, _ = self.fixture(root)
            data['routers']['demo']['prefix_sha256'] = '0' * 64
            dest = root / 'isolated.conf'
            with patch('ru_routes.agent.isolated.inspect', return_value=data):
                with self.assertRaisesRegex(ValueError, 'do not match'):
                    make_isolated(root / 'inbox', rid, 'backup', dest)
            self.assertFalse(dest.exists())

    def test_rejects_duplicate_routes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            rid, data, folder = self.fixture(root)
            file = folder / 'bird.conf'
            file.write_text(file.read_text().replace('route 9.9.9.0/24 blackhole;',
                'route 8.8.8.8/32 blackhole;'))
            with patch('ru_routes.agent.isolated.inspect', return_value=data):
                with self.assertRaisesRegex(ValueError, 'duplicate'):
                    make_isolated(root / 'inbox', rid, 'backup', root / 'isolated.conf')

    def test_rejects_existing_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            rid, data, _ = self.fixture(root)
            dest = root / 'isolated.conf'
            dest.write_text('protected')
            with patch('ru_routes.agent.isolated.inspect', return_value=data):
                with self.assertRaises(FileExistsError):
                    make_isolated(root / 'inbox', rid, 'primary', dest)
            self.assertEqual(dest.read_text(), 'protected')

    def test_rejects_missing_table(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            rid, data, folder = self.fixture(root)
            file = folder / 'bird.conf'
            file.write_text('router id 10.0.0.1;\n')
            with patch('ru_routes.agent.isolated.inspect', return_value=data):
                with self.assertRaisesRegex(ValueError, 'incomplete'):
                    make_isolated(root / 'inbox', rid, 'primary', root / 'isolated.conf')


if __name__ == '__main__':
    unittest.main()
