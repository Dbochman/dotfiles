#!/usr/bin/env bash
set -euo pipefail

# check-launchd-services: Checks critical macOS LaunchAgent services
# Stdin: JSON with verb, target, context
# Stdout: JSON result
# Exit codes: 0=OK, 1=warning, 2=critical, 3=unknown

INPUT=$(cat)
VERB=$(printf '%s' "$INPUT" | sed -n 's/.*"verb" *: *"\([^"]*\)".*/\1/p' | head -1)

# Persistent services (must have a PID running)
PERSISTENT_SERVICES="ai.openclaw.gateway ai.openclaw.nest-dashboard ai.openclaw.usage-dashboard"

# Run-and-exit services (PID "-" is normal, only check exit code)
TRANSIENT_SERVICES="ai.openclaw.nest-snapshot com.openclaw.bb-watchdog com.openclaw.presence-receive"

check_service() {
  local label="$1"
  local expect_pid="$2"  # "yes" for persistent, "no" for transient

  local out
  out=$(launchctl list "$label" 2>/dev/null) || {
    echo "missing"
    return
  }

  # launchctl list <label> returns plist dict: "PID" = 94171; "LastExitStatus" = 0;
  local pid exit_code
  pid=$(printf '%s' "$out" | sed -n 's/.*"PID" *= *\([0-9]*\).*/\1/p' | head -1)
  exit_code=$(printf '%s' "$out" | sed -n 's/.*"LastExitStatus" *= *\([0-9]*\).*/\1/p' | head -1)

  if [ "$expect_pid" = "yes" ]; then
    if [ -n "$pid" ] && [ "$pid" != "-" ] && [ "$pid" != "0" ]; then
      echo "healthy:PID ${pid}"
    elif [ -n "$exit_code" ] && [ "$exit_code" != "0" ] && [ "$exit_code" != "-" ]; then
      echo "critical:exited with code ${exit_code}"
    else
      echo "warning:no PID (not running)"
    fi
  else
    # Transient: just check it's loaded and not crash-looping
    if [ -n "$exit_code" ] && [ "$exit_code" != "0" ] && [ "$exit_code" != "-" ] && [ "$exit_code" != "-15" ]; then
      echo "warning:last exit code ${exit_code}"
    else
      echo "healthy:loaded (exit=${exit_code:-0})"
    fi
  fi
}

case "$VERB" in
  health)
    SIGNALS="["
    FIRST=true
    EXIT_CODE=0
    STATUS="healthy"
    CRITICAL_COUNT=0
    WARNING_COUNT=0

    for svc in $PERSISTENT_SERVICES; do
      RESULT=$(check_service "$svc" "yes")
      SVC_STATUS="${RESULT%%:*}"
      SVC_DETAIL="${RESULT#*:}"

      if [ "$FIRST" = true ]; then FIRST=false; else SIGNALS="${SIGNALS},"; fi
      SIGNALS="${SIGNALS}{\"source\":\"${svc}\",\"status\":\"${SVC_STATUS}\",\"detail\":\"${SVC_DETAIL}\"}"

      case "$SVC_STATUS" in
        critical) CRITICAL_COUNT=$((CRITICAL_COUNT + 1)) ;;
        warning|missing) WARNING_COUNT=$((WARNING_COUNT + 1)) ;;
      esac
    done

    for svc in $TRANSIENT_SERVICES; do
      RESULT=$(check_service "$svc" "no")
      SVC_STATUS="${RESULT%%:*}"
      SVC_DETAIL="${RESULT#*:}"

      if [ "$FIRST" = true ]; then FIRST=false; else SIGNALS="${SIGNALS},"; fi
      SIGNALS="${SIGNALS}{\"source\":\"${svc}\",\"status\":\"${SVC_STATUS}\",\"detail\":\"${SVC_DETAIL}\"}"

      case "$SVC_STATUS" in
        critical) CRITICAL_COUNT=$((CRITICAL_COUNT + 1)) ;;
        warning|missing) WARNING_COUNT=$((WARNING_COUNT + 1)) ;;
      esac
    done

    SIGNALS="${SIGNALS}]"
    TOTAL=$((CRITICAL_COUNT + WARNING_COUNT))

    if [ "$CRITICAL_COUNT" -gt 0 ]; then
      STATUS="critical"
      SUMMARY="${CRITICAL_COUNT} critical, ${WARNING_COUNT} warning — service(s) crashed or not running"
      ACTIONS='["Check logs for crashed services","Restart: launchctl kickstart -k gui/$(id -u)/<label>"]'
      EXIT_CODE=2
    elif [ "$WARNING_COUNT" -gt 0 ]; then
      STATUS="warning"
      SUMMARY="${WARNING_COUNT} service(s) have warnings"
      ACTIONS='["Check launchctl list for affected services","Review service logs"]'
      EXIT_CODE=1
    else
      SUMMARY="All LaunchAgent services healthy"
      ACTIONS='[]'
    fi

    printf '{"status":"%s","summary":"%s","confidence":0.9,"signals":%s,"recommendedActions":%s}\n' \
      "$STATUS" "$SUMMARY" "$SIGNALS" "$ACTIONS"
    exit "$EXIT_CODE"
    ;;

  diagnose)
    FINDINGS='['
    FIRST=true

    for svc in $PERSISTENT_SERVICES; do
      RESULT=$(check_service "$svc" "yes")
      SVC_STATUS="${RESULT%%:*}"
      SVC_DETAIL="${RESULT#*:}"

      case "$SVC_STATUS" in
        critical) SEV="critical" ;;
        warning|missing) SEV="warning" ;;
        *) SEV="info" ;;
      esac

      if [ "$FIRST" = true ]; then FIRST=false; else FINDINGS="${FINDINGS},"; fi
      FINDINGS="${FINDINGS}{\"id\":\"svc-${svc}\",\"severity\":\"${SEV}\",\"title\":\"${svc}\",\"detail\":\"${SVC_DETAIL} (persistent, expects PID)\"}"
    done

    for svc in $TRANSIENT_SERVICES; do
      RESULT=$(check_service "$svc" "no")
      SVC_STATUS="${RESULT%%:*}"
      SVC_DETAIL="${RESULT#*:}"

      case "$SVC_STATUS" in
        critical) SEV="critical" ;;
        warning|missing) SEV="warning" ;;
        *) SEV="info" ;;
      esac

      if [ "$FIRST" = true ]; then FIRST=false; else FINDINGS="${FINDINGS},"; fi
      FINDINGS="${FINDINGS}{\"id\":\"svc-${svc}\",\"severity\":\"${SEV}\",\"title\":\"${svc}\",\"detail\":\"${SVC_DETAIL} (transient, run-and-exit)\"}"
    done

    FINDINGS="${FINDINGS}]"

    printf '{"healthy":true,"summary":"LaunchAgent service diagnostics","findings":%s}\n' "$FINDINGS"
    exit 0
    ;;

  *)
    printf '{"status":"unknown","summary":"Unsupported verb: %s","confidence":0.0,"signals":[],"recommendedActions":[]}\n' "$VERB"
    exit 3
    ;;
esac
