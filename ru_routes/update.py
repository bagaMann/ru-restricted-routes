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
DEFAULT_SOURCES = Path(__file__).resolve().parent.parent / "sources.txt"


def read_sources(path):
    """Read one HTTPS URL per line; reject duplicates and empty lists."""
    urls = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        if not value.startswith("https://") or any(c.isspace() for c in value):
            raise ValueError(f"invalid HTTPS source: {value!r}")
        if value in urls:
            raise ValueError(f"duplicate source: {value}")
        urls.append(value)
    if not urls:
        raise ValueError("sources file contains no URLs")
    return urls


def fetch_sources(urls, max_bytes=2_000_000):
    """Download every feed, rejecting partial updates and oversized inputs."""
    feeds = []
    total = 0
    for url in urls:
        req = Request(url, headers={"User-Agent": "ru-restricted-routes/0.3"})
        with urlopen(req, timeout=25) as response:
            payload = response.read(max_bytes - total + 1)
        total += len(payload)
        if total > max_bytes:
            raise ValueError("combined feeds exceed 2 MB")
        networks = parse_prefixes(payload.decode("utf-8-sig"))
        if not networks:
            raise ValueError(f"empty source: {url}")
        feeds.append((url, payload, networks))
    return feeds


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
    p.add_argument("--url", help="override sources.txt with one HTTPS URL")
    p.add_argument("--sources-file", type=Path, default=DEFAULT_SOURCES)
    p.add_argument("--state-dir", type=Path, default=Path("/var/lib/ru-restricted-routes"))
    p.add_argument("--max-prefixes", type=int, default=2500)
    p.add_argument("--min-prefixes", type=int, default=10)
    p.add_argument("--approve-large-change", action="store_true")
    args = p.parse_args()
    urls = [args.url] if args.url else read_sources(args.sources_file)
    if any(not url.startswith("https://") or any(c.isspace() for c in url) for url in urls):
        p.error("only valid HTTPS feeds are allowed")
    if args.max_prefixes < 1 or args.min_prefixes < 1 or args.min_prefixes > args.max_prefixes:
        p.error("invalid prefix limits")
    feeds = fetch_sources(urls)
    raw = "\n".join(payload.decode("utf-8-sig") for _, payload, _ in feeds)
    prefixes = parse_prefixes(raw)
    if not args.min_prefixes <= len(prefixes) <= args.max_prefixes:
        raise ValueError(f"prefix count outside limits: {len(prefixes)}")
    lock_path = Path("/run/lock/ru-restricted-routes.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        publish_candidate(args, prefixes, feeds)


def publish_candidate(args, prefixes, feeds):
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
        "sources": [{"url": url, "sha256": hashlib.sha256(payload).hexdigest(), "prefix_count": len(networks)} for url, payload, networks in feeds],
        "prefix_count": len(prefixes),
        "previous_count": len(old),
        "status": "candidate_not_applied",
        "candidate_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "bird_sha256": hashlib.sha256(render_bird(prefixes).encode("utf-8")).hexdigest(),
    }
    atomic_write(args.state_dir / "candidate.json", json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))



if __name__ == "__main__":
    main()
