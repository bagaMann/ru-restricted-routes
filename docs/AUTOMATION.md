# Automation: staged rollout

The daily timer **only downloads and validates candidates**. It never changes
`/etc/bird/bird.conf`, active routes, or BGP. This is intentional until
feed trust, change controls, and rollback have been validated in production.

## Install candidate-only timer

```bash
cd /opt/ru-restricted-routes
git pull --ff-only
python3 -m unittest discover -s tests -v
install -d -m 755 /var/lib/ru-restricted-routes
install -m 644 systemd/ru-restricted-fetch.service /etc/systemd/system/
install -m 644 systemd/ru-restricted-fetch.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now ru-restricted-fetch.timer
systemctl start ru-restricted-fetch.service
systemctl status ru-restricted-fetch.service --no-pager
journalctl -u ru-restricted-fetch.service -n 30 --no-pager
cat /var/lib/ru-restricted-routes/candidate.json
```

If the upstream list has grown beyond the configured 2500 prefix limit,
the service fails closed; raise the limit only after review.

## Manual review and apply

```bash
python3 -m ru_routes.apply \
  --candidate /var/lib/ru-restricted-routes/candidate.bird.conf
# DRY RUN only. Review candidate.txt, candidate.json and prefix changes first.
python3 -m ru_routes.apply \
  --candidate /var/lib/ru-restricted-routes/candidate.bird.conf --confirm
birdc show protocols all ru_to_mikrotik
birdc show route table ru_restricted_table count
ip -4 route
```

`--confirm` is deliberately required. The tool makes a rollback copy at
`/var/lib/ru-restricted-routes/previous-routes.conf`. To roll back, validate
that backup and explicitly apply it as the candidate. BIRD reconfiguration
success alone does not guarantee that every BGP update was accepted by the peer.

**Important:** Current `update.py` compares removals against
`/var/lib/ru-restricted-routes/current.txt` only if that file exists.
Until an approved baseline is maintained, this removal guard does not provide
meaningful protection. The candidate-only timer remains safe from automatic
routing changes regardless.

**Do not** configure a timer to run `apply.py --confirm` yet.

## Verified manual promotion (new)

The apply command now waits for both the actual BIRD table count and the
BGP export count to equal the candidate prefix count, while BGP remains
Established. If validation fails within 30 seconds, it restores the previous
route fragment, reconfigures BIRD and verifies rollback. If rollback itself
cannot be verified, the command fails loudly and needs operator intervention.

After reviewing candidate.txt and candidate.json, apply and promote the
approved baseline together:

```bash
python3 -m ru_routes.apply --candidate /var/lib/ru-restricted-routes/candidate.bird.conf --confirm --promote-current
```

The candidate.txt must match the generated BIRD fragment exactly. current.txt
is updated only after the BIRD state has been verified, including the no-change
case. This validates the BIRD side, not installation or forwarding on MikroTik.

**Automatic application is still disabled.** The daily systemd timer remains
fetch-only. Do not add --confirm to the existing fetch service.
