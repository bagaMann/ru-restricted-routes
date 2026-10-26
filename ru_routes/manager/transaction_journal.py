"""Fail-closed manager transaction intent journal.

This module records only *intent*, never applies or claims active BIRD state.
A single pending transaction blocks creation of another until explicitly
aborted. Disk writes use exclusive creation, fsync and a directory lock.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from datetime import datetime, timezone

from ru_routes.agent.stage import verify_release

RID = re.compile(r'^[0-9a-f]{64}$')
TXID = re.compile(r'^tx-[0-9a-f]{64}$')
VALID_STATES = {'intent_prepared_not_active', 'aborted_not_active'}


def _safe_path(path):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink():
        raise ValueError('journal directory must be an absolute, nonsymlink path')
    resolved = path.resolve()
    if resolved == Path('/etc/bird') or Path('/etc/bird') in resolved.parents:
        raise ValueError('journal must not be inside /etc/bird')
    return path


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def _read(state):
    file = state / 'journal.json'
    if file.is_symlink():
        raise ValueError('symlinked journal rejected')
    if not file.exists():
        return None
    if file.stat().st_size > 200_000:
        raise ValueError('journal too large')
    obj = json.loads(file.read_bytes())
    if not isinstance(obj, dict) or set(obj) != {'schema', 'transactions', 'sha256'}:
        raise ValueError('invalid journal fields')
    checksum = obj.pop('sha256')
    if hashlib.sha256(_canonical(obj)).hexdigest() != checksum:
        raise ValueError('journal checksum mismatch')
    obj['sha256'] = checksum
    if obj['schema'] != 1 or not isinstance(obj['transactions'], list):
        raise ValueError('unknown journal schema')
    for index, tx in enumerate(obj['transactions']):
        if (not isinstance(tx, dict) or set(tx) != {'id', 'release_id', 'state', 'created_at_utc', 'closed_at_utc'}
                or not isinstance(tx['id'], str) or not TXID.fullmatch(tx['id'])
                or not isinstance(tx['release_id'], str) or not RID.fullmatch(tx['release_id'])
                or tx['state'] not in VALID_STATES
                or (tx['state'] == 'intent_prepared_not_active') != (tx['closed_at_utc'] is None)):
            raise ValueError(f'invalid journal transaction {index}')
    if len({tx['id'] for tx in obj['transactions']}) != len(obj['transactions']):
        raise ValueError('duplicate journal transactions')
    if sum(tx['state'] == 'intent_prepared_not_active' for tx in obj['transactions']) > 1:
        raise ValueError('multiple pending journal transactions')
    return obj


def _write(state, content):
    data = {'schema': 1, 'transactions': content}
    data['sha256'] = hashlib.sha256(_canonical(data)).hexdigest()
    encoded = json.dumps(data, indent=2, sort_keys=True).encode() + b'\n'
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.journal-', dir=state, delete=False) as f:
            temporary = Path(f.name)
            f.write(encoded)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, state / 'journal.json')
        fd = os.open(state, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _locked(state):
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    if state.is_symlink():
        raise ValueError('symlinked journal directory')
    lock = state / '.lock'
    if lock.is_symlink():
        raise ValueError('symlinked journal lock')
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def journal_status(state_dir):
    state = _safe_path(state_dir)
    if not state.exists():
        return {'status': 'empty', 'transactions': []}
    fd = _locked(state)
    try:
        journal = _read(state)
        return {'status': 'ready' if journal else 'empty',
                'transactions': journal['transactions'] if journal else []}
    finally:
        os.close(fd)


def initialize(state_dir, release_dir):
    """Record an intent for a checksummed two-role release without activating it."""
    ids = [verify_release(release_dir, role) for role in ('primary', 'backup')]
    if ids[0] != ids[1] or not RID.fullmatch(ids[0]):
        raise ValueError('release identity mismatch')
    release_id = ids[0]
    state = _safe_path(state_dir)
    fd = _locked(state)
    try:
        journal = _read(state)
        transactions = [] if journal is None else journal['transactions']
        if any(tx['state'] == 'intent_prepared_not_active' for tx in transactions):
            raise ValueError('pending intent exists; inspect or explicitly abort it')
        # Unique monotonically increasing sequence within this journal.
        sequence = len(transactions) + 1
        txid = 'tx-' + hashlib.sha256(f'{release_id}:{sequence}'.encode()).hexdigest()
        record = {'id': txid, 'release_id': release_id,
                  'state': 'intent_prepared_not_active',
                  'created_at_utc': datetime.now(timezone.utc).isoformat(),
                  'closed_at_utc': None}
        _write(state, transactions + [record])
        return record
    finally:
        os.close(fd)


def abort(state_dir, txid):
    if not isinstance(txid, str) or not TXID.fullmatch(txid):
        raise ValueError('invalid transaction id')
    state = _safe_path(state_dir)
    if not state.exists():
        raise ValueError('journal is missing')
    fd = _locked(state)
    try:
        journal = _read(state)
        if journal is None:
            raise ValueError('journal is empty')
        transactions = journal['transactions']
        matches = [tx for tx in transactions if tx['id'] == txid]
        if len(matches) != 1 or matches[0]['state'] != 'intent_prepared_not_active':
            raise ValueError('transaction is missing or not pending')
        replacement = []
        for tx in transactions:
            if tx['id'] == txid:
                tx = dict(tx, state='aborted_not_active',
                          closed_at_utc=datetime.now(timezone.utc).isoformat())
            replacement.append(tx)
        _write(state, replacement)
        return next(tx for tx in replacement if tx['id'] == txid)
    finally:
        os.close(fd)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state-dir', type=Path, required=True)
    sub = p.add_subparsers(dest='action', required=True)
    init = sub.add_parser('init', help='record transaction intent (no activation)')
    init.add_argument('--release-dir', type=Path, required=True)
    sub.add_parser('status', help='read journal and validate integrity')
    end = sub.add_parser('abort', help='close intent explicitly')
    end.add_argument('--txid', required=True)
    a = p.parse_args()
    if a.action == 'init':
        result = initialize(a.state_dir, a.release_dir)
    elif a.action == 'abort':
        result = abort(a.state_dir, a.txid)
    else:
        result = journal_status(a.state_dir)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
