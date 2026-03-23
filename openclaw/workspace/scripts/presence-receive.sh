#!/bin/bash
# presence-receive.sh — Receive Crosstown presence state via Tailscale file transfer
#
# Runs on Mac Mini. Tailscale `file get` blocks until a file arrives,
# then moves it to the presence state directory and re-evaluates.
#
# Called by com.openclaw.presence-receive LaunchAgent (KeepAlive).

set -euo pipefail

LOG_FILE="/tmp/presence-detect.log"
STATE_DIR="${HOME}/.openclaw/presence"
RECV_DIR="${STATE_DIR}/incoming"
NODE="/opt/homebrew/bin/node"

mkdir -p "$RECV_DIR"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
}

log "Waiting for Tailscale file transfer..."

# Block until a file arrives (--wait required since Tailscale 1.56+)
tailscale file get --wait "$RECV_DIR/" 2>/dev/null

# The Crosstown script sends via stdin, which arrives as a file named "stdin"
if [ -f "${RECV_DIR}/stdin" ]; then
  mv "${RECV_DIR}/stdin" "${STATE_DIR}/crosstown-scan.json"
  log "Received crosstown-scan.json via Tailscale"

  # Trigger re-evaluation
  "${HOME}/.openclaw/workspace/scripts/presence-detect.sh" evaluate >> "$LOG_FILE" 2>&1 || true
else
  # Check for any other files
  for f in "${RECV_DIR}"/*; do
    [ -f "$f" ] || continue
    mv "$f" "${STATE_DIR}/crosstown-scan.json"
    log "Received crosstown-scan.json via Tailscale (from $(basename "$f"))"
    "${HOME}/.openclaw/workspace/scripts/presence-detect.sh" evaluate >> "$LOG_FILE" 2>&1 || true
    break
  done
fi
