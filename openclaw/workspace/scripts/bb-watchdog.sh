#!/bin/bash
# bb-watchdog.sh — Detect and fix BlueBubbles chat.db observer stalls
#
# BB's polling loop on chat.db can stall indefinitely on headless Macs.
# This script runs every 1 minute via LaunchAgent and restarts BB if stalled.
#
# Detection: Track the GUID of the latest message (any sender). If the GUID
# changes (new message exists) but BB hasn't dispatched a webhook for it,
# the chat.db observer has stalled. This avoids false positives when BB is
# simply idle with no new messages.
#
# Safety:
#   - 15-min restart cooldown prevents restart loops
#   - Poke-first recovery (restart only after repeated unresolved lag)
#   - Graceful quit before force-kill
#   - Daily log rotation (keeps 7 days)

set -euo pipefail

STATE_DIR="${HOME}/.openclaw/bb-watchdog"
STATE_FILE="${STATE_DIR}/state.json"
LOG_FILE="/tmp/bb-watchdog.log"
LAG_METRICS_FILE="/tmp/bb-ingest-lag.log"
LAG_ALERT_SEC="${BB_INGEST_LAG_ALERT_SEC:-90}"
POKE_SCRIPT="${HOME}/.openclaw/workspace/scripts/poke-messages.scpt"
POKE_RETRY_THRESHOLD="${BB_POKE_RETRY_THRESHOLD:-3}"

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

const lagAlertSec = Number(process.argv[2] || 90);

// Parse latest message (any sender)
let latestGuid = '', latestDate = 0;
try {
  const aj = JSON.parse(process.argv[1] || '{}');
  latestGuid = aj.data?.[0]?.guid || '';
  latestDate = aj.data?.[0]?.dateCreated || 0;
} catch {}

// Load previous state
// State tracks:
//   allGuid        — GUID of latest message (any sender) last time we checked
//   allSeenAt      — when we first saw that GUID
//   lastRestart    — timestamp of last BB restart
//   pendingGuid    — guid currently suspected as delayed
//   pendingChecks  — consecutive checks with unresolved lag on pendingGuid
let prev = { allGuid: '', allSeenAt: 0, lastRestart: 0, pendingGuid: '', pendingChecks: 0 };
try {
  if (fs.existsSync(stateFile)) {
    const raw = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
    // Migrate from old state format
    prev.allGuid = raw.allGuid || raw.guid || '';
    prev.allSeenAt = raw.allSeenAt || raw.seenAt || 0;
    prev.lastRestart = raw.lastRestart || 0;
    prev.pendingGuid = raw.pendingGuid || '';
    prev.pendingChecks = Number(raw.pendingChecks || 0);
  }
} catch {}

const now = Date.now();
const msgAgeMin = latestDate > 0 ? Math.floor((now - latestDate) / 60000) : 999;
const msgAgeSec = latestDate > 0 ? Math.floor((now - latestDate) / 1000) : 999999;
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
const pokeRetryThreshold = Number(process.argv[3] || 3);

let action = 'ok';
let reason = '';
let saveState = false;
let lagAlert = false;
const newMsgNeedsAttention = (webhookAgeMin > 1) && (msgAgeSec >= lagAlertSec);

if (!latestGuid) {
  action = 'skip';
  reason = 'could not fetch latest message from BB API';
} else if (guidChanged) {
  // New message appeared since last check.
  const timeSinceMsg = latestDate > 0 ? Math.floor((now - latestDate) / 60000) : 0;
  lagAlert = msgAgeSec >= lagAlertSec;
  prev.allGuid = latestGuid;
  prev.allSeenAt = now;
  saveState = true;

  if (!newMsgNeedsAttention) {
    action = 'ok';
    reason = 'new message detected and webhooks recent (' + webhookAgeMin + 'min ago, guid=' + latestGuid.substring(0, 12) + '...)';
    prev.pendingGuid = '';
    prev.pendingChecks = 0;
  } else if (inCooldown) {
    action = 'skip';
    reason = 'new delayed message but within restart cooldown (' + Math.floor(sinceRestart / 60000) + 'min since last restart)';
    prev.pendingGuid = latestGuid;
    prev.pendingChecks = 1;
  } else {
    action = 'poke';
    reason = 'new delayed message (' + timeSinceMsg + 'min old), attempting Messages poke before restart';
    prev.pendingGuid = latestGuid;
    prev.pendingChecks = 1;
  }
} else if (prev.pendingGuid && latestGuid === prev.pendingGuid) {
  // Same delayed guid is still unresolved.
  lagAlert = msgAgeSec >= lagAlertSec;

  if (!newMsgNeedsAttention) {
    action = 'ok';
    reason = 'pending guid resolved without restart';
    prev.pendingGuid = '';
    prev.pendingChecks = 0;
    saveState = true;
  } else if (inCooldown) {
    action = 'skip';
    reason = 'pending delayed message still within restart cooldown (' + Math.floor(sinceRestart / 60000) + 'min since last restart)';
    saveState = false;
  } else {
    const checks = Number(prev.pendingChecks || 1) + 1;
    prev.pendingChecks = checks;
    saveState = true;
    if (checks >= pokeRetryThreshold) {
      action = 'restart';
      reason = 'delayed message persisted after ' + checks + ' checks, escalating to restart';
    } else {
      action = 'poke';
      reason = 'delayed message persists (check ' + checks + '/' + pokeRetryThreshold + '), poking Messages';
    }
  }
} else {
  // Same GUID as last check — no new messages, BB is idle
  action = 'ok';
  const idleMin = prev.allSeenAt ? Math.floor((now - Number(prev.allSeenAt)) / 60000) : msgAgeMin;
  reason = 'idle, no new messages (last new msg ' + idleMin + 'min ago)';
  if (prev.pendingGuid) {
    prev.pendingGuid = '';
    prev.pendingChecks = 0;
    saveState = true;
  }
}

// Save state when changed
if (saveState) {
  fs.writeFileSync(stateFile, JSON.stringify(prev, null, 2));
}

console.log([action, reason, msgAgeMin, webhookAgeMin, latestGuid, msgAgeSec, lagAlert ? '1' : '0'].join('|'));
" "$ALL_LATEST_JSON" "$LAG_ALERT_SEC" "$POKE_RETRY_THRESHOLD" 2>/dev/null || echo "error|node failed|0|0||0|0")

ACTION=$(echo "$RESULT" | cut -d'|' -f1)
REASON=$(echo "$RESULT" | cut -d'|' -f2)
MSG_AGE=$(echo "$RESULT" | cut -d'|' -f3)
WEBHOOK_AGE=$(echo "$RESULT" | cut -d'|' -f4)
GUID=$(echo "$RESULT" | cut -d'|' -f5)
MSG_AGE_SEC=$(echo "$RESULT" | cut -d'|' -f6)
LAG_ALERT=$(echo "$RESULT" | cut -d'|' -f7)

if [[ "$LAG_ALERT" == "1" ]]; then
  SHORT_GUID=$(echo "$GUID" | cut -c1-12)
  log "LAG ALERT: inbound ingest lag ${MSG_AGE_SEC}s (threshold ${LAG_ALERT_SEC}s) guid=${SHORT_GUID}..."
  echo "$(date '+%Y-%m-%d %H:%M:%S'),${MSG_AGE_SEC},${LAG_ALERT_SEC},${GUID}" >> "$LAG_METRICS_FILE"
fi

case "$ACTION" in
  ok)
    log "OK: ${REASON}"
    ;;
  skip)
    log "SKIP: ${REASON}"
    ;;
  poke)
    log "STALL DETECTED: ${REASON}"
    if [[ -f "$POKE_SCRIPT" ]]; then
      if osascript "$POKE_SCRIPT" >/dev/null 2>&1; then
        log "ACTION: Messages poke succeeded"
      else
        log "WARN: Messages poke failed"
      fi
    else
      log "WARN: poke script missing at ${POKE_SCRIPT}"
    fi
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
