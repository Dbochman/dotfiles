#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/../.." && pwd)
SCANNER="$REPO_ROOT/openclaw/workspace/scripts/presence-detect.sh"
grep -Fqx \
  'PRESENCE_SCANNER_CONFIG_CONTRACT="cabin-sources-v2"' \
  "$SCANNER"
TEST_ROOT=$(mktemp -d)
FAKE_BIN="$TEST_ROOT/fake-bin"
CONFIG="$TEST_ROOT/presence-devices.json"
GRPC_FIXTURE="$TEST_ROOT/grpc-response.json"
MESH_FIXTURE="$TEST_ROOT/mesh-grpc-response.json"
GRPC_CALLS="$TEST_ROOT/grpc-calls"
MESH_TARGET="router:mesh-KITCHEN_01"
CONTROLLER_DYLAN="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
CONTROLLER_JULIA="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
MESH_DYLAN="dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
MESH_JULIA="EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE"

cleanup() {
  rm -rf "$TEST_ROOT"
}
trap cleanup EXIT

mkdir -p "$FAKE_BIN"

cat > "$FAKE_BIN/grpcurl" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
request=$(/bin/cat)
target=$(/usr/bin/python3 -c '
import json
import sys
request = json.load(sys.stdin)
if set(request) == {"wifiGetClients"} and request["wifiGetClients"] == {}:
    print("")
elif (
    set(request) == {"targetId", "wifiGetClients"}
    and isinstance(request["targetId"], str)
    and request["wifiGetClients"] == {}
):
    print(request["targetId"])
else:
    raise SystemExit(2)
' <<< "$request")

if [[ -z "$target" ]]; then
  printf 'controller\n' >> "$FAKE_GRPC_CALLS"
  [[ "${FAKE_CONTROLLER_FAILURE:-0}" != "1" ]] || exit 1
  /bin/cat "$FAKE_GRPC_FIXTURE"
else
  [[ "$target" == "$FAKE_MESH_TARGET" ]] || exit 2
  printf 'mesh\n' >> "$FAKE_GRPC_CALLS"
  [[ "${FAKE_MESH_FAILURE:-0}" != "1" ]] || exit 1
  /bin/cat "$FAKE_MESH_FIXTURE"
fi
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

write_v2_config() {
  cat > "$CONFIG" <<JSON
{
  "schema_version": 2,
  "site": "cabin",
  "sources": [
    {
      "kind": "starlink_controller",
      "bindings": {
        "Dylan": {"kind": "starlink_captive_client_id", "value": "$CONTROLLER_DYLAN"},
        "Julia": {"kind": "starlink_captive_client_id", "value": "$CONTROLLER_JULIA"}
      }
    },
    {
      "kind": "starlink_mesh",
      "target_id": "$MESH_TARGET",
      "bindings": {
        "Dylan": {"kind": "starlink_captive_client_id", "value": "$MESH_DYLAN"},
        "Julia": {"kind": "starlink_captive_client_id", "value": "$MESH_JULIA"}
      }
    }
  ]
}
JSON
  chmod 600 "$CONFIG"
}

run_success() {
  local label="$1" expected_dylan="$2" expected_julia="$3"
  local expected_calls="${4:-1}"
  local home="$TEST_ROOT/home-$label" output="$TEST_ROOT/$label.json"
  mkdir -p "$home"
  : > "$GRPC_CALLS"

  HOME="$home" \
    PRESENCE_DEVICE_CONFIG="$CONFIG" \
    PRESENCE_GRPCURL_BIN="$FAKE_BIN/grpcurl" \
    FAKE_GRPC_FIXTURE="$GRPC_FIXTURE" \
    FAKE_MESH_FIXTURE="$MESH_FIXTURE" \
    FAKE_MESH_TARGET="$MESH_TARGET" \
    FAKE_GRPC_CALLS="$GRPC_CALLS" \
    /bin/bash "$SCANNER" observe cabin > "$output" 2> "$TEST_ROOT/$label.err"

  ! grep -Fq 'command not found' "$TEST_ROOT/$label.err"
  ! grep -Fq 'ERROR:' "$TEST_ROOT/$label.err"

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
    "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
    "EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE",
    "router:mesh-KITCHEN_01",
    "Julia contractor iPhone",
    "192.168.1.",
    "02:00:00:00:00:",
)
for value in private_values:
    if value in serialized:
        raise SystemExit(f"private Starlink value leaked into output: {value!r}")
PY

  [[ $(wc -l < "$GRPC_CALLS") -eq "$expected_calls" ]]
  [[ ! -e "$home/.openclaw" ]]
}

run_failure() {
  local label="$1" expected_error="$2"
  local mesh_failure="${3:-0}"
  local home="$TEST_ROOT/home-$label" output="$TEST_ROOT/$label.json"
  mkdir -p "$home"
  : > "$GRPC_CALLS"

  if HOME="$home" \
      PRESENCE_DEVICE_CONFIG="$CONFIG" \
      PRESENCE_GRPCURL_BIN="$FAKE_BIN/grpcurl" \
      FAKE_GRPC_FIXTURE="$GRPC_FIXTURE" \
      FAKE_MESH_FIXTURE="$MESH_FIXTURE" \
      FAKE_MESH_TARGET="$MESH_TARGET" \
      FAKE_MESH_FAILURE="$mesh_failure" \
      FAKE_GRPC_CALLS="$GRPC_CALLS" \
      /bin/bash "$SCANNER" observe cabin > "$output" 2> "$TEST_ROOT/$label.err"; then
    echo "expected Cabin fixture to fail: $label" >&2
    return 1
  fi
  grep -q '"error":"observation_failed"' "$output"
  grep -q "$expected_error" "$TEST_ROOT/$label.err"
  for private_value in \
    "$MESH_TARGET" \
    "$CONTROLLER_DYLAN" \
    "$CONTROLLER_JULIA" \
    "$MESH_DYLAN" \
    "$MESH_JULIA"; do
    ! grep -Fq "$private_value" "$output"
    ! grep -Fq "$private_value" "$TEST_ROOT/$label.err"
  done
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

# Schema v2 explicitly binds each person on the primary controller and every
# mesh node. Presence is the union of those source-specific exact identities.
write_v2_config
cat > "$GRPC_FIXTURE" <<'JSON'
{
  "wifiGetClients": {
    "clients": [
      {
        "captiveClientId": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "dhcpLeaseFound": true,
        "dhcpLeaseActive": true,
        "secondsUntilDhcpLeaseExpires": 900,
        "noDataIdleS": 10
      }
    ]
  }
}
JSON
cat > "$MESH_FIXTURE" <<'JSON'
{
  "wifiGetClients": {
    "clients": [
      {
        "captiveClientId": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        "role": "CLIENT",
        "active": true,
        "associatedTimeS": 90,
        "signalStrength": -46,
        "rxStatsValid": true,
        "txStatsValid": true
      }
    ]
  }
}
JSON
run_success v2-controller-mesh-union true true 2

# A phone can roam to the extender and acquire a distinct node-local identity.
# A complete stale controller row does not override a fresh exact mesh row.
cat > "$GRPC_FIXTURE" <<'JSON'
{
  "wifiGetClients": {
    "clients": [
      {
        "captiveClientId": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "dhcpLeaseFound": true,
        "dhcpLeaseActive": true,
        "secondsUntilDhcpLeaseExpires": 900,
        "noDataIdleS": 301
      }
    ]
  }
}
JSON
cat > "$MESH_FIXTURE" <<'JSON'
{
  "wifiGetClients": {
    "clients": [
      {
        "captiveClientId": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
        "role": "CLIENT",
        "active": true,
        "associatedTimeS": 12.5,
        "signalStrength": -51,
        "rxStatsValid": true,
        "txStatsValid": true,
        "dhcpLeaseFound": null,
        "dhcpLeaseActive": null,
        "secondsUntilDhcpLeaseExpires": null,
        "noDataIdleS": null
      }
    ]
  }
}
JSON
run_success v2-phone-roamed-to-mesh true false 2

# A strict mesh positive wins over the controller's intermittent incomplete
# duplicate view for the same resident.
cat > "$GRPC_FIXTURE" <<'JSON'
{
  "wifiGetClients": {
    "clients": [
      {
        "captiveClientId": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "dhcpLeaseFound": true
      }
    ]
  }
}
JSON
cat > "$MESH_FIXTURE" <<'JSON'
{
  "wifiGetClients": {
    "clients": [
      {
        "captiveClientId": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
        "role": "CLIENT",
        "associatedTimeS": 42,
        "signalStrength": -49,
        "rxStatsValid": true,
        "txStatsValid": true
      }
    ]
  }
}
JSON
run_success v2-controller-unknown-mesh-positive true false 2

# Incomplete selected-row evidence remains unknown when no source has a strict
# positive for that resident.
cat > "$MESH_FIXTURE" <<'JSON'
{"wifiGetClients":{"clients":[]}}
JSON
run_failure v2-controller-unknown-no-positive \
  'Starlink client response failed strict parsing'

# The same union precedence applies in the other direction: an incomplete mesh
# duplicate does not veto a strict controller positive.
cat > "$GRPC_FIXTURE" <<'JSON'
{
  "wifiGetClients": {
    "clients": [
      {
        "captiveClientId": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "dhcpLeaseFound": true,
        "dhcpLeaseActive": true,
        "secondsUntilDhcpLeaseExpires": 900,
        "noDataIdleS": 10
      }
    ]
  }
}
JSON
cat > "$MESH_FIXTURE" <<'JSON'
{
  "wifiGetClients": {
    "clients": [
      {
        "captiveClientId": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
        "role": "CLIENT",
        "associatedTimeS": 42,
        "signalStrength": -49,
        "rxStatsValid": true
      }
    ]
  }
}
JSON
run_success v2-controller-positive-mesh-unknown true false 2

# Starlink can report `active: false` for a connected mesh client. That field is
# diagnostic-only; the exact row and strict association evidence prove presence.
cat > "$GRPC_FIXTURE" <<'JSON'
{"wifiGetClients":{"clients":[]}}
JSON
cat > "$MESH_FIXTURE" <<'JSON'
{
  "wifiGetClients": {
    "clients": [
      {
        "captiveClientId": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
        "role": "CLIENT",
        "active": false,
        "associatedTimeS": 400,
        "signalStrength": -62,
        "rxStatsValid": true,
        "txStatsValid": true
      },
      {
        "captiveClientId": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        "role": "CLIENT",
        "active": false,
        "associatedTimeS": 400,
        "signalStrength": -64,
        "rxStatsValid": true,
        "txStatsValid": true
      }
    ]
  }
}
JSON
run_success v2-mesh-active-diagnostic-only true true 2

# Absence is authoritative only after every configured source returns a valid
# clients array and none contains a selected exact identity.
cat > "$MESH_FIXTURE" <<'JSON'
{"wifiGetClients":{"clients":[{"captiveClientId":"ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"}]}}
JSON
run_success v2-all-sources-queried-both-absent false false 2

# A single configured source failure invalidates the whole observation even if
# another source contains a strict positive.
cat > "$GRPC_FIXTURE" <<'JSON'
{
  "wifiGetClients": {
    "clients": [
      {
        "captiveClientId": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "dhcpLeaseFound": true,
        "dhcpLeaseActive": true,
        "secondsUntilDhcpLeaseExpires": 900,
        "noDataIdleS": 10
      }
    ]
  }
}
JSON
run_failure v2-mesh-query-failure 'Starlink source gRPC API unreachable' 1

# `active` is not part of the authoritative mesh predicate and may be omitted.
cat > "$GRPC_FIXTURE" <<'JSON'
{"wifiGetClients":{"clients":[]}}
JSON
cat > "$MESH_FIXTURE" <<'JSON'
{
  "wifiGetClients": {
    "clients": [
      {
        "captiveClientId": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
        "role": "CLIENT",
        "associatedTimeS": 30,
        "signalStrength": -48,
        "rxStatsValid": true,
        "txStatsValid": true
      }
    ]
  }
}
JSON
run_success v2-mesh-missing-active true false 2

# Missing or invalid mesh association fields on a selected exact identity are
# schema failures, never negative evidence.
cat > "$MESH_FIXTURE" <<'JSON'
{
  "wifiGetClients": {
    "clients": [
      {
        "captiveClientId": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
        "role": "ROUTER",
        "active": true,
        "associatedTimeS": 30,
        "signalStrength": -48,
        "rxStatsValid": true,
        "txStatsValid": true
      }
    ]
  }
}
JSON
run_failure v2-mesh-invalid-role 'Starlink client response failed strict parsing'

cat > "$MESH_FIXTURE" <<'JSON'
{
  "wifiGetClients": {
    "clients": [
      {
        "captiveClientId": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
        "role": "CLIENT",
        "active": true,
        "associatedTimeS": 30,
        "signalStrength": -48,
        "rxStatsValid": true
      }
    ]
  }
}
JSON
run_failure v2-mesh-incomplete-stats 'Starlink client response failed strict parsing'

# Sources and bindings are closed schemas. Duplicate controllers, duplicate
# target IDs, unknown source kinds, and duplicate identities within one source
# all fail before any network request.
cat > "$CONFIG" <<JSON
{
  "schema_version": 2,
  "site": "cabin",
  "sources": [
    {
      "kind": "starlink_controller",
      "bindings": {
        "Dylan": {"kind": "starlink_captive_client_id", "value": "$CONTROLLER_DYLAN"},
        "Julia": {"kind": "starlink_captive_client_id", "value": "$CONTROLLER_JULIA"}
      }
    },
    {
      "kind": "starlink_controller",
      "bindings": {
        "Dylan": {"kind": "starlink_captive_client_id", "value": "$MESH_DYLAN"},
        "Julia": {"kind": "starlink_captive_client_id", "value": "$MESH_JULIA"}
      }
    }
  ]
}
JSON
chmod 600 "$CONFIG"
run_failure v2-duplicate-controller 'Protected presence device config is unavailable or invalid'
[[ ! -s "$GRPC_CALLS" ]]

cat > "$CONFIG" <<JSON
{
  "schema_version": 2,
  "site": "cabin",
  "sources": [
    {
      "kind": "starlink_controller",
      "bindings": {
        "Dylan": {"kind": "starlink_captive_client_id", "value": "$CONTROLLER_DYLAN"},
        "Julia": {"kind": "starlink_captive_client_id", "value": "$CONTROLLER_JULIA"}
      }
    },
    {
      "kind": "starlink_mesh",
      "target_id": "$MESH_TARGET",
      "bindings": {
        "Dylan": {"kind": "starlink_captive_client_id", "value": "$MESH_DYLAN"},
        "Julia": {"kind": "starlink_captive_client_id", "value": "$MESH_JULIA"}
      }
    },
    {
      "kind": "starlink_mesh",
      "target_id": "ROUTER:MESH-kitchen_01",
      "bindings": {
        "Dylan": {"kind": "starlink_captive_client_id", "value": "$MESH_DYLAN"},
        "Julia": {"kind": "starlink_captive_client_id", "value": "$MESH_JULIA"}
      }
    }
  ]
}
JSON
chmod 600 "$CONFIG"
run_failure v2-duplicate-mesh-target 'Protected presence device config is unavailable or invalid'
[[ ! -s "$GRPC_CALLS" ]]

cat > "$CONFIG" <<JSON
{
  "schema_version": 2,
  "site": "cabin",
  "sources": [
    {
      "kind": "starlink_controller",
      "bindings": {
        "Dylan": {"kind": "starlink_captive_client_id", "value": "$CONTROLLER_DYLAN"},
        "Julia": {"kind": "starlink_captive_client_id", "value": "$CONTROLLER_JULIA"}
      }
    },
    {
      "kind": "starlink_mesh",
      "target_id": "$MESH_TARGET",
      "bindings": {
        "Dylan": {"kind": "starlink_captive_client_id", "value": "$MESH_DYLAN"},
        "Julia": {"kind": "starlink_captive_client_id", "value": "$MESH_DYLAN"}
      }
    }
  ]
}
JSON
chmod 600 "$CONFIG"
run_failure v2-duplicate-source-binding 'Protected presence device config is unavailable or invalid'
[[ ! -s "$GRPC_CALLS" ]]

cat > "$CONFIG" <<JSON
{
  "schema_version": 2,
  "site": "cabin",
  "sources": [
    {
      "kind": "starlink_controller",
      "bindings": {
        "Dylan": {"kind": "starlink_captive_client_id", "value": "$CONTROLLER_DYLAN"},
        "Julia": {"kind": "starlink_captive_client_id", "value": "$CONTROLLER_JULIA"}
      }
    },
    {
      "kind": "starlink_repeater",
      "target_id": "$MESH_TARGET",
      "bindings": {
        "Dylan": {"kind": "starlink_captive_client_id", "value": "$MESH_DYLAN"},
        "Julia": {"kind": "starlink_captive_client_id", "value": "$MESH_JULIA"}
      }
    }
  ]
}
JSON
chmod 600 "$CONFIG"
run_failure v2-unknown-source-kind 'Protected presence device config is unavailable or invalid'
[[ ! -s "$GRPC_CALLS" ]]

cat > "$CONFIG" <<JSON
{
  "schema_version": 2,
  "site": "cabin",
  "sources": [
    {
      "kind": "starlink_controller",
      "bindings": {
        "Dylan": {"kind": "starlink_captive_client_id", "value": "$CONTROLLER_DYLAN"},
        "Julia": {"kind": "starlink_captive_client_id", "value": "$CONTROLLER_JULIA"}
      }
    },
    {
      "kind": "starlink_mesh",
      "target_id": "mesh target with spaces",
      "bindings": {
        "Dylan": {"kind": "starlink_captive_client_id", "value": "$MESH_DYLAN"},
        "Julia": {"kind": "starlink_captive_client_id", "value": "$MESH_JULIA"}
      }
    }
  ]
}
JSON
chmod 600 "$CONFIG"
run_failure v2-invalid-target-id 'Protected presence device config is unavailable or invalid'
[[ ! -s "$GRPC_CALLS" ]]

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
