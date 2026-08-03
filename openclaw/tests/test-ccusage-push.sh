#!/bin/bash
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/../.." && pwd)
SCRIPT="$REPO_ROOT/openclaw/bin/ccusage-push.sh"
TEST_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/ccusage-push-test.XXXXXX")
trap 'rm -rf "$TEST_ROOT"' EXIT

cat > "$TEST_ROOT/npx-ok" <<'EOF'
#!/bin/bash
printf '%s\n' '{"daily":[{"period":"2026-08-01","totalTokens":10}]}'
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
  CCUSAGE_SSH_IDENTITY="$TEST_ROOT/test-identity" \
  CCUSAGE_MINI_HOST="test-mini" \
  CCUSAGE_REMOTE_DIR="/test/usage-history" \
    "$SCRIPT" >/dev/null 2>&1
}

touch "$TEST_ROOT/test-identity"

run_push "$TEST_ROOT/npx-ok" "$TEST_ROOT/scp-ok"

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
