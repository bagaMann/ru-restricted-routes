"""Query primary and backup preflight status over independent pinned SSH sessions.

Use a dedicated SSH identity authorized ONLY for the agent's read-only
forced command. Responses are verified against the release and pending
manager intent; this module cannot advance transactions or activate BIRD.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess

from ru_routes.manager.review_receipts import review_pair

HOST = re.compile(r'^[a-zA-Z0-9_.-]{1,253}$')


def fetch_status(host, role, release_id, *, known_hosts, identity_file,
                 user='routesync', ssh='ssh', timeout=20):
    if role not in ('primary', 'backup') or not HOST.fullmatch(host):
        raise ValueError('invalid target role or host')
    if not HOST.fullmatch(user):
        raise ValueError('invalid SSH user')
    if not Path(known_hosts).is_file() or not Path(identity_file).is_file():
        raise FileNotFoundError('known_hosts and dedicated SSH identity are required')
    command = [ssh, '-T', '-i', str(identity_file), '-o', 'IdentitiesOnly=yes',
               '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
               '-o', f'UserKnownHostsFile={known_hosts}',
               '-o', 'ConnectTimeout=10', '--',
               f'{user}@{host}', 'status']
    payload = json.dumps({'schema': 1, 'release_id': release_id}).encode()
    result = subprocess.run(command, input=payload, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f'{role}: SSH read-only status request failed: '
                           f'{result.stderr.decode(errors="replace")[:350]}')
    if len(result.stdout) > 600_000:
        raise ValueError(f'{role}: oversized status response')
    try:
        reply = json.loads(result.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f'{role}: status response is not JSON') from exc
    if (not isinstance(reply, dict) or
            reply.get('role') != role or reply.get('release_id') != release_id or
            reply.get('status') != 'preflight_receipt_verified_not_active'):
        raise ValueError(f'{role}: unexpected agent status identity')
    return reply


def check_agents(release_dir, journal_dir, primary_host, backup_host, *,
                 known_hosts, identity_file, user='routesync'):
    from ru_routes.agent.stage import verify_release
    from ru_routes.manager.transaction_journal import journal_status
    identities = [verify_release(release_dir, role) for role in ('primary', 'backup')]
    if identities[0] != identities[1]:
        raise ValueError('role releases do not match')
    release_id = identities[0]
    pending = [item for item in journal_status(journal_dir)['transactions']
               if item['state'] == 'intent_prepared_not_active']
    if len(pending) != 1 or pending[0]['release_id'] != release_id:
        raise ValueError('no matching pending manager transaction')
    if primary_host == backup_host:
        raise ValueError('primary and backup must use different SSH destinations')
    response = {}
    for role, host in (('primary', primary_host), ('backup', backup_host)):
        response[role] = fetch_status(
            host, role, release_id, known_hosts=known_hosts,
            identity_file=identity_file, user=user)
    # Match the received receipts to the authoritative release digests.
    manifest = json.loads((Path(release_dir) / 'release.json').read_text(encoding='utf-8'))
    for role, obj in response.items():
        expected = manifest['servers'][role]
        route_sets = {name: {'prefix_count': v['prefix_count'],
                             'prefix_sha256': v['prefix_sha256']}
                      for name, v in expected['routers'].items()}
        if obj['routers'] != route_sets:
            raise ValueError(f'{role}: remote routes do not match release')
    return {'status': 'both_remote_preflights_verified_not_active',
            'release_id': release_id, 'txid': pending[0]['id'],
            'agents': response}


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
    print(json.dumps(check_agents(
        a.release_dir, a.journal_dir, a.primary_host, a.backup_host,
        known_hosts=a.known_hosts, identity_file=a.identity_file, user=a.user), indent=2))


if __name__ == '__main__':
    main()
