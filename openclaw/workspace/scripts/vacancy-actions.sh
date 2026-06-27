#!/bin/bash
# vacancy-actions.sh — Automated actions when a house becomes vacant
# Triggered by WatchPaths on ~/.openclaw/presence/state.json
#
# When confirmed_vacant:
#   - Turn off all lights
#   - Set thermostat to eco
#   - Turn off Cielo minisplits (Crosstown)
#   - Enable Eight Sleep away mode (Crosstown)
#   - Start Roombas
#
# When occupied again:
#   - End Eight Sleep away mode (resume smart schedule)
#   - Clear markers (reset for next vacancy)
#   - (Welcome home actions handled by crosstown-routines/cabin-routines skills)

set -euo pipefail

PRESENCE_DIR="$HOME/.openclaw/presence"
STATE_FILE="$PRESENCE_DIR/state.json"
MARKER_DIR="$PRESENCE_DIR/vacancy-dispatched"
LOG_FILE="$HOME/.openclaw/logs/vacancy-actions.log"

# All CLIs resolved via PATH (~/.openclaw/bin + /opt/homebrew/bin)

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"
}

# Load cached credentials used by the device helper CLIs — no live op reads.
SECRETS_FILE="$HOME/.openclaw/.secrets-cache"
if [[ -f "$SECRETS_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$SECRETS_FILE"
  set +a
fi

IMSG_BIN="${IMSG_BIN:-/opt/homebrew/bin/imsg}"
DYLAN_CHAT_ID="${DYLAN_CHAT_ID:-171}"

_send_imessage() {
  local msg="$1"
  if [[ ! -x "$IMSG_BIN" ]]; then
    log "  WARN: imsg not executable at $IMSG_BIN, skipping iMessage"
    return 0
  fi

  if "$IMSG_BIN" send \
    --chat-id "$DYLAN_CHAT_ID" \
    --service imessage \
    --text "$msg" \
    --json >> "$LOG_FILE" 2>&1; then
    log "  iMessage notification: SENT via native imsg"
  else
    log "  WARN: native imsg notification failed"
  fi
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

  # Eight Sleep Pod — enable away mode (extended absence)
  for side in dylan julia; do
    if 8sleep away "$side" start >> "$LOG_FILE" 2>&1; then
      log "  Eight Sleep $side: AWAY MODE ON"
    else
      log "  ERROR: Failed to enable Eight Sleep $side away mode"
    fi
  done

  # Lock front door
  lock_output=$(august status 2>&1) || true
  lock_state=$(echo "$lock_output" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('state',{}).get('locked','unknown'))" 2>/dev/null || echo "unknown")
  if [[ "$lock_state" == "True" ]]; then
    log "  Front door: ALREADY LOCKED"
    _send_imessage "🔒 Crosstown vacant — front door was already locked"
  else
    if lock_result=$(august lock 2>&1); then
      locked=$(echo "$lock_result" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('state',{}).get('locked',False))" 2>/dev/null || echo "False")
      if [[ "$locked" == "True" ]]; then
        log "  Front door: LOCKED"
        _send_imessage "🔒 Crosstown vacant — front door locked automatically"
      else
        log "  ERROR: Lock command succeeded but door not confirmed locked"
        _send_imessage "⚠️ Crosstown vacant — lock command sent but could not confirm door is locked"
      fi
    else
      log "  ERROR: Failed to lock front door"
      _send_imessage "🚨 Crosstown vacant — FAILED to lock front door! Please check manually"
    fi
  fi

  # Start Roombas
  if crosstown-roomba start all >> "$LOG_FILE" 2>&1; then
    log "  Crosstown Roombas: STARTED"
  else
    log "  ERROR: Failed to start Crosstown Roombas (may be offline)"
  fi

  date > "$MARKER_DIR/crosstown"
  log "Crosstown vacancy actions complete"

elif [[ "$crosstown_occupancy" == "occupied" ]] && [[ -f "$MARKER_DIR/crosstown" ]]; then
  log "Crosstown occupied again — ending Eight Sleep away mode and clearing vacancy marker"

  # End Eight Sleep away mode (resume smart schedule)
  for side in dylan julia; do
    if 8sleep away "$side" end >> "$LOG_FILE" 2>&1; then
      log "  Eight Sleep $side: AWAY MODE OFF"
    else
      log "  ERROR: Failed to end Eight Sleep $side away mode"
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
