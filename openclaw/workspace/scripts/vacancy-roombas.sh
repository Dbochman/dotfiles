#!/bin/bash
# vacancy-roombas.sh — Start Roombas when a house becomes vacant
# Triggered by WatchPaths on ~/.openclaw/presence/state.json
#
# Logic:
#   - Reads presence state.json for occupancy
#   - If confirmed_vacant AND not already dispatched → start Roombas
#   - If occupied AND previously dispatched → clear marker (reset)
#   - Marker files prevent re-triggering during same vacancy period

set -euo pipefail

PRESENCE_DIR="$HOME/.openclaw/presence"
STATE_FILE="$PRESENCE_DIR/state.json"
MARKER_DIR="$PRESENCE_DIR/roombas-dispatched"
LOG_FILE="/tmp/vacancy-roombas.log"

# Crosstown Roomba CLI (runs via SSH to MacBook Pro)
CROSSTOWN_ROOMBA="$HOME/.openclaw/skills/crosstown-roomba/crosstown-roomba"

# Cabin Roomba CLI (runs locally via Google Assistant)
CABIN_ROOMBA="$HOME/.openclaw/skills/roomba/roomba"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"
}

mkdir -p "$MARKER_DIR"

if [[ ! -f "$STATE_FILE" ]]; then
  log "ERROR: state.json not found"
  exit 1
fi

# Parse occupancy from state.json
crosstown_occupancy=$(python3 -c "
import json, sys
with open('$STATE_FILE') as f:
    d = json.load(f)
print(d.get('crosstown', {}).get('occupancy', 'unknown'))
" 2>/dev/null || echo "unknown")

cabin_occupancy=$(python3 -c "
import json, sys
with open('$STATE_FILE') as f:
    d = json.load(f)
print(d.get('cabin', {}).get('occupancy', 'unknown'))
" 2>/dev/null || echo "unknown")

log "Check: crosstown=$crosstown_occupancy cabin=$cabin_occupancy"

# --- Crosstown ---
if [[ "$crosstown_occupancy" == "confirmed_vacant" ]] && [[ ! -f "$MARKER_DIR/crosstown" ]]; then
  log "Crosstown confirmed vacant — starting Roombas"
  if "$CROSSTOWN_ROOMBA" start all >> "$LOG_FILE" 2>&1; then
    date > "$MARKER_DIR/crosstown"
    log "Crosstown Roombas started successfully"
  else
    log "ERROR: Failed to start Crosstown Roombas"
  fi
elif [[ "$crosstown_occupancy" == "occupied" ]] && [[ -f "$MARKER_DIR/crosstown" ]]; then
  log "Crosstown occupied again — clearing dispatch marker"
  rm -f "$MARKER_DIR/crosstown"
fi

# --- Cabin ---
if [[ "$cabin_occupancy" == "confirmed_vacant" ]] && [[ ! -f "$MARKER_DIR/cabin" ]]; then
  log "Cabin confirmed vacant — starting Roombas"
  started=0
  if "$CABIN_ROOMBA" start floomba >> "$LOG_FILE" 2>&1; then
    ((started++))
  else
    log "ERROR: Failed to start Floomba"
  fi
  if "$CABIN_ROOMBA" start philly >> "$LOG_FILE" 2>&1; then
    ((started++))
  else
    log "ERROR: Failed to start Philly"
  fi
  if [[ $started -gt 0 ]]; then
    date > "$MARKER_DIR/cabin"
    log "Cabin Roombas started ($started/2)"
  fi
elif [[ "$cabin_occupancy" == "occupied" ]] && [[ -f "$MARKER_DIR/cabin" ]]; then
  log "Cabin occupied again — clearing dispatch marker"
  rm -f "$MARKER_DIR/cabin"
fi
