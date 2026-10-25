"""Restricted SSH entrypoint for staging route bundles, not activating BIRD.

Run via an OpenSSH authorized_keys forced command, with fixed role and inbox.
Any SSH_ORIGINAL_COMMAND is ignored; the client cannot select other actions.
"""
import argparse
import json
from pathlib import Path
import sys

from ru_routes.agent.remote import MAX_MESSAGE_BYTES, receive


def handle(stream, role, inbox):
    if role not in ('primary', 'backup'):
        raise ValueError('invalid configured agent role')
    # Never interpret SSH_ORIGINAL_COMMAND or request-supplied paths.
    data = stream.read(MAX_MESSAGE_BYTES + 1)
    return receive(data, role, Path(inbox))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--role', required=True, choices=('primary', 'backup'))
    parser.add_argument('--inbox', required=True, type=Path)
    args = parser.parse_args()
    try:
        result = handle(sys.stdin.buffer, args.role, args.inbox)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({'status': 'rejected', 'error': str(exc)}), file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
