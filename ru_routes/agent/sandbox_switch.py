"""Exercise apply + rollback on a disposable static-only BIRD child.

This does NOT touch the production BIRD daemon, BGP peers or kernel routes.
The pre-change configuration is a generated empty static baseline, not a
snapshot of the system BIRD configuration. Fail closed on any mismatch.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time

from ru_routes.agent.isolated import make_isolated
from ru_routes.agent.live_test import validate_tables
from ru_routes.manager.inspect import inspect


ROUTE_LINE = re.compile(r'^\s*route [0-9./]+ blackhole;\s*$', re.M)
BANNER = re.compile(r'^BIRD \S+ ready\.$')
TABLE = re.compile(r'^Table (rt_[a-z][a-z0-9_]{0,31}):$')


def build_empty_baseline(target_text):
    """Only remove literal static blackhole route lines from generated config."""
    if 'protocol bgp' in target_text or 'protocol kernel' in target_text:
        raise ValueError('isolated target unexpectedly contains network protocols')
    routes = ROUTE_LINE.findall(target_text)
    if not routes:
        raise ValueError('isolated target has no static routes')
    baseline = ROUTE_LINE.sub('', target_text)
    if 'route ' in baseline:
        raise ValueError('unexpected route syntax in baseline')
    return baseline


def verify_empty_listing(text, table):
    """Strictly require the empty table on the disposable process."""
    lines = [line.strip() for line in text.splitlines() if line.strip()
             and line.strip() != 'Access restricted'
             and not BANNER.fullmatch(line.strip())]
    if lines != [f'Table {table}:']:
        raise ValueError(f'{table}: rollback baseline not empty: {lines[:4]!r}')


def run_sandbox_switch(inbox, release_id, role, *, bird='bird', birdc='birdc',
                       timeout=12):
    evidence = inspect(inbox, release_id, role)
    with tempfile.TemporaryDirectory(prefix='rr-switch-sandbox-') as directory:
        root = Path(directory)
        target = root / 'target.conf'
        baseline = root / 'baseline.conf'
        socket = root / 'bird.ctl'
        pid = root / 'bird.pid'
        make_isolated(inbox, release_id, role, target)
        target_bytes = target.read_bytes()
        baseline.write_text(build_empty_baseline(target_bytes.decode('utf-8')),
                            encoding='utf-8')
        for config in (target, baseline):
            subprocess.run([bird, '-p', '-c', str(config)], check=True,
                           capture_output=True, text=True, timeout=15)

        process = subprocess.Popen([bird, '-f', '-c', str(baseline),
                                    '-s', str(socket), '-P', str(pid)],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        activated = False
        rollback_verified = False

        def command(*arguments):
            output = subprocess.run([birdc, '-s', str(socket), *arguments],
                                    capture_output=True, text=True, timeout=15)
            if output.returncode:
                raise RuntimeError(f'sandbox birdc {arguments[0]} error: '
                                   f'{output.stderr[:400]!r}; {output.stdout[:400]!r}')
            return output.stdout

        def read_table(name):
            return command('-r', 'show', 'route', 'table', f'rt_{name}')

        def verify_empty():
            for name in evidence['routers']:
                verify_empty_listing(read_table(name), f'rt_{name}')

        def verify_target():
            return validate_tables(evidence['routers'],
                                   lambda table: command('-r', 'show', 'route',
                                                         'table', table))

        def await_check(check, label):
            deadline = time.monotonic() + timeout
            failure = None
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError('sandbox BIRD exited unexpectedly')
                try:
                    return check()
                except (RuntimeError, ValueError) as exc:
                    failure = exc
                    time.sleep(0.1)
            raise RuntimeError(f'{label} verification timed out: {failure}')

        def reconfigure(config):
            # BIRD 2 command grammar: configure "config file".
            answer = command('configure', f'"{config}"')
            if 'Reconfigured' not in answer and 'Reconfiguration in progress' not in answer:
                raise RuntimeError(f'unexpected sandbox configure result: {answer[:500]!r}')

        try:
            deadline = time.monotonic() + timeout
            while not socket.exists():
                if process.poll() is not None:
                    raise RuntimeError(f'sandbox BIRD startup failed: {process.returncode}')
                if time.monotonic() >= deadline:
                    raise TimeoutError('sandbox BIRD socket unavailable')
                time.sleep(0.1)
            await_check(verify_empty, 'initial baseline')
            # Mark the operation as attempted BEFORE issuing the configure request.
            activated = True
            reconfigure(target)
            verified = await_check(verify_target, 'target static routes')
            reconfigure(baseline)
            await_check(verify_empty, 'rollback baseline')
            rollback_verified = True
            return {
                'status': 'sandbox_apply_and_rollback_verified_not_active',
                'release_id': release_id,
                'role': role,
                'target_config_sha256': hashlib.sha256(target_bytes).hexdigest(),
                'routers': verified,
                'baseline_route_count': 0,
                'rollback_verified': True,
                'production_bird_touched': False,
            }
        finally:
            # If the target was attempted and rollback was not verified,
            # try to undo inside the disposable child. Always stop OUR child.
            if activated and not rollback_verified and process.poll() is None:
                try:
                    reconfigure(baseline)
                    await_check(verify_empty, 'error-path rollback')
                except Exception:
                    pass
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inbox', required=True)
    parser.add_argument('--release-id', required=True)
    parser.add_argument('--role', choices=('primary', 'backup'), required=True)
    args = parser.parse_args()
    print(json.dumps(run_sandbox_switch(args.inbox, args.release_id,
                                        args.role), indent=2))


if __name__ == '__main__':
    main()
