# Steering a client to one uplink: what actually happens on OMR

Measured 2026-10-26 on the bench GL-MT6000, OpenMPTCProuter v0.64.2-6.18,
kernel 6.18.39. This note exists because the obvious implementation **looks
like it works and does not**, and the failure is silent.

## What was tried

Per-client policy routing, the textbook way: one rule per client, in a
preference band the role owns.

```text
$ ansible-playbook -i inventory/hosts.yml playbook-client-tables.yml \
    -e '{"client_tables_clients":[{"ip":"192.168.50.180","objective":"minimize_cost"}]}'
$ ip rule list | awk '$1 ~ /^120/'
12000:	from 192.168.50.180 lookup 11
12100:	from 192.168.50.180 to 192.168.50.0/24 lookup lan
```

Uplink tables on this box: `wan3` → 11, `wwan` → 13 (from
`uci network.<iface>.ip4table`). Both have a default route. The rules installed,
a second run reported `changed=0`, and `state=absent` removed them cleanly.

## Why it does nothing

```text
$ ip route get 1.1.1.1 from 192.168.50.180
RTNETLINK answers: Network unreachable
```

and 20 MB pulled from that client moved over the wireless uplink, not the wired
one the rule named:

```text
lan3  rx +117,622 B
phy1  rx +21,956,622 B      <- ~21 MB over Wi-Fi
```

The reason is OMR's own steering: LAN client TCP is TPROXY-marked in nft
prerouting (`meta mark set 0x1`, then `tproxy ip to :tproxy_port`), and OMR
installs

```text
0:	from all fwmark 0x1 lookup 100
```

at **preference 0**. Routing therefore resolves the fwmark rule before any
`from <client>` rule can be consulted, and the packet is delivered locally to
the transparent proxy instead of being forwarded out an uplink table. A
source-based rule at pref 12000 is unreachable by construction — not
misconfigured, not slow, just downstream of a higher-priority verdict.

## The consequence that matters for TollGate

On OMR, the bond path is also where accounting lives: TPROXY → bond → the
TollGate session that meters and bills. Pinning a client to a raw uplink table
bypasses that, so a "minimize cost" client would egress **unmetered**. Any buyer
steering on OMR must therefore pick an uplink **without leaving the accounted
path**, or the price the buyer sees stops matching what they consume.

## What the correct implementation must do

Set the packet's mark to the *uplink's own* OMR mark (`0x4539xx`, the scheme OMR
already routes: `0x453911` → table 11, `0x453913` → table 13) in a chain that
runs **before** the TPROXY chain, with a final `accept` for that packet. Two
consequences to decide first, because they are product decisions, not code:

1. Does a steered client still get metered? If the mark bypasses the proxy, it
   does not — unless the meter sits on the forwarding path rather than in the
   proxy.
2. Does "balanced" on OMR mean "the bond" (all uplinks, kernel-scheduled)? It
   must, because the kernel exposes no weight knob.

Until those are answered, this repository ships **no** per-client steering. The
`pr/client-tables` branch keeps the role and its nine offline tests as the basis
for the mark-based version; it is deliberately unmerged.

## Second attempt: set the mark early (also insufficient)

A mark-based variant was built and run: an `inet` chain at `priority mangle - 20`
(-170, ahead of OMR's `-160`) setting the uplink's own OMR mark
(`0x453911` for table 11) with `accept`, plus a `counter`. Rendered fragment is
derived from `uci network.<iface>.ip4table` and validated with `nft -c`.

It installs, self-verifies, and reverts cleanly, and the counter proves the rule
**does** match client traffic:

```text
nft list table inet client_egress
  ip saddr 192.168.50.180 counter packets 849 bytes 65032 meta mark set 0x00453911 accept
```

But the traffic still did not follow the mark. A 20 MB download from that client
moved:

```text
lan3 rx +355,752 B        <- the uplink the mark named (table 11)
phy1 rx +30,296,125 B     <- the uplink that carried it
counter +67 packets       <- but a 20 MB transfer is ~14k packets
```

So the rule sees only a sliver of the client's packets (the rest are handled
before it, or re-marked downstream), and egress stays on the bond. Marking at
prerouting is therefore **not** sufficient either. Establishing the real
mechanism needs `nft ... trace` on a live flow, which is a deliberate
instrumented session, not a guess.

## Status

No per-client steering ships. The two roles (`pr/client-tables`, and the
mark-based `pr/client-egress`) are kept unmerged with their tests; this file is
the record of what was measured.
