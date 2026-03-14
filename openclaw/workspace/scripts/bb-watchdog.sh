#!/bin/bash
# bb-watchdog.sh — Detect and fix BlueBubbles chat.db observer stalls
#
# BB's polling loop on chat.db can stall indefinitely on headless Macs.
# BB's webhook dispatch service can also silently die (e.g., Cloudflare
# daemon crash-loops corrupt BB's event loop) — messages appear in the DB
# but BB never POSTs them to the gateway webhook.
#
# This script runs every 1 minute via LaunchAgent and restarts BB if stalled.
#
# Detection:
#   1. Private API helper disconnected — BB receives messages and marks read,
#      but can't send responses. Full restart required (not soft restart) because
#      soft restart only reconnects the helper but doesn't restart the chat.db
#      observer, which often co-stalls with the helper.
#   2. Track the GUID of the latest message (any sender). If the GUID changes
#      but BB hasn't dispatched a webhook, the observer has stalled.
#   3. If BB hasn't dispatched ANY webhook in WEBHOOK_DEAD_THRESHOLD_MIN and
#      fresh messages exist, the webhook service itself is dead.
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
CRON_JOBS_FILE="${HOME}/.openclaw/cron/jobs.json"

mkdir -p "$STATE_DIR"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
}

# Check if any cron job is currently running (has a runningAtMs marker).
# Gateway restart during a cron run kills the agent session, preventing the
# "finished" record from being written to the cron run JSONL — which breaks
# the usage dashboard's "recent cron runs" display.
cron_job_running() {
  if [[ ! -f "$CRON_JOBS_FILE" ]]; then
    return 1
  fi
  $NODE -e "
    const fs = require('fs');
    try {
      const d = JSON.parse(fs.readFileSync('$CRON_JOBS_FILE', 'utf8'));
      const jobs = Array.isArray(d) ? d : (d.jobs || []);
      const running = jobs.filter(j => j.runningAtMs && j.runningAtMs > 0);
      if (running.length > 0) {
        console.log(running.map(j => j.id).join(','));
        process.exit(0);
      }
      process.exit(1);
    } catch { process.exit(1); }
  " 2>/dev/null
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

# Check if BB is running (API port)
if ! curl -s --max-time 5 "${BB_URL}/api/v1/ping?password=${BB_PW}" > /dev/null 2>&1; then
  log "WARN: BB not reachable, attempting to start"
  open -a BlueBubbles
  exit 0
fi

# Check if BB Private API helper is connected. The helper is a separate process
# that BB uses to interface with Messages.app for sending, typing indicators,
# reactions, etc. When disconnected, BB still receives webhooks and marks messages
# as read (standard API), but the gateway can't send responses. A soft restart
# reconnects the helper without killing BB entirely.
HELPER_STATUS=$(curl -s --max-time 5 "${BB_URL}/api/v1/server/info?password=${BB_PW}" 2>/dev/null || echo '{}')
HELPER_CONNECTED=$($NODE -e "try { const d = JSON.parse(process.argv[1]); console.log(d.data?.helper_connected === true ? 'true' : 'false'); } catch { console.log('unknown'); }" "$HELPER_STATUS" 2>/dev/null || echo "unknown")

if [[ "$HELPER_CONNECTED" == "false" ]]; then
  # Check cooldown before restarting
  LAST_RESTART=$($NODE -e "
    const fs = require('fs');
    try { const s = JSON.parse(fs.readFileSync('$STATE_FILE','utf8')); console.log(s.lastRestart || 0); } catch { console.log(0); }
  " 2>/dev/null || echo "0")
  SINCE_RESTART=$($NODE -e "console.log(Date.now() - Number(process.argv[1]))" "$LAST_RESTART" 2>/dev/null || echo "999999999")

  if [[ "$SINCE_RESTART" -lt 900000 ]]; then
    log "WARN: Private API helper disconnected but within restart cooldown"
  else
    log "PRIVATE API HELPER DISCONNECTED: full-restarting BlueBubbles"
    # Full restart required — soft restart only reconnects the helper but does NOT
    # restart the chat.db file system observer. If the observer is also stalled
    # (common co-occurrence), soft restart leaves BB unable to detect new messages
    # even though helper_connected returns true.

    # Step 1: Full BB restart
    osascript -e 'tell application "BlueBubbles" to quit' 2>/dev/null || true
    sleep 5
    if pgrep -xq "BlueBubbles"; then
      pkill -x "BlueBubbles" 2>/dev/null || true
      sleep 2
    fi
    open -a BlueBubbles
    log "ACTION: BlueBubbles full-restarted, waiting 15s for init..."
    sleep 15

    # Step 2: Verify helper reconnected
    CHECK=$(curl -s --max-time 5 "${BB_URL}/api/v1/server/info?password=${BB_PW}" 2>/dev/null || echo '{}')
    IS_CONNECTED=$($NODE -e "try { const d = JSON.parse(process.argv[1]); console.log(d.data?.helper_connected === true ? 'true' : 'false'); } catch { console.log('false'); }" "$CHECK" 2>/dev/null || echo "false")
    if [[ "$IS_CONNECTED" == "true" ]]; then
      log "ACTION: Private API helper reconnected after full restart"
    else
      log "WARN: Private API helper still disconnected after full restart"
    fi

    # Step 3: Restart gateway to re-establish clean webhook connection
    RUNNING_JOBS=$(cron_job_running)
    if [[ $? -eq 0 ]]; then
      log "DEFER: Gateway restart deferred — cron job(s) running: ${RUNNING_JOBS}"
    elif launchctl kickstart -k "gui/$(id -u)/ai.openclaw.gateway" 2>/dev/null; then
      log "ACTION: Gateway restarted after Private API helper recovery"
    else
      log "WARN: Gateway restart failed after Private API helper recovery"
    fi

    # Record restart in state
    $NODE -e "
      const fs = require('fs');
      const stateFile = '$STATE_FILE';
      let prev = {};
      try { prev = JSON.parse(fs.readFileSync(stateFile, 'utf8')); } catch {}
      prev.lastRestart = Date.now();
      fs.writeFileSync(stateFile, JSON.stringify(prev, null, 2));
    " 2>/dev/null
    exit 0
  fi
fi

# Verify the gateway's BB plugin is loaded by checking if the gateway process
# is listening and the BB webhook endpoint responds. BB dispatches webhooks to
# the gateway at http://localhost:18789/bluebubbles-webhook — if the gateway's
# BB plugin failed to load (e.g., broken module import after npm upgrade), BB
# will dispatch webhooks into the void and no messages reach OpenClaw.
GW_URL="http://localhost:18789"
GW_BB_HEALTHY=$(curl -s --max-time 3 -o /dev/null -w '%{http_code}' "${GW_URL}/__openclaw__/canvas/" 2>/dev/null || echo "000")
if [[ "$GW_BB_HEALTHY" == "000" ]]; then
  log "WARN: Gateway not reachable — BB webhooks may not be received"
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

// Cross-check: verify gateway's BB plugin loaded in the CURRENT process.
// BB can dispatch webhooks successfully (webhookAgeMin is low) but the gateway
// may not have its BB plugin loaded (e.g., broken import after npm upgrade).
//
// Strategy: find the last gateway startup line ('listening on ws://') in the
// log, then check if 'webhook listening' (BB plugin init) appears AFTER it.
// This scopes the check to the current gateway process, avoiding:
//   - False negatives from stale startup lines in earlier process lifetimes
//   - False positives from multi-day uptime aging out of scan window
let gatewayBbAliveMin = 999;
let gatewayBbPluginLoaded = false;
try {
  // Gateway log is named by local date, not UTC — check today and yesterday
  const localNow = new Date(now - new Date().getTimezoneOffset() * 60000);
  const localToday = localNow.toISOString().slice(0, 10);
  const localYesterday = new Date(localNow - 86400000).toISOString().slice(0, 10);
  const candidates = [localToday, localYesterday];

  // Phase 1: Find the last gateway startup line across log files
  let startupLineIdx = -1;
  let startupLogLines = null;
  for (const day of candidates) {
    const gwLogPath = '/tmp/openclaw/openclaw-' + day + '.log';
    if (!fs.existsSync(gwLogPath)) continue;
    const gwContent = fs.readFileSync(gwLogPath, 'utf8');
    const gwLines = gwContent.split('\n');
    for (let i = gwLines.length - 1; i >= 0; i--) {
      if (gwLines[i].includes('listening on ws://')) {
        startupLineIdx = i;
        startupLogLines = gwLines;
        break;
      }
    }
    if (startupLineIdx >= 0) break;
  }

  // Phase 2: From the startup line forward, check if BB plugin loaded
  if (startupLogLines && startupLineIdx >= 0) {
    for (let i = startupLineIdx; i < startupLogLines.length; i++) {
      if (!startupLogLines[i].includes('bluebubbles')) continue;
      if (startupLogLines[i].includes('webhook listening')) {
        gatewayBbPluginLoaded = true;
      }
      // Also track most recent BB activity for diagnostics
      if (startupLogLines[i].includes('webhook listening') || startupLogLines[i].includes('inbound') || startupLogLines[i].includes('new-message')) {
        try {
          const entry = JSON.parse(startupLogLines[i]);
          const ts = entry._meta?.date;
          if (ts) {
            gatewayBbAliveMin = Math.floor((now - new Date(ts).getTime()) / 60000);
          }
        } catch {
          const m = startupLogLines[i].match(/(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})/);
          if (m) gatewayBbAliveMin = Math.floor((now - new Date(m[1]).getTime()) / 60000);
        }
      }
    }
  }
} catch {}
// Gateway BB plugin is dead ONLY if it didn't load in the current process.
const gatewayBbDead = !gatewayBbPluginLoaded;

// Decision logic:
// 1. If we can't reach BB API → skip
// 2. If GUID changed AND webhook was dispatched recently → new message processed, all good
// 3. If GUID changed AND no recent webhook → STALL (new message exists but BB didn't webhook it)
// 4. If GUID unchanged → no new messages, idle (regardless of how old the last message is)
const pokeRetryThreshold = Number(process.argv[3] || 3);
const WEBHOOK_DEAD_THRESHOLD_MIN = 30;

let action = 'ok';
let reason = '';
let saveState = false;
let lagAlert = false;
const newMsgNeedsAttention = (webhookAgeMin > 1) && (msgAgeSec >= lagAlertSec);

// Webhook-dead detection: if BB hasn't dispatched ANY webhook in 30+ min
// but new messages are arriving, the webhook service is dead. This catches
// silent failures (e.g., Cloudflare daemon crash-loop corrupting BB's event
// loop) that the per-message lag check misses because fresh messages have
// low msgAgeSec.
const webhookServiceDead = webhookAgeMin >= WEBHOOK_DEAD_THRESHOLD_MIN && guidChanged;

// Gateway-BB-dead detection: BB dispatches webhooks (webhookAgeMin is low)
// but gateway's BB plugin isn't loaded or isn't processing them. This catches
// broken BB plugin imports after npm upgrades. Action: restart gateway only.
const gatewayNeedsRestart = gatewayBbDead && webhookAgeMin < 10 && guidChanged;

if (gatewayNeedsRestart && !inCooldown) {
  prev.allGuid = latestGuid;
  prev.allSeenAt = now;
  prev.pendingGuid = '';
  prev.pendingChecks = 0;
  saveState = true;
  action = 'restart-gateway';
  reason = 'BB dispatching webhooks (last ' + webhookAgeMin + 'min ago) but gateway BB plugin never loaded (no webhook-listening entry in gateway log) — restarting gateway only';
} else if (!latestGuid) {
  action = 'skip';
  reason = 'could not fetch latest message from BB API';
} else if (webhookServiceDead && !inCooldown) {
  // Webhook dispatch is completely dead — escalate directly to restart
  // (skip poke, it won't help with a dead webhook service)
  prev.allGuid = latestGuid;
  prev.allSeenAt = now;
  prev.pendingGuid = '';
  prev.pendingChecks = 0;
  saveState = true;
  action = 'restart';
  reason = 'webhook service dead (no dispatch in ' + webhookAgeMin + 'min) but new messages arriving, full restart required';
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
    # Silent when healthy — only log non-idle OK (e.g., new message processed)
    if [[ "$REASON" != idle* ]]; then
      log "OK: ${REASON}"
    fi
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

    # Wait for BB to initialize, then restart gateway to re-register webhook.
    # BB restart invalidates the gateway's webhook registration — without this,
    # the gateway holds a stale webhook and never receives new messages.
    sleep 15
    RUNNING_JOBS=$(cron_job_running)
    if [[ $? -eq 0 ]]; then
      log "DEFER: Gateway restart deferred — cron job(s) running: ${RUNNING_JOBS}"
    elif launchctl kickstart -k "gui/$(id -u)/ai.openclaw.gateway" 2>/dev/null; then
      log "ACTION: Gateway restarted (webhook re-registration after BB restart)"
    else
      log "WARN: Gateway restart failed — webhook may be stale"
    fi

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
  restart-gateway)
    log "GATEWAY BB PLUGIN DEAD: ${REASON}"
    RUNNING_JOBS=$(cron_job_running)
    if [[ $? -eq 0 ]]; then
      log "DEFER: Gateway restart deferred — cron job(s) running: ${RUNNING_JOBS}"
    else
      log "ACTION: Restarting gateway only (BB is healthy)..."
      if launchctl kickstart -k "gui/$(id -u)/ai.openclaw.gateway" 2>/dev/null; then
        log "ACTION: Gateway restarted (BB plugin reload)"
      else
        log "WARN: Gateway restart failed"
      fi
    fi
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
