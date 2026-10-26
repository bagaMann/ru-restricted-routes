import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ru_routes.manager.review_receipts import review_pair

RID = 'a' * 64


class ReviewReceiptsTests(unittest.TestCase):
    def setup_files(self, root):
        release = root / 'release'
        release.mkdir()
        state = root / 'state'
        state.mkdir()
        routers = {'demo': {'groups': ['ru'], 'prefix_count': 1,
                            'prefix_sha256': 'c' * 64}}
        manifest = {'servers': {
            'primary': {'config_sha256': '1' * 64, 'routers': routers},
            'backup': {'config_sha256': '2' * 64, 'routers': routers},
        }}
        (release / 'release.json').write_text(json.dumps(manifest))
        paths = {}
        for role in ('primary', 'backup'):
            obj = {'schema': 1, 'status': 'preflight_verified_not_activated',
                   'role': role, 'release_id': RID,
                   'config_sha256': manifest['servers'][role]['config_sha256'],
                   'routers': {'demo': {'prefix_count': 1, 'prefix_sha256': 'c' * 64}},
                   'verified_at_utc': '2026-10-26T09:43:01+00:00'}
            file = root / f'{role}.json'
            file.write_text(json.dumps(obj))
            paths[role] = file
        return release, state, paths

    def run_review(self, release, state, paths):
        journal = {'transactions': [{'id': 'tx-' + 'f' * 64,
                                     'release_id': RID,
                                     'state': 'intent_prepared_not_active'}]}
        with patch('ru_routes.manager.review_receipts.verify_release', return_value=RID), \
             patch('ru_routes.manager.review_receipts.journal_status', return_value=journal):
            return review_pair(release, state, paths['primary'], paths['backup'])

    def test_matching_pair(self):
        with tempfile.TemporaryDirectory() as d:
            release, state, paths = self.setup_files(Path(d))
            result = self.run_review(release, state, paths)
            self.assertEqual(result['status'],
                             'receipt_contents_match_not_authenticated_not_active')

    def test_wrong_backup_route_digest(self):
        with tempfile.TemporaryDirectory() as d:
            release, state, paths = self.setup_files(Path(d))
            obj = json.loads(paths['backup'].read_text())
            obj['routers']['demo']['prefix_sha256'] = '0' * 64
            paths['backup'].write_text(json.dumps(obj))
            with self.assertRaisesRegex(ValueError, 'backup: receipt'):
                self.run_review(release, state, paths)

    def test_swapped_roles_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            release, state, paths = self.setup_files(Path(d))
            with self.assertRaisesRegex(ValueError, 'primary: receipt'):
                self.run_review(release, state, {
                    'primary': paths['backup'], 'backup': paths['primary']})

    def test_symlinked_receipt_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            release, state, paths = self.setup_files(Path(d))
            link = Path(d) / 'link.json'
            link.symlink_to(paths['primary'])
            with self.assertRaisesRegex(ValueError, 'symlinked'):
                self.run_review(release, state, {'primary': link, 'backup': paths['backup']})

    def test_wrong_time_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            release, state, paths = self.setup_files(Path(d))
            obj = json.loads(paths['primary'].read_text())
            obj['verified_at_utc'] = '2026-10-26T09:43:01'
            paths['primary'].write_text(json.dumps(obj))
            with self.assertRaisesRegex(ValueError, 'timestamp'):
                self.run_review(release, state, paths)


if __name__ == '__main__':
    unittest.main()
