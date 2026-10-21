"""Explicitly apply a reviewed BIRD 2 candidate, with rollback on errors.

No network fetching is performed. The main BIRD config is not modified.
"""
import argparse
import fcntl
import os
from pathlib import Path
import re
import time
import subprocess
import tempfile

from ru_routes.generate import parse_prefixes, render_bird


def run(*argv):
    subprocess.run(argv, check=True, timeout=30)


def atomic_bytes(target, data):
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".ru-routes-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o644)
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def validate_candidate(candidate):
    # Accept only exact generator output, never arbitrary executable BIRD text.
    raw = candidate.read_text(encoding="utf-8")
    prefix_lines = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("route ") and stripped.endswith(" blackhole;"):
            prefix_lines.append(stripped[len("route "):-len(" blackhole;")])
    networks = parse_prefixes("\n".join(prefix_lines))
    if render_bird(networks) != raw:
        raise ValueError("candidate differs from canonical generator output")
    return len(networks)


def candidate_prefix_text(candidate):
    """Return canonical newline-separated CIDRs from a validated fragment."""
    validate_candidate(candidate)
    lines = [
        line.strip()[len("route "):-len(" blackhole;")]
        for line in candidate.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("route ") and line.strip().endswith(" blackhole;")
    ]
    networks = parse_prefixes("\n".join(lines))
    return "".join(f"{network}\n" for network in networks)


def parse_bird_state(table, protocol):
    """Parse BIRD 2 route and protocol reports with explicit diagnostics."""
    route_match = re.search(r"(\d+) of (\d+) routes for (\d+) networks", table)
    exported_match = re.search(r"Routes:\s+\d+ imported,\s+(\d+) exported", protocol)
    established = re.search(r"BGP state:\s+Established\b", protocol)
    missing = []
    if not route_match:
        missing.append("table route count")
    if not exported_match:
        missing.append("BGP exported route count")
    if not established:
        missing.append("BGP Established state")
    if missing:
        raise RuntimeError("Cannot parse BIRD status: " + ", ".join(missing))
    return int(route_match.group(1)), int(exported_match.group(1))


def read_bird_state():
    """Read actual table count and exported BGP routes; fail closed on parse errors."""
    table = subprocess.run(
        ["birdc", "show", "route", "table", "ru_restricted_table", "count"],
        check=True, capture_output=True, text=True, timeout=15,
    ).stdout
    protocol = subprocess.run(
        ["birdc", "show", "protocols", "all", "ru_to_mikrotik"],
        check=True, capture_output=True, text=True, timeout=15,
    ).stdout
    return parse_bird_state(table, protocol)


def wait_for_routes(expected, timeout=30):
    """Wait for both BIRD table and BGP export to converge."""
    deadline = time.monotonic() + timeout
    last = None
    while True:
        try:
            last = read_bird_state()
            if last == (expected, expected):
                return
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, RuntimeError) as exc:
            last = str(exc)
        if time.monotonic() >= deadline:
            raise RuntimeError(f"BIRD failed to converge to {expected} routes: {last}")
        time.sleep(1)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--candidate", type=Path, required=True)
    p.add_argument("--target", type=Path, default=Path("/etc/bird/ru-restricted/routes.conf"))
    p.add_argument("--bird-config", type=Path, default=Path("/etc/bird/bird.conf"))
    p.add_argument("--backup", type=Path, default=Path("/var/lib/ru-restricted-routes/previous-routes.conf"))
    p.add_argument("--lock", type=Path, default=Path("/run/lock/ru-restricted-routes.lock"))
    p.add_argument("--confirm", action="store_true", help="required to modify running BIRD")
    p.add_argument("--promote-current", action="store_true", help="update current.txt only after verified apply")
    args = p.parse_args()
    args.lock.parent.mkdir(parents=True, exist_ok=True)
    with args.lock.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        apply_locked(args)


def apply_locked(args):
    count = validate_candidate(args.candidate)
    if not args.target.is_file() or not args.bird_config.is_file():
        p.error("existing target and BIRD config are required")
    if args.promote_current:
        expected_text = candidate_prefix_text(args.candidate)
        approved_candidate = args.candidate.parent / "candidate.txt"
        if not approved_candidate.is_file() or approved_candidate.read_text(encoding="utf-8") != expected_text:
            raise ValueError("candidate.txt does not match candidate BIRD fragment")

    if not args.confirm:
        print(f"DRY RUN: valid candidate with {count} prefixes; use --confirm to apply")
        return

    old = args.target.read_bytes()
    new = args.candidate.read_bytes()
    if old == new:
        wait_for_routes(count)
        if args.promote_current:
            atomic_bytes(args.candidate.parent / "current.txt", expected_text.encode())
        print("No changes; BIRD state verified")
        return
    # Check the proposed config BEFORE changing the live include.
    # A copy of the main config is only safe if its include points at the
    # target; substitute that exact quoted path in a temporary copy.
    main_text = args.bird_config.read_text(encoding="utf-8")
    quoted = '"' + str(args.target) + '"'
    if main_text.count(quoted) != 1:
        raise ValueError("main config must reference target exactly once")
    with tempfile.TemporaryDirectory(dir=args.target.parent) as staging:
        staged = Path(staging) / "routes.conf"
        staged.write_bytes(new)
        os.chmod(staged, 0o644)
        # Place a temporary main config beside the real one so any relative
        # includes retain the same base directory.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=args.bird_config.parent,
                                         prefix=".ru-bird-check-", suffix=".conf") as cfg:
            cfg.write(main_text.replace(quoted, '"' + str(staged) + '"'))
            cfg.flush()
            os.chmod(cfg.name, 0o644)
            run("bird", "-p", "-c", cfg.name)
    args.backup.parent.mkdir(parents=True, exist_ok=True)
    atomic_bytes(args.backup, old)
    atomic_bytes(args.target, new)
    try:
        run("bird", "-p", "-c", str(args.bird_config))
        run("birdc", "configure")
        wait_for_routes(count)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, RuntimeError):
        atomic_bytes(args.target, old)
        try:
            run("birdc", "configure")
            previous_count = validate_candidate(args.backup)
            wait_for_routes(previous_count)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, RuntimeError, ValueError) as rollback_error:
            raise RuntimeError(f"Apply failed and rollback could not be verified: {rollback_error}")
        raise
    if args.promote_current:
        atomic_bytes(args.candidate.parent / "current.txt", expected_text.encode())
    print(f"Applied {count} prefixes; rollback copy: {args.backup}")


if __name__ == "__main__":
    main()
