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
