# Staged feed validation on Debian / BIRD 2.17.5

The upstream [ru-direct](https://github.com/kyoresuas/ru-direct) publishes
`ru-standard.ipv4.txt` on its `release` branch. This is a broad RU-service
routing list, **not** a proof that each destination is blocked abroad.

```bash
cd /opt/ru-restricted-routes
git pull --ff-only
python3 -m unittest discover -s tests -v
python3 -m ru_routes.update --state-dir /tmp/ru-restricted-review
cat /tmp/ru-restricted-review/candidate.json
head -25 /tmp/ru-restricted-review/candidate.txt
```

No BIRD config is modified. Generated `candidate.bird.conf` is intentionally
**not** included automatically. It needs an isolated `ipv4 table
ru_restricted_table;` declaration and export-only BGP configuration, to be
reviewed for the real topology. In particular, a blackhole static route must
never be imported to the Linux kernel or used as the MikroTik forwarding
next-hop.

Review prefixes before enabling. This script deliberately leaves promotion
to `current.txt` manual and never executes `birdc configure`. The update
guard compares against `current.txt` only if an approved baseline exists.
Feed download is limited to HTTPS, 2 MB and a 25-second timeout.

Do not paste secrets or credentials in issue reports.
