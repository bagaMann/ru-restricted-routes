"""Read-only forced-command SSH endpoint for inspecting an agent receipt.

Provision a separate SSH public key with a fixed command in authorized_keys.
The request controls only a 64-character release ID, never file paths or role.
No BIRD, networking or filesystem write actions.
"""
import argparse
import json
import re
import sys

from ru_routes.agent.receipt import inspect_receipt
from ru_routes.agent.intent import status as intent_status
from ru_routes.agent.transition_journal import history as transition_history
from ru_routes.agent.runtime_report import read_report
from ru_routes.agent.control_journal import status as control_status

_RELEASE = re.compile(r'^[0-9a-f]{64}$')
MAX_REQUEST = 1024


def respond(data, *, role, inbox, state_dir, agent_state_dir=None,
            transition_dir=None, runtime_report_dir=None, control_state_dir=None):
    if role not in ('primary', 'backup'):
        raise ValueError('unknown fixed agent role')
    if len(data) > MAX_REQUEST:
        raise ValueError('status request too long')
    request = json.loads(data)
    if not isinstance(request, dict) or set(request) not in (
            {'schema', 'release_id'}, {'schema', 'release_id', 'query'}):
        raise ValueError('invalid status request fields')
    release_id = request['release_id']
    if request['schema'] != 1 or not isinstance(release_id, str) or not _RELEASE.fullmatch(release_id):
        raise ValueError('invalid status request identity')
    if request.get('query', 'receipt') == 'receipt':
        return inspect_receipt(inbox, release_id, role, state_dir)
    if request['query'] == 'control' and control_state_dir:
        record = control_status(control_state_dir)
        if (record.get('state') != 'control_prepared_not_authorized_not_active' or
                record.get('release_id') != release_id or record.get('role') != role):
            raise ValueError('no matching control preparation')
        return {**record, 'activation_authorized': False,
                'activation_performed': False}
    if request['query'] == 'runtime' and runtime_report_dir:
        report = read_report(runtime_report_dir, release_id, role)
        return report
    if request['query'] == 'transition' and transition_dir:
        report = transition_history(transition_dir)
        entries = report['entries']
        if (not entries or entries[-1]['release_id'] != release_id or
                entries[-1]['role'] != role):
            raise ValueError('no matching transition journal')
        # Return only the validated latest identity/state, not an unbounded log.
        return {'status': report['status'], 'release_id': release_id,
                'role': role, 'txid': entries[-1]['txid'],
                'recorded_pid': entries[-1]['pid'],
                'baseline_sha256': entries[-1]['baseline_sha256'],
                'target_sha256': entries[-1]['target_sha256'],
                'recorded_at_utc': entries[-1]['recorded_at_utc'],
                'source': 'durable_transition_journal_not_live_runtime'}
    if request['query'] != 'intent' or not agent_state_dir:
        raise ValueError('unsupported status query')
    record = intent_status(agent_state_dir)
    if (record.get('status') != 'agent_prepared_not_active' or
            record.get('release_id') != release_id or record.get('role') != role):
        raise ValueError('agent has no matching prepared intent')
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--role', choices=('primary', 'backup'), required=True)
    parser.add_argument('--inbox', required=True)
    parser.add_argument('--state-dir', required=True)
    parser.add_argument('--agent-state-dir', default=None)
    parser.add_argument('--transition-dir', default=None)
    parser.add_argument('--runtime-report-dir', default=None)
    parser.add_argument('--control-state-dir', default=None)
    args = parser.parse_args()
    try:
        result = respond(sys.stdin.buffer.read(MAX_REQUEST + 1), role=args.role,
                         inbox=args.inbox, state_dir=args.state_dir,
                         agent_state_dir=args.agent_state_dir,
                         transition_dir=args.transition_dir,
                         runtime_report_dir=args.runtime_report_dir,
                         control_state_dir=args.control_state_dir)
    except (ValueError, OSError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({'status': 'rejected', 'error': str(exc)}), file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
