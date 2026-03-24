#!/usr/bin/env bash
set -euo pipefail

# check-openclaw-gateway: Checks OpenClaw gateway health
# Stdin: JSON with verb, target, context
# Stdout: JSON result
# Exit codes: 0=OK, 1=warning, 2=critical, 3=unknown

INPUT=$(cat)
VERB=$(printf '%s' "$INPUT" | sed -n 's/.*"verb" *: *"\([^"]*\)".*/\1/p' | head -1)

GW_URL="http://localhost:18789"
GW_LABEL="ai.openclaw.gateway"

case "$VERB" in
  health)
    # Check health endpoint
    HTTP_CODE=$(curl -sf -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 10 \
      "${GW_URL}/health" 2>/dev/null) || HTTP_CODE="000"

    # Check LaunchAgent process (launchctl list <label> returns plist dict)
    LAUNCHCTL_OUT=$(launchctl list "$GW_LABEL" 2>/dev/null) || LAUNCHCTL_OUT=""
    GW_PID=$(printf '%s' "$LAUNCHCTL_OUT" | sed -n 's/.*"PID" *= *\([0-9]*\).*/\1/p' | head -1)
    GW_EXIT=$(printf '%s' "$LAUNCHCTL_OUT" | sed -n 's/.*"LastExitStatus" *= *\([0-9]*\).*/\1/p' | head -1)

    SIGNALS="["
    EXIT_CODE=0
    STATUS="healthy"
    WARNINGS=""

    # Signal: HTTP health
    if [ "$HTTP_CODE" = "200" ]; then
      SIGNALS="${SIGNALS}{\"source\":\"http\",\"status\":\"healthy\",\"detail\":\"Gateway /health returned 200\"}"
    elif [ "$HTTP_CODE" = "000" ]; then
      SIGNALS="${SIGNALS}{\"source\":\"http\",\"status\":\"critical\",\"detail\":\"Gateway unreachable on port 18789\"}"
      STATUS="critical"
      EXIT_CODE=2
    else
      SIGNALS="${SIGNALS}{\"source\":\"http\",\"status\":\"warning\",\"detail\":\"Gateway /health returned HTTP ${HTTP_CODE}\"}"
      STATUS="warning"
      EXIT_CODE=1
    fi

    # Signal: process status
    if [ -n "$GW_PID" ] && [ "$GW_PID" != "-" ]; then
      SIGNALS="${SIGNALS},{\"source\":\"launchctl\",\"status\":\"healthy\",\"detail\":\"Gateway process running (PID ${GW_PID})\"}"
    elif [ -n "$GW_EXIT" ] && [ "$GW_EXIT" != "0" ] && [ "$GW_EXIT" != "-" ]; then
      SIGNALS="${SIGNALS},{\"source\":\"launchctl\",\"status\":\"critical\",\"detail\":\"Gateway exited with code ${GW_EXIT} — possible crash loop\"}"
      if [ "$EXIT_CODE" -lt 2 ]; then STATUS="critical"; EXIT_CODE=2; fi
    else
      SIGNALS="${SIGNALS},{\"source\":\"launchctl\",\"status\":\"warning\",\"detail\":\"Gateway process not running (no PID)\"}"
      if [ "$EXIT_CODE" -lt 1 ]; then STATUS="warning"; EXIT_CODE=1; fi
    fi

    SIGNALS="${SIGNALS}]"

    if [ "$EXIT_CODE" -eq 0 ]; then
      SUMMARY="OpenClaw gateway healthy — HTTP 200, PID ${GW_PID}"
      ACTIONS='[]'
    elif [ "$EXIT_CODE" -eq 1 ]; then
      SUMMARY="OpenClaw gateway degraded — HTTP ${HTTP_CODE}"
      ACTIONS='["Check gateway logs: ~/.openclaw/logs/gateway.log","Restart: launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway"]'
    else
      SUMMARY="OpenClaw gateway down — unreachable or crashed"
      ACTIONS='["Check gateway logs: ~/.openclaw/logs/gateway.log","Restart: launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway","Verify secrets: source ~/.openclaw/.secrets-cache && env | grep -c PASSWORD"]'
    fi

    printf '{"status":"%s","summary":"%s","confidence":0.95,"signals":%s,"recommendedActions":%s}\n' \
      "$STATUS" "$SUMMARY" "$SIGNALS" "$ACTIONS"
    exit "$EXIT_CODE"
    ;;

  diagnose)
    HTTP_CODE=$(curl -sf -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 10 \
      "${GW_URL}/health" 2>/dev/null) || HTTP_CODE="000"

    LAUNCHCTL_OUT=$(launchctl list "$GW_LABEL" 2>/dev/null) || LAUNCHCTL_OUT=""
    GW_PID=$(printf '%s' "$LAUNCHCTL_OUT" | sed -n 's/.*"PID" *= *\([0-9]*\).*/\1/p' | head -1)
    GW_EXIT=$(printf '%s' "$LAUNCHCTL_OUT" | sed -n 's/.*"LastExitStatus" *= *\([0-9]*\).*/\1/p' | head -1)

    # Check gateway log for recent errors
    GW_LOG="$HOME/.openclaw/logs/gateway.log"
    RECENT_ERRORS=""
    if [ -f "$GW_LOG" ]; then
      RECENT_ERRORS=$(tail -50 "$GW_LOG" 2>/dev/null | grep -ci 'error\|fatal\|crash' 2>/dev/null) || RECENT_ERRORS="0"
    fi

    FINDINGS='['
    FINDINGS="${FINDINGS}{\"id\":\"gw-http\",\"severity\":\"info\",\"title\":\"Health Endpoint\",\"detail\":\"HTTP ${HTTP_CODE}\"}"
    FINDINGS="${FINDINGS},{\"id\":\"gw-pid\",\"severity\":\"info\",\"title\":\"Process\",\"detail\":\"PID=${GW_PID:-none}, exit=${GW_EXIT:-unknown}\"}"

    if [ "$RECENT_ERRORS" -gt 5 ] 2>/dev/null; then
      FINDINGS="${FINDINGS},{\"id\":\"gw-errors\",\"severity\":\"warning\",\"title\":\"Recent Log Errors\",\"detail\":\"${RECENT_ERRORS} error/fatal/crash lines in last 50 log entries\"}"
    fi

    if [ "$HTTP_CODE" = "000" ]; then
      FINDINGS="${FINDINGS},{\"id\":\"gw-unreachable\",\"severity\":\"critical\",\"title\":\"Gateway Unreachable\",\"detail\":\"Cannot connect to localhost:18789\"}"
    fi

    FINDINGS="${FINDINGS}]"

    HEALTHY=true
    if [ "$HTTP_CODE" != "200" ]; then HEALTHY=false; fi

    printf '{"healthy":%s,"summary":"Gateway HTTP=%s, PID=%s, exit=%s, recent_errors=%s","findings":%s}\n' \
      "$HEALTHY" "$HTTP_CODE" "${GW_PID:-none}" "${GW_EXIT:-unknown}" "${RECENT_ERRORS:-0}" "$FINDINGS"
    exit 0
    ;;

  *)
    printf '{"status":"unknown","summary":"Unsupported verb: %s","confidence":0.0,"signals":[],"recommendedActions":[]}\n' "$VERB"
    exit 3
    ;;
esac
