"""Strict local control-request validation; PREPARE_CHECK is read-only.

This is deliberately NOT an SSH command endpoint or a write capability.
No request may carry arbitrary shell commands, filesystem paths, or BIRD
configure arguments. Future write operations require a separate protocol.
"""
import argparse
import json
import re
import sys

from ru_routes.agent.intent import TX, status as intent_status
from ru_routes.agent.recovery_bundle import verify_bundle
from ru_routes.agent.recover_runtime import reconcile_runtime

RELEASE = re.compile(r'^[0-9a-f]{64}$')
MAX_REQUEST_BYTES = 1024
FIELDS = {'schema', 'operation', 'txid', 'release_id', 'role'}
OPERATIONS = {'prepare_check'}


def validate_request(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_REQUEST_BYTES:
        raise ValueError('request invalid or oversized')
    try:
        request = json.loads(raw)
    except (UnicodeError, ValueError) as exc:
        raise ValueError('invalid JSON request') from exc
    if not isinstance(request, dict) or set(request) != FIELDS:
        raise ValueError('invalid control request fields')
    if type(request['schema']) is not int or request['schema'] != 1:
        raise ValueError('unsupported control schema')
    if request['operation'] not in OPERATIONS:
        raise ValueError('unsupported read-only operation')
    if (not isinstance(request['txid'], str) or
            not TX.fullmatch(request['txid']) or
            not isinstance(request['release_id'], str) or
            not RELEASE.fullmatch(request['release_id']) or
            request['role'] not in ('primary', 'backup')):
        raise ValueError('invalid transaction identity')
    return request


def process(raw, *, storage, role, release_id, agent_state_dir,
            runtime_dir, transition_dir):
    request = validate_request(raw)
    if request['role'] != role or request['release_id'] != release_id:
        raise ValueError('request does not match fixed agent identity')

    prepared = intent_status(agent_state_dir)
    if (prepared.get('status') != 'agent_prepared_not_active' or
            prepared.get('txid') != request['txid'] or
            prepared.get('role') != role or
            prepared.get('release_id') != release_id):
        raise ValueError('matching prepared intent required')
    snapshot = verify_bundle(storage, request['txid'], role,
                             release_id=release_id)
    runtime = reconcile_runtime(storage, request['txid'], role, release_id,
                                agent_state_dir, runtime_dir, transition_dir)
    if (runtime.get('reconciled') is not True or
            runtime.get('baseline_empty_verified') is not True or
            runtime.get('runtime_checked') is not True or
            runtime.get('journal_state') != 'baseline_restored'):
        raise ValueError('current managed BIRD baseline not verified')
    return {'schema': 1,
            'status': 'local_control_precheck_verified_not_authorized',
            'operation': 'prepare_check',
            'txid': request['txid'], 'release_id': release_id, 'role': role,
            'target_sha256': snapshot['target_sha256'],
            'baseline_sha256': snapshot['baseline_sha256'],
            'managed_pid': runtime['current_pid'],
            'activation_authorized': False,
            'activation_performed': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--storage', required=True)
    p.add_argument('--role', required=True, choices=('primary', 'backup'))
    p.add_argument('--release-id', required=True)
    p.add_argument('--agent-state-dir', required=True)
    p.add_argument('--runtime-dir', default='/run/ru-routes-managed-bird')
    p.add_argument('--transition-dir', required=True)
    a = p.parse_args()
    if hasattr(sys.stdin, 'buffer'):
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    else:
        raw = sys.stdin.read(MAX_REQUEST_BYTES + 1).encode()
    result = process(raw, storage=a.storage, role=a.role,
                     release_id=a.release_id,
                     agent_state_dir=a.agent_state_dir,
                     runtime_dir=a.runtime_dir,
                     transition_dir=a.transition_dir)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
