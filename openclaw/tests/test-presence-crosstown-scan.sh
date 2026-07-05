#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/../.." && pwd)
SCANNER="$REPO_ROOT/openclaw/workspace/scripts/presence-detect.sh"
TEST_HOME=$(mktemp -d)
FAKE_BIN="$TEST_HOME/fake-bin"
ARP_STANDARD="$TEST_HOME/arp-standard.txt"
ARP_REACHABILITY="$TEST_HOME/arp-reachability.txt"
TAILSCALE_CAPTURE="$TEST_HOME/tailscale-payload.json"
TAILSCALE_CALLS="$TEST_HOME/tailscale-calls.txt"

cleanup() {
  rm -rf "$TEST_HOME"
}
trap cleanup EXIT

mkdir -p "$FAKE_BIN" "$TEST_HOME/.openclaw/logs" "$TEST_HOME/.openclaw/presence"
printf '%s\n' \
  'CROSSTOWN_DYLAN_MAC=02:00:00:00:00:11' \
  'CROSSTOWN_JULIA_MAC=02:00:00:00:00:22' \
  > "$TEST_HOME/.openclaw/presence-devices.env"
chmod 600 "$TEST_HOME/.openclaw/presence-devices.env"

cat > "$FAKE_BIN/ping" <<'SH'
#!/bin/bash
exit "${FAKE_PING_EXIT:-1}"
SH

cat > "$FAKE_BIN/arp" <<'SH'
#!/bin/bash
set -euo pipefail

case "${1:-}" in
  -a)
    [[ "${FAKE_ARP_A_FAIL:-0}" != "1" ]] || exit 2
    /bin/cat "$FAKE_ARP_STANDARD"
    ;;
  -anl)
    [[ "${FAKE_ARP_ANL_FAIL:-0}" != "1" ]] || exit 2
    /bin/cat "$FAKE_ARP_REACHABILITY"
    ;;
  *)
    exit 64
    ;;
esac
SH

cat > "$FAKE_BIN/tailscale" <<'SH'
#!/bin/bash
set -euo pipefail
printf '%s\n' "$*" >> "$FAKE_TAILSCALE_CALLS"
/bin/cat > "$FAKE_TAILSCALE_CAPTURE"
SH

chmod +x "$FAKE_BIN/ping" "$FAKE_BIN/arp" "$FAKE_BIN/tailscale"

write_standard_row() {
  local name="$1" ip="$2" mac="$3" iface="${4:-en0}"
  printf '%s (%s) at %s on %s ifscope [ethernet]\n' "$name" "$ip" "$mac" "$iface"
}

write_reachability_header() {
  printf '%s\n' 'Neighbor                Linklayer Address Expire(O) Expire(I)          Netif Refs Prbs'
}

write_live_gateway() {
  printf '%s\n' '192.168.165.1          aa:bb:cc:dd:ee:1  1m20s     48s            en0    1'
}

run_success() {
  local label="$1" expected_dylan="$2" expected_julia="$3" expected_total="$4"
  local output="$TEST_HOME/$label.json"

  rm -f "$TAILSCALE_CAPTURE" "$TAILSCALE_CALLS"
  HOME="$TEST_HOME" \
    PRESENCE_PING_BIN="$FAKE_BIN/ping" \
    PRESENCE_ARP_BIN="$FAKE_BIN/arp" \
    PRESENCE_TAILSCALE_BIN="$FAKE_BIN/tailscale" \
    PRESENCE_CROSSTOWN_SWEEP_HOSTS="" \
    FAKE_ARP_STANDARD="$ARP_STANDARD" \
    FAKE_ARP_REACHABILITY="$ARP_REACHABILITY" \
    FAKE_TAILSCALE_CAPTURE="$TAILSCALE_CAPTURE" \
    FAKE_TAILSCALE_CALLS="$TAILSCALE_CALLS" \
    FAKE_PING_EXIT=1 \
    /bin/bash "$SCANNER" crosstown > "$output"

  /usr/bin/python3 - \
    "$output" "$expected_dylan" "$expected_julia" "$expected_total" <<'PY'
import json
import sys

path, expected_dylan, expected_julia, expected_total = sys.argv[1:]
with open(path, encoding="utf-8") as output_file:
    result = json.load(output_file)
if result.get("location") != "crosstown":
    raise SystemExit(f"unexpected location: {result!r}")
expected = {
    "Dylan": expected_dylan == "true",
    "Julia": expected_julia == "true",
}
actual = {
    person: result.get("presence", {}).get(person, {}).get("present")
    for person in expected
}
if actual != expected:
    raise SystemExit(f"unexpected presence: expected {expected!r}, got {actual!r}")
if result.get("totalDevices") != int(expected_total):
    raise SystemExit(
        f"unexpected totalDevices: expected {expected_total}, got {result.get('totalDevices')}"
    )
PY

  cmp -s "$output" "$TEST_HOME/.openclaw/presence/crosstown-scan.json"
  cmp -s "$output" "$TAILSCALE_CAPTURE"
  [[ $(wc -l < "$TAILSCALE_CALLS") -eq 1 ]]
  grep -q 'file cp --name crosstown-scan.json - dylans-mac-mini:' "$TAILSCALE_CALLS"
}

run_failure() {
  local label="$1" expected_log="$2"
  local output="$TEST_HOME/$label.out"

  rm -f \
    "$TAILSCALE_CAPTURE" \
    "$TAILSCALE_CALLS" \
    "$TEST_HOME/.openclaw/presence/crosstown-scan.json"
  : > "$TEST_HOME/.openclaw/logs/presence-detect.log"

  if HOME="$TEST_HOME" \
      PRESENCE_PING_BIN="$FAKE_BIN/ping" \
      PRESENCE_ARP_BIN="$FAKE_BIN/arp" \
      PRESENCE_TAILSCALE_BIN="$FAKE_BIN/tailscale" \
      PRESENCE_CROSSTOWN_SWEEP_HOSTS="" \
      FAKE_ARP_STANDARD="$ARP_STANDARD" \
      FAKE_ARP_REACHABILITY="$ARP_REACHABILITY" \
      FAKE_TAILSCALE_CAPTURE="$TAILSCALE_CAPTURE" \
      FAKE_TAILSCALE_CALLS="$TAILSCALE_CALLS" \
      FAKE_PING_EXIT=1 \
      FAKE_ARP_ANL_FAIL="${FAKE_ARP_ANL_FAIL:-0}" \
      /bin/bash "$SCANNER" crosstown > "$output" 2>&1; then
    echo "expected Crosstown fixture to fail: $label" >&2
    return 1
  fi

  [[ ! -e "$TEST_HOME/.openclaw/presence/crosstown-scan.json" ]]
  [[ ! -e "$TAILSCALE_CAPTURE" ]]
  [[ ! -e "$TAILSCALE_CALLS" ]]
  grep -q "$expected_log" "$TEST_HOME/.openclaw/logs/presence-detect.log"
}

# A live receive-side reachability timer proves presence even when every ICMP
# probe fails (the common sleeping-iPhone case).
write_standard_row dylans-iphone.lan 192.168.165.124 02:00:00:00:00:11 > "$ARP_STANDARD"
{
  write_reachability_header
  write_live_gateway
  printf '%s\n' '192.168.165.124        02:00:00:00:00:11 1m5s      37s            en0    1'
} > "$ARP_REACHABILITY"
run_success fresh-mac true false 2

# A complete cached row with only send-side freshness is stale and must not
# create a second-location positive.
write_standard_row dylans-iphone.lan 192.168.165.124 02:00:00:00:00:11 > "$ARP_STANDARD"
{
  write_reachability_header
  write_live_gateway
  printf '%s\n' '192.168.165.124        02:00:00:00:00:11 1m5s      expired        en0    1'
} > "$ARP_REACHABILITY"
run_success expired-inbound false false 1

# Static/no-receive and incomplete entries are also non-authoritative.
write_standard_row dylans-iphone.lan 192.168.165.124 02:00:00:00:00:11 > "$ARP_STANDARD"
{
  write_reachability_header
  write_live_gateway
  printf '%s\n' '192.168.165.124        02:00:00:00:00:11 (none)    (none)         en0    1'
} > "$ARP_REACHABILITY"
run_success no-inbound false false 1

write_standard_row '?' 192.168.165.124 '(incomplete)' > "$ARP_STANDARD"
{
  write_reachability_header
  write_live_gateway
  printf '%s\n' '192.168.165.124        (incomplete)      expired   expired        en0    1'
} > "$ARP_REACHABILITY"
run_success incomplete false false 1

# A fresh device merely inheriting the old reserved IP is not identity.
write_standard_row '?' 192.168.165.248 02:00:00:00:00:44 > "$ARP_STANDARD"
{
  write_reachability_header
  write_live_gateway
  printf '%s\n' '192.168.165.248        02:00:00:00:00:44 1m2s      44s            en0    1'
} > "$ARP_REACHABILITY"
run_success wrong-mac-on-old-ip false false 2

# Exact hostname remains a live, MAC-rotation-tolerant fallback when the two
# snapshots agree on IP, MAC, and interface.
write_standard_row julias-iphone.lan 192.168.165.77 02:00:00:00:00:33 > "$ARP_STANDARD"
{
  write_reachability_header
  write_live_gateway
  printf '%s\n' '192.168.165.77         02:00:00:00:00:33 1m8s      51s            en0    1'
} > "$ARP_REACHABILITY"
run_success hostname-fallback false true 2

# Substring names and cross-interface joins cannot establish identity.
write_standard_row not-julias-iphone.lan 192.168.165.77 02:00:00:00:00:33 > "$ARP_STANDARD"
{
  write_reachability_header
  write_live_gateway
  printf '%s\n' '192.168.165.77         02:00:00:00:00:33 1m8s      51s            en0    1'
} > "$ARP_REACHABILITY"
run_success hostname-substring false false 2

write_standard_row julias-iphone.lan 192.168.165.77 02:00:00:00:00:33 en1 > "$ARP_STANDARD"
{
  write_reachability_header
  write_live_gateway
  printf '%s\n' '192.168.165.77         02:00:00:00:00:33 1m8s      51s            en0    1'
} > "$ARP_REACHABILITY"
run_success hostname-interface-mismatch false false 2

# Infrastructure/parser failures preserve the last good canonical snapshot by
# exiting before the local write or Taildrop push.
: > "$ARP_STANDARD"
write_reachability_header > "$ARP_REACHABILITY"
FAKE_ARP_ANL_FAIL=1 run_failure reachability-command-failure 'Extended ARP reachability query failed'
unset FAKE_ARP_ANL_FAIL

printf '%s\n' 'unexpected header' > "$ARP_REACHABILITY"
run_failure malformed-header 'ARP reachability parsing failed'

{
  write_reachability_header
  printf '%s\n' '192.168.165.1          aa:bb:cc:dd:ee:1  1m20s     expired        en0    1'
  printf '%s\n' '192.168.165.124        02:00:00:00:00:11 1m5s      37s            en0    1'
} > "$ARP_REACHABILITY"
run_failure stale-gateway 'ARP reachability parsing failed'

echo "test-presence-crosstown-scan: PASS"
