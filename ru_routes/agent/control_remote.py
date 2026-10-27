"""Fixed-command SSH control-channel probe: deliberately NO write verbs.

This is a separate, unprivileged handshake endpoint for a future managed
command channel. Only one exact bounded JSON shape is accepted. The caller
cannot choose paths or roles and cannot invoke shell/BIRD/system commands.
"""
import argparse
import json
import re
import sys

from ru_routes.agent.control_journal import status as control_status

HEX = re.compile(r'^[0-9a-f]{64}$')
TX = re.compile(r'^tx-[0-9a-f]{64}$')
MAX_REQUEST = 1024


def respond(raw, *, role, release_id, state_dir):
    if not isinstance(raw, bytes) or len(raw) > MAX_REQUEST:
        raise ValueError('control probe oversized')
    try:
        request = json.loads(raw)
    except (UnicodeError, ValueError) as exc:
        raise ValueError('malformed control request') from exc
    if not isinstance(request, dict) or set(request) != {
            'schema', 'operation', 'txid', 'release_id', 'role'}:
        raise ValueError('invalid control fields')
    if (type(request['schema']) is not int or request['schema'] != 1 or
            type(request['operation']) is not str or request['operation'] != 'probe'):
        raise ValueError('unsupported control operation')
    if (type(request['txid']) is not str or not TX.fullmatch(request['txid']) or
            type(request['release_id']) is not str or
            not HEX.fullmatch(request['release_id']) or
            type(request['role']) is not str or
            request['role'] not in ('primary', 'backup')):
        raise ValueError('invalid control identity')
    if (role not in ('primary', 'backup') or not HEX.fullmatch(release_id) or
            request['role'] != role or request['release_id'] != release_id):
        raise ValueError('request does not match fixed agent')
    record = control_status(state_dir)
    if (record.get('state') != 'control_prepared_not_authorized_not_active' or
            any(record.get(k) != request[k] for k in ('txid', 'release_id', 'role'))):
        raise ValueError('matching one-shot control preparation required')
    return {'schema': 1, 'operation': 'probe',
            'status': 'control_channel_probe_only_not_authorized',
            'txid': request['txid'], 'release_id': release_id, 'role': role,
            'target_sha256': record['target_sha256'],
            'baseline_sha256': record['baseline_sha256'],
            'activation_authorized': False, 'activation_performed': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--role', required=True, choices=('primary', 'backup'))
    p.add_argument('--release-id', required=True)
    p.add_argument('--state-dir', required=True)
    args = p.parse_args()
    try:
        result = respond(sys.stdin.buffer.read(MAX_REQUEST + 1),
                         role=args.role, release_id=args.release_id,
                         state_dir=args.state_dir)
    except (TypeError, ValueError, OSError, KeyError) as exc:
        print(json.dumps({'status': 'rejected', 'error': str(exc)}),
              file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
