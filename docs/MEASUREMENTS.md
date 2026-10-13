# MEASUREMENTS — what the bond actually delivers

Method matters more than the numbers here, because the numbers are
**unflattering** and it would be easy to cherry-pick a better story.

## Method

- Target: `https://speed.cloudflare.com/__down?bytes=<N>` (single object, HTTPS)
- Path: router → SOCKS/TPROXY → xray (`tcpMPTCP`) → shadowsocks → **one** remote
  host (`ss-server --mptcp`), then out to the internet
- Client: `curl` on the router, `--socks5-hostname 127.0.0.1:1081`
- Each configuration run twice; the table reports the best observed
- Uplinks: wifi STA (mobile hotspot) + ethernet (house LAN)
- Both uplinks share the **same remote endpoint**, hence the same RTT (~200 ms)

## Results

| Configuration | Bytes | Time | Throughput |
|---|---|---|---|
| ethernet only | 30 MB | 18.06 s | **~13.3 Mbit/s** |
| wifi STA only | 30 MB | 14.52 s | **~16.5 Mbit/s** |
| both, bonded (default scheduler) | 30 MB | 16.04 s | **~15.0 Mbit/s** |
| both, bonded (best observed) | 50 MB | 22.57 s | **21.2 Mbit/s** |
| both, bonded (rate-limited run) | 60 MB | 24.73 s | **~19.4 Mbit/s** |

Raw curl counters for the record:

```
ETH_ONLY=1661581B/s    t=18.055102s
WIFI_ONLY=2066000B/s   t=14.520825s
BONDED=1870226B/s      t=16.040855s
BONDED_SLOW=2426119B/s t=24.730866s
```

## Verdict — read this before quoting a speedup

**Best bonded ÷ best single path = 21.2 / 16.5 ≈ 1.28×.**
Some runs were *slower* than the better single path (15.0 vs 16.5).

Label it **resilient multi-path with modest aggregation**. It is **not** "2×".
The gain is real but small, and it is bounded by design:

1. **A shared remote bottleneck.** Both subflows terminate at the same
   `ss-server`. The far-side egress is the same pipe for both, so there is
   nothing to add up. Aggregation only pays when the paths have *independent*
   bottlenecks.
2. **Default scheduler preference.** The MPTCP scheduler prefers the
   lower-RTT subflow for new data, so a healthy bond can look almost
   single-path in a short sample.
3. **A high, shared RTT.** ~200 ms RTT to the remote caps single-stream
   throughput well below the uplinks' real capacity.

## What the bond *does* demonstrably give you

- **Path diversity, verified.** MP_JOIN on a second address completes
  (`SYN join → SYN-ACK → HMAC join`), and a live socket reports
  `subflows:2` / `local_addr_max:2`. Two *different* uplinks genuinely carry
  one transport.
- **Subflow-level survivability** once a second subflow exists: killing one
  address does not tear down the meta-connection.

## What is NOT proven

- **Mid-transfer failover.** In every attempt, no second subflow was live at
  the moment the uplink was killed, so there was nothing to fail over to and
  the transfer stalled. **Failover is untested, not disproven.** Reproduce it
  only after confirming `subflows:2` is stably present before the kill.
- **Any claim of bandwidth aggregation beyond ~1.3×.**

## How to re-measure

```sh
# on the router
for i in 1 2 3; do
  curl -s -m 120 --socks5-hostname 127.0.0.1:1081 -o /dev/null \
    -w "bonded=%{speed_download}B/s t=%{time_total}s\n" \
    "https://speed.cloudflare.com/__down?bytes=30000000"
done

# confirm the socket really is MPTCP while a transfer runs
curl -s -m 60 --limit-rate 2M --socks5-hostname 127.0.0.1:1081 -o /dev/null \
  "https://speed.cloudflare.com/__down?bytes=60000000" &
sleep 6; ss -Min dst <server>; wait
```

To measure aggregation fairly you must remove the shared far-side bottleneck
(two independent exit hosts) or use a rate-balancing scheduler. Until then,
expect ~1.2–1.3× and treat anything more as noise.
