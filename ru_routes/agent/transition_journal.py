"""Durable fail-closed journal for managed BIRD configuration transitions.

This records BEFORE/AFTER evidence for future apply/rollback operations.
It NEVER calls birdc itself. An interrupted operation stays unresolved and
blocks additional begins until an explicit, externally verified resolution.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile

from ru_routes.agent.intent import TX
from ru_routes.manager.transaction_journal import _safe_path, _canonical

STATES = ('baseline_verified', 'apply_in_progress', 'target_verified',
          'rollback_in_progress', 'baseline_restored')
ALLOWED = {
    None: {'baseline_verified'},
    'baseline_verified': {'apply_in_progress'},
    'apply_in_progress': {'target_verified', 'rollback_in_progress'},
    'target_verified': {'rollback_in_progress'},
    'rollback_in_progress': {'baseline_restored'},
    'baseline_restored': set(),
}


def _validate(entry):
    if not isinstance(entry, dict) or set(entry) != {
            'txid', 'release_id', 'role', 'state', 'recorded_at_utc',
            'pid', 'baseline_sha256', 'target_sha256'}:
        raise ValueError('invalid transition record')
    if (not isinstance(entry['txid'], str) or not TX.fullmatch(entry['txid']) or
            not isinstance(entry['release_id'], str) or
            len(entry['release_id']) != 64 or
            any(ch not in '0123456789abcdef' for ch in entry['release_id']) or
            entry['role'] not in ('primary', 'backup') or
            entry['state'] not in STATES or
            type(entry['pid']) is not int or entry['pid'] <= 1 or
            any(not isinstance(entry[key], str) or len(entry[key]) != 64 or
                any(ch not in '0123456789abcdef' for ch in entry[key])
                for key in ('baseline_sha256', 'target_sha256'))):
        raise ValueError('invalid transition record identity')


def _load(folder):
    file = folder / 'transitions.json'
    if file.is_symlink():
        raise ValueError('symlinked transition journal')
    if not file.exists():
        return []
    if file.stat().st_size > 100_000:
        raise ValueError('transition journal too large')
    obj = json.loads(file.read_bytes())
    if not isinstance(obj, dict) or set(obj) != {'schema', 'entries', 'sha256'}:
        raise ValueError('invalid transition journal')
    unsigned = {'schema': obj['schema'], 'entries': obj['entries']}
    if hashlib.sha256(_canonical(unsigned)).hexdigest() != obj['sha256']:
        raise ValueError('transition journal checksum mismatch')
    if obj['schema'] != 1 or not isinstance(obj['entries'], list) or len(obj['entries']) > 20:
        raise ValueError('unsupported transition journal format')
    last = None
    identity = None
    for entry in obj['entries']:
        _validate(entry)
        current = (entry['txid'], entry['release_id'], entry['role'],
                   entry['baseline_sha256'], entry['target_sha256'])
        if identity is not None and identity != current:
            raise ValueError('mixed identities in transition journal')
        if entry['state'] not in ALLOWED[last]:
            raise ValueError('invalid transition order')
        identity = current
        last = entry['state']
    return obj['entries']


def _publish(folder, entries):
    unsigned = {'schema': 1, 'entries': entries}
    obj = dict(unsigned, sha256=hashlib.sha256(_canonical(unsigned)).hexdigest())
    temp = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.transitions-', dir=folder,
                                         delete=False) as f:
            temp = Path(f.name)
            f.write((json.dumps(obj, indent=2, sort_keys=True) + '\n').encode())
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, folder / 'transitions.json')
        fd = os.open(folder, os.O_DIRECTORY | os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def history(state_dir):
    folder = _safe_path(state_dir)
    if not folder.exists():
        return {'status': 'no_transitions', 'entries': []}
    if folder.is_symlink():
        raise ValueError('symlinked transition directory')
    records = _load(folder)
    return {'status': records[-1]['state'] if records else 'no_transitions',
            'entries': records}


def append(state_dir, *, txid, release_id, role, state, pid,
           baseline_sha256, target_sha256):
    entry = {'txid': txid, 'release_id': release_id, 'role': role,
             'state': state, 'pid': pid,
             'baseline_sha256': baseline_sha256,
             'target_sha256': target_sha256,
             'recorded_at_utc': datetime.now(timezone.utc).isoformat()}
    _validate(entry)
    folder = _safe_path(state_dir)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if folder.is_symlink():
        raise ValueError('symlinked transition directory')
    lock = folder / '.lock'
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        previous = _load(folder)
        old = previous[-1]['state'] if previous else None
        if state not in ALLOWED[old]:
            raise ValueError(f'illegal transition: {old} -> {state}')
        if previous:
            first = previous[0]
            for key in ('txid', 'release_id', 'role', 'baseline_sha256', 'target_sha256'):
                if entry[key] != first[key]:
                    raise ValueError(f'transition identity changed: {key}')
        _publish(folder, previous + [entry])
        return entry
    finally:
        os.close(fd)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state-dir', required=True)
    sub = p.add_subparsers(dest='action', required=True)
    sub.add_parser('status')
    args = p.parse_args()
    print(json.dumps(history(args.state_dir), indent=2))


if __name__ == '__main__':
    main()
