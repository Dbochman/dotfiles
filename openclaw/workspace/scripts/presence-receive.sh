#!/bin/bash
# presence-receive.sh — Ingest Crosstown presence delivered by Tailscale
#
# The macOS Tailscale app automatically places Taildrop files in ~/Downloads.
# This one-shot receiver is launched by a WatchPaths event, validates the newest
# named scan, atomically promotes it to the canonical presence directory, and
# re-evaluates correlated presence.

set -euo pipefail

LOG_FILE="${PRESENCE_LOG_FILE:-$HOME/.openclaw/logs/presence-detect.log}"
STATE_DIR="${PRESENCE_STATE_DIR:-$HOME/.openclaw/presence}"
DOWNLOAD_DIR="${PRESENCE_DOWNLOAD_DIR:-$HOME/Downloads}"
EVALUATOR="${PRESENCE_EVALUATOR:-$HOME/.openclaw/workspace/scripts/presence-detect.sh}"
CANONICAL_FILE="${STATE_DIR}/crosstown-scan.json"

mkdir -p "$STATE_DIR" "$(dirname "$LOG_FILE")"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
}

crosstown_scan_epoch() {
  /usr/bin/python3 - "$1" <<'PY'
import json
import os
import sys
from datetime import datetime, timedelta, timezone

path = sys.argv[1]
try:
    if os.path.islink(path) or not os.path.isfile(path):
        raise ValueError("not a regular file")
    size = os.path.getsize(path)
    if size <= 0 or size > 1024 * 1024:
        raise ValueError("unexpected file size")
    with open(path, encoding="utf-8") as scan_file:
        scan = json.load(scan_file)
    if not isinstance(scan, dict) or scan.get("location") != "crosstown":
        raise ValueError("not a Crosstown scan")
    timestamp = scan.get("timestamp")
    if not isinstance(timestamp, str):
        raise ValueError("timestamp missing")
    presence = scan.get("presence")
    if not isinstance(presence, dict):
        raise ValueError("presence missing")
    for person in ("Dylan", "Julia"):
        entry = presence.get(person)
        if not isinstance(entry, dict) or not isinstance(entry.get("present"), bool):
            raise ValueError("tracked presence missing")
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    if parsed.astimezone(timezone.utc) > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError("timestamp is in the future")
    print(int(parsed.timestamp() * 1_000_000))
except (OSError, ValueError):
    raise SystemExit(1)
PY
}

is_taildrop_candidate() {
  case "$(basename "$1")" in
    crosstown-scan.json|crosstown-scan\ \([0-9]*\).json) return 0 ;;
    *) return 1 ;;
  esac
}

if [ ! -d "$DOWNLOAD_DIR" ]; then
  log "ERROR: Tailscale download directory is unavailable: $DOWNLOAD_DIR"
  exit 1
fi

shopt -s nullglob
files=()
for candidate in "$DOWNLOAD_DIR"/crosstown-scan*.json; do
  if is_taildrop_candidate "$candidate" && [ -f "$candidate" ] && [ ! -L "$candidate" ]; then
    files+=("$candidate")
  fi
done
shopt -u nullglob
[ "${#files[@]}" -gt 0 ] || exit 0

# Tailscale/macOS adds numeric suffixes when a name already exists. Allow the
# latest arrival a few seconds to become a complete JSON file before selecting
# the greatest embedded scan timestamp from every valid candidate.
latest_arrival="${files[0]}"
for candidate in "${files[@]:1}"; do
  if [ "$candidate" -nt "$latest_arrival" ]; then
    latest_arrival="$candidate"
  fi
done

for attempt in 1 2 3 4 5 6 7 8 9 10; do
  if crosstown_scan_epoch "$latest_arrival" >/dev/null 2>&1; then
    break
  fi
  [ "$attempt" -eq 10 ] || sleep 0.5
done

valid_files=()
newest=""
newest_epoch=""
for candidate in "${files[@]}"; do
  candidate_epoch=$(crosstown_scan_epoch "$candidate" 2>/dev/null || true)
  [ -n "$candidate_epoch" ] || continue
  valid_files+=("$candidate")
  if [ -z "$newest" ] \
    || [ "$candidate_epoch" -gt "$newest_epoch" ] \
    || { [ "$candidate_epoch" -eq "$newest_epoch" ] && [ "$candidate" -nt "$newest" ]; }; then
    newest="$candidate"
    newest_epoch="$candidate_epoch"
  fi
done
if [ -z "$newest" ]; then
  log "WARN: Ignoring incomplete or invalid Taildrop scan: $(basename "$latest_arrival")"
  exit 0
fi

# Never let a delayed or replayed Taildrop move canonical state backwards.
canonical_epoch=$(crosstown_scan_epoch "$CANONICAL_FILE" 2>/dev/null || true)
if [ -n "$canonical_epoch" ] && [ "$newest_epoch" -le "$canonical_epoch" ]; then
  for candidate in "${valid_files[@]}"; do
    rm -f "$candidate"
  done
  log "Discarded ${#valid_files[@]} stale or duplicate Crosstown scan(s) from Downloads"
  exit 0
fi

# Snapshot the selected download inside STATE_DIR, revalidate the snapshot,
# then atomically rename it over canonical state. Source files are removed only
# after promotion succeeds.
stage_file="${STATE_DIR}/.crosstown-scan.$$.json"
trap 'rm -f "$stage_file"' EXIT
cp -p "$newest" "$stage_file"
stage_epoch=$(crosstown_scan_epoch "$stage_file" 2>/dev/null || true)
if [ "$stage_epoch" != "$newest_epoch" ]; then
  log "WARN: Taildrop scan changed during ingestion: $(basename "$newest")"
  exit 0
fi
/usr/bin/xattr -c "$stage_file" 2>/dev/null || true
chmod 600 "$stage_file"
mv -f "$stage_file" "$CANONICAL_FILE"
trap - EXIT

removed=0
for candidate in "${valid_files[@]}"; do
  rm -f "$candidate"
  removed=$((removed + 1))
done

log "Received crosstown-scan.json from Downloads (source $(basename "$newest"); cleaned $removed transfer file(s))"

if [ "${PRESENCE_RECEIVE_EVALUATE:-1}" = "0" ]; then
  log "Presence evaluation deferred by rollout guard"
  exit 0
fi

if ! "$EVALUATOR" evaluate >> "$LOG_FILE" 2>&1; then
  log "ERROR: Presence evaluation failed after Crosstown scan ingestion"
  exit 1
fi
