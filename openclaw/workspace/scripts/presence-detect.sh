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

# Tracked people per location — vacancy requires all tracked people for THAT
# location to be absent AND confirmed at the other location.
CABIN_TRACKED='["Dylan","Julia"]'
CROSSTOWN_TRACKED='["Dylan","Julia"]'

# Cabin (Philly) — matched by device name from Starlink gRPC API
CABIN_DEVICES='[
  {"person":"Dylan","match":"name","pattern":"Dylan","require":"iPhone"},
  {"person":"Dylan","match":"name","pattern":"Dylan","require":"phone"},
  {"person":"Julia","match":"name","pattern":"Julia"},
  {"person":"Julia","match":"name_fallback","pattern":"iPhone","excludeNames":["Dylan"]}
]'

# Crosstown (Boston) — matched by MAC address via ARP scan
CROSSTOWN_DEVICES='[
  {"person":"Dylan","match":"mac","pattern":"6c:3a:ff:5f:fc:ba"},
  {"person":"Julia","match":"mac","pattern":"38:e1:3d:c0:40:63"},
  {"person":"Julia","match":"ip","pattern":"192.168.165.248"},
  {"person":"Julia","match":"hostname","pattern":"julias-iphone"}
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
  local known_ips="192.168.165.124 192.168.165.248"
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
    else if (dev.match === 'ip') matched = ip === dev.pattern;
    else if (dev.match === 'name' || dev.match === 'hostname') {
      const nm = line.match(/^(\S+)/);
      matched = nm && nm[1].toLowerCase().includes(dev.pattern.toLowerCase());
    }
    if (matched) {
      results[dev.person] = { present: true, ip, mac, device: dev.match === 'mac' ? 'phone (MAC match)' : dev.match === 'ip' ? 'phone (IP match)' : line.match(/^(\S+)/)?.[1] || 'unknown' };
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
// Per-location tracked people — vacancy requires all tracked people for THAT
// location to be absent AND confirmed at the other location.
const cabinTracked = $CABIN_TRACKED;
const crosstownTracked = $CROSSTOWN_TRACKED;
const allTracked = [...new Set([...cabinTracked, ...crosstownTracked])];

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

// Per-person location — sticky: once detected at a location, stay there
// until positively detected at the OTHER location (arrival-based model).
const people = {};
for (const person of allTracked) {
  const seenAtCabin = cabinPresence[person]?.present === true;
  const seenAtCrosstown = crosstownPresence[person]?.present === true;
  const prevLoc = prev.people?.[person]?.location || 'unknown';

  let location;
  if (seenAtCabin && seenAtCrosstown) {
    // Seen at both — unusual, pick based on scan freshness
    location = cabinAge <= crosstownAge ? 'cabin' : 'crosstown';
  } else if (seenAtCabin) {
    location = 'cabin';
  } else if (seenAtCrosstown) {
    location = 'crosstown';
  } else {
    // Not detected anywhere this scan — keep previous location (sticky)
    location = prevLoc;
  }

  people[person] = {
    cabin: location === 'cabin',
    crosstown: location === 'crosstown',
    location
  };
}

// Occupancy per location — uses that location's tracked list
// Cabin vacancy: all cabin-tracked people absent at cabin AND present at crosstown
// Crosstown vacancy: all crosstown-tracked people absent at crosstown AND present at cabin
function occupancy(location) {
  const tracked = location === 'cabin' ? cabinTracked : crosstownTracked;
  const otherFresh = location === 'cabin' ? crosstownFresh : cabinFresh;
  const here = location === 'cabin' ? 'cabin' : 'crosstown';
  const there = location === 'cabin' ? 'crosstown' : 'cabin';

  const anyHere = tracked.some(p => people[p]?.[here]);
  const noneHere = tracked.every(p => !people[p]?.[here]);
  const allThere = tracked.every(p => people[p]?.[there]);

  if (anyHere) return 'occupied';
  if (noneHere && allThere && otherFresh) return 'confirmed_vacant';
  if (noneHere) return 'possibly_vacant';
  return 'unknown';
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

// Per-person transitions — only fire when positively detected at a NEW location
// (not when sticky-held at previous location)
for (const person of allTracked) {
  const prevLoc = prev.people?.[person]?.location;
  const currLoc = people[person].location;
  const actuallyDetected = (cabinPresence[person]?.present === true) || (crosstownPresence[person]?.present === true);
  if (prevLoc && prevLoc !== currLoc && currLoc !== 'unknown' && actuallyDetected) {
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

// Append to presence history JSONL (date-partitioned, mirrors nest-history pattern)
const histDir = stateDir + '/history';
try { fs.mkdirSync(histDir, { recursive: true }); } catch {}
const dayKey = now.slice(0, 10); // YYYY-MM-DD
const histRecord = JSON.stringify({
  timestamp: now,
  cabin: { occupancy: cabinOccupancy, people: allTracked.filter(p => people[p]?.cabin) },
  crosstown: { occupancy: crosstownOccupancy, people: allTracked.filter(p => people[p]?.crosstown) }
});
fs.appendFileSync(histDir + '/' + dayKey + '.jsonl', histRecord + '\n');

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
