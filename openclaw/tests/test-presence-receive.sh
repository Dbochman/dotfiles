#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/../.." && pwd)
TEST_HOME=$(mktemp -d)
trap 'rm -rf "$TEST_HOME"' EXIT

mkdir -p \
  "$TEST_HOME/Downloads" \
  "$TEST_HOME/EmptyDownloads" \
  "$TEST_HOME/.openclaw/presence" \
  "$TEST_HOME/.openclaw/logs" \
  "$TEST_HOME/.openclaw/workspace/scripts"

write_scan() {
  local path="$1" timestamp="$2" dylan="$3" julia="$4"
  cat > "$path" <<JSON
{"timestamp":"$timestamp","location":"crosstown","presence":{"Dylan":{"present":$dylan},"Julia":{"present":$julia}}}
JSON
}

printf '%s\n' \
  '#!/usr/bin/env bash' \
  'printf "%s\n" "$1" >> "$HOME/evaluator-calls"' \
  > "$TEST_HOME/.openclaw/workspace/scripts/presence-detect.sh"
chmod +x "$TEST_HOME/.openclaw/workspace/scripts/presence-detect.sh"

# An unrelated Downloads event with no matching files is a clean no-op.
HOME="$TEST_HOME" PRESENCE_DOWNLOAD_DIR="$TEST_HOME/EmptyDownloads" \
  bash "$REPO_ROOT/openclaw/workspace/scripts/presence-receive.sh"
test ! -e "$TEST_HOME/evaluator-calls"

# Numeric suffixes are how macOS resolves repeated Taildrop names. The greatest
# embedded scan timestamp wins even if an older payload arrived later.
write_scan "$TEST_HOME/Downloads/crosstown-scan.json" \
  "2026-06-27T20:00:00Z" false false
write_scan "$TEST_HOME/Downloads/crosstown-scan (1).json" \
  "2026-06-27T20:15:00Z" true true
touch -t 202606272030 "$TEST_HOME/Downloads/crosstown-scan.json"
touch -t 202606272015 "$TEST_HOME/Downloads/crosstown-scan (1).json"

# Unrelated and invalid Downloads content must not be consumed.
write_scan "$TEST_HOME/Downloads/stdin.txt" \
  "2026-06-27T20:30:00Z" false false
printf '%s\n' '{"not":"presence"}' \
  > "$TEST_HOME/Downloads/crosstown-scan (99).json"
# A newer incomplete/invalid arrival must not block the older valid payload.
touch -t 202606272040 "$TEST_HOME/Downloads/crosstown-scan (99).json"

HOME="$TEST_HOME" \
  bash "$REPO_ROOT/openclaw/workspace/scripts/presence-receive.sh"

python3 - "$TEST_HOME/.openclaw/presence/crosstown-scan.json" <<'PY'
import json
import sys

with open(sys.argv[1]) as state_file:
    state = json.load(state_file)
if state.get("timestamp") != "2026-06-27T20:15:00Z":
    raise SystemExit(f"expected newest scan, got {state!r}")
if not all(state["presence"][person]["present"] for person in ("Dylan", "Julia")):
    raise SystemExit(f"expected both people present, got {state!r}")
PY

test "$(cat "$TEST_HOME/evaluator-calls")" = "evaluate"
test ! -e "$TEST_HOME/Downloads/crosstown-scan.json"
test ! -e "$TEST_HOME/Downloads/crosstown-scan (1).json"
test -e "$TEST_HOME/Downloads/stdin.txt"
test -e "$TEST_HOME/Downloads/crosstown-scan (99).json"

# The rollout guard ingests a valid payload without changing correlated state.
write_scan "$TEST_HOME/Downloads/crosstown-scan (2).json" \
  "2026-06-27T20:30:00Z" false true
HOME="$TEST_HOME" PRESENCE_RECEIVE_EVALUATE=0 \
  bash "$REPO_ROOT/openclaw/workspace/scripts/presence-receive.sh"
test "$(wc -l < "$TEST_HOME/evaluator-calls" | tr -d ' ')" -eq 1

python3 - "$TEST_HOME/.openclaw/presence/crosstown-scan.json" <<'PY'
import json
import sys

with open(sys.argv[1]) as state_file:
    state = json.load(state_file)
if state.get("timestamp") != "2026-06-27T20:30:00Z":
    raise SystemExit(f"guarded ingest did not promote newest scan: {state!r}")
PY

# A delayed valid payload is consumed but cannot regress canonical state or
# trigger another evaluation.
write_scan "$TEST_HOME/Downloads/crosstown-scan (3).json" \
  "2026-06-27T20:25:00Z" true false
HOME="$TEST_HOME" \
  bash "$REPO_ROOT/openclaw/workspace/scripts/presence-receive.sh"
test "$(wc -l < "$TEST_HOME/evaluator-calls" | tr -d ' ')" -eq 1
test ! -e "$TEST_HOME/Downloads/crosstown-scan (3).json"

python3 - "$TEST_HOME/.openclaw/presence/crosstown-scan.json" <<'PY'
import json
import sys

with open(sys.argv[1]) as state_file:
    state = json.load(state_file)
if state.get("timestamp") != "2026-06-27T20:30:00Z":
    raise SystemExit(f"stale replay regressed canonical scan: {state!r}")
PY

grep -q 'file cp --name crosstown-scan.json - dylans-mac-mini:' \
  "$REPO_ROOT/openclaw/workspace/scripts/presence-detect.sh"

plist="$REPO_ROOT/openclaw/launchagents/com.openclaw.presence-receive.plist"
plutil -lint "$plist" >/dev/null
test "$(/usr/libexec/PlistBuddy -c 'Print :WatchPaths:0' "$plist")" \
  = "/Users/dbochman/Downloads"
if /usr/libexec/PlistBuddy -c 'Print :KeepAlive' "$plist" >/dev/null 2>&1; then
  echo "presence receiver must not remain a KeepAlive job" >&2
  exit 1
fi

dd if=/dev/zero \
  of="$TEST_HOME/.openclaw/logs/presence-detect.log" \
  bs=1024 count=2 2>/dev/null
HOME="$TEST_HOME" PRESENCE_LOG_MAX_BYTES=1024 \
  bash "$REPO_ROOT/openclaw/workspace/scripts/presence-detect.sh" invalid \
  >/dev/null 2>&1 || true

rotated="$TEST_HOME/.openclaw/logs/presence-detect.log.1"
test -f "$rotated"
test "$(stat -f%z "$rotated")" -eq 2048

echo "test-presence-receive: PASS"
