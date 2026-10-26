"""Write-once, verified BGP-free recovery bundle for a future managed BIRD.

Captures *two generated static-only configurations*, NOT the current live
system daemon. No /etc/bird changes, no socket access and no activation.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from datetime import datetime, timezone

from ru_routes.agent.isolated import make_isolated
from ru_routes.agent.intent import status as agent_status, TX
from ru_routes.agent.sandbox_switch import build_empty_baseline
from ru_routes.manager.inspect import inspect
from ru_routes.manager.transaction_journal import _safe_path

HEX64 = re.compile(r'^[0-9a-f]{64}$')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def _load_file(file):
    if file.is_symlink() or not file.is_file() or file.stat().st_size > 4_000_000:
        raise ValueError('snapshot file absent, symlinked, or oversized')
    return file.read_bytes()


def verify_bundle(storage, txid, role, *, release_id=None):
    if not isinstance(txid, str) or not TX.fullmatch(txid) or role not in ('primary', 'backup'):
        raise ValueError('invalid transaction or role')
    base = _safe_path(storage)
    folder = base / f'{txid}.{role}'
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError('recovery bundle unavailable')
    manifest = json.loads(_load_file(folder / 'snapshot.json'))
    if not isinstance(manifest, dict) or set(manifest) != {
            'schema', 'txid', 'release_id', 'role', 'status',
            'created_at_utc', 'target_sha256', 'baseline_sha256',
            'routers'}:
        raise ValueError('invalid snapshot manifest')
    if (manifest['schema'] != 1 or manifest['txid'] != txid or
            manifest['role'] != role or
            manifest['status'] != 'static_only_recovery_bundle_not_active' or
            not isinstance(manifest['release_id'], str) or
            not HEX64.fullmatch(manifest['release_id']) or
            (release_id is not None and release_id != manifest['release_id'])):
        raise ValueError('snapshot identity mismatch')
    target = _load_file(folder / 'target.bird.conf')
    baseline = _load_file(folder / 'baseline.bird.conf')
    if sha(target) != manifest['target_sha256'] or sha(baseline) != manifest['baseline_sha256']:
        raise ValueError('recovery bundle checksum mismatch')
    if build_empty_baseline(target.decode('utf-8')).encode() != baseline:
        raise ValueError('recovery baseline differs from target')
    return manifest


def capture(inbox, release_id, role, txid, agent_state_dir, storage):
    if not isinstance(txid, str) or not TX.fullmatch(txid):
        raise ValueError('invalid transaction id')
    prepared = agent_status(agent_state_dir)
    if (prepared.get('status') != 'agent_prepared_not_active' or
            prepared.get('txid') != txid or
            prepared.get('release_id') != release_id or
            prepared.get('role') != role):
        raise ValueError('matching prepared agent intent required')
    verified = inspect(inbox, release_id, role)
    storage = _safe_path(storage)
    destination = storage / f'{txid}.{role}'
    if destination.exists() or destination.is_symlink():
        raise FileExistsError('recovery bundle already exists; never overwrite')
    storage.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.snapshot-', dir=storage) as tmp:
        root = Path(tmp)
        target_file = root / 'target.bird.conf'
        make_isolated(inbox, release_id, role, target_file)
        target = target_file.read_bytes()
        baseline = build_empty_baseline(target.decode('utf-8')).encode()
        (root / 'baseline.bird.conf').write_bytes(baseline)
        receipt = {'schema': 1, 'txid': txid, 'role': role,
                   'release_id': release_id,
                   'status': 'static_only_recovery_bundle_not_active',
                   'created_at_utc': datetime.now(timezone.utc).isoformat(),
                   'target_sha256': sha(target), 'baseline_sha256': sha(baseline),
                   'routers': verified['routers']}
        (root / 'snapshot.json').write_text(
            json.dumps(receipt, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        for file in root.iterdir():
            os.chmod(file, 0o600)
            with file.open('rb') as fh:
                os.fsync(fh.fileno())
        # Re-check source; the staged version must not change during capture.
        if inspect(inbox, release_id, role) != verified:
            raise ValueError('release changed while generating recovery bundle')
        # Rename within the same filesystem creates a complete immutable-looking
        # directory; later attempts to overwrite are refused.
        os.rename(root, destination)
        fd = os.open(storage, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return verify_bundle(storage, txid, role, release_id=release_id)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--storage', required=True)
    sub = p.add_subparsers(dest='action', required=True)
    create = sub.add_parser('capture')
    create.add_argument('--inbox', required=True)
    create.add_argument('--release-id', required=True)
    create.add_argument('--role', choices=('primary', 'backup'), required=True)
    create.add_argument('--txid', required=True)
    create.add_argument('--agent-state-dir', required=True)
    check = sub.add_parser('verify')
    check.add_argument('--txid', required=True)
    check.add_argument('--role', choices=('primary', 'backup'), required=True)
    check.add_argument('--release-id', required=True)
    a = p.parse_args()
    result = (capture(a.inbox, a.release_id, a.role, a.txid,
                      a.agent_state_dir, a.storage)
              if a.action == 'capture'
              else verify_bundle(a.storage, a.txid, a.role, release_id=a.release_id))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
