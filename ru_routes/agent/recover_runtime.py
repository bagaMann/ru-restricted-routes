"""Read-only restart/recovery reconciliation of the owned managed BIRD.

Report independently: durable transition outcome, current process PID and
actual baseline tables. Incomplete journal state is NEVER silently repaired.
No birdc configure, no journal mutation, no system BIRD access.
"""
import argparse
import json

from ru_routes.agent.managed_runtime import audit
from ru_routes.agent.recovery_bundle import verify_bundle
from ru_routes.agent.transition_journal import history

CLOSED = 'baseline_restored'


def reconcile_runtime(storage, txid, role, release_id, agent_state_dir,
                      runtime_dir, journal_dir, *, birdc='birdc'):
    manifest = verify_bundle(storage, txid, role, release_id=release_id)
    saved = history(journal_dir)
    entries = saved['entries']
    if not entries:
        return {'status': 'no_transition_history_requires_review',
                'txid': txid, 'role': role, 'release_id': release_id,
                'journal_state': saved['status'],
                'runtime_checked': False, 'reconciled': False}

    expected = ('txid', 'role', 'release_id', 'baseline_sha256', 'target_sha256')
    values = {'txid': txid, 'role': role, 'release_id': release_id,
              'baseline_sha256': manifest['baseline_sha256'],
              'target_sha256': manifest['target_sha256']}
    for item in entries:
        if any(item[field] != values[field] for field in expected):
            raise ValueError('journal identity/digests differ from recovery bundle')

    # audit() compares actual /proc argv, the dedicated socket and exact empty
    # route tables. A journal alone is not proof of runtime state.
    try:
        running = audit(storage, txid, role, release_id,
                        agent_state_dir, runtime_dir, birdc=birdc)
    except (ValueError, RuntimeError, OSError, TimeoutError) as exc:
        return {'status': 'runtime_not_verified_manual_review',
                'txid': txid, 'role': role, 'release_id': release_id,
                'journal_state': saved['status'],
                'runtime_checked': True, 'reconciled': False,
                'diagnostic': str(exc)[:500]}

    last = entries[-1]
    if saved['status'] != CLOSED:
        result = 'incomplete_journal_runtime_empty_requires_review'
    elif running['managed_pid'] != last['pid']:
        result = 'baseline_verified_after_managed_bird_restart'
    else:
        result = 'baseline_verified_same_managed_bird_process'
    return {'status': result, 'txid': txid, 'role': role,
            'release_id': release_id, 'journal_state': saved['status'],
            'recorded_pid': last['pid'],
            'current_pid': running['managed_pid'],
            'runtime_checked': True,
            'baseline_empty_verified': True,
            'reconciled': saved['status'] == CLOSED,
            'activation_performed': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--storage', required=True)
    p.add_argument('--txid', required=True)
    p.add_argument('--role', choices=('primary', 'backup'), required=True)
    p.add_argument('--release-id', required=True)
    p.add_argument('--agent-state-dir', required=True)
    p.add_argument('--runtime-dir', default='/run/ru-routes-managed-bird')
    p.add_argument('--journal-dir', required=True)
    a = p.parse_args()
    print(json.dumps(reconcile_runtime(
        a.storage, a.txid, a.role, a.release_id,
        a.agent_state_dir, a.runtime_dir, a.journal_dir), indent=2))


if __name__ == '__main__':
    main()
