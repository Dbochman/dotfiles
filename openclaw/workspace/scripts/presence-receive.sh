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

mkdir -p "$RECV_DIR"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
}

log "Waiting for Tailscale file transfer..."

# Block until a file arrives (--wait required since Tailscale 1.56+)
tailscale file get --wait "$RECV_DIR/" 2>/dev/null

# Take the newest file as the canonical state, delete the rest. Tailscale
# 1.56+ renames stdin pushes to "stdin.txt"; if any prior copy lingers in
# RECV_DIR, the next push gets "refusing to overwrite" and silently jams
# the whole pipeline (this happened once for 9 days, May 1–10 2026).
shopt -s nullglob
files=("${RECV_DIR}"/*)
shopt -u nullglob

if [ "${#files[@]}" -eq 0 ]; then
  log "WARN: tailscale file get returned but RECV_DIR is empty"
  exit 0
fi

# Newest by mtime wins the canonical slot
newest=""
for f in "${files[@]}"; do
  [ -f "$f" ] || continue
  if [ -z "$newest" ] || [ "$f" -nt "$newest" ]; then
    newest="$f"
  fi
done

mv "$newest" "${STATE_DIR}/crosstown-scan.json"
log "Received crosstown-scan.json via Tailscale (from $(basename "$newest"); ${#files[@]} file(s) in queue)"

# Drop any stragglers so the next push doesn't hit name collisions
for f in "${files[@]}"; do
  [ -f "$f" ] && rm -f "$f"
done

# Trigger re-evaluation
"${HOME}/.openclaw/workspace/scripts/presence-detect.sh" evaluate >> "$LOG_FILE" 2>&1 || true
