"""Read-only reconciliation of manager and both remote agent intent journals.

This requires a status-only SSH forced command installed on BOTH agents with
--agent-state-dir. It never writes journals or activates BIRD.
"""
import argparse
import json
from pathlib import Path
import subprocess

from ru_routes.agent.stage import verify_release
from ru_routes.manager.check_agents import HOST
from ru_routes.manager.transaction_journal import journal_status


def fetch_intent(host, role, release_id, *, known_hosts, identity_file,
                 user='routesync', ssh='ssh', timeout=20):
    if role not in ('primary', 'backup') or not HOST.fullmatch(host) or not HOST.fullmatch(user):
        raise ValueError('invalid SSH host, user or role')
    if not Path(known_hosts).is_file() or not Path(identity_file).is_file():
        raise FileNotFoundError('pinned host keys and status-only SSH identity required')
    command = [ssh, '-T', '-i', str(identity_file), '-o', 'IdentitiesOnly=yes',
               '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
               '-o', f'UserKnownHostsFile={known_hosts}', '-o', 'ConnectTimeout=10',
               '--', f'{user}@{host}', 'status']
    request = json.dumps({'schema': 1, 'release_id': release_id, 'query': 'intent'}).encode()
    output = subprocess.run(command, input=request, capture_output=True, timeout=timeout)
    if output.returncode:
        raise RuntimeError(f'{role}: remote agent intent unavailable: '
                           f'{output.stderr.decode(errors="replace")[:350]}')
    if len(output.stdout) > 100_000:
        raise ValueError(f'{role}: oversized remote intent')
    try:
        obj = json.loads(output.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f'{role}: malformed remote intent') from exc
    if not isinstance(obj, dict) or set(obj) != {
            'txid', 'release_id', 'role', 'status', 'created_at_utc', 'closed_at_utc'}:
        raise ValueError(f'{role}: invalid agent intent fields')
    if (obj['role'] != role or obj['release_id'] != release_id or
            obj['status'] != 'agent_prepared_not_active' or
            obj['closed_at_utc'] is not None or not isinstance(obj['txid'], str)):
        raise ValueError(f'{role}: unexpected prepared intent identity')
    return obj


def reconcile(release_dir, journal_dir, primary_host, backup_host, *,
              known_hosts, identity_file, user='routesync'):
    if primary_host == backup_host:
        raise ValueError('primary and backup SSH hosts must differ')
    ids = [verify_release(release_dir, role) for role in ('primary', 'backup')]
    if ids[0] != ids[1]:
        raise ValueError('release identities differ between roles')
    release_id = ids[0]
    pending = [tx for tx in journal_status(journal_dir)['transactions']
               if tx['state'] == 'intent_prepared_not_active']
    if len(pending) != 1 or pending[0]['release_id'] != release_id:
        raise ValueError('matching pending manager intent required')
    txid = pending[0]['id']
    agents = {}
    for role, host in (('primary', primary_host), ('backup', backup_host)):
        obj = fetch_intent(host, role, release_id, known_hosts=known_hosts,
                           identity_file=identity_file, user=user)
        if obj['txid'] != txid:
            raise ValueError(f'{role}: remote agent txid does not match manager')
        agents[role] = obj
    return {'status': 'all_three_intents_match_not_active',
            'txid': txid, 'release_id': release_id, 'agents': agents}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-dir', required=True)
    parser.add_argument('--journal-dir', required=True)
    parser.add_argument('--primary-host', required=True)
    parser.add_argument('--backup-host', required=True)
    parser.add_argument('--known-hosts', required=True)
    parser.add_argument('--identity-file', required=True)
    parser.add_argument('--user', default='routesync')
    a = parser.parse_args()
    print(json.dumps(reconcile(a.release_dir, a.journal_dir,
                               a.primary_host, a.backup_host,
                               known_hosts=a.known_hosts,
                               identity_file=a.identity_file, user=a.user), indent=2))


if __name__ == '__main__':
    main()
