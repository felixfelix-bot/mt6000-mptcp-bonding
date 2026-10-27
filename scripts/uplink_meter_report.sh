#!/bin/sh
# Per-client, per-uplink traffic report from the counters that roles/uplink_proxies
# installs (chain up = client egress, chain down = client ingress).
#
# Both ends meter independently; this is the router's view. A session is pinned to
# one uplink, so one client maps to exactly one uplink and one price.
#
#   ./uplink_meter_report.sh [--price lan3=24 --price phy1-sta0=86] [--json]
#
# Prices are sats per GiB for that uplink; omit them and only byte counts print.
# Reads live nft; set UPLINK_NFT_FIXTURE=<file> to parse a captured dump instead
# (that is how the offline test runs).
set -eu

TABLE=${UPLINK_TABLE:-uplink_proxies}
CONF_DIR=${UPLINK_CONF_DIR:-/etc/uplink-proxies}
JSON=0
PRICES=""

while [ $# -gt 0 ]; do
	case "$1" in
	--json) JSON=1; shift ;;
	--price) PRICES="$PRICES ${2:?--price needs iface=sats_per_gib}"; shift 2 ;;
	--price=*) PRICES="$PRICES ${1#--price=}"; shift ;;
	-h | --help)
		sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
		exit 0
		;;
	*)
		printf 'unknown argument: %s\n' "$1" >&2
		exit 2
		;;
	esac
done

price_for() {
	for pair in $PRICES; do
		case "$pair" in
		"$1="*) printf '%s' "${pair#*=}"; return 0 ;;
		esac
	done
	printf ''
}

dump_counters() {
	if [ -n "${UPLINK_NFT_FIXTURE:-}" ]; then
		cat "$UPLINK_NFT_FIXTURE"
	else
		nft list table inet "$TABLE" 2>/dev/null || true
	fi
}

counters=$(dump_counters)

# client -> uplink interface, from the configs the role renders
client_uplink() {
	_ip=$1
	for f in "$CONF_DIR"/*.json; do
		[ -e "$f" ] || continue
		port=$(sed -n 's/.*"local_port": *\([0-9][0-9]*\).*/\1/p' "$f" | head -1)
		iface=$(sed -n 's/.*"outbound_bind_interface": *"\([^"]*\)".*/\1/p' "$f" | head -1)
		[ -n "$port" ] || continue
		# the redirect rule names the port; find the client for that port
		case "$counters" in
		*"redirect to :$port"*) printf '%s' "$iface"; return 0 ;;
		esac
	done
	echo ""
}

bytes_for() {
	_ip=$1
	_dir=$2
	printf '%s\n' "$counters" |
		awk -v c="$_dir-$_ip" '
			$0 ~ c {
				for (i = 1; i < NF; i++) {
					if ($i == "bytes") { print $(i+1); exit }
				}
			}' || echo 0
}

ips=$(printf '%s\n' "$counters" | sed -n 's/.*comment "up-\([0-9.]*\)".*/\1/p' | sort -u)

if [ "$JSON" = 1 ]; then
	printf '{"table":"%s","clients":[' "$TABLE"
	first=1
	for ip in $ips; do
		up=$(bytes_for "$ip" up)
		down=$(bytes_for "$ip" down)
		total=$((up + down))
		iface=$(client_uplink "$ip")
		price=$(price_for "$iface")
		[ "$first" = 1 ] || printf ','
		first=0
		if [ -n "$price" ]; then
			sats=$(awk -v b="$total" -v p="$price" 'BEGIN { printf "%.6f", (b / 1073741824) * p }')
			printf '{"ip":"%s","uplink":"%s","up_bytes":%s,"down_bytes":%s,"total_bytes":%s,"sats":%s}' \
				"$ip" "$iface" "$up" "$down" "$total" "$sats"
		else
			printf '{"ip":"%s","uplink":"%s","up_bytes":%s,"down_bytes":%s,"total_bytes":%s,"sats":null}' \
				"$ip" "$iface" "$up" "$down" "$total"
		fi
	done
	printf ']}\n'
	exit 0
fi

printf '%-16s %-12s %14s %14s %14s %10s\n' CLIENT UPLINK UP_BYTES DOWN_BYTES TOTAL_BYTES SATS
for ip in $ips; do
	up=$(bytes_for "$ip" up)
	down=$(bytes_for "$ip" down)
	total=$((up + down))
	iface=$(client_uplink "$ip")
	price=$(price_for "$iface")
	if [ -n "$price" ]; then
		sats=$(awk -v b="$total" -v p="$price" 'BEGIN { printf "%.6f", (b / 1073741824) * p }')
	else
		sats="-"
	fi
	printf '%-16s %-12s %14s %14s %14s %10s\n' "$ip" "${iface:--}" "$up" "$down" "$total" "$sats"
done
