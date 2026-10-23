import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ru_routes.multigroup import parse_manual, discover_groups, load_config, stage

CONFIG = """servers:
  primary: {local_ip: 192.168.11.13, local_as: 64002}
  backup: {local_ip: 192.168.11.14, local_as: 64002}
routers:
  one: {neighbor: 192.168.11.1, remote_as: 64018, groups: [ru, manual]}
  two: {neighbor: 192.168.11.2, remote_as: 64018, groups: [meta]}
  three: {neighbor: 192.168.11.3, remote_as: 64018, groups: [all]}
"""

class MultiGroupTests(unittest.TestCase):
    def test_manual_single_host(self):
        self.assertEqual([str(n) for n in parse_manual('8.8.8.8\n9.9.9.0/24\n')], ['8.8.8.8/32', '9.9.9.0/24'])
    def test_invalid_manual(self):
        for source in ['192.168.1.1', 'garbage', '8.8.8.0/33']:
            with self.assertRaises(ValueError): parse_manual(source)
    def test_stage_router_isolation_and_all(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t); src = root/'sources'; src.mkdir()
            (src/'ru.txt').write_text('https://example.org/ru\n')
            (src/'meta.txt').write_text('https://example.org/meta\n')
            (src/'manual.txt').write_text('1.1.1.1\n')
            cfg = root/'config.yaml'; cfg.write_text(CONFIG)
            with patch('ru_routes.multigroup.fetch_sources', side_effect=[ [('https://example.org/meta', b'9.9.9.0/24\n', [])], [('https://example.org/ru', b'8.8.8.0/24\n', [])] ]):
                result=stage(cfg,src,root/'out')
            p=result['servers']['primary']['routers']; b=result['servers']['backup']['routers']
            self.assertEqual([p[k]['prefix_count'] for k in ('one','two','three')], [2,1,3])
            self.assertEqual(p,b)
            content=(root/'out'/'primary.bird.conf').read_text()
            self.assertIn('neighbor 192.168.11.3 as 64018;',content)
            self.assertNotIn('protocol kernel',content)
            self.assertIn('import none;',content)
            self.assertIn('staged_not_applied', (root/'out'/'manifest.json').read_text())
    def test_missing_group_fail_closed(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t); src=root/'sources'; src.mkdir(); (src/'manual.txt').write_text('1.1.1.1\n')
            cfg=root/'config.yaml';cfg.write_text(CONFIG)
            with self.assertRaises(ValueError):stage(cfg,src,root/'out')
            self.assertFalse((root/'out').exists())
    def test_config_rejects_unknown_fields(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'c.yaml'; p.write_text(CONFIG+'unexpected: true\n')
            with self.assertRaises(ValueError):load_config(p)
    def test_dynamic_google_group(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t); root.joinpath('google.txt').write_text('https://example.org/google\n')
            root.joinpath('manual.txt').write_text('8.8.4.4\n')
            with patch('ru_routes.multigroup.fetch_sources',return_value=[('https://example.org/google', b'8.8.8.0/24\n', [])]):
                self.assertEqual(set(discover_groups(root)), {'google','manual'})

if __name__ == '__main__':unittest.main()
