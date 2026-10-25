"""Ship immutable releases to unprivileged remote agents over pinned-key SSH.

No BIRD reload or apply. SSH user/host settings must be provisioned separately.
"""
import argparse
import base64
import json
from pathlib import Path
import re
import shlex
import subprocess

from ru_routes.agent.stage import verify_release

IDENTITY = re.compile(r'^[a-zA-Z0-9_.@-]{1,255}$')


def make_transfer(release_dir, role):
    release_dir = Path(release_dir)
    release_id = verify_release(release_dir, role)
    message = {
        'schema': 1,
        'role': role,
        'manifest': base64.b64encode((release_dir / 'release.json').read_bytes()).decode('ascii'),
        'config': base64.b64encode((release_dir / f'{role}.bird.conf').read_bytes()).decode('ascii'),
    }
    return release_id, json.dumps(message, separators=(',', ':')).encode('utf-8')


def ship(release_dir, role, host, remote_inbox, known_hosts, user='routesync', ssh='ssh', identity_file=None):
    if not IDENTITY.fullmatch(host) or not IDENTITY.fullmatch(user):
        raise ValueError('invalid SSH host or user')
    known_hosts = Path(known_hosts)
    if not known_hosts.is_file():
        raise FileNotFoundError(f'SSH pinned known_hosts is missing: {known_hosts}')
    if not Path(remote_inbox).is_absolute():
        raise ValueError('remote inbox must be an absolute path')
    if identity_file is not None and not Path(identity_file).is_file():
        raise FileNotFoundError(f'SSH identity file missing: {identity_file}')
    release_id, payload = make_transfer(release_dir, role)
    remote_command = ' '.join(shlex.quote(s) for s in
        ('python3', '-m', 'ru_routes.agent.remote', '--role', role,
         '--inbox', str(remote_inbox)))
    command = [ssh, '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
               '-o', f'UserKnownHostsFile={known_hosts}', '-o', 'ConnectTimeout=10',
               '--', f'{user}@{host}', remote_command]
    if identity_file is not None:
        command[1:1] = ['-i', str(identity_file), '-o', 'IdentitiesOnly=yes']
    completed = subprocess.run(command, input=payload, capture_output=True, timeout=45)
    if completed.returncode:
        raise RuntimeError(f'agent {role} rejected transfer or SSH failed: '
                           f'{completed.stderr.decode(errors="replace")[:300]}')
    try:
        response = json.loads(completed.stdout)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError('agent response was not valid JSON') from exc
    if response != {'role': role, 'release_id': release_id, 'status': 'prepared_not_activated'}:
        raise RuntimeError('agent did not confirm the expected prepared release')
    return response


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--release-dir', type=Path, required=True)
    p.add_argument('--role', choices=('primary', 'backup'), required=True)
    p.add_argument('--host', required=True)
    p.add_argument('--user', default='routesync')
    p.add_argument('--remote-inbox', type=Path, required=True)
    p.add_argument('--known-hosts', type=Path, required=True)
    p.add_argument('--identity-file', type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(ship(a.release_dir, a.role, a.host, a.remote_inbox,
                          a.known_hosts, a.user, identity_file=a.identity_file), indent=2))


if __name__ == '__main__':
    main()
