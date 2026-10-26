"""Fail-closed, read-only deployment readiness plan.

Uses two independent pinned SSH queries per role (receipt + intent). The
result is informational, time-of-check evidence, NOT an activation token.
No BIRD config reload, journal mutation, or remote shell execution.
"""
import argparse
import json
from pathlib import Path

from ru_routes.manager.check_agents import check_agents
from ru_routes.manager.reconcile_intents import reconcile
from ru_routes.agent.stage import verify_release


def build_plan(release_dir, journal_dir, primary_host, backup_host, *,
               known_hosts, identity_file, user='routesync'):
    before = [verify_release(release_dir, role) for role in ('primary', 'backup')]
    if before[0] != before[1]:
        raise ValueError('release identities differ')
    kwargs = {'known_hosts': known_hosts, 'identity_file': identity_file, 'user': user}
    # Both routines independently read journal and query distinct agents.
    receipts = check_agents(release_dir, journal_dir, primary_host, backup_host, **kwargs)
    intents = reconcile(release_dir, journal_dir, primary_host, backup_host, **kwargs)
    if (receipts['status'] != 'both_remote_preflights_verified_not_active' or
            intents['status'] != 'all_three_intents_match_not_active' or
            receipts['txid'] != intents['txid'] or
            receipts['release_id'] != intents['release_id'] or
            receipts['release_id'] != before[0]):
        raise ValueError('preflight and intent evidence disagree')
    # This plan deliberately contains no executed operations.
    manifest = json.loads((Path(release_dir) / 'release.json').read_text(encoding='utf-8'))
    routes = {}
    for role in ('backup', 'primary'):
        expected = manifest['servers'][role]['routers']
        reported = receipts['agents'][role]['routers']
        expected_digest = {
            name: {'prefix_count': spec['prefix_count'],
                   'prefix_sha256': spec['prefix_sha256']}
            for name, spec in expected.items()
        }
        if reported != expected_digest:
            raise ValueError(f'{role}: receipt routes differ from release')
        routes[role] = expected_digest
        if intents['agents'][role]['txid'] != intents['txid']:
            raise ValueError(f'{role}: stale agent intent')

    after = [verify_release(release_dir, role) for role in ('primary', 'backup')]
    if after != before:
        raise ValueError('release mutated during checks')
    return {
        'status': 'readiness_plan_only_not_authorized_not_active',
        'release_id': before[0],
        'txid': intents['txid'],
        'proposed_order': ['backup', 'primary'],
        'hosts': {'backup': backup_host, 'primary': primary_host},
        'routes': routes,
        'checks': ['pinned_ssh_preflight', 'pinned_ssh_intent',
                   'manager_journal_pending', 'route_digests'],
        'not_checked': ['live_bgp_sessions', 'live_exported_routes',
                        'rollback_snapshot', 'runtime_config_ownership',
                        'agent_activation_capability'],
        'activation_performed': False,
    }


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
    print(json.dumps(build_plan(
        a.release_dir, a.journal_dir, a.primary_host, a.backup_host,
        known_hosts=a.known_hosts, identity_file=a.identity_file,
        user=a.user), indent=2))


if __name__ == '__main__':
    main()
