# mwan3 weighted multi-WAN (optional, IPv4-only)

## Scope and evidence

`uplink_mode: mptcp` is the default; `uplink_mode: mwan3` selects weighted
per-flow load balancing and failover. This is **not packet aggregation** and does
not combine both uplinks' bandwidth for a single connection. It needs no remote
MPTCP server.

### mwan3 is not installable on OpenMPTCProuter

Measured on the bench GL-MT6000 running OpenMPTCProuter v0.64.2-6.18
(kernel 6.18.39, `apk` era):

```text
$ apk add mwan3
ERROR: unable to select packages:
  iptables-mod-conntrack-extra (no such package):
    required by: mwan3-2.12.2-r1[iptables-mod-conntrack-extra]
$ apk search -x iptables-mod-conntrack-extra iptables-mod-ipset
(no results)
$ apk info -e iptables
iptables-nft                       # nft-backed iptables; no legacy modules
```

OMR ships `iptables-nft` only; `mwan3` needs `iptables-mod-conntrack-extra` from
the legacy module set, which is absent from the OMR package feed. `uplink_mode:
mwan3` therefore cannot run on an OMR image — on stock OpenWrt it can.

The playbook now fails fast instead of half-deploying: the task *Fail fast when
the package repo cannot satisfy mwan3* runs `apk add --simulate mwan3` before any
configuration and stops with the dependency named:

```text
mt6000 | FAILED | rc=2 >>
ERROR: unable to select packages:
  iptables-mod-conntrack-extra (no such package):
    required by: mwan3-2.12.2-r1[iptables-mod-conntrack-extra]
```

Consequence for TollGate buyer-side steering: on OMR the objective can only be
expressed as **per-client routing-table selection** (cheapest uplink / fastest
uplink / the bond), not as weights. Weights remain available on stock-OpenWrt
firmware only.


**Status: offline-only for this new mode.** No live router run happened. SSH
access was blocked by publickey-only authentication and no usable key was held.
The repository's existing MPTCP measurements are not mwan3 evidence. The commands
below are a deployment/verification procedure, not recorded successful runs.

The mwan3 path runs `common` + `uplinks` + `mwan3`; it skips the MPTCP toolchain,
bond setup, proxy and steering. `apk` is supported. An `opkg` target receives a
loud skip diagnostic: this is not a claim of opkg installation support.

## Prerequisites: configure before applying

- Establish working SSH access and retain a separate management/recovery path.
- Configure working IPv4 uplinks, including the wireless PSK if using `wwan`.
- Put participating uplinks in the WAN firewall zone with NAT/masquerading and
  LAN-to-WAN forwarding. An interface outside the correct zone can be rejected
  even if its route and mwan3 status look healthy.
- Give participating network interfaces **distinct network routing metrics**.
  These are the metrics of their routes, not the mwan3 member metrics below.

The role validates these prerequisites; it does **not** mutate existing WAN or
firewall configuration to repair them. Fix a failed prerequisite deliberately
before retrying. This mode is IPv4-only; it makes no IPv6 balancing or leak-control
promise.

## Default policy and weights

| Interface | Generated member name | Member metric | Weight |
|---|---|---|---|
| `wan2` | `wan2_member` | 1 | 3 |
| `wwan` | `wwan_member` | 1 | 1 |

Member names are generated as `<iface>_member`. Policies refer to interface names
(`wan2`, `wwan`), which resolve to those generated members. Default tracking
addresses are `1.1.1.1` and `8.8.8.8`.

The default policy and rule records are:

```yaml
mwan3_policies:
  - name: balanced
    members: [wan2, wwan]
    last_resort: unreachable
mwan3_rules:
  - name: default_ipv4
    proto: all
    dest_ip: 0.0.0.0/0
    use_policy: balanced
```

Configure these collections in `group_vars/all.yml`; rules also accept an optional
`src_ip` IPv4 address/CIDR (default `0.0.0.0/0`).

mwan3 first selects available members with the **lowest member metric**. Only
within that equal-metric group do weights divide new flows. Thus metric 1 on both
members and weights 3:1 target roughly 75% of new flows through `wan2` and 25%
through `wwan`. This is statistical, not an exact count or a byte ratio: a few
large downloads can dominate bytes on either link. A single download does not
become three parts Ethernet and one part Wi-Fi.

For preferred-link failover, give the preferred member a lower member metric and
the backup a higher one. Weight does not override metric priority. Do not confuse
this with the distinct network routing metrics required above. When no policy
member remains usable, `last_resort: unreachable` rejects matching traffic rather
than silently falling back to an unrelated route. Failover of new flows does not
promise survival of existing NATed connections after an uplink failure.

## Apply, switch and restore

For a clean deployment, after satisfying prerequisites:

```sh
ansible-playbook -i inventory/hosts.yml playbook.yml -e uplink_mode=mwan3 --check
ansible-playbook -i inventory/hosts.yml playbook.yml -e uplink_mode=mwan3
```

`--check` is not proof of working router forwarding. Switching modes requires an
**explicit teardown first**; changing only `uplink_mode` is not a migration.
For example, MPTCP to mwan3:

```sh
ansible-playbook -i inventory/hosts.yml playbook.yml -e uplink_mode=mptcp -e state=absent
ansible-playbook -i inventory/hosts.yml playbook.yml -e uplink_mode=mwan3
```

For mwan3 to MPTCP, tear down with `uplink_mode=mwan3` and `state=absent`, then
apply `uplink_mode=mptcp` with the required MPTCP credentials and configuration.
Do not leave the previous mode's policy routing, proxy or steering active.

Before replacing `/etc/config/mwan3`, the original config is preserved at
`/etc/mt6000-mwan3-backup/config`. If no original config existed, the
`/etc/mt6000-mwan3-backup/config.absent` sentinel records that fact. This backup
state persists across repeat applies; it must not be replaced by a backup of the
already-managed config. Keep it until teardown restores the original config (or
restores absence for the sentinel case), then removes the backup state. Do not
manually delete the backup to force a mode switch.

## Verify on the router (not yet executed here)

```sh
mwan3 status
mwan3 interfaces
mwan3 policies
mwan3 use wan2 ping -c 3 1.1.1.1
mwan3 use wwan ping -c 3 8.8.8.8
ip -4 route show
ip -4 rule show
```

`mwan3 use <iface> <command>` runs a command through the selected interface; it
is not a counters command. Inspect the interface/policy status and counters
exposed by `mwan3 interfaces` and `mwan3 policies`, and inspect the installed
firewall/set backend as applicable:

```sh
nft list ruleset     # where nftables is installed/in use
ipset list          # where ipset is installed/in use
```

There is no assumed standalone `mwan3 counters` command. Backend details depend
on the installed packages; do not treat a missing backend utility as evidence of
successful balancing.

Generate many independent IPv4 connections from a LAN client and compare flow
counts before/after; a single speed test or byte count cannot verify a 3:1 flow
ratio. Verify both interfaces online, each interface-bound probe succeeds, and
LAN forwarding/NAT works. Then test loss and recovery of each uplink while keeping
management access independent. Check new-flow failover, policy exhaustion, and
repeat-apply idempotence. Finally test teardown restores the original config or
absence and removes backup state. All of these live checks remain unverified.
