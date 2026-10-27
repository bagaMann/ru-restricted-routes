"""Verify both immutable local control-preparation receipts via status SSH.

No SSH command privileges, no BIRD writes, and no authorization grant.
A receipt is historical preparation evidence, not current PID evidence.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess

from ru_routes.agent.stage import verify_release
from ru_routes.manager.check_agents import HOST
from ru_routes.manager.transaction_journal import journal_status

HASH = re.compile(r'^[0-9a-f]{64}$')
TX = re.compile(r'^tx-[0-9a-f]{64}$')
FIELDS = {'state', 'txid', 'release_id', 'role', 'target_sha256',
          'baseline_sha256', 'managed_pid', 'prepared_at_utc',
          'activation_authorized', 'activation_performed'}


def fetch_control(host, role, release_id, *, known_hosts, identity_file,
                  user='routesync', ssh='ssh', timeout=20):
    if (role not in ('backup', 'primary') or not HOST.fullmatch(host) or
            not HOST.fullmatch(user)):
        raise ValueError('invalid SSH destination')
    if not Path(known_hosts).is_file() or not Path(identity_file).is_file():
        raise FileNotFoundError('pinned SSH files missing')
    cmd = [ssh, '-T', '-i', str(identity_file), '-o', 'IdentitiesOnly=yes',
           '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
           '-o', f'UserKnownHostsFile={known_hosts}',
           '-o', 'ConnectTimeout=10', '--', f'{user}@{host}', 'status']
    input_data = json.dumps({'schema': 1, 'release_id': release_id,
                             'query': 'control'}).encode()
    result = subprocess.run(cmd, input=input_data,
                            capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f'{role}: remote control journal unavailable: '
                           f'{result.stderr.decode(errors="replace")[:350]}')
    if len(result.stdout) > 12000:
        raise ValueError(f'{role}: oversized control reply')
    obj = json.loads(result.stdout)
    if (not isinstance(obj, dict) or set(obj) != FIELDS or
            obj['state'] != 'control_prepared_not_authorized_not_active' or
            obj['role'] != role or obj['release_id'] != release_id or
            type(obj['managed_pid']) is not int or obj['managed_pid'] < 2 or
            obj['activation_authorized'] is not False or
            obj['activation_performed'] is not False or
            not isinstance(obj['txid'], str) or not TX.fullmatch(obj['txid']) or
            any(not isinstance(obj[key], str) or not HASH.fullmatch(obj[key])
                for key in ('target_sha256', 'baseline_sha256'))):
        raise ValueError(f'{role}: invalid control preparation evidence')
    return obj


def check_pair(release_dir, journal_dir, primary_host, backup_host, *,
               known_hosts, identity_file, user='routesync'):
    if primary_host == backup_host:
        raise ValueError('primary and backup hosts must be distinct')
    identities = [verify_release(release_dir, role)
                  for role in ('backup', 'primary')]
    if identities[0] != identities[1]:
        raise ValueError('different release IDs')
    pending = [tx for tx in journal_status(journal_dir)['transactions']
               if tx['state'] == 'intent_prepared_not_active']
    if len(pending) != 1 or pending[0]['release_id'] != identities[0]:
        raise ValueError('matching manager intent required')
    reports = {}
    for role, host in (('backup', backup_host), ('primary', primary_host)):
        item = fetch_control(host, role, identities[0],
                             known_hosts=known_hosts,
                             identity_file=identity_file, user=user)
        if item['txid'] != pending[0]['id']:
            raise ValueError(f'{role}: control preparation txid mismatch')
        reports[role] = item
    return {'status': 'both_control_preparations_match_not_authorized',
            'txid': pending[0]['id'], 'release_id': identities[0],
            'reports': reports, 'activation_authorized': False,
            'activation_performed': False,
            'live_runtime_checked': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for field in ('release-dir', 'journal-dir', 'primary-host',
                  'backup-host', 'known-hosts', 'identity-file'):
        p.add_argument('--' + field, required=True)
    p.add_argument('--user', default='routesync')
    args = p.parse_args()
    print(json.dumps(check_pair(args.release_dir, args.journal_dir,
                                args.primary_host, args.backup_host,
                                known_hosts=args.known_hosts,
                                identity_file=args.identity_file,
                                user=args.user), indent=2))


if __name__ == '__main__':
    main()
