#!/bin/bash
# vacancy-actions.sh — Automated actions when a house becomes vacant
# Triggered by WatchPaths on ~/.openclaw/presence/state.json
#
# When confirmed_vacant:
#   - Turn off all lights
#   - Set thermostat to eco
#   - Turn off Cielo minisplits (Crosstown)
#   - Turn off Eight Sleep Pod (Crosstown)
#   - Start Roombas
#
# When occupied again:
#   - Restore Eight Sleep Pod (resume smart schedule)
#   - Clear markers (reset for next vacancy)
#   - (Welcome home actions handled by crosstown-routines/cabin-routines skills)

set -euo pipefail

PRESENCE_DIR="$HOME/.openclaw/presence"
STATE_FILE="$PRESENCE_DIR/state.json"
MARKER_DIR="$PRESENCE_DIR/vacancy-dispatched"
LOG_FILE="/tmp/vacancy-actions.log"

# All CLIs resolved via PATH (~/.openclaw/bin + /opt/homebrew/bin)

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
import json
with open('$STATE_FILE') as f:
    d = json.load(f)
print(d.get('crosstown', {}).get('occupancy', 'unknown'))
" 2>/dev/null || echo "unknown")

cabin_occupancy=$(python3 -c "
import json
with open('$STATE_FILE') as f:
    d = json.load(f)
print(d.get('cabin', {}).get('occupancy', 'unknown'))
" 2>/dev/null || echo "unknown")

log "Check: crosstown=$crosstown_occupancy cabin=$cabin_occupancy"

# --- Crosstown vacancy ---
if [[ "$crosstown_occupancy" == "confirmed_vacant" ]] && [[ ! -f "$MARKER_DIR/crosstown" ]]; then
  log "Crosstown confirmed vacant — running vacancy actions"

  # Lights off
  if hue --crosstown all-off >> "$LOG_FILE" 2>&1; then
    log "  Crosstown lights: OFF"
  else
    log "  ERROR: Failed to turn off Crosstown lights"
  fi

  # Thermostat eco
  if nest eco crosstown on >> "$LOG_FILE" 2>&1; then
    log "  Crosstown thermostat: ECO"
  else
    log "  ERROR: Failed to set Crosstown eco mode"
  fi

  # Cielo minisplits off
  for unit in bedroom office "living room"; do
    if cielo off -d "$unit" >> "$LOG_FILE" 2>&1; then
      log "  Cielo $unit: OFF"
    else
      log "  ERROR: Failed to turn off Cielo $unit"
    fi
  done

  # Eight Sleep Pod off
  for side in dylan julia; do
    if 8sleep off "$side" >> "$LOG_FILE" 2>&1; then
      log "  Eight Sleep $side: OFF"
    else
      log "  ERROR: Failed to turn off Eight Sleep $side"
    fi
  done

  # Start Roombas
  if crosstown-roomba start all >> "$LOG_FILE" 2>&1; then
    log "  Crosstown Roombas: STARTED"
  else
    log "  ERROR: Failed to start Crosstown Roombas (may be offline)"
  fi

  date > "$MARKER_DIR/crosstown"
  log "Crosstown vacancy actions complete"

elif [[ "$crosstown_occupancy" == "occupied" ]] && [[ -f "$MARKER_DIR/crosstown" ]]; then
  log "Crosstown occupied again — restoring Eight Sleep and clearing vacancy marker"

  # Eight Sleep Pod back on (resume smart schedule)
  for side in dylan julia; do
    if 8sleep on "$side" >> "$LOG_FILE" 2>&1; then
      log "  Eight Sleep $side: ON"
    else
      log "  ERROR: Failed to turn on Eight Sleep $side"
    fi
  done

  rm -f "$MARKER_DIR/crosstown"
fi

# --- Cabin vacancy ---
if [[ "$cabin_occupancy" == "confirmed_vacant" ]] && [[ ! -f "$MARKER_DIR/cabin" ]]; then
  log "Cabin confirmed vacant — running vacancy actions"

  # Lights off
  if hue --cabin all-off >> "$LOG_FILE" 2>&1; then
    log "  Cabin lights: OFF"
  else
    log "  ERROR: Failed to turn off Cabin lights"
  fi

  # Thermostat eco
  if nest eco cabin on >> "$LOG_FILE" 2>&1; then
    log "  Cabin thermostat: ECO"
  else
    log "  ERROR: Failed to set Cabin eco mode"
  fi

  # Start Roombas
  started=0
  if roomba start floomba >> "$LOG_FILE" 2>&1; then
    ((started++))
  else
    log "  ERROR: Failed to start Floomba"
  fi
  if roomba start philly >> "$LOG_FILE" 2>&1; then
    ((started++))
  else
    log "  ERROR: Failed to start Philly"
  fi
  log "  Cabin Roombas: STARTED ($started/2)"

  date > "$MARKER_DIR/cabin"
  log "Cabin vacancy actions complete"

elif [[ "$cabin_occupancy" == "occupied" ]] && [[ -f "$MARKER_DIR/cabin" ]]; then
  log "Cabin occupied again — clearing vacancy marker"
  rm -f "$MARKER_DIR/cabin"
fi
