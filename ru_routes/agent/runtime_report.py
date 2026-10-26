"""Publish a root-generated, read-only managed BIRD runtime audit snapshot.

A local administrator/systemd oneshot executes the privileged /proc and
Unix socket checks; routesync never gains socket access. Reports are atomic,
fsynced and contain explicit failure status; never use them as permissions.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile

from ru_routes.agent.recover_runtime import reconcile_runtime
from ru_routes.manager.transaction_journal import _safe_path, _canonical


def publish(storage, txid, role, release_id, agent_state_dir, runtime_dir,
            transition_dir, report_dir):
    destination = _safe_path(report_dir)
    destination.mkdir(parents=True, mode=0o755, exist_ok=True)
    if destination.is_symlink():
        raise ValueError('symlink report directory')
    if destination.stat().st_mode & 0o022:
        raise ValueError('report directory writable by untrusted users')
    report = reconcile_runtime(storage, txid, role, release_id,
                               agent_state_dir, runtime_dir, transition_dir)
    checked_at = datetime.now(timezone.utc).isoformat()
    clean = (report.get('reconciled') is True and
             report.get('baseline_empty_verified') is True and
             report.get('runtime_checked') is True and
             report.get('journal_state') == 'baseline_restored' and
             report.get('status') in (
                 'baseline_verified_same_managed_bird_process',
                 'baseline_verified_after_managed_bird_restart'))
    doc = {'schema': 1, 'txid': txid, 'release_id': release_id,
           'role': role, 'checked_at_utc': checked_at,
           'status': 'live_baseline_verified' if clean else 'live_not_verified',
           'evidence': report}
    doc['sha256'] = hashlib.sha256(_canonical(doc)).hexdigest()
    target = destination / 'runtime-status.json'
    if target.is_symlink():
        raise ValueError('symlinked report rejected')
    temp = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.runtime-', dir=destination,
                                         delete=False) as file:
            temp = Path(file.name)
            file.write((json.dumps(doc, indent=2, sort_keys=True) + '\n').encode())
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temp, 0o644)
        os.replace(temp, target)
        fd = os.open(destination, os.O_DIRECTORY | os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
    return doc


def read_report(report_dir, release_id, role, *, txid=None):
    root = _safe_path(report_dir)
    file = root / 'runtime-status.json'
    if file.is_symlink() or not file.is_file() or file.stat().st_size > 20000:
        raise ValueError('runtime report unavailable')
    obj = json.loads(file.read_bytes())
    if not isinstance(obj, dict) or set(obj) != {
            'schema', 'txid', 'release_id', 'role', 'checked_at_utc',
            'status', 'evidence', 'sha256'}:
        raise ValueError('invalid runtime report structure')
    signed = {key: val for key, val in obj.items() if key != 'sha256'}
    if hashlib.sha256(_canonical(signed)).hexdigest() != obj['sha256']:
        raise ValueError('runtime report checksum mismatch')
    if (obj['schema'] != 1 or obj['release_id'] != release_id or
            obj['role'] != role or (txid is not None and txid != obj['txid']) or
            obj['status'] not in ('live_baseline_verified', 'live_not_verified')):
        raise ValueError('runtime report identity mismatch')
    stamp = datetime.fromisoformat(obj['checked_at_utc'])
    if stamp.tzinfo is None:
        raise ValueError('runtime report timestamp lacks timezone')
    age = (datetime.now(timezone.utc) - stamp).total_seconds()
    if age < -10 or age > 120:
        raise ValueError('runtime report stale or from future')
    if obj['status'] == 'live_baseline_verified':
        evidence = obj['evidence']
        if (not isinstance(evidence, dict) or
                evidence.get('reconciled') is not True or
                evidence.get('baseline_empty_verified') is not True or
                evidence.get('runtime_checked') is not True or
                evidence.get('journal_state') != 'baseline_restored' or
                evidence.get('txid') != obj['txid'] or
                evidence.get('release_id') != release_id or
                evidence.get('role') != role):
            raise ValueError('runtime report positive evidence inconsistent')
    return obj


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--storage', required=True)
    p.add_argument('--txid', required=True)
    p.add_argument('--role', choices=('primary', 'backup'), required=True)
    p.add_argument('--release-id', required=True)
    p.add_argument('--agent-state-dir', required=True)
    p.add_argument('--runtime-dir', default='/run/ru-routes-managed-bird')
    p.add_argument('--transition-dir', required=True)
    p.add_argument('--report-dir', required=True)
    a = p.parse_args()
    result = publish(a.storage, a.txid, a.role, a.release_id,
                     a.agent_state_dir, a.runtime_dir,
                     a.transition_dir, a.report_dir)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
