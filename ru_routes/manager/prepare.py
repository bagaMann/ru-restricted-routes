"""Two-node release preparation coordinator; never activates BIRD.

The coordinator accepts two independent agent staging destinations. An SSH
transport can later supply the same prepare/inspect protocol remotely.
"""
import argparse
import json
from pathlib import Path

from ru_routes.agent.stage import stage_release


def prepare_pair(release_dir, primary_inbox, backup_inbox):
    """Preflight both agents and stage identical release IDs.

    Both destinations must be distinct. If the second stage fails, remove
    only the first *newly staged* directory. No BIRD operations occur.
    """
    release_dir = Path(release_dir)
    primary_inbox = Path(primary_inbox)
    backup_inbox = Path(backup_inbox)
    if primary_inbox.resolve() == backup_inbox.resolve():
        raise ValueError('primary and backup inbox must be distinct')

    # Both bundles must pass validation before creating either destination.
    from ru_routes.agent.stage import verify_release
    release_id = verify_release(release_dir, 'primary')
    if verify_release(release_dir, 'backup') != release_id:
        raise ValueError('roles refer to different releases')
    for inbox in (primary_inbox, backup_inbox):
        if (inbox / release_id).exists():
            raise FileExistsError(f'release already present: {inbox / release_id}')

    prepared = []
    try:
        for role, inbox in (('primary', primary_inbox), ('backup', backup_inbox)):
            actual = stage_release(release_dir, role, inbox)
            if actual != release_id:
                raise ValueError('agent staged unexpected release')
            prepared.append(inbox / release_id)
    except Exception:
        # Cleanup only the newly staged primary bundle. The operation is
        # deliberately limited to known files under the newly created path.
        for target in reversed(prepared):
            for filename in ('bird.conf', 'release.json'):
                (target / filename).unlink(missing_ok=True)
            target.rmdir()
        raise
    return {'release_id': release_id, 'primary': 'prepared', 'backup': 'prepared',
            'status': 'prepared_not_activated'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-dir', type=Path, required=True)
    parser.add_argument('--primary-inbox', type=Path, required=True)
    parser.add_argument('--backup-inbox', type=Path, required=True)
    a = parser.parse_args()
    print(json.dumps(prepare_pair(a.release_dir, a.primary_inbox, a.backup_inbox), indent=2))


if __name__ == '__main__':
    main()
