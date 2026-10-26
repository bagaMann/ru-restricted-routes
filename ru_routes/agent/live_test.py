"""Run a short-lived, BGP-free BIRD and check its actual IPv4 tables.

No live /etc/bird changes, no BGP, no kernel protocol, no global control socket.
"""
import argparse
import hashlib
import ipaddress
import json
from pathlib import Path
import subprocess
import tempfile
import time

from ru_routes.agent.isolated import make_isolated
from ru_routes.manager.inspect import inspect
from ru_routes.verify_routes import parse_listing


def validate_tables(routers, query):
    result = {}
    for name, expected in sorted(routers.items()):
        output = query(f'rt_{name}')
        try:
            actual = parse_listing(output, table=f'rt_{name}', source=f'st_{name}')
        except ValueError as exc:
            raise ValueError(f'{name}: unexpected birdc output: {exc}; '
                             f'first 1200 bytes={output[:1200]!r}') from exc
        normalized = list(ipaddress.collapse_addresses(sorted(actual)))
        content = ''.join(f'{net}\n' for net in normalized).encode()
        count = len(normalized)
        digest = hashlib.sha256(content).hexdigest()
        if count != expected['prefix_count'] or digest != expected['prefix_sha256']:
            raise ValueError(f'{name}: BIRD table differs from release: '
                             f'count={count}, expected={expected["prefix_count"]}')
        result[name] = {'prefix_count': count, 'prefix_sha256': digest}
    return result


def run_isolated(inbox, release_id, role, *, bird='bird', birdc='birdc',
                 startup_timeout=10):
    verified = inspect(inbox, release_id, role)
    with tempfile.TemporaryDirectory(prefix='rr-isolated-bird-') as folder:
        path = Path(folder)
        config = path / 'static.bird.conf'
        socket = path / 'bird.ctl'
        pid = path / 'bird.pid'
        make_isolated(inbox, release_id, role, config)
        subprocess.run([bird, '-p', '-c', str(config)], check=True, timeout=15,
                       capture_output=True)
        # -f keeps only OUR child process in the foreground.
        # -s and -P prevent collision with the system bird service.
        proc = subprocess.Popen([bird, '-f', '-c', str(config), '-s',
                                 str(socket), '-P', str(pid)],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + startup_timeout
            while True:
                if proc.poll() is not None:
                    raise RuntimeError(f'isolated BIRD exited early: {proc.returncode}')
                if socket.exists():
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError('isolated BIRD control socket did not appear')
                time.sleep(0.1)

            def query(table):
                completed = subprocess.run([birdc, '-r', '-s', str(socket),
                                            'show', 'route', 'table', table],
                                           capture_output=True, text=True, timeout=15)
                if completed.returncode:
                    raise RuntimeError(f'isolated BIRD query failed for {table}: '
                                       f'stdout={completed.stdout[:400]!r}; '
                                       f'stderr={completed.stderr[:400]!r}')
                return completed.stdout

            # Route initialization can briefly lag socket creation.
            last_error = None
            while time.monotonic() < deadline:
                try:
                    routers = validate_tables(verified['routers'], query)
                    return {'status': 'isolated_live_routes_verified',
                            'release_id': release_id, 'role': role, 'routers': routers}
                except (ValueError, RuntimeError) as exc:
                    last_error = exc
                    if proc.poll() is not None:
                        break
                    time.sleep(0.15)
            raise RuntimeError(f'isolated BIRD routes failed verification: {last_error}')
        finally:
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inbox', required=True)
    parser.add_argument('--release-id', required=True)
    parser.add_argument('--role', choices=('primary', 'backup'), required=True)
    args = parser.parse_args()
    print(json.dumps(run_isolated(args.inbox, args.release_id, args.role), indent=2))


if __name__ == '__main__':
    main()
