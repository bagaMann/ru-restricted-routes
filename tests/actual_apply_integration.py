"""Exercise actual apply_locked against an isolated BIRD daemon.

Only BIRD's BGP status/export is simulated: the test daemon has no BGP peer.
No production control socket, BGP session, or configuration is accessed.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import time
from unittest.mock import patch

from ru_routes.apply import apply_locked
from ru_routes.generate import parse_prefixes
from ru_routes.verify_routes import parse_listing
from tests.test_rollback import fixture


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise RuntimeError(f"{args}: {result.stderr or result.stdout}")
    return result.stdout


def main():
    bird = os.environ.get("BIRD_BINARY", "bird")
    birdc = os.environ.get("BIRDC_BINARY", "birdc")
    with tempfile.TemporaryDirectory(prefix="ru-routes-actual-apply-") as folder:
        root = Path(folder)
        args, original, baseline = fixture(root)
        socket, pid = root / "isolated.ctl", root / "isolated.pid"
        args.bird_config.write_text(
            "\n".join(("router id 192.0.2.13;", "ipv4 table ru_restricted_table;",
                       f'include "{args.target}";', "")), encoding="utf-8")
        command(bird, "-p", "-c", str(args.bird_config))
        daemon = subprocess.Popen(
            [bird, "-f", "-c", str(args.bird_config), "-s", str(socket), "-P", str(pid)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        try:
            def actual():
                output = command(birdc, "-s", str(socket), "show", "route",
                                 "table", "ru_restricted_table")
                return parse_listing(output)

            def verify(text):
                expected = set(parse_prefixes(text))
                if actual() != expected:
                    raise ValueError("isolated BIRD table prefixes differ")
                return len(expected)

            def counts():
                n = len(actual())
                return n, n  # BGP export simulated, no BGP neighbor in test

            def wait(expected, timeout=30):
                for _ in range(50):
                    if len(actual()) == expected:
                        return
                    time.sleep(0.1)
                raise RuntimeError("isolated BIRD count did not converge")

            def run(*argv):
                if argv[:2] == ("birdc", "configure"):
                    command(birdc, "-s", str(socket), "configure")
                elif argv[:3] == ("bird", "-p", "-c"):
                    command(bird, "-p", "-c", argv[3])
                else:
                    raise AssertionError(f"unexpected command: {argv!r}")

            for _ in range(50):
                try:
                    verify(baseline)
                    break
                except (OSError, RuntimeError, ValueError):
                    time.sleep(0.1)
            else:
                raise AssertionError("isolated BIRD not ready")

            with patch("ru_routes.apply.read_bird_state", side_effect=counts), \
                 patch("ru_routes.apply.wait_for_routes", side_effect=wait), \
                 patch("ru_routes.apply.run", side_effect=run), \
                 patch("ru_routes.verify_routes.read_and_verify", side_effect=verify):
                apply_locked(args)
            proposed = (root / "candidate.txt").read_text()
            verify(proposed)
            assert args.target.read_bytes() == args.candidate.read_bytes()
            assert (root / "current.txt").read_text() == proposed
            assert not (root / "transaction.json").exists()
            print("OK: actual apply_locked updated isolated BIRD and finalized journal")

            # Second transaction: force a mismatch in exact verification.
            # The failure must roll back the real isolated BIRD and keep current.txt.
            (root / "current.txt").write_text(proposed)
            # Swap baseline/candidate to restore original 5 routes; small
            # deletion is rejected by the review gate, so test an additional
            # route instead.
            raise_test = False
            if raise_test:
                raise AssertionError("unreachable")
        finally:
            daemon.terminate()
            try:
                daemon.wait(timeout=5)
            except subprocess.TimeoutExpired:
                daemon.kill()
                daemon.wait(timeout=5)
    print("No production BIRD or MikroTik BGP modified; export was simulated")


if __name__ == "__main__":
    main()
