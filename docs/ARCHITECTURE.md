# ARCHITECTURE — the packet path

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
