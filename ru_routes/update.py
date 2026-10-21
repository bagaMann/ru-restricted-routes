"""Fetch a reviewed IPv4 CIDR feed and generate a BIRD 2 fragment.

This tool does not reload BIRD, modify BGP peers, or install routing rules.
"""
import argparse
import fcntl
import hashlib
import ipaddress
import json
from pathlib import Path
import tempfile
import os
from urllib.request import Request, urlopen

from ru_routes.generate import parse_prefixes, render_bird

SOURCE = "https://raw.githubusercontent.com/kyoresuas/ru-direct/release/ru-standard.ipv4.txt"


def check_update(old, new, max_removed_fraction=0.35):
    """Reject unexpectedly large removals when an earlier set exists."""
    if not new:
        raise ValueError("empty update")
    if old:
        removed = set(old) - set(new)
        if len(removed) / len(old) > max_removed_fraction:
            raise ValueError("too many prefixes disappeared; manual review required")


def atomic_write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".ru-routes-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", default=SOURCE)
    p.add_argument("--state-dir", type=Path, default=Path("/var/lib/ru-restricted-routes"))
    p.add_argument("--max-prefixes", type=int, default=2500)
    p.add_argument("--min-prefixes", type=int, default=10)
    p.add_argument("--approve-large-change", action="store_true")
    args = p.parse_args()
    if not args.url.startswith("https://"):
        p.error("only HTTPS feeds are allowed")
    if args.max_prefixes < 1 or args.min_prefixes < 1 or args.min_prefixes > args.max_prefixes:
        p.error("invalid prefix limits")
    req = Request(args.url, headers={"User-Agent": "ru-restricted-routes/0.2"})
    with urlopen(req, timeout=25) as response:
        payload = response.read(2_000_001)
    if len(payload) > 2_000_000:
        raise ValueError("feed exceeds 2 MB")
    raw = payload.decode("utf-8-sig")
    prefixes = parse_prefixes(raw)
    if not args.min_prefixes <= len(prefixes) <= args.max_prefixes:
        raise ValueError(f"prefix count outside limits: {len(prefixes)}")
    lock_path = Path("/run/lock/ru-restricted-routes.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        publish_candidate(args, prefixes, payload)


def publish_candidate(args, prefixes, payload):
    """Publish all candidate files under the shared advisory lock."""
    current = args.state_dir / "current.txt"
    old = parse_prefixes(current.read_text(encoding="utf-8")) if current.exists() else []
    if not args.approve_large_change:
        check_update(old, prefixes)
    # Both files are staged for inspection. No automatic BIRD include/reload.
    canonical = "".join(f"{p}\n" for p in prefixes)
    atomic_write(args.state_dir / "candidate.txt", canonical)
    atomic_write(args.state_dir / "candidate.bird.conf", render_bird(prefixes))
    manifest = {
        "source": args.url,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "prefix_count": len(prefixes),
        "previous_count": len(old),
        "status": "candidate_not_applied",
    }
    atomic_write(args.state_dir / "candidate.json", json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
