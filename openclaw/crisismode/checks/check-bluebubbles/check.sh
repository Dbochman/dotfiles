#!/usr/bin/env bash
set -euo pipefail

# check-bluebubbles: Checks BlueBubbles iMessage server health
# Requires BLUEBUBBLES_PASSWORD env var
# Stdin: JSON with verb, target, context
# Stdout: JSON result
# Exit codes: 0=OK, 1=warning, 2=critical, 3=unknown

INPUT=$(cat)
VERB=$(printf '%s' "$INPUT" | sed -n 's/.*"verb" *: *"\([^"]*\)".*/\1/p' | head -1)

BB_URL="http://localhost:1234"
BB_PASS="${BLUEBUBBLES_PASSWORD:-}"

if [ -z "$BB_PASS" ]; then
  printf '{"status":"unknown","summary":"BLUEBUBBLES_PASSWORD not set","confidence":0.0,"signals":[],"recommendedActions":["Set BLUEBUBBLES_PASSWORD env var"]}\n'
  exit 3
fi

case "$VERB" in
  health)
    # Check server info endpoint
    RESP=$(curl -sf --connect-timeout 5 --max-time 10 \
      "${BB_URL}/api/v1/server/info?password=${BB_PASS}" 2>/dev/null) || RESP=""

    if [ -z "$RESP" ]; then
      printf '{"status":"critical","summary":"BlueBubbles unreachable on port 1234","confidence":0.95,"signals":[{"source":"http","status":"critical","detail":"Connection to localhost:1234 failed"}],"recommendedActions":["Check if BlueBubbles.app is running","Run: launchctl list com.bluebubbles.server","Restart BlueBubbles via Activity Monitor or reboot"]}\n'
      exit 2
    fi

    # Parse response fields using sed (no jq dependency)
    PRIVATE_API=$(printf '%s' "$RESP" | sed -n 's/.*"private_api" *: *\([a-z]*\).*/\1/p' | head -1)
    PROXY_SERVICE=$(printf '%s' "$RESP" | sed -n 's/.*"proxy_service" *: *"\([^"]*\)".*/\1/p' | head -1)
    OS_VERSION=$(printf '%s' "$RESP" | sed -n 's/.*"os_version" *: *"\([^"]*\)".*/\1/p' | head -1)

    SIGNALS="["
    EXIT_CODE=0
    STATUS="healthy"
    WARNINGS=""

    # Signal: server reachable
    SIGNALS="${SIGNALS}{\"source\":\"http\",\"status\":\"healthy\",\"detail\":\"BlueBubbles API responding on port 1234\"}"

    # Signal: Private API
    if [ "$PRIVATE_API" = "true" ]; then
      SIGNALS="${SIGNALS},{\"source\":\"private-api\",\"status\":\"healthy\",\"detail\":\"Private API enabled\"}"
    else
      SIGNALS="${SIGNALS},{\"source\":\"private-api\",\"status\":\"warning\",\"detail\":\"Private API disabled — reactions, typing, effects unavailable\"}"
      STATUS="warning"
      WARNINGS="Private API disabled. "
      EXIT_CODE=1
    fi

    # Signal: proxy mode
    if [ "$PROXY_SERVICE" = "lan-url" ] || [ "$PROXY_SERVICE" = "dynamic-dns" ]; then
      SIGNALS="${SIGNALS},{\"source\":\"proxy\",\"status\":\"healthy\",\"detail\":\"Proxy mode: ${PROXY_SERVICE}\"}"
    elif [ -n "$PROXY_SERVICE" ]; then
      SIGNALS="${SIGNALS},{\"source\":\"proxy\",\"status\":\"warning\",\"detail\":\"Proxy mode: ${PROXY_SERVICE} (expected lan-url)\"}"
      WARNINGS="${WARNINGS}Unexpected proxy mode. "
      if [ "$EXIT_CODE" -lt 1 ]; then EXIT_CODE=1; STATUS="warning"; fi
    fi

    SIGNALS="${SIGNALS}]"

    if [ "$EXIT_CODE" -eq 0 ]; then
      SUMMARY="BlueBubbles healthy — Private API active, proxy=${PROXY_SERVICE}, macOS ${OS_VERSION}"
      ACTIONS='[]'
    else
      SUMMARY="BlueBubbles reachable but degraded: ${WARNINGS}macOS ${OS_VERSION}"
      ACTIONS='["Check BlueBubbles Private API settings","Verify proxy is set to lan-url"]'
    fi

    printf '{"status":"%s","summary":"%s","confidence":0.95,"signals":%s,"recommendedActions":%s}\n' \
      "$STATUS" "$SUMMARY" "$SIGNALS" "$ACTIONS"
    exit "$EXIT_CODE"
    ;;

  diagnose)
    RESP=$(curl -sf --connect-timeout 5 --max-time 10 \
      "${BB_URL}/api/v1/server/info?password=${BB_PASS}" 2>/dev/null) || RESP=""

    if [ -z "$RESP" ]; then
      printf '{"healthy":false,"summary":"BlueBubbles unreachable","findings":[{"id":"bb-unreachable","severity":"critical","title":"Server unreachable","detail":"Cannot connect to BlueBubbles on localhost:1234"}]}\n'
      exit 0
    fi

    PRIVATE_API=$(printf '%s' "$RESP" | sed -n 's/.*"private_api" *: *\([a-z]*\).*/\1/p' | head -1)
    PROXY_SERVICE=$(printf '%s' "$RESP" | sed -n 's/.*"proxy_service" *: *"\([^"]*\)".*/\1/p' | head -1)
    OS_VERSION=$(printf '%s' "$RESP" | sed -n 's/.*"os_version" *: *"\([^"]*\)".*/\1/p' | head -1)
    PLATFORM=$(printf '%s' "$RESP" | sed -n 's/.*"platform" *: *"\([^"]*\)".*/\1/p' | head -1)

    # Check recent message activity via chat list
    CHATS_RESP=$(curl -sf --connect-timeout 5 --max-time 10 \
      "${BB_URL}/api/v1/chat?password=${BB_PASS}&limit=1&sort=lastmessage&offset=0" 2>/dev/null) || CHATS_RESP=""

    FINDINGS='['
    FINDINGS="${FINDINGS}{\"id\":\"bb-server\",\"severity\":\"info\",\"title\":\"Server Info\",\"detail\":\"macOS ${OS_VERSION}, platform ${PLATFORM}\"}"
    FINDINGS="${FINDINGS},{\"id\":\"bb-private-api\",\"severity\":\"info\",\"title\":\"Private API\",\"detail\":\"${PRIVATE_API}\"}"
    FINDINGS="${FINDINGS},{\"id\":\"bb-proxy\",\"severity\":\"info\",\"title\":\"Proxy Mode\",\"detail\":\"${PROXY_SERVICE}\"}"

    if [ "$PRIVATE_API" != "true" ]; then
      FINDINGS="${FINDINGS},{\"id\":\"bb-private-api-warn\",\"severity\":\"warning\",\"title\":\"Private API Disabled\",\"detail\":\"Reactions, typing indicators, and effects are unavailable\"}"
    fi

    FINDINGS="${FINDINGS}]"

    HEALTHY=true
    if [ "$PRIVATE_API" != "true" ]; then HEALTHY=false; fi

    printf '{"healthy":%s,"summary":"BlueBubbles on macOS %s, Private API=%s, proxy=%s","findings":%s}\n' \
      "$HEALTHY" "$OS_VERSION" "$PRIVATE_API" "$PROXY_SERVICE" "$FINDINGS"
    exit 0
    ;;

  *)
    printf '{"status":"unknown","summary":"Unsupported verb: %s","confidence":0.0,"signals":[],"recommendedActions":[]}\n' "$VERB"
    exit 3
    ;;
esac
