#!/bin/bash
# bb-watchdog.sh — Detect and fix BlueBubbles chat.db observer stalls
#
# BB's polling loop on chat.db can stall indefinitely on headless Macs.
# This script runs every 5 minutes via LaunchAgent and restarts BB if stalled.
#
# Detection: Track the GUID of the latest message (any sender). If the GUID
# changes (new message exists) but BB hasn't dispatched a webhook for it,
# the chat.db observer has stalled. This avoids false positives when BB is
# simply idle with no new messages.
#
# Safety:
#   - 15-min restart cooldown prevents restart loops
#   - Only restarts when new unprocessed messages exist (not on idle)
#   - Graceful quit before force-kill
#   - Daily log rotation (keeps 7 days)

set -euo pipefail

STATE_DIR="${HOME}/.openclaw/bb-watchdog"
STATE_FILE="${STATE_DIR}/state.json"
LOG_FILE="/tmp/bb-watchdog.log"

# Rotate log daily — keep 7 days
if [[ -f "$LOG_FILE" ]]; then
  log_date=$(stat -f '%Sm' -t '%Y-%m-%d' "$LOG_FILE" 2>/dev/null || echo "")
  today=$(date '+%Y-%m-%d')
  if [[ -n "$log_date" && "$log_date" != "$today" ]]; then
    mv "$LOG_FILE" "${LOG_FILE}.${log_date}"
    # Remove logs older than 7 days
    find /tmp -maxdepth 1 -name 'bb-watchdog.log.*' -mtime +7 -delete 2>/dev/null || true
  fi
fi
NODE="/opt/homebrew/bin/node"
BB_LOG="${HOME}/Library/Logs/bluebubbles-server/main.log"

mkdir -p "$STATE_DIR"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
}

# Load BB password from secrets cache
if [[ -f "${HOME}/.openclaw/.secrets-cache" ]]; then
  set -a
  source "${HOME}/.openclaw/.secrets-cache"
  set +a
fi

BB_URL="http://localhost:1234"
BB_PW="${BLUEBUBBLES_PASSWORD:-}"

if [[ -z "$BB_PW" ]]; then
  log "ERROR: BLUEBUBBLES_PASSWORD not set"
  exit 1
fi

# Check if BB is running
if ! curl -s --max-time 5 "${BB_URL}/api/v1/ping?password=${BB_PW}" > /dev/null 2>&1; then
  log "WARN: BB not reachable, attempting to start"
  open -a BlueBubbles
  exit 0
fi

# Query latest message (any sender) — this is what we track for stall detection
ALL_LATEST_JSON=$(curl -s --max-time 10 -X POST "${BB_URL}/api/v1/message/query?password=${BB_PW}" \
  -H "Content-Type: application/json" \
  -d '{"limit":1,"sort":"DESC"}' 2>/dev/null || echo '{}')

# Parse and decide in one node call
RESULT=$($NODE -e "
const fs = require('fs');
const stateFile = '$STATE_FILE';

// Parse latest message (any sender)
let latestGuid = '', latestDate = 0;
try {
  const aj = JSON.parse(process.argv[1] || '{}');
  latestGuid = aj.data?.[0]?.guid || '';
  latestDate = aj.data?.[0]?.dateCreated || 0;
} catch {}

// Load previous state
// State tracks:
//   allGuid     — GUID of latest message (any sender) last time we checked
//   allSeenAt   — when we first saw that GUID
//   lastRestart — timestamp of last BB restart
let prev = { allGuid: '', allSeenAt: 0, lastRestart: 0 };
try {
  if (fs.existsSync(stateFile)) {
    const raw = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
    // Migrate from old state format
    prev.allGuid = raw.allGuid || raw.guid || '';
    prev.allSeenAt = raw.allSeenAt || raw.seenAt || 0;
    prev.lastRestart = raw.lastRestart || 0;
  }
} catch {}

const now = Date.now();
const msgAgeMin = latestDate > 0 ? Math.floor((now - latestDate) / 60000) : 999;
const guidChanged = latestGuid && latestGuid !== prev.allGuid;
const sinceRestart = prev.lastRestart ? now - Number(prev.lastRestart) : Infinity;
const inCooldown = sinceRestart < 900000; // 15 min

// Check BB log for most recent webhook dispatch timestamp
let webhookAgeMin = 999;
try {
  const logContent = fs.readFileSync('$BB_LOG', 'utf8');
  const lines = logContent.split('\n');
  for (let i = lines.length - 1; i >= 0; i--) {
    if (lines[i].includes('WebhookService') && lines[i].includes('Dispatching')) {
      const match = lines[i].match(/\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})/);
      if (match) {
        const ts = new Date(match[1].replace(' ', 'T'));
        webhookAgeMin = Math.floor((now - ts.getTime()) / 60000);
      }
      break;
    }
  }
} catch {}

// Decision logic:
// 1. If we can't reach BB API → skip
// 2. If GUID changed AND webhook was dispatched recently → new message processed, all good
// 3. If GUID changed AND no recent webhook → STALL (new message exists but BB didn't webhook it)
// 4. If GUID unchanged → no new messages, idle (regardless of how old the last message is)
let action = 'ok';
let reason = '';
let saveState = false;

if (!latestGuid) {
  action = 'skip';
  reason = 'could not fetch latest message from BB API';
} else if (guidChanged) {
  // New message appeared since last check
  const timeSinceMsg = latestDate > 0 ? Math.floor((now - latestDate) / 60000) : 0;

  if (webhookAgeMin <= 5) {
    // Webhook fired recently — BB processed it fine
    action = 'ok';
    reason = 'new message detected and webhooks recent (' + webhookAgeMin + 'min ago, guid=' + latestGuid.substring(0, 12) + '...)';
    saveState = true;
  } else if (timeSinceMsg <= 2) {
    // Message is very fresh — give BB a moment to process it
    action = 'ok';
    reason = 'new message just arrived (' + timeSinceMsg + 'min ago), waiting for webhook';
    saveState = true;
  } else if (inCooldown) {
    action = 'skip';
    reason = 'new message but within restart cooldown (' + Math.floor(sinceRestart / 60000) + 'min since last restart)';
    saveState = true;
  } else {
    // New message exists, it's not brand new, and BB hasn't webhoked it → stall
    action = 'restart';
    reason = 'new message (' + timeSinceMsg + 'min old) but no webhook dispatched (last webhook ' + webhookAgeMin + 'min ago)';
    saveState = true;
  }
} else {
  // Same GUID as last check — no new messages, BB is idle
  action = 'ok';
  const idleMin = prev.allSeenAt ? Math.floor((now - Number(prev.allSeenAt)) / 60000) : msgAgeMin;
  reason = 'idle, no new messages (last new msg ' + idleMin + 'min ago)';
}

// Save state when GUID changes
if (saveState) {
  prev.allGuid = latestGuid;
  prev.allSeenAt = now;
  fs.writeFileSync(stateFile, JSON.stringify(prev, null, 2));
}

console.log([action, reason, msgAgeMin, webhookAgeMin, latestGuid].join('|'));
" "$ALL_LATEST_JSON" 2>/dev/null || echo "error|node failed|0|0|")

ACTION=$(echo "$RESULT" | cut -d'|' -f1)
REASON=$(echo "$RESULT" | cut -d'|' -f2)
MSG_AGE=$(echo "$RESULT" | cut -d'|' -f3)
WEBHOOK_AGE=$(echo "$RESULT" | cut -d'|' -f4)
GUID=$(echo "$RESULT" | cut -d'|' -f5)

case "$ACTION" in
  ok)
    log "OK: ${REASON}"
    ;;
  skip)
    log "SKIP: ${REASON}"
    ;;
  restart)
    log "STALL DETECTED: ${REASON}"
    log "ACTION: Restarting BlueBubbles..."

    # Graceful quit
    osascript -e 'tell application "BlueBubbles" to quit' 2>/dev/null || true
    sleep 5

    # Force-kill if still running
    if pgrep -xq "BlueBubbles"; then
      pkill -x "BlueBubbles" 2>/dev/null || true
      sleep 2
    fi

    open -a BlueBubbles
    log "ACTION: BlueBubbles restarted"

    # Record restart in state
    $NODE -e "
const fs = require('fs');
const stateFile = '$STATE_FILE';
let prev = {};
try { prev = JSON.parse(fs.readFileSync(stateFile, 'utf8')); } catch {}
prev.lastRestart = Date.now();
fs.writeFileSync(stateFile, JSON.stringify(prev, null, 2));
" 2>/dev/null
    ;;
  error)
    log "ERROR: ${REASON}"
    ;;
esac
