# RU Restricted Routes

Experimental preparation of destination IPv4 prefixes for BIRD 2 and MikroTik BGP.

**Status: initial offline generator, not production ready.** No live feeds are bundled or automatically trusted. A list of Russian networks is NOT equivalent to a verified list of geoblocked services.

## First offline test

On Debian with Python 3.9+:

```bash
python3 -m unittest discover -s tests -v
printf '8.8.8.0/24\n' > /tmp/example-prefixes.txt
python3 -m ru_routes.generate --input /tmp/example-prefixes.txt --output /tmp/ru-restricted.conf
cat /tmp/ru-restricted.conf
```

`8.8.8.0/24` is **test data only**, not a Russian-service prefix.

The generated fragment references a dedicated `ru_restricted_table`, which must be declared separately in BIRD 2 (`ipv4 table ru_restricted_table;`). **Do not include the fragment in a live configuration yet.** Blackhole static routes are for BGP origination only and must never be imported into the forwarding/kernel table. For BIRD 1.x a separate generator will be required.

## Planned

1. Curated, reviewed feeds for banks, government, marketplaces and streaming.
2. Secure source fetching, CIDR normalization, provenance, change limits and rollback.
3. BIRD version-aware export filters, no kernel import, staged configuration validation.
4. systemd timer and operational tests.

Do not install arbitrary third-party feed scripts as root. CIDR lists alone cannot guarantee service-level geolocation routing, particularly on shared CDN IPs.
