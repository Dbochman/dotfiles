#!/bin/bash
# vacancy-actions.sh — Automated actions when a house becomes vacant
# Triggered by WatchPaths on ~/.openclaw/presence/state.json
#
# When confirmed_vacant:
#   - Turn off all lights
#   - Set thermostat to eco
#   - Turn off Cielo minisplits (Crosstown)
#   - Align each person's current Eight Sleep Pod to their detected location
#   - Start Roombas
#
# When occupied again:
#   - Clear markers (reset for next vacancy)
#   - (Welcome home actions handled by crosstown-routines/cabin-routines skills)

set -euo pipefail

PRESENCE_DIR="$HOME/.openclaw/presence"
STATE_FILE="$PRESENCE_DIR/state.json"
MARKER_DIR="$PRESENCE_DIR/vacancy-dispatched"
LOG_FILE="$HOME/.openclaw/logs/vacancy-actions.log"
ROOMBA_SNOOZE_FILE="${ROOMBA_SNOOZE_FILE:-$HOME/.openclaw/dog-walk/snooze.json}"
PRESENCE_SCANNER="${PRESENCE_SCANNER:-$HOME/.openclaw/workspace/scripts/presence-detect.sh}"
VACANCY_ACTION_JOURNAL="${VACANCY_ACTION_JOURNAL:-$HOME/.openclaw/bin/vacancy-action-journal.py}"
VACANCY_JOURNAL_PYTHON="${VACANCY_JOURNAL_PYTHON:-/opt/homebrew/bin/python3}"
CROSSTOWN_VACANT_ROOMBA="${CROSSTOWN_VACANT_ROOMBA:-$HOME/.openclaw/bin/crosstown-vacant-roomba.py}"
HOME_EVENT_ACTION="${HOME_EVENT_ACTION:-$HOME/.openclaw/bin/home-event-action}"
HOME_EVENT_ACTION_POLICY="${HOME_EVENT_ACTION_POLICY:-$HOME/.openclaw/home-events/config/action-policy.json}"
JOURNAL_RUN_ID=""
JOURNAL_ATTEMPT_ID=""
JOURNAL_WARNING_EMITTED=0
JOURNAL_TELEMETRY_FAILED=0

# All CLIs resolved via PATH (~/.openclaw/bin + /opt/homebrew/bin)

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG_FILE"
}

# Observation-only telemetry around the existing action runner. Every helper
# failure is intentionally fail-open: it may add one sanitized warning but can
# never change a device command, its ordering, or the vacancy marker.
journal_warn() {
  JOURNAL_TELEMETRY_FAILED=1
  if [[ "$JOURNAL_WARNING_EMITTED" -eq 0 ]]; then
    log "  WARN: Vacancy action telemetry unavailable; legacy actions continue"
    JOURNAL_WARNING_EMITTED=1
  fi
}

journal_recover() {
  [[ "$JOURNAL_TELEMETRY_FAILED" -eq 0 ]] || return 0
  if [[ ! -x "$VACANCY_ACTION_JOURNAL" ]]; then
    journal_warn
    return 0
  fi
  if ! "$VACANCY_ACTION_JOURNAL" recover >/dev/null 2>&1; then
    journal_warn
  fi
  return 0
}

journal_begin_run() {
  local site="$1" output
  JOURNAL_RUN_ID=""
  [[ "$JOURNAL_TELEMETRY_FAILED" -eq 0 ]] || return 0
  if [[ ! -x "$VACANCY_ACTION_JOURNAL" ]] || \
      ! output=$("$VACANCY_ACTION_JOURNAL" begin-run --site "$site" 2>/dev/null); then
    journal_warn
    return 0
  fi
  JOURNAL_RUN_ID=$(printf '%s' "$output" | "$VACANCY_JOURNAL_PYTHON" -c \
    'import json,sys; value=json.load(sys.stdin); print(value.get("run_id", ""))' \
    2>/dev/null || true)
  if [[ ! "$JOURNAL_RUN_ID" =~ ^run_[0-9a-f]{32}$ ]]; then
    JOURNAL_RUN_ID=""
    journal_warn
  fi
  return 0
}

journal_begin_action() {
  local target="$1" action="$2" output
  JOURNAL_ATTEMPT_ID=""
  [[ "$JOURNAL_TELEMETRY_FAILED" -eq 0 ]] || return 0
  [[ -n "$JOURNAL_RUN_ID" ]] || return 0
  if ! output=$("$VACANCY_ACTION_JOURNAL" begin-action \
      --run-id "$JOURNAL_RUN_ID" --target "$target" --action "$action" 2>/dev/null); then
    journal_warn
    return 0
  fi
  JOURNAL_ATTEMPT_ID=$(printf '%s' "$output" | "$VACANCY_JOURNAL_PYTHON" -c \
    'import json,sys; value=json.load(sys.stdin); print(value.get("attempt_id", ""))' \
    2>/dev/null || true)
  if [[ ! "$JOURNAL_ATTEMPT_ID" =~ ^attempt_[0-9a-f]{32}$ ]]; then
    JOURNAL_ATTEMPT_ID=""
    journal_warn
  fi
  return 0
}

journal_finish_action() {
  local outcome="$1" verification="$2" reason_code="$3"
  [[ "$JOURNAL_TELEMETRY_FAILED" -eq 0 ]] || return 0
  [[ -n "$JOURNAL_RUN_ID" && -n "$JOURNAL_ATTEMPT_ID" ]] || return 0
  if ! "$VACANCY_ACTION_JOURNAL" finish-action \
      --run-id "$JOURNAL_RUN_ID" \
      --attempt-id "$JOURNAL_ATTEMPT_ID" \
      --outcome "$outcome" \
      --verification "$verification" \
      --reason-code "$reason_code" >/dev/null 2>&1; then
    journal_warn
  fi
  JOURNAL_ATTEMPT_ID=""
  return 0
}

journal_complete_run() {
  [[ "$JOURNAL_TELEMETRY_FAILED" -eq 0 ]] || return 0
  [[ -n "$JOURNAL_RUN_ID" ]] || return 0
  if ! "$VACANCY_ACTION_JOURNAL" complete-run \
      --run-id "$JOURNAL_RUN_ID" >/dev/null 2>&1; then
    journal_warn
  fi
  JOURNAL_RUN_ID=""
  JOURNAL_ATTEMPT_ID=""
  return 0
}

# Return the exact action owner. Once the protected policy exists, an invalid
# policy or unavailable helper fails closed for that target rather than
# risking overlap between the legacy runner and the bus worker.
action_owner() {
  local site="$1" target="$2" output owner
  if [[ ! -e "$HOME_EVENT_ACTION_POLICY" && ! -L "$HOME_EVENT_ACTION_POLICY" ]]; then
    printf '%s\n' legacy
    return 0
  fi
  if [[ ! -f "$HOME_EVENT_ACTION_POLICY" || -L "$HOME_EVENT_ACTION_POLICY" || ! -x "$HOME_EVENT_ACTION" ]]; then
    printf '%s\n' unsafe
    return 0
  fi
  if ! output=$("$HOME_EVENT_ACTION" ownership --site "$site" --target "$target" 2>/dev/null); then
    printf '%s\n' unsafe
    return 0
  fi
  owner=$(printf '%s' "$output" | "$VACANCY_JOURNAL_PYTHON" -c \
    'import json,sys; value=json.load(sys.stdin); print(value.get("owner", "unsafe") if value.get("ok") is True else "unsafe")' \
    2>/dev/null || echo unsafe)
  case "$owner" in
    legacy|bus) printf '%s\n' "$owner" ;;
    *) printf '%s\n' unsafe ;;
  esac
}

# Load cached credentials used by the device helper CLIs — no live op reads.
SECRETS_FILE="$HOME/.openclaw/.secrets-cache"
if [[ -f "$SECRETS_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$SECRETS_FILE"
  set +a
fi

IMSG_BIN="${IMSG_BIN:-/opt/homebrew/bin/imsg}"
DYLAN_CHAT_ID="${DYLAN_CHAT_ID:-171}"

_send_imessage() {
  local msg="$1"
  if [[ ! -x "$IMSG_BIN" ]]; then
    log "  WARN: imsg not executable at $IMSG_BIN, skipping iMessage"
    return 0
  fi

  if "$IMSG_BIN" send \
    --chat-id "$DYLAN_CHAT_ID" \
    --service imessage \
    --text "$msg" \
    --json >> "$LOG_FILE" 2>&1; then
    log "  iMessage notification: SENT via native imsg"
  else
    log "  WARN: native imsg notification failed"
  fi
}

# The Roomba Dashboard owns one per-location snooze policy shared by dog-walk
# and vacancy automation. Missing policy means starts are allowed; malformed
# policy fails closed so a broken safety control cannot start a vacuum.
roomba_start_blocked() {
  local location="$1" snooze_state
  ROOMBA_BLOCK_REASON=""

  [[ -f "$ROOMBA_SNOOZE_FILE" ]] || return 1

  snooze_state=$(python3 - "$ROOMBA_SNOOZE_FILE" "$location" <<'PY' 2>/dev/null || echo error
import json
import sys
from datetime import datetime, timezone

with open(sys.argv[1], encoding="utf-8") as snooze_file:
    snooze = json.load(snooze_file)
if not isinstance(snooze, dict):
    raise ValueError("snooze policy must be an object")

location = sys.argv[2]
if location not in snooze or snooze[location] is None:
    print("clear")
else:
    expires = snooze[location]
    if not isinstance(expires, str) or not expires:
        raise ValueError("snooze expiry must be a nonempty timestamp or null")
    expiry = datetime.fromisoformat(expires.replace("Z", "+00:00"))
    if expiry.tzinfo is None:
        raise ValueError("snooze expiry must include a timezone")
    print("snoozed" if expiry > datetime.now(timezone.utc) else "clear")
PY
  )

  case "$snooze_state" in
    clear) return 1 ;;
    snoozed)
      ROOMBA_BLOCK_REASON="snoozed"
      log "  ${location} Roomba automation: SKIPPED (snoozed)"
      return 0
      ;;
    *)
      ROOMBA_BLOCK_REASON="policy_invalid_fail_closed"
      log "  WARN: Invalid Roomba snooze policy; skipping ${location} start"
      return 0
      ;;
  esac
}

# Do not let an absent, invalid, or legacy Cabin binding semantically relocate
# Julia's Eight Sleep side. Schema-v2 activation releases this containment
# automatically only while the deployed scanner can validate the protected
# exact production binding without writing presence state.
cabin_presence_enrollment_active() {
  [[ -x "$PRESENCE_SCANNER" ]] &&
    "$PRESENCE_SCANNER" validate-config cabin >/dev/null 2>&1
}

mkdir -p "$MARKER_DIR"

if [[ ! -f "$STATE_FILE" ]]; then
  log "ERROR: state.json not found"
  exit 1
fi

journal_recover

# Parse the correlated occupancy and sticky per-person location in one read.
state_values=$(python3 - "$STATE_FILE" <<'PY' 2>/dev/null || printf 'unknown\tunknown\tunknown\tunknown\n'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as state_file:
    state = json.load(state_file)
print(
    state.get("crosstown", {}).get("occupancy", "unknown"),
    state.get("cabin", {}).get("occupancy", "unknown"),
    state.get("people", {}).get("Dylan", {}).get("location", "unknown"),
    state.get("people", {}).get("Julia", {}).get("location", "unknown"),
    sep="\t",
)
PY
)
IFS=$'\t' read -r crosstown_occupancy cabin_occupancy dylan_location julia_location <<< "$state_values"

log "Check: crosstown=$crosstown_occupancy cabin=$cabin_occupancy dylan=$dylan_location julia=$julia_location"

# --- Crosstown vacancy ---
if [[ "$crosstown_occupancy" == "confirmed_vacant" ]] && [[ ! -f "$MARKER_DIR/crosstown" ]]; then
  log "Crosstown confirmed vacant — running vacancy actions"
  journal_begin_run crosstown

  # Lights off
  journal_begin_action all_lights turn_off
  case "$(action_owner crosstown all_lights)" in
    bus)
      journal_finish_action skipped policy_decision delegated_to_event_bus
      log "  Crosstown lights: DELEGATED to home-event action worker"
      ;;
    legacy)
      if hue --crosstown all-off >> "$LOG_FILE" 2>&1; then
        journal_finish_action command_accepted command_exit completed
        log "  Crosstown lights: OFF"
      else
        journal_finish_action failed command_exit command_failed
        log "  ERROR: Failed to turn off Crosstown lights"
      fi
      ;;
    *)
      journal_finish_action skipped policy_decision policy_invalid_fail_closed
      log "  WARN: Crosstown lights skipped; action ownership is unsafe"
      ;;
  esac

  # Thermostat eco
  journal_begin_action central_hvac enable_eco
  if nest eco crosstown on >> "$LOG_FILE" 2>&1; then
    journal_finish_action command_accepted command_exit completed
    log "  Crosstown thermostat: ECO"
  else
    journal_finish_action failed command_exit command_failed
    log "  ERROR: Failed to set Crosstown eco mode"
  fi

  # Cielo minisplits off
  for unit in bedroom office "living room"; do
    case "$unit" in
      bedroom) journal_target="cielo_bedroom" ;;
      office) journal_target="cielo_office" ;;
      "living room") journal_target="cielo_living_room" ;;
    esac
    journal_begin_action "$journal_target" turn_off
    if cielo off -d "$unit" >> "$LOG_FILE" 2>&1; then
      journal_finish_action command_accepted command_exit completed
      log "  Cielo $unit: OFF"
    else
      journal_finish_action failed command_exit command_failed
      log "  ERROR: Failed to turn off Cielo $unit"
    fi
  done

  # Lock front door
  journal_begin_action front_door_lock lock
  lock_output=$(august status 2>&1) || true
  lock_state=$(echo "$lock_output" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('state',{}).get('locked','unknown'))" 2>/dev/null || echo "unknown")
  if [[ "$lock_state" == "True" ]]; then
    journal_finish_action state_confirmed state_confirmed already_satisfied
    log "  Front door: ALREADY LOCKED"
    _send_imessage "🔒 Crosstown vacant — front door was already locked"
  else
    if lock_result=$(august lock 2>&1); then
      locked=$(echo "$lock_result" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('state',{}).get('locked',False))" 2>/dev/null || echo "False")
      if [[ "$locked" == "True" ]]; then
        journal_finish_action state_confirmed state_confirmed completed
        log "  Front door: LOCKED"
        _send_imessage "🔒 Crosstown vacant — front door locked automatically"
      else
        journal_finish_action outcome_unknown state_confirmed verification_failed
        log "  ERROR: Lock command succeeded but door not confirmed locked"
        _send_imessage "⚠️ Crosstown vacant — lock command sent but could not confirm door is locked"
      fi
    else
      journal_finish_action failed command_exit command_failed
      log "  ERROR: Failed to lock front door"
      _send_imessage "🚨 Crosstown vacant — FAILED to lock front door! Please check manually"
    fi
  fi

  # The shared daily controller owns cat-activity suppression, the dashboard
  # snooze, current-phase checks, per-robot verification, and one run decision
  # per local day. This vacancy transition counts as today's scheduled run.
  journal_begin_action crosstown_roombas start_cleaning
  roomba_output=""
  if roomba_output=$("$CROSSTOWN_VACANT_ROOMBA" --source vacancy_transition 2>> "$LOG_FILE"); then
    roomba_status=0
  else
    roomba_status=$?
  fi
  [[ -z "$roomba_output" ]] || printf '%s\n' "$roomba_output" >> "$LOG_FILE"
  roomba_outcome=$(printf '%s' "$roomba_output" | "$VACANCY_JOURNAL_PYTHON" -c \
    'import json,sys; value=json.load(sys.stdin); print(value.get("outcome", "invalid") if value.get("ok") is True else "invalid")' \
    2>/dev/null || echo invalid)
  case "$roomba_outcome" in
    started)
      journal_finish_action state_confirmed state_confirmed completed
      log "  Crosstown Roombas: STARTED and VERIFIED"
      ;;
    already_cleaning)
      journal_finish_action state_confirmed state_confirmed already_satisfied
      log "  Crosstown Roombas: ALREADY CLEANING"
      ;;
    snoozed)
      journal_finish_action skipped policy_decision snoozed
      log "  Crosstown Roombas: SKIPPED (snoozed)"
      ;;
    recent_cat_activity)
      journal_finish_action skipped policy_decision recent_cat_activity
      log "  Crosstown Roombas: SKIPPED (recent litter-box activity)"
      ;;
    already_handled)
      journal_finish_action skipped policy_decision daily_already_handled
      log "  Crosstown Roombas: SKIPPED (daily decision already recorded)"
      ;;
    robot_not_ready)
      journal_finish_action skipped policy_decision robot_not_ready
      log "  Crosstown Roombas: SKIPPED (robot not safely ready)"
      ;;
    *)
      journal_finish_action failed command_exit command_failed
      log "  ERROR: Crosstown daily Roomba controller failed (status $roomba_status)"
      ;;
  esac

  date > "$MARKER_DIR/crosstown"
  journal_complete_run
  log "Crosstown vacancy actions complete"

elif [[ "$crosstown_occupancy" == "occupied" ]] && \
    [[ "$dylan_location" == "crosstown" || "$julia_location" == "crosstown" ]] && \
    [[ -f "$MARKER_DIR/crosstown" ]]; then
  log "Crosstown occupied again — clearing vacancy marker"
  rm -f "$MARKER_DIR/crosstown"
fi

# --- Cabin vacancy ---
if [[ "$cabin_occupancy" == "confirmed_vacant" ]] && [[ ! -f "$MARKER_DIR/cabin" ]]; then
  log "Cabin confirmed vacant — running vacancy actions"
  journal_begin_run cabin

  # Lights off
  journal_begin_action all_lights turn_off
  case "$(action_owner cabin all_lights)" in
    bus)
      journal_finish_action skipped policy_decision delegated_to_event_bus
      log "  Cabin lights: DELEGATED to home-event action worker"
      ;;
    legacy)
      if hue --cabin all-off >> "$LOG_FILE" 2>&1; then
        journal_finish_action command_accepted command_exit completed
        log "  Cabin lights: OFF"
      else
        journal_finish_action failed command_exit command_failed
        log "  ERROR: Failed to turn off Cabin lights"
      fi
      ;;
    *)
      journal_finish_action skipped policy_decision policy_invalid_fail_closed
      log "  WARN: Cabin lights skipped; action ownership is unsafe"
      ;;
  esac

  # Thermostat eco
  journal_begin_action central_hvac enable_eco
  if nest eco cabin on >> "$LOG_FILE" 2>&1; then
    journal_finish_action command_accepted command_exit completed
    log "  Cabin thermostat: ECO"
  else
    journal_finish_action failed command_exit command_failed
    log "  ERROR: Failed to set Cabin eco mode"
  fi

  # Start Roombas unless the shared automation snooze is active.
  if roomba_start_blocked cabin; then
    for journal_target in floomba philly; do
      journal_begin_action "$journal_target" start_cleaning
      journal_finish_action skipped policy_decision "$ROOMBA_BLOCK_REASON"
    done
  else
    started=0
    journal_begin_action floomba start_cleaning
    if roomba start floomba >> "$LOG_FILE" 2>&1; then
      journal_finish_action command_accepted command_exit completed
      started=$((started + 1))
    else
      journal_finish_action failed command_exit command_failed
      log "  ERROR: Failed to start Floomba"
    fi
    journal_begin_action philly start_cleaning
    if roomba start philly >> "$LOG_FILE" 2>&1; then
      journal_finish_action command_accepted command_exit completed
      started=$((started + 1))
    else
      journal_finish_action failed command_exit command_failed
      log "  ERROR: Failed to start Philly"
    fi
    log "  Cabin Roombas: STARTED ($started/2)"
  fi

  date > "$MARKER_DIR/cabin"
  journal_complete_run
  log "Cabin vacancy actions complete"

elif [[ "$cabin_occupancy" == "occupied" ]] && \
    [[ "$dylan_location" == "cabin" || "$julia_location" == "cabin" ]] && \
    [[ -f "$MARKER_DIR/cabin" ]]; then
  log "Cabin occupied again — clearing vacancy marker"
  rm -f "$MARKER_DIR/cabin"
fi

# Eight Sleep's user-scoped API models exactly one current Pod per person; the
# other Pod becomes away. Reconcile each sticky presence location independently
# when sticky location changes so split households work while manual app
# overrides remain untouched until the next positive relocation.
reconcile_eightsleep_home() {
  local person="$1" side="$2" location="$3"
  local marker="$MARKER_DIR/8sleep-$side-home"
  local stage_file="$marker.$$"
  local previous="unknown"

  case "$location" in
    crosstown|cabin) ;;
    *)
      log "  WARN: Skipping Eight Sleep $side reconciliation; $person location is $location"
      return 0
      ;;
  esac

  [[ -f "$marker" ]] && previous=$(cat "$marker" 2>/dev/null || echo "unknown")
  [[ "$previous" == "$location" ]] && return 0

  if 8sleep --location "$location" home "$side" >> "$LOG_FILE" 2>&1; then
    printf '%s\n' "$location" > "$stage_file"
    mv -f "$stage_file" "$marker"
    rm -f \
      "$MARKER_DIR/crosstown-8sleep-$side" \
      "$MARKER_DIR/cabin-8sleep-$side"
    if [[ "$previous" == "$location" ]]; then
      log "  Eight Sleep $side: $location home state verified"
    else
      log "  Eight Sleep $side: home moved ${previous}->${location}; other Pod away"
    fi
  else
    rm -f "$stage_file"
    log "  ERROR: Failed to reconcile Eight Sleep $side home to $location"
  fi
}

reconcile_eightsleep_home "Dylan" dylan "$dylan_location"

julia_eightsleep_location="$julia_location"
if [[ "$julia_location" == "cabin" ]] && ! cabin_presence_enrollment_active; then
  julia_eightsleep_location="crosstown"
  log "  WARN: Pinning Eight Sleep julia to crosstown until strict Cabin presence enrollment validates"
fi
reconcile_eightsleep_home "Julia" julia "$julia_eightsleep_location"
