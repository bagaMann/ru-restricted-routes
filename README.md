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


## Coordinated prepare (feature branch)

After building a release, the independent manager can prepare both agent
inboxes, **without activating BIRD or using SSH**:

```bash
python3 -m ru_routes.manager.prepare --release-dir /tmp/rr-release-001 \
  --primary-inbox /tmp/rr-agent-primary \
  --backup-inbox /tmp/rr-agent-backup
```

The coordinator pre-validates both role-specific bundles before any publication,
rejects duplicates and mismatched destinations, and attempts to remove only the
first freshly staged directory if preparing the second role fails. This is
**best-effort cleanup rather than an atomic distributed transaction**. It does
not handle a process crash or network failure during publication.

This local-only protocol is preparation for authenticated SSH transport. It
does **not** perform cryptographic sender authentication, install live configs,
verify BIRD, alter BGP or perform failover. Keep production configuration
unchanged.


### Verify both locally prepared agent bundles

The manager can inspect the **already staged** release without trusting a status
string from an agent or changing BIRD:

```bash
python3 -m ru_routes.manager.inspect \
  --release-id YOUR_64_HEX_RELEASE_ID \
  --primary-inbox /tmp/rr-agent-primary \
  --backup-inbox /tmp/rr-agent-backup
```

This verifies each copied config's SHA256, manifest release identity and
identical per-router route-set metadata across roles. Output
`both_prepared_verified_not_active` means staged files are intact, **not**
that BIRD has loaded them or that the BGP peers received them.

Live SSH trust, process-crash-safe durable transactions and BIRD activation
remain pending.


### Experimental SSH release transfer — staging only

`ru_routes.manager.ssh_transport` sends one checked, role-specific bundle over
OpenSSH with `BatchMode=yes`, `StrictHostKeyChecking=yes` and a dedicated
`known_hosts` path. `ru_routes.agent.remote` receives bounded JSON via stdin
and reuses the local package integrity verification. It **does not activate or
reload BIRD**.

Both machines must have the same feature branch installed in a Python search
path, and the destination inbox must be writable by an unprivileged account.
Example for a test server only (set real test addresses and review SSH host
key fingerprints independently before populating `known_hosts`):

```bash
python3 -m ru_routes.manager.ssh_transport \
  --release-dir /tmp/rr-release-001 --role primary \
  --host TEST_BIRD_PRIMARY --user routesync \
  --remote-inbox /var/lib/routesync/inbox \
  --known-hosts /home/manager/.ssh/route-agent-known_hosts
```

Repeat for role `backup` and its other host. Each successful transfer returns
`prepared_not_activated` with the same `release_id`. This is **not yet**
a distributed two-node transaction, nor an SSH installer. Forced-command
restrictions, isolated agent service account, durable crash recovery,
host-key enrollment, two-node reconciliation and activation/rollback must be
implemented and tested before a production rollout.


### SSH agent account hardening (test servers only)

A separate `ru_routes.agent.forced` entrypoint keeps the agent role and
destination directory fixed by the **server's** SSH key configuration.
It ignores `SSH_ORIGINAL_COMMAND` and accepts only bounded release data
on standard input. A client-supplied command or inbox path is not trusted.

Use a dedicated unprivileged `routesync` account with no sudo or access
to `/etc/bird`. Install this repository on each test agent, pin the
manager's public SSH key in that account's `authorized_keys`, and
**manually** restrict the key to a forced command. For example, on the
*primary test agent* (replace `SSH_PUBLIC_KEY` with the manager's
real public key; do not use a shared password or private key here):

```text
restrict,command="cd /opt/ru-restricted-routes && exec /usr/bin/python3 -m ru_routes.agent.forced --role primary --inbox /var/lib/routesync/inbox" SSH_PUBLIC_KEY
```

On the backup test agent, configure `--role backup`, with its own
account, key authorization and inbox. Restrict permissions on the inbox
so only that agent account can write it, and do not expose this account
through password authentication. Independently verify SSH server host key
fingerprints and populate the manager's dedicated `known_hosts` file
before attempting any transfer.

The manager's experimental SSH transport still sends a remote command
for compatibility with test SSH servers; OpenSSH's **server-side**
`authorized_keys` forced command overrides it. Never grant unrestricted
shell access to the manager key. Test first on non-production hosts.

**Limits:** This is staging only. It is not a privileged installer,
distributed transaction, key-provisioning automation, route activation or
proof that live BGP exports match the proposed release.


### BGP-free isolated validation (new)

The agent can now derive a **new**, static-only BIRD configuration from a
previously verified release. It reconstructs route declarations from parsed
IPv4 networks, checks each table's prefix count and SHA256 against the release
manifest and **never copies BGP, device or kernel protocols** from the input.
It refuses overwriting existing output files and writing under /etc/bird.

Run as administrator on each **test** agent after pulling the current branch:

```bash
cd /opt/ru-restricted-routes-agent
python3 -m ru_routes.agent.isolated \
  --inbox /var/lib/routesync/inbox \
  --release-id 4afc7549adfd5025ba4069db797487996ef9974c8a1505387e83b241b7732f84 \
  --role primary \
  --output /tmp/rr-primary-isolated.bird.conf
bird -p -c /tmp/rr-primary-isolated.bird.conf
```

Change `--role backup` and output basename to `rr-backup-isolated.bird.conf`
on the backup test agent. Review the generated files: there must be no
`protocol bgp`, `neighbor`, `protocol kernel`, or `protocol device`.
This stage **only generates a separate config**. Running a second BIRD daemon,
examining its actual routing tables, crash recovery and activation/rollback
will be developed and tested separately.

 
### Short-lived isolated BIRD runtime test (test nodes only)

After stage verification and BGP-free config generation pass, run the
separate Python test runner. It generates a fresh static-only config in a
temporary directory, validates syntax via `bird -p`, launches a *child*
`bird -f` with its own `-s` control socket and `-P` PID file, then uses
`birdc -r -s` to compare each actual table with the release's exact
IPv4 prefix digest. The child is terminated on both success and failure.
It does not call `birdc` against the system socket, nor touch
`/etc/bird`.

Primary test host:

```bash
cd /opt/ru-restricted-routes-agent
python3 -m ru_routes.agent.live_test \
  --inbox /var/lib/routesync/inbox \
  --release-id 4afc7549adfd5025ba4069db797487996ef9974c8a1505387e83b241b7732f84 \
  --role primary
```

Backup test host: run the same command with `--role backup`.

Expected JSON status is `isolated_live_routes_verified` with exact route
counts and SHA256 per table. This does **not** establish BGP export
correctness or production readiness. Never run the *full received*
`bird.conf` as an additional process, because it contains BGP peers.

 
### Verified preflight receipt (not active)

After a successful isolated live-table test, the agent can persist a
**write-once local preflight receipt**. This re-runs the static-only
BIRD child test, compares each table's exact route digest with the
staged release, checks the staged files again, then publishes a
JSON receipt using a temporary file, fsync and a no-clobber hard link.
The receipt status is `preflight_verified_not_activated`.
It is **not** authority to activate BGP and is not a distributed commit.

On `bird-primary`:

```bash
cd /opt/ru-restricted-routes-agent
python3 -m ru_routes.agent.preflight \
  --inbox /var/lib/routesync/inbox \
  --release-id 4afc7549adfd5025ba4069db797487996ef9974c8a1505387e83b241b7732f84 \
  --role primary \
  --state-dir /var/lib/ru-routes-preflight
```

On `bird-backup`: repeat with `--role backup`. These commands
do not configure system BIRD or touch `/etc/bird`.
The receipt filename includes both release ID and role; attempting
to generate it twice intentionally raises `FileExistsError`.
Use a distinct state directory on each host, owned by a trusted
administrator. Crash-safe activation and multi-agent transaction
recovery remain future steps.

 
### Read-only preflight receipt inspection

On each agent independently, compare its persisted preflight receipt against
the **current staged release** before starting any future transaction:

```bash
cd /opt/ru-restricted-routes-agent
python3 -m ru_routes.agent.receipt \
  --inbox /var/lib/routesync/inbox \
  --release-id 4afc7549adfd5025ba4069db797487996ef9974c8a1505387e83b241b7732f84 \
  --role primary \
  --state-dir /var/lib/ru-routes-preflight
```

Use `--role backup` on bird-backup. Expected status is
`preflight_receipt_verified_not_active`. The command rejects mismatched
release IDs, roles, configuration digests and route selection hashes.
This is read-only and does **not** activate BIRD. Historical receipts alone
are not sufficient authorization for activation, especially if files can
be changed by a privileged local actor.

 
### Manager transaction intent journal (no activation yet)

New `ru_routes.manager.transaction_journal` introduces a single pending
transaction intent, protected by a filesystem lock and checksummed atomic
state publication with `fsync`. It verifies both role release bundles before
recording the intent. It refuses a second pending transaction, malformed
journal data or a journal path under `/etc/bird`.

On **Route Manager** (192.168.11.13):

```bash
cd /opt/ru-restricted-routes
python3 -m ru_routes.manager.transaction_journal \
  --state-dir /var/lib/ru-routes-manager-tx \
  init --release-dir /tmp/rr-release-001

python3 -m ru_routes.manager.transaction_journal \
  --state-dir /var/lib/ru-routes-manager-tx status
```

An explicit `abort --txid tx-...` closes a pending intent *without*
touching either agent. **This journal is only a preparatory mechanism**:
it has no ability to activate a route, command BIRD, verify remote current
state, or issue a two-host commit. The SHA256 journal checksum detects
accidental changes but is not an authentication signature against a local
privileged attacker. Keep the state directory administrator-owned.
Do not confuse `intent_prepared_not_active` with BGP activation.

 
### Manager receipt pair review (read-only, not authenticated)

`ru_routes.manager.review_receipts` validates two **separately supplied**
preflight JSON receipts against the two role-specific configuration hashes
and route digests in an immutable release. It also requires exactly one
matching pending manager journal transaction.

Important: the command validates **receipt content only**. It does not
fetch receipts from agents, attest which host produced each receipt,
authenticate a remote server, advance a transaction or activate BIRD.
Never use this status as sufficient authorization for an apply operation.
Copy receipts separately using a trusted, host-authenticated channel and
restrict write access to the copies before review.

```bash
cd /opt/ru-restricted-routes
python3 -m ru_routes.manager.review_receipts \
  --release-dir /tmp/rr-release-001 \
  --journal-dir /var/lib/ru-routes-manager-tx \
  --primary-receipt /root/rr-evidence/primary.json \
  --backup-receipt /root/rr-evidence/backup.json
```

Expected status: `receipt_contents_match_not_authenticated_not_active`.
Remote attestation/status via a separately restricted SSH capability, and
recovery-aware activation, are required before any live BGP change.

 
### Remote authenticated read-only status (test agents)

A separate, **status-only SSH key** is required. Do not reuse the manager
staging key: that key is forced to `ru_routes.agent.forced` for uploads.
The new forced-command endpoint `ru_routes.agent.status_remote` accepts only
a JSON object `{"schema":1,"release_id":"<64 hex>"}` on stdin and performs
read-only inspection of the previously generated preflight receipt and
staged release. The manager `ru_routes.manager.check_agents` queries
two pinned SSH hosts with that identity, checks both replies against
the still-pending local transaction and release route digests and prints
`both_remote_preflights_verified_not_active`. This does NOT activate BIRD.

On manager (192.168.11.13) create a dedicated SSH key:

```bash
test ! -e /root/.ssh/ru_routes_status_ed25519 &&
ssh-keygen -t ed25519 -a 100 -N '' \
  -f /root/.ssh/ru_routes_status_ed25519 \
  -C ru-routes-status-manager
cat /root/.ssh/ru_routes_status_ed25519.pub
```

On **each** agent after `git pull --ff-only` and successful tests,
grant `routesync` narrowly scoped read and traverse permission for the
preflight receipt directory (Debian: `apt install acl`; preserve root
ownership). Do not grant write permission:

```bash
setfacl -m u:routesync:rx /var/lib/ru-routes-preflight
setfacl -m u:routesync:r /var/lib/ru-routes-preflight/*.preflight.json
```

Append (DO NOT overwrite) a second entry to
`/var/lib/routesync/.ssh/authorized_keys` with the new status
public key and a fixed server-side command:

- bird-primary, 10.110.11.201:
  `restrict,from="192.168.11.13",command="cd /opt/ru-restricted-routes-agent && exec /usr/bin/python3 -m ru_routes.agent.status_remote --role primary --inbox /var/lib/routesync/inbox --state-dir /var/lib/ru-routes-preflight" ssh-ed25519 AAAA... ru-routes-status-manager`
- bird-backup, 10.110.27.201:
  `restrict,from="192.168.11.13",command="cd /opt/ru-restricted-routes-agent && exec /usr/bin/python3 -m ru_routes.agent.status_remote --role backup --inbox /var/lib/routesync/inbox --state-dir /var/lib/ru-routes-preflight" ssh-ed25519 AAAA... ru-routes-status-manager`

The manager public key must be copied in full, replacing
`ssh-ed25519 AAAA... ru-routes-status-manager` exactly.
Keep file owner `routesync:routesync` and permissions `0600`.
Validate the two independently confirmed SSH host fingerprints in the
dedicated known_hosts before continuing.

```bash
cd /opt/ru-restricted-routes
python3 -m ru_routes.manager.check_agents \
  --release-dir /tmp/rr-release-001 \
  --journal-dir /var/lib/ru-routes-manager-tx \
  --primary-host 10.110.11.201 \
  --backup-host 10.110.27.201 \
  --identity-file /root/.ssh/ru_routes_status_ed25519 \
  --known-hosts /root/.ssh/route-agent-known_hosts
```

Note: the manager's report is only as fresh as the saved preflight
receipts. It does not re-run BIRD or verify currently active BGP. Before
activation, a new authenticated preflight/status protocol, a privileged
least-authority service and crash-safe recovery must be implemented.

 
### Agent-side durable prepare intent (no activation)

The new `ru_routes.agent.intent` provides a local, administrator-only
write-ahead record tied to a verified preflight receipt and the manager's
transaction ID. The only states are `agent_prepared_not_active` and
`agent_aborted_not_active`. Locking, checksums and fsynced atomic
publication detect corruption and prevent a second local prepare.
An interrupted transaction remains visible; it is **not** silently retried.
The journal intentionally has no command for activating or reconfiguring BIRD.

Use the existing manager transaction ID, unchanged, on each test node.
On bird-primary:

```bash
cd /opt/ru-restricted-routes-agent
python3 -m ru_routes.agent.intent --state-dir /var/lib/ru-routes-agent-tx \
  prepare --inbox /var/lib/routesync/inbox \
  --receipt-dir /var/lib/ru-routes-preflight \
  --release-id 4afc7549adfd5025ba4069db797487996ef9974c8a1505387e83b241b7732f84 \
  --role primary \
  --txid tx-9aedb448667daced2d34dea9d9285f2c894d3b8ddc757bf3cc92e7ae6d5adbfe

python3 -m ru_routes.agent.intent --state-dir /var/lib/ru-routes-agent-tx status
```

On bird-backup, use exactly the same commands with `--role backup`.
These are **local administrator operations**, not commands available
through either restricted SSH key. Do not attempt another prepare without
explicitly inspecting and reconciling the existing agent state.
The optional `abort --txid ...` records cancellation only when the
intent is still prepared and not active.
This is groundwork for an activation/rollback protocol, not a complete
two-phase commit implementation.

 
### Read-only reconciliation of manager + primary + backup intent journals

The status-only SSH forced command now optionally accepts an `intent`
query. Enable it by adding `--agent-state-dir /var/lib/ru-routes-agent-tx`
to the **existing status-only** forced-command SSH entry on each agent.
Do not change the separate upload key's forced command. The new query
only reads a checksummed agent state; it cannot advance states or
activate/reload BIRD.

On **each agent** as root (after pulling the new revision and confirming
tests), allow `routesync` to read, but not write, the agent intent
journal:

```bash
setfacl -m u:routesync:rx /var/lib/ru-routes-agent-tx
setfacl -m u:routesync:r /var/lib/ru-routes-agent-tx/agent-state.json
getfacl /var/lib/ru-routes-agent-tx /var/lib/ru-routes-agent-tx/agent-state.json
```

In `/var/lib/routesync/.ssh/authorized_keys`, modify **only** the
existing `ru-routes-status-manager` key entry:
- on primary, its forced command becomes
  `cd /opt/ru-restricted-routes-agent && exec /usr/bin/python3 -m ru_routes.agent.status_remote --role primary --inbox /var/lib/routesync/inbox --state-dir /var/lib/ru-routes-preflight --agent-state-dir /var/lib/ru-routes-agent-tx`
- on backup the same with `--role backup`.

Preserve the `restrict,from="192.168.11.13",command="..."` authorized_keys
options and the **existing** ed25519 status public key. Do not append
a duplicate status key; edit its existing line.

On Route Manager:

```bash
cd /opt/ru-restricted-routes
python3 -m ru_routes.manager.reconcile_intents \
  --release-dir /tmp/rr-release-001 \
  --journal-dir /var/lib/ru-routes-manager-tx \
  --primary-host 10.110.11.201 \
  --backup-host 10.110.27.201 \
  --identity-file /root/.ssh/ru_routes_status_ed25519 \
  --known-hosts /root/.ssh/route-agent-known_hosts
```

Expected status: `all_three_intents_match_not_active`, including the
manager's exact `txid` and matching roles/release IDs from both
separate SSH servers. Failed/missing/ambiguous reports are errors,
not a reason to resume an incomplete transaction automatically.
This remains a **read-only verification**, not an activation protocol.

 
### Unified readiness plan — still no activation

`ru_routes.manager.deployment_plan` combines the read-only preflight
query and the three-journal reconciliation. It fails closed if either
remote agent cannot be authenticated, the current release/transaction
identity differs, or a route digest disagrees. A successful result
documents a **proposed** sequence: `backup` then `primary`.

```bash
cd /opt/ru-restricted-routes
python3 -m ru_routes.manager.deployment_plan \
  --release-dir /tmp/rr-release-001 \
  --journal-dir /var/lib/ru-routes-manager-tx \
  --primary-host 10.110.11.201 \
  --backup-host 10.110.27.201 \
  --identity-file /root/.ssh/ru_routes_status_ed25519 \
  --known-hosts /root/.ssh/route-agent-known_hosts
```

Expected: `readiness_plan_only_not_authorized_not_active`.
No network or BIRD changes are made beyond authenticated SSH **read**
requests. Results are a snapshot, **not an authorization token** or
proof of live BGP state. Before activation we still need separate
isolated rollback snapshots, live export verification, agent-controlled
activation permissions and recovery after interruption.

 
### Disposable BIRD configure-and-rollback exercise (test agents only)

The next step exercises an actual BIRD 2 `configure` transaction **only
inside a short-lived child BIRD** using an isolated control socket and
PID file. It starts with **empty static tables**, reconfigures to the
verified staged static routes, checks exact route SHA256 for each
table, reconfigures back to the empty baseline, verifies the tables
are empty and shuts down the child process.

This is NOT a snapshot of `/etc/bird`, not a rollback of production
BIRD, and not BGP activation. The test deliberately contains no
`protocol bgp` or `protocol kernel`; the normal BIRD process is
unaffected. The error path attempts rollback and always terminates
the disposable child.

On `bird-primary`:

```bash
cd /opt/ru-restricted-routes-agent
python3 -m ru_routes.agent.sandbox_switch \
  --inbox /var/lib/routesync/inbox \
  --release-id 4afc7549adfd5025ba4069db797487996ef9974c8a1505387e83b241b7732f84 \
  --role primary
```

On `bird-backup`, use `--role backup`. Expected status:
`sandbox_apply_and_rollback_verified_not_active`, with
`rollback_verified: true`, `production_bird_touched: false`.
This **does not** change the agent or manager intent journal.

 
### Persistent static-only recovery bundle (no system BIRD changes)

`ru_routes.agent.recovery_bundle` builds and atomically publishes a
write-once bundle for a **future separately managed static-only BIRD**
instance. The bundle contains `target.bird.conf`, an empty-table
`baseline.bird.conf` and `snapshot.json` with SHA256 checksums and
the pre-existing agent `txid`. It first requires the matching
`agent_prepared_not_active` local intent and checks the staged
release again. `verify` detects missing/tampered files.
This is **not a snapshot of the currently running system BIRD** and
cannot restore that process; we must implement separate owned-process
startup and crash recovery before permanent activation.

After pulling and passing tests on each agent, run the following on
`bird-primary`:

```bash
cd /opt/ru-restricted-routes-agent
python3 -m ru_routes.agent.recovery_bundle \
  --storage /var/lib/ru-routes-managed-recovery \
  capture --inbox /var/lib/routesync/inbox \
  --release-id 4afc7549adfd5025ba4069db797487996ef9974c8a1505387e83b241b7732f84 \
  --role primary \
  --txid tx-9aedb448667daced2d34dea9d9285f2c894d3b8ddc757bf3cc92e7ae6d5adbfe \
  --agent-state-dir /var/lib/ru-routes-agent-tx

python3 -m ru_routes.agent.recovery_bundle \
  --storage /var/lib/ru-routes-managed-recovery \
  verify --release-id 4afc7549adfd5025ba4069db797487996ef9974c8a1505387e83b241b7732f84 \
  --role primary \
  --txid tx-9aedb448667daced2d34dea9d9285f2c894d3b8ddc757bf3cc92e7ae6d5adbfe
```

Use `--role backup` on bird-backup. Both commands are local and
write only to the new storage directory; they do **not** touch the
agent intent journal or live BIRD. Running `capture` twice for
the same txid and role intentionally fails rather than overwriting
rollback evidence.

 
### Dedicated managed BIRD baseline service (opt-in test, no BGP)

`ru_routes.agent.managed_bird` checks the agent intent and the
write-once static-only recovery bundle, then executes BIRD with the
**EMPTY** baseline. The socket and PID file live in
`/run/ru-routes-managed-bird`, separate from the system `bird.service`.
A restart always loads the empty baseline; it does **not** replay a
previously applied target or automatically enable advertisements.

The optional unit `deploy/ru-routes-managed-bird.service` is **not
installed or enabled by a git pull**. It denies ambient capabilities,
restricts filesystem writes to its runtime directory and contains
no BGP or kernel protocol. Do not install it on an existing production
BIRD host without isolating and testing the environment.

On each test agent after successful tests and recovery-bundle verification,
first validate the startup spec **without starting any process**:

```bash
mkdir -p -m 0700 /run/ru-routes-managed-bird
cd /opt/ru-restricted-routes-agent
python3 -m ru_routes.agent.managed_bird \
  --storage /var/lib/ru-routes-managed-recovery \
  --txid tx-9aedb448667daced2d34dea9d9285f2c894d3b8ddc757bf3cc92e7ae6d5adbfe \
  --release-id 4afc7549adfd5025ba4069db797487996ef9974c8a1505387e83b241b7732f84 \
  --role primary \
  --agent-state-dir /var/lib/ru-routes-agent-tx \
  --runtime-dir /run/ru-routes-managed-bird \
  --check-only
```

On bird-backup use `--role backup`. Expected status is
`managed_baseline_startup_only` and config path ending in
`baseline.bird.conf`; ensure the returned argv names only the separate
`managed.ctl` socket.

When ready for the **test service** on the chosen agent:

```bash
cat > /etc/ru-routes-managed.env <<'ENV'
STORAGE=/var/lib/ru-routes-managed-recovery
TXID=tx-9aedb448667daced2d34dea9d9285f2c894d3b8ddc757bf3cc92e7ae6d5adbfe
RELEASE_ID=4afc7549adfd5025ba4069db797487996ef9974c8a1505387e83b241b7732f84
ROLE=primary
AGENT_STATE_DIR=/var/lib/ru-routes-agent-tx
ENV
chmod 600 /etc/ru-routes-managed.env
install -m 0644 deploy/ru-routes-managed-bird.service \
  /etc/systemd/system/ru-routes-managed-bird.service
systemctl daemon-reload
systemctl start ru-routes-managed-bird
systemctl status --no-pager ru-routes-managed-bird
birdc -r -s /run/ru-routes-managed-bird/managed.ctl show route table rt_demo_all
```

On backup use `ROLE=backup`. The output of `show route` should
have the `Table rt_demo_all:` header **without routes**. Verify the
normal system BIRD process has not been changed with
`systemctl status bird` and its own socket as appropriate.

Do **not** run `systemctl enable` yet. A failure may be inspected with
`journalctl -u ru-routes-managed-bird -n 80 --no-pager`; stop this
separate process with `systemctl stop ru-routes-managed-bird`.
No programmatic target activation or crash-safe BGP rollback exists yet.
