#!/usr/bin/env bash
# Idempotence + bond verification. A config-management repo that is not
# idempotent is broken; this is the test that proves it isn't.
#
#   ./tests/idempotence.sh [inventory]
set -uo pipefail

INV="${1:-inventory/hosts.yml}"
cd "$(dirname "$0")/.."

# The proxy config path on the target (kept in step with group_vars/all.yml).
XRAY_CFG="${XRAY_CFG:-/tmp/xray-bond.json}"
SOCKS_PORT="${SOCKS_PORT:-1081}"
FWMARK_TABLE="${FWMARK_TABLE:-100}"

fail=0
ok()   { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=1; }

echo "== 1. playbook syntax =="
ansible-playbook -i "$INV" playbook.yml --syntax-check >/dev/null 2>&1 \
  && ok "syntax" || bad "syntax"

echo "== 2. run 1 (converge) =="
out1=$(ansible-playbook -i "$INV" playbook.yml 2>&1)
c1=$(grep -oE 'changed=[0-9]+' <<<"$out1" | tail -1)
grep -q 'unreachable=0' <<<"$out1" && ok "run1 reachable ($c1)" || bad "run1 unreachable"

echo "== 3. run 2 (must be idempotent) =="
out2=$(ansible-playbook -i "$INV" playbook.yml 2>&1)
c2=$(grep -oE 'changed=[0-9]+' <<<"$out2" | tail -1)
n2="${c2#changed=}"
if [ "${n2:-99}" -eq 0 ]; then ok "run2 changed=0"; else bad "run2 $c2 (not idempotent)"; fi
grep -q 'failed=0' <<<"$out2" && ok "run2 no failures" || bad "run2 had failures"

echo "== 4. on-box bond assertions =="
HOST=$(awk '/ansible_host:/{print $2; exit}' "$INV")
chk=$(ssh -o ConnectTimeout=15 "$HOST" '
  e=0
  ip mptcp endpoint show 2>/dev/null | grep -q subflow || { echo "no-endpoints"; e=1; }
  n=$(ip mptcp endpoint show 2>/dev/null | grep -c subflow)
  [ "$n" -ge 2 ] || { echo "endpoints=$n (need 2)"; e=1; }
  sysctl -n net.mptcp.enabled | grep -q 1 || { echo "mptcp-off"; e=1; }
  ip rule | grep -q "lookup 200" || { echo "no-policy-routing"; e=1; }
  ip route show table '"${FWMARK_TABLE:-100}"' >/dev/null 2>&1 || true
  [ $e -eq 0 ] && echo ALL_OK
' 2>&1)

if grep -q ALL_OK <<<"$chk"; then
  ok "endpoints >= 2, mptcp on, policy routing present"
else
  bad "on-box: $chk"
fi

echo "== 5. MPTCP proof on a live socket =="
# Discover the far-end address from the proxy config on the box, so this test
# carries no deployment-specific address of its own.
SERVER="${SERVER:-}"
if [ -z "$SERVER" ]; then
  SERVER=$(ssh -o ConnectTimeout=15 "$HOST" \
    "grep -oE '(server|address)[\"'\'': ]+[\"'\'']?[0-9a-zA-Z._-]+' $XRAY_CFG 2>/dev/null | head -1 | grep -oE '[0-9a-zA-Z._-]+\$'" \
    2>/dev/null | tr -d '\r"')
fi
if [ -z "$SERVER" ] || [ "$SERVER" = "127.0.0.1" ]; then
  bad "could not discover the MPTCP far end from $XRAY_CFG"
  echo "     pass SERVER=<host> in the environment to check explicitly"
  echo
  echo "TESTS FAILED"
  exit 1
fi
proof=$(ssh -o ConnectTimeout=15 "$HOST" "
  curl -s -m 25 --limit-rate 500k --socks5-hostname 127.0.0.1:${SOCKS_PORT:-1081} \\
    -o /dev/null 'https://speed.cloudflare.com/__down?bytes=4000000' &
  sleep 6
  ss -Mti dst $SERVER 2>/dev/null | grep -c tcp-ulp-mptcp
  wait 2>/dev/null" 2>/dev/null | tr -d "\r" | tail -1)

if [ "${proof:-0}" -gt 0 ]; then
  ok "tcp-ulp-mptcp present ($proof sockets)"
else
  bad "no MPTCP socket seen — LEARNINGS §1"
fi

echo
[ $fail -eq 0 ] && echo "ALL TESTS PASSED" || echo "TESTS FAILED"
exit $fail
