"""Explicit journal finalization after verifying restored BIRD state.

Never configures BIRD. Requires --confirm to remove a transaction journal.
"""
import argparse
import fcntl
from pathlib import Path

from ru_routes import transaction
from ru_routes.apply import candidate_prefix_text, read_bird_state
from ru_routes.recover import inspect
from ru_routes.verify_routes import read_and_verify


def finalize(journal, *, confirm=False, verifier=read_and_verify, state_reader=read_bird_state):
    checked = inspect(journal)
    if isinstance(checked, str):
        return checked
    entry, actual = checked
    if entry["state"] != "recovery_required":
        raise ValueError("journal must be recovery_required before finalization")
    if actual != entry["old_sha256"]:
        raise ValueError("restored target checksum differs from original")
    target = Path(entry["target"])
    expected = candidate_prefix_text(target)
    count = len(expected.splitlines())
    live, exported = state_reader()
    if (live, exported) != (count, count):
        raise ValueError("BIRD table/export counts do not match restored file")
    verified = verifier(expected)
    if verified != count:
        raise ValueError("BIRD verifier returned unexpected route count")
    if not confirm:
        return f"Verified {count} routes; use --confirm to close recovery journal"
    transaction.clear(journal)
    return f"Verified {count} routes and closed recovery journal"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, default=Path("/var/lib/ru-restricted-routes/transaction.json"))
    parser.add_argument("--lock", type=Path, default=Path("/run/lock/ru-restricted-routes.lock"))
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    args.lock.parent.mkdir(parents=True, exist_ok=True)
    with args.lock.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print(finalize(args.journal, confirm=args.confirm))


if __name__ == "__main__":
    main()
