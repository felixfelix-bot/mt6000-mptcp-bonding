# LEARNINGS — every gotcha, ranked by how long it cost

Written from a real session on **GL-MT6000 / OpenWrt 25.12.5 / kernel 6.12.94**
(hostname `tollgate-VIL4`). Each entry: **symptom → cause → fix**.

---

## 1. `mptcpize` and `sing-box` silently produce plain TCP

**Symptom.** `mptcpize run sing-box …` starts fine, throughput is normal, but
`ss -Mti dst <server>` shows **no** `tcp-ulp-mptcp` and no token. `ps` does not
even show a `mptcpize` parent.

**Cause.** `mptcpize` works by `LD_PRELOAD`-ing `libmptcpwrap.so` and
intercepting `socket()`. **Go binaries are statically linked** and make raw
syscalls — the preload is invisible to them. `sing-box` is Go, so it always
opened plain TCP.

**Fix.** Use a proxy that sets the sockopt itself: `xray-core`
(`streamSettings.sockopt.tcpMPTCP = true`).

**Rule.** Never trust "it started". Prove it with
`ss -Mti dst <server> | grep tcp-ulp-mptcp`.

---

## 2. The 2nd subflow never joins — the SYN is dropped by the far NAT

**Symptom.** Socket is MPTCP and healthy, `subflows_total:1` forever, even though
`ip mptcp endpoint show` lists two addresses. `local_addr_max:2` but
`local_addr_used:1`.

**Cause.** The MP_JOIN SYN for address B is routed by the **main table**, whose
lowest-metric default points at uplink A. The packet leaves with source B's
address on uplink A; the remote NAT has no state for it and drops it. You see the
SYN in `tcpdump` on the wrong interface and **no SYN-ACK ever returns**.

**Fix.** Per-source policy routing, each table carrying its **own default**:

```sh
ip route replace default via <gwB> dev <ifB> table 200
ip rule add from <addrB>/32 lookup 200 pref 100
```

**Proof it worked** (this is the money capture):

```
lan1  Out 192.168.2.16.37971 > <server>: TCP [S] ... mptcp 12 join id 2 token 0x7f3ed484
lan1  In  <server> > 192.168.2.16.37971: TCP [S.] ... mptcp 16 join id 0 hmac 0xc32235c8...
lan1  Out 192.168.2.16.37971 > <server>: TCP [.] ... mptcp 24 join hmac 0x16757b0d...
```

Verify with:
```sh
tcpdump -i <ifB> -n 'tcp port <serverport> and tcp[tcpflags] & (tcp-syn|tcp-ack) != 0'
```

---

## 3. Per-source tables break router-local traffic (SSH dies)

**Symptom.** After adding the rules from §2, SSH to the router hangs, or the
client gets `Connection refused`. A SYN for :22 is seen arriving, but the reply
routing fails.

**Cause.** Rule `from <addrB> lookup 200` now catches **all** traffic sourced
from addrB — including the router's own replies to inbound SSH. Table 200 has
only a default route, so the reply is pushed out an uplink and lost. (A table
with a default but no connected route also cannot resolve the neighbour.)

**Fix.** Put the **connected routes** in each per-source table:

```sh
ip route add <wan2-subnet>/24 dev <ifB> proto kernel scope link src <addrB> table 200
ip route add <wwan-subnet>/24 dev <ifA> proto kernel scope link src <addrA> table 200
```

---

## 4. A second WAN outside every firewall zone falls through to REJECT

**Symptom.** A brand-new WAN interface gets a DHCP lease and can *send*, but
nothing can reach the router over it; clients get RST, not a timeout. Packets
**do** arrive (`tcpdump` on the interface shows the SYN) and are answered with
RST.

**Cause.** fw4's input chain ends with `jump handle_reject`. A network that is in
no zone is not matched by any `iifname … jump input_<zone>` rule, so it hits
REJECT. OpenWrt's `uci` on this image also **silently ignored** attempts to add
the network to a zone (`uci add_list firewall.@zone[0].network='wan2'` produced
no change after commit).

**Fix.** Either add a dedicated zone, or — the reliable path — reach the router
over an interface that *is* zoned (here, `br-lan`). Prefer the zoned path for
management; treat the 2nd WAN as data-only.

---

## 5. TPROXY will swallow SSH to the router

**Symptom.** `ssh <router>` → `kex_exchange_identification: Connection closed by
remote host`. The nft counter for the tproxy rule keeps climbing.

**Cause.** `ip saddr <guest-net> … tproxy ip to :1082` also matches traffic
**destined to the router's own addresses** in that subnet. The SSH packets get
diverted into the proxy instead of the local stack.

**Fix.** `return` before diverting:

```
ip daddr { <router-ips> }   return
ip daddr { <local-subnets> } return
ip daddr <proxy-server>      return
ip saddr <guest-net> meta l4proto tcp meta mark set 0x1 tproxy ip to :1082 accept
```

**Gotcha inside the gotcha:** in an `inet` family table the TPROXY statement must
name its family — `tproxy ip to :1082`, not `tproxy to :1082`, or nft errors with
`conflicting protocols specified: ip vs. unknown`.

---

## 6. `bsbf-mptcp` (the OpenWrt package) fails silently twice

It **is** packaged upstream — `apk add bsbf-mptcp`. Two defects found:

**(a) Missing state dir.** Every tick it logs:
`grep: /etc/bsbf/bsbf-mptcp-subflow-backup: No such file or directory` and
manages nothing. Fix: `mkdir -p /etc/bsbf; touch /etc/bsbf/bsbf-mptcp-subflow-backup`.

**(b) It deletes one endpoint.** Its per-interface connectivity check prunes the
endpoint on the interface it cannot probe (here `lan1`), leaving one address and
`subflows_total:1`. Its route filter also expects **every** default route to
carry a `metric`; a route created by `proto static` without one is ignored.

**Consequence.** Do not depend on the daemon alone. Keep a deterministic keeper:

```sh
while true; do
  ip mptcp endpoint add <addrA> dev <ifA> subflow 2>/dev/null
  ip mptcp endpoint add <addrB> dev <ifB> subflow 2>/dev/null
  sleep 2
done
```

---

## 7. `ss` output is version-dependent — read the right row

`subflows_total` appears on the **meta** row and can read `1` even when a second
subflow exists for a moment, and `subflows:2` appears on some kernels instead.
Sampling at the wrong instant (or after the transfer ended) gives a false
"not bonded". Capture with `tcpdump` for the JOIN, and sample `ss -Min` **during**
a rate-limited transfer (`curl --limit-rate`) so the socket lives long enough.

---

## 8. Throughput expectations — set them honestly

Measured (single remote hop, Cloudflare, same 20 MB object):

| Path | Speed |
|---|---|
| ethernet only | ~13–14 Mbit/s |
| wifi STA only | ~16–17 Mbit/s |
| both bonded | ~19.4 Mbit/s best |

≈ **1.17×**, not 2×. Reasons: both subflows terminate at the same remote host
(same RTT, same bottleneck), and the default scheduler prefers the lowest-RTT
subflow rather than strictly aggregating. Frame this as **resilience + modest
aggregation**. If you need real aggregation you must remove the shared remote
bottleneck (separate exit hosts) or use a scheduler that balances by rate.

---

## 9. Serial console beats every credential problem

The root password on a TollGate-flashed box is **derived from a per-device
BIP39 mnemonic** shown once at provisioning; it is not in any fleet store. The
UART console does not ask for it — it drops you into a root shell. Have a
USB-UART adapter and a 3-wire lead; see `docs/ACCESS.md`. This turned a hard
blocker into a ten-minute fix.

---

## 10. Licensing — interoperate, never vendor

`bondingshouldbefree`'s OpenWrt work is **GPL-2.0-only**; TollGate is GPL-3.0.
The safe pattern used here: **invoke the packaged binary** and re-implement the
one behaviour that matters (endpoint keeping) in our own code. `xray-core` is
MPL-2.0 (fine to call). Do not copy GPL-2.0 source into a GPL-3.0 tree.
