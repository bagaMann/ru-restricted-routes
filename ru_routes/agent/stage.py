"""Safely stage a role-specific bundle for a local BIRD agent.

No installation, BIRD reload, privileged action or network calls.
"""
import hashlib
import json
from pathlib import Path


def sha(data):
    return hashlib.sha256(data).hexdigest()


def verify_release(release_dir, role):
    if role not in ('primary', 'backup'):
        raise ValueError('unknown role')
    release_dir = Path(release_dir)
    release = json.loads((release_dir / 'release.json').read_text(encoding='utf-8'))
    if release.get('schema') != 1 or set(release.get('servers', {})) != {'primary', 'backup'}:
        raise ValueError('unsupported or incomplete release')
    identity = dict(release)
    release_id = identity.pop('release_id', None)
    if sha(json.dumps(identity, sort_keys=True).encode()) != release_id:
        raise ValueError('release manifest checksum mismatch')
    data = (release_dir / f'{role}.bird.conf').read_bytes()
    if sha(data) != release['servers'][role]['config_sha256']:
        raise ValueError('role configuration checksum mismatch')
    return release_id


def stage_release(release_dir, role, inbox):
    release_id = verify_release(release_dir, role)
    release_dir, inbox = Path(release_dir), Path(inbox)
    data = (release_dir / f'{role}.bird.conf').read_bytes()
    release = json.loads((release_dir / 'release.json').read_text(encoding='utf-8'))
    if inbox.resolve() == Path('/etc/bird') or Path('/etc/bird') in inbox.resolve().parents:
        raise ValueError('cannot stage inside live BIRD configuration')
    target = inbox / release_id
    if target.exists():
        raise FileExistsError('release was already staged; never overwrite')
    target.mkdir(parents=True)
    try:
        (target / 'bird.conf').write_bytes(data)
        (target / 'release.json').write_text(
            json.dumps(release, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    except Exception:
        for path in target.iterdir():
            path.unlink()
        target.rmdir()
        raise
    return release_id


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-dir', type=Path, required=True)
    parser.add_argument('--role', choices=('primary', 'backup'), required=True)
    parser.add_argument('--inbox', type=Path, required=True)
    a = parser.parse_args()
    print('STAGED (not activated): ' + stage_release(a.release_dir, a.role, a.inbox))


if __name__ == '__main__':
    main()
