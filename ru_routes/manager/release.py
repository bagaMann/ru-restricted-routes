"""Produce immutable, verifiable deployment bundles from staged configurations."""
import hashlib
import json
from pathlib import Path


def sha(data):
    return hashlib.sha256(data).hexdigest()


def build_release(stage_dir, release_dir):
    """Create both server bundles. No network or BIRD access."""
    stage_dir, release_dir = Path(stage_dir), Path(release_dir)
    manifest_bytes = (stage_dir / 'manifest.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get('status') != 'staged_not_applied':
        raise ValueError('only reviewed staging artifacts may become releases')
    if set(manifest.get('servers', {})) != {'primary', 'backup'}:
        raise ValueError('both server roles are required')
    configs = {}
    for role in ('primary', 'backup'):
        data = (stage_dir / f'{role}.bird.conf').read_bytes()
        if sha(data) != manifest['servers'][role]['config_sha256']:
            raise ValueError(f'{role}: staging configuration checksum mismatch')
        configs[role] = data
    if release_dir.exists():
        raise FileExistsError('release destination already exists')
    release = {
        'schema': 1,
        'stage_sha256': sha(manifest_bytes),
        'servers': {role: {'config_sha256': sha(data),
                           'routers': manifest['servers'][role]['routers']}
                    for role, data in configs.items()},
    }
    # The same logical router must carry the same prefix identity on both roles.
    if release['servers']['primary']['routers'] != release['servers']['backup']['routers']:
        raise ValueError('server route selections differ')
    release['release_id'] = sha(json.dumps(release, sort_keys=True).encode())
    release_dir.mkdir(parents=True)
    try:
        for role, data in configs.items():
            (release_dir / f'{role}.bird.conf').write_bytes(data)
        (release_dir / 'release.json').write_text(
            json.dumps(release, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    except Exception:
        for path in release_dir.iterdir():
            path.unlink()
        release_dir.rmdir()
        raise
    return release
