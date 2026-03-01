#!/bin/bash
# presence-detect.sh — Multi-location presence detection for OpenClaw
#
# Detects who is home at each location by querying local network devices.
#
# Usage:
#   presence-detect.sh cabin       # Scan cabin WiFi (run on Mac Mini)
#   presence-detect.sh crosstown   # Scan Crosstown LAN (run on MacBook Pro)
#   presence-detect.sh evaluate    # Correlate both locations (run on Mac Mini)
#
# Cabin (Philly):   Starlink gRPC API via grpcurl (Mac Mini, local)
# Crosstown (Boston): ARP scan (MacBook Pro, local)
#
# Vacancy rules:
#   A location is only "confirmed_vacant" when ALL tracked people are
#   absent there AND confirmed present at the other location.
#   Otherwise it's "possibly_vacant" (phones may be sleeping).
#
# Crosstown pushes its state to the Mac Mini via `tailscale file cp`
# after each scan. The Mac Mini's `evaluate` mode reads both.

set -euo pipefail

LOG_FILE="/tmp/presence-detect.log"
NODE="/opt/homebrew/bin/node"
GRPCURL="/opt/homebrew/bin/grpcurl"
TAILSCALE="/usr/local/bin/tailscale"
[ -x "$TAILSCALE" ] || TAILSCALE="/Applications/Tailscale.app/Contents/MacOS/Tailscale"
STATE_DIR="${HOME}/.openclaw/presence"

mkdir -p "$STATE_DIR"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
}

# ── Known devices ────────────────────────────────────────────────────────────

# All tracked people (used by the evaluator)
TRACKED_PEOPLE='["Dylan","Julia"]'

# Cabin (Philly) — matched by device name from Starlink gRPC API
CABIN_DEVICES='[
  {"person":"Dylan","match":"name","pattern":"Dylan","require":"iPhone"},
  {"person":"Dylan","match":"name","pattern":"Dylan","require":"phone"},
  {"person":"Julia","match":"name","pattern":"Julia"},
  {"person":"Julia","match":"name_fallback","pattern":"iPhone","excludeNames":["Dylan"]}
]'

# Crosstown (Boston) — matched by MAC address via ARP scan
CROSSTOWN_DEVICES='[
  {"person":"Dylan","match":"mac","pattern":"6c:3a:ff:5f:fc:ba"}
]'

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

  $NODE -e "
const devices = $CABIN_DEVICES;
const response = JSON.parse(process.argv[1]);
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
" "$grpc_response" 2>/dev/null || echo '{"error":"parse_failed","location":"cabin"}'
}

# ── Crosstown: ARP scan ─────────────────────────────────────────────────────

scan_crosstown() {
  # Targeted ping for known devices (iPhones sleep, need longer timeout)
  local known_ips="192.168.165.124"
  for ip in $known_ips; do
    ping -c3 -W2 "$ip" >/dev/null 2>&1 &
  done
  for i in $(seq 1 254); do ping -c1 -W1 "192.168.165.$i" >/dev/null 2>&1 & done
  wait
  local arp_output
  arp_output=$(arp -a | grep '192.168.165' 2>/dev/null || echo "")

  if [ -z "$arp_output" ]; then
    log "ERROR: ARP scan returned no results"
    echo '{"error":"arp_scan_failed","location":"crosstown"}'
    return 1
  fi

  $NODE -e "
const devices = $CROSSTOWN_DEVICES;
const arpLines = process.argv[1].split('\n').filter(Boolean);
const results = {};

for (const dev of devices) {
  for (const line of arpLines) {
    const macMatch = line.match(/at\s+([0-9a-f:]+)/i);
    const ipMatch = line.match(/\(([0-9.]+)\)/);
    if (!macMatch || !ipMatch) continue;
    const mac = macMatch[1].toLowerCase();
    const ip = ipMatch[1];
    let matched = false;
    if (dev.match === 'mac') matched = mac === dev.pattern.toLowerCase();
    else if (dev.match === 'name') {
      const nm = line.match(/^(\S+)/);
      matched = nm && nm[1].toLowerCase().includes(dev.pattern.toLowerCase());
    }
    if (matched) {
      results[dev.person] = { present: true, ip, mac, device: dev.match === 'mac' ? 'phone (MAC match)' : line.match(/^(\S+)/)?.[1] || 'unknown' };
      break;
    }
  }
  if (!results[dev.person]) results[dev.person] = { present: false };
}

console.log(JSON.stringify({
  location: 'crosstown',
  timestamp: new Date().toISOString(),
  totalDevices: arpLines.length,
  presence: results
}, null, 2));
" "$arp_output" 2>/dev/null || echo '{"error":"parse_failed","location":"crosstown"}'
}

# ── Evaluate: Correlate both locations (runs on Mac Mini) ────────────────────

evaluate() {
  local cabin_state crosstown_state

  # Read cabin state (local, from last cabin scan)
  cabin_state=$(cat "${STATE_DIR}/cabin-scan.json" 2>/dev/null || echo '{}')

  # Read crosstown state (pushed by MacBook Pro via Tailscale)
  crosstown_state=$(cat "${STATE_DIR}/crosstown-scan.json" 2>/dev/null || echo '{}')

  $NODE -e "
const fs = require('fs');
const cabin = JSON.parse(process.argv[1]);
const crosstown = JSON.parse(process.argv[2]);
const tracked = $TRACKED_PEOPLE;

const stateDir = '$STATE_DIR';
const prevFile = stateDir + '/prev-evaluated.json';
const eventsFile = stateDir + '/events.json';

// Load previous evaluated state
let prev = {};
try { prev = JSON.parse(fs.readFileSync(prevFile, 'utf8')); } catch {}

const now = new Date().toISOString();
const cabinPresence = cabin.presence || {};
const crosstownPresence = crosstown.presence || {};

// Staleness check: if a scan is >30 min old, don't trust it for cross-correlation
const cabinAge = cabin.timestamp ? (Date.now() - new Date(cabin.timestamp).getTime()) / 60000 : 999;
const crosstownAge = crosstown.timestamp ? (Date.now() - new Date(crosstown.timestamp).getTime()) / 60000 : 999;
const cabinFresh = cabinAge < 30;
const crosstownFresh = crosstownAge < 30;

// Per-person location
const people = {};
for (const person of tracked) {
  const atCabin = cabinPresence[person]?.present === true;
  const atCrosstown = crosstownPresence[person]?.present === true;
  people[person] = {
    cabin: atCabin,
    crosstown: atCrosstown,
    location: atCabin ? 'cabin' : atCrosstown ? 'crosstown' : 'unknown'
  };
}

// Occupancy per location — vacancy requires ALL tracked people confirmed at the OTHER location
const allAtCrosstown = tracked.every(p => people[p].crosstown);
const allAtCabin = tracked.every(p => people[p].cabin);
const anyAtCabin = tracked.some(p => people[p].cabin);
const anyAtCrosstown = tracked.some(p => people[p].crosstown);
const noneAtCabin = tracked.every(p => !people[p].cabin);
const noneAtCrosstown = tracked.every(p => !people[p].crosstown);

function occupancy(location) {
  if (location === 'cabin') {
    if (anyAtCabin) return 'occupied';
    if (noneAtCabin && allAtCrosstown && crosstownFresh) return 'confirmed_vacant';
    if (noneAtCabin) return 'possibly_vacant';
    return 'unknown';
  } else {
    if (anyAtCrosstown) return 'occupied';
    if (noneAtCrosstown && allAtCabin && cabinFresh) return 'confirmed_vacant';
    if (noneAtCrosstown) return 'possibly_vacant';
    return 'unknown';
  }
}

const cabinOccupancy = occupancy('cabin');
const crosstownOccupancy = occupancy('crosstown');

// Transition detection — compare against previous evaluation
const transitions = [];
const prevCabin = prev.cabin?.occupancy;
const prevCrosstown = prev.crosstown?.occupancy;

if (prevCabin && prevCabin !== cabinOccupancy) {
  transitions.push({ location: 'cabin', from: prevCabin, to: cabinOccupancy, timestamp: now });
}
if (prevCrosstown && prevCrosstown !== crosstownOccupancy) {
  transitions.push({ location: 'crosstown', from: prevCrosstown, to: crosstownOccupancy, timestamp: now });
}

// Per-person transitions
for (const person of tracked) {
  const prevLoc = prev.people?.[person]?.location;
  const currLoc = people[person].location;
  if (prevLoc && prevLoc !== currLoc && currLoc !== 'unknown') {
    transitions.push({ person, event: 'relocated', from: prevLoc, to: currLoc, timestamp: now });
  }
}

// Build result
const result = {
  timestamp: now,
  people,
  cabin: {
    occupancy: cabinOccupancy,
    scanAge: Math.round(cabinAge) + 'min',
    fresh: cabinFresh
  },
  crosstown: {
    occupancy: crosstownOccupancy,
    scanAge: Math.round(crosstownAge) + 'min',
    fresh: crosstownFresh
  },
  transitions
};

// Save state
fs.writeFileSync(prevFile, JSON.stringify(result, null, 2));

// Append transitions to events log
if (transitions.length > 0) {
  let events = [];
  try { events = JSON.parse(fs.readFileSync(eventsFile, 'utf8')); } catch {}
  events.push(...transitions);
  events = events.slice(-100);
  fs.writeFileSync(eventsFile, JSON.stringify(events, null, 2));
}

// Write combined state
fs.writeFileSync(stateDir + '/state.json', JSON.stringify(result, null, 2));

console.log(JSON.stringify(result, null, 2));
" "$cabin_state" "$crosstown_state" 2>/dev/null || echo '{"error":"evaluate_failed"}'
}

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
  cabin)
    result=$(scan_cabin)
    echo "$result" > "${STATE_DIR}/cabin-scan.json"
    log "Cabin scan: $(echo "$result" | tr -d '\n' | head -c 300)"
    # After scanning, run evaluate to update correlated state
    evaluate
    ;;
  crosstown)
    result=$(scan_crosstown)
    echo "$result" > "${STATE_DIR}/crosstown-scan.json"
    log "Crosstown scan: $(echo "$result" | tr -d '\n' | head -c 300)"
    # Push state to Mac Mini via Tailscale
    echo "$result" | $TAILSCALE file cp - dylans-mac-mini: 2>/dev/null && \
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
