"""Read-only manager review of two supplied agent preflight receipts.

Receipt files must be transferred through an authenticated channel separately.
This checks their CONTENT, not their origin; it never changes transaction state.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path

from ru_routes.agent.stage import verify_release
from ru_routes.manager.transaction_journal import journal_status


def _receipt_file(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 500_000:
        raise ValueError('receipt missing, symlinked, or oversized')
    obj = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(obj, dict):
        raise ValueError('receipt must be an object')
    return obj


def review_pair(release_dir, journal_dir, primary_receipt, backup_receipt):
    identities = [verify_release(release_dir, role) for role in ('primary', 'backup')]
    if identities[0] != identities[1]:
        raise ValueError('release roles differ')
    release_id = identities[0]
    journal = journal_status(journal_dir)
    pending = [tx for tx in journal['transactions']
               if tx['state'] == 'intent_prepared_not_active']
    if len(pending) != 1 or pending[0]['release_id'] != release_id:
        raise ValueError('no matching pending manager transaction')
    release = json.loads((Path(release_dir) / 'release.json').read_text(encoding='utf-8'))
    evidence = {}
    for role, location in (('primary', primary_receipt), ('backup', backup_receipt)):
        obj = _receipt_file(location)
        if set(obj) != {'schema', 'status', 'release_id', 'role', 'config_sha256',
                        'routers', 'verified_at_utc'}:
            raise ValueError(f'{role}: invalid receipt fields')
        expected = release['servers'][role]
        expected_routes = {name: {'prefix_count': v['prefix_count'],
                                  'prefix_sha256': v['prefix_sha256']}
                           for name, v in expected['routers'].items()}
        if (obj['schema'] != 1 or
                obj['status'] != 'preflight_verified_not_activated' or
                obj['role'] != role or obj['release_id'] != release_id or
                obj['config_sha256'] != expected['config_sha256'] or
                obj['routers'] != expected_routes):
            raise ValueError(f'{role}: receipt does not match expected release')
        try:
            dt = datetime.fromisoformat(obj['verified_at_utc'])
            if dt.tzinfo is None or dt.utcoffset().total_seconds() != 0:
                raise ValueError('non-UTC timestamp')
        except (TypeError, ValueError) as exc:
            raise ValueError(f'{role}: invalid receipt timestamp') from exc
        evidence[role] = {'verified_at_utc': obj['verified_at_utc'],
                          'config_sha256': obj['config_sha256']}
    return {'status': 'receipt_contents_match_not_authenticated_not_active',
            'txid': pending[0]['id'], 'release_id': release_id,
            'agents': evidence}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--release-dir', required=True)
    p.add_argument('--journal-dir', required=True)
    p.add_argument('--primary-receipt', required=True)
    p.add_argument('--backup-receipt', required=True)
    a = p.parse_args()
    print(json.dumps(review_pair(a.release_dir, a.journal_dir,
                                 a.primary_receipt, a.backup_receipt), indent=2))


if __name__ == '__main__':
    main()
