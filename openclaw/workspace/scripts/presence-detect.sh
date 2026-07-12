#!/bin/bash
# presence-detect.sh — Multi-location presence detection for OpenClaw
#
# Detects who is home at each location by querying local network devices.
#
# Usage:
#   presence-detect.sh cabin       # Scan cabin WiFi + Potato GPS (run on Mac Mini)
#   presence-detect.sh crosstown   # Scan Crosstown LAN (run on MacBook Pro)
#   presence-detect.sh potato      # Scan Potato GPS only (run on Mac Mini)
#   presence-detect.sh evaluate    # Correlate all signals (run on Mac Mini)
#   presence-detect.sh observe cabin|crosstown  # Read-only network observation
#
# Cabin (Philly):   Starlink gRPC API via grpcurl (Mac Mini, local)
# Crosstown (Boston): ARP scan (MacBook Pro, local)
#
# Vacancy rules:
#   A location is only "confirmed_vacant" when ALL tracked people are
#   absent there AND confirmed present at the other location.
#   Otherwise it's "possibly_vacant" (phones may be sleeping).
#
# Crosstown pushes a named state file to the Mac Mini via `tailscale file cp`
# after each scan. The macOS Tailscale app deposits it in Downloads, where the
# Mini's WatchPaths receiver promotes it before `evaluate` reads both scans.

set -euo pipefail
umask 077

REQUESTED_MODE="${1:-}"
READ_ONLY_OBSERVATION=0
[ "$REQUESTED_MODE" = "observe" ] && READ_ONLY_OBSERVATION=1

LOG_FILE="$HOME/.openclaw/logs/presence-detect.log"
if [ -x "/opt/homebrew/opt/node@22/bin/node" ]; then
  NODE="/opt/homebrew/opt/node@22/bin/node"
elif [ -x "/opt/homebrew/bin/node" ]; then
  NODE="/opt/homebrew/bin/node"
else
  NODE="$(command -v node || echo /opt/homebrew/bin/node)"
fi
GRPCURL="${PRESENCE_GRPCURL_BIN:-/opt/homebrew/bin/grpcurl}"
PING="${PRESENCE_PING_BIN:-/sbin/ping}"
ARP="${PRESENCE_ARP_BIN:-/usr/sbin/arp}"
if [ -n "${PRESENCE_TAILSCALE_BIN:-}" ]; then
  TAILSCALE="$PRESENCE_TAILSCALE_BIN"
else
  TAILSCALE="/usr/local/bin/tailscale"
  [ -x "$TAILSCALE" ] || TAILSCALE="/Applications/Tailscale.app/Contents/MacOS/Tailscale"
fi
STATE_DIR="${HOME}/.openclaw/presence"
EVALUATE_LOCK_FILE="${STATE_DIR}/evaluate.lock"
EVALUATE_LOCK_TIMEOUT_SECONDS=10
HOME_EVENTS_PRESENCE_ENABLED="${HOME_EVENTS_PRESENCE_ENABLED:-0}"
HOME_EVENTCTL="${HOME_EVENTCTL:-$HOME/.openclaw/bin/home-eventctl}"
PRESENCE_EVENT_OUTBOX_DIR="${PRESENCE_EVENT_OUTBOX_DIR:-$STATE_DIR/home-events-outbox}"

rotate_log_if_needed() {
  local max_bytes=${PRESENCE_LOG_MAX_BYTES:-$((100 * 1024 * 1024))}
  local keep=3
  local size lock i previous

  [ -f "$LOG_FILE" ] || return 0
  size=$(stat -f%z "$LOG_FILE" 2>/dev/null || echo 0)
  [ "$size" -gt "$max_bytes" ] || return 0

  lock="${LOG_FILE}.rotate.lock"
  mkdir "$lock" 2>/dev/null || return 0

  size=$(stat -f%z "$LOG_FILE" 2>/dev/null || echo 0)
  if [ "$size" -gt "$max_bytes" ]; then
    i=$keep
    while [ "$i" -gt 1 ]; do
      previous=$((i - 1))
      [ -f "$LOG_FILE.$previous" ] && mv -f "$LOG_FILE.$previous" "$LOG_FILE.$i"
      i=$previous
    done
    mv -f "$LOG_FILE" "$LOG_FILE.1"
    : > "$LOG_FILE"
  fi

  rmdir "$lock" 2>/dev/null || true
}

if [ "$READ_ONLY_OBSERVATION" -eq 0 ]; then
  mkdir -p "$STATE_DIR" "$(dirname "$LOG_FILE")"
  /bin/chmod 700 "$STATE_DIR"
  rotate_log_if_needed
fi

log() {
  if [ "$READ_ONLY_OBSERVATION" -eq 1 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >&2
  else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
  fi
}

write_private_state_file() {
  local target="$1"

  "$NODE" -e '
"use strict";
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const target = process.argv[1];
const directory = path.dirname(target);
const owner = typeof process.getuid === "function" ? process.getuid() : null;
const directoryMetadata = fs.lstatSync(directory);
if (!directoryMetadata.isDirectory() || directoryMetadata.isSymbolicLink() ||
    (owner !== null && directoryMetadata.uid !== owner) ||
    (directoryMetadata.mode & 0o077) !== 0) process.exit(2);
try {
  const existing = fs.lstatSync(target);
  if (!existing.isFile() || existing.isSymbolicLink() ||
      (owner !== null && existing.uid !== owner)) process.exit(2);
} catch (error) {
  if (!error || error.code !== "ENOENT") process.exit(2);
}
const temporary = target + ".tmp." + process.pid + "." + crypto.randomBytes(8).toString("hex");
let descriptor;
try {
  descriptor = fs.openSync(
    temporary,
    fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_NOFOLLOW,
    0o600
  );
  const input = fs.readFileSync(0);
  fs.writeFileSync(descriptor, input);
  fs.fchmodSync(descriptor, 0o600);
  fs.fsyncSync(descriptor);
  fs.closeSync(descriptor);
  descriptor = undefined;
  fs.renameSync(temporary, target);
  const directoryDescriptor = fs.openSync(directory, fs.constants.O_RDONLY);
  try { fs.fsyncSync(directoryDescriptor); } finally { fs.closeSync(directoryDescriptor); }
} catch (error) {
  if (descriptor !== undefined) {
    try { fs.closeSync(descriptor); } catch {}
  }
  try { fs.unlinkSync(temporary); } catch {}
  process.exit(2);
}
' "$target"
}

# ── Known devices ────────────────────────────────────────────────────────────

CABIN_TRACKED='["Dylan","Julia"]'
CROSSTOWN_TRACKED='["Dylan","Julia"]'

CABIN_DEVICES='[
  {"person":"Dylan","match":"name","pattern":"Dylan","require":"iPhone"},
  {"person":"Dylan","match":"name","pattern":"Dylan","require":"phone"},
  {"person":"Julia","match":"name","pattern":"Julia"},
  {"person":"Julia","match":"name_fallback","pattern":"iPhone","excludeNames":["Dylan"]}
]'

# Crosstown MAC identities are machine-local private configuration on the MBP.
CROSSTOWN_DEVICE_CONFIG="${PRESENCE_CROSSTOWN_DEVICE_CONFIG:-$HOME/.openclaw/presence-devices.env}"
CROSSTOWN_DEVICES=""

load_crosstown_devices() {
  if [[ -z "${CROSSTOWN_DYLAN_MAC:-}" || -z "${CROSSTOWN_JULIA_MAC:-}" ]]; then
    if [[ ! -e "$CROSSTOWN_DEVICE_CONFIG" && ! -L "$CROSSTOWN_DEVICE_CONFIG" ]]; then
      log "WARN: Crosstown device config is not provisioned; using exact hostname-only matching"
      CROSSTOWN_DEVICES='[
  {"person":"Dylan","match":"hostname","pattern":"dylans-iphone"},
  {"person":"Julia","match":"hostname","pattern":"julias-iphone"}
]'
      return 0
    fi
    if [[ ! -f "$CROSSTOWN_DEVICE_CONFIG" || -L "$CROSSTOWN_DEVICE_CONFIG" ]]; then
      log "ERROR: Crosstown device config must be a regular non-symlink file"
      return 1
    fi
    local owner mode
    read -r owner mode < <(stat -f '%u %Lp' "$CROSSTOWN_DEVICE_CONFIG")
    if [[ "$owner" != "$(id -u)" || "$mode" != "600" ]]; then
      log "ERROR: Crosstown device config must be owned by the current user with mode 0600"
      return 1
    fi
    set -a
    if ! . "$CROSSTOWN_DEVICE_CONFIG" >/dev/null 2>&1; then
      set +a
      log "ERROR: Crosstown device config could not be loaded"
      return 1
    fi
    set +a
  fi

  local mac_pattern='^([[:xdigit:]]{2}:){5}[[:xdigit:]]{2}$'
  if [[ ! "${CROSSTOWN_DYLAN_MAC:-}" =~ $mac_pattern ||
        ! "${CROSSTOWN_JULIA_MAC:-}" =~ $mac_pattern ]]; then
    log "ERROR: Crosstown device config contains an invalid MAC address"
    return 1
  fi

  CROSSTOWN_DEVICES=$(printf '%s' "[
  {\"person\":\"Dylan\",\"match\":\"mac\",\"pattern\":\"$CROSSTOWN_DYLAN_MAC\"},
  {\"person\":\"Julia\",\"match\":\"mac\",\"pattern\":\"$CROSSTOWN_JULIA_MAC\"},
  {\"person\":\"Julia\",\"match\":\"hostname\",\"pattern\":\"julias-iphone\"}
]")
}

# ── Cabin: Starlink gRPC API ────────────────────────────────────────────────

scan_cabin() {
  local grpc_response
  grpc_response=$($GRPCURL -plaintext -d '{"wifiGetClients":{}}' \
    192.168.1.1:9000 SpaceX.API.Device.Device/Handle 2>/dev/null || echo '{}')

  if [ "$grpc_response" = "{}" ] || [ -z "$grpc_response" ]; then
    log "ERROR: Starlink gRPC API unreachable"
    echo '{"error":"starlink_unreachable","location":"cabin"}'
    return 1
  fi

  { printf '%s\0%s' "$CABIN_DEVICES" "$grpc_response"; } | "$NODE" -e "
const input = require('fs').readFileSync(0);
const separator = input.indexOf(0);
if (separator < 0) process.exit(2);
const devices = JSON.parse(input.subarray(0, separator).toString('utf8'));
const response = JSON.parse(input.subarray(separator + 1).toString('utf8'));
const clients = response?.wifiGetClients?.clients || [];
const results = {};

for (const dev of devices) {
  if (results[dev.person]) continue;
  for (const client of clients) {
    const name = (client.name || '').toLowerCase();
    const pattern = dev.pattern.toLowerCase();
    let matched = false;

    if (dev.match === 'name') {
      matched = name.includes(pattern);
      if (matched && dev.require && !name.includes(dev.require.toLowerCase())) matched = false;
    } else if (dev.match === 'name_fallback') {
      matched = name.includes(pattern);
      if (matched && dev.excludeNames) {
        for (const excl of dev.excludeNames) {
          if (name.includes(excl.toLowerCase())) { matched = false; break; }
        }
        if (matched) {
          for (const [, info] of Object.entries(results)) {
            if (info.present && info.mac === (client.macAddress || '').toLowerCase()) { matched = false; break; }
          }
        }
      }
    } else if (dev.match === 'mac') {
      matched = (client.macAddress || '').toLowerCase() === pattern.toLowerCase();
    }

    if (matched) {
      results[dev.person] = {
        present: true,
        device: client.name || 'unknown',
        ip: client.ipAddress || '',
        mac: client.macAddress || '',
        signal: client.signalStrength || 0,
        connectedMinutes: Math.round((client.associatedTimeS || 0) / 60),
        interface: client.iface || ''
      };
      break;
    }
  }
}

for (const dev of devices) {
  if (!results[dev.person]) results[dev.person] = { present: false };
}

console.log(JSON.stringify({
  location: 'cabin',
  timestamp: new Date().toISOString(),
  totalClients: clients.length,
  presence: results
}, null, 2));
" 2>/dev/null || echo '{"error":"parse_failed","location":"cabin"}'
}

# ── Crosstown: ARP scan ─────────────────────────────────────────────────────

scan_crosstown() {
  if ! load_crosstown_devices; then
    echo '{"error":"crosstown_device_config_unavailable","location":"crosstown"}'
    return 1
  fi
  # Step 1: Probe the subnet. iPhones may ignore ICMP while still answering
  # link-layer resolution, so ping success is not used as the presence signal.
  local gateway_ip="192.168.165.1"
  local known_ips="192.168.165.124 192.168.165.248"
  local probe_ips="$gateway_ip $known_ips"
  local sweep_hosts
  if [ "${PRESENCE_CROSSTOWN_SWEEP_HOSTS+x}" = "x" ]; then
    # Test seam: an explicitly empty value disables only the broad sweep.
    sweep_hosts="$PRESENCE_CROSSTOWN_SWEEP_HOSTS"
  else
    sweep_hosts=$(/usr/bin/seq 1 254)
  fi
  for ip in $probe_ips; do
    "$PING" -c3 -W2 "$ip" >/dev/null 2>&1 &
  done
  for i in $sweep_hosts; do
    "$PING" -c1 -W1 "192.168.165.$i" >/dev/null 2>&1 &
  done
  wait

  # Step 2: Re-probe tracked IPs, then require fresh inbound link-layer
  # reachability from `arp -anl`. Plain `arp -a` retains complete entries after
  # a device leaves, and deleting those entries requires root on macOS.
  sleep 1
  for ip in $probe_ips; do
    "$PING" -c2 -W3 "$ip" >/dev/null 2>&1 &
  done
  wait

  local arp_all reachability_all
  if ! arp_all=$("$ARP" -a 2>/dev/null); then
    log "WARN: ARP hostname query failed; continuing with numeric reachability"
    arp_all=""
  fi
  if ! reachability_all=$("$ARP" -anl 2>/dev/null); then
    log "ERROR: Extended ARP reachability query failed"
    echo '{"error":"arp_reachability_failed","location":"crosstown"}'
    return 1
  fi
  if [ -z "$reachability_all" ]; then
    log "ERROR: Extended ARP reachability query returned no output"
    echo '{"error":"arp_scan_failed","location":"crosstown"}'
    return 1
  fi

  local parsed
  if ! parsed=$({ printf '%s\0%s\0%s' "$CROSSTOWN_DEVICES" "$arp_all" "$reachability_all"; } | "$NODE" -e "
const input = require('fs').readFileSync(0);
const firstSeparator = input.indexOf(0);
const secondSeparator = input.indexOf(0, firstSeparator + 1);
if (firstSeparator < 0 || secondSeparator < 0) process.exit(2);
const devices = JSON.parse(input.subarray(0, firstSeparator).toString('utf8'));
const arpLines = input.subarray(firstSeparator + 1, secondSeparator).toString('utf8').split('\n').filter(Boolean);
const rawReachabilityLines = input.subarray(secondSeparator + 1).toString('utf8').split('\n').map(line => line.trim()).filter(Boolean);
const results = {};

function fail(message) {
  console.error(message);
  process.exit(2);
}

function normalizeMac(value) {
  const parts = String(value || '').toLowerCase().split(':');
  if (parts.length !== 6 || parts.some(part => !/^[0-9a-f]{1,2}$/.test(part))) return null;
  return parts.map(part => Number.parseInt(part, 16).toString(16).padStart(2, '0')).join(':');
}

function inboundExpiryIsLive(value) {
  // macOS arp -anl renders live durations as combinations such as 52s,
  // 2m21s, or 1h3m. Unknown/expired/static entries must not prove presence.
  if (value === 'expired' || value === '(none)') return false;
  if (!/^[1-9][0-9dhms]{1,8}$/.test(value || '')) fail('unexpected inbound reachability value');
  return true;
}

const header = rawReachabilityLines.shift() || '';
if (!/^Neighbor\s+Linklayer Address\s+Expire\(O\)\s+Expire\(I\)\s+Netif(?:\s+Refs\s+Prbs)?$/.test(header)) {
  fail('unexpected arp -anl header');
}

const liveRows = [];
const liveKeys = new Map();
for (const line of rawReachabilityLines) {
  const columns = line.trim().split(/\s+/);
  if (columns.length < 5) continue;
  const [ip, rawMac, , inboundExpiry, iface] = columns;
  if (!/^192\.168\.165\.[0-9]+$/.test(ip)) continue;
  if (rawMac === '(incomplete)') continue;
  const mac = normalizeMac(rawMac);
  if (!mac) fail('malformed in-subnet link-layer address');
  if (!inboundExpiryIsLive(inboundExpiry)) continue;
  const key = ip + '|' + iface;
  const previous = liveKeys.get(key);
  if (previous && previous !== mac) fail('conflicting live link-layer rows');
  if (!previous) {
    liveKeys.set(key, mac);
    liveRows.push({ ip, mac, iface });
  }
}

const gatewayRows = liveRows.filter(row => row.ip === '192.168.165.1');
if (gatewayRows.length !== 1) fail('gateway lacks unambiguous fresh inbound reachability');
const activeInterface = gatewayRows[0].iface;
const activeRows = liveRows.filter(row => row.iface === activeInterface);

function neighborKey(ip, mac, iface) {
  return ip + '|' + mac + '|' + iface;
}

const namesByNeighbor = new Map();
for (const line of arpLines) {
  const match = line.match(/^(\S+)\s+\(([0-9.]+)\)\s+at\s+([0-9a-f:]+|\(incomplete\))\s+on\s+(\S+)/i);
  if (!match) continue;
  const [, rawName, ip, rawMac, iface] = match;
  const mac = normalizeMac(rawMac);
  if (!mac) continue;
  const live = activeRows.some(row => row.ip === ip && row.mac === mac && row.iface === iface);
  if (!live) continue;
  const name = rawName.toLowerCase().replace(/\.$/, '');
  if (name !== '?') namesByNeighbor.set(neighborKey(ip, mac, iface), name);
}

function hostnameMatches(name, pattern) {
  if (!name) return false;
  const expected = pattern.toLowerCase().replace(/\.$/, '');
  return name === expected || name.split('.')[0] === expected;
}

for (const dev of devices) {
  for (const row of activeRows) {
    const name = namesByNeighbor.get(neighborKey(row.ip, row.mac, row.iface));
    let matched = false;
    if (dev.match === 'mac') matched = row.mac === normalizeMac(dev.pattern);
    else if (dev.match === 'name' || dev.match === 'hostname') matched = hostnameMatches(name, dev.pattern);
    if (matched) {
      results[dev.person] = {
        present: true,
        ip: row.ip,
        mac: row.mac,
        device: dev.match === 'mac' ? 'phone (MAC match)' : name
      };
      break;
    }
  }
  if (!results[dev.person]) results[dev.person] = { present: false };
}

console.log(JSON.stringify({
  location: 'crosstown',
  timestamp: new Date().toISOString(),
  totalDevices: activeRows.length,
  presence: results
}, null, 2));
" 2>/dev/null); then
    log "ERROR: ARP reachability parsing failed"
    echo '{"error":"parse_failed","location":"crosstown"}'
    return 1
  fi
  printf '%s\n' "$parsed"
}

# ── Potato: Fi collar GPS (runs on Mac Mini, queries cellular API) ──────────
# Fi-collar already classifies coords against CROSSTOWN_LAT/LON and CABIN_LAT/LON
# (radii 150m/300m); we just relay its `location`/`at_location` fields.

scan_potato() {
  local fi_response
  fi_response=$(
    set -a
    [ -f "$HOME/.openclaw/.secrets-cache" ] && . "$HOME/.openclaw/.secrets-cache"
    set +a
    /opt/homebrew/bin/fi-collar location 2>/dev/null
  )

  if [ -z "$fi_response" ] || [ "$fi_response" = "{}" ]; then
    echo '{"error":"fi_unreachable","name":"Potato"}'
    return 1
  fi

  printf '%s' "$fi_response" | "$NODE" -e "
const data = JSON.parse(require('fs').readFileSync(0, 'utf8'));
if (data.error) { console.log(JSON.stringify({error: data.error, name: 'Potato'})); process.exit(0); }
console.log(JSON.stringify({
  name: 'Potato',
  timestamp: new Date().toISOString(),
  location: data.location,
  at_location: data.at_location,
  distance_m: data.distance_m,
  activity: data.activity,
  lastReport: data.lastReport,
  latitude: data.latitude,
  longitude: data.longitude
}, null, 2));
" 2>/dev/null || echo '{"error":"parse_failed","name":"Potato"}'
}

# Validate the strict stdout contract used by side-effect-free callers. This is
# intentionally separate from scheduled scan modes so their behavior is
# unchanged.
validate_observation() {
  local result="$1" expected_location="$2"
  printf '%s' "$result" | "$NODE" -e '
let observation;
try {
  observation = JSON.parse(require("fs").readFileSync(0, "utf8"));
} catch {
  process.exit(2);
}
if (!observation || typeof observation !== "object" || Array.isArray(observation)) process.exit(2);
if (observation.error || observation.location !== process.argv[1]) process.exit(2);
const timestamp = Date.parse(observation.timestamp);
const ageMs = Date.now() - timestamp;
if (!Number.isFinite(timestamp) || ageMs < -60000 || ageMs > 300000) process.exit(2);
const presence = observation.presence;
if (!presence || typeof presence !== "object" || Array.isArray(presence)
    || Object.keys(presence).length === 0) process.exit(2);
for (const [person, info] of Object.entries(presence)) {
  if (!person || !info || typeof info !== "object" || Array.isArray(info)
      || typeof info.present !== "boolean") process.exit(2);
}
console.log(JSON.stringify(observation, null, 2));
' "$expected_location"
}

observe_network() {
  local location="$1" result validated scan_status
  case "$location" in
    cabin)
      if result=$(scan_cabin); then scan_status=0; else scan_status=$?; fi
      ;;
    crosstown)
      if result=$(scan_crosstown); then scan_status=0; else scan_status=$?; fi
      ;;
    *)
      echo '{"error":"unknown_observation_location"}'
      return 2
      ;;
  esac

  if [ "$scan_status" -ne 0 ]; then
    log "ERROR: Read-only $location observation failed (status $scan_status)"
    echo "{\"error\":\"observation_failed\",\"location\":\"$location\"}"
    return "$scan_status"
  fi
  if ! validated=$(validate_observation "$result" "$location" 2>/dev/null); then
    log "ERROR: Read-only $location observation returned malformed data"
    echo "{\"error\":\"invalid_observation\",\"location\":\"$location\"}"
    return 1
  fi
  printf '%s\n' "$validated"
}

# ── Evaluate: Correlate both locations (runs on Mac Mini) ────────────────────

presence_state_worker() {
  local mode="$1"
  shift

  PRESENCE_STATE_DIR="$STATE_DIR" \
  PRESENCE_OUTBOX_DIR="$PRESENCE_EVENT_OUTBOX_DIR" \
  PRESENCE_OUTBOX_ENABLED="$HOME_EVENTS_PRESENCE_ENABLED" \
    "$NODE" - "$mode" "$CABIN_TRACKED" "$CROSSTOWN_TRACKED" "$@" <<'NODE'
'use strict';

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const mode = process.argv[2];
const cabinTracked = JSON.parse(process.argv[3]);
const crosstownTracked = JSON.parse(process.argv[4]);
const args = process.argv.slice(5);
const allTracked = [...new Set([...cabinTracked, ...crosstownTracked])];
const stateDir = process.env.PRESENCE_STATE_DIR;
const outboxDir = process.env.PRESENCE_OUTBOX_DIR;
const outboxEnabled = process.env.PRESENCE_OUTBOX_ENABLED === '1';
const stateFile = path.join(stateDir, 'state.json');
const prevFile = path.join(stateDir, 'prev-evaluated.json');
const eventsFile = path.join(stateDir, 'events.json');
const producerStateFile = path.join(outboxDir, 'producer-state.json');
const missingStateHash = sha256('presence-state-missing-v1');

function sha256(value) {
  return crypto.createHash('sha256').update(value).digest('hex');
}

function stableStringify(value) {
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return '[' + value.map(stableStringify).join(',') + ']';
  const keys = Object.keys(value).sort();
  return '{' + keys.map(key => JSON.stringify(key) + ':' + stableStringify(value[key])).join(',') + '}';
}

function stateHash(value) {
  return sha256(stableStringify(value));
}

function fsyncDirectory(directory) {
  const fd = fs.openSync(directory, fs.constants.O_RDONLY);
  try {
    fs.fsyncSync(fd);
  } finally {
    fs.closeSync(fd);
  }
}

function ensureDirectory(directory, fileMode, exactMode) {
  try {
    fs.mkdirSync(directory, { recursive: true, mode: fileMode });
  } catch (error) {
    if (error.code !== 'EEXIST') throw error;
  }
  const metadata = fs.lstatSync(directory);
  if (!metadata.isDirectory() || metadata.isSymbolicLink()) throw new Error('unsafe_directory');
  if (typeof process.getuid === 'function' && metadata.uid !== process.getuid()) {
    throw new Error('wrong_directory_owner');
  }
  if (exactMode && (metadata.mode & 0o777) !== fileMode) throw new Error('unsafe_directory_mode');
}

function ensureOutbox() {
  ensureDirectory(outboxDir, 0o700, true);
}

function atomicWrite(target, contents, fileMode = 0o600) {
  const directory = path.dirname(target);
  ensureDirectory(directory, directory === outboxDir ? 0o700 : 0o700, false);
  const temporary = target + '.tmp.' + process.pid + '.' + crypto.randomBytes(8).toString('hex');
  let fd;
  try {
    fd = fs.openSync(
      temporary,
      fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_NOFOLLOW,
      fileMode
    );
    fs.writeFileSync(fd, contents, 'utf8');
    fs.fchmodSync(fd, fileMode);
    fs.fsyncSync(fd);
    fs.closeSync(fd);
    fd = undefined;
    fs.renameSync(temporary, target);
    fsyncDirectory(directory);
  } catch (error) {
    if (fd !== undefined) {
      try { fs.closeSync(fd); } catch {}
    }
    try { fs.unlinkSync(temporary); } catch {}
    throw error;
  }
}

function atomicWriteJson(target, value) {
  atomicWrite(target, JSON.stringify(value, null, 2) + '\n');
}

function readOptionalJson(target, protectedFile = false) {
  try {
    if (protectedFile) validateProtectedFile(target);
    return JSON.parse(fs.readFileSync(target, 'utf8'));
  } catch (error) {
    if (error.code === 'ENOENT') return null;
    throw error;
  }
}

function currentCanonical() {
  let value;
  try {
    value = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
  } catch (error) {
    if (error.code === 'ENOENT') return { value: null, hash: missingStateHash };
    throw error;
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('invalid_state');
  return { value, hash: stateHash(value) };
}

function validateProtectedFile(target) {
  const metadata = fs.lstatSync(target);
  if (!metadata.isFile() || metadata.isSymbolicLink()) throw new Error('unsafe_outbox_file');
  if (typeof process.getuid === 'function' && metadata.uid !== process.getuid()) {
    throw new Error('wrong_outbox_owner');
  }
  if ((metadata.mode & 0o077) !== 0) throw new Error('unsafe_outbox_mode');
  if (metadata.size <= 0 || metadata.size > 8 * 1024 * 1024) throw new Error('unsafe_outbox_size');
}

function exactKeys(value, expected) {
  const actual = Object.keys(value).sort();
  const wanted = [...expected].sort();
  return actual.length === wanted.length && actual.every((key, index) => key === wanted[index]);
}

function validateProducerState(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      !exactKeys(value, ['schema_version', 'sequence', 'observation_id', 'state_hash', 'evaluated_at']) ||
      value.schema_version !== 1 ||
      !Number.isSafeInteger(value.sequence) || value.sequence < 1 ||
      typeof value.observation_id !== 'string' || !/^[0-9a-f]{64}$/.test(value.observation_id) ||
      typeof value.state_hash !== 'string' || !/^[0-9a-f]{64}$/.test(value.state_hash) ||
      typeof value.evaluated_at !== 'string' || !Number.isFinite(Date.parse(value.evaluated_at))) {
    throw new Error('invalid_producer_state');
  }
  return value;
}

function validateNormalizedEvent(event) {
  const fields = [
    'schema_version', 'source_event_id', 'event_type', 'site', 'entity_kind',
    'entity_alias', 'occurred_at', 'observed_at', 'time_precision', 'attributes'
  ];
  if (!event || typeof event !== 'object' || Array.isArray(event) || !exactKeys(event, fields) ||
      event.schema_version !== 1 ||
      typeof event.source_event_id !== 'string' || !/^presence_[0-9a-f]{64}$/.test(event.source_event_id) ||
      !['presence.occupancy_changed', 'presence.person_relocated'].includes(event.event_type) ||
      !['cabin', 'crosstown'].includes(event.site) ||
      !['site', 'person'].includes(event.entity_kind) ||
      typeof event.entity_alias !== 'string' || !/^[a-z][a-z0-9_]{0,63}$/.test(event.entity_alias) ||
      typeof event.occurred_at !== 'string' || !Number.isFinite(Date.parse(event.occurred_at)) ||
      typeof event.observed_at !== 'string' || !Number.isFinite(Date.parse(event.observed_at)) ||
      event.time_precision !== 'evaluation' ||
      !event.attributes || typeof event.attributes !== 'object' || Array.isArray(event.attributes)) {
    throw new Error('invalid_normalized_event');
  }
  if (event.event_type === 'presence.occupancy_changed') {
    if (event.entity_kind !== 'site' || event.entity_alias !== event.site ||
        !exactKeys(event.attributes, ['previous', 'current', 'confidence', 'evidence_at', 'state_hash']) ||
        !['occupied', 'confirmed_vacant', 'possibly_vacant', 'unknown'].includes(event.attributes.previous) ||
        !['occupied', 'confirmed_vacant', 'possibly_vacant', 'unknown'].includes(event.attributes.current) ||
        event.attributes.confidence !== 'canonical') {
      throw new Error('invalid_occupancy_event');
    }
  } else if (event.entity_kind !== 'person' ||
      !exactKeys(event.attributes, ['person_alias', 'from_site', 'to_site', 'confidence', 'evidence_at', 'state_hash']) ||
      event.attributes.person_alias !== event.entity_alias ||
      event.attributes.to_site !== event.site ||
      event.attributes.confidence !== 'positive_detection') {
    throw new Error('invalid_relocation_event');
  }
  if (!['cabin', 'crosstown'].includes(event.attributes.from_site) &&
      event.event_type === 'presence.person_relocated') throw new Error('invalid_relocation_from');
  if (!['cabin', 'crosstown'].includes(event.attributes.to_site) &&
      event.event_type === 'presence.person_relocated') throw new Error('invalid_relocation_to');
  if (typeof event.attributes.evidence_at !== 'string' ||
      !Number.isFinite(Date.parse(event.attributes.evidence_at)) ||
      typeof event.attributes.state_hash !== 'string' ||
      !/^[0-9a-f]{64}$/.test(event.attributes.state_hash)) {
    throw new Error('invalid_event_evidence');
  }
  return event;
}

function validateBatch(batch) {
  const fields = [
    'schema_version', 'source', 'batch_id', 'observation_id', 'prior_state_hash',
    'target_state_hash', 'target_state', 'events', 'events_projection',
    'history_relative_path', 'history_projection', 'producer_state'
  ];
  if (!batch || typeof batch !== 'object' || Array.isArray(batch) || !exactKeys(batch, fields)) {
    throw new Error('invalid_batch');
  }
  if (batch.schema_version !== 1 || batch.source !== 'presence') throw new Error('invalid_batch_schema');
  for (const key of ['batch_id', 'observation_id', 'prior_state_hash', 'target_state_hash']) {
    if (typeof batch[key] !== 'string' || !/^[0-9a-f]{64}$/.test(batch[key])) {
      throw new Error('invalid_batch_identity');
    }
  }
  if (!batch.target_state || typeof batch.target_state !== 'object' || Array.isArray(batch.target_state)) {
    throw new Error('invalid_batch_target');
  }
  if (stateHash(batch.target_state) !== batch.target_state_hash) throw new Error('batch_hash_mismatch');
  if (!Array.isArray(batch.events) || batch.events.length > 32) throw new Error('invalid_batch_events');
  batch.events.forEach(validateNormalizedEvent);
  if (batch.events_projection !== null && typeof batch.events_projection !== 'string') {
    throw new Error('invalid_events_projection');
  }
  if (typeof batch.history_relative_path !== 'string' ||
      !/^history\/[0-9]{4}-[0-9]{2}-[0-9]{2}\.jsonl$/.test(batch.history_relative_path)) {
    throw new Error('invalid_history_path');
  }
  if (typeof batch.history_projection !== 'string') throw new Error('invalid_history_projection');
  validateProducerState(batch.producer_state);
  return batch;
}

function readBatch(target) {
  validateProtectedFile(target);
  return validateBatch(JSON.parse(fs.readFileSync(target, 'utf8')));
}

function crashAfter(point) {
  if (process.env.PRESENCE_TEST_CRASH_AFTER === point) process.exit(86);
}

function finishCompatibilityProjections(batch) {
  atomicWriteJson(prevFile, batch.target_state);
  if (batch.events_projection !== null) atomicWrite(eventsFile, batch.events_projection);
  atomicWrite(path.join(stateDir, batch.history_relative_path), batch.history_projection);
  atomicWriteJson(producerStateFile, batch.producer_state);
}

function recoverOutbox() {
  ensureOutbox();
  const entries = fs.readdirSync(outboxDir).sort();
  const pending = entries.filter(name => /^[0-9a-f]{64}\.pending\.json$/.test(name));
  if (pending.length > 1) throw new Error('multiple_pending_batches');
  if (pending.length === 1) {
    const pendingPath = path.join(outboxDir, pending[0]);
    const batch = readBatch(pendingPath);
    const canonical = currentCanonical();
    if (canonical.hash === batch.target_state_hash) {
      finishCompatibilityProjections(batch);
      const readyPath = path.join(outboxDir, batch.batch_id + '.ready.json');
      if (fs.existsSync(readyPath)) throw new Error('ready_batch_collision');
      fs.renameSync(pendingPath, readyPath);
      fsyncDirectory(outboxDir);
    } else if (canonical.hash === batch.prior_state_hash) {
      fs.unlinkSync(pendingPath);
      fsyncDirectory(outboxDir);
    } else {
      throw new Error('pending_state_mismatch');
    }
  }
  for (const name of fs.readdirSync(outboxDir).sort()) {
    if (/^[0-9a-f]{64}\.ready\.json$/.test(name)) readBatch(path.join(outboxDir, name));
  }
  process.stdout.write('{"ok":true}\n');
}

function readyEvents(target) {
  if (path.dirname(target) !== outboxDir || !/^[0-9a-f]{64}\.ready\.json$/.test(path.basename(target))) {
    throw new Error('invalid_ready_path');
  }
  const batch = readBatch(target);
  for (const event of batch.events) process.stdout.write(JSON.stringify(event) + '\n');
}

function acknowledgeReady(target) {
  if (path.dirname(target) !== outboxDir || !/^[0-9a-f]{64}\.ready\.json$/.test(path.basename(target))) {
    throw new Error('invalid_ready_path');
  }
  readBatch(target);
  fs.unlinkSync(target);
  fsyncDirectory(outboxDir);
  process.stdout.write('{"ok":true}\n');
}

function listReady() {
  ensureOutbox();
  const ready = fs.readdirSync(outboxDir)
    .filter(name => /^[0-9a-f]{64}\.ready\.json$/.test(name))
    .map(name => ({ name, batch: readBatch(path.join(outboxDir, name)) }))
    .sort((left, right) => {
      const sequenceDifference = left.batch.producer_state.sequence - right.batch.producer_state.sequence;
      return sequenceDifference || left.name.localeCompare(right.name);
    });
  for (let index = 1; index < ready.length; index += 1) {
    if (ready[index - 1].batch.producer_state.sequence === ready[index].batch.producer_state.sequence) {
      throw new Error('duplicate_ready_sequence');
    }
  }
  for (const item of ready) process.stdout.write(path.join(outboxDir, item.name) + '\n');
}

function readLegacyJson(target, fallback) {
  try {
    return JSON.parse(fs.readFileSync(target, 'utf8'));
  } catch {
    return fallback;
  }
}

function readEvaluationInput(filename) {
  const target = path.join(stateDir, filename);
  try {
    const value = JSON.parse(fs.readFileSync(target, 'utf8'));
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      throw new Error('invalid_evaluation_input');
    }
    return value;
  } catch (error) {
    if (error.code === 'ENOENT') return {};
    throw error;
  }
}

function snapshotEvaluationInputs() {
  const inputs = {
    cabin: readEvaluationInput('cabin-scan.json'),
    crosstown: readEvaluationInput('crosstown-scan.json'),
    potato: readEvaluationInput('potato-scan.json')
  };
  const marker = process.env.SCAN_READ_MARKER;
  const release = process.env.SCAN_READ_RELEASE_FILE;
  if (marker && release) {
    fs.writeFileSync(marker, '', { mode: 0o600 });
    const sleeper = new Int32Array(new SharedArrayBuffer(4));
    let released = false;
    for (let attempt = 0; attempt < 1000; attempt += 1) {
      if (fs.existsSync(release)) {
        released = true;
        break;
      }
      Atomics.wait(sleeper, 0, 0, 10);
    }
    if (!released) throw new Error('test_scan_release_timeout');
  }
  return inputs;
}

function historyProjection(relativePath, record) {
  const target = path.join(stateDir, relativePath);
  let existing = '';
  try { existing = fs.readFileSync(target, 'utf8'); } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  if (existing && !existing.endsWith('\n')) existing += '\n';
  return existing + JSON.stringify(record) + '\n';
}

function safeAlias(value) {
  const alias = String(value).trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
  if (!/^[a-z][a-z0-9_]{0,63}$/.test(alias)) throw new Error('invalid_person_alias');
  return alias;
}

function evidenceTimestamp(cabin, crosstown, preferredSite) {
  const preferred = preferredSite === 'cabin' ? cabin.timestamp : crosstown.timestamp;
  if (typeof preferred === 'string' && Number.isFinite(Date.parse(preferred))) return preferred;
  const candidates = [cabin.timestamp, crosstown.timestamp]
    .filter(value => typeof value === 'string' && Number.isFinite(Date.parse(value)))
    .sort();
  return candidates[candidates.length - 1] || new Date().toISOString();
}

function evaluate(cabin, crosstown, potato) {
  const prev = readLegacyJson(prevFile, {});
  const now = process.env.PRESENCE_TEST_NOW || new Date().toISOString();
  if (!Number.isFinite(Date.parse(now))) throw new Error('invalid_evaluation_time');
  const nowMs = Date.parse(now);
  const cabinPresence = cabin.presence || {};
  const crosstownPresence = crosstown.presence || {};

  // Scans older than 30 minutes cannot support cross-location correlation.
  const cabinAge = cabin.timestamp ? (nowMs - new Date(cabin.timestamp).getTime()) / 60000 : 999;
  const crosstownAge = crosstown.timestamp ? (nowMs - new Date(crosstown.timestamp).getTime()) / 60000 : 999;
  const cabinFresh = cabinAge < 30;
  const crosstownFresh = crosstownAge < 30;

  const people = {};
  for (const person of allTracked) {
    const seenAtCabin = cabinFresh && cabinPresence[person]?.present === true;
    const seenAtCrosstown = crosstownFresh && crosstownPresence[person]?.present === true;
    const prevLoc = prev.people?.[person]?.location || 'unknown';
    let location;
    if (seenAtCabin && seenAtCrosstown) location = prevLoc;
    else if (seenAtCabin) location = 'cabin';
    else if (seenAtCrosstown) location = 'crosstown';
    else location = prevLoc;
    people[person] = {
      cabin: location === 'cabin',
      crosstown: location === 'crosstown',
      location
    };
  }

  // Potato remains informational and never participates in vacancy decisions.
  if (potato && !potato.error && potato.latitude != null) {
    const location = potato.at_location && (potato.location === 'cabin' || potato.location === 'crosstown')
      ? potato.location : 'traveling';
    people.Potato = {
      cabin: location === 'cabin',
      crosstown: location === 'crosstown',
      location,
      activity: potato.activity || null,
      distance_m: potato.distance_m ?? null,
      source: 'fi-collar'
    };
  }

  function occupancy(location) {
    const tracked = location === 'cabin' ? cabinTracked : crosstownTracked;
    const hereFresh = location === 'cabin' ? cabinFresh : crosstownFresh;
    const otherFresh = location === 'cabin' ? crosstownFresh : cabinFresh;
    const herePresence = location === 'cabin' ? cabinPresence : crosstownPresence;
    const here = location;
    const there = location === 'cabin' ? 'crosstown' : 'cabin';
    const anyFreshDirectHere = hereFresh && tracked.some(person => herePresence[person]?.present === true);
    const anyHere = tracked.some(person => people[person]?.[here]);
    const noneHere = tracked.every(person => !people[person]?.[here]);
    const allThere = tracked.every(person => people[person]?.[there]);
    if (anyHere || anyFreshDirectHere) return 'occupied';
    if (noneHere && allThere && otherFresh) return 'confirmed_vacant';
    if (noneHere) return 'possibly_vacant';
    return 'unknown';
  }

  const cabinOccupancy = occupancy('cabin');
  const crosstownOccupancy = occupancy('crosstown');
  const transitions = [];
  const prevCabin = prev.cabin?.occupancy;
  const prevCrosstown = prev.crosstown?.occupancy;
  if (prevCabin && prevCabin !== cabinOccupancy) {
    transitions.push({ location: 'cabin', from: prevCabin, to: cabinOccupancy, timestamp: now });
  }
  if (prevCrosstown && prevCrosstown !== crosstownOccupancy) {
    transitions.push({ location: 'crosstown', from: prevCrosstown, to: crosstownOccupancy, timestamp: now });
  }
  for (const person of allTracked) {
    const prevLoc = prev.people?.[person]?.location;
    const currLoc = people[person].location;
    const actuallyDetected =
      (cabinFresh && cabinPresence[person]?.present === true) ||
      (crosstownFresh && crosstownPresence[person]?.present === true);
    if (prevLoc && prevLoc !== currLoc && currLoc !== 'unknown' && actuallyDetected) {
      transitions.push({ person, event: 'relocated', from: prevLoc, to: currLoc, timestamp: now });
    }
  }

  const result = {
    timestamp: now,
    people,
    cabin: {
      occupancy: cabinOccupancy,
      stateChangedAt: (prevCabin && prevCabin !== cabinOccupancy) ? now : (prev.cabin?.stateChangedAt || now),
      scanAge: Math.round(cabinAge) + 'min',
      fresh: cabinFresh
    },
    crosstown: {
      occupancy: crosstownOccupancy,
      stateChangedAt: (prevCrosstown && prevCrosstown !== crosstownOccupancy)
        ? now : (prev.crosstown?.stateChangedAt || now),
      scanAge: Math.round(crosstownAge) + 'min',
      fresh: crosstownFresh
    },
    transitions
  };

  let eventsProjection = null;
  if (transitions.length > 0) {
    let legacyEvents = readLegacyJson(eventsFile, []);
    if (!Array.isArray(legacyEvents)) legacyEvents = [];
    eventsProjection = JSON.stringify([...legacyEvents, ...transitions].slice(-100), null, 2) + '\n';
  }
  const historyRelativePath = 'history/' + now.slice(0, 10) + '.jsonl';
  const historyRecord = {
    timestamp: now,
    cabin: { occupancy: cabinOccupancy, people: allTracked.filter(person => people[person]?.cabin) },
    crosstown: { occupancy: crosstownOccupancy, people: allTracked.filter(person => people[person]?.crosstown) }
  };

  if (!outboxEnabled) {
    atomicWriteJson(prevFile, result);
    if (eventsProjection !== null) atomicWrite(eventsFile, eventsProjection);
    atomicWriteJson(stateFile, result);
    atomicWrite(path.join(stateDir, historyRelativePath), historyProjection(historyRelativePath, historyRecord));
    process.stdout.write(JSON.stringify(result, null, 2) + '\n');
    return;
  }

  ensureOutbox();
  const canonical = currentCanonical();
  const transitionIdentity = transitions.map(transition => {
    if (transition.event === 'relocated') {
      return { event: 'relocated', person: safeAlias(transition.person), from: transition.from, to: transition.to };
    }
    return { event: 'occupancy_changed', location: transition.location, from: transition.from, to: transition.to };
  });
  const observationMaterial = {
    schema_version: 1,
    evidence: {
      cabin: typeof cabin.timestamp === 'string' ? cabin.timestamp : null,
      crosstown: typeof crosstown.timestamp === 'string' ? crosstown.timestamp : null
    },
    state: {
      cabin: cabinOccupancy,
      crosstown: crosstownOccupancy,
      people: Object.fromEntries(allTracked.map(person => [safeAlias(person), people[person].location]))
    },
    transitions: transitionIdentity
  };
  const observationId = sha256(stableStringify(observationMaterial));
  const targetStateHash = stateHash(result);
  const batchId = sha256('presence-batch-v1\0' + observationId + '\0' + targetStateHash);
  const rawProducerState = readOptionalJson(producerStateFile, true);
  const priorProducerState = rawProducerState === null ? null : validateProducerState(rawProducerState);
  const baseline = priorProducerState === null;
  const busEvents = baseline ? [] : transitions.map((transition, index) => {
    const sourceEventId = 'presence_' + sha256(
      'presence-event-v1\0' + observationId + '\0' + index + '\0' + stableStringify(transitionIdentity[index])
    );
    if (transition.event === 'relocated') {
      const personAlias = safeAlias(transition.person);
      return {
        schema_version: 1,
        source_event_id: sourceEventId,
        event_type: 'presence.person_relocated',
        site: transition.to,
        entity_kind: 'person',
        entity_alias: personAlias,
        occurred_at: transition.timestamp,
        observed_at: now,
        time_precision: 'evaluation',
        attributes: {
          person_alias: personAlias,
          from_site: transition.from,
          to_site: transition.to,
          confidence: 'positive_detection',
          evidence_at: evidenceTimestamp(cabin, crosstown, transition.to),
          state_hash: targetStateHash
        }
      };
    }
    return {
      schema_version: 1,
      source_event_id: sourceEventId,
      event_type: 'presence.occupancy_changed',
      site: transition.location,
      entity_kind: 'site',
      entity_alias: transition.location,
      occurred_at: transition.timestamp,
      observed_at: now,
      time_precision: 'evaluation',
      attributes: {
        previous: transition.from,
        current: transition.to,
        confidence: 'canonical',
        evidence_at: evidenceTimestamp(cabin, crosstown, transition.location),
        state_hash: targetStateHash
      }
    };
  });

  historyRecord.observationId = observationId;
  const batch = {
    schema_version: 1,
    source: 'presence',
    batch_id: batchId,
    observation_id: observationId,
    prior_state_hash: canonical.hash,
    target_state_hash: targetStateHash,
    target_state: result,
    events: busEvents,
    events_projection: eventsProjection,
    history_relative_path: historyRelativePath,
    history_projection: historyProjection(historyRelativePath, historyRecord),
    producer_state: {
      schema_version: 1,
      sequence: priorProducerState === null ? 1 : priorProducerState.sequence + 1,
      observation_id: observationId,
      state_hash: targetStateHash,
      evaluated_at: now
    }
  };
  const pendingPath = path.join(outboxDir, batchId + '.pending.json');
  const readyPath = path.join(outboxDir, batchId + '.ready.json');
  if (fs.existsSync(pendingPath) || fs.existsSync(readyPath)) throw new Error('batch_collision');
  atomicWriteJson(pendingPath, batch);
  crashAfter('after_pending');
  atomicWriteJson(stateFile, result);
  crashAfter('after_state_commit');
  finishCompatibilityProjections(batch);
  crashAfter('after_projections');
  fs.renameSync(pendingPath, readyPath);
  fsyncDirectory(outboxDir);
  crashAfter('after_ready');
  process.stdout.write(JSON.stringify(result, null, 2) + '\n');
}

if (mode === 'recover') {
  recoverOutbox();
} else if (mode === 'ready-events') {
  readyEvents(args[0]);
} else if (mode === 'ack-ready') {
  acknowledgeReady(args[0]);
} else if (mode === 'list-ready') {
  listReady();
} else if (mode === 'evaluate') {
  const inputs = snapshotEvaluationInputs();
  evaluate(inputs.cabin, inputs.crosstown, inputs.potato);
} else {
  throw new Error('unknown_worker_mode');
}
NODE
}

publish_presence_ready() {
  local ready ready_list payloads event publish_failed
  local -a ready_files=()

  if ! ready_list=$(presence_state_worker list-ready 2>/dev/null); then
    log "ERROR: Presence event outbox ordering failed closed"
    return 1
  fi
  if [[ -n "$ready_list" ]]; then
    while IFS= read -r ready; do
      [[ -n "$ready" ]] && ready_files+=("$ready")
    done <<< "$ready_list"
  fi

  [[ "${#ready_files[@]}" -gt 0 ]] || return 0

  for ready in "${ready_files[@]}"; do
    if ! payloads=$(presence_state_worker ready-events "$ready" 2>/dev/null); then
      log "ERROR: Presence event outbox contains an invalid ready batch"
      return 1
    fi

    publish_failed=0
    if [[ -n "$payloads" ]]; then
      while IFS= read -r event; do
        if [[ ! -x "$HOME_EVENTCTL" ]] ||
            ! printf '%s\n' "$event" | "$HOME_EVENTCTL" enqueue --source presence >/dev/null 2>&1; then
          publish_failed=1
          break
        fi
      done <<< "$payloads"
    fi

    if [[ "$publish_failed" -eq 1 ]]; then
      log "WARN: Home-event bus unavailable; retaining presence batch for retry"
      break
    fi
    if ! presence_state_worker ack-ready "$ready" >/dev/null 2>&1; then
      log "ERROR: Failed to acknowledge a published presence batch"
      return 1
    fi
  done
}

prepare_presence_outbox() {
  if ! presence_state_worker recover >/dev/null 2>&1; then
    log "ERROR: Presence event outbox recovery failed closed"
    return 1
  fi
  publish_presence_ready
}

evaluate_locked() {
  local result

  if [[ "$HOME_EVENTS_PRESENCE_ENABLED" != "0" && "$HOME_EVENTS_PRESENCE_ENABLED" != "1" ]]; then
    log "ERROR: HOME_EVENTS_PRESENCE_ENABLED must be 0 or 1"
    echo '{"error":"invalid_home_events_presence_flag"}'
    return 1
  fi

  if [[ "$HOME_EVENTS_PRESENCE_ENABLED" = "1" ]] && ! prepare_presence_outbox; then
    echo '{"error":"presence_outbox_recovery_failed"}'
    return 1
  fi

  # The worker snapshots fixed protected-state paths only after pending work
  # has been reconciled under the evaluator lock. Raw network/GPS observations
  # never enter argv or the environment.
  if ! result=$(presence_state_worker evaluate 2>/dev/null); then
    log "ERROR: Presence evaluation or durable state commit failed"
    echo '{"error":"evaluate_failed"}'
    return 1
  fi
  printf '%s\n' "$result"

  # Bus availability is deliberately not part of canonical presence success.
  # Valid ready batches survive and retry during the next serialized evaluation.
  if [[ "$HOME_EVENTS_PRESENCE_ENABLED" = "1" ]] && ! publish_presence_ready; then
    log "ERROR: Presence event outbox acknowledgement failed closed"
    return 1
  fi
}

prepare_evaluate_lock_file() {
  local metadata

  if [[ -L "$EVALUATE_LOCK_FILE" ]]; then
    log "ERROR: Presence evaluation lock file is unsafe"
    return 1
  fi
  if [[ ! -e "$EVALUATE_LOCK_FILE" ]]; then
    if ! (umask 077; set -o noclobber; : > "$EVALUATE_LOCK_FILE") 2>/dev/null; then
      [[ -e "$EVALUATE_LOCK_FILE" && ! -L "$EVALUATE_LOCK_FILE" ]] || return 1
    fi
  fi
  [[ -f "$EVALUATE_LOCK_FILE" && ! -L "$EVALUATE_LOCK_FILE" ]] || return 1
  metadata=$(/usr/bin/stat -f '%u %Lp' "$EVALUATE_LOCK_FILE" 2>/dev/null) || return 1
  [[ "$metadata" = "$(/usr/bin/id -u) 600" ]] || {
    [[ "${metadata%% *}" = "$(/usr/bin/id -u)" ]] || return 1
    /bin/chmod 600 "$EVALUATE_LOCK_FILE" || return 1
  }
}

# Cabin scans and Taildrop receipts can request correlation concurrently. Keep
# network scanning outside this lock, but serialize every evaluation from its
# first scan read through all correlated state, event, and history writes.
evaluate() (
  local lock_status path_inode fd_inode

  if ! prepare_evaluate_lock_file; then
    log "ERROR: Presence evaluation lock file validation failed"
    echo '{"error":"evaluate_lock_unsafe"}'
    return 1
  fi
  exec 9<>"$EVALUATE_LOCK_FILE"
  path_inode=$(/usr/bin/stat -f '%i' "$EVALUATE_LOCK_FILE" 2>/dev/null) || return 1
  fd_inode=$(/usr/bin/stat -f '%i' /dev/fd/9 2>/dev/null) || return 1
  if [[ -L "$EVALUATE_LOCK_FILE" || "$path_inode" != "$fd_inode" ]]; then
    log "ERROR: Presence evaluation lock file changed during open"
    echo '{"error":"evaluate_lock_unsafe"}'
    return 1
  fi
  if /usr/bin/lockf -s -t "$EVALUATE_LOCK_TIMEOUT_SECONDS" 9; then
    evaluate_locked
    return
  else
    lock_status=$?
  fi

  if [ "$lock_status" -eq 75 ]; then
    log "ERROR: Presence evaluation lock timed out after ${EVALUATE_LOCK_TIMEOUT_SECONDS}s"
    echo '{"error":"evaluate_lock_timeout"}'
  else
    log "ERROR: Presence evaluation lock acquisition failed (status $lock_status)"
    echo '{"error":"evaluate_lock_failed"}'
  fi
  return "$lock_status"
)

# ── Main ─────────────────────────────────────────────────────────────────────

LOCATION="${1:-}"
if [ -z "$LOCATION" ]; then
  hostname=$(hostname -s 2>/dev/null || echo "unknown")
  case "$hostname" in
    *mac-mini*|*dylans-mac-mini*) LOCATION="cabin" ;;
    *macbook-pro*) LOCATION="crosstown" ;;
    *) LOCATION="unknown" ;;
  esac
fi

log "Running: $LOCATION"

case "$LOCATION" in
  observe)
    if [ "$#" -ne 2 ]; then
      echo '{"error":"usage","message":"presence-detect.sh observe <cabin|crosstown>"}'
      exit 2
    fi
    observe_network "$2"
    ;;
  cabin)
    result=$(scan_cabin)
    printf '%s\n' "$result" | write_private_state_file "${STATE_DIR}/cabin-scan.json"
    log "Cabin scan completed"
    # Also refresh Potato's Fi-collar GPS — runs on Mini, queries cellular API
    potato_result=$(scan_potato)
    printf '%s\n' "$potato_result" | write_private_state_file "${STATE_DIR}/potato-scan.json"
    log "Potato scan completed"
    # After scanning, run evaluate to update correlated state
    evaluate >/dev/null
    ;;
  potato)
    result=$(scan_potato)
    printf '%s\n' "$result" | write_private_state_file "${STATE_DIR}/potato-scan.json"
    log "Potato scan completed"
    echo "$result"
    ;;
  crosstown)
    result=$(scan_crosstown)
    printf '%s\n' "$result" | write_private_state_file "${STATE_DIR}/crosstown-scan.json"
    log "Crosstown scan completed"
    # Push state to Mac Mini via Tailscale; capture stderr so failures don't go silent
    push_err=$(echo "$result" | $TAILSCALE file cp --name crosstown-scan.json - dylans-mac-mini: 2>&1 >/dev/null) && \
      log "Pushed crosstown state to Mac Mini via Tailscale" || \
      log "WARN: Failed to push crosstown state to Mac Mini"
    echo "$result"
    ;;
  evaluate)
    evaluate
    ;;
  *)
    log "ERROR: Unknown location '$LOCATION'"
    echo "{\"error\":\"unknown_location\",\"location\":\"$LOCATION\"}"
    exit 1
    ;;
esac
