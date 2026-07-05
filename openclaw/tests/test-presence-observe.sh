#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/../.." && pwd)
SCANNER="$REPO_ROOT/openclaw/workspace/scripts/presence-detect.sh"
TEST_ROOT=$(mktemp -d)
FAKE_BIN="$TEST_ROOT/fake-bin"
TAILSCALE_CALLS="$TEST_ROOT/tailscale-calls"

cleanup() {
  rm -rf "$TEST_ROOT"
}
trap cleanup EXIT

mkdir -p "$FAKE_BIN"
printf '%s\n' \
  'CROSSTOWN_DYLAN_MAC=02:00:00:00:00:11' \
  'CROSSTOWN_JULIA_MAC=02:00:00:00:00:22' \
  > "$TEST_ROOT/presence-devices.env"
chmod 600 "$TEST_ROOT/presence-devices.env"

cat > "$FAKE_BIN/grpcurl" <<'SH'
#!/usr/bin/env bash
if [[ "${FAKE_GRPC_MODE:-valid}" == "malformed" ]]; then
  printf '%s\n' 'not-json'
  exit 0
fi
printf '%s\n' '{"wifiGetClients":{"clients":[{"name":"Dylan iPhone","ipAddress":"192.168.1.20","macAddress":"02:00:00:00:00:33"}]}}'
SH

cat > "$FAKE_BIN/ping" <<'SH'
#!/usr/bin/env bash
exit 1
SH

cat > "$FAKE_BIN/arp" <<'SH'
#!/usr/bin/env bash
case "${1:-}" in
  -a)
    printf '%s\n' 'dylans-iphone.lan (192.168.165.124) at 02:00:00:00:00:11 on en0 ifscope [ethernet]'
    ;;
  -anl)
    printf '%s\n' \
      'Neighbor                Linklayer Address Expire(O) Expire(I)          Netif Refs Prbs' \
      '192.168.165.1          aa:bb:cc:dd:ee:1  1m20s     48s            en0    1' \
      '192.168.165.124        02:00:00:00:00:11 1m5s      37s            en0    1'
    ;;
  *) exit 64 ;;
esac
SH

cat > "$FAKE_BIN/tailscale" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$FAKE_TAILSCALE_CALLS"
exit 99
SH

chmod +x "$FAKE_BIN/grpcurl" "$FAKE_BIN/ping" "$FAKE_BIN/arp" "$FAKE_BIN/tailscale"

assert_observation() {
  local path="$1" expected_location="$2" expected_dylan="$3"
  /usr/bin/python3 - "$path" "$expected_location" "$expected_dylan" <<'PY'
import json
import sys

path, expected_location, expected_dylan = sys.argv[1:]
with open(path, encoding="utf-8") as handle:
    observation = json.load(handle)
if observation.get("location") != expected_location:
    raise SystemExit(f"wrong location: {observation!r}")
actual = observation.get("presence", {}).get("Dylan", {}).get("present")
if actual is not (expected_dylan == "true"):
    raise SystemExit(f"wrong Dylan presence: {actual!r}")
PY
}

run_observe() {
  local location="$1" home output
  home="$TEST_ROOT/home-$location"
  output="$TEST_ROOT/$location.json"
  mkdir -p "$home"
  HOME="$home" \
    PRESENCE_GRPCURL_BIN="$FAKE_BIN/grpcurl" \
    PRESENCE_PING_BIN="$FAKE_BIN/ping" \
    PRESENCE_ARP_BIN="$FAKE_BIN/arp" \
    PRESENCE_TAILSCALE_BIN="$FAKE_BIN/tailscale" \
    PRESENCE_CROSSTOWN_DEVICE_CONFIG="$TEST_ROOT/presence-devices.env" \
    PRESENCE_CROSSTOWN_SWEEP_HOSTS="" \
    FAKE_TAILSCALE_CALLS="$TAILSCALE_CALLS" \
    /bin/bash "$SCANNER" observe "$location" > "$output" 2> "$TEST_ROOT/$location.err"

  assert_observation "$output" "$location" true
  [[ ! -e "$home/.openclaw" ]]
  [[ ! -e "$TAILSCALE_CALLS" ]]
}

run_observe cabin
run_observe crosstown

# First-rollout compatibility is hostname-only when the private config has not
# been provisioned; it remains read-only and does not infer identity from IP.
NO_CONFIG_HOME="$TEST_ROOT/home-no-config"
mkdir -p "$NO_CONFIG_HOME"
HOME="$NO_CONFIG_HOME" \
  PRESENCE_PING_BIN="$FAKE_BIN/ping" \
  PRESENCE_ARP_BIN="$FAKE_BIN/arp" \
  PRESENCE_TAILSCALE_BIN="$FAKE_BIN/tailscale" \
  PRESENCE_CROSSTOWN_SWEEP_HOSTS="" \
  FAKE_TAILSCALE_CALLS="$TAILSCALE_CALLS" \
  /bin/bash "$SCANNER" observe crosstown \
  > "$TEST_ROOT/no-config.json" 2> "$TEST_ROOT/no-config.err"
assert_observation "$TEST_ROOT/no-config.json" crosstown true
grep -q 'hostname-only matching' "$TEST_ROOT/no-config.err"
[[ ! -e "$NO_CONFIG_HOME/.openclaw" ]]

ln -s "$TEST_ROOT/presence-devices.env" "$TEST_ROOT/insecure-presence.env"
if HOME="$NO_CONFIG_HOME" \
    PRESENCE_ARP_BIN="$FAKE_BIN/arp" \
    PRESENCE_CROSSTOWN_DEVICE_CONFIG="$TEST_ROOT/insecure-presence.env" \
    /bin/bash "$SCANNER" observe crosstown \
    > "$TEST_ROOT/insecure-config.json" 2> "$TEST_ROOT/insecure-config.err"; then
  echo "symlinked presence device config unexpectedly succeeded" >&2
  exit 1
fi
grep -q 'observation_failed' "$TEST_ROOT/insecure-config.json"

# Malformed scanner output must fail without writing state or invoking Taildrop.
MALFORMED_HOME="$TEST_ROOT/home-malformed"
mkdir -p "$MALFORMED_HOME"
if HOME="$MALFORMED_HOME" \
    PRESENCE_GRPCURL_BIN="$FAKE_BIN/grpcurl" \
    PRESENCE_TAILSCALE_BIN="$FAKE_BIN/tailscale" \
    FAKE_GRPC_MODE=malformed \
    FAKE_TAILSCALE_CALLS="$TAILSCALE_CALLS" \
    /bin/bash "$SCANNER" observe cabin > "$TEST_ROOT/malformed.json" 2> "$TEST_ROOT/malformed.err"; then
  echo "malformed read-only observation unexpectedly succeeded" >&2
  exit 1
fi
grep -q '"error":"invalid_observation"' "$TEST_ROOT/malformed.json"
[[ ! -e "$MALFORMED_HOME/.openclaw" ]]
[[ ! -e "$TAILSCALE_CALLS" ]]

echo "test-presence-observe: PASS"
