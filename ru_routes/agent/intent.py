"""Agent-side durable intent state, without BIRD activation or SSH write actions.

Only a locally authenticated administrator can record/abort a PREPARED intent.
Crash recovery is fail-closed: an incomplete intent remains pending.
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

from ru_routes.agent.receipt import inspect_receipt
from ru_routes.manager.transaction_journal import _safe_path, _canonical

TX = re.compile(r'^tx-[0-9a-f]{64}$')
STATES = {'agent_prepared_not_active', 'agent_aborted_not_active'}


def _read(state):
    file = state / 'agent-state.json'
    if file.is_symlink():
        raise ValueError('symlink agent state rejected')
    if not file.exists():
        return None
    if file.stat().st_size > 100_000:
        raise ValueError('agent state oversized')
    obj = json.loads(file.read_bytes())
    if not isinstance(obj, dict) or set(obj) != {'schema', 'record', 'sha256'}:
        raise ValueError('invalid agent state structure')
    unsigned = {'schema': obj['schema'], 'record': obj['record']}
    if hashlib.sha256(_canonical(unsigned)).hexdigest() != obj['sha256']:
        raise ValueError('agent state checksum mismatch')
    rec = obj['record']
    if not isinstance(rec, dict) or set(rec) != {'txid', 'release_id', 'role', 'status', 'created_at_utc', 'closed_at_utc'}:
        raise ValueError('invalid agent record')
    if (obj['schema'] != 1 or not isinstance(rec['txid'], str) or not TX.fullmatch(rec['txid']) or
            not isinstance(rec['release_id'], str) or not re.fullmatch('[0-9a-f]{64}', rec['release_id']) or
            rec['role'] not in ('primary', 'backup') or rec['status'] not in STATES or
            (rec['status'] == 'agent_prepared_not_active') != (rec['closed_at_utc'] is None)):
        raise ValueError('invalid agent state values')
    return rec


def _write(state, record):
    unsigned = {'schema': 1, 'record': record}
    obj = dict(unsigned, sha256=hashlib.sha256(_canonical(unsigned)).hexdigest())
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=state, prefix='.agent-', delete=False) as fp:
            temp = Path(fp.name)
            fp.write((json.dumps(obj, indent=2, sort_keys=True) + '\n').encode())
            fp.flush()
            os.fsync(fp.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, state / 'agent-state.json')
        fd = os.open(state, os.O_DIRECTORY | os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def _lock(state, create):
    if create:
        state.mkdir(parents=True, mode=0o700, exist_ok=True)
    if state.is_symlink():
        raise ValueError('symlink state directory rejected')
    fd = os.open(state / '.agent.lock',
                 os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def status(state_dir):
    state = _safe_path(state_dir)
    if not state.exists():
        return {'status': 'no_agent_intent'}
    if state.is_symlink():
        raise ValueError('symlink state directory rejected')
    # The journal is published with atomic os.replace; readers never need
    # a write-capable lock or filesystem changes.
    record = _read(state)
    return record if record else {'status': 'no_agent_intent'}


def prepare(inbox, release_id, role, receipt_dir, state_dir, txid):
    if not isinstance(txid, str) or not TX.fullmatch(txid):
        raise ValueError('invalid transaction id')
    verified = inspect_receipt(inbox, release_id, role, receipt_dir)
    if verified['status'] != 'preflight_receipt_verified_not_active':
        raise ValueError('unverified preflight')
    state = _safe_path(state_dir)
    lock = _lock(state, True)
    try:
        old = _read(state)
        if old is not None:
            raise ValueError('agent state already exists: reconcile before new prepare')
        record = {'txid': txid, 'release_id': release_id, 'role': role,
                  'status': 'agent_prepared_not_active',
                  'created_at_utc': datetime.now(timezone.utc).isoformat(),
                  'closed_at_utc': None}
        _write(state, record)
        return record
    finally:
        os.close(lock)


def abort(state_dir, txid):
    if not isinstance(txid, str) or not TX.fullmatch(txid):
        raise ValueError('invalid transaction id')
    state = _safe_path(state_dir)
    if not state.is_dir():
        raise ValueError('agent state directory absent')
    lock = _lock(state, False)
    try:
        record = _read(state)
        if record is None or record['txid'] != txid or record['status'] != 'agent_prepared_not_active':
            raise ValueError('no matching pending agent intent')
        closed = dict(record, status='agent_aborted_not_active',
                      closed_at_utc=datetime.now(timezone.utc).isoformat())
        _write(state, closed)
        return closed
    finally:
        os.close(lock)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state-dir', required=True)
    sub = p.add_subparsers(dest='action', required=True)
    req = sub.add_parser('prepare')
    req.add_argument('--inbox', required=True)
    req.add_argument('--release-id', required=True)
    req.add_argument('--role', choices=('primary', 'backup'), required=True)
    req.add_argument('--receipt-dir', required=True)
    req.add_argument('--txid', required=True)
    stop = sub.add_parser('abort')
    stop.add_argument('--txid', required=True)
    sub.add_parser('status')
    args = p.parse_args()
    if args.action == 'prepare':
        result = prepare(args.inbox, args.release_id, args.role,
                         args.receipt_dir, args.state_dir, args.txid)
    elif args.action == 'abort':
        result = abort(args.state_dir, args.txid)
    else:
        result = status(args.state_dir)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
