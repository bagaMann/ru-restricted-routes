"""Stage isolated BIRD 2 configurations for groups, peers and two servers.

No BIRD reload, config installation, SSH or BGP connection occurs.
"""
import argparse
import hashlib
import ipaddress
import json
from pathlib import Path
import re

from ru_routes.generate import parse_prefixes
from ru_routes.update import fetch_sources, read_sources

NAME = re.compile(r'^[a-z][a-z0-9_]{0,31}$')


def parse_manual(text):
    lines = []
    for raw in text.splitlines():
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        if '/' not in line:
            line += '/32'
        lines.append(line)
    return parse_prefixes('\n'.join(lines))


def load_config(path):
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError('PyYAML required: apt install python3-yaml') from exc
    obj = yaml.safe_load(path.read_text(encoding='utf-8'))
    if not isinstance(obj, dict) or set(obj) != {'servers', 'routers'}:
        raise ValueError('config must contain exactly servers and routers')
    servers, routers = obj['servers'], obj['routers']
    if not isinstance(servers, dict) or set(servers) != {'primary', 'backup'}:
        raise ValueError('servers must define primary and backup')
    if not isinstance(routers, dict) or not routers:
        raise ValueError('at least one router required')
    for role, server in servers.items():
        if not isinstance(server, dict) or set(server) != {'local_ip', 'local_as'}:
            raise ValueError(f'invalid server {role}')
        ipaddress.IPv4Address(server['local_ip'])
        check_as(server['local_as'])
    for name, router in routers.items():
        if not NAME.fullmatch(name):
            raise ValueError(f'invalid router name: {name}')
        if not isinstance(router, dict) or set(router) != {'neighbor', 'remote_as', 'groups'}:
            raise ValueError(f'invalid router {name}')
        ipaddress.IPv4Address(router['neighbor'])
        check_as(router['remote_as'])
        groups = router['groups']
        if not isinstance(groups, list) or not groups or not all(isinstance(g, str) and NAME.fullmatch(g) for g in groups):
            raise ValueError(f'invalid groups for {name}')
        if len(set(groups)) != len(groups) or ('all' in groups and len(groups) != 1):
            raise ValueError(f'ambiguous groups for {name}')
    return obj


def check_as(value):
    if type(value) is not int or not (1 <= value <= 4294967295):
        raise ValueError(f'invalid AS number: {value}')


def discover_groups(source_dir):
    if not source_dir.is_dir():
        raise ValueError('sources directory not found')
    paths = sorted(source_dir.glob('*.txt'))
    if not paths:
        raise ValueError('no source files')
    groups = {}
    for path in paths:
        name = path.stem
        if not NAME.fullmatch(name) or name == 'all':
            raise ValueError(f'invalid group filename: {path.name}')
        if path.is_symlink():
            raise ValueError('symlink source not allowed')
        if name == 'manual' or name.endswith('_manual'):
            networks = parse_manual(path.read_text(encoding='utf-8-sig'))
            origin = [{'file': path.name}]
        else:
            urls = read_sources(path)
            feeds = fetch_sources(urls)
            networks = parse_prefixes('\n'.join(payload.decode('utf-8-sig') for _, payload, _ in feeds))
            origin = [{'url': url, 'sha256': hashlib.sha256(payload).hexdigest()} for url, payload, _ in feeds]
        groups[name] = {'networks': networks, 'sources': origin}
    return groups


def render_role(role, config, groups):
    server = config['servers'][role]
    lines = ['# STAGED ONLY: never include directly in production BIRD',
             f'router id {server["local_ip"]};', 'protocol device route_device {}']
    result = {}
    for name, router in sorted(config['routers'].items()):
        selected = sorted(groups) if router['groups'] == ['all'] else router['groups']
        unknown = set(selected) - set(groups)
        if unknown:
            raise ValueError(f'{name}: missing groups {sorted(unknown)}')
        nets = list(ipaddress.collapse_addresses(n for g in selected for n in groups[g]['networks']))
        if not nets or len(nets) > 20000:
            raise ValueError(f'{name}: invalid prefix count {len(nets)}')
        table = f'rt_{name}'
        static = f'st_{name}'
        lines += [f'ipv4 table {table};', f'protocol static {static} {{', f'    ipv4 {{ table {table}; }};']
        lines += [f'    route {n} blackhole;' for n in nets]
        lines += ['}', f'filter ex_{name} {{', f'    if (source = RTS_STATIC) && (proto = "{static}") then accept;', '    reject;', '}',
                  f'protocol bgp bgp_{name} {{', f'    local {server["local_ip"]} as {server["local_as"]};',
                  f'    neighbor {router["neighbor"]} as {router["remote_as"]};',
                  f'    ipv4 {{ table {table}; import none; export filter ex_{name}; }};', '}']
        result[name] = {'groups': selected, 'prefix_count': len(nets), 'prefix_sha256': hashlib.sha256((''.join(f'{n}\n' for n in nets)).encode()).hexdigest()}
    return '\n'.join(lines) + '\n', result


def stage(config_path, sources_dir, output_dir):
    destination = output_dir.resolve()
    if destination == Path('/etc/bird') or Path('/etc/bird') in destination.parents:
        raise ValueError('staging into /etc/bird is forbidden')
    config = load_config(config_path)
    groups = discover_groups(sources_dir)
    generated = {}
    manifest = {'status': 'staged_not_applied', 'groups': {}, 'servers': {}}
    for name, group in sorted(groups.items()):
        manifest['groups'][name] = {'prefix_count': len(group['networks']), 'sources': group['sources']}
    for role in ('primary', 'backup'):
        content, routers = render_role(role, config, groups)
        generated[role + '.bird.conf'] = content
        manifest['servers'][role] = {'routers': routers, 'config_sha256': hashlib.sha256(content.encode()).hexdigest()}
    # Validate all inputs before writing; never touch /etc/bird.
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, content in generated.items():
        (output_dir / filename).write_text(content, encoding='utf-8')
    (output_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, default=Path('config.yaml'))
    p.add_argument('--sources-dir', type=Path, default=Path('sources'))
    p.add_argument('--output-dir', type=Path, default=Path('/tmp/ru-routes-multigroup-stage'))
    a = p.parse_args()
    result = stage(a.config, a.sources_dir, a.output_dir)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
