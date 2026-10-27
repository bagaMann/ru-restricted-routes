"""Durable, write-once local command preparation receipt; NEVER activates BIRD.

Distinct from historical apply/rollback transition journal and prepared
agent intent. Requires current local control precheck and records exact
tx/release/role/config digests and PID. One per agent; duplicate requests
are rejected instead of producing an authority or replayable token.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

from ru_routes.agent.control_precheck import MAX_REQUEST_BYTES, process
from ru_routes.agent.intent import TX
from ru_routes.manager.transaction_journal import _safe_path, _canonical

SHA = re.compile(r'^[0-9a-f]{64}$')
STATE = 'control_prepared_not_authorized_not_active'


def _read(root):
    path = root / 'control-state.json'
    if path.is_symlink():
        raise ValueError('symlinked control journal')
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > 12000:
        raise ValueError('invalid control journal file')
    item = json.loads(path.read_bytes())
    if not isinstance(item, dict) or set(item) != {'schema', 'record', 'sha256'}:
        raise ValueError('invalid control journal schema')
    original = {'schema': item['schema'], 'record': item['record']}
    if hashlib.sha256(_canonical(original)).hexdigest() != item['sha256']:
        raise ValueError('control journal checksum mismatch')
    record = item['record']
    if item['schema'] != 1 or not isinstance(record, dict) or set(record) != {
            'state', 'txid', 'release_id', 'role', 'target_sha256',
            'baseline_sha256', 'managed_pid', 'prepared_at_utc'}:
        raise ValueError('invalid control record')
    if (record['state'] != STATE or
            not isinstance(record['txid'], str) or not TX.fullmatch(record['txid']) or
            not isinstance(record['release_id'], str) or not SHA.fullmatch(record['release_id']) or
            record['role'] not in ('primary', 'backup') or
            type(record['managed_pid']) is not int or record['managed_pid'] < 2 or
            any(not isinstance(record[key], str) or not SHA.fullmatch(record[key])
                for key in ('target_sha256', 'baseline_sha256')) or
            not isinstance(record['prepared_at_utc'], str)):
        raise ValueError('invalid control journal identity')
    return record


def status(state_dir):
    root = _safe_path(state_dir)
    if not root.exists():
        return {'status': 'no_control_preparation'}
    if root.is_symlink():
        raise ValueError('symlinked control state dir')
    record = _read(root)
    return record if record else {'status': 'no_control_preparation'}


def prepare(raw, *, state_dir, storage, role, release_id,
            agent_state_dir, runtime_dir, transition_dir):
    root = _safe_path(state_dir)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or root.stat().st_mode & 0o077:
        raise ValueError('control state directory has unsafe permissions')
    fd = os.open(root / '.control.lock',
                 os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if _read(root) is not None:
            raise ValueError('control preparation already recorded; no replay')
        checked = process(raw, storage=storage, role=role,
                          release_id=release_id, agent_state_dir=agent_state_dir,
                          runtime_dir=runtime_dir, transition_dir=transition_dir)
        record = {
            'state': STATE, 'txid': checked['txid'],
            'release_id': checked['release_id'], 'role': checked['role'],
            'target_sha256': checked['target_sha256'],
            'baseline_sha256': checked['baseline_sha256'],
            'managed_pid': checked['managed_pid'],
            'prepared_at_utc': datetime.now(timezone.utc).isoformat(),
        }
        plain = {'schema': 1, 'record': record}
        data = dict(plain, sha256=hashlib.sha256(_canonical(plain)).hexdigest())
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(prefix='.control-', dir=root,
                                             delete=False) as file:
                tmp = Path(file.name)
                file.write((json.dumps(data, sort_keys=True, indent=2) + '\n').encode())
                file.flush()
                os.fsync(file.fileno())
            os.chmod(tmp, 0o600)
            # Exclusive create: prevent overwriting an existing journal.
            os.link(tmp, root / 'control-state.json', follow_symlinks=False)
            directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
        return {**record, 'activation_authorized': False,
                'activation_performed': False}
    finally:
        os.close(fd)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state-dir', required=True)
    sub = p.add_subparsers(dest='action', required=True)
    sub.add_parser('status')
    setup = sub.add_parser('prepare')
    setup.add_argument('--storage', required=True)
    setup.add_argument('--role', choices=('primary', 'backup'), required=True)
    setup.add_argument('--release-id', required=True)
    setup.add_argument('--agent-state-dir', required=True)
    setup.add_argument('--runtime-dir', default='/run/ru-routes-managed-bird')
    setup.add_argument('--transition-dir', required=True)
    a = p.parse_args()
    if a.action == 'status':
        result = status(a.state_dir)
    else:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        result = prepare(raw, state_dir=a.state_dir, storage=a.storage,
                         role=a.role, release_id=a.release_id,
                         agent_state_dir=a.agent_state_dir,
                         runtime_dir=a.runtime_dir,
                         transition_dir=a.transition_dir)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
