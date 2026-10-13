# TOLLGATE INTEGRATION — how bonding plugs in

Target: [`OpenTollGate/tollgate-module-basic-go`](https://github.com/OpenTollGate/tollgate-module-basic-go)

## The core insight: bonding is *just the uplink*

TollGate's job is: run a captive portal, take payment (Cashu), and gate internet
access. Its **uplink** is whatever interface currently has a default route.

Multi-WAN bonding does not change any of that. It replaces *one* uplink with a
bonded transport, and it does so **below** the portal:

```
   guest ─► nodogsplash portal ─► payment gate ─► [ forwarding ]
                                                       │
                                          ┌────────────▼─────────────┐
                                          │  MPTCP bonded uplink     │  ← only this changes
                                          │  subflow A + subflow B   │
                                          └──────────────────────────┘
```

**Consequence:** merchant mode, portal, payment verification, and session
accounting are all untouched. That is what makes this integration tractable.

## What the integration actually costs

### 1. Firewall / rules collision — the hard part

TollGate already manipulates `fw4`, NAT, and transparent redirects. The bonding
layer needs:

- its own `nft` table (`inet mptcpbond`) with a `prerouting` hook at
  `priority mangle - 10`;
- `ip rule` entries at **pref 90 / 100 / 101**;
- its own routing tables (100/200/201).

**Observed during development:** a naive `redirect to :<port>` on the router's
own traffic looped the proxy into itself (an infinite redirect). The working
form is **TPROXY with an explicit mark**, plus `return` rules for router-local
destinations — otherwise the portal's own traffic (and SSH) gets swallowed.

**Recommendation:** keep the bond rules in a **separate table** from TollGate's,
use the `mangle - 10` hook so both can coexist, and never `flush` the whole
ruleset. Give the bond a **dedicated mark bit** and a dedicated table range.

### 2. Which traffic goes over the bond?

Two options:

- **(a) Everything.** Simplest: change the default route to the bonded path.
  Portal, payments and guests all benefit; the blast radius is the whole box.
- **(b) Only guest egress.** Leave the router's own management traffic on the
  primary WAN and steer only `forward`-chain traffic into the bond. Safer for
  a paid device — a bond misconfiguration cannot lock you out.

**Recommendation: (b) for the first release**, (a) as an opt-in.

### 3. Portal redirect must not fight TPROXY

TollGate redirects *unauthenticated* guests to the portal. The bond wants to
TPROXY *authenticated* traffic. Both hook `prerouting`.

Ordering rule: **portal decision first, then bond steer.** Mark traffic as
"authenticated" in the portal chain and let the bond table act only on the
marked set — or exclude the portal's own port so its redirect is never
diverted.

### 4. Payment accounting across subflows

`step_size`-based billing (e.g. charge per 10 MB) assumes **one** flow per
session. On a bond, bytes arrive split across subflows that the far side
reassembles.

- Price on **application-level accounting** (TollGate's own counter), not on
  per-interface byte counters.
- Reassembled MPTCP bytes equal application bytes, so the counter is correct
  **if** it is measured above the bond. Do not sum per-uplink counters — you
  will double-count retransmissions and can bill a customer twice.
- An indivisible `step_size` interacts badly with a mid-session failover: a
  step can straddle the failure. Prefer a time- or byte-continuous model, or
  accept that a step boundary may cost one step.

### 5. Licensing

- `tollgate-module-basic-go` is **GPL-3.0**.
- `bondingshouldbefree`'s OpenWrt stack is **GPL-2.0-only** — **incompatible**.
  Interoperate (invoke the packaged binary), never copy its source into
  TollGate.
- `xray-core` is **MPL-2.0** — fine to call.
- The kernel MPTCP implementation and `iproute2` MPTCP support are upstream;
  use those freely.

## Suggested shape of the change

```
tollgate-module-basic-go/
  src/mptcp_bonding/           # NEW — thin layer
    config.go                  # read bond settings (server, subflows, steering set)
    endpoints.go               # ensure subflow endpoints (the keeper)
    steer.go                   # nft table + ip rules; opt-in, default off
  packaging/files/etc/
    init.d/mptcp-bond          # from this repo, parameterised
    nftables.d/30-mptcp-steering.nft
```

Config schema (fits the existing `feat/mptcp-bonding` direction):

```jsonc
{
  "mptcp_bonding": {
    "enabled": false,              // default OFF — opt-in
    "policy": "guest_only",        // "guest_only" | "all"
    "server": "host:port",         // the far-end de-aggregation point
    "method": "chacha20-ietf-poly1305",
    "uplinks": ["wan", "wwan"],    // interface names
    "subflows": 8
  }
}
```

**Default `enabled: false`.** A paid device must not silently change its
connectivity model.

## Honest assessment of what this buys TollGate

| Benefit | Real? |
|---|---|
| Survives one uplink failing | **Yes** — once a 2nd subflow is live (failover itself is not yet proven, see MEASUREMENTS) |
| More bandwidth | **Marginal, ~1.2–1.3×** — do not market as "2×" |
| Live uplink migration (wifi→5G) without dropping sessions | **Plausible and valuable** — the strongest actual argument for a roaming merchant device |
| Complexity cost | **High** — nftables/routing coexistence with the portal |

**The honest pitch:** bonding is worth it for **resilience and session
continuity on a moving merchant box**, not for throughput.

## What was and was not demonstrated

- ✅ The bond itself: two uplinks, MP_JOIN proven by packet capture,
  `subflows:2` on a live socket, MPTCP verified end-to-end to a remote
  de-aggregation server.
- ✅ LAN-client steering into the bonded path (nft TPROXY; counter advances).
- ⚠️ **A running TollGate guest-buys-access-over-the-bond demo was NOT
  completed.** The pieces exist and the integration path above is the plan;
  the end-to-end portal-through-bond test is outstanding work.
