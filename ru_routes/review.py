"""Read-only approval gate for an already fetched BIRD candidate.

No BIRD configuration, state files, or routes are modified.
"""
import argparse
import fcntl
import json
from pathlib import Path

from ru_routes.apply import candidate_prefix_text, read_bird_state
from ru_routes.generate import parse_prefixes


def review(current_text, candidate_text, live_count, exported_count,
           max_changed_fraction=0.20, max_removed_fraction=0.10):
    current = set(parse_prefixes(current_text))
    candidate = set(parse_prefixes(candidate_text))
    added = candidate - current
    removed = current - candidate
    result = {
        "current_count": len(current),
        "candidate_count": len(candidate),
        "added": len(added),
        "removed": len(removed),
        "live_count": live_count,
        "exported_count": exported_count,
        "approved": False,
        "reasons": [],
    }
    if live_count != len(current) or exported_count != len(current):
        result["reasons"].append("live BIRD state differs from approved baseline")
    if len(removed) / len(current) > max_removed_fraction:
        result["reasons"].append("too many removed prefixes")
    if (len(added) + len(removed)) / len(current) > max_changed_fraction:
        result["reasons"].append("too many changed prefixes")
    result["approved"] = not result["reasons"]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=Path("/var/lib/ru-restricted-routes"))
    parser.add_argument("--max-changed-fraction", type=float, default=0.20)
    parser.add_argument("--max-removed-fraction", type=float, default=0.10)
    args = parser.parse_args()
    if not 0 <= args.max_changed_fraction <= 1 or not 0 <= args.max_removed_fraction <= 1:
        parser.error("fraction limits must be between 0 and 1")
    lock_path = Path("/run/lock/ru-restricted-routes.lock")
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        state = args.state_dir
        current_text = (state / "current.txt").read_text(encoding="utf-8")
        candidate_text = (state / "candidate.txt").read_text(encoding="utf-8")
        rendered_text = candidate_prefix_text(state / "candidate.bird.conf")
        if candidate_text != rendered_text:
            raise ValueError("candidate.txt and candidate.bird.conf differ")
        live, exported = read_bird_state()
        result = review(current_text, candidate_text, live, exported,
                        args.max_changed_fraction, args.max_removed_fraction)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result["approved"]:
            raise SystemExit(2)
    

if __name__ == "__main__":
    main()
