#!/bin/bash
# bb-watchdog.sh — Detect and fix BlueBubbles chat.db observer stalls
#
# BB's polling loop on chat.db can stall indefinitely on headless Macs.
# This script runs every 5 minutes via LaunchAgent and restarts BB if stalled.
#
# Detection: Compare BB's "latest message" API response against what the
# gateway last processed. If BB's API shows messages that were never
# webhoked (BB log has no recent Dispatching entries), BB's observer stalled.
#
# Safety:
#   - 15-min restart cooldown prevents restart loops
#   - Idle detection: no restart if newest message is >15 min old
#   - Graceful quit before force-kill

set -euo pipefail

STATE_DIR="${HOME}/.openclaw/bb-watchdog"
STATE_FILE="${STATE_DIR}/state.json"
LOG_FILE="/tmp/bb-watchdog.log"
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

# Query latest incoming message (not from me)
LATEST_JSON=$(curl -s --max-time 10 -X POST "${BB_URL}/api/v1/message/query?password=${BB_PW}" \
  -H "Content-Type: application/json" \
  -d '{"limit":1,"sort":"DESC","where":[{"statement":"message.is_from_me = :val","args":{"val":0}}]}' 2>/dev/null || echo '{}')

# Query absolute latest message (any sender) for freshness check
ALL_LATEST_JSON=$(curl -s --max-time 10 -X POST "${BB_URL}/api/v1/message/query?password=${BB_PW}" \
  -H "Content-Type: application/json" \
  -d '{"limit":1,"sort":"DESC"}' 2>/dev/null || echo '{}')

# Parse everything in one node call for efficiency and correctness
RESULT=$($NODE -e "
const fs = require('fs');
const stateFile = '$STATE_FILE';

// Parse API responses
let latestGuid = '', latestDate = 0, allLatestDate = 0;
try {
  const lj = JSON.parse(process.argv[1] || '{}');
  latestGuid = lj.data?.[0]?.guid || '';
  latestDate = lj.data?.[0]?.dateCreated || 0;
} catch {}
try {
  const aj = JSON.parse(process.argv[2] || '{}');
  allLatestDate = aj.data?.[0]?.dateCreated || 0;
} catch {}

// Load previous state
let prev = { guid: '', seenAt: 0, lastRestart: 0 };
try {
  if (fs.existsSync(stateFile)) {
    prev = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
  }
} catch {}

const now = Date.now();
const msgAgeMin = allLatestDate > 0 ? Math.floor((now - allLatestDate) / 60000) : 999;

// Check BB log for most recent webhook dispatch timestamp
let webhookAgeMin = 999;
try {
  const logContent = fs.readFileSync('$BB_LOG', 'utf8');
  const lines = logContent.split('\n');
  for (let i = lines.length - 1; i >= 0; i--) {
    if (lines[i].includes('WebhookService') && lines[i].includes('Dispatching')) {
      // Extract timestamp: [2026-03-01 16:05:43.808]
      const match = lines[i].match(/\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})/);
      if (match) {
        const ts = new Date(match[1].replace(' ', 'T'));
        webhookAgeMin = Math.floor((now - ts.getTime()) / 60000);
      }
      break;
    }
  }
} catch {}

// Decision
const guidChanged = latestGuid && latestGuid !== prev.guid;
const sinceRestart = prev.lastRestart ? now - Number(prev.lastRestart) : Infinity;
const inCooldown = sinceRestart < 900000; // 15 min

let action = 'ok';
let reason = '';

if (!latestGuid) {
  action = 'skip';
  reason = 'could not fetch latest message from BB API';
} else if (guidChanged) {
  action = 'ok';
  reason = 'new incoming message detected (guid=' + latestGuid.substring(0, 20) + '...)';
  // Update state
  prev.guid = latestGuid;
  prev.seenAt = now;
} else if (inCooldown) {
  action = 'skip';
  reason = 'within restart cooldown (' + Math.floor(sinceRestart / 60000) + 'min since last)';
} else if (msgAgeMin > 15) {
  action = 'ok';
  reason = 'no recent messages (' + msgAgeMin + 'min old), probably idle';
} else if (webhookAgeMin > 10) {
  action = 'restart';
  reason = 'messages are ' + msgAgeMin + 'min old but last webhook was ' + webhookAgeMin + 'min ago';
} else {
  action = 'ok';
  reason = 'webhooks are recent (' + webhookAgeMin + 'min ago)';
}

// Save state on guid change
if (guidChanged) {
  fs.writeFileSync(stateFile, JSON.stringify(prev, null, 2));
}

// Output for bash: action|reason|staleMin|msgAgeMin|webhookAgeMin
const staleMin = prev.seenAt ? Math.floor((now - Number(prev.seenAt)) / 60000) : 0;
console.log([action, reason, staleMin, msgAgeMin, webhookAgeMin, latestGuid].join('|'));
" "$LATEST_JSON" "$ALL_LATEST_JSON" 2>/dev/null || echo "error|node failed|0|0|0|")

ACTION=$(echo "$RESULT" | cut -d'|' -f1)
REASON=$(echo "$RESULT" | cut -d'|' -f2)
STALE_MIN=$(echo "$RESULT" | cut -d'|' -f3)
MSG_AGE=$(echo "$RESULT" | cut -d'|' -f4)
WEBHOOK_AGE=$(echo "$RESULT" | cut -d'|' -f5)
GUID=$(echo "$RESULT" | cut -d'|' -f6)

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
