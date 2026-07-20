#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/../.." && pwd)
SCANNER="$REPO_ROOT/openclaw/workspace/scripts/presence-detect.sh"
TEST_ROOT=$(mktemp -d)
FAKE_BIN="$TEST_ROOT/fake-bin"
CONFIG="$TEST_ROOT/presence-devices.json"
GRPC_FIXTURE="$TEST_ROOT/grpc-response.json"
GRPC_CALLS="$TEST_ROOT/grpc-calls"

cleanup() {
  rm -rf "$TEST_ROOT"
}
trap cleanup EXIT

mkdir -p "$FAKE_BIN"

cat > "$FAKE_BIN/grpcurl" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf 'called\n' >> "$FAKE_GRPC_CALLS"
/bin/cat "$FAKE_GRPC_FIXTURE"
SH
chmod +x "$FAKE_BIN/grpcurl"

write_config() {
  local site="${1:-cabin}"
  cat > "$CONFIG" <<JSON
{
  "schema_version": 1,
  "site": "$site",
  "people": {
    "Dylan": {"kind": "starlink_captive_client_id", "value": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"},
    "Julia": {"kind": "starlink_captive_client_id", "value": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}
  }
}
JSON
  chmod 600 "$CONFIG"
}

run_success() {
  local label="$1" expected_dylan="$2" expected_julia="$3"
  local home="$TEST_ROOT/home-$label" output="$TEST_ROOT/$label.json"
  mkdir -p "$home"

  HOME="$home" \
    PRESENCE_DEVICE_CONFIG="$CONFIG" \
    PRESENCE_GRPCURL_BIN="$FAKE_BIN/grpcurl" \
    FAKE_GRPC_FIXTURE="$GRPC_FIXTURE" \
    FAKE_GRPC_CALLS="$GRPC_CALLS" \
    /bin/bash "$SCANNER" observe cabin > "$output" 2> "$TEST_ROOT/$label.err"

  /usr/bin/python3 - "$output" "$expected_dylan" "$expected_julia" <<'PY'
import json
import sys

path, expected_dylan, expected_julia = sys.argv[1:]
with open(path, encoding="utf-8") as stream:
    observation = json.load(stream)
if observation.get("location") != "cabin":
    raise SystemExit(f"wrong location: {observation!r}")
expected = {
    "Dylan": expected_dylan == "true",
    "Julia": expected_julia == "true",
}
actual = {
    person: observation.get("presence", {}).get(person, {}).get("present")
    for person in expected
}
if actual != expected:
    raise SystemExit(f"unexpected presence: expected {expected!r}, got {actual!r}")
for person, details in observation.get("presence", {}).items():
    if set(details) != {"present"}:
        raise SystemExit(f"unsanitized {person} presence details: {details!r}")
serialized = json.dumps(observation)
private_values = (
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
    "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
    "Julia contractor iPhone",
    "192.168.1.",
    "02:00:00:00:00:",
)
for value in private_values:
    if value in serialized:
        raise SystemExit(f"private Starlink value leaked into output: {value!r}")
PY

  [[ ! -e "$home/.openclaw" ]]
}

run_failure() {
  local label="$1" expected_error="$2"
  local home="$TEST_ROOT/home-$label" output="$TEST_ROOT/$label.json"
  mkdir -p "$home"

  if HOME="$home" \
      PRESENCE_DEVICE_CONFIG="$CONFIG" \
      PRESENCE_GRPCURL_BIN="$FAKE_BIN/grpcurl" \
      FAKE_GRPC_FIXTURE="$GRPC_FIXTURE" \
      FAKE_GRPC_CALLS="$GRPC_CALLS" \
      /bin/bash "$SCANNER" observe cabin > "$output" 2> "$TEST_ROOT/$label.err"; then
    echo "expected Cabin fixture to fail: $label" >&2
    return 1
  fi
  grep -q '"error":"observation_failed"' "$output"
  grep -q "$expected_error" "$TEST_ROOT/$label.err"
  [[ ! -e "$home/.openclaw" ]]
}

write_config

# A configured captive-client identity must also have a current DHCP lease and
# recent traffic. The Starlink `active` field is deliberately not consulted;
# it is false for some live clients.
cat > "$GRPC_FIXTURE" <<'JSON'
{
  "wifiGetClients": {
    "clients": [
      {
        "name": "Julia contractor iPhone",
        "ipAddress": "192.168.1.90",
        "macAddress": "02:00:00:00:00:90",
        "captiveClientId": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
        "dhcpLeaseFound": true,
        "dhcpLeaseActive": true,
        "secondsUntilDhcpLeaseExpires": 900,
        "noDataIdleS": 1
      },
      {
        "name": "Untrusted Dylan label",
        "ipAddress": "192.168.1.91",
        "macAddress": "02:00:00:00:00:91",
        "captiveClientId": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "dhcpLeaseFound": true,
        "dhcpLeaseActive": true,
        "secondsUntilDhcpLeaseExpires": 1200.5,
        "noDataIdleS": 300,
        "active": false
      },
      {
        "captiveClientId": "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
        "dhcpLeaseFound": true,
        "dhcpLeaseActive": true,
        "secondsUntilDhcpLeaseExpires": 1800,
        "noDataIdleS": 0,
        "active": false
      }
    ]
  }
}
JSON
run_success exact-live-bindings true true

# Exact identities with stale or expired liveness evidence are absent.
cat > "$GRPC_FIXTURE" <<'JSON'
{
  "wifiGetClients": {
    "clients": [
      {
        "captiveClientId": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "dhcpLeaseFound": true,
        "dhcpLeaseActive": true,
        "secondsUntilDhcpLeaseExpires": 600,
        "noDataIdleS": 301
      },
      {
        "captiveClientId": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "dhcpLeaseFound": true,
        "dhcpLeaseActive": true,
        "secondsUntilDhcpLeaseExpires": 0,
        "noDataIdleS": 20
      }
    ]
  }
}
JSON
run_success stale-bindings false false

cat > "$GRPC_FIXTURE" <<'JSON'
{
  "wifiGetClients": {
    "clients": [
      {
        "captiveClientId": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "dhcpLeaseFound": false,
        "dhcpLeaseActive": true,
        "secondsUntilDhcpLeaseExpires": 600,
        "noDataIdleS": 20
      },
      {
        "captiveClientId": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "dhcpLeaseFound": true,
        "dhcpLeaseActive": false,
        "secondsUntilDhcpLeaseExpires": 600,
        "noDataIdleS": 20
      }
    ]
  }
}
JSON
run_success inactive-leases false false

# A matching identity with missing/malformed liveness fields is an API schema
# failure, not negative evidence that could contribute to a vacancy decision.
cat > "$GRPC_FIXTURE" <<'JSON'
{"wifiGetClients":{"clients":[{"captiveClientId":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","dhcpLeaseFound":true}]}}
JSON
run_failure malformed-liveness 'Starlink client response failed strict parsing'

cat > "$GRPC_FIXTURE" <<'JSON'
{
  "wifiGetClients": {
    "clients": [
      {"captiveClientId":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","dhcpLeaseFound":true,"dhcpLeaseActive":true,"secondsUntilDhcpLeaseExpires":600,"noDataIdleS":10},
      {"captiveClientId":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA","dhcpLeaseFound":true,"dhcpLeaseActive":true,"secondsUntilDhcpLeaseExpires":600,"noDataIdleS":10}
    ]
  }
}
JSON
run_failure duplicate-identity 'Starlink client response failed strict parsing'

# The protected config is exact, site-scoped, owner-only, and non-symlinked.
write_config crosstown
run_failure wrong-site-config 'Protected presence device config is unavailable or invalid'

cat > "$CONFIG" <<'JSON'
{
  "schema_version": 1,
  "site": "cabin",
  "people": {
    "Dylan": {"kind": "starlink_captive_client_id", "value": "not-64-hex"},
    "Julia": {"kind": "starlink_captive_client_id", "value": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}
  }
}
JSON
chmod 600 "$CONFIG"
run_failure malformed-identity-config 'Protected presence device config is unavailable or invalid'

write_config
chmod 644 "$CONFIG"
run_failure loose-config-mode 'Protected presence device config is unavailable or invalid'

write_config
mv "$CONFIG" "$CONFIG.real"
ln -s "$CONFIG.real" "$CONFIG"
run_failure symlink-config 'Protected presence device config is unavailable or invalid'
rm "$CONFIG"
mv "$CONFIG.real" "$CONFIG"

# Scheduled mode must preserve the last good scan and avoid the network when
# bindings have not yet been provisioned on a host.
SCHEDULED_HOME="$TEST_ROOT/home-scheduled"
mkdir -p "$SCHEDULED_HOME/.openclaw/logs" "$SCHEDULED_HOME/.openclaw/presence"
printf '%s\n' '{"sentinel":"last-good-cabin-scan"}' \
  > "$SCHEDULED_HOME/.openclaw/presence/cabin-scan.json"
: > "$GRPC_CALLS"
if HOME="$SCHEDULED_HOME" \
    PRESENCE_DEVICE_CONFIG="$SCHEDULED_HOME/.openclaw/missing-presence-devices.json" \
    PRESENCE_GRPCURL_BIN="$FAKE_BIN/grpcurl" \
    FAKE_GRPC_FIXTURE="$GRPC_FIXTURE" \
    FAKE_GRPC_CALLS="$GRPC_CALLS" \
    /bin/bash "$SCANNER" cabin > "$TEST_ROOT/scheduled-missing.out" 2>&1; then
  echo "scheduled Cabin scan without bindings unexpectedly succeeded" >&2
  exit 1
fi
grep -q 'last-good-cabin-scan' "$SCHEDULED_HOME/.openclaw/presence/cabin-scan.json"
[[ ! -s "$GRPC_CALLS" ]]

echo "test-presence-cabin-scan: PASS"
