"""Crash-detection journal. Recovery is deliberately manual.

The journal is persisted before the live route file is replaced. It is never
silently cleared on startup; an administrator must inspect interrupted work.
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def durable_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".transaction-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def load(path):
    path = Path(path)
    if not path.exists():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("state") not in ("prepared", "applying", "verified", "recovery_required"):
        raise ValueError("invalid transaction state")
    return record


def require_clean(path):
    record = load(path)
    if record is not None:
        raise RuntimeError(
            "transaction journal exists (state=%s); manual inspection required: %s"
            % (record["state"], path)
        )


def record(path, state, target, old, new, backup):
    durable_write(path, json.dumps({
        "version": 1, "state": state, "target": str(target),
        "backup": str(backup), "old_sha256": digest(old),
        "new_sha256": digest(new),
    }, sort_keys=True, indent=2).encode("utf-8") + b"\n")


def clear(path):
    path = Path(path)
    path.unlink()
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
