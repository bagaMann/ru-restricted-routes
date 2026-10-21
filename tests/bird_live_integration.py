"""Live reconfiguration test using a completely separate BIRD daemon.

The test instance has no BGP, kernel, device or direct protocols. It uses
temporary config, control socket and PID file. The production BIRD is untouched.
"""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

from ru_routes.generate import parse_prefixes, render_bird


def execute(argv, timeout=10):
    return subprocess.run(argv, check=True, capture_output=True, text=True, timeout=timeout).stdout


def count_routes(birdc, socket):
    result = execute([birdc, "-s", str(socket), "show", "route", "table", "ru_restricted_table", "count"])
    match = re.search(r"(\d+) of (\d+) routes for (\d+) networks", result)
    if not match:
        raise RuntimeError("unexpected BIRD route count output: " + result)
    return int(match.group(1))


def main():
    bird = os.environ.get("BIRD_BINARY", "bird")
    birdc = os.environ.get("BIRDC_BINARY", "birdc")
    with tempfile.TemporaryDirectory(prefix="ru-routes-bird-live-") as folder:
        root = Path(folder)
        socket = root / "bird.ctl"
        pid = root / "bird.pid"
        config = root / "bird.conf"
        routes = root / "routes.conf"
        original = parse_prefixes("8.8.8.0/24\n1.1.1.0/24\n")
        updated = parse_prefixes("8.8.8.0/24\n1.1.1.0/24\n9.9.9.0/24\n")
        routes.write_text(render_bird(original), encoding="utf-8")
        config.write_text(
            "router id 192.0.2.13;\n"
            "ipv4 table ru_restricted_table;\n"
            f'include "{routes}";\n',
            encoding="utf-8",
        )
        execute([bird, "-p", "-c", str(config)])
        # -f runs in foreground, -s and -P isolate the control socket and PID.
        process = subprocess.Popen(
            [bird, "-f", "-c", str(config), "-s", str(socket), "-P", str(pid)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
        try:
            for _ in range(50):
                if process.poll() is not None:
                    raise RuntimeError("test BIRD exited: " + process.stderr.read())
                if socket.exists():
                    try:
                        if count_routes(birdc, socket) == 2:
                            break
                    except (OSError, subprocess.SubprocessError, RuntimeError):
                        pass
                time.sleep(0.1)
            else:
                raise RuntimeError("test BIRD did not become ready")
            print("OK: initial live table contains 2 routes")
            for label, prefixes, expected in (
                ("update", updated, 3),
                ("rollback", original, 2),
            ):
                routes.write_text(render_bird(prefixes), encoding="utf-8")
                execute([bird, "-p", "-c", str(config)])
                response = execute([birdc, "-s", str(socket), "configure"])
                if "Reconfiguration" not in response and "reconfigured" not in response.lower():
                    raise RuntimeError("unexpected configure response: " + response)
                for _ in range(50):
                    if count_routes(birdc, socket) == expected:
                        break
                    time.sleep(0.1)
                else:
                    raise RuntimeError(f"{label}: expected {expected} routes")
                print(f"OK: live {label} contains {expected} routes")
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    print("Production BIRD and MikroTik BGP were not modified")


if __name__ == "__main__":
    main()
