# ARCHITECTURE — the packet path

## Mode comparison

`uplink_mode: mptcp` remains the default. The alternative `uplink_mode: mwan3`
is IPv4-only weighted **per-flow** routing, not packet aggregation.

| Property | `mptcp` (default) | `mwan3` (optional) |
|---|---|---|
| Role path | `common` + `uplinks` + `bond` + `proxy` + `steering` | `common` + `uplinks` + `mwan3` |
| Traffic path | TPROXY → xray → MPTCP subflows → remote server | mwan3 policy → one uplink per flow → WAN NAT → destination |
| Remote endpoint | MPTCP de-aggregation server required | No aggregation server required |
| Single connection | Can use multiple MPTCP subflows | Uses one uplink; no combined-link throughput |
| Link selection | MPTCP scheduler and endpoints | Lowest available member metric, then weights among equal metrics |
| Toolchain | MPTCP tools, proxy and steering | Skips MPTCP toolchain, proxy and steering; `apk` supported, `opkg` loudly skipped |
| Evidence | Historical MPTCP observations below and in `MEASUREMENTS.md` | Offline-only; no live router validation |

The mwan3 defaults are `wan2_member` (interface `wan2`, metric 1, weight 3) and
`wwan_member` (interface `wwan`, metric 1, weight 1), with tracking addresses
`1.1.1.1` and `8.8.8.8`. Names are generated as `<iface>_member`. The `balanced`
policy references `[wan2, wwan]` with `last_resort: unreachable`; the
`default_ipv4` rule matches `proto: all`, `dest_ip: 0.0.0.0/0` and uses `balanced`.
Equal member metrics yield an approximate 3:1 **flow ratio, not byte ratio**.
Lower member metrics provide preference/failover, independent of weights.

```
LAN client IPv4 flow
  → mwan3 rule (default_ipv4)
  → balanced policy (lowest available member metric)
  → weighted choice: wan2 (3) OR wwan (1)
  → preconfigured WAN forwarding + NAT
  → destination (no proxy, no de-aggregation server)
```

Participating interfaces must already have distinct **network routing metrics**
and belong to the WAN firewall zone with NAT/masquerading and LAN-to-WAN
forwarding. The role validates these prerequisites instead of mutating existing
WAN/firewall configuration. Equal mwan3 member metrics do not mean equal network
route metrics. The historical MPTCP note below about unzoned `wan2` is **not** a
valid mwan3 deployment configuration.

Switching modes requires explicit `state=absent` for the current mode before
applying the other. The original mwan3 config is retained at
`/etc/mt6000-mwan3-backup/config`, or its original absence is recorded by
`config.absent` in the same directory. Backup state persists until teardown
restores the original state, then removes it.

See [MWAN3-WEIGHTS.md](MWAN3-WEIGHTS.md) for configuration and verification.
No live mwan3 router run happened: publickey-only SSH authentication blocked
access and no usable key was held. Balancing, failover and restore remain
unverified on hardware.

## Default MPTCP packet path

The diagram, routing tables and measurements below describe **MPTCP mode only**;
they are not installed by the mwan3 path.

```
                 ┌──────────────────── GL-MT6000 (OpenWrt 25.12.5) ───────────────────┐
                 │                                                                     │
  LAN clients ──►│ br-lan ──► nft prerouting (inet mptcpbond)                          │
  (steering      │              │                                                      │
   subnet)       │              ├─ daddr == router    → return (protects SSH)          │
                 │              ├─ daddr == far end    → return                        │
                 │              └─ saddr == clients    → mark 0x1 + TPROXY :1082       │
                 │                                              │                      │
                 │                                     ┌────────▼────────┐             │
                 │                                     │ xray  tproxy-in │             │
                 │                                     │  (dokodemo)     │             │
                 │                                     └────────┬────────┘             │
                 │                                              │ shadowsocks          │
                 │                                     sockopt.tcpMPTCP = true         │
                 │                                              │                      │
                 │   ┌──────────────────────────────────────────▼──────────────────┐   │
                 │   │ kernel MPTCP meta-socket   (net.mptcp.enabled=1)             │   │
                 │   │  subflow 1: 10.108.82.91  via phy1-sta0  (wifi STA)          │   │
                 │   │  subflow 2: 192.168.2.16  via lan1       (ethernet)          │   │
                 │   └───────┬───────────────────────────────────────┬──────────────┘   │
                 │           │                                       │                  │
                 │      phy1-sta0                                 lan1                   │
                 │        (wwan)                                (wan2)                   │
                 │           │                                       │                  │
                 │     ┌─────▼─────┐                          ┌──────▼──────┐           │
                 │     │ wifi AP   │                          │ house LAN   │           │
                 │     │ (mobile)  │                          │  switch     │           │
                 │     └─────┬─────┘                          └──────┬──────┘           │
                 └───────────┼────────────────────────────────────────┼─────────────────┘
                             │                                        │
                        internet                                 internet
                             └───────────────┬────────────────────────┘
                                             ▼
                              ┌──────────────────────────────┐
                              │ MPTCP de-aggregation server  │
                              │  ss-server --mptcp :65101    │
                              │  (rejoins both subflows)     │
                              └──────────────┬───────────────┘
                                             ▼
                                        destination
```

## Routing tables

| Table | Purpose | Contents |
|---|---|---|
| `main` | normal | `default` via each uplink, **both with a metric** (10 = wifi, 20 = ethernet) |
| `100` | steering + TPROXY delivery | `local default dev lo`, `default` via wan2, connected routes |
| `200` | subflow 2 (ethernet) | `default` via wan2 gw **+ connected routes** |
| `201` | subflow 1 (wifi) | `default` via wwan gw **+ connected routes** |

## Rules (order matters)

```
90:  from all fwmark 0x1/0x1 lookup 100     ← TPROXY delivery (must precede the rest)
100: from <wan2-ip>  lookup 200             ← ethernet subflow
101: from <wwan-ip>  lookup 201             ← wifi subflow
```

Why each one exists:

- **90** — the kernel marks TPROXY'd packets; they must be delivered to the
  **local** socket. Without it the packet is routed onward and the proxy never
  sees it.
- **100 / 101** — see `LEARNINGS.md` §2. Without these the second subflow's
  MP_JOIN SYN leaves on the wrong uplink and is dropped by the far NAT.
  **Each table also needs its connected route** (§3), or router-local traffic
  (SSH) breaks.

## nftables

```
table inet mptcpbond {
  chain prerouting {                      # hook prerouting, priority mangle - 10
    ip daddr { <router ips>, <subnets> } return     # never divert the box itself
    ip daddr <far-end>                   return     # never divert the tunnel
    ip saddr <client subnet> meta l4proto tcp \
      meta mark set 0x1 tproxy ip to :1082 counter accept
  }
}
```

Two pitfalls encoded here, both in `LEARNINGS.md`:
- without the `return` lines, the proxy **eats SSH to the router** (§5);
- in an `inet` table the statement must be `tproxy ip to :PORT` (§5).

## Endpoint keeping

`bsbf-mptcp` (packaged upstream) is started, but a small keeper loop
(`/usr/sbin/mptcp-endpoint-keeper`) is what actually guarantees both subflow
endpoints exist — the daemon prunes one on this image (§6).

## What is NOT automated yet

- Mid-transfer **failover** has not been achieved reproducibly (no second
  subflow guaranteed at the moment of failure).
- **Aggregation is ~1.17×**, not 2× — see `MEASUREMENTS.md`.
- Reaching the box over `wan2` directly (it is deliberately left out of
  firewall zones; manage via the LAN side). See `LEARNINGS.md` §4.
