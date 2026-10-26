"""Read-only verification of persisted preflight receipts against staged releases.

A valid receipt is historical evidence, NOT a command to activate BIRD.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from ru_routes.agent.preflight import _safe_state_dir
from ru_routes.manager.inspect import inspect


def inspect_receipt(inbox, release_id, role, state_dir):
    verified = inspect(inbox, release_id, role)
    state = _safe_state_dir(state_dir)
    receipt_file = state / f'{release_id}.{role}.preflight.json'
    if receipt_file.is_symlink() or not receipt_file.is_file():
        raise ValueError('preflight receipt missing or symlinked')
    if receipt_file.stat().st_size > 500_000:
        raise ValueError('preflight receipt exceeds size limit')
    receipt = json.loads(receipt_file.read_text(encoding='utf-8'))
    required = {'schema', 'status', 'release_id', 'role', 'config_sha256',
                'routers', 'verified_at_utc'}
    if not isinstance(receipt, dict) or set(receipt) != required:
        raise ValueError('invalid preflight receipt fields')
    if (receipt['schema'] != 1 or
            receipt['status'] != 'preflight_verified_not_activated' or
            receipt['release_id'] != release_id or receipt['role'] != role or
            receipt['config_sha256'] != verified['config_sha256']):
        raise ValueError('preflight receipt identity mismatch')
    stamp = datetime.fromisoformat(receipt['verified_at_utc'])
    if stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0:
        raise ValueError('preflight receipt must have UTC timestamp')
    expected = {name: {'prefix_count': data['prefix_count'],
                       'prefix_sha256': data['prefix_sha256']}
                for name, data in verified['routers'].items()}
    if receipt['routers'] != expected:
        raise ValueError('preflight receipt prefixes mismatch')
    return {'status': 'preflight_receipt_verified_not_active',
            'release_id': release_id, 'role': role,
            'verified_at_utc': receipt['verified_at_utc'],
            'routers': expected}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inbox', type=Path, required=True)
    parser.add_argument('--release-id', required=True)
    parser.add_argument('--role', choices=('primary', 'backup'), required=True)
    parser.add_argument('--state-dir', type=Path, required=True)
    a = parser.parse_args()
    print(json.dumps(inspect_receipt(a.inbox, a.release_id, a.role,
                                    a.state_dir), indent=2))


if __name__ == '__main__':
    main()
