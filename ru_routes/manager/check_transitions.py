"""Manager reads BOTH durable transition summaries over pinned status-only SSH.

Journal evidence is NOT a live runtime attestation or authorization to
activate. Does not contact system BIRD or mutate anything.
"""
import argparse
import json
from pathlib import Path
import subprocess

from ru_routes.agent.stage import verify_release
from ru_routes.manager.check_agents import HOST
from ru_routes.manager.transaction_journal import journal_status


def fetch_transition(host, role, release_id, *, known_hosts, identity_file,
                     user='routesync', timeout=20, ssh='ssh'):
    if role not in ('primary', 'backup') or not HOST.fullmatch(host) or not HOST.fullmatch(user):
        raise ValueError('invalid SSH destination')
    if not Path(known_hosts).is_file() or not Path(identity_file).is_file():
        raise FileNotFoundError('SSH pinned key files missing')
    command = [ssh, '-T', '-i', str(identity_file), '-o', 'IdentitiesOnly=yes',
               '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
               '-o', f'UserKnownHostsFile={known_hosts}', '-o', 'ConnectTimeout=10',
               '--', f'{user}@{host}', 'status']
    payload = json.dumps({'schema': 1, 'release_id': release_id,
                          'query': 'transition'}).encode()
    result = subprocess.run(command, input=payload, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f'{role}: SSH transition read failed: '
                           f'{result.stderr.decode(errors="replace")[:350]}')
    if len(result.stdout) > 100_000:
        raise ValueError(f'{role}: oversized SSH response')
    try:
        obj = json.loads(result.stdout)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f'{role}: invalid transition JSON') from exc
    expected_keys = {'status', 'release_id', 'role', 'txid', 'recorded_pid',
                     'baseline_sha256', 'target_sha256',
                     'recorded_at_utc', 'source'}
    if (not isinstance(obj, dict) or set(obj) != expected_keys or
            obj['role'] != role or obj['release_id'] != release_id or
            obj['source'] != 'durable_transition_journal_not_live_runtime'):
        raise ValueError(f'{role}: unexpected transition identity')
    return obj


def check_pair(release_dir, journal_dir, primary_host, backup_host, *,
               known_hosts, identity_file, user='routesync'):
    if primary_host == backup_host:
        raise ValueError('agent hosts must differ')
    ids = [verify_release(release_dir, role) for role in ('primary', 'backup')]
    if ids[0] != ids[1]:
        raise ValueError('different release identifiers')
    pending = [tx for tx in journal_status(journal_dir)['transactions']
               if tx['state'] == 'intent_prepared_not_active']
    if len(pending) != 1 or pending[0]['release_id'] != ids[0]:
        raise ValueError('matching pending manager transaction required')
    reports = {}
    for role, host in (('backup', backup_host), ('primary', primary_host)):
        record = fetch_transition(host, role, ids[0], known_hosts=known_hosts,
                                  identity_file=identity_file, user=user)
        # The role-specific isolated-config hashes are not necessarily
        # recorded in the release manifest. Compare across verified
        # SSH reports and retain local bundle hashes as separate evidence.
        if record['txid'] != pending[0]['id'] or record['status'] != 'baseline_restored':
            raise ValueError(f'{role}: transition journal not fully restored')
        reports[role] = record
    return {'status': 'both_transition_journals_restored_not_live_attested',
            'txid': pending[0]['id'], 'release_id': ids[0],
            'reports': reports, 'live_runtime_checked': False,
            'activation_authorized': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--release-dir', required=True)
    p.add_argument('--journal-dir', required=True)
    p.add_argument('--primary-host', required=True)
    p.add_argument('--backup-host', required=True)
    p.add_argument('--known-hosts', required=True)
    p.add_argument('--identity-file', required=True)
    p.add_argument('--user', default='routesync')
    a = p.parse_args()
    print(json.dumps(check_pair(a.release_dir, a.journal_dir,
                                a.primary_host, a.backup_host,
                                known_hosts=a.known_hosts,
                                identity_file=a.identity_file, user=a.user), indent=2))


if __name__ == '__main__':
    main()
