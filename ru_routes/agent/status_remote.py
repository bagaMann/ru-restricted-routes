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

_RELEASE = re.compile(r'^[0-9a-f]{64}$')
MAX_REQUEST = 1024


def respond(data, *, role, inbox, state_dir):
    if role not in ('primary', 'backup'):
        raise ValueError('unknown fixed agent role')
    if len(data) > MAX_REQUEST:
        raise ValueError('status request too long')
    request = json.loads(data)
    if not isinstance(request, dict) or set(request) != {'schema', 'release_id'}:
        raise ValueError('invalid status request fields')
    release_id = request['release_id']
    if request['schema'] != 1 or not isinstance(release_id, str) or not _RELEASE.fullmatch(release_id):
        raise ValueError('invalid status request identity')
    return inspect_receipt(inbox, release_id, role, state_dir)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--role', choices=('primary', 'backup'), required=True)
    parser.add_argument('--inbox', required=True)
    parser.add_argument('--state-dir', required=True)
    args = parser.parse_args()
    try:
        result = respond(sys.stdin.buffer.read(MAX_REQUEST + 1), role=args.role,
                         inbox=args.inbox, state_dir=args.state_dir)
    except (ValueError, OSError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({'status': 'rejected', 'error': str(exc)}), file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
