"""Crash-safe journal primitives for a FUTURE privileged managed BIRD executor.

NO SSH endpoint or BIRD operations are implemented here. The only CLI
operation is read-only status. This journal is separate from the completed
static cycle and control preparation journals; never reset those journals.

Each state transition is written and fsynced BEFORE an eventual BIRD action.
Interrupted or uncertain states fail closed, never implying route state.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from ru_routes.agent.intent import TX
from ru_routes.manager.transaction_journal import _canonical, _safe_path

HEX = re.compile(r'^[a-f0-9]{64}$')
STATES = {
    None: {'execution_prepared'},
    'execution_prepared': {'apply_in_progress', 'manual_review_required'},
    'apply_in_progress': {'target_verified', 'rollback_in_progress',
                          'manual_review_required'},
    'target_verified': {'rollback_in_progress', 'manual_review_required'},
    'rollback_in_progress': {'baseline_restored', 'manual_review_required'},
    'baseline_restored': set(),
    'manual_review_required': set(),
}
UNRESOLVED = {'apply_in_progress', 'target_verified', 'rollback_in_progress',
              'manual_review_required'}
MAX_RECORDS = 12


def _validate(record):
    if not isinstance(record, dict) or set(record) != {
            'txid', 'release_id', 'role', 'state', 'baseline_sha256',
            'target_sha256', 'managed_pid', 'recorded_at_utc'}:
        raise ValueError('invalid execution record fields')
    if (type(record['txid']) is not str or not TX.fullmatch(record['txid']) or
            type(record['release_id']) is not str or not HEX.fullmatch(record['release_id']) or
            type(record['role']) is not str or record['role'] not in ('backup', 'primary') or
            type(record['state']) is not str or record['state'] not in STATES or
            record['state'] is None or
            type(record['managed_pid']) is not int or record['managed_pid'] < 2 or
            any(type(record[k]) is not str or not HEX.fullmatch(record[k])
                for k in ('baseline_sha256', 'target_sha256')) or
            type(record['recorded_at_utc']) is not str):
        raise ValueError('invalid execution record identity')
    try:
        stamp = datetime.fromisoformat(record['recorded_at_utc'])
    except ValueError as exc:
        raise ValueError('invalid execution timestamp') from exc
    if stamp.tzinfo is None:
        raise ValueError('execution timestamp must be timezone-aware')


def _read(root):
    file = root / 'execution.json'
    if file.is_symlink():
        raise ValueError('symlinked execution journal')
    if not file.exists():
        return []
    if not file.is_file() or file.stat().st_size > 30000:
        raise ValueError('invalid execution journal file')
    item = json.loads(file.read_bytes())
    if not isinstance(item, dict) or set(item) != {'schema', 'entries', 'sha256'}:
        raise ValueError('invalid execution journal format')
    unsigned = {'schema': item['schema'], 'entries': item['entries']}
    if hashlib.sha256(_canonical(unsigned)).hexdigest() != item['sha256']:
        raise ValueError('execution journal checksum mismatch')
    entries = item['entries']
    if item['schema'] != 1 or not isinstance(entries, list) or len(entries) > MAX_RECORDS:
        raise ValueError('invalid execution journal schema')
    previous = None
    identity = None
    for record in entries:
        _validate(record)
        if record['state'] not in STATES[previous]:
            raise ValueError('invalid execution journal transition order')
        current = tuple(record[k] for k in ('txid', 'release_id', 'role',
                                             'baseline_sha256', 'target_sha256',
                                             'managed_pid'))
        if identity is not None and identity != current:
            raise ValueError('mixed execution journal identity')
        identity = current
        previous = record['state']
    return entries


def inspect(state_dir):
    folder = _safe_path(state_dir)
    if not folder.exists():
        return {'status': 'no_execution', 'entries': [],
                'unresolved': False, 'activation_authorized': False}
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError('invalid execution state directory')
    entries = _read(folder)
    last = entries[-1]['state'] if entries else 'no_execution'
    return {'status': last, 'entries': entries,
            'unresolved': last in UNRESOLVED,
            'activation_authorized': False}


def _save(folder, records):
    plain = {'schema': 1, 'entries': records}
    payload = dict(plain, sha256=hashlib.sha256(_canonical(plain)).hexdigest())
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(dir=folder, prefix='.execution-',
                                         delete=False) as file:
            tmp = Path(file.name)
            file.write((json.dumps(payload, sort_keys=True, indent=2) + '\n').encode())
            file.flush()
            os.fsync(file.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, folder / 'execution.json')
        fd = os.open(folder, os.O_DIRECTORY | os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def append(state_dir, *, state, txid, release_id, role,
           baseline_sha256, target_sha256, managed_pid):
    """Local root-only journal primitive; not exposed as a CLI write verb."""
    record = {'state': state, 'txid': txid, 'release_id': release_id,
              'role': role, 'baseline_sha256': baseline_sha256,
              'target_sha256': target_sha256, 'managed_pid': managed_pid,
              'recorded_at_utc': datetime.now(timezone.utc).isoformat()}
    _validate(record)
    folder = _safe_path(state_dir)
    if folder.exists() and folder.is_symlink():
        raise ValueError('symlinked execution directory')
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if folder.stat().st_mode & 0o077:
        raise ValueError('unsafe execution journal directory permissions')
    path = folder / '.execution.lock'
    if path.is_symlink():
        raise ValueError('symlinked execution lock')
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('execution journal busy') from exc
        old = _read(folder)
        prior = old[-1]['state'] if old else None
        if state not in STATES[prior]:
            raise ValueError(f'illegal execution transition: {prior} -> {state}')
        if old:
            keys = ('txid', 'release_id', 'role', 'baseline_sha256',
                    'target_sha256', 'managed_pid')
            if any(record[k] != old[0][k] for k in keys):
                raise ValueError('execution identity changed')
        if len(old) >= MAX_RECORDS:
            raise ValueError('execution journal at maximum size')
        _save(folder, old + [record])
        return record
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', required=True)
    parser.add_argument('action', choices=('status',))
    args = parser.parse_args()
    print(json.dumps(inspect(args.state_dir), indent=2))


if __name__ == '__main__':
    main()
