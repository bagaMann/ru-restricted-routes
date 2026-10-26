"""Persist a verified isolated BIRD preflight receipt, never activate routes.

Receipts are write-once local evidence, NOT proof of persistent active BGP.
"""
import argparse
import json
import os
from pathlib import Path
import tempfile
from datetime import datetime, timezone

from ru_routes.agent.live_test import run_isolated
from ru_routes.manager.inspect import inspect


def _safe_state_dir(path):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('state directory must be absolute')
    if path.is_symlink() or path.resolve() == Path('/etc/bird') or Path('/etc/bird') in path.resolve().parents:
        raise ValueError('unsafe receipt directory')
    return path


def preflight(inbox, release_id, role, state_dir):
    state = _safe_state_dir(state_dir)
    # Validate before running BIRD and fail closed if receipt was already created.
    verified = inspect(inbox, release_id, role)
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = state / f'{release_id}.{role}.preflight.json'
    if target.exists() or target.is_symlink():
        raise FileExistsError('preflight receipt already exists')

    live = run_isolated(inbox, release_id, role)
    if (live.get('status') != 'isolated_live_routes_verified' or
            live.get('release_id') != release_id or live.get('role') != role):
        raise ValueError('isolated live BIRD preflight failed')
    if set(live.get('routers', {})) != set(verified['routers']):
        raise ValueError('preflight router selection mismatch')
    for name, actual in live['routers'].items():
        expected = verified['routers'][name]
        if (actual['prefix_count'] != expected['prefix_count'] or
                actual['prefix_sha256'] != expected['prefix_sha256']):
            raise ValueError(f'{name}: isolated table differs from release')
    # Re-read the staged config to guard against mutation during preflight.
    after = inspect(inbox, release_id, role)
    if after != verified:
        raise ValueError('prepared release changed during preflight')

    receipt = {
        'schema': 1,
        'status': 'preflight_verified_not_activated',
        'release_id': release_id,
        'role': role,
        'config_sha256': verified['config_sha256'],
        'routers': live['routers'],
        'verified_at_utc': datetime.now(timezone.utc).isoformat(),
    }
    payload = (json.dumps(receipt, indent=2, sort_keys=True) + '\n').encode()
    # Atomic, no-overwrite publish with fsync; incomplete temp is never a receipt.
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.preflight-', dir=state, delete=False) as handle:
            temp_name = handle.name
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        os.link(temp_name, target)  # atomic no-clobber, refuses existing target
        fd = os.open(state, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if temp_name is not None:
            Path(temp_name).unlink(missing_ok=True)
    return receipt


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inbox', type=Path, required=True)
    p.add_argument('--release-id', required=True)
    p.add_argument('--role', choices=('primary', 'backup'), required=True)
    p.add_argument('--state-dir', type=Path, required=True)
    args = p.parse_args()
    print(json.dumps(preflight(args.inbox, args.release_id, args.role, args.state_dir), indent=2))


if __name__ == '__main__':
    main()
