#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/../.." && pwd)
SCRIPT="$REPO_ROOT/openclaw/workspace/scripts/vacancy-actions.sh"
TEST_HOME=$(mktemp -d)
trap 'rm -rf "$TEST_HOME"' EXIT

PRESENCE_DIR="$TEST_HOME/.openclaw/presence"
MARKER_DIR="$PRESENCE_DIR/vacancy-dispatched"
CALLS_FILE="$TEST_HOME/device-calls"
FAKE_BIN="$TEST_HOME/fake-bin"

mkdir -p \
  "$PRESENCE_DIR" \
  "$MARKER_DIR" \
  "$TEST_HOME/.openclaw/logs" \
  "$FAKE_BIN"

# Every physical-device command resolves to this recorder. The restricted PATH
# below makes it impossible for this test to reach the real home-control CLIs.
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'cmd=$(basename "$0")' \
  '{' \
  '  printf "%s" "$cmd"' \
  '  for arg in "$@"; do' \
  '    printf "\\t%s" "$arg"' \
  '  done' \
  '  printf "\\n"' \
  '} >> "$FAKE_CALLS"' \
  'if [[ "$cmd" == "august" && "${1:-}" == "status" ]]; then' \
  '  printf "%s\\n" '\''{"state":{"locked":true}}'\''' \
  'fi' \
  'if [[ "$cmd" == "8sleep" && -n "${FAKE_8SLEEP_FAIL_ARGS:-}" && "$*" == "$FAKE_8SLEEP_FAIL_ARGS" ]]; then' \
  '  exit 1' \
  'fi' \
  > "$FAKE_BIN/device-recorder"
chmod +x "$FAKE_BIN/device-recorder"

for command_name in \
  hue nest cielo 8sleep august crosstown-roomba roomba; do
  ln -s device-recorder "$FAKE_BIN/$command_name"
done

export FAKE_CALLS="$CALLS_FILE"

write_state() {
  local crosstown_occupancy="$1" cabin_occupancy="$2"
  local dylan_location="$3" julia_location="$4"

  cat > "$PRESENCE_DIR/state.json" <<JSON
{
  "crosstown": {"occupancy": "$crosstown_occupancy"},
  "cabin": {"occupancy": "$cabin_occupancy"},
  "people": {
    "Dylan": {"location": "$dylan_location"},
    "Julia": {"location": "$julia_location"}
  }
}
JSON
}

run_vacancy_actions() {
  HOME="$TEST_HOME" \
    PATH="$FAKE_BIN:/usr/bin:/bin" \
    IMSG_BIN="$TEST_HOME/no-imsg" \
    bash "$SCRIPT"
}

assert_call() {
  local expected="$1"
  shift
  local arg
  for arg in "$@"; do
    expected="${expected}"$'\t'"${arg}"
  done

  if ! grep -Fqx "$expected" "$CALLS_FILE"; then
    echo "missing expected device call: $expected" >&2
    echo "recorded calls:" >&2
    sed 's/^/  /' "$CALLS_FILE" >&2
    exit 1
  fi
}

assert_call_count() {
  local expected="$1" actual
  actual=$(wc -l < "$CALLS_FILE" | tr -d ' ')
  if [[ "$actual" -ne "$expected" ]]; then
    echo "expected $expected device calls, got $actual" >&2
    sed 's/^/  /' "$CALLS_FILE" >&2
    exit 1
  fi
}

# Missing home markers reconcile both people to their sticky location.
write_state possibly_vacant possibly_vacant crosstown crosstown
: > "$CALLS_FILE"
run_vacancy_actions

assert_call 8sleep --location crosstown home dylan
assert_call 8sleep --location crosstown home julia
assert_call_count 2
test "$(cat "$MARKER_DIR/8sleep-dylan-home")" = "crosstown"
test "$(cat "$MARKER_DIR/8sleep-julia-home")" = "crosstown"

# Same-location state writes are permanently deduplicated. An old marker still
# suppresses reconciliation so a manual away/app override remains untouched.
touch -t 202001010000 "$MARKER_DIR/8sleep-dylan-home"
touch -t 202001010000 "$MARKER_DIR/8sleep-julia-home"
: > "$CALLS_FILE"
run_vacancy_actions
test ! -s "$CALLS_FILE"

# A positive sticky relocation changes marker content and moves both people.
write_state possibly_vacant possibly_vacant cabin cabin
: > "$CALLS_FILE"
run_vacancy_actions

assert_call 8sleep --location cabin home dylan
assert_call 8sleep --location cabin home julia
assert_call_count 2
test "$(cat "$MARKER_DIR/8sleep-dylan-home")" = "cabin"
test "$(cat "$MARKER_DIR/8sleep-julia-home")" = "cabin"

# Split households reconcile each person independently to their own location.
rm -f "$MARKER_DIR/8sleep-dylan-home" "$MARKER_DIR/8sleep-julia-home"
write_state possibly_vacant possibly_vacant crosstown cabin
: > "$CALLS_FILE"
run_vacancy_actions

assert_call 8sleep --location crosstown home dylan
assert_call 8sleep --location cabin home julia
assert_call_count 2
test "$(cat "$MARKER_DIR/8sleep-dylan-home")" = "crosstown"
test "$(cat "$MARKER_DIR/8sleep-julia-home")" = "cabin"

# Unknown locations are a no-op and preserve the last proven home assignment.
write_state possibly_vacant possibly_vacant unknown cabin
: > "$CALLS_FILE"
run_vacancy_actions
test ! -s "$CALLS_FILE"
test "$(cat "$MARKER_DIR/8sleep-dylan-home")" = "crosstown"
test "$(cat "$MARKER_DIR/8sleep-julia-home")" = "cabin"

# A partial per-person failure writes only the successful marker. The next
# identical state retries only the failed person and leaves no staging files.
rm -f "$MARKER_DIR/8sleep-dylan-home" "$MARKER_DIR/8sleep-julia-home"
write_state possibly_vacant possibly_vacant cabin cabin
export FAKE_8SLEEP_FAIL_ARGS="--location cabin home julia"
: > "$CALLS_FILE"
run_vacancy_actions
unset FAKE_8SLEEP_FAIL_ARGS

assert_call 8sleep --location cabin home dylan
assert_call 8sleep --location cabin home julia
assert_call_count 2
test "$(cat "$MARKER_DIR/8sleep-dylan-home")" = "cabin"
test ! -e "$MARKER_DIR/8sleep-julia-home"

: > "$CALLS_FILE"
run_vacancy_actions
assert_call 8sleep --location cabin home julia
assert_call_count 1
test "$(cat "$MARKER_DIR/8sleep-julia-home")" = "cabin"
if find "$MARKER_DIR" -name '8sleep-*-home.*' -print -quit | grep -q .; then
  echo "Eight Sleep home marker staging file was not cleaned up" >&2
  exit 1
fi

# Exercise a fresh general vacancy with the Eight Sleep action already marked
# as dispatched. Both successful Roomba starts must be counted; postfix
# arithmetic under `set -e` would abort after the first success and fail here.
printf '%s\n' crosstown > "$MARKER_DIR/8sleep-dylan-home"
printf '%s\n' crosstown > "$MARKER_DIR/8sleep-julia-home"
write_state occupied confirmed_vacant crosstown crosstown
: > "$CALLS_FILE"
run_vacancy_actions

assert_call hue --cabin all-off
assert_call nest eco cabin on
assert_call roomba start floomba
assert_call roomba start philly
assert_call_count 4
test -f "$MARKER_DIR/cabin"
grep -Fq 'Cabin Roombas: STARTED (2/2)' \
  "$TEST_HOME/.openclaw/logs/vacancy-actions.log"

# Exercise the real shell parser with a fake sibling API script. This validates
# the response contract without importing credentials or making network calls.
CLI_TEST_DIR="$TEST_HOME/8sleep-cli"
CLI_OUTPUT="$TEST_HOME/8sleep-cli-output"
mkdir -p "$CLI_TEST_DIR"
cp "$REPO_ROOT/openclaw/skills/8sleep/8sleep" "$CLI_TEST_DIR/8sleep"
printf '%s\n' \
  '#!/usr/bin/env python3' \
  'import os' \
  'print(os.environ["FAKE_8SLEEP_API_RESPONSE"])' \
  > "$CLI_TEST_DIR/8sleep-api.py"

assert_home_parser_rejects() {
  local response="$1" description="$2"
  if FAKE_8SLEEP_API_RESPONSE="$response" \
    "$CLI_TEST_DIR/8sleep" --location cabin home dylan \
    > "$CLI_OUTPUT" 2>&1; then
    echo "8sleep home parser accepted $description" >&2
    cat "$CLI_OUTPUT" >&2
    exit 1
  fi
  grep -Fq 'Error: Eight Sleep home verification returned an invalid response' \
    "$CLI_OUTPUT"
}

assert_home_parser_rejects '{}' 'an empty response'
assert_home_parser_rejects \
  '{"success":true,"state":"home","side":"julia","location":"cabin","changed":true,"response":{}}' \
  'a mismatched side'
assert_home_parser_rejects \
  '{"success":true,"state":"home","side":"dylan","location":"crosstown","changed":true,"response":{}}' \
  'a mismatched location'

FAKE_8SLEEP_API_RESPONSE='{"success":true,"state":"home","side":"dylan","location":"cabin","changed":true,"response":{}}' \
  "$CLI_TEST_DIR/8sleep" --location cabin home dylan \
  > "$CLI_OUTPUT" 2>&1
grep -Fqx 'Dylan home at Cabin (updated)' "$CLI_OUTPUT"

echo "test-vacancy-actions: PASS"
