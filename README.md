# mt6000-mptcp-bonding

Reproducible multi-WAN (MPTCP) bonding for the **GL.iNet GL-MT6000** (Flint 2),
stock **OpenWrt 25.12.5**, kernel **6.12.94**.

Bonds a **physical WAN port** with a **wireless STA uplink** into one MPTCP
meta-connection, proxied to a remote MPTCP-capable de-aggregation server.

Provisioning is **Ansible**. The optional mwan3 mode requires preconfigured WAN
firewall/NAT/forwarding and routing metrics; see the prerequisites below.

## Optional weighted multi-WAN (mwan3)

`uplink_mode: mptcp` remains the default. Set `uplink_mode: mwan3` for IPv4-only,
**per-flow load balancing**, not packet aggregation: each connection uses one
uplink, with no MPTCP proxy or remote de-aggregation server.

The mwan3 path runs `common` + `uplinks` + `mwan3`, skipping the MPTCP toolchain,
bond, proxy and steering configuration. Package installation supports `apk`;
`opkg` systems are explicitly skipped with a loud diagnostic, not silently
reported as supported.

Defaults assign `wan2` weight 3 and `wwan` weight 1, both at member metric 1.
Equal-metric weights give an approximate **3:1 ratio of new flows, not bytes**;
lower member metrics select preferred links and higher metrics provide failover.
Tracking defaults are `1.1.1.1` and `8.8.8.8`.

Before applying, configure the participating uplinks in the WAN firewall zone,
with NAT/masquerading and LAN-to-WAN forwarding, and give their network routes
**distinct routing metrics**. These route metrics are separate from mwan3 member
metrics. The role validates prerequisites; it does not rewrite existing WAN or
firewall configuration.

```sh
ansible-playbook -i inventory/hosts.yml playbook.yml -e uplink_mode=mwan3 --check
ansible-playbook -i inventory/hosts.yml playbook.yml -e uplink_mode=mwan3
```

**Do not switch modes in place.** Run `-e state=absent` for the current mode first,
then apply the new mode. See [MWAN3-WEIGHTS.md](docs/MWAN3-WEIGHTS.md) for the full
configuration, persistent backup/restore contract, and verification commands.

**New-mode evidence: offline-only.** No live router run happened: SSH access was
blocked by publickey-only authentication, with no usable key held. The MPTCP
measurements below do not validate mwan3 balancing, failover or teardown.

---

## Status — what is actually proven

| Capability | State | Evidence |
|---|---|---|
| Serial root console, no password | ✅ works | `root@tollgate-VIL4:~#` @115200 |
| Kernel MPTCP present & enabled | ✅ works | `net.mptcp.enabled=1`, kernel 6.12.94 |
| Wifi STA as 2nd uplink | ✅ works | `wwan` up, lease `10.108.82.91` |
| Ethernet 2nd uplink (`lan1`→wan2) | ✅ works | lease `192.168.2.16` |
| MPTCP over the proxy to the VPS | ✅ works | `tcp-ulp-mptcp`, token negotiated |
| 2nd subflow MP_JOIN (two paths) | ✅ reproducible | tcpdump: SYN join → SYN-ACK → HMAC ack |
| `subflows:2` on a live socket | ✅ observed | `ss -Min` |
| Failover mid-transfer | ⚠️ not achieved | no 2nd subflow present at kill time |
| Throughput aggregation | ⚠️ ~1.17× | 19.4 vs 16.5 Mbit/s best single path |

**Honest summary:** this is *resilient multi-path with modest aggregation*, not
"2× the speed". Both uplinks share the same remote RTT (~200 ms) and the default
scheduler prefers the lower-RTT subflow. Do not sell it as doubling.

---

## The three findings that cost the most time

### 1. `sing-box` and `mptcpize` CANNOT do MPTCP here → use `xray-core`

`mptcpize` uses `LD_PRELOAD`, which **does not work on Go static binaries**.
`sing-box` sets no MPTCP sockopt. Both silently produce plain TCP.

✅ **Use `xray-core`** with an explicit sockopt:

```json
"streamSettings": { "sockopt": { "tcpMPTCP": true } }
```

Verify, don't assume: `ss -Mti dst <server>` must show `tcp-ulp-mptcp` + a token.

### 2. Policy routing is MANDATORY for the 2nd subflow

Without `ip rule from <src> lookup <table>` each with a **default route**, the
MP_JOIN SYN for the second address egresses the *primary* uplink (lowest metric),
arrives at the remote NAT with a foreign source, and is **silently dropped**.
You then see the socket forever at `subflows_total:1`.

```sh
ip rule add from 192.168.2.16/32 lookup 200 pref 100
ip rule add from 10.108.82.91/32 lookup 201 pref 101
```

**Trap:** each per-source table also needs the **connected subnet routes**, or
router-local traffic (SSH!) breaks because return packets pick a table with only
a default route.

### 3. The tproxy rule will eat your SSH — exclude router-local addresses

A naive `ip saddr <guest-net> tproxy ip to :1082` diverts traffic **destined to
the router itself**. Symptom: `kex_exchange_identification: Connection closed`
when SSHing to the box. Always `return` first:

```
ip daddr { <router-ips> } return
ip daddr { <local-subnets> } return
ip daddr <proxy-server> return
```

Also: a second WAN that is **not a member of any firewall zone** falls through
fw4's `handle_reject`. Either zone it, or reach the box over a zoned interface.

---

## Quick start

```sh
# 1. SSH reachability (see docs/ACCESS.md for the serial bootstrap)
ansible-galaxy collection install community.general   # optional
ansible-playbook -i inventory/hosts.yml playbook.yml --check   # dry run
ansible-playbook -i inventory/hosts.yml playbook.yml           # apply

# 2. Verify the bond
ssh vil4 'ss -Min | grep -A1 ESTAB'         # expect subflows:2
ssh vil4 'ip mptcp endpoint show'           # expect BOTH uplinks
```

Run it twice — the second run must report `changed=0` (idempotence is a test,
see `tests/idempotence.sh`).

## Layout

```
playbook.yml              # the whole bond, in order
inventory/hosts.yml       # vil4 via ProxyJump cobrador
group_vars/all.yml        # ← EDIT THIS: ssids, passwords, server, subnets
roles/
  common/                 # packages + sysctl (mptcp limits)
  uplinks/                # wifi STA + ethernet wan2 + per-source policy routing
  bond/                   # mptcp endpoints + bsbf-mptcp daemon fix
  proxy/                  # xray-core with tcpMPTCP -> remote ss-server
  steering/               # nft TPROXY so LAN clients use the bond
scripts/serial-console.py # no-SSH fallback (serial console; sets up SSH)
docs/
  LEARNINGS.md            # every gotcha, with the symptom that reveals it
  ACCESS.md               # serial bootstrap -> SSH
  ARCHITECTURE.md         # packet path, tables, rules
  TOLLGATE-INTEGRATION.md # how this plugs into TollGate
  MEASUREMENTS.md         # raw throughput runs + method
```

## Safety

- The playbook **does not change the WAN port**. Your existing uplink stays the
  primary route; bonding is additive.
- `roles/proxy` binds to `127.0.0.1` for SOCKS and `:1082` for TPROXY. It is
  **not** an open relay.
- Reverting: `ansible-playbook -i inventory/hosts.yml playbook.yml -e state=absent`
  removes the bond config and returns the box to a plain router.

## License

MIT (this repo). `bsbf-mptcp` is a separate GPL-2.0-only project — it is
**invoked**, never vendored. `xray-core` is MPL-2.0. See `docs/LEARNINGS.md`.

## Shell access (IaC)

`/etc/dropbear/authorized_keys` is configuration, not a manual step: the key list
lives in `group_vars/all.yml` (`mt6000_authorized_keys`) and is reconciled by
`roles/access`, idempotently and public-keys-only.

```sh
ansible-playbook -i inventory/hosts.yml playbook-access.yml        # access only
ansible-playbook -i inventory/hosts.yml playbook.yml --syntax-check # full run
```

Target selection is environment-driven, so the production box and a bench unit
share one tracked inventory:

```sh
MT6000_HOST=vil4 ansible-playbook -i inventory/hosts.yml playbook-access.yml
MT6000_HOST=192.168.50.1 MT6000_KEY=~/.ssh/id_ed25519 \
  ansible-playbook -i inventory/hosts.yml playbook-access.yml
```

Re-running is a no-op (`changed=0`) once the keys are present — measured on the
bench, `ok=5 changed=0`, two keys installed.
