#!/usr/bin/env python3
"""Validate a local CIDR feed and generate an isolated BIRD static protocol.

No downloads, daemon reloads or changes to routing are performed.
"""
import argparse
import ipaddress
import os
from pathlib import Path
import tempfile


def parse_prefixes(text: str) -> list[ipaddress.IPv4Network]:
    networks = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        try:
            network = ipaddress.ip_network(line, strict=True)
        except ValueError as exc:
            raise ValueError(f'line {number}: invalid CIDR {line!r}: {exc}') from exc
        if not isinstance(network, ipaddress.IPv4Network) or not network.is_global:
            raise ValueError(f'line {number}: non-global IPv4 prefix {line!r}')
        networks.append(network)
    if not networks:
        raise ValueError('input contains no usable IPv4 networks')
    return list(ipaddress.collapse_addresses(networks))


def render_bird(networks: list[ipaddress.IPv4Network]) -> str:
    lines = ['# Generated file; review before including in bird.conf',
             '# blackhole is used ONLY to originate BGP routes; do not import into kernel.',
             'protocol static ru_restricted_static {',
             '    ipv4 { table ru_restricted_table; };']
    lines.extend(f'    route {network} blackhole;' for network in networks)
    lines.append('}')
    return '\n'.join(lines) + '\n'


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--max-prefixes', type=int, default=20000)
    args = parser.parse_args()
    if args.max_prefixes < 1:
        parser.error('--max-prefixes must be positive')
    networks = parse_prefixes(args.input.read_text(encoding='utf-8'))
    if len(networks) > args.max_prefixes:
        parser.error(f'prefix count {len(networks)} exceeds limit {args.max_prefixes}')
    content = render_bird(networks)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=args.output.parent, prefix='.ru-routes-', text=True)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, args.output)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    print(f'Generated {len(networks)} prefixes in {args.output}')


if __name__ == '__main__':
    main()
