"""Check recent root-generated live runtime snapshots from both agents via SSH.

A snapshot is evidence about a past instant, NOT permission to activate BGP.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

from ru_routes.agent.stage import verify_release
from ru_routes.manager.check_agents import HOST
from ru_routes.manager.transaction_journal import journal_status


def fetch_runtime(host, role, release_id, *, known_hosts, identity_file,
                  user='routesync', ssh='ssh', timeout=20):
    if role not in ('primary', 'backup') or not HOST.fullmatch(host) or not HOST.fullmatch(user):
        raise ValueError('invalid SSH destination')
    if not Path(known_hosts).is_file() or not Path(identity_file).is_file():
        raise FileNotFoundError('pinned SSH files missing')
    cmd = [ssh, '-T', '-i', str(identity_file), '-o', 'IdentitiesOnly=yes',
           '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
           '-o', f'UserKnownHostsFile={known_hosts}', '-o', 'ConnectTimeout=10',
           '--', f'{user}@{host}', 'status']
    payload = json.dumps({'schema': 1, 'release_id': release_id,
                          'query': 'runtime'}).encode()
    process = subprocess.run(cmd, input=payload, capture_output=True, timeout=timeout)
    if process.returncode:
        raise RuntimeError(f'{role}: remote runtime report unavailable: '
                           f'{process.stderr.decode(errors="replace")[:400]}')
    if len(process.stdout) > 40000:
        raise ValueError(f'{role}: remote report oversized')
    reply = json.loads(process.stdout)
    if not isinstance(reply, dict) or set(reply) != {
            'schema', 'txid', 'release_id', 'role', 'checked_at_utc',
            'status', 'evidence', 'sha256'}:
        raise ValueError(f'{role}: invalid runtime report fields')
    if (reply['schema'] != 1 or reply['release_id'] != release_id or
            reply['role'] != role or reply['status'] != 'live_baseline_verified'):
        raise ValueError(f'{role}: runtime not verified')
    from ru_routes.manager.transaction_journal import _canonical
    import hashlib
    data = {key: value for key, value in reply.items() if key != 'sha256'}
    if hashlib.sha256(_canonical(data)).hexdigest() != reply['sha256']:
        raise ValueError(f'{role}: runtime report checksum mismatch')
    stamp = datetime.fromisoformat(reply['checked_at_utc'])
    if stamp.tzinfo is None or not -10 <= (datetime.now(timezone.utc) - stamp).total_seconds() <= 120:
        raise ValueError(f'{role}: stale runtime report')
    proof = reply['evidence']
    if (not isinstance(proof, dict) or proof.get('reconciled') is not True or
            proof.get('baseline_empty_verified') is not True or
            proof.get('runtime_checked') is not True or
            proof.get('journal_state') != 'baseline_restored' or
            proof.get('txid') != reply['txid'] or
            proof.get('release_id') != release_id or proof.get('role') != role):
        raise ValueError(f'{role}: inconsistent runtime report evidence')
    return reply


def check_pair(release_dir, journal_dir, primary_host, backup_host, *,
               known_hosts, identity_file, user='routesync'):
    if primary_host == backup_host:
        raise ValueError('agent SSH hosts must differ')
    ids = [verify_release(release_dir, role) for role in ('primary', 'backup')]
    if ids[0] != ids[1]:
        raise ValueError('different role releases')
    pending = [tx for tx in journal_status(journal_dir)['transactions']
               if tx['state'] == 'intent_prepared_not_active']
    if len(pending) != 1 or pending[0]['release_id'] != ids[0]:
        raise ValueError('pending manager tx does not match release')
    reports = {}
    for role, host in (('backup', backup_host), ('primary', primary_host)):
        report = fetch_runtime(host, role, ids[0], known_hosts=known_hosts,
                               identity_file=identity_file, user=user)
        if report['txid'] != pending[0]['id']:
            raise ValueError(f'{role}: runtime report txid mismatch')
        reports[role] = {'status': report['status'],
                         'checked_at_utc': report['checked_at_utc'],
                         'current_pid': report['evidence']['current_pid'],
                         'journal_state': report['evidence']['journal_state']}
    return {'status': 'both_recent_live_baselines_verified_not_authorized',
            'txid': pending[0]['id'], 'release_id': ids[0],
            'reports': reports, 'live_runtime_checked': True,
            'activation_authorized': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for arg in ('release-dir', 'journal-dir', 'primary-host', 'backup-host',
                'known-hosts', 'identity-file'):
        p.add_argument('--' + arg, required=True)
    p.add_argument('--user', default='routesync')
    a = p.parse_args()
    print(json.dumps(check_pair(a.release_dir, a.journal_dir,
                                a.primary_host, a.backup_host,
                                known_hosts=a.known_hosts,
                                identity_file=a.identity_file, user=a.user), indent=2))


if __name__ == '__main__':
    main()
