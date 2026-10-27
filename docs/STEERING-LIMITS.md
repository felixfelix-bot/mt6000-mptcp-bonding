# Steering a client to one uplink: what actually happens on OMR

Measured 2026-10-26 on the bench GL-MT6000, OpenMPTCProuter v0.64.2-6.18,
kernel 6.18.39.

**Correction first.** Two earlier attempts were reported here as failures on the
strength of a test client at `192.168.50.180`. That client was **not routed
through the router at all**: this host's default route left via a different
network (`192.168.1.1`). Every byte counted during those tests was unrelated
traffic, so those "measurements" proved nothing. The harness now pins the
interface (`curl --interface <lan-nic>`) and the packet counters on the router
are checked to move before any conclusion is drawn.

## What OMR actually does with LAN client traffic

`nft … trace` on a live flow from a correctly-routed client:

```text
inet ztrace pre            rule ip saddr 192.168.50.10 ip protocol tcp counter   (verdict continue)
inet fw4 raw_prerouting    policy accept
inet fw4 mangle_prerouting policy accept
inet fw4 omr_bypass_reply  policy accept
inet fw4 dstnat            rule … (verdict jump)
inet fw4 ss_rules_pre_tcp  rule … (verdict goto)
inet fw4 ss_rules_forward_tcp rule meta l4proto tcp redirect to :1100 (verdict accept)
```

```text
$ nft list chain inet fw4 ss_rules_forward_tcp
meta l4proto tcp redirect to :1100
$ netstat -ltnp | grep :1100
tcp  0  0 :::1100  :::*  LISTEN  25453/sslocal
$ ps w | grep sslocal
25453 root  /usr/bin/sslocal -c /var/etc/shadowsocks-rust/ss_redir.hi1.json
```

So client TCP is **REDIRECTed to a local `ss-redir` (shadowsocks-rust) on port
1100**, which terminates the connection and opens its own outbound socket. No
mark is set on the client's packets, and there is no TPROXY in this build — the
`fwmark 0x1 lookup 100` rule belongs to another path.

## Consequences

1. **A `from <client>` routing rule cannot work.** The packet is delivered
   locally at `nat`/prerouting; it never reaches routing as a forwardable packet.
2. **A packet mark on the client cannot work either.** Even a correct
   `0x4539<table>` mark would only affect a packet that is already being
   delivered to `sslocal`; the uplink is chosen afterwards, by the proxy.
3. **The egress decision belongs to the proxy process**, and so does metering:
   the TollGate session that bills the buyer is on that same path. Steering a
   client per-uplink therefore cannot be done by firewall rules on this
   architecture, and any implementation that bypasses port 1100 also bypasses
   accounting.

## What an implementation would have to look like

Either

- **per-uplink proxy instances** — two `ss-redir` listeners, each with its own
  outbound path, and a `nat` rule per client selecting the redirect port, so the
  owning instance still meters; or
- **no per-uplink steering at all** on OMR, with objectives expressed only as
  "bond" (all uplinks, kernel-scheduled) and price differentiation handled by the
  TollGate session instead of by the router.

The second is what the current code effectively supports. The first is now
demonstrated to work (above) and is an Ansible-shaped change; what remains is the
product decision: **who owns the meter when two instances exist** — the instance a
client is redirected to, or the TollGate session in front of both.

## The mechanism that can work: per-uplink proxy instances

`sslocal` on this image supports binding its **outbound** socket:

```text
--outbound-bind-addr <ADDR>        bind address, outbound socket will bind this address
--outbound-bind-interface <IFACE>  SO_BINDTODEVICE for outbound sockets
--outbound-fwmark <MARK>           SO_MARK for outbound sockets
```

and the same key works in the JSON config (`"outbound_bind_interface": "lan3"`). A
second instance was started on port 1101 with that key and listened successfully:

```text
tcp  0  0 :::1100  LISTEN  25453/sslocal     <- existing, bond path
tcp  0  0 :::1101  LISTEN  24319/sslocal     <- new, bound to lan3
```

So the shape is: one `ss-redir` per uplink, each bound to that uplink, plus a
`nat` rule per client selecting the redirect port. Each instance keeps its own
meter, which is the property that matters for billing.

**Proven, both directions.** Test client `192.168.50.10` on the LAN, router
counters read before and after each transfer (idle background on this box is
~1,750 KB/10 s on lan3 and ~1,463 KB/10 s on phy1, so the signal has to beat
that):

```text
instance bound to lan3 (:1101), 3 x 20 MB from the client
  lan3 rx +64,509 KB        <- carried the transfer
  phy1 rx + 2,639 KB        <- background only

instance bound to phy1-sta0 (:1102), 20 MB
  lan3 rx +   482 KB        <- background only
  phy1 rx +20,827 KB        <- carried the transfer
```

Both downloads returned `rc=200`, and the client's redirect was the only thing
changed between the two runs. So per-client, per-uplink egress **is** achievable
on this platform, with the proxy instance doing the steering.

One detail that is easy to get wrong: an instance started with `"mptcp": true`
(the stock config) still spreads its outbound subflows across the bond, and the
transfer splits — a 2 MB pull moved ~1.0 MB out of lan3 and ~1.2 MB out of phy1.
The instance must set `"mptcp": false` to pin to one uplink.

## Hazard found the hard way

A `nat` redirect matching the test client's source address **also captured SSH to
the router**, because the rule redirected every TCP flow from that host, including
`root@192.168.50.1:22`. SSH died mid-session and the box was unreachable from that
host. Recovered through another LAN machine (dq05) using SSH agent forwarding —
no private key was copied anywhere — then the rule was deleted.

Two rules for anyone repeating this:

1. Always add `ip daddr != <router-ip>` (and your management host) to a redirect
   rule, or scope it by the client's outbound port set.
2. Have an out-of-band path to the box **before** testing redirects, not after.

## Status

- **Firewall-level steering: disproven.** `pr/client-tables` (source rules) and
  `pr/client-egress` (packet marks) are kept unmerged with their tests as negative
  results, because a role that installs cleanly and does nothing is worse than no
  role.
- **Proxy-instance steering: proven by hand**, both directions, with router
  counters as evidence (above).
- **Not yet automated:** the per-uplink instances and the per-client redirect
  rules exist only as the commands in this document. Turning them into a role
  needs the metering decision first, so that the role can encode one owner of the
  byte counters instead of two.
- **Not yet decided:** whether a steered client is metered by the instance it is
  redirected to, or by a session in front of both instances.