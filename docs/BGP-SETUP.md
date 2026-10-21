# BGP export setup (BIRD 2.17.5)

The generated `candidate.bird.conf` defines a static protocol in `ru_restricted_table`.
It must not be connected to a Linux kernel protocol.

## Design

- Keep existing `master4` kernel import/export untouched.
- Declare `ipv4 table ru_restricted_table;` once.
- Include the generated fragment only after validation and approval.
- Attach the BGP IPv4 channel to `ru_restricted_table` with `import none;` and a restrictive export filter.
- Choose BGP `next hop` based on actual MikroTik routing topology; do not assume that the BIRD host is the forwarding gateway.
- Use a distinct local BGP session unless a current session is deliberately modified.

Example **template only** (not ready to deploy):

```bird
ipv4 table ru_restricted_table;

filter ru_restricted_export {
    if source = RTS_STATIC then accept;
    reject;
}

protocol bgp ru_restricted_peer {
    local as 64512;
    neighbor 192.0.2.2 as 64513;
    ipv4 {
        table ru_restricted_table;
        import none;
        export filter ru_restricted_export;
    };
}
```

Documentation addresses/ASNs above are placeholders. Router ID, peer reachability,
source address, multihop, next-hop and MikroTik route filters must be set for the actual network.

**Important:** BGP next-hop selection is independent of an existing IPsec tunnel.
Before deployment verify the MikroTik BGP input filter sets a resolvable next-hop
or that the BIRD-announced next-hop resolves correctly through the VPN routing design.

Never run `birdc configure` with a generated fragment until the full combined
configuration passes `bird -p -c ...` and has been reviewed.
