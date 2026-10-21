# Candidate review (read-only)

This stage compares the approved baseline in `current.txt` with the fetched
`candidate.txt` and its canonical BIRD fragment. It also checks live BIRD
table and BGP exported route counts. BGP must be Established.

Default limits: at most 10% removed prefixes and 20% total changed prefixes,
measured against the approved baseline. This is a safety gate, not a guarantee
that the upstream feed is trustworthy.

Run tests and review manually:

```bash
cd /opt/ru-restricted-routes
git pull --ff-only
python3 -m unittest discover -s tests -v
python3 -m ru_routes.review
```

Expected when unchanged: `approved: true`, `added: 0`, `removed: 0`.
Exit status 2 indicates rejection; missing or inconsistent files cause an error.

Optional one-shot service (no timer, no BIRD changes):

```bash
install -m 644 systemd/ru-restricted-review.service /etc/systemd/system/
systemctl daemon-reload
systemctl start ru-restricted-review.service
journalctl -u ru-restricted-review.service -n 30 --no-pager
```

**Do not** enable automatic application yet. The review is read-only and does
not serialize against concurrent fetch; the later apply stage must recheck a
stable candidate under a lock immediately before making changes.
