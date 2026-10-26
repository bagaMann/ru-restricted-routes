"""Explicit local apply->verify->rollback cycle for ONLY the owned managed BIRD.

No BGP, kernel, system BIRD sockets, remote SSH commands or persistent target.
Requires exact PID+argv audit and empty baseline. Writes a durable journal
BEFORE both config changes. On any error, attempts baseline restoration;
a crash/interrupted journal requires manual reconciliation, never auto-retry.
"""
import argparse
import fcntl
import os
import json
from pathlib import Path
import subprocess
import time

from ru_routes.agent.managed_runtime import audit
from ru_routes.agent.recovery_bundle import verify_bundle
from ru_routes.agent.transition_journal import append, history
from ru_routes.agent.live_test import validate_tables
from ru_routes.agent.sandbox_switch import verify_empty_listing
from ru_routes.manager.transaction_journal import _safe_path


def _query(birdc, socket, *cmd):
    result = subprocess.run([birdc, '-s', socket, *cmd],
                            capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise RuntimeError(f'managed birdc command failed: {result.stderr[:300]}')
    return result.stdout


def _check_until(check, *, timeout=15):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            return check()
        except (ValueError, RuntimeError) as exc:
            last = exc
            time.sleep(0.15)
    raise RuntimeError(f'managed routes did not converge: {last}')


def _configure(birdc, socket, config):
    result = _query(birdc, socket, 'configure', f'"{config}"')
    if 'Reconfigured' not in result and 'Reconfiguration in progress' not in result:
        raise RuntimeError(f'managed BIRD configure not acknowledged: {result[:300]}')


def _cycle_locked(storage, txid, role, release_id, agent_state_dir, runtime_dir,
                  journal_dir, *, birdc='birdc'):
    if history(journal_dir)['entries']:
        raise ValueError('transition journal already exists; do not re-run cycle')
    before = audit(storage, txid, role, release_id,
                   agent_state_dir, runtime_dir, birdc=birdc)
    snapshot = verify_bundle(storage, txid, role, release_id=release_id)
    folder = Path(storage) / f'{txid}.{role}'
    target = str(folder / 'target.bird.conf')
    baseline = str(folder / 'baseline.bird.conf')
    socket = before['control_socket']
    pid = before['managed_pid']
    journal_keys = {'txid': txid, 'release_id': release_id, 'role': role,
                    'pid': pid,
                    'baseline_sha256': snapshot['baseline_sha256'],
                    'target_sha256': snapshot['target_sha256']}

    def record(state):
        return append(journal_dir, state=state, **journal_keys)

    def listing(router):
        return _query(birdc, socket, '-r', 'show', 'route',
                      'table', f'rt_{router}')

    def empty():
        for router in snapshot['routers']:
            verify_empty_listing(listing(router), f'rt_{router}')
        return True

    def target_ok():
        expected = {k: {'prefix_count': v['prefix_count'],
                        'prefix_sha256': v['prefix_sha256']}
                    for k, v in snapshot['routers'].items()}
        return validate_tables(expected, lambda table:
                               _query(birdc, socket, '-r',
                                      'show', 'route', 'table', table))

    record('baseline_verified')
    if not audit_process_identity(storage, txid, role, release_id,
                                  agent_state_dir, runtime_dir, pid):
        raise RuntimeError('managed BIRD process changed before apply')
    record('apply_in_progress')
    rollback_recorded = False
    try:
        _configure(birdc, socket, target)
        observed = _check_until(target_ok)
        record('target_verified')
        # A PID mismatch now means the previously trusted process is gone.
        # Never issue rollback into an unverified new service.
        if audit_process_identity(storage, txid, role, release_id,
                                  agent_state_dir, runtime_dir, pid) is False:
            raise RuntimeError('managed BIRD process changed during cycle')
        record('rollback_in_progress')
        rollback_recorded = True
        _configure(birdc, socket, baseline)
        _check_until(empty)
        record('baseline_restored')
        return {'status': 'managed_cycle_applied_verified_rolled_back',
                'release_id': release_id, 'txid': txid, 'role': role,
                'managed_pid': pid, 'routers': observed,
                'rollback_verified': True, 'active': False}
    except Exception:
        if not rollback_recorded:
            try:
                record('rollback_in_progress')
                rollback_recorded = True
            except Exception:
                pass
        try:
            if audit_process_identity(storage, txid, role, release_id,
                                      agent_state_dir, runtime_dir, pid):
                _configure(birdc, socket, baseline)
                _check_until(empty)
                # Do not claim successful rollback if its write-ahead record failed.
                if rollback_recorded:
                    record('baseline_restored')
        except Exception:
            pass
        raise


def cycle(storage, txid, role, release_id, agent_state_dir, runtime_dir,
          journal_dir, *, birdc='birdc'):
    """Serialize the WHOLE cycle, including audit, configure and rollback."""
    folder = _safe_path(journal_dir)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if folder.is_symlink():
        raise ValueError('symlinked cycle journal directory')
    path = folder / '.cycle.lock'
    if path.is_symlink():
        raise ValueError('symlinked cycle lock rejected')
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('another managed BIRD cycle is running') from exc
        return _cycle_locked(storage, txid, role, release_id,
                             agent_state_dir, runtime_dir, journal_dir,
                             birdc=birdc)
    finally:
        os.close(fd)


def audit_process_identity(storage, txid, role, release_id,
                           agent_state_dir, runtime_dir, expected_pid):
    """Check identity without requiring empty tables while target is loaded."""
    from ru_routes.agent.managed_bird import startup_spec
    spec = startup_spec(storage, txid, role, release_id,
                        agent_state_dir, runtime_dir)
    pidfile = Path(runtime_dir) / 'managed.pid'
    if pidfile.is_symlink() or not pidfile.is_file():
        return False
    try:
        pid = int(pidfile.read_text().strip())
        if pid != expected_pid:
            return False
        cmdline = (Path('/proc') / str(pid) / 'cmdline').read_bytes()
        argv = [x.decode() for x in cmdline.split(b'\0') if x]
        return (len(argv) == len(spec['argv']) and
                Path(argv[0]).name == Path(spec['argv'][0]).name and
                argv[1:] == spec['argv'][1:])
    except (ValueError, OSError, UnicodeError):
        return False


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--storage', required=True)
    p.add_argument('--txid', required=True)
    p.add_argument('--role', choices=('primary', 'backup'), required=True)
    p.add_argument('--release-id', required=True)
    p.add_argument('--agent-state-dir', required=True)
    p.add_argument('--runtime-dir', default='/run/ru-routes-managed-bird')
    p.add_argument('--journal-dir', required=True)
    p.add_argument('--confirm-test-cycle', action='store_true')
    a = p.parse_args()
    if not a.confirm_test_cycle:
        raise SystemExit('Refusing without --confirm-test-cycle')
    print(json.dumps(cycle(a.storage, a.txid, a.role, a.release_id,
                           a.agent_state_dir, a.runtime_dir, a.journal_dir),
                     indent=2))


if __name__ == '__main__':
    main()
