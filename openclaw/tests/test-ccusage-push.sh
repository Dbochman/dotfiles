#!/bin/bash
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/../.." && pwd)
SCRIPT="$REPO_ROOT/openclaw/bin/ccusage-push.sh"
TEST_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/ccusage-push-test.XXXXXX")
trap 'rm -rf "$TEST_ROOT"' EXIT

cat > "$TEST_ROOT/npx-ok" <<'EOF'
#!/bin/bash
if [[ -n "${CCUSAGE_TEST_NPX_LOG:-}" ]]; then
  printf '%s\n' "$*" > "$CCUSAGE_TEST_NPX_LOG"
fi
printf '%s\n' '{"daily":[{"date":"2026-08-01","totalTokens":10,"reasoningOutputTokens":2,"costUSD":0.25}]}'
EOF
cat > "$TEST_ROOT/npx-invalid" <<'EOF'
#!/bin/bash
printf '%s\n' 'not json'
EOF
cat > "$TEST_ROOT/npx-fail" <<'EOF'
#!/bin/bash
exit 9
EOF
cat > "$TEST_ROOT/scp-ok" <<'EOF'
#!/bin/bash
for argument in "$@"; do
  test -s "$argument" 2>/dev/null && exit 0
done
exit 1
EOF
cat > "$TEST_ROOT/scp-fail" <<'EOF'
#!/bin/bash
exit 8
EOF
chmod +x "$TEST_ROOT"/*

run_push() {
  CCUSAGE_NPX="$1" \
  CCUSAGE_SCP="$2" \
  CCUSAGE_TEST_NPX_LOG="$TEST_ROOT/npx.args" \
  CCUSAGE_SSH_IDENTITY="$TEST_ROOT/test-identity" \
  CCUSAGE_MINI_HOST="test-mini" \
  CCUSAGE_REMOTE_DIR="/test/usage-history" \
    "$SCRIPT" >/dev/null 2>&1
}

run_local() {
  CCUSAGE_NPX="$1" \
  CCUSAGE_SCP="$TEST_ROOT/scp-fail" \
  CCUSAGE_TEST_NPX_LOG="$TEST_ROOT/npx.args" \
  CCUSAGE_SSH_IDENTITY="$TEST_ROOT/missing-identity" \
  CCUSAGE_LOCAL_DIR="$TEST_ROOT/local-history" \
    "$SCRIPT" >/dev/null 2>&1
}

touch "$TEST_ROOT/test-identity"

run_push "$TEST_ROOT/npx-ok" "$TEST_ROOT/scp-ok"
if ! grep -Eq '^--yes ccusage@20\.0\.19 codex daily --json --breakdown --offline --since [0-9]{4}-[0-9]{2}-[0-9]{2}$' "$TEST_ROOT/npx.args"; then
  echo "expected pinned explicit Codex collection command" >&2
  exit 1
fi

run_local "$TEST_ROOT/npx-ok"
LOCAL_RESULT=$(find "$TEST_ROOT/local-history" -maxdepth 1 -type f -name 'ccusage-codex-*.json' -print)
if [[ -z "$LOCAL_RESULT" ]] || [[ "$(stat -f '%Lp' "$LOCAL_RESULT")" != "600" ]]; then
  echo "expected owner-only local Codex usage result" >&2
  exit 1
fi
if ! /usr/bin/python3 -c 'import json,sys; data=json.load(open(sys.argv[1])); assert data["daily"][0]["reasoningOutputTokens"] == 2' "$LOCAL_RESULT"; then
  echo "expected valid local Codex usage data" >&2
  exit 1
fi

if run_push "$TEST_ROOT/npx-fail" "$TEST_ROOT/scp-ok"; then
  echo "expected ccusage collection failure to return nonzero" >&2
  exit 1
fi

if run_push "$TEST_ROOT/npx-invalid" "$TEST_ROOT/scp-ok"; then
  echo "expected invalid JSON to return nonzero" >&2
  exit 1
fi

if run_push "$TEST_ROOT/npx-ok" "$TEST_ROOT/scp-fail"; then
  echo "expected transfer failure to return nonzero" >&2
  exit 1
fi

echo "ccusage push tests passed"
