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


## Experimental multi-group / dual-BIRD staging

This is a **separate, non-applying prototype**. The current `ru_routes.update`,
`review`, `apply`, production BIRD configuration and BGP sessions remain
unchanged. The prototype **does not deploy, synchronize, or activate**
anything on either BIRD server.

Install the YAML parser on Debian (`apt install python3-yaml`). Configure
`config.yaml` with two servers (`primary`, `backup`) and the router
neighbor/AS/group selection. The provided addresses from 192.0.2.0/24 are
**documentation placeholders** and must not be applied.

Each `sources/<group>.txt` contains one or more HTTPS URLs to line-oriented
IPv4 CIDR lists. The reserved `sources/manual.txt` contains public IPv4
addresses (normalized to /32) or CIDRs, not URLs. A `google.txt` or
`meta.txt` becomes an additional named group as soon as the file is created
with at least one valid URL. An optional group named `foo_manual.txt` is
treated as another distinct manual group. Empty or invalid source files
**cause staging to fail**, rather than publish a partial set.

Router group assignments in YAML may be `[ru, manual]`, `[meta, google]`
or `[all]`. The last form includes every discovered group. A missing named
group is an error. The generator builds an isolated routing table, static
blackhole-origin protocol and strictly filtered export for each router.

Run from repository root:

```bash
python3 -m unittest discover -s tests -v
python3 -m ru_routes.multigroup --config config.yaml --sources-dir sources \
  --output-dir /tmp/ru-routes-multigroup-stage
```

Inspect `primary.bird.conf`, `backup.bird.conf`, and `manifest.json`
in the staging directory. The manifest has group and per-router prefix
counts plus SHA256 digests. Both roles use **the same downloaded inputs in
one run**. The staged BIRD configs are complete standalone example configs,
not include fragments; **never include them in the live /etc/bird/bird.conf**.
No automated promotion, rollback, BIRD convergence verification, server
distribution, or MikroTik failover mechanism has been implemented for this
new multi-group path. Those require isolated BIRD/BGP integration tests
before any production use.


## Independent Manager / Agent — prototype branch

The immutable release prototype lives in `feature/independent-manager-agent`.
The 64-test checkpoint remains at `checkpoint/multigroup-64-tests-20261023`.
Nothing in this prototype modifies BIRD, sends SSH, or automatically activates routes.

After generating a multi-group stage, build one consistent release for both roles:

```bash
python3 -m ru_routes.multigroup --output-dir /tmp/rr-stage
python3 -m ru_routes.manager.release --stage-dir /tmp/rr-stage --release-dir /tmp/rr-release-001
python3 -m ru_routes.agent.stage --release-dir /tmp/rr-release-001 --role primary --inbox /tmp/rr-primary-inbox
python3 -m ru_routes.agent.stage --release-dir /tmp/rr-release-001 --role backup --inbox /tmp/rr-backup-inbox
python3 -m unittest discover -s tests -v
```

The manager release includes role-specific config checksums, per-router prefix
identities and a deterministic `release_id`; the agent stages a checked bundle
in an immutable per-release directory. Duplicate publication is rejected.

**Not implemented:** authenticated transport, signatures/trust anchors,
filesystem durability on sudden power loss, sandboxing, BIRD syntax checks,
atomic promotion, rollback or live BGP validation. SHA256 detects accidental
changes but is not sender authentication. Do not use the prototype to deploy
configurations on production servers.
