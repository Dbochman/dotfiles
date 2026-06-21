#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/../.." && pwd)
TEST_HOME=$(mktemp -d)
trap 'rm -rf "$TEST_HOME"' EXIT

mkdir -p \
  "$TEST_HOME/.openclaw/presence/incoming" \
  "$TEST_HOME/.openclaw/logs" \
  "$TEST_HOME/.openclaw/workspace/scripts" \
  "$TEST_HOME/bin"

printf '%s\n' '{"source":"stale"}' \
  > "$TEST_HOME/.openclaw/presence/incoming/stdin.txt"

printf '%s\n' \
  '#!/usr/bin/env bash' \
  'echo "{\"evaluated\":true}"' \
  > "$TEST_HOME/.openclaw/workspace/scripts/presence-detect.sh"
chmod +x "$TEST_HOME/.openclaw/workspace/scripts/presence-detect.sh"

printf '%s\n' \
  '#!/usr/bin/env bash' \
  'destination="${@: -1}"' \
  'printf '\''%s\n'\'' '\''{"source":"fresh"}'\'' > "${destination%/}/stdin.txt"' \
  > "$TEST_HOME/bin/tailscale"
chmod +x "$TEST_HOME/bin/tailscale"

HOME="$TEST_HOME" PATH="$TEST_HOME/bin:/usr/bin:/bin" \
  bash "$REPO_ROOT/openclaw/workspace/scripts/presence-receive.sh"

python3 - "$TEST_HOME/.openclaw/presence/crosstown-scan.json" <<'PY'
import json
import sys

with open(sys.argv[1]) as state_file:
    state = json.load(state_file)
if state.get("source") != "fresh":
    raise SystemExit(f"expected fresh state, got {state!r}")
PY

grep -q "Recovered queued presence transfer" \
  "$TEST_HOME/.openclaw/logs/presence-detect.log"

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
