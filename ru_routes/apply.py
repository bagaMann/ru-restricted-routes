"""Explicitly apply a reviewed BIRD 2 candidate, with rollback on errors.

No network fetching is performed. The main BIRD config is not modified.
"""
import argparse
import fcntl
import os
from pathlib import Path
import shutil
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


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--candidate", type=Path, required=True)
    p.add_argument("--target", type=Path, default=Path("/etc/bird/ru-restricted/routes.conf"))
    p.add_argument("--bird-config", type=Path, default=Path("/etc/bird/bird.conf"))
    p.add_argument("--backup", type=Path, default=Path("/var/lib/ru-restricted-routes/previous-routes.conf"))
    p.add_argument("--lock", type=Path, default=Path("/run/lock/ru-restricted-routes.lock"))
    p.add_argument("--confirm", action="store_true", help="required to modify running BIRD")
    args = p.parse_args()
    count = validate_candidate(args.candidate)
    if not args.target.is_file() or not args.bird_config.is_file():
        p.error("existing target and BIRD config are required")
    if not args.confirm:
        print(f"DRY RUN: valid candidate with {count} prefixes; use --confirm to apply")
        return
    args.lock.parent.mkdir(parents=True, exist_ok=True)
    with args.lock.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        old = args.target.read_bytes()
        new = args.candidate.read_bytes()
        if old == new:
            print("No changes")
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
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            atomic_bytes(args.target, old)
            try:
                run("birdc", "configure")
            except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
                pass
            raise
        print(f"Applied {count} prefixes; rollback copy: {args.backup}")


if __name__ == "__main__":
    main()
