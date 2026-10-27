"""Local one-shot static-only managed BIRD apply/verify/rollback executor.

NOT exposed via SSH. Uses a distinct write-ahead execution journal, an
exclusive process lock and the existing immutable recovery bundle. Never
touches system BIRD, enables BGP, or retains routes after a successful cycle.
A crash remains unresolved and is NOT retried automatically.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path

from ru_routes.agent.control_journal import status as control_status
from ru_routes.agent.execution_journal import append, inspect
from ru_routes.agent.managed_runtime import audit
from ru_routes.agent.managed_cycle import (
    _query, _configure, _check_until, audit_process_identity,
)
from ru_routes.agent.recovery_bundle import verify_bundle
from ru_routes.agent.live_test import validate_tables
from ru_routes.agent.sandbox_switch import verify_empty_listing
from ru_routes.manager.transaction_journal import _safe_path

DEFAULT_EXECUTION_DIR = '/var/lib/ru-routes-agent-execution'


def execute(storage, txid, role, release_id, agent_state_dir, runtime_dir,
            transition_dir, control_dir, execution_dir, *, birdc='birdc'):
    """One explicit local test cycle; no remote invocation or auto-recovery."""
    folder = _safe_path(execution_dir)
    if folder.exists() and folder.is_symlink():
        raise ValueError('symlinked executor directory')
    folder.mkdir(parents=True, mode=0o700, exist_ok=True)
    if folder.stat().st_mode & 0o077:
        raise ValueError('unsafe executor directory permissions')
    lock_file = folder / '.executor.lock'
    if lock_file.is_symlink():
        raise ValueError('symlinked executor lock')
    fd = os.open(lock_file, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('another managed executor is running') from exc
        if inspect(folder)['status'] != 'no_execution':
            raise ValueError('execution history exists; do not replay or auto-recover')
        preparation = control_status(control_dir)
        if (preparation.get('state') != 'control_prepared_not_authorized_not_active' or
                any(preparation.get(key) != expected for key, expected in (
                    ('txid', txid), ('release_id', release_id), ('role', role)))):
            raise ValueError('matching local control preparation is required')
        snapshot = verify_bundle(storage, txid, role, release_id=release_id)
        for key in ('target_sha256', 'baseline_sha256'):
            if snapshot[key] != preparation[key]:
                raise ValueError('control preparation checksum differs from recovery bundle')
        # Validate the exact independently owned process and its empty tables.
        running = audit(storage, txid, role, release_id,
                        agent_state_dir, runtime_dir, birdc=birdc)
        socket = running['control_socket']
        pid = running['managed_pid']
        if pid != preparation['managed_pid']:
            raise ValueError('managed BIRD PID differs from prepared PID; re-review required')
        root = Path(storage) / f'{txid}.{role}'
        target = str(root / 'target.bird.conf')
        baseline = str(root / 'baseline.bird.conf')
        metadata = {'txid': txid, 'role': role, 'release_id': release_id,
                    'baseline_sha256': snapshot['baseline_sha256'],
                    'target_sha256': snapshot['target_sha256'],
                    'managed_pid': pid}

        def record(state):
            return append(folder, state=state, **metadata)

        def identity():
            if not audit_process_identity(storage, txid, role, release_id,
                                          agent_state_dir, runtime_dir, pid):
                raise RuntimeError('managed BIRD process identity changed')

        def show(table):
            return _query(birdc, socket, '-r', 'show', 'route', 'table', table)

        def verify_target():
            expected = {key: {'prefix_count': value['prefix_count'],
                              'prefix_sha256': value['prefix_sha256']}
                        for key, value in snapshot['routers'].items()}
            return validate_tables(expected, show)

        def verify_empty():
            for router in snapshot['routers']:
                verify_empty_listing(show(f'rt_{router}'), f'rt_{router}')
            return True

        # Also serialize against the previous local managed cycle entrypoint.
        transition_lock = _safe_path(transition_dir) / '.cycle.lock'
        if transition_lock.is_symlink():
            raise ValueError('symlinked historical cycle lock')
        cycle_fd = os.open(transition_lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(cycle_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError('historical managed cycle is running') from exc
            identity()
            record('execution_prepared')
            # No BIRD configuration change until durable write-ahead state.
            identity()
            record('apply_in_progress')
            rollback_logged = False
            try:
                _configure(birdc, socket, target)
                observed = _check_until(verify_target)
                identity()
                record('target_verified')
                identity()
                record('rollback_in_progress')
                rollback_logged = True
                _configure(birdc, socket, baseline)
                _check_until(verify_empty)
                identity()
                record('baseline_restored')
                return {'status': 'local_executor_applied_verified_rolled_back',
                        'txid': txid, 'release_id': release_id, 'role': role,
                        'managed_pid': pid, 'routers': observed,
                        'rollback_verified': True, 'active': False,
                        'bgp_enabled': False}
            except Exception:
                # Best effort restoration only when the original PID and
                # argv are still present. Never apply to a replacement PID.
                if not rollback_logged:
                    try:
                        record('rollback_in_progress')
                        rollback_logged = True
                    except Exception:
                        pass
                try:
                    identity()
                    if rollback_logged:
                        _configure(birdc, socket, baseline)
                        _check_until(verify_empty)
                        identity()
                        record('baseline_restored')
                except Exception:
                    pass
                raise
        finally:
            os.close(cycle_fd)
    finally:
        os.close(fd)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--storage', required=True)
    p.add_argument('--txid', required=True)
    p.add_argument('--role', choices=('backup', 'primary'), required=True)
    p.add_argument('--release-id', required=True)
    p.add_argument('--agent-state-dir', required=True)
    p.add_argument('--runtime-dir', default='/run/ru-routes-managed-bird')
    p.add_argument('--transition-dir', required=True)
    p.add_argument('--control-dir', required=True)
    p.add_argument('--execution-dir', default=DEFAULT_EXECUTION_DIR)
    p.add_argument('--confirm-local-static-cycle', action='store_true')
    a = p.parse_args()
    if not a.confirm_local_static_cycle:
        p.error('explicit --confirm-local-static-cycle required')
    print(json.dumps(execute(a.storage, a.txid, a.role, a.release_id,
                             a.agent_state_dir, a.runtime_dir,
                             a.transition_dir, a.control_dir,
                             a.execution_dir), indent=2))


if __name__ == '__main__':
    main()
