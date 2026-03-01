#!/bin/bash
# presence-detect.sh — Multi-location presence detection for OpenClaw
#
# Detects who is home at each location by querying local network devices.
#
# Usage:
#   presence-detect.sh [location]
#
#   location: "cabin" or "crosstown" (default: auto-detect based on hostname)
#
# Cabin (Philly):   Starlink gRPC API via grpcurl on Mac Mini (local)
# Crosstown (Boston): ARP scan via SSH to MacBook Pro
#
# Output: JSON to stdout with presence state per person per location.
# Logs: /tmp/presence-detect.log

set -euo pipefail

LOG_FILE="/tmp/presence-detect.log"
NODE="/opt/homebrew/bin/node"
GRPCURL="/opt/homebrew/bin/grpcurl"
STATE_DIR="${HOME}/.openclaw/presence"
STATE_FILE="${STATE_DIR}/state.json"

mkdir -p "$STATE_DIR"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
}

# ── Known devices ────────────────────────────────────────────────────────────

# Cabin (Philly) — matched by device name from Starlink gRPC API
# iPhones use randomized MACs per-network, so we match by name reported to Starlink
CABIN_DEVICES='[
  {"person":"Dylan","match":"name","pattern":"Dylan","require":"iPhone"},
  {"person":"Dylan","match":"name","pattern":"Dylan","require":"phone"},
  {"person":"Julia","match":"name","pattern":"Julia"},
  {"person":"Julia","match":"name_fallback","pattern":"iPhone","excludeNames":["Dylan"]}
]'

# Crosstown (Boston) — matched by MAC address via ARP scan
# Dylan has private WiFi address OFF at Crosstown, so real MAC is known
CROSSTOWN_DEVICES='[
  {"person":"Dylan","match":"mac","pattern":"6c:3a:ff:5f:fc:ba"}
]'

# ── Location detection ───────────────────────────────────────────────────────

detect_location() {
  local hostname
  hostname=$(hostname -s 2>/dev/null || echo "unknown")
  case "$hostname" in
    *mac-mini*|*dylans-mac-mini*) echo "cabin" ;;
    *macbook-pro*) echo "crosstown" ;;
    *) echo "unknown" ;;
  esac
}

LOCATION="${1:-$(detect_location)}"

# ── Cabin: Starlink gRPC API ────────────────────────────────────────────────

scan_cabin() {
  local grpc_response
  grpc_response=$($GRPCURL -plaintext -d '{"wifiGetClients":{}}' \
    192.168.1.1:9000 SpaceX.API.Device.Device/Handle 2>/dev/null || echo '{}')

  if [ "$grpc_response" = "{}" ] || [ -z "$grpc_response" ]; then
    log "ERROR: Starlink gRPC API unreachable"
    echo '{"error":"starlink_unreachable","location":"cabin","clients":[]}'
    return 1
  fi

  # Parse gRPC response and match against known devices using Node.js
  $NODE -e "
const devices = $CABIN_DEVICES;
const response = JSON.parse(process.argv[1]);
const clients = response?.wifiGetClients?.clients || [];

const now = Date.now();
const results = {};

for (const dev of devices) {
  if (results[dev.person]) continue; // already found

  for (const client of clients) {
    const name = (client.name || '').toLowerCase();
    const pattern = dev.pattern.toLowerCase();

    let matched = false;
    if (dev.match === 'name') {
      matched = name.includes(pattern);
      // Require check: name must also contain this string (e.g., require 'iPhone')
      if (matched && dev.require && !name.includes(dev.require.toLowerCase())) {
        matched = false;
      }
    } else if (dev.match === 'name_fallback') {
      // Match by name but exclude devices containing any of excludeNames
      matched = name.includes(pattern);
      if (matched && dev.excludeNames) {
        for (const excl of dev.excludeNames) {
          if (name.includes(excl.toLowerCase())) { matched = false; break; }
        }
        // Also check: is this device already claimed by someone else?
        if (matched) {
          for (const [person, info] of Object.entries(results)) {
            if (info.present && info.mac === (client.macAddress || '').toLowerCase()) {
              matched = false; break;
            }
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

// Mark missing people as absent
for (const dev of devices) {
  if (!results[dev.person]) {
    results[dev.person] = { present: false };
  }
}

const output = {
  location: 'cabin',
  timestamp: new Date().toISOString(),
  totalClients: clients.length,
  presence: results
};

console.log(JSON.stringify(output, null, 2));
" "$grpc_response" 2>/dev/null || echo '{"error":"parse_failed","location":"cabin"}'
}

# ── Crosstown: ARP scan via SSH to MacBook Pro ──────────────────────────────

scan_crosstown() {
  local arp_output

  # Crosstown scan must run ON the MacBook Pro (192.168.165.x subnet).
  # OpenClaw invokes this via: ssh dylans-macbook-pro "~/.openclaw/workspace/scripts/presence-detect.sh crosstown"
  # The Mac Mini cannot SSH to MacBook Pro (1Password agent needs GUI approval).
  # Phase 1: Targeted ping of known device IPs with longer timeout (iPhones sleep)
  # Phase 2: Broader /24 sweep to catch unknown devices
  # Phase 3: Read ARP table
  local known_ips="192.168.165.124"  # Dylan's iPhone
  for ip in $known_ips; do
    ping -c3 -W2 "$ip" >/dev/null 2>&1 &
  done
  for i in $(seq 1 254); do ping -c1 -W1 "192.168.165.$i" >/dev/null 2>&1 & done
  wait
  arp_output=$(arp -a | grep '192.168.165' 2>/dev/null || echo "")

  if [ -z "$arp_output" ]; then
    log "ERROR: ARP scan returned no results"
    echo '{"error":"arp_scan_failed","location":"crosstown"}'
    return 1
  fi

  # Parse ARP output and match against known devices
  $NODE -e "
const devices = $CROSSTOWN_DEVICES;
const arpLines = process.argv[1].split('\n').filter(Boolean);

const results = {};

for (const dev of devices) {
  for (const line of arpLines) {
    // ARP format: hostname (192.168.165.x) at aa:bb:cc:dd:ee:ff on en0 ...
    const macMatch = line.match(/at\s+([0-9a-f:]+)/i);
    const ipMatch = line.match(/\(([0-9.]+)\)/);
    if (!macMatch || !ipMatch) continue;

    const mac = macMatch[1].toLowerCase();
    const ip = ipMatch[1];

    let matched = false;
    if (dev.match === 'mac') {
      matched = mac === dev.pattern.toLowerCase();
    } else if (dev.match === 'name') {
      const nameMatch = line.match(/^(\S+)/);
      matched = nameMatch && nameMatch[1].toLowerCase().includes(dev.pattern.toLowerCase());
    }

    if (matched) {
      results[dev.person] = {
        present: true,
        ip: ip,
        mac: mac,
        device: dev.match === 'mac' ? 'phone (MAC match)' : line.match(/^(\S+)/)?.[1] || 'unknown'
      };
      break;
    }
  }

  if (!results[dev.person]) {
    results[dev.person] = { present: false };
  }
}

const output = {
  location: 'crosstown',
  timestamp: new Date().toISOString(),
  totalDevices: arpLines.length,
  presence: results
};

console.log(JSON.stringify(output, null, 2));
" "$arp_output" 2>/dev/null || echo '{"error":"parse_failed","location":"crosstown"}'
}

# ── Main ─────────────────────────────────────────────────────────────────────

log "Scanning $LOCATION..."

case "$LOCATION" in
  cabin)
    result=$(scan_cabin)
    ;;
  crosstown)
    result=$(scan_crosstown)
    ;;
  all)
    cabin_result=$(scan_cabin 2>/dev/null || echo '{"error":"cabin_scan_failed"}')
    crosstown_result=$(scan_crosstown 2>/dev/null || echo '{"error":"crosstown_scan_failed"}')
    result=$($NODE -e "
const c = JSON.parse(process.argv[1]);
const x = JSON.parse(process.argv[2]);
console.log(JSON.stringify({ locations: [c, x], timestamp: new Date().toISOString() }, null, 2));
" "$cabin_result" "$crosstown_result" 2>/dev/null)
    ;;
  *)
    log "ERROR: Unknown location '$LOCATION'"
    echo "{\"error\":\"unknown_location\",\"location\":\"$LOCATION\"}"
    exit 1
    ;;
esac

# ── Transition detection & state update ──────────────────────────────────────

EVENTS_FILE="${STATE_DIR}/events.json"
PREV_STATE_FILE="${STATE_DIR}/prev-state.json"

# Compare current scan against previous state, detect arrivals/departures
transitions=$($NODE -e "
const fs = require('fs');
const current = JSON.parse(process.argv[1]);
const prevFile = '$PREV_STATE_FILE';
const eventsFile = '$EVENTS_FILE';

let prev = {};
try { prev = JSON.parse(fs.readFileSync(prevFile, 'utf8')); } catch {}

const prevPresence = prev.presence || {};
const currPresence = current.presence || {};
const location = current.location || 'unknown';
const now = new Date().toISOString();

const transitions = [];

for (const [person, curr] of Object.entries(currPresence)) {
  const wasPresentBefore = prevPresence[person]?.present === true;
  const isPresentNow = curr.present === true;

  if (isPresentNow && !wasPresentBefore) {
    transitions.push({ person, event: 'arrived', location, timestamp: now, device: curr.device || '' });
  } else if (!isPresentNow && wasPresentBefore) {
    transitions.push({ person, event: 'departed', location, timestamp: now });
  }
}

// Load existing events (keep last 100)
let events = [];
try { events = JSON.parse(fs.readFileSync(eventsFile, 'utf8')); } catch {}
events.push(...transitions);
events = events.slice(-100);
fs.writeFileSync(eventsFile, JSON.stringify(events, null, 2));

// Save current as previous for next run
fs.writeFileSync(prevFile, JSON.stringify(current, null, 2));

// Output transitions for this run
console.log(JSON.stringify(transitions));
" "$result" 2>/dev/null || echo '[]')

# Update state file with occupancy summary
enriched=$($NODE -e "
const current = JSON.parse(process.argv[1]);
const transitions = JSON.parse(process.argv[2]);

// Add occupancy field: 'occupied' if anyone present, 'vacant' otherwise
const presence = current.presence || {};
const anyoneHome = Object.values(presence).some(p => p.present);
current.occupancy = anyoneHome ? 'occupied' : 'vacant';
current.transitions = transitions;

console.log(JSON.stringify(current, null, 2));
" "$result" "$transitions" 2>/dev/null || echo "$result")

echo "$enriched" > "$STATE_FILE"

# Log transitions
if [ "$transitions" != "[]" ] && [ -n "$transitions" ]; then
  log "TRANSITION: $transitions"
fi
log "Result: $(echo "$enriched" | tr -d '\n' | head -c 500)"

# Output
echo "$enriched"
