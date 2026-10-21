"""Strict parser for BIRD 2 IPv4 route and BGP export listings."""
import ipaddress
import re
import subprocess

from ru_routes.generate import parse_prefixes

_ROUTE = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){3}/\d{1,2})\s+(\S+)\s+\[([^\]\s]+)\s+[^\]]+\]\s+\*")
_HEADER = re.compile(r"^Table\s+([A-Za-z0-9_]+):$")
_BANNER = re.compile(r"^BIRD\s+\S+\s+ready\.$")


def parse_listing(output, *, table="ru_restricted_table", source="ru_restricted_static"):
    seen_header = False
    found = set()
    for raw in output.splitlines():
        line = raw.strip()
        if not line or _BANNER.fullmatch(line):
            continue
        header = _HEADER.fullmatch(line)
        if header:
            if seen_header or header.group(1) != table:
                raise ValueError("unexpected BIRD table header")
            seen_header = True
            continue
        if not seen_header:
            raise ValueError("route listing missing expected table header")
        match = _ROUTE.match(line)
        if not match:
            raise ValueError("unexpected BIRD route line: " + line[:120])
        prefix, route_type, protocol = match.groups()
        network = ipaddress.ip_network(prefix, strict=True)
        if network.version != 4 or route_type != "blackhole" or protocol != source:
            raise ValueError("unexpected route attributes: " + line[:120])
        if network in found:
            raise ValueError("duplicate route: " + prefix)
        found.add(network)
    if not seen_header:
        raise ValueError("missing expected table header")
    if not found:
        raise ValueError("empty BIRD route listing")
    return found


def verify_listings(expected_text, table_output, export_output):
    expected = set(parse_prefixes(expected_text))
    actual = parse_listing(table_output)
    exported = parse_listing(export_output)
    if actual != expected:
        raise ValueError("BIRD table prefix mismatch: missing=%d extra=%d" %
                         (len(expected - actual), len(actual - expected)))
    if exported != expected:
        raise ValueError("BGP export prefix mismatch: missing=%d extra=%d" %
                         (len(expected - exported), len(exported - expected)))
    return len(expected)


def read_and_verify(expected_text, *, birdc="birdc"):
    def query(*args):
        return subprocess.run([birdc, *args], check=True, capture_output=True,
                              text=True, timeout=20).stdout
    table = query("show", "route", "table", "ru_restricted_table")
    exported = query("show", "route", "export", "ru_to_mikrotik")
    return verify_listings(expected_text, table, exported)
