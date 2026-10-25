"""Unprivileged agent endpoint: accept a verified staged release on stdin.

Never runs birdc, modifies /etc/bird, or opens network sockets.
Provision SSH account with a forced command and a private writable inbox.
"""
import argparse
import base64
import binascii
import json
from pathlib import Path
import sys
import tempfile

from ru_routes.agent.stage import stage_release

MAX_MESSAGE_BYTES = 8_000_000


def decode_message(data):
    if len(data) > MAX_MESSAGE_BYTES:
        raise ValueError('transfer exceeds 8 MB')
    message = json.loads(data)
    if not isinstance(message, dict) or set(message) != {'schema', 'role', 'manifest', 'config'}:
        raise ValueError('unexpected transfer fields')
    if message['schema'] != 1 or message['role'] not in ('primary', 'backup'):
        raise ValueError('unsupported transfer')
    try:
        manifest = base64.b64decode(message['manifest'], validate=True)
        config = base64.b64decode(message['config'], validate=True)
    except (binascii.Error, ValueError, TypeError) as exc:
        raise ValueError('invalid transfer encoding') from exc
    if len(manifest) > 500_000 or len(config) > 4_000_000:
        raise ValueError('transfer file exceeds limit')
    return message['role'], manifest, config


def receive(data, expected_role, inbox):
    role, manifest, config = decode_message(data)
    if role != expected_role:
        raise ValueError('agent role mismatch')
    inbox = Path(inbox)
    if inbox.resolve() == Path('/etc/bird') or Path('/etc/bird') in inbox.resolve().parents:
        raise ValueError('live BIRD directory is forbidden')
    with tempfile.TemporaryDirectory(prefix='rr-agent-receive-') as temp:
        release_dir = Path(temp)
        (release_dir / 'release.json').write_bytes(manifest)
        (release_dir / f'{role}.bird.conf').write_bytes(config)
        release_id = stage_release(release_dir, role, inbox)
    return {'status': 'prepared_not_activated', 'role': role, 'release_id': release_id}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--role', choices=('primary', 'backup'), required=True)
    p.add_argument('--inbox', type=Path, required=True)
    a = p.parse_args()
    data = sys.stdin.buffer.read(MAX_MESSAGE_BYTES + 1)
    try:
        print(json.dumps(receive(data, a.role, a.inbox)))
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({'status': 'rejected', 'error': str(exc)}), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
