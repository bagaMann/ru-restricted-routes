"""Build an isolated static-only BIRD config from a verified agent inbox.

No BGP/device/kernel protocol is copied into the isolated configuration.
The generated routes must exactly match the release manifest.
"""
import argparse
import hashlib
import ipaddress
import json
from pathlib import Path
import re

from ru_routes.manager.inspect import inspect

STATIC = re.compile(r'protocol static st_([a-z][a-z0-9_]{0,31}) \{\s*ipv4 \{ table rt_\1; \};(?P<routes>(?:\s*route [0-9./]+ blackhole;)*)\s*\}', re.S)
ROUTE = re.compile(r'route ([0-9./]+) blackhole;')


def make_isolated(inbox, release_id, role, output):
    verified = inspect(inbox, release_id, role)
    source = Path(inbox) / release_id / 'bird.conf'
    text = source.read_text(encoding='utf-8')
    routers = verified['routers']
    found = {}
    for match in STATIC.finditer(text):
        name = match.group(1)
        if name in found or name not in routers:
            raise ValueError('duplicate or unexpected static table')
        raw = ROUTE.findall(match.group('routes'))
        networks = [str(ipaddress.IPv4Network(item, strict=True)) for item in raw]
        if len(networks) != len(set(networks)):
            raise ValueError('duplicate static prefix')
        canonical = sorted((ipaddress.IPv4Network(n) for n in networks))
        canonical = list(ipaddress.collapse_addresses(canonical))
        encoded = ''.join(f'{net}\n' for net in canonical).encode()
        expected = routers[name]
        if (len(canonical) != expected['prefix_count'] or
                hashlib.sha256(encoded).hexdigest() != expected['prefix_sha256']):
            raise ValueError(f'{name}: static prefixes do not match release')
        found[name] = canonical
    if set(found) != set(routers):
        raise ValueError('incomplete static tables')
    # Emit a new config from verified IP networks, never copy source statements.
    lines = ['# ISOLATED STATIC TEST ONLY - NO BGP OR KERNEL PROTOCOL',
             'router id 127.0.0.1;']
    for name, networks in sorted(found.items()):
        lines.extend([f'ipv4 table rt_{name};', f'protocol static st_{name} {{',
                      f'    ipv4 {{ table rt_{name}; }};'])
        lines.extend(f'    route {net} blackhole;' for net in networks)
        lines.append('}')
    content = '\n'.join(lines) + '\n'
    output = Path(output)
    if output.is_symlink() or output.resolve() == Path('/etc/bird') or Path('/etc/bird') in output.resolve().parents:
        raise ValueError('cannot write into live BIRD config')
    if output.exists():
        raise FileExistsError('isolated output must not already exist')
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        stream.write(content)
    return {'status': 'isolated_static_only', 'release_id': release_id,
            'role': role, 'routers': {name: len(nets) for name, nets in found.items()},
            'config_path': str(output)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inbox', required=True)
    p.add_argument('--release-id', required=True)
    p.add_argument('--role', choices=['primary', 'backup'], required=True)
    p.add_argument('--output', required=True)
    args = p.parse_args()
    print(json.dumps(make_isolated(args.inbox, args.release_id, args.role, args.output),
                     indent=2))


if __name__ == '__main__':
    main()
