#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/../.." && pwd)
EVALUATOR="$REPO_ROOT/openclaw/workspace/scripts/presence-detect.sh"
TEST_ROOT=$(mktemp -d)

cleanup() {
  rm -rf "$TEST_ROOT"
}
trap cleanup EXIT

new_home() {
  local name="$1"
  local home="$TEST_ROOT/$name"

  mkdir -p "$home/.openclaw/presence" "$home/.openclaw/logs" "$home/bin"
  : > "$home/accepted-events.jsonl"
  cat > "$home/bin/home-eventctl" <<'SCRIPT'
#!/usr/bin/env bash
set -euo pipefail

[[ "$#" -eq 3 && "$1" == "enqueue" && "$2" == "--source" && "$3" == "presence" ]]
payload=$(cat)
python3 -c 'import json,sys; value=json.load(sys.stdin); assert isinstance(value,dict)' <<< "$payload"
[[ ! -e "$HOME/fail-home-eventctl" ]] || exit 75
printf '%s\n' "$payload" >> "$HOME/accepted-events.jsonl"
SCRIPT
  chmod +x "$home/bin/home-eventctl"
  printf '%s\n' "$home"
}

write_scans() {
  local home="$1" cabin_present="$2" crosstown_present="$3"
  local cabin_timestamp="$4" crosstown_timestamp="$5"

  cat > "$home/.openclaw/presence/cabin-scan.json" <<JSON
{"timestamp":"$cabin_timestamp","location":"cabin","presence":{"Dylan":{"present":$cabin_present},"Julia":{"present":$cabin_present}}}
JSON
  cat > "$home/.openclaw/presence/crosstown-scan.json" <<JSON
{"timestamp":"$crosstown_timestamp","location":"crosstown","presence":{"Dylan":{"present":$crosstown_present},"Julia":{"present":$crosstown_present}}}
JSON
}

run_enabled() {
  local home="$1" now="$2" crash_after="${3:-}"

  HOME="$home" \
  HOME_EVENTS_PRESENCE_ENABLED=1 \
  HOME_EVENTCTL="$home/bin/home-eventctl" \
  PRESENCE_TEST_NOW="$now" \
  PRESENCE_TEST_CRASH_AFTER="$crash_after" \
    /bin/bash "$EVALUATOR" evaluate > "$home/evaluate.out"
}

run_disabled() {
  local home="$1" now="$2"

  HOME="$home" \
  HOME_EVENTS_PRESENCE_ENABLED=0 \
  PRESENCE_TEST_NOW="$now" \
    /bin/bash "$EVALUATOR" evaluate > "$home/evaluate.out"
}

assert_location() {
  local home="$1" expected="$2"

  python3 - "$home/.openclaw/presence/state.json" "$expected" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    state = json.load(stream)
expected = sys.argv[2]
for person in ("Dylan", "Julia"):
    actual = state["people"][person]["location"]
    if actual != expected:
        raise SystemExit(f"expected {person} at {expected}, got {actual}")
PY
}

assert_event_count() {
  local home="$1" expected="$2"

  python3 - "$home/accepted-events.jsonl" "$expected" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    events = [json.loads(line) for line in stream if line.strip()]
if len(events) != int(sys.argv[2]):
    raise SystemExit(f"expected {sys.argv[2]} accepted events, got {len(events)}")
PY
}

assert_no_work_files() {
  local home="$1"

  if compgen -G "$home/.openclaw/presence/home-events-outbox/*.pending.json" >/dev/null ||
      compgen -G "$home/.openclaw/presence/home-events-outbox/*.ready.json" >/dev/null; then
    echo "presence outbox retained unexpected work" >&2
    find "$home/.openclaw/presence/home-events-outbox" -maxdepth 1 -type f -print >&2
    return 1
  fi
}

assert_transition_payloads() {
  local home="$1" offset="$2" from_site="$3" to_site="$4"

  python3 - \
    "$home/accepted-events.jsonl" "$offset" "$from_site" "$to_site" \
    "$REPO_ROOT/openclaw/bin/home_event_bus.py" <<'PY'
import importlib.util
import json
import re
import sys

path, raw_offset, from_site, to_site, module_path = sys.argv[1:]
spec = importlib.util.spec_from_file_location("presence_test_home_event_bus", module_path)
assert spec and spec.loader
home_event_bus = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = home_event_bus
spec.loader.exec_module(home_event_bus)
with open(path, encoding="utf-8") as stream:
    events = [json.loads(line) for line in stream if line.strip()]
events = events[int(raw_offset):int(raw_offset) + 4]
if len(events) != 4:
    raise SystemExit(f"expected a four-event transition, got {len(events)}")

required = {
    "schema_version", "source_event_id", "event_type", "site",
    "entity_kind", "entity_alias", "occurred_at", "observed_at",
    "time_precision", "attributes",
}
for event in events:
    if set(event) != required:
        raise SystemExit(f"unexpected event fields: {sorted(event)}")
    if event["schema_version"] != 1 or event["time_precision"] != "evaluation":
        raise SystemExit(f"wrong presence envelope: {event}")
    if not re.fullmatch(r"presence_[0-9a-f]{64}", event["source_event_id"]):
        raise SystemExit(f"unsafe source event id: {event['source_event_id']!r}")
    home_event_bus.normalize_input(
        "presence", event, b"p" * 32, clock=lambda event=event: event["observed_at"]
    )
if len({event["source_event_id"] for event in events}) != 4:
    raise SystemExit("transition source event ids were not unique")

occupancy = [event for event in events if event["event_type"] == "presence.occupancy_changed"]
relocations = [event for event in events if event["event_type"] == "presence.person_relocated"]
if len(occupancy) != 2 or len(relocations) != 2:
    raise SystemExit(f"unexpected event taxonomy: {events!r}")
expected_occupancy = {
    from_site: ("occupied", "confirmed_vacant"),
    to_site: ("confirmed_vacant", "occupied"),
}
for event in occupancy:
    attributes = event["attributes"]
    if (attributes["previous"], attributes["current"]) != expected_occupancy[event["site"]]:
        raise SystemExit(f"wrong occupancy transition: {event!r}")
    if event["entity_kind"] != "site" or attributes["confidence"] != "canonical":
        raise SystemExit(f"wrong occupancy metadata: {event!r}")
    if not re.fullmatch(r"[0-9a-f]{64}", attributes["state_hash"]):
        raise SystemExit(f"wrong state hash: {event!r}")
for event in relocations:
    attributes = event["attributes"]
    if event["site"] != to_site or event["entity_kind"] != "person":
        raise SystemExit(f"wrong relocation site: {event!r}")
    if attributes["from_site"] != from_site or attributes["to_site"] != to_site:
        raise SystemExit(f"wrong relocation transition: {event!r}")
    if attributes["person_alias"] != event["entity_alias"]:
        raise SystemExit(f"wrong person alias: {event!r}")
    if attributes["confidence"] != "positive_detection":
        raise SystemExit(f"wrong relocation confidence: {event!r}")
PY
}

# A fresh producer establishes a silent baseline. The first real relocation is
# published once; a repeated evaluation of identical evidence is silent.
basic_home=$(new_home basic)
write_scans "$basic_home" true false \
  2026-07-12T14:59:00Z 2026-07-12T14:59:01Z
run_enabled "$basic_home" 2026-07-12T15:00:00Z
assert_location "$basic_home" cabin
assert_event_count "$basic_home" 0
assert_no_work_files "$basic_home"

# Multiple transitions retained during a bus outage replay in canonical
# producer sequence, never hash-filename order.
ordered_home=$(new_home ordered-replay)
write_scans "$ordered_home" true false \
  2026-07-12T14:59:00Z 2026-07-12T14:59:01Z
run_enabled "$ordered_home" 2026-07-12T15:00:00Z
: > "$ordered_home/fail-home-eventctl"
write_scans "$ordered_home" false true \
  2026-07-12T15:00:30Z 2026-07-12T15:00:31Z
run_enabled "$ordered_home" 2026-07-12T15:01:00Z
write_scans "$ordered_home" true false \
  2026-07-12T15:01:30Z 2026-07-12T15:01:31Z
run_enabled "$ordered_home" 2026-07-12T15:02:00Z
[[ $(find "$ordered_home/.openclaw/presence/home-events-outbox" \
  -name '*.ready.json' | wc -l | tr -d ' ') -eq 2 ]]
rm "$ordered_home/fail-home-eventctl"
run_enabled "$ordered_home" 2026-07-12T15:03:00Z
assert_event_count "$ordered_home" 8
assert_transition_payloads "$ordered_home" 0 cabin crosstown
assert_transition_payloads "$ordered_home" 4 crosstown cabin
assert_no_work_files "$ordered_home"

write_scans "$basic_home" false true \
  2026-07-12T15:00:30Z 2026-07-12T15:00:31Z
run_enabled "$basic_home" 2026-07-12T15:01:00Z
assert_location "$basic_home" crosstown
assert_event_count "$basic_home" 4
assert_transition_payloads "$basic_home" 0 cabin crosstown
run_enabled "$basic_home" 2026-07-12T15:02:00Z
assert_event_count "$basic_home" 4
assert_no_work_files "$basic_home"

# Bus failure does not fail or roll back canonical presence. The ready batch is
# private and is retried before the next evaluation.
: > "$basic_home/fail-home-eventctl"
write_scans "$basic_home" true false \
  2026-07-12T15:02:30Z 2026-07-12T15:02:31Z
run_enabled "$basic_home" 2026-07-12T15:03:00Z
assert_location "$basic_home" cabin
assert_event_count "$basic_home" 4
ready_file=$(compgen -G "$basic_home/.openclaw/presence/home-events-outbox/*.ready.json")
[[ -n "$ready_file" ]]
[[ "$(stat -f '%Lp' "$basic_home/.openclaw/presence/home-events-outbox")" == "700" ]]
[[ "$(stat -f '%Lp' "$ready_file")" == "600" ]]
rm "$basic_home/fail-home-eventctl"
run_enabled "$basic_home" 2026-07-12T15:04:00Z
assert_event_count "$basic_home" 8
assert_transition_payloads "$basic_home" 4 crosstown cabin
assert_no_work_files "$basic_home"

# Enabling the producer on an already-running canonical evaluator is also a
# silent baseline; it must not replay the last compatibility transition.
upgrade_home=$(new_home upgrade)
write_scans "$upgrade_home" true false \
  2026-07-12T14:59:00Z 2026-07-12T14:59:01Z
run_disabled "$upgrade_home" 2026-07-12T15:00:00Z
write_scans "$upgrade_home" false true \
  2026-07-12T15:00:30Z 2026-07-12T15:00:31Z
run_enabled "$upgrade_home" 2026-07-12T15:01:00Z
assert_event_count "$upgrade_home" 0
write_scans "$upgrade_home" true false \
  2026-07-12T15:01:30Z 2026-07-12T15:01:31Z
run_enabled "$upgrade_home" 2026-07-12T15:02:00Z
assert_event_count "$upgrade_home" 4
assert_transition_payloads "$upgrade_home" 0 crosstown cabin

exercise_crash_boundary() {
  local boundary="$1" canonical_after_crash="$2"
  local home
  home=$(new_home "crash-$boundary")

  write_scans "$home" true false \
    2026-07-12T14:59:00Z 2026-07-12T14:59:01Z
  run_enabled "$home" 2026-07-12T15:00:00Z
  write_scans "$home" false true \
    2026-07-12T15:00:30Z 2026-07-12T15:00:31Z
  if run_enabled "$home" 2026-07-12T15:01:00Z "$boundary"; then
    echo "$boundary crash seam unexpectedly succeeded" >&2
    return 1
  fi
  assert_location "$home" "$canonical_after_crash"
  assert_event_count "$home" 0

  run_enabled "$home" 2026-07-12T15:02:00Z
  assert_location "$home" crosstown
  assert_event_count "$home" 4
  assert_transition_payloads "$home" 0 cabin crosstown
  assert_no_work_files "$home"
  cmp -s \
    "$home/.openclaw/presence/state.json" \
    "$home/.openclaw/presence/prev-evaluated.json"
  python3 - "$home/.openclaw/presence/events.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    events = json.load(stream)
if len(events) != 4:
    raise SystemExit(f"crash recovery duplicated compatibility events: {events!r}")
PY
}

exercise_crash_boundary after_pending cabin
exercise_crash_boundary after_state_commit crosstown
exercise_crash_boundary after_projections crosstown
exercise_crash_boundary after_ready crosstown

# A pending batch whose canonical state matches neither declared hash is not
# guessed away. Recovery fails before another evaluation can advance state.
mismatch_home=$(new_home mismatch)
write_scans "$mismatch_home" true false \
  2026-07-12T14:59:00Z 2026-07-12T14:59:01Z
run_enabled "$mismatch_home" 2026-07-12T15:00:00Z
write_scans "$mismatch_home" false true \
  2026-07-12T15:00:30Z 2026-07-12T15:00:31Z
if run_enabled "$mismatch_home" 2026-07-12T15:01:00Z after_pending; then
  echo "mismatch fixture did not stop after staging" >&2
  exit 1
fi
python3 - "$mismatch_home/.openclaw/presence/state.json" <<'PY'
import json
import sys

with open(sys.argv[1], "w", encoding="utf-8") as stream:
    json.dump({"timestamp": "unrelated", "people": {}}, stream)
    stream.write("\n")
PY
if run_enabled "$mismatch_home" 2026-07-12T15:02:00Z; then
  echo "mismatched canonical state did not fail closed" >&2
  exit 1
fi
grep -q 'presence_outbox_recovery_failed' "$mismatch_home/evaluate.out"
assert_event_count "$mismatch_home" 0
compgen -G "$mismatch_home/.openclaw/presence/home-events-outbox/*.pending.json" >/dev/null

# The evaluation lock cannot redirect writes through a symlink.
lock_home=$(new_home unsafe-lock)
write_scans "$lock_home" true false \
  2026-07-12T14:59:00Z 2026-07-12T14:59:01Z
lock_canary="$lock_home/lock-canary"
printf '%s\n' unchanged > "$lock_canary"
ln -s "$lock_canary" "$lock_home/.openclaw/presence/evaluate.lock"
if run_enabled "$lock_home" 2026-07-12T15:00:00Z; then
  echo "symlinked evaluation lock did not fail closed" >&2
  exit 1
fi
grep -q 'evaluate_lock_unsafe' "$lock_home/evaluate.out"
[[ "$(cat "$lock_canary")" = "unchanged" ]]
[[ ! -e "$lock_home/.openclaw/presence/state.json" ]]

echo "test-presence-home-events: PASS"
