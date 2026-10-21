"""Safely stage and apply a downloaded candidate to an existing BIRD 2 setup.

Run as root after reviewing the candidate and its manifest. This program
does not download feeds or modify the main BIRD configuration.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def run(*args):
    subprocess.run(args, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--target", type=Path, default=Path("/etc/bird/ru-restricted/routes.conf"))
    parser.add_argument("--bird-config", type=Path, default=Path("/etc/bird/bird.conf"))
    parser.add_argument("--backup", type=Path, default=Path("/var/lib/ru-restricted-routes/previous-routes.conf"))
    args = parser.parse_args()
    if not args.candidate.is_file():
        parser.error("candidate not found")
    if not args.target.is_file():
        parser.error("target must already exist; configure BIRD manually first")
    data = args.candidate.read_bytes()
    if not data or b"protocol static ru_restricted_static" not in data:
        parser.error("candidate is not a generated static protocol")
    old = args.target.read_bytes()
    if data == old:
        print("No changes")
        return
    # Candidate and target are both kept in the same file system for atomic replacement.
    args.target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmpname = tempfile.mkstemp(prefix=".ru-restricted-", dir=args.target.parent)
    tmp = Path(tmpname)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        # BIRD -p parses the full config including the candidate at its target path.
        os.replace(tmp, args.target)
        try:
            run("bird", "-p", "-c", str(args.bird_config))
            run("birdc", "configure")
        except (OSError, subprocess.CalledProcessError):
            args.target.write_bytes(old)
            os.chmod(args.target, 0o644)
            # Restore running config if reconfiguration had partially succeeded.
            try:
                run("birdc", "configure")
            except (OSError, subprocess.CalledProcessError):
                pass
            raise
        args.backup.parent.mkdir(parents=True, exist_ok=True)
        args.backup.write_bytes(old)
        print("Candidate applied; previous routes saved to", args.backup)
    finally:
        tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
