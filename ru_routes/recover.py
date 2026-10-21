"""Manual, fail-closed recovery of interrupted route-file transactions.

Does not invoke BIRD automatically. File restoration requires --confirm and
an administrator must separately verify the live daemon before clearing journal.
"""
import argparse
import fcntl
import json
from pathlib import Path

from ru_routes import transaction


def inspect(journal):
    entry = transaction.load(journal)
    if entry is None:
        return "No pending transaction"
    backup = Path(entry["backup"])
    target = Path(entry["target"])
    if entry.get("version") != 1:
        raise ValueError("unsupported transaction version")
    if not backup.is_file() or transaction.digest(backup.read_bytes()) != entry["old_sha256"]:
        raise ValueError("backup missing or checksum mismatch")
    if not target.is_file():
        raise ValueError("target file missing")
    actual = transaction.digest(target.read_bytes())
    if actual not in (entry["old_sha256"], entry["new_sha256"]):
        raise ValueError("target differs from both known transaction versions")
    return entry, actual


def restore_file(journal, confirm=False):
    checked = inspect(journal)
    if isinstance(checked, str):
        return checked
    entry, actual = checked
    if not confirm:
        return "Recovery available; use --confirm to restore backup file (BIRD is NOT reconfigured)"
    if entry["state"] == "verified":
        raise ValueError("verified transaction requires manual review, not automatic restoration")
    if actual == entry["new_sha256"]:
        transaction.durable_write(Path(entry["target"]), Path(entry["backup"]).read_bytes())
    data = dict(entry)
    data["state"] = "recovery_required"
    transaction.durable_write(journal, (json.dumps(data, indent=2, sort_keys=True) + "\n").encode())
    return "Backup file restored; journal retained. Verify BIRD manually before clearing."


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, default=Path("/var/lib/ru-restricted-routes/transaction.json"))
    parser.add_argument("--lock", type=Path, default=Path("/run/lock/ru-restricted-routes.lock"))
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    with args.lock.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print(restore_file(args.journal, args.confirm))


if __name__ == "__main__":
    main()
