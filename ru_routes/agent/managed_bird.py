"""Fail-closed startup for a separately owned, static-only managed BIRD.

Never use the system BIRD socket/configuration. Boot always starts the
verified EMPTY recovery baseline, not the target or the last active state.
A future transaction controller must explicitly reconcile before apply.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess

from ru_routes.agent.recovery_bundle import verify_bundle
from ru_routes.agent.intent import status as intent_status


def startup_spec(storage, txid, role, release_id, agent_state_dir,
                 runtime_dir, *, bird='bird'):
    if role not in ('primary', 'backup'):
        raise ValueError('invalid role')
    intent = intent_status(agent_state_dir)
    if (intent.get('status') != 'agent_prepared_not_active' or
            intent.get('release_id') != release_id or
            intent.get('role') != role or intent.get('txid') != txid):
        raise ValueError('matching pending agent intent required')
    snapshot = verify_bundle(storage, txid, role, release_id=release_id)
    # The snapshot owns only STATIC tables and is never the production config.
    directory = Path(storage) / f'{txid}.{role}'
    baseline = directory / 'baseline.bird.conf'
    runtime = Path(runtime_dir)
    if not runtime.is_absolute() or runtime.is_symlink():
        raise ValueError('runtime directory must be absolute and not symlinked')
    if runtime.resolve() == Path('/run/bird') or Path('/etc/bird') in runtime.resolve().parents:
        raise ValueError('runtime overlaps system BIRD')
    if not runtime.exists() or not runtime.is_dir():
        raise ValueError('runtime directory must exist (created by systemd RuntimeDirectory)')
    socket = runtime / 'managed.ctl'
    pid = runtime / 'managed.pid'
    for item in (socket, pid):
        if item.is_symlink():
            raise ValueError('symlinked managed BIRD runtime file')
    return {
        'status': 'managed_baseline_startup_only',
        'release_id': release_id, 'txid': txid, 'role': role,
        'baseline_sha256': snapshot['baseline_sha256'],
        'argv': [bird, '-f', '-c', str(baseline), '-s', str(socket),
                 '-P', str(pid)],
        'control_socket': str(socket),
        'config': str(baseline),
    }


def start(storage, txid, role, release_id, agent_state_dir, runtime_dir,
          *, bird='bird'):
    spec = startup_spec(storage, txid, role, release_id, agent_state_dir,
                        runtime_dir, bird=bird)
    # Fail BEFORE exec; no automatic restore of prior active routes.
    subprocess.run([bird, '-p', '-c', spec['config']],
                   check=True, timeout=15, capture_output=True)
    os.execvp(bird, spec['argv'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--storage', required=True)
    parser.add_argument('--txid', required=True)
    parser.add_argument('--release-id', required=True)
    parser.add_argument('--role', choices=('primary', 'backup'), required=True)
    parser.add_argument('--agent-state-dir', required=True)
    parser.add_argument('--runtime-dir', required=True)
    parser.add_argument('--check-only', action='store_true')
    a = parser.parse_args()
    options = (a.storage, a.txid, a.role, a.release_id,
               a.agent_state_dir, a.runtime_dir)
    if a.check_only:
        print(json.dumps(startup_spec(*options), indent=2))
    else:
        start(*options)


if __name__ == '__main__':
    main()
