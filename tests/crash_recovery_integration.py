"""Crash recovery integration with isolated BIRD, no production BGP or routes.

Runs SIGKILL against a disposable transaction worker. The isolated daemon has
no BGP peer, so export verification is simulated; production BGP is not tested.
"""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

from ru_routes import transaction
from ru_routes.generate import parse_prefixes, render_bird
from ru_routes.recover import restore_file
from ru_routes.finalize import finalize
from ru_routes.verify_routes import parse_listing


def cmd(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=15).stdout


def main():
    bird = os.environ.get("BIRD_BINARY", "bird")
    birdc = os.environ.get("BIRDC_BINARY", "birdc")
    with tempfile.TemporaryDirectory(prefix="ru-routes-crash-") as tmp:
        root = Path(tmp)
        socket, pid = root / "bird.ctl", root / "bird.pid"
        target, backup, journal = root / "routes.conf", root / "backup.conf", root / "transaction.json"
        config = root / "bird.conf"
        old_text = "1.1.1.0/24\n8.8.8.0/24\n"
        new_text = old_text + "9.9.9.0/24\n"
        old = render_bird(parse_prefixes(old_text)).encode()
        new = render_bird(parse_prefixes(new_text)).encode()
        transaction.durable_write(target, old)
        config.write_text("router id 192.0.2.13;\nipv4 table ru_restricted_table;\n" +
                          'protocol static ru_restricted_static { ipv4 { table ru_restricted_table; }; include "' +
                          str(target) + '"; }\n')
        cmd(bird, "-p", "-c", str(config))
        daemon = subprocess.Popen([bird, "-f", "-c", str(config), "-s", str(socket),
                                   "-P", str(pid)], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.PIPE, text=True)
        try:
            def actual():
                listing = cmd(birdc, "-s", str(socket), "show", "route", "table", "ru_restricted_table")
                return parse_listing(listing)

            def wait(expected):
                for _ in range(50):
                    try:
                        if actual() == set(parse_prefixes(expected)):
                            return
                    except (OSError, subprocess.SubprocessError, ValueError):
                        pass
                    time.sleep(0.1)
                raise AssertionError("isolated BIRD did not converge")

            def reconfigure():
                cmd(birdc, "-s", str(socket), "configure")

            wait(old_text)
            phases = ("prepared", "applying", "file_replaced", "bird_configured")
            for phase in phases:
                transaction.durable_write(target, old)
                reconfigure()
                wait(old_text)
                if journal.exists():
                    transaction.clear(journal)
                # Disposable worker uses the same durable journal/file primitives as apply.
                worker = """
import os, signal, sys
from pathlib import Path
from ru_routes import transaction
root, phase = Path(sys.argv[1]), sys.argv[2]
target, backup, journal = root/'routes.conf', root/'backup.conf', root/'transaction.json'
old, new = (root/'old.bin').read_bytes(), (root/'new.bin').read_bytes()
transaction.durable_write(backup, old)
transaction.record(journal, 'prepared', target, old, new, backup)
if phase == 'prepared': os.kill(os.getpid(), signal.SIGKILL)
transaction.record(journal, 'applying', target, old, new, backup)
if phase == 'applying': os.kill(os.getpid(), signal.SIGKILL)
transaction.durable_write(target, new)
if phase == 'file_replaced': os.kill(os.getpid(), signal.SIGKILL)
if phase == 'bird_configured':
    import subprocess
    subprocess.run([sys.argv[3], '-s', sys.argv[4], 'configure'], check=True, capture_output=True)
    os.kill(os.getpid(), signal.SIGKILL)
"""
                (root / "old.bin").write_bytes(old)
                (root / "new.bin").write_bytes(new)
                result = subprocess.run([sys.executable, "-c", worker, str(root), phase,
                                         birdc, str(socket)], cwd=Path(__file__).resolve().parent.parent,
                                        capture_output=True, text=True, timeout=15)
                if result.returncode != -signal.SIGKILL:
                    raise AssertionError(f"{phase}: worker did not die via SIGKILL: {result.returncode} {result.stderr}")
                if not journal.exists():
                    raise AssertionError(f"{phase}: missing crash journal")
                if phase == "bird_configured":
                    wait(new_text)
                # No journal finalization before manual file restoration.
                try:
                    finalize(journal, confirm=True, verifier=lambda _: 2, state_reader=lambda: (2, 2))
                    raise AssertionError("premature finalization accepted")
                except ValueError:
                    pass
                restore_file(journal, confirm=True)
                if target.read_bytes() != old:
                    raise AssertionError("file recovery failed")
                # Simulate export only, but inspect the REAL isolated BIRD table.
                def verify(text):
                    expected = set(parse_prefixes(text))
                    if actual() != expected:
                        raise ValueError("isolated BIRD route mismatch")
                    return len(expected)
                def state():
                    n = len(actual())
                    return n, n  # no BGP peer in this isolated test
                if phase == "bird_configured":
                    try:
                        finalize(journal, confirm=True, verifier=verify, state_reader=state)
                        raise AssertionError("finalization accepted stale live BIRD state")
                    except ValueError:
                        pass
                    if not journal.exists():
                        raise AssertionError("journal lost after failed verification")
                    reconfigure()
                    wait(old_text)
                finalize(journal, confirm=True, verifier=verify, state_reader=state)
                if journal.exists():
                    raise AssertionError("journal not cleared after verified recovery")
                print(f"OK: SIGKILL at {phase}; recovered and finalized")
        finally:
            daemon.terminate()
            try:
                daemon.wait(timeout=5)
            except subprocess.TimeoutExpired:
                daemon.kill()
                daemon.wait(timeout=5)
    print("Isolated BIRD only; BGP export simulated; production untouched")


if __name__ == "__main__":
    main()
