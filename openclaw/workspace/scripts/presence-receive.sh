#!/bin/bash
# presence-receive.sh — Receive Crosstown presence state via Tailscale file transfer
#
# Runs on Mac Mini. Tailscale `file get` blocks until a file arrives,
# then moves it to the presence state directory and re-evaluates.
#
# Called by com.openclaw.presence-receive LaunchAgent (KeepAlive).

set -euo pipefail

LOG_FILE="$HOME/.openclaw/logs/presence-detect.log"
STATE_DIR="${HOME}/.openclaw/presence"
RECV_DIR="${STATE_DIR}/incoming"

mkdir -p "$RECV_DIR" "$(dirname "$LOG_FILE")"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
}

process_queue() {
  local files newest f

  shopt -s nullglob
  files=("${RECV_DIR}"/*)
  shopt -u nullglob
  [ "${#files[@]}" -gt 0 ] || return 1

  # Newest by mtime wins the canonical slot.
  newest=""
  for f in "${files[@]}"; do
    [ -f "$f" ] || continue
    if [ -z "$newest" ] || [ "$f" -nt "$newest" ]; then
      newest="$f"
    fi
  done
  [ -n "$newest" ] || return 1

  mv -f "$newest" "${STATE_DIR}/crosstown-scan.json"
  log "Received crosstown-scan.json via Tailscale (from $(basename "$newest"); ${#files[@]} file(s) in queue)"

  # Drop stragglers so the next stdin.txt delivery cannot collide.
  for f in "${files[@]}"; do
    [ -f "$f" ] && rm -f "$f"
  done

  "${HOME}/.openclaw/workspace/scripts/presence-detect.sh" evaluate >> "$LOG_FILE" 2>&1 || true
}

# Recover an inbound file left behind by a prior crash before asking Tailscale
# to write another stdin.txt into the same directory.
if process_queue; then
  log "Recovered queued presence transfer before entering receive wait"
fi

log "Waiting for Tailscale file transfer..."

# Block until a file arrives (--wait required since Tailscale 1.56+). Capture
# CLI output so a retryable error cannot flood launchd's stdout in a tight loop.
if ! receive_output=$(tailscale file get --wait "$RECV_DIR/" 2>&1); then
  summary=$(printf '%s' "$receive_output" | tr '\n' ' ' | cut -c1-300)
  log "ERROR: tailscale file get failed: ${summary:-unknown error}"
  sleep 30
  exit 1
fi

if ! process_queue; then
  log "WARN: tailscale file get returned but RECV_DIR is empty"
fi
