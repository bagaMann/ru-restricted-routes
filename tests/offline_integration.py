"""Offline integration test for candidate preparation, change and rollback.

Uses a temporary directory and never calls birdc or modifies /etc/bird.
"""
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

from ru_routes.apply import atomic_bytes, validate_candidate
from ru_routes.generate import parse_prefixes, render_bird
from ru_routes.update import check_update


def main():
    initial = parse_prefixes("8.8.8.0/24\n1.1.1.0/24\n")
    changed = parse_prefixes("8.8.8.0/24\n1.1.1.0/24\n9.9.9.0/24\n")
    with TemporaryDirectory(prefix="ru-routes-integration-") as folder:
        root = Path(folder)
        live = root / "routes.conf"
        backup = root / "previous.conf"
        candidate = root / "candidate.conf"

        atomic_bytes(live, render_bird(initial).encode())
        initial_hash = hashlib.sha256(live.read_bytes()).hexdigest()
        check_update(initial, changed)
        atomic_bytes(candidate, render_bird(changed).encode())
        assert validate_candidate(candidate) == 3

        # Simulated apply (no running BIRD interaction).
        atomic_bytes(backup, live.read_bytes())
        atomic_bytes(live, candidate.read_bytes())
        assert validate_candidate(live) == 3

        # Simulated rollback to the previous known-good configuration.
        atomic_bytes(live, backup.read_bytes())
        assert validate_candidate(live) == 2
        assert hashlib.sha256(live.read_bytes()).hexdigest() == initial_hash
        print("OK: offline apply, rollback and SHA256 verification passed")
        print("No BIRD daemon, BGP session or Linux route was modified")


if __name__ == "__main__":
    main()
