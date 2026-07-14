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
cat > "$TEST_ROOT/cabin-presence-devices.json" <<'JSON'
{
  "schema_version": 1,
  "site": "cabin",
  "people": {
    "Dylan": {"kind": "starlink_captive_client_id", "value": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"},
    "Julia": {"kind": "starlink_captive_client_id", "value": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}
  }
}
JSON
cat > "$TEST_ROOT/crosstown-presence-devices.json" <<'JSON'
{
  "schema_version": 1,
  "site": "crosstown",
  "people": {
    "Dylan": {"kind": "mac", "value": "02:00:00:00:00:11"},
    "Julia": {"kind": "mac", "value": "02:00:00:00:00:22"}
  }
}
JSON
chmod 600 \
  "$TEST_ROOT/cabin-presence-devices.json" \
  "$TEST_ROOT/crosstown-presence-devices.json"

cat > "$FAKE_BIN/grpcurl" <<'SH'
#!/usr/bin/env bash
if [[ "${FAKE_GRPC_MODE:-valid}" == "malformed" ]]; then
  printf '%s\n' 'not-json'
  exit 0
fi
printf '%s\n' '{"wifiGetClients":{"clients":[{"name":"Untrusted label","ipAddress":"192.168.1.20","macAddress":"02:00:00:00:00:33","captiveClientId":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","dhcpLeaseFound":true,"dhcpLeaseActive":true,"secondsUntilDhcpLeaseExpires":1200,"noDataIdleS":20}]}}'
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
for person, details in observation.get("presence", {}).items():
    if set(details) != {"present"}:
        raise SystemExit(f"unsanitized {person} presence details: {details!r}")
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
    PRESENCE_DEVICE_CONFIG="$TEST_ROOT/$location-presence-devices.json" \
    PRESENCE_CROSSTOWN_SWEEP_HOSTS="" \
    FAKE_TAILSCALE_CALLS="$TAILSCALE_CALLS" \
    /bin/bash "$SCANNER" observe "$location" > "$output" 2> "$TEST_ROOT/$location.err"

  assert_observation "$output" "$location" true
  [[ ! -e "$home/.openclaw" ]]
  [[ ! -e "$TAILSCALE_CALLS" ]]
}

run_observe cabin
run_observe crosstown

# The deployment preflight validates only protected bindings and must remain
# read-only: no scan, state directory, log, or Taildrop side effect.
VALIDATE_HOME="$TEST_ROOT/home-validate"
mkdir -p "$VALIDATE_HOME"
for location in cabin crosstown; do
  HOME="$VALIDATE_HOME" \
    PRESENCE_DEVICE_CONFIG="$TEST_ROOT/$location-presence-devices.json" \
    /bin/bash "$SCANNER" validate-config "$location" \
    > "$TEST_ROOT/validate-$location.json" \
    2> "$TEST_ROOT/validate-$location.err"
  grep -q "{\"ok\":true,\"site\":\"$location\"}" \
    "$TEST_ROOT/validate-$location.json"
done
[[ ! -e "$VALIDATE_HOME/.openclaw" ]]
[[ ! -e "$TAILSCALE_CALLS" ]]
if HOME="$VALIDATE_HOME" \
    PRESENCE_DEVICE_CONFIG="$TEST_ROOT/cabin-presence-devices.json" \
    /bin/bash "$SCANNER" validate-config crosstown \
    > "$TEST_ROOT/validate-wrong-site.json" \
    2> "$TEST_ROOT/validate-wrong-site.err"; then
  echo "wrong-site presence config unexpectedly validated" >&2
  exit 1
fi
grep -q '"error":"device_config_invalid"' "$TEST_ROOT/validate-wrong-site.json"

# Missing private bindings fail closed instead of falling back to names or IPs.
NO_CONFIG_HOME="$TEST_ROOT/home-no-config"
mkdir -p "$NO_CONFIG_HOME"
if HOME="$NO_CONFIG_HOME" \
    PRESENCE_PING_BIN="$FAKE_BIN/ping" \
    PRESENCE_ARP_BIN="$FAKE_BIN/arp" \
    PRESENCE_TAILSCALE_BIN="$FAKE_BIN/tailscale" \
    PRESENCE_CROSSTOWN_SWEEP_HOSTS="" \
    FAKE_TAILSCALE_CALLS="$TAILSCALE_CALLS" \
    /bin/bash "$SCANNER" observe crosstown \
    > "$TEST_ROOT/no-config.json" 2> "$TEST_ROOT/no-config.err"; then
  echo "unconfigured read-only presence scan unexpectedly succeeded" >&2
  exit 1
fi
grep -q '"error":"observation_failed"' "$TEST_ROOT/no-config.json"
grep -q 'Protected presence device config is unavailable or invalid' "$TEST_ROOT/no-config.err"
[[ ! -e "$NO_CONFIG_HOME/.openclaw" ]]

ln -s "$TEST_ROOT/crosstown-presence-devices.json" "$TEST_ROOT/insecure-presence.json"
if HOME="$NO_CONFIG_HOME" \
    PRESENCE_ARP_BIN="$FAKE_BIN/arp" \
    PRESENCE_DEVICE_CONFIG="$TEST_ROOT/insecure-presence.json" \
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
    PRESENCE_DEVICE_CONFIG="$TEST_ROOT/cabin-presence-devices.json" \
    FAKE_GRPC_MODE=malformed \
    FAKE_TAILSCALE_CALLS="$TAILSCALE_CALLS" \
    /bin/bash "$SCANNER" observe cabin > "$TEST_ROOT/malformed.json" 2> "$TEST_ROOT/malformed.err"; then
  echo "malformed read-only observation unexpectedly succeeded" >&2
  exit 1
fi
grep -q '"error":"observation_failed"' "$TEST_ROOT/malformed.json"
[[ ! -e "$MALFORMED_HOME/.openclaw" ]]
[[ ! -e "$TAILSCALE_CALLS" ]]

echo "test-presence-observe: PASS"
