"""Read-only integrity and identity inspection of prepared agent releases.

Never reconfigure BIRD, access the network, or mark a release active.
"""
import argparse
import hashlib
import json
from pathlib import Path


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def inspect(inbox, release_id, role):
    if role not in ('primary', 'backup'):
        raise ValueError('unknown role')
    if not isinstance(release_id, str) or len(release_id) != 64 or any(c not in '0123456789abcdef' for c in release_id):
        raise ValueError('invalid release identifier')
    inbox = Path(inbox)
    root = inbox / release_id
    if root.is_symlink():
        raise ValueError('symlink release directory not accepted')
    manifest_path, config_path = root / 'release.json', root / 'bird.conf'
    if manifest_path.is_symlink() or config_path.is_symlink():
        raise ValueError('symlink release files not accepted')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('schema') != 1 or set(manifest.get('servers', {})) != {'primary', 'backup'}:
        raise ValueError('unsupported release manifest')
    unsigned = dict(manifest)
    identity = unsigned.pop('release_id', None)
    if identity != release_id or digest(json.dumps(unsigned, sort_keys=True).encode()) != identity:
        raise ValueError('invalid release identity')
    expected = manifest['servers'][role]['config_sha256']
    if digest(config_path.read_bytes()) != expected:
        raise ValueError('prepared config checksum mismatch')
    return {
        'release_id': release_id,
        'role': role,
        'status': 'prepared_verified_not_active',
        'config_sha256': expected,
        'routers': manifest['servers'][role]['routers']
    }


def inspect_pair(primary_inbox, backup_inbox, release_id):
    primary = inspect(primary_inbox, release_id, 'primary')
    backup = inspect(backup_inbox, release_id, 'backup')
    if primary['routers'] != backup['routers']:
        raise ValueError('agents have different route selections')
    return {'release_id': release_id, 'status': 'both_prepared_verified_not_active',
            'routers': primary['routers']}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--release-id', required=True)
    p.add_argument('--primary-inbox', type=Path, required=True)
    p.add_argument('--backup-inbox', type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(inspect_pair(a.primary_inbox, a.backup_inbox, a.release_id), indent=2))


if __name__ == '__main__':
    main()
