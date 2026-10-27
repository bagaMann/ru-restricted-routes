"""Manager probes separate unprivileged control SSH channel, no writes."""
import argparse
import json
from pathlib import Path
import re
import subprocess

from ru_routes.agent.stage import verify_release
from ru_routes.manager.check_agents import HOST
from ru_routes.manager.transaction_journal import journal_status

SHA = re.compile(r'^[0-9a-f]{64}$')


def probe(host, role, txid, release_id, *, known_hosts, identity_file,
          user='routectl', ssh='ssh', timeout=20):
    if role not in ('primary', 'backup') or not HOST.fullmatch(host) or not HOST.fullmatch(user):
        raise ValueError('invalid SSH identity')
    if not Path(known_hosts).is_file() or not Path(identity_file).is_file():
        raise FileNotFoundError('pinned SSH files required')
    cmd = [ssh, '-T', '-i', str(identity_file), '-o', 'IdentitiesOnly=yes',
           '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
           '-o', f'UserKnownHostsFile={known_hosts}',
           '-o', 'ConnectTimeout=10', '--', f'{user}@{host}', 'probe']
    payload = json.dumps({'schema': 1, 'operation': 'probe', 'txid': txid,
                          'release_id': release_id, 'role': role}).encode()
    run = subprocess.run(cmd, input=payload, capture_output=True, timeout=timeout)
    if run.returncode:
        raise RuntimeError(f'{role}: control channel probe rejected: '
                           f'{run.stderr.decode(errors="replace")[:350]}')
    if len(run.stdout) > 8192:
        raise ValueError(f'{role}: oversized control reply')
    try:
        obj = json.loads(run.stdout)
    except (UnicodeError, ValueError) as exc:
        raise ValueError(f'{role}: invalid control reply JSON') from exc
    if not isinstance(obj, dict) or set(obj) != {
            'schema', 'operation', 'status', 'txid', 'release_id',
            'role', 'target_sha256', 'baseline_sha256',
            'activation_authorized', 'activation_performed'}:
        raise ValueError(f'{role}: unsupported control response')
    if (type(obj['schema']) is not int or obj['schema'] != 1 or
            obj['operation'] != 'probe' or
            obj['status'] != 'control_channel_probe_only_not_authorized' or
            obj['txid'] != txid or obj['release_id'] != release_id or
            obj['role'] != role or obj['activation_authorized'] is not False or
            obj['activation_performed'] is not False or
            any(type(obj[k]) is not str or not SHA.fullmatch(obj[k])
                for k in ('target_sha256', 'baseline_sha256'))):
        raise ValueError(f'{role}: incorrect probe identity or permissions')
    return obj


def check_pair(release_dir, journal_dir, primary_host, backup_host, *,
               known_hosts, identity_file, user='routectl'):
    if primary_host == backup_host:
        raise ValueError('control agent SSH hosts must differ')
    ids = [verify_release(release_dir, role) for role in ('backup', 'primary')]
    if ids[0] != ids[1]:
        raise ValueError('release mismatch')
    pending = [tx for tx in journal_status(journal_dir)['transactions']
               if tx['state'] == 'intent_prepared_not_active']
    if len(pending) != 1 or pending[0]['release_id'] != ids[0]:
        raise ValueError('prepared manager transaction required')
    txid = pending[0]['id']
    reports = {}
    for role, host in (('backup', backup_host), ('primary', primary_host)):
        reports[role] = probe(host, role, txid, ids[0],
                              known_hosts=known_hosts, identity_file=identity_file,
                              user=user)
    return {'status': 'both_separate_control_ssh_probes_verified_no_write',
            'txid': txid, 'release_id': ids[0], 'reports': reports,
            'activation_authorized': False, 'activation_performed': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ('release-dir', 'journal-dir', 'primary-host', 'backup-host',
                'known-hosts', 'identity-file'):
        p.add_argument('--' + key, required=True)
    p.add_argument('--user', default='routectl')
    args = p.parse_args()
    print(json.dumps(check_pair(args.release_dir, args.journal_dir,
                                args.primary_host, args.backup_host,
                                known_hosts=args.known_hosts,
                                identity_file=args.identity_file,
                                user=args.user), indent=2))


if __name__ == '__main__':
    main()
