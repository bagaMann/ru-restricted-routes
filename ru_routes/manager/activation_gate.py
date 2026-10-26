"""Strict read-only pre-activation gate for static-only managed BIRD.

Combines manager intent, authenticated remote preflight, agent intent,
transition history, and recent root-generated runtime reports. No rights
are granted and no route is applied. A result is point-in-time evidence.
"""
import argparse
import json

from ru_routes.manager.check_agents import check_agents
from ru_routes.manager.reconcile_intents import reconcile
from ru_routes.manager.check_transitions import check_pair as check_transitions
from ru_routes.manager.check_runtime import check_pair as check_runtime


def inspect_gate(release_dir, journal_dir, primary_host, backup_host, *,
                 known_hosts, identity_file, user='routesync'):
    args = (release_dir, journal_dir, primary_host, backup_host)
    kwargs = {'known_hosts': known_hosts, 'identity_file': identity_file,
              'user': user}
    preflight = check_agents(*args, **kwargs)
    intents = reconcile(*args, **kwargs)
    transitions = check_transitions(*args, **kwargs)
    runtime = check_runtime(*args, **kwargs)

    expected = (
        ('preflight', preflight, 'both_remote_preflights_verified_not_active'),
        ('intents', intents, 'all_three_intents_match_not_active'),
        ('transitions', transitions,
         'both_transition_journals_restored_not_live_attested'),
        ('runtime', runtime, 'both_recent_live_baselines_verified_not_authorized'),
    )
    txids = set()
    releases = set()
    for name, item, status in expected:
        if item.get('status') != status:
            raise ValueError(f'{name}: failed gate: {item.get("status")}')
        txids.add(item['txid'])
        releases.add(item['release_id'])
    if len(txids) != 1 or len(releases) != 1:
        raise ValueError('cross-check failed: release or transaction identity differs')
    if (runtime.get('live_runtime_checked') is not True or
            runtime.get('activation_authorized') is not False or
            transitions.get('activation_authorized') is not False):
        raise ValueError('unexpected activation or runtime attestation flags')

    for role in ('backup', 'primary'):
        receipt = preflight['agents'][role]
        intent = intents['agents'][role]
        entry = transitions['reports'][role]
        current = runtime['reports'][role]
        if (receipt['role'] != role or intent['role'] != role or
                entry['role'] != role or
                entry['status'] != 'baseline_restored' or
                current['status'] != 'live_baseline_verified' or
                current['journal_state'] != 'baseline_restored'):
            raise ValueError(f'{role}: remote attestations disagree')
        if intent['txid'] != preflight['txid'] or entry['txid'] != preflight['txid']:
            raise ValueError(f'{role}: remote transaction disagrees')
        if (intent['release_id'] != preflight['release_id'] or
                entry['release_id'] != preflight['release_id'] or
                receipt['release_id'] != preflight['release_id']):
            raise ValueError(f'{role}: remote release disagrees')

    return {
        'status': 'static_only_activation_gate_passed_no_write_authority',
        'txid': preflight['txid'], 'release_id': preflight['release_id'],
        'proposed_order': ['backup', 'primary'],
        'last_observed': runtime['reports'],
        'all_preflight_receipts_verified': True,
        'all_intents_match': True,
        'both_transition_journals_restored': True,
        'recent_live_baselines_verified': True,
        'activation_authorized': False,
        'activation_performed': False,
        'still_required': [
            'new explicit write-capable agent protocol',
            'separate tightly scoped authentication and authorization',
            'atomic manager transaction state transitions and rollback',
            'failure injection and reconnection tests',
            'independent BGP export verification before BGP activation',
        ],
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('release-dir', 'journal-dir', 'primary-host',
                 'backup-host', 'known-hosts', 'identity-file'):
        p.add_argument('--' + name, required=True)
    p.add_argument('--user', default='routesync')
    a = p.parse_args()
    report = inspect_gate(a.release_dir, a.journal_dir,
                          a.primary_host, a.backup_host,
                          known_hosts=a.known_hosts,
                          identity_file=a.identity_file,
                          user=a.user)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
