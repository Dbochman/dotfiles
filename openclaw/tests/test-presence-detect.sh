#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/../.." && pwd)
EVALUATOR="$REPO_ROOT/openclaw/workspace/scripts/presence-detect.sh"
VACANCY_ACTIONS="$REPO_ROOT/openclaw/workspace/scripts/vacancy-actions.sh"
TEST_HOME=$(mktemp -d)
LOCK_HOLDER_PID=""
BLOCKED_EVALUATOR_PID=""
LOCK_RELEASE_FILE=""
SCAN_READ_RELEASE_FILE=""

cleanup() {
  if [[ -n "$LOCK_RELEASE_FILE" ]]; then
    : > "$LOCK_RELEASE_FILE"
  fi
  if [[ -n "$SCAN_READ_RELEASE_FILE" ]]; then
    : > "$SCAN_READ_RELEASE_FILE"
  fi
  if [[ -n "$BLOCKED_EVALUATOR_PID" ]]; then
    kill "$BLOCKED_EVALUATOR_PID" 2>/dev/null || true
    wait "$BLOCKED_EVALUATOR_PID" 2>/dev/null || true
  fi
  if [[ -n "$LOCK_HOLDER_PID" ]]; then
    kill "$LOCK_HOLDER_PID" 2>/dev/null || true
    wait "$LOCK_HOLDER_PID" 2>/dev/null || true
  fi
  rm -rf "$TEST_HOME"
}
trap cleanup EXIT

PRESENCE_DIR="$TEST_HOME/.openclaw/presence"
MARKER_DIR="$PRESENCE_DIR/vacancy-dispatched"
CALLS_FILE="$TEST_HOME/device-message-calls"
FAKE_BIN="$TEST_HOME/fake-bin"
SAFE_BIN="$TEST_HOME/safe-bin"
SCAN_READ_MARKER="$TEST_HOME/evaluator-scan-read"

mkdir -p \
  "$PRESENCE_DIR" \
  "$MARKER_DIR" \
  "$TEST_HOME/.openclaw/logs" \
  "$FAKE_BIN" \
  "$SAFE_BIN"

# The action subprocess receives an empty environment and an allowlisted PATH.
# Every physical-device and message command used by vacancy-actions resolves to
# this recorder, so the regression cannot reach the real integrations.
printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  'cmd=${0##*/}' \
  'printf "%s" "$cmd" >> "$FAKE_CALLS"' \
  'for arg in "$@"; do printf "\\t%s" "$arg" >> "$FAKE_CALLS"; done' \
  'printf "\\n" >> "$FAKE_CALLS"' \
  'if [[ "$cmd" == "august" && "${1:-}" == "status" ]]; then' \
  '  printf "%s\\n" '\''{"state":{"locked":true}}'\''' \
  'fi' \
  > "$FAKE_BIN/device-recorder"
chmod +x "$FAKE_BIN/device-recorder"

for command_name in \
  hue nest cielo 8sleep august crosstown-roomba roomba imsg; do
  ln -s device-recorder "$FAKE_BIN/$command_name"
done

# Only non-network utilities required by evaluate/vacancy-actions are exposed.
for utility in bash cat date dirname mkdir mv node python3 rm; do
  utility_path=$(command -v "$utility")
  ln -s "$utility_path" "$SAFE_BIN/$utility"
done

: > "$CALLS_FILE"

timestamps=$(python3 - <<'PY'
from datetime import datetime, timedelta, timezone

now = datetime.now(timezone.utc)
print(
    *[
        (now - timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")
        for minutes in (75, 65, 8, 7, 6, 5)
    ],
    sep="\t",
)
PY
)
IFS=$'\t' read -r stale_cabin stale_crosstown fresh_1 fresh_2 fresh_3 fresh_4 \
  <<< "$timestamps"

write_scan() {
  local path="$1" location="$2" timestamp="$3" dylan="$4" julia="$5"

  cat > "$path" <<JSON
{"timestamp":"$timestamp","location":"$location","presence":{"Dylan":{"present":$dylan},"Julia":{"present":$julia}}}
JSON
}

write_all_presence() {
  local cabin_timestamp="$1" cabin_present="$2"
  local crosstown_timestamp="$3" crosstown_present="$4"

  write_scan \
    "$PRESENCE_DIR/cabin-scan.json" cabin \
    "$cabin_timestamp" "$cabin_present" "$cabin_present"
  write_scan \
    "$PRESENCE_DIR/crosstown-scan.json" crosstown \
    "$crosstown_timestamp" "$crosstown_present" "$crosstown_present"
}

reset_evaluator() {
  rm -f \
    "$PRESENCE_DIR/cabin-scan.json" \
    "$PRESENCE_DIR/crosstown-scan.json" \
    "$PRESENCE_DIR/potato-scan.json" \
    "$PRESENCE_DIR/state.json" \
    "$PRESENCE_DIR/prev-evaluated.json" \
    "$PRESENCE_DIR/events.json"
}

evaluate() {
  local label="$1" output

  output="$TEST_HOME/evaluator-$label.out"

  # A failed Node evaluation must not be masked by state from an earlier
  # fixture step. Keep prev-evaluated.json for sticky sequencing.
  rm -f "$PRESENCE_DIR/state.json"

  if ! /usr/bin/env -i \
      HOME="$TEST_HOME" \
      PATH="$SAFE_BIN" \
      SCAN_READ_MARKER="$SCAN_READ_MARKER" \
      SCAN_READ_RELEASE_FILE="$SCAN_READ_RELEASE_FILE" \
      /bin/bash "$EVALUATOR" evaluate > "$output"; then
    echo "fixture evaluation failed: $label" >&2
    sed 's/^/  /' "$output" >&2
    return 1
  fi

  python3 - "$output" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as output_file:
    output = json.load(output_file)
if output.get("error") == "evaluate_failed":
    raise SystemExit("embedded Node evaluator returned evaluate_failed")
if "error" in output:
    raise SystemExit(f"fixture evaluator returned an error: {output['error']}")
PY

  if [[ ! -s "$PRESENCE_DIR/state.json" ]]; then
    echo "fixture evaluation did not create a nonempty state: $label" >&2
    return 1
  fi
}

run_vacancy_actions() {
  /usr/bin/env -i \
    HOME="$TEST_HOME" \
    PATH="$FAKE_BIN:$SAFE_BIN" \
    FAKE_CALLS="$CALLS_FILE" \
    IMSG_BIN="$FAKE_BIN/imsg" \
    /bin/bash "$VACANCY_ACTIONS"
}

assert_all_people_at() {
  local expected_location="$1" cabin_occupancy="$2" crosstown_occupancy="$3"

  python3 - \
    "$PRESENCE_DIR/state.json" \
    "$expected_location" \
    "$cabin_occupancy" \
    "$crosstown_occupancy" <<'PY'
import json
import sys

path, expected_location, cabin_occupancy, crosstown_occupancy = sys.argv[1:]
with open(path, encoding="utf-8") as state_file:
    state = json.load(state_file)

for person in ("Dylan", "Julia"):
    actual = state["people"][person]["location"]
    if actual != expected_location:
        raise SystemExit(f"expected {person} at {expected_location}, got {actual}")
if state["cabin"]["occupancy"] != cabin_occupancy:
    raise SystemExit(f"expected cabin {cabin_occupancy}, got {state['cabin']['occupancy']}")
if state["crosstown"]["occupancy"] != crosstown_occupancy:
    raise SystemExit(
        f"expected Crosstown {crosstown_occupancy}, got {state['crosstown']['occupancy']}"
    )
PY
}

assert_no_device_or_message_calls() {
  if [[ -s "$CALLS_FILE" ]]; then
    echo "unexpected device/message calls:" >&2
    sed 's/^/  /' "$CALLS_FILE" >&2
    exit 1
  fi
}

assert_split_state_is_safe() {
  local label="$1"

  python3 - "$PRESENCE_DIR/state.json" "$label" <<'PY'
import json
import sys

path, label = sys.argv[1:]
with open(path, encoding="utf-8") as state_file:
    state = json.load(state_file)

expected_locations = {"Dylan": "cabin", "Julia": "crosstown"}
for person, expected in expected_locations.items():
    person_state = state["people"][person]
    actual = person_state["location"]
    if actual != expected:
        raise SystemExit(f"{label}: expected {person} at {expected}, got {actual}")
    if person_state["cabin"] != (expected == "cabin"):
        raise SystemExit(f"{label}: inconsistent cabin flag for {person}: {person_state!r}")
    if person_state["crosstown"] != (expected == "crosstown"):
        raise SystemExit(f"{label}: inconsistent Crosstown flag for {person}: {person_state!r}")
for location in ("cabin", "crosstown"):
    occupancy = state[location]["occupancy"]
    if occupancy != "occupied":
        raise SystemExit(f"{label}: expected {location} occupied, got {occupancy}")
if state["transitions"]:
    raise SystemExit(f"{label}: unexpected relocation/vacancy transitions: {state['transitions']!r}")
PY

  test ! -e "$PRESENCE_DIR/events.json"
  test ! -e "$MARKER_DIR/cabin"
  test ! -e "$MARKER_DIR/crosstown"
  run_vacancy_actions
  test ! -e "$MARKER_DIR/cabin"
  test ! -e "$MARKER_DIR/crosstown"
  assert_no_device_or_message_calls
}

# With no sticky location, simultaneous fresh positives are ambiguous. Direct
# positives veto vacancy at both locations without manufacturing a relocation.
reset_evaluator
write_all_presence "$fresh_1" true "$fresh_2" true
evaluate dual-fresh-without-sticky
python3 - "$PRESENCE_DIR/state.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as state_file:
    state = json.load(state_file)
for person in ("Dylan", "Julia"):
    if state["people"][person]["location"] != "unknown":
        raise SystemExit(f"dual-fresh evidence assigned {person}: {state['people'][person]}")
for location in ("cabin", "crosstown"):
    if state[location]["occupancy"] != "occupied":
        raise SystemExit(f"dual-fresh evidence made {location} {state[location]['occupancy']}")
if state["transitions"]:
    raise SystemExit(f"dual-fresh evidence emitted transitions: {state['transitions']!r}")
PY
test ! -e "$PRESENCE_DIR/events.json"

# Reproduce the observed four-evaluation ordering. Start from an unambiguous
# split household, then keep Dylan dual-positive while the newer snapshot
# alternates Crosstown/Cabin/Crosstown. Scan recency must never manufacture a
# relocation, a vacancy transition, or a physical action.
reset_evaluator
rm -f \
  "$MARKER_DIR/cabin" \
  "$MARKER_DIR/crosstown" \
  "$MARKER_DIR/8sleep-dylan-home" \
  "$MARKER_DIR/8sleep-julia-home"
printf '%s\n' cabin > "$MARKER_DIR/8sleep-dylan-home"
printf '%s\n' crosstown > "$MARKER_DIR/8sleep-julia-home"
: > "$CALLS_FILE"

write_scan "$PRESENCE_DIR/cabin-scan.json" cabin "$fresh_1" true false
write_scan "$PRESENCE_DIR/crosstown-scan.json" crosstown "$fresh_1" false true
evaluate split-unambiguous
assert_split_state_is_safe split-unambiguous

write_scan "$PRESENCE_DIR/crosstown-scan.json" crosstown "$fresh_2" true true
evaluate split-crosstown-newer
assert_split_state_is_safe split-crosstown-newer

write_scan "$PRESENCE_DIR/cabin-scan.json" cabin "$fresh_3" true false
evaluate split-cabin-newer
assert_split_state_is_safe split-cabin-newer

write_scan "$PRESENCE_DIR/crosstown-scan.json" crosstown "$fresh_4" true true
evaluate split-crosstown-newest
assert_split_state_is_safe split-crosstown-newest

# A stale old-location positive does not create ambiguity. The sole fresh
# opposite positive proves the arrival and relocates in either direction.
reset_evaluator
write_all_presence "$fresh_1" true "$fresh_2" false
evaluate initial-cabin
assert_all_people_at cabin occupied confirmed_vacant

write_all_presence "$stale_cabin" true "$fresh_3" true
evaluate stale-cabin-fresh-crosstown
assert_all_people_at crosstown confirmed_vacant occupied
python3 - "$PRESENCE_DIR/state.json" cabin crosstown <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as state_file:
    state = json.load(state_file)
for person in ("Dylan", "Julia"):
    matches = [
        transition
        for transition in state["transitions"]
        if transition.get("person") == person
        and transition.get("event") == "relocated"
        and transition.get("from") == sys.argv[2]
        and transition.get("to") == sys.argv[3]
    ]
    if len(matches) != 1:
        raise SystemExit(f"expected one fresh relocation for {person}: {state['transitions']!r}")
PY

write_all_presence "$fresh_4" true "$stale_crosstown" true
evaluate stale-crosstown-fresh-cabin
assert_all_people_at cabin occupied confirmed_vacant
python3 - "$PRESENCE_DIR/state.json" crosstown cabin <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as state_file:
    state = json.load(state_file)
for person in ("Dylan", "Julia"):
    matches = [
        transition
        for transition in state["transitions"]
        if transition.get("person") == person
        and transition.get("event") == "relocated"
        and transition.get("from") == sys.argv[2]
        and transition.get("to") == sys.argv[3]
    ]
    if len(matches) != 1:
        raise SystemExit(f"expected one fresh relocation for {person}: {state['transitions']!r}")
PY

exercise_marker_ambiguity() {
  local vacant="$1" occupied="$2"
  local cabin_initial=false crosstown_initial=false
  local cabin_vacancy_occupancy crosstown_vacancy_occupancy

  if [[ "$occupied" == "cabin" ]]; then
    cabin_initial=true
    cabin_vacancy_occupancy=occupied
    crosstown_vacancy_occupancy=confirmed_vacant
  else
    crosstown_initial=true
    cabin_vacancy_occupancy=confirmed_vacant
    crosstown_vacancy_occupancy=occupied
  fi

  reset_evaluator
  rm -f "$MARKER_DIR/cabin" "$MARKER_DIR/crosstown"
  write_all_presence "$fresh_1" "$cabin_initial" "$fresh_2" "$crosstown_initial"
  evaluate "marker-$vacant-initial"
  assert_all_people_at \
    "$occupied" "$cabin_vacancy_occupancy" "$crosstown_vacancy_occupancy"

  printf '%s\n' existing-vacancy > "$MARKER_DIR/$vacant"
  printf '%s\n' "$occupied" > "$MARKER_DIR/8sleep-dylan-home"
  printf '%s\n' "$occupied" > "$MARKER_DIR/8sleep-julia-home"
  : > "$CALLS_FILE"

  # Fresh positives at both locations only veto vacancy. They retain the last
  # unambiguous sticky assignment and must not clear the vacancy marker.
  write_all_presence "$fresh_3" true "$fresh_4" true
  evaluate "marker-$vacant-ambiguous"
  assert_all_people_at "$occupied" occupied occupied
  run_vacancy_actions
  test -f "$MARKER_DIR/$vacant"
  assert_no_device_or_message_calls

  # Removing the conflicting positive makes the location vacant again. The
  # preserved marker prevents every physical action from dispatching twice.
  write_all_presence "$fresh_3" "$cabin_initial" "$fresh_4" "$crosstown_initial"
  evaluate "marker-$vacant-resolved"
  assert_all_people_at \
    "$occupied" "$cabin_vacancy_occupancy" "$crosstown_vacancy_occupancy"
  run_vacancy_actions
  test -f "$MARKER_DIR/$vacant"
  assert_no_device_or_message_calls

  # A sole fresh positive at the vacant location is a genuine arrival and may
  # clear its marker. Mark the newly vacant opposite side to suppress its
  # already-dispatched actions while this branch is exercised.
  printf '%s\n' existing-vacancy > "$MARKER_DIR/$occupied"
  printf '%s\n' "$vacant" > "$MARKER_DIR/8sleep-dylan-home"
  printf '%s\n' "$vacant" > "$MARKER_DIR/8sleep-julia-home"
  if [[ "$vacant" == "cabin" ]]; then
    write_all_presence "$fresh_3" true "$fresh_4" false
    cabin_vacancy_occupancy=occupied
    crosstown_vacancy_occupancy=confirmed_vacant
  else
    write_all_presence "$fresh_3" false "$fresh_4" true
    cabin_vacancy_occupancy=confirmed_vacant
    crosstown_vacancy_occupancy=occupied
  fi
  evaluate "marker-$vacant-genuine-arrival"
  assert_all_people_at \
    "$vacant" "$cabin_vacancy_occupancy" "$crosstown_vacancy_occupancy"
  run_vacancy_actions
  test ! -e "$MARKER_DIR/$vacant"
  test -f "$MARKER_DIR/$occupied"
  assert_no_device_or_message_calls
}

# Exercise marker retention and genuine-arrival clearing symmetrically without
# duplicating the fixture-only device/message harness.
exercise_marker_ambiguity cabin crosstown
exercise_marker_ambiguity crosstown cabin

# Hold the production evaluator lock before starting a fixture evaluation. A
# restricted cat wrapper records the first scan read, proving the evaluator
# cannot snapshot the old fixtures while blocked. Replace both scans, release
# the lock, and require the committed state to reflect only the replacements.
rm -f "$SAFE_BIN/cat"
printf '%s\n' \
  '#!/bin/bash' \
  'set -euo pipefail' \
  'case "${1:-}" in' \
  '  "$HOME/.openclaw/presence/"*-scan.json)' \
  '    : > "$SCAN_READ_MARKER"' \
  '    for _ in {1..1000}; do' \
  '      [[ -e "$SCAN_READ_RELEASE_FILE" ]] && break' \
  '      /bin/sleep 0.01' \
  '    done' \
  '    [[ -e "$SCAN_READ_RELEASE_FILE" ]] || exit 1' \
  '    ;;' \
  'esac' \
  'exec /bin/cat "$@"' \
  > "$SAFE_BIN/cat"
chmod +x "$SAFE_BIN/cat"

reset_evaluator
SCAN_READ_RELEASE_FILE="$TEST_HOME/evaluator-scan-read-release"
rm -f \
  "$MARKER_DIR/cabin" \
  "$MARKER_DIR/crosstown" \
  "$SCAN_READ_MARKER" \
  "$SCAN_READ_RELEASE_FILE"
write_all_presence "$fresh_1" true "$fresh_1" false

lock_ready_file="$TEST_HOME/evaluator-lock-ready"
LOCK_RELEASE_FILE="$TEST_HOME/evaluator-lock-release"
rm -f "$lock_ready_file" "$LOCK_RELEASE_FILE"
/usr/bin/lockf -k "$PRESENCE_DIR/evaluate.lock" /bin/bash -c '
  : > "$1"
  for _ in {1..500}; do
    [[ -e "$2" ]] && exit 0
    /bin/sleep 0.01
  done
  exit 1
' _ "$lock_ready_file" "$LOCK_RELEASE_FILE" &
LOCK_HOLDER_PID=$!

for _ in {1..500}; do
  [[ -e "$lock_ready_file" ]] && break
  kill -0 "$LOCK_HOLDER_PID" 2>/dev/null || break
  /bin/sleep 0.01
done
if [[ ! -e "$lock_ready_file" ]]; then
  echo "fixture lock holder did not acquire the evaluator lock" >&2
  exit 1
fi

evaluator_started_file="$TEST_HOME/evaluator-started"
rm -f "$evaluator_started_file"
(
  trap - EXIT
  : > "$evaluator_started_file"
  evaluate lock-blocked-newer-snapshots
) &
BLOCKED_EVALUATOR_PID=$!

for _ in {1..1000}; do
  [[ -e "$evaluator_started_file" ]] && break
  kill -0 "$BLOCKED_EVALUATOR_PID" 2>/dev/null || break
  /bin/sleep 0.01
done
if [[ ! -e "$evaluator_started_file" ]]; then
  echo "fixture evaluator did not start" >&2
  exit 1
fi

# Replace the scans while the evaluator is contending on the held lock.
write_all_presence "$fresh_3" false "$fresh_4" true
: > "$LOCK_RELEASE_FILE"

if ! wait "$LOCK_HOLDER_PID"; then
  echo "fixture lock holder failed to release cleanly" >&2
  exit 1
fi
LOCK_HOLDER_PID=""

for _ in {1..1000}; do
  [[ -e "$SCAN_READ_MARKER" ]] && break
  kill -0 "$BLOCKED_EVALUATOR_PID" 2>/dev/null || break
  /bin/sleep 0.01
done
if [[ ! -e "$SCAN_READ_MARKER" ]]; then
  echo "fixture evaluator did not reach its first scan read" >&2
  exit 1
fi

lock_probe_status=0
/usr/bin/lockf -s -t 0 -k \
  "$PRESENCE_DIR/evaluate.lock" /usr/bin/true || lock_probe_status=$?
if [[ "$lock_probe_status" -ne 75 ]]; then
  echo "fixture evaluator did not hold its lock at the scan-read boundary (status $lock_probe_status)" >&2
  exit 1
fi
test ! -e "$PRESENCE_DIR/state.json"

: > "$SCAN_READ_RELEASE_FILE"
if ! wait "$BLOCKED_EVALUATOR_PID"; then
  echo "fixture evaluator failed after its scan read was released" >&2
  exit 1
fi
BLOCKED_EVALUATOR_PID=""

test -e "$SCAN_READ_MARKER"
assert_all_people_at crosstown confirmed_vacant occupied

echo "test-presence-detect: PASS"
