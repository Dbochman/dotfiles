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
FAKE_PRESENCE_SCANNER="$TEST_HOME/fake-presence-scanner"
FAKE_JOURNAL="$TEST_HOME/fake-vacancy-action-journal"
JOURNAL_CALLS_FILE="$TEST_HOME/journal-calls"

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
  '  printf '\''{"state":{"locked":%s}}\n'\'' "${FAKE_AUGUST_LOCKED:-true}"' \
  'fi' \
  'if [[ "$cmd" == "august" && "${1:-}" == "lock" ]]; then' \
  '  printf "%s\\n" '\''{"state":{"locked":true}}'\''' \
  'fi' \
  'if [[ "$cmd" == "crosstown-vacant-roomba" ]]; then' \
  '  printf "{\"ok\":true,\"outcome\":\"%s\"}\\n" "${FAKE_VACANT_ROOMBA_OUTCOME:-started}"' \
  'fi' \
  'if [[ "$cmd" == "8sleep" && -n "${FAKE_8SLEEP_FAIL_ARGS:-}" && "$*" == "$FAKE_8SLEEP_FAIL_ARGS" ]]; then' \
  '  exit 1' \
  'fi' \
  > "$FAKE_BIN/device-recorder"
chmod +x "$FAKE_BIN/device-recorder"

for command_name in \
  hue nest cielo 8sleep august crosstown-vacant-roomba roomba imsg; do
  ln -s device-recorder "$FAKE_BIN/$command_name"
done

printf '%s\n' \
  '#!/usr/bin/env bash' \
  '[[ "$*" == "validate-config cabin" && "${FAKE_CABIN_ENROLLMENT_ACTIVE:-0}" == "1" ]]' \
  > "$FAKE_PRESENCE_SCANNER"
chmod +x "$FAKE_PRESENCE_SCANNER"

printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'printf "%s\n" "$*" >> "$FAKE_JOURNAL_CALLS"' \
  'case "${1:-}" in' \
  '  recover) printf "%s\n" '\''{"ok":true,"recovered":0}'\'' ;;' \
  '  begin-run) printf "%s\n" '\''{"ok":true,"run_id":"run_11111111111111111111111111111111","cycle_id":"cycle_22222222222222222222222222222222"}'\'' ;;' \
  '  begin-action) printf "%s\n" '\''{"ok":true,"attempt_id":"attempt_33333333333333333333333333333333"}'\'' ;;' \
  '  finish-action) printf "%s\n" '\''{"ok":true}'\'' ;;' \
  '  complete-run) printf "%s\n" '\''{"ok":true}'\'' ;;' \
  '  *) exit 2 ;;' \
  'esac' \
  > "$FAKE_JOURNAL"
chmod +x "$FAKE_JOURNAL"

export FAKE_CALLS="$CALLS_FILE"
export FAKE_JOURNAL_CALLS="$JOURNAL_CALLS_FILE"

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
    IMSG_BIN="${FAKE_IMSG_BIN:-$TEST_HOME/no-imsg}" \
    PRESENCE_SCANNER="$FAKE_PRESENCE_SCANNER" \
    VACANCY_ACTION_JOURNAL="${VACANCY_ACTION_JOURNAL_OVERRIDE:-$TEST_HOME/no-journal}" \
    CROSSTOWN_VACANT_ROOMBA="$FAKE_BIN/crosstown-vacant-roomba" \
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
test "$(cat "$MARKER_DIR/8sleep-dylan-home")" = "crosstown:own"
test "$(cat "$MARKER_DIR/8sleep-julia-home")" = "crosstown:own"

# Same-location state writes are permanently deduplicated. An old marker still
# suppresses reconciliation so a manual away/app override remains untouched.
touch -t 202001010000 "$MARKER_DIR/8sleep-dylan-home"
touch -t 202001010000 "$MARKER_DIR/8sleep-julia-home"
: > "$CALLS_FILE"
run_vacancy_actions
test ! -s "$CALLS_FILE"

# Before Julia's strict Cabin enrollment, a positive Cabin location moves Dylan
# but pins Julia to Crosstown rather than trusting the permissive fallback.
write_state possibly_vacant possibly_vacant cabin cabin
: > "$CALLS_FILE"
run_vacancy_actions

assert_call 8sleep --location cabin home dylan
if grep -Fq $'8sleep\t--location\tcabin\thome\tjulia' "$CALLS_FILE"; then
  echo "unenrolled Julia was relocated to the Cabin Pod" >&2
  exit 1
fi
assert_call_count 1
test "$(cat "$MARKER_DIR/8sleep-dylan-home")" = "cabin:own"
test "$(cat "$MARKER_DIR/8sleep-julia-home")" = "crosstown:own"
grep -Fq 'Pinning Eight Sleep julia to crosstown' \
  "$TEST_HOME/.openclaw/logs/vacancy-actions.log"

# The same interim pin applies when only Julia is assigned to the Cabin.
rm -f "$MARKER_DIR/8sleep-dylan-home" "$MARKER_DIR/8sleep-julia-home"
write_state possibly_vacant possibly_vacant crosstown cabin
: > "$CALLS_FILE"
run_vacancy_actions

assert_call 8sleep --location crosstown home dylan
assert_call 8sleep --location crosstown home julia
assert_call_count 2
test "$(cat "$MARKER_DIR/8sleep-dylan-home")" = "crosstown:own"
test "$(cat "$MARKER_DIR/8sleep-julia-home")" = "crosstown:own"

# Unknown locations are a no-op and preserve the last proven home assignment.
write_state possibly_vacant possibly_vacant unknown cabin
: > "$CALLS_FILE"
run_vacancy_actions
test ! -s "$CALLS_FILE"
test "$(cat "$MARKER_DIR/8sleep-dylan-home")" = "crosstown:own"
test "$(cat "$MARKER_DIR/8sleep-julia-home")" = "crosstown:own"

# Once the deployed strict scanner validates the production enrollment, Julia
# can move to the Cabin again. A partial failure still writes only the
# successful marker; the next identical state retries only the failed person.
rm -f "$MARKER_DIR/8sleep-dylan-home" "$MARKER_DIR/8sleep-julia-home"
write_state possibly_vacant possibly_vacant cabin cabin
export FAKE_CABIN_ENROLLMENT_ACTIVE=1
export FAKE_8SLEEP_FAIL_ARGS="--location cabin home julia"
: > "$CALLS_FILE"
run_vacancy_actions
unset FAKE_8SLEEP_FAIL_ARGS

assert_call 8sleep --location cabin home dylan
assert_call 8sleep --location cabin home julia
assert_call_count 2
test "$(cat "$MARKER_DIR/8sleep-dylan-home")" = "cabin:own"
test ! -e "$MARKER_DIR/8sleep-julia-home"

: > "$CALLS_FILE"
run_vacancy_actions
assert_call 8sleep --location cabin home julia
assert_call_count 1
test "$(cat "$MARKER_DIR/8sleep-julia-home")" = "cabin:own"
unset FAKE_CABIN_ENROLLMENT_ACTIVE
if find "$MARKER_DIR" -name '8sleep-*-home.*' -print -quit | grep -q .; then
  echo "Eight Sleep home marker staging file was not cleaned up" >&2
  exit 1
fi

# A verified split household gives each sole occupant both sides of their Pod.
write_state possibly_vacant possibly_vacant crosstown cabin
export FAKE_CABIN_ENROLLMENT_ACTIVE=1
: > "$CALLS_FILE"
run_vacancy_actions

assert_call 8sleep --location crosstown home dylan both
assert_call 8sleep --location cabin home julia both
assert_call_count 2
test "$(cat "$MARKER_DIR/8sleep-dylan-home")" = "crosstown:both"
test "$(cat "$MARKER_DIR/8sleep-julia-home")" = "cabin:both"

# Reuniting restores each resident's normal side even though the location of
# the resident who stayed behind did not change.
write_state possibly_vacant possibly_vacant cabin cabin
: > "$CALLS_FILE"
run_vacancy_actions

assert_call 8sleep --location cabin home dylan
assert_call 8sleep --location cabin home julia
assert_call_count 2
test "$(cat "$MARKER_DIR/8sleep-dylan-home")" = "cabin:own"
test "$(cat "$MARKER_DIR/8sleep-julia-home")" = "cabin:own"
unset FAKE_CABIN_ENROLLMENT_ACTIVE

# Exercise a fresh general vacancy with the Eight Sleep action already marked
# as dispatched. Both successful Roomba starts must be counted; postfix
# arithmetic under `set -e` would abort after the first success and fail here.
printf '%s\n' crosstown:own > "$MARKER_DIR/8sleep-dylan-home"
printf '%s\n' crosstown:own > "$MARKER_DIR/8sleep-julia-home"
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

# The dashboard snooze is shared by dog-walk and vacancy automation. Other
# vacancy actions still run and the marker is written. The asymmetric cases
# below prove each location is controlled independently.
mkdir -p "$TEST_HOME/.openclaw/dog-walk"
cat > "$TEST_HOME/.openclaw/dog-walk/snooze.json" <<'JSON'
{"cabin":"2999-01-01T00:00:00Z","crosstown":null}
JSON

rm -f "$MARKER_DIR/cabin"
write_state occupied confirmed_vacant crosstown crosstown
: > "$CALLS_FILE"
run_vacancy_actions

assert_call hue --cabin all-off
assert_call nest eco cabin on
assert_call_count 2
test -f "$MARKER_DIR/cabin"
if grep -Eq '^roomba\tstart\t' "$CALLS_FILE"; then
  echo "cabin snooze allowed a Roomba start" >&2
  exit 1
fi
grep -Fq 'cabin Roomba automation: SKIPPED (snoozed)' \
  "$TEST_HOME/.openclaw/logs/vacancy-actions.log"

# The cabin snooze is scoped: an explicit null leaves Crosstown automation
# enabled and preserves its legacy start behavior.
rm -f "$MARKER_DIR/crosstown"
write_state confirmed_vacant occupied cabin cabin
printf '%s\n' cabin:own > "$MARKER_DIR/8sleep-dylan-home"
printf '%s\n' crosstown:own > "$MARKER_DIR/8sleep-julia-home"
: > "$CALLS_FILE"
run_vacancy_actions

assert_call crosstown-vacant-roomba --source vacancy_transition
assert_call_count 7

# An expired timestamp is also clear and preserves the same start behavior.
cat > "$TEST_HOME/.openclaw/dog-walk/snooze.json" <<'JSON'
{"cabin":null,"crosstown":"2000-01-01T00:00:00Z"}
JSON
rm -f "$MARKER_DIR/crosstown"
: > "$CALLS_FILE"
run_vacancy_actions

assert_call crosstown-vacant-roomba --source vacancy_transition
assert_call_count 7

# Reversing the policy snoozes only Crosstown.
cat > "$TEST_HOME/.openclaw/dog-walk/snooze.json" <<'JSON'
{"cabin":null,"crosstown":"2999-01-01T00:00:00Z"}
JSON
rm -f "$MARKER_DIR/crosstown"
: > "$CALLS_FILE"
export FAKE_VACANT_ROOMBA_OUTCOME=snoozed
run_vacancy_actions
unset FAKE_VACANT_ROOMBA_OUTCOME

assert_call hue --crosstown all-off
assert_call nest eco crosstown on
assert_call cielo off -d bedroom
assert_call cielo off -d office
assert_call cielo off -d "living room"
assert_call august status
assert_call crosstown-vacant-roomba --source vacancy_transition
assert_call_count 7
test -f "$MARKER_DIR/crosstown"
if ! grep -Fq 'Crosstown Roombas: SKIPPED (snoozed)' \
    "$TEST_HOME/.openclaw/logs/vacancy-actions.log"; then
  echo "missing snoozed Crosstown Roomba outcome" >&2
  tail -20 "$TEST_HOME/.openclaw/logs/vacancy-actions.log" >&2
  exit 1
fi

# A malformed falsey value fails closed rather than silently enabling a start.
printf '%s\n' '{"cabin":false}' > "$TEST_HOME/.openclaw/dog-walk/snooze.json"
rm -f "$MARKER_DIR/cabin"
write_state occupied confirmed_vacant crosstown crosstown
printf '%s\n' crosstown:own > "$MARKER_DIR/8sleep-dylan-home"
printf '%s\n' crosstown:own > "$MARKER_DIR/8sleep-julia-home"
: > "$CALLS_FILE"
run_vacancy_actions

assert_call hue --cabin all-off
assert_call nest eco cabin on
assert_call_count 2
if grep -Eq '^roomba\tstart\t' "$CALLS_FILE"; then
  echo "invalid snooze policy allowed a Roomba start" >&2
  exit 1
fi
grep -Fq 'WARN: Invalid Roomba snooze policy; skipping cabin start' \
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
  '{"success":true,"state":"home","side":"julia","location":"cabin","coverage":"own","changed":true,"response":{}}' \
  'a mismatched side'
assert_home_parser_rejects \
  '{"success":true,"state":"home","side":"dylan","location":"crosstown","coverage":"own","changed":true,"response":{}}' \
  'a mismatched location'

FAKE_8SLEEP_API_RESPONSE='{"success":true,"state":"home","side":"dylan","location":"cabin","coverage":"own","changed":true,"response":{}}' \
  "$CLI_TEST_DIR/8sleep" --location cabin home dylan \
  > "$CLI_OUTPUT" 2>&1
grep -Fqx 'Dylan home at Cabin, own side (updated)' "$CLI_OUTPUT"

FAKE_8SLEEP_API_RESPONSE='{"success":true,"state":"home","side":"dylan","location":"cabin","coverage":"both","changed":true,"response":{}}' \
  "$CLI_TEST_DIR/8sleep" --location cabin home dylan both \
  > "$CLI_OUTPUT" 2>&1
grep -Fqx 'Dylan home at Cabin, both sides (updated)' "$CLI_OUTPUT"

# Observation-only journaling wraps the exact legacy commands without changing
# their count, arguments, order, or marker behavior.
rm -f "$TEST_HOME/.openclaw/dog-walk/snooze.json" "$MARKER_DIR/cabin"
write_state occupied confirmed_vacant crosstown crosstown
printf '%s\n' crosstown:own > "$MARKER_DIR/8sleep-dylan-home"
printf '%s\n' crosstown:own > "$MARKER_DIR/8sleep-julia-home"
: > "$CALLS_FILE"
: > "$JOURNAL_CALLS_FILE"
export VACANCY_ACTION_JOURNAL_OVERRIDE="$FAKE_JOURNAL"
run_vacancy_actions
unset VACANCY_ACTION_JOURNAL_OVERRIDE

assert_call hue --cabin all-off
assert_call nest eco cabin on
assert_call roomba start floomba
assert_call roomba start philly
assert_call_count 4
test -f "$MARKER_DIR/cabin"
test "$(wc -l < "$JOURNAL_CALLS_FILE" | tr -d ' ')" -eq 11
grep -Fqx 'recover' "$JOURNAL_CALLS_FILE"
grep -Fqx \
  'begin-action --run-id run_11111111111111111111111111111111 --target all_lights --action turn_off' \
  "$JOURNAL_CALLS_FILE"
grep -Fqx \
  'finish-action --run-id run_11111111111111111111111111111111 --attempt-id attempt_33333333333333333333333333333333 --outcome command_accepted --verification command_exit --reason-code completed' \
  "$JOURNAL_CALLS_FILE"
grep -Fqx \
  'complete-run --run-id run_11111111111111111111111111111111' \
  "$JOURNAL_CALLS_FILE"

# Exact bus ownership removes only Crosstown Hue from the legacy executor.
mkdir -p \
  "$TEST_HOME/.openclaw/home-events/config" \
  "$TEST_HOME/.openclaw/bin"
printf '%s\n' '{"active":true}' \
  > "$TEST_HOME/.openclaw/home-events/config/action-policy.json"
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'site=""' \
  'while [[ $# -gt 0 ]]; do case "$1" in --site) site="$2"; shift 2 ;; *) shift ;; esac; done' \
  'if [[ "$site" == crosstown ]]; then printf "%s\n" '\''{"ok":true,"owner":"bus"}'\''; else printf "%s\n" '\''{"ok":true,"owner":"legacy"}'\''; fi' \
  > "$TEST_HOME/.openclaw/bin/home-event-action"
chmod 600 "$TEST_HOME/.openclaw/home-events/config/action-policy.json"
chmod +x "$TEST_HOME/.openclaw/bin/home-event-action"
rm -f "$MARKER_DIR/crosstown"
write_state confirmed_vacant occupied crosstown crosstown
printf '%s\n' crosstown:own > "$MARKER_DIR/8sleep-dylan-home"
printf '%s\n' crosstown:own > "$MARKER_DIR/8sleep-julia-home"
: > "$CALLS_FILE"
: > "$JOURNAL_CALLS_FILE"
export VACANCY_ACTION_JOURNAL_OVERRIDE="$FAKE_JOURNAL"
run_vacancy_actions
unset VACANCY_ACTION_JOURNAL_OVERRIDE

if grep -Eq '^hue\t--crosstown\tall-off$' "$CALLS_FILE"; then
  echo "bus-owned Crosstown Hue still ran through the legacy executor" >&2
  exit 1
fi
assert_call nest eco crosstown on
assert_call cielo off -d bedroom
assert_call cielo off -d office
assert_call cielo off -d "living room"
assert_call august status
assert_call crosstown-vacant-roomba --source vacancy_transition
assert_call_count 6
grep -Fqx \
  'finish-action --run-id run_11111111111111111111111111111111 --attempt-id attempt_33333333333333333333333333333333 --outcome skipped --verification policy_decision --reason-code delegated_to_event_bus' \
  "$JOURNAL_CALLS_FILE"

# An already-secure front door is intentionally silent. A successful lock
# attempt still sends its existing notification.
rm -f "$MARKER_DIR/crosstown"
: > "$CALLS_FILE"
export FAKE_IMSG_BIN="$FAKE_BIN/imsg"
run_vacancy_actions
if grep -Eq '^imsg\t' "$CALLS_FILE"; then
  echo "already-locked Crosstown vacancy sent an iMessage" >&2
  exit 1
fi

rm -f "$MARKER_DIR/crosstown"
: > "$CALLS_FILE"
export FAKE_AUGUST_LOCKED=false
run_vacancy_actions
unset FAKE_AUGUST_LOCKED FAKE_IMSG_BIN
assert_call august lock
assert_call imsg send --chat-id 171 --service imessage \
  --text "🔒 Crosstown vacant — front door locked automatically" --json

echo "test-vacancy-actions: PASS"
