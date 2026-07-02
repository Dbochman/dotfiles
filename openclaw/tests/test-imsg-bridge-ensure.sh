#!/bin/bash

set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
SCRIPT="$ROOT/bin/imsg-bridge-ensure"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/home" "$TMP/bin"
EVENTS="$TMP/events"
READY="$TMP/bridge-ready"

printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  'case "${1:-}" in' \
  '  status)' \
  '    if [[ -f "$FAKE_READY" ]]; then' \
  '      printf '\''{"bridge_version":2,"v2_ready":true}\n'\''' \
  '    else' \
  '      printf '\''{"bridge_version":0,"v2_ready":false}\n'\''' \
  '    fi' \
  '    ;;' \
  '  launch)' \
  '    printf '\''imsg %s\n'\'' "$*" >> "$FAKE_EVENTS"' \
  '    if [[ " $* " != *" --kill-only "* ]]; then touch "$FAKE_READY"; fi' \
  '    printf '\''{"ok":true}\n'\''' \
  '    ;;' \
  '  *) exit 2 ;;' \
  'esac' > "$TMP/bin/imsg"

printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  'printf '\''launchctl %s\n'\'' "$*" >> "$FAKE_EVENTS"' \
  'exit 0' > "$TMP/bin/launchctl"

printf '%s\n' \
  '#!/bin/bash' \
  'exit 0' > "$TMP/bin/curl"

chmod +x "$TMP/bin/imsg" "$TMP/bin/launchctl" "$TMP/bin/curl"

run_watchdog() {
  env \
    HOME="$TMP/home" \
    OPENCLAW_HOME="$TMP/home/.openclaw" \
    IMSG_BIN="$TMP/bin/imsg" \
    LAUNCHCTL_BIN="$TMP/bin/launchctl" \
    CURL_BIN="$TMP/bin/curl" \
    FAKE_EVENTS="$EVENTS" \
    FAKE_READY="$READY" \
    IMSG_BRIDGE_STARTUP_DELAY_SECONDS=0 \
    IMSG_BRIDGE_MIN_REPAIR_INTERVAL_SECONDS=0 \
    IMSG_BRIDGE_POLL_ATTEMPTS=2 \
    IMSG_BRIDGE_POLL_INTERVAL_SECONDS=0 \
    IMSG_BRIDGE_GATEWAY_POLL_ATTEMPTS=1 \
    "$SCRIPT"
}

run_watchdog

grep -q 'imsg launch --kill-only --json' "$EVENTS"
grep -q 'imsg launch --json' "$EVENTS"
grep -q 'launchctl kickstart -k gui/' "$EVENTS"
grep -q 'bridge repaired and gateway restarted successfully' \
  "$TMP/home/.openclaw/logs/imsg-bridge-ensure.log"

: > "$EVENTS"
run_watchdog

if grep -qE 'imsg launch|launchctl kickstart' "$EVENTS"; then
  echo "healthy second run unexpectedly attempted a repair" >&2
  exit 1
fi

echo "imsg bridge watchdog tests passed"
