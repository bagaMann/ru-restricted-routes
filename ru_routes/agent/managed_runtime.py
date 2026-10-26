"""Read-only audit of the EXACT managed BIRD process before any configure.

Checks pidfile, /proc argv, owned config, independent socket, and empty
static tables. Does not execute write commands. Must be run as root.
"""
import argparse
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess

from ru_routes.agent.managed_bird import startup_spec
from ru_routes.agent.sandbox_switch import verify_empty_listing

_PID = re.compile(r'^[1-9][0-9]{0,9}$')


def audit(storage, txid, role, release_id, agent_state_dir, runtime_dir,
          *, birdc='birdc'):
    spec = startup_spec(storage, txid, role, release_id,
                        agent_state_dir, runtime_dir)
    pidfile = Path(runtime_dir) / 'managed.pid'
    socketfile = Path(spec['control_socket'])
    if pidfile.is_symlink() or socketfile.is_symlink():
        raise ValueError('symlinked managed PID or socket')
    if not pidfile.is_file() or pidfile.stat().st_size > 20:
        raise ValueError('managed PID file missing or oversized')
    raw = pidfile.read_text(encoding='ascii').strip()
    if not _PID.fullmatch(raw):
        raise ValueError('invalid managed BIRD PID')
    pid = int(raw)
    if pid == os.getpid():
        raise ValueError('refusing self PID')
    cmdline = (Path('/proc') / str(pid) / 'cmdline').read_bytes()
    argv = [item.decode('utf-8') for item in cmdline.split(b'\0') if item]
    expected = spec['argv']
    # On Debian bird may resolve executable from PATH to full location;
    # require every operational argument byte-for-byte.
    if len(argv) != len(expected) or Path(argv[0]).name != Path(expected[0]).name or argv[1:] != expected[1:]:
        raise ValueError('PID process is not the expected managed BIRD')
    if not socketfile.exists() or not stat.S_ISSOCK(socketfile.stat().st_mode):
        raise ValueError('managed control socket not present')
    # Do not trust a different service that happens to use the same socket path.
    # Exact process command line above is prerequisite, before socket access.
    from ru_routes.agent.recovery_bundle import verify_bundle
    manifest = verify_bundle(storage, txid, role, release_id=release_id)
    observed = {}
    for router in sorted(manifest['routers']):
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,31}', router):
            raise ValueError('invalid router name')
        result = subprocess.run([birdc, '-r', '-s', str(socketfile),
                                 'show', 'route', 'table', f'rt_{router}'],
                                capture_output=True, text=True, timeout=15)
        if result.returncode:
            raise RuntimeError(f'{router}: birdc failed: {result.stderr[:400]}')
        verify_empty_listing(result.stdout, f'rt_{router}')
        observed[router] = {'prefix_count': 0}
    return {'status': 'managed_runtime_verified_empty_not_active',
            'txid': txid, 'release_id': release_id, 'role': role,
            'managed_pid': pid, 'control_socket': str(socketfile),
            'baseline_sha256': manifest['baseline_sha256'],
            'routers': observed, 'activation_performed': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--storage', required=True)
    p.add_argument('--txid', required=True)
    p.add_argument('--role', required=True, choices=('primary', 'backup'))
    p.add_argument('--release-id', required=True)
    p.add_argument('--agent-state-dir', required=True)
    p.add_argument('--runtime-dir', default='/run/ru-routes-managed-bird')
    a = p.parse_args()
    print(json.dumps(audit(a.storage, a.txid, a.role, a.release_id,
                           a.agent_state_dir, a.runtime_dir), indent=2))


if __name__ == '__main__':
    main()
