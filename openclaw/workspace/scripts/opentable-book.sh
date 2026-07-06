#!/usr/bin/env bash
# opentable-book.sh — Safely preview or commit an OpenTable reservation.
#
# Usage:
#   opentable-book.sh [--dry-run] [--max-time-delta MINUTES] \
#     <search> <date YYYY-MM-DD> [time HH:MM] [party_size]
#   opentable-book.sh --confirm <approval-id>
#
# Preview is the default. Only --confirm permits clicking Complete reservation.
# The script emits exactly one structured JSON object on stdout.

set -euo pipefail

CONFIRM_REQUESTED=0
CONFIRM_ID=""
EXPLICIT_DRY_RUN=0
MAX_TIME_DELTA="${OPENTABLE_MAX_TIME_DELTA_MINUTES:-30}"
MAX_TIME_DELTA_SET=0
APPROVAL_CACHE_DIR="${OPENTABLE_APPROVAL_CACHE_DIR:-$HOME/.cache/openclaw-gateway/opentable-approvals}"
APPROVAL_TTL_SECONDS="${OPENTABLE_APPROVAL_TTL_SECONDS:-300}"
POSITIONAL=()

emit_json() {
  python3 - "$@" <<'PY'
import json
import sys

values = sys.argv[1:]
if len(values) % 3:
    raise SystemExit("emit_json requires key/type/value triplets")
result = {}
for index in range(0, len(values), 3):
    key, kind, value = values[index:index + 3]
    if kind == "bool":
        result[key] = value == "true"
    elif kind == "int":
        result[key] = int(value)
    else:
        result[key] = value
print(json.dumps(result, separators=(",", ":")))
PY
}

emit_error() {
  emit_json \
    success bool false \
    mode str "$1" \
    error_code str "$2" \
    message str "$3"
}

usage_error() {
  emit_error \
    "preview" \
    "usage" \
    "Usage: opentable-book.sh [--dry-run] [--max-time-delta MINUTES] <search> <date YYYY-MM-DD> [time HH:MM] [party_size] | opentable-book.sh --confirm <approval-id>"
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --confirm)
      CONFIRM_REQUESTED=1
      if [[ $# -lt 2 || "$2" == --* ]]; then
        emit_error "commit" "missing_approval_id" "--confirm requires the approval ID from a successful preview"
        exit 2
      fi
      CONFIRM_ID="$2"
      shift 2
      ;;
    --confirm=*)
      CONFIRM_REQUESTED=1
      CONFIRM_ID="${1#*=}"
      shift
      ;;
    --dry-run|--preview)
      EXPLICIT_DRY_RUN=1
      shift
      ;;
    --max-time-delta)
      [[ $# -ge 2 ]] || usage_error
      MAX_TIME_DELTA="$2"
      MAX_TIME_DELTA_SET=1
      shift 2
      ;;
    --max-time-delta=*)
      MAX_TIME_DELTA="${1#*=}"
      MAX_TIME_DELTA_SET=1
      shift
      ;;
    --help|-h)
      usage_error
      ;;
    --)
      shift
      while [[ $# -gt 0 ]]; do
        POSITIONAL+=("$1")
        shift
      done
      ;;
    -*)
      usage_error
      ;;
    *)
      POSITIONAL+=("$1")
      shift
      ;;
  esac
done

if [[ "$CONFIRM_REQUESTED" -eq 1 && "$EXPLICIT_DRY_RUN" -eq 1 ]]; then
  emit_error "preview" "conflicting_mode" "Choose either --dry-run or --confirm, not both"
  exit 2
fi

MODE="preview"
[[ "$CONFIRM_REQUESTED" -eq 1 ]] && MODE="commit"

urlencode() {
  python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$1"
}

normalize_eval_json() {
  python3 -c '
import json
import sys

try:
    outer = json.load(sys.stdin)
    value = outer.get("result") if isinstance(outer, dict) else None
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        value = {}
except (json.JSONDecodeError, TypeError, ValueError):
    value = {}
print(json.dumps(value, separators=(",", ":")))
'
}

json_field() {
  local payload="$1"
  local field="$2"
  printf '%s' "$payload" | python3 -c '
import json
import sys

try:
    value = json.load(sys.stdin).get(sys.argv[1], "")
except (json.JSONDecodeError, AttributeError):
    value = ""
if isinstance(value, bool):
    print("1" if value else "0")
elif isinstance(value, int):
    print(value)
elif isinstance(value, str):
    print(value.replace("\r", " ").replace("\n", " ").replace("\x00", " ")[:2000])
' "$field"
}

display_time_delta() {
  local requested="$1"
  local displayed="$2"
  python3 - "$requested" "$displayed" <<'PY'
import datetime as dt
import re
import sys

try:
    requested = dt.datetime.strptime(sys.argv[1], "%H:%M")
    match = re.fullmatch(r"(\d{1,2}):(\d{2}) ([AP]M)", sys.argv[2])
    if not match or not 1 <= int(match.group(1)) <= 12:
        raise ValueError
    hour = int(match.group(1)) % 12
    if match.group(3) == "PM":
        hour += 12
    displayed = requested.replace(hour=hour, minute=int(match.group(2)))
except ValueError:
    raise SystemExit(1)
print(abs(int((displayed - requested).total_seconds() // 60)))
PY
}

emit_preview() {
  local status="$1"
  local approvable="$2"
  local approval_id="${3:-}"
  local expires_at="${4:-0}"
  emit_json \
    success bool true \
    mode str preview \
    status str "$status" \
    approvable bool "$approvable" \
    approval_id str "$approval_id" \
    approval_expires_at int "$expires_at" \
    restaurant str "$RESTAURANT" \
    location str "$LOCATION" \
    seating str "$SEATING" \
    venue_id str "$VENUE_ID" \
    form_identity str "$FORM_ID" \
    requested_date str "$DATE" \
    requested_time str "$TIME" \
    selected_date str "$PAGE_DATE" \
    selected_time str "$PAGE_TIME" \
    requested_party_size int "$PARTY" \
    selected_party_size int "$PAGE_PARTY" \
    time_delta_minutes int "$PAGE_DELTA" \
    max_time_delta_minutes int "$MAX_TIME_DELTA" \
    payment_required str "$PAYMENT_REQUIRED" \
    payment_terms str "$PAYMENT_TERMS" \
    cancellation_policy str "$CANCELLATION_POLICY" \
    no_show_policy str "$NO_SHOW_POLICY"
}

emit_commit() {
  emit_json \
    success bool true \
    mode str commit \
    status str confirmed \
    approval_id str "$CONFIRM_ID" \
    confirmation_id str "$CONFIRMATION_ID" \
    restaurant str "$RESTAURANT" \
    location str "$LOCATION" \
    seating str "$SEATING" \
    venue_id str "$VENUE_ID" \
    requested_date str "$DATE" \
    requested_time str "$TIME" \
    confirmed_date str "$CONFIRMED_DATE" \
    confirmed_time str "$CONFIRMED_TIME" \
    party_size int "$CONFIRMED_PARTY" \
    time_delta_minutes int "$CONFIRMED_DELTA" \
    max_time_delta_minutes int "$MAX_TIME_DELTA"
}

emit_unknown() {
  emit_json \
    success bool false \
    mode str commit \
    status str unknown \
    error_code str reservation_status_unknown \
    message str "$1" \
    non_retryable bool true \
    mutation_attempted bool true \
    reservation_may_exist bool true \
    approval_id str "$CONFIRM_ID"
}

emit_outside_window() {
  emit_json \
    success bool false \
    mode str "$MODE" \
    error_code str outside_time_window \
    message str "Closest available time exceeds the configured maximum delta" \
    requested_time str "$TIME" \
    closest_time str "$1" \
    time_delta_minutes int "$2" \
    max_time_delta_minutes int "$MAX_TIME_DELTA"
}

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
APPROVAL_STATE_HELPER="${OPENTABLE_APPROVAL_STATE_HELPER:-$SCRIPT_DIR/opentable-book-state.py}"

approval_state() {
  if [[ ! -f "$APPROVAL_STATE_HELPER" ]]; then
    emit_json \
      ok bool false \
      error_code str approval_store_unavailable \
      message str "OpenTable approval state helper is unavailable"
    return 3
  fi
  python3 "$APPROVAL_STATE_HELPER" "$1" "$APPROVAL_CACHE_DIR" "${@:2}"
}
if [[ "$CONFIRM_REQUESTED" -eq 1 ]]; then
  if [[ -z "$CONFIRM_ID" ]]; then
    emit_error commit missing_approval_id "--confirm requires the approval ID from a successful preview"
    exit 2
  fi
  if [[ ! "$CONFIRM_ID" =~ ^[A-Za-z0-9_-]{24,128}$ ]]; then
    emit_error commit invalid_approval_id "Approval ID has an invalid format"
    exit 2
  fi
  if [[ "$MAX_TIME_DELTA_SET" -eq 1 || ${#POSITIONAL[@]} -ne 0 ]]; then
    emit_error commit approval_scope_override "Commit accepts only --confirm and its approval ID"
    exit 2
  fi
  if ! CLAIM_RESULT=$(approval_state claim "$CONFIRM_ID"); then
    CLAIM_CODE=$(json_field "$CLAIM_RESULT" error_code)
    CLAIM_MESSAGE=$(json_field "$CLAIM_RESULT" message)
    [[ -n "$CLAIM_CODE" ]] || CLAIM_CODE=approval_state_invalid
    [[ -n "$CLAIM_MESSAGE" ]] || CLAIM_MESSAGE="Approval state could not be validated"
    emit_error commit "$CLAIM_CODE" "$CLAIM_MESSAGE"
    exit 3
  fi
  SEARCH=$(json_field "$CLAIM_RESULT" search)
  DATE=$(json_field "$CLAIM_RESULT" requested_date)
  TIME=$(json_field "$CLAIM_RESULT" requested_time)
  PARTY=$(json_field "$CLAIM_RESULT" requested_party_size)
  MAX_TIME_DELTA=$(json_field "$CLAIM_RESULT" max_time_delta_minutes)
  APPROVED_RESTAURANT=$(json_field "$CLAIM_RESULT" restaurant)
  APPROVED_LOCATION=$(json_field "$CLAIM_RESULT" location)
  APPROVED_SEATING=$(json_field "$CLAIM_RESULT" seating)
  APPROVED_VENUE_ID=$(json_field "$CLAIM_RESULT" venue_id)
  APPROVED_FORM_ID=$(json_field "$CLAIM_RESULT" form_identity)
  APPROVED_DATE=$(json_field "$CLAIM_RESULT" selected_date)
  APPROVED_TIME=$(json_field "$CLAIM_RESULT" selected_time)
  APPROVED_PARTY=$(json_field "$CLAIM_RESULT" selected_party_size)
  APPROVED_PAYMENT_REQUIRED=$(json_field "$CLAIM_RESULT" payment_required)
  APPROVED_PAYMENT_TERMS=$(json_field "$CLAIM_RESULT" payment_terms)
  APPROVED_CANCELLATION_POLICY=$(json_field "$CLAIM_RESULT" cancellation_policy)
  APPROVED_NO_SHOW_POLICY=$(json_field "$CLAIM_RESULT" no_show_policy)
else
  if [[ ${#POSITIONAL[@]} -lt 2 || ${#POSITIONAL[@]} -gt 4 ]]; then
    usage_error
  fi
  SEARCH="${POSITIONAL[0]}"
  DATE="${POSITIONAL[1]}"
  TIME="${POSITIONAL[2]:-19:00}"
  PARTY="${POSITIONAL[3]:-2}"
fi

if [[ -z "$SEARCH" || ${#SEARCH} -gt 200 || "$SEARCH" == *$'\n'* || "$SEARCH" == *$'\r'* ]]; then
  emit_error "$MODE" invalid_search "Search must contain 1-200 characters on one line"
  exit 2
fi

if ! python3 -c '
import datetime as dt
import sys

try:
    value = dt.date.fromisoformat(sys.argv[1])
except ValueError:
    raise SystemExit(1)
raise SystemExit(0 if value >= dt.date.today() else 1)
' "$DATE"; then
  emit_error "$MODE" invalid_date "Date must be a real YYYY-MM-DD date that is not in the past"
  exit 2
fi

if [[ ! "$TIME" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]]; then
  emit_error "$MODE" invalid_time "Time must use 24-hour HH:MM format"
  exit 2
fi
if [[ ! "$PARTY" =~ ^([1-9]|1[0-9]|20)$ ]]; then
  emit_error "$MODE" invalid_party_size "Party size must be an integer from 1 through 20"
  exit 2
fi
if [[ ! "$MAX_TIME_DELTA" =~ ^(0|[1-9][0-9]{0,2})$ ]] || (( 10#$MAX_TIME_DELTA > 720 )); then
  emit_error "$MODE" invalid_max_time_delta "Maximum time delta must be an integer from 0 through 720 minutes"
  exit 2
fi
if [[ "$CONFIRM_REQUESTED" -eq 0 ]] && { [[ ! "$APPROVAL_TTL_SECONDS" =~ ^[1-9][0-9]{1,3}$ ]] || (( 10#$APPROVAL_TTL_SECONDS < 30 || 10#$APPROVAL_TTL_SECONDS > 1800 )); }; then
  emit_error preview invalid_approval_ttl "Approval TTL must be an integer from 30 through 1800 seconds"
  exit 2
fi

DATETIME="${DATE}T${TIME}:00"
ENCODED_SEARCH=$(urlencode "$SEARCH")
ENCODED_DATETIME=$(urlencode "$DATETIME")
PINCHTAB_INSTANCE_HELPER="$HOME/.openclaw/bin/pinchtab-headless-instance"
PINCHTAB_INSTANCE_ID=""
PINCHTAB_INSTANCE_STARTED=0
TAB_ID=""

cleanup() {
  if [[ -n "$PINCHTAB_INSTANCE_ID" && -n "$TAB_ID" && -x "$PINCHTAB_INSTANCE_HELPER" ]]; then
    "$PINCHTAB_INSTANCE_HELPER" close "$PINCHTAB_INSTANCE_ID" "$TAB_ID" >/dev/null 2>&1 || true
  fi
  if [[ -n "$PINCHTAB_INSTANCE_ID" && -x "$PINCHTAB_INSTANCE_HELPER" ]]; then
    "$PINCHTAB_INSTANCE_HELPER" release "$PINCHTAB_INSTANCE_ID" "$PINCHTAB_INSTANCE_STARTED" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

if [[ ! -x "$PINCHTAB_INSTANCE_HELPER" ]]; then
  emit_error "$MODE" "browser_helper_unavailable" "Managed PinchTab helper is unavailable"
  exit 1
fi

if ! IFS=$'\t' read -r PINCHTAB_INSTANCE_ID PINCHTAB_INSTANCE_STARTED \
  < <("$PINCHTAB_INSTANCE_HELPER" acquire opentable 2>/dev/null); then
  emit_error "$MODE" "browser_acquire_failed" "Could not acquire the managed OpenTable browser"
  exit 1
fi
if [[ -z "$PINCHTAB_INSTANCE_ID" || ! "$PINCHTAB_INSTANCE_STARTED" =~ ^[01]$ ]]; then
  emit_error "$MODE" "browser_acquire_failed" "Managed OpenTable browser returned an invalid lease"
  exit 1
fi

pt() {
  "$PINCHTAB_INSTANCE_HELPER" eval "$PINCHTAB_INSTANCE_ID" "$TAB_ID" "$1" 2>/dev/null
}

SEARCH_URL="https://www.opentable.com/s?covers=${PARTY}&dateTime=${ENCODED_DATETIME}&metroId=7&term=${ENCODED_SEARCH}"
if ! TAB_ID=$("$PINCHTAB_INSTANCE_HELPER" open "$PINCHTAB_INSTANCE_ID" "$SEARCH_URL" 2>/dev/null); then
  emit_error "$MODE" "browser_open_failed" "Could not open the OpenTable search"
  exit 1
fi
if [[ -z "$TAB_ID" ]]; then
  emit_error "$MODE" "browser_open_failed" "Managed OpenTable browser returned no tab"
  exit 1
fi
sleep 8

if [[ "${OPENTABLE_BROWSER_SMOKE_TEST:-0}" == "1" ]]; then
  if ! SMOKE_RAW=$(pt "(function(){
    /* OT_STEP_SMOKE */
    return JSON.stringify({origin:window.location.origin,path:window.location.pathname});
  })()"); then
    emit_error smoke_test browser_smoke_failed "Could not verify the OpenTable browser context"
    exit 1
  fi
  SMOKE=$(printf '%s' "$SMOKE_RAW" | normalize_eval_json)
  SMOKE_ORIGIN=$(json_field "$SMOKE" origin)
  SMOKE_PATH=$(json_field "$SMOKE" path)
  if [[ "$SMOKE_ORIGIN" != "https://www.opentable.com" || "$SMOKE_PATH" != "/s" ]]; then
    emit_error smoke_test invalid_browser_context "OpenTable smoke test left the approved HTTPS origin or route"
    exit 1
  fi
  emit_json \
    success bool true \
    mode str smoke_test \
    status str browser_ready \
    origin str "$SMOKE_ORIGIN" \
    path str "$SMOKE_PATH"
  exit 0
fi

# Cookie dismissal is best effort and intentionally returns no page content.
pt "(function(){
  /* OT_STEP_COOKIE */
  document.cookie='OptanonAlertBoxClosed='+new Date().toISOString()+'; path=/; domain=.opentable.com; max-age=31536000';
  document.cookie='OptanonConsent=isGpcEnabled=0&interactionCount=1&groups=C0001:1,C0002:1,C0003:1,C0004:1; path=/; domain=.opentable.com; max-age=31536000';
  const consent=document.querySelector('#onetrust-consent-sdk');
  if(consent) consent.remove();
  return null;
})()" >/dev/null || true
sleep 2

# Select the closest visible slot only when it is within the approved window.
# This advances to booking details but never clicks the final reservation button.
if ! SLOT_RAW=$(pt "(function(){
  /* OT_STEP_SLOT */
  const clean=value => String(value || '').replace(/\\s+/g, ' ').trim().slice(0, 40);
  const context={origin:window.location.origin,path:window.location.pathname};
  const result=value => JSON.stringify({...context,...value});
  if(context.origin !== 'https://www.opentable.com' || context.path !== '/s') return result({status:'invalid_context'});
  const elements=Array.from(document.querySelectorAll('main a[role=button]'));
  const timeElements=elements.filter(element => /\\d{1,2}:\\d{2} [AP]M/.test(element.textContent.trim()) && element.getBoundingClientRect().width > 0);
  if(!timeElements.length) {
    const cardHosts=Array.from(document.querySelectorAll('[data-test="restaurant-card"]'));
    const opaqueCards=cardHosts.filter(card => {
      const rect=card.getBoundingClientRect();
      const visible=rect.width > 0 && rect.height > 0;
      const inspectableText=clean(card.innerText || card.textContent || card.getAttribute('aria-label'));
      const inspectableControl=card.querySelector('a, button, [role="button"], [href], [aria-label]');
      return visible && !inspectableText && !inspectableControl;
    });
    if(opaqueCards.length) return result({status:'renderer_unavailable'});
    return result({status:'no_slots'});
  }
  const target='${TIME}'.split(':');
  const targetMinutes=parseInt(target[0], 10) * 60 + parseInt(target[1], 10);
  const maxDiff=${MAX_TIME_DELTA};
  let best=null;
  let bestDiff=9999;
  let bestTime='';
  timeElements.forEach(element => {
    const match=element.textContent.trim().match(/(\\d{1,2}):(\\d{2}) ([AP]M)/);
    if(!match) return;
    let hour=parseInt(match[1], 10);
    if(match[3] === 'PM' && hour !== 12) hour += 12;
    if(match[3] === 'AM' && hour === 12) hour = 0;
    const minutes=hour * 60 + parseInt(match[2], 10);
    const diff=Math.abs(minutes - targetMinutes);
    if(diff < bestDiff) {
      bestDiff=diff;
      best=element;
      bestTime=clean(match[0]);
    }
  });
  if(!best) return result({status:'no_slots'});
  if(bestDiff > maxDiff) return result({status:'outside_window',closestTime:bestTime,diffMinutes:bestDiff});
  const rect=best.getBoundingClientRect();
  const options={bubbles:true,cancelable:true,clientX:rect.x+rect.width/2,clientY:rect.y+rect.height/2,button:0};
  best.dispatchEvent(new MouseEvent('mousedown', options));
  best.dispatchEvent(new MouseEvent('mouseup', options));
  best.dispatchEvent(new MouseEvent('click', options));
  return result({status:'selected',selectedTime:bestTime,diffMinutes:bestDiff});
})()"); then
  emit_error "$MODE" "availability_check_failed" "Could not safely inspect OpenTable availability"
  exit 1
fi
SLOT=$(printf '%s' "$SLOT_RAW" | normalize_eval_json)
SLOT_STATUS=$(json_field "$SLOT" "status")
SLOT_ORIGIN=$(json_field "$SLOT" "origin")
SLOT_PATH=$(json_field "$SLOT" "path")

if [[ "$SLOT_ORIGIN" != "https://www.opentable.com" || "$SLOT_PATH" != "/s" ]]; then
  emit_error "$MODE" invalid_browser_context "OpenTable search left the approved HTTPS origin or route"
  exit 1
fi

case "$SLOT_STATUS" in
  invalid_context)
    emit_error "$MODE" invalid_browser_context "OpenTable search left the approved HTTPS origin or route"
    exit 1
    ;;
  renderer_unavailable)
    emit_error "$MODE" "availability_renderer_unavailable" "OpenTable result cards could not be safely inspected"
    exit 1
    ;;
  no_slots)
    emit_error "$MODE" "no_available_times" "No available times were found"
    exit 1
    ;;
  outside_window)
    CLOSEST_TIME=$(json_field "$SLOT" "closestTime")
    SLOT_DELTA=$(json_field "$SLOT" "diffMinutes")
    if [[ ! "$CLOSEST_TIME" =~ ^[0-9]{1,2}:[0-9]{2}\ [AP]M$ || ! "$SLOT_DELTA" =~ ^[0-9]+$ ]]; then
      emit_error "$MODE" "availability_parse_failed" "OpenTable returned an invalid availability result"
      exit 1
    fi
    emit_outside_window "$CLOSEST_TIME" "$SLOT_DELTA"
    exit 1
    ;;
  selected)
    ;;
  *)
    emit_error "$MODE" "availability_parse_failed" "OpenTable returned an invalid availability result"
    exit 1
    ;;
esac

sleep 10

# Read a strict allowlist from one scoped booking form. Page text remains
# untrusted: missing identity, payment, or policy facts never become approval.
if ! INFO_RAW=$(pt "(function(){
  /* OT_STEP_DETAILS */
  const clean=(value, limit=1000) => String(value || '').replace(/\\s+/g, ' ').trim().slice(0, limit);
  const origin=window.location.origin;
  const path=window.location.pathname;
  const routeValid=origin === 'https://www.opentable.com' && (path === '/booking/details' || path.startsWith('/booking/details/'));
  const buttons=Array.from(document.querySelectorAll('form button, [data-test*=\"booking\"] button, [data-testid*=\"booking\"] button'))
    .filter(button => clean(button.textContent, 80).toLowerCase() === 'complete reservation');
  const button=buttons.length === 1 ? buttons[0] : null;
  const root=button?.closest('form, [data-test*=\"booking\"], [data-testid*=\"booking\"]') || null;
  const rootText=root?.innerText || '';
  const lines=rootText.split('\\n').map(line => clean(line, 1000)).filter(Boolean);
  const lineMatching=pattern => lines.find(line => pattern.test(line)) || '';
  const field=(selectors, limit=1000) => clean(root?.querySelector(selectors)?.textContent, limit);
  const restaurant=clean(document.querySelector('h1, [data-test*=\"restaurant-name\"], [data-testid*=\"restaurant-name\"]')?.textContent, 160);
  const location=field('[data-test*=\"address\"], [data-testid*=\"address\"], [itemprop=\"address\"]', 300);
  const seating=field('[data-test*=\"seating\"], [data-testid*=\"seating\"], [aria-label*=\"seating\" i]', 160);
  const dateNode=root?.querySelector('time[datetime], [data-test*=\"date\"], [data-testid*=\"date\"]');
  const dateSource=clean(dateNode?.getAttribute('datetime') || dateNode?.textContent, 100);
  const dateMatch=dateSource.match(/\\d{4}-\\d{2}-\\d{2}/);
  const timeNode=root?.querySelector('[data-test*=\"time\"], [data-testid*=\"time\"], time[datetime]');
  const timeSource=clean(timeNode?.getAttribute('datetime') || timeNode?.textContent, 100);
  const timeMatch=timeSource.match(/(\\d{1,2}:\\d{2} [AP]M)/) || timeSource.match(/T(\\d{2}):(\\d{2})/);
  let selectedTime='';
  if(timeMatch && timeMatch.length === 2) selectedTime=clean(timeMatch[1], 20);
  if(timeMatch && timeMatch.length === 3) {
    const hour24=parseInt(timeMatch[1], 10);
    const hour12=hour24 % 12 || 12;
    selectedTime=hour12+':'+timeMatch[2]+' '+(hour24 >= 12 ? 'PM' : 'AM');
  }
  const partyText=field('[data-test*=\"party\"], [data-testid*=\"party\"], [aria-label*=\"guest\" i], [aria-label*=\"party\" i]', 160) || lineMatching(/\\b(guest|people|party|cover)/i);
  const partyMatch=partyText.match(/\\b([1-9]|1\\d|20)\\b/);
  const paymentText=field('[data-test*=\"payment\"], [data-testid*=\"payment\"], [class*=\"payment\"]', 1000) || lineMatching(/\\b(payment|deposit|prepay|credit card|charge)\\b/i);
  let paymentRequired='unknown';
  if(/\\b(no payment|no prepayment|pay at (the )?restaurant|nothing due)\\b/i.test(paymentText)) paymentRequired='no';
  else if(document.querySelectorAll('iframe[src*=\"spreedly\"]').length > 0 || /\\b(deposit|prepay|charged?|credit card required|due (now|today))\\b/i.test(paymentText)) paymentRequired='yes';
  const cancellationPolicy=field('[data-test*=\"cancellation\"], [data-testid*=\"cancellation\"], [class*=\"cancellation\"]', 1000) || lineMatching(/cancel(lation)?/i);
  const noShowPolicy=field('[data-test*=\"no-show\"], [data-testid*=\"no-show\"], [class*=\"no-show\"]', 1000) || lineMatching(/no[- ]show/i);
  const params=new URL(window.location.href).searchParams;
  const venueId=clean(root?.getAttribute('data-restaurant-id') || root?.getAttribute('data-venue-id') || params.get('rid') || params.get('restaurantId') || params.get('venueId'), 160);
  const formIdentity=clean(root?.getAttribute('data-testid') || root?.getAttribute('data-test') || root?.id || root?.getAttribute('action'), 300);
  return JSON.stringify({
    origin,
    path,
    routeValid,
    restaurant,
    location,
    seating,
    selectedDate:clean(dateMatch?.[0], 20),
    selectedTime,
    partySize:partyMatch ? parseInt(partyMatch[1], 10) : 0,
    paymentRequired,
    paymentTerms:clean(paymentText, 1000),
    cancellationPolicy:clean(cancellationPolicy, 1000),
    noShowPolicy:clean(noShowPolicy, 1000),
    venueId,
    formIdentity,
    finalButtonCount:buttons.length,
    completeDisabled:!!button?.disabled
  });
})()"); then
  emit_error "$MODE" "booking_details_failed" "Could not safely inspect the booking details"
  exit 1
fi
INFO=$(printf '%s' "$INFO_RAW" | normalize_eval_json)
INFO_ORIGIN=$(json_field "$INFO" origin)
INFO_PATH=$(json_field "$INFO" path)
ROUTE_VALID=$(json_field "$INFO" routeValid)
RESTAURANT=$(json_field "$INFO" "restaurant")
LOCATION=$(json_field "$INFO" location)
SEATING=$(json_field "$INFO" seating)
PAGE_DATE=$(json_field "$INFO" selectedDate)
PAGE_TIME=$(json_field "$INFO" "selectedTime")
PAGE_PARTY=$(json_field "$INFO" partySize)
PAYMENT_REQUIRED=$(json_field "$INFO" paymentRequired)
PAYMENT_TERMS=$(json_field "$INFO" paymentTerms)
CANCELLATION_POLICY=$(json_field "$INFO" cancellationPolicy)
NO_SHOW_POLICY=$(json_field "$INFO" noShowPolicy)
VENUE_ID=$(json_field "$INFO" venueId)
FORM_ID=$(json_field "$INFO" formIdentity)
FINAL_BUTTON_COUNT=$(json_field "$INFO" finalButtonCount)
COMPLETE_DISABLED=$(json_field "$INFO" "completeDisabled")

if [[ "$INFO_ORIGIN" != "https://www.opentable.com" || "$ROUTE_VALID" != "1" || ! "$INFO_PATH" =~ ^/booking/details(/|$) ]]; then
  emit_error "$MODE" invalid_browser_context "OpenTable booking details left the approved HTTPS origin or route"
  exit 1
fi
if [[ "$FINAL_BUTTON_COUNT" != "1" || "$COMPLETE_DISABLED" != "0" ]]; then
  emit_error "$MODE" "booking_not_ready" "OpenTable booking details are not ready for confirmation"
  exit 1
fi
if ! PAGE_DELTA=$(display_time_delta "$TIME" "$PAGE_TIME"); then
  emit_error "$MODE" "booking_time_invalid" "OpenTable booking details did not contain a valid selected time"
  exit 1
fi
if [[ ! "$PAGE_DELTA" =~ ^[0-9]+$ ]] || (( PAGE_DELTA > MAX_TIME_DELTA )); then
  emit_outside_window "$PAGE_TIME" "$PAGE_DELTA"
  exit 1
fi

[[ -n "$LOCATION" ]] || LOCATION=unknown
[[ -n "$SEATING" ]] || SEATING=unknown
[[ -n "$PAGE_DATE" ]] || PAGE_DATE=unknown
[[ "$PAGE_PARTY" =~ ^([1-9]|1[0-9]|20)$ ]] || PAGE_PARTY=0
[[ "$PAYMENT_REQUIRED" == "yes" || "$PAYMENT_REQUIRED" == "no" ]] || PAYMENT_REQUIRED=unknown
[[ -n "$PAYMENT_TERMS" ]] || PAYMENT_TERMS=unknown
[[ -n "$CANCELLATION_POLICY" ]] || CANCELLATION_POLICY=unknown
[[ -n "$NO_SHOW_POLICY" ]] || NO_SHOW_POLICY=unknown
[[ -n "$VENUE_ID" ]] || VENUE_ID=unknown
[[ -n "$FORM_ID" ]] || FORM_ID=unknown

CORE_COMPLETE=1
if [[ -z "$RESTAURANT" || "$LOCATION" == unknown || "$SEATING" == unknown || "$PAGE_DATE" == unknown || "$PAGE_PARTY" -eq 0 || "$VENUE_ID" == unknown || "$FORM_ID" == unknown ]]; then
  CORE_COMPLETE=0
fi
TERMS_COMPLETE=1
if [[ "$PAYMENT_REQUIRED" == unknown || "$PAYMENT_TERMS" == unknown || "$CANCELLATION_POLICY" == unknown || "$NO_SHOW_POLICY" == unknown ]]; then
  TERMS_COMPLETE=0
fi
REQUEST_MATCH=1
if [[ "$PAGE_DATE" != "$DATE" || "$PAGE_PARTY" != "$PARTY" ]]; then
  REQUEST_MATCH=0
fi

if [[ "$CONFIRM_REQUESTED" -eq 0 ]]; then
  if [[ "$CORE_COMPLETE" -ne 1 ]]; then
    emit_preview details_incomplete false
    exit 0
  fi
  if [[ "$TERMS_COMPLETE" -ne 1 ]]; then
    emit_preview terms_unknown false
    exit 0
  fi
  if [[ "$REQUEST_MATCH" -ne 1 ]]; then
    emit_preview request_mismatch false
    exit 0
  fi

  REQUEST_JSON=$(emit_json \
    search str "$SEARCH" \
    requested_date str "$DATE" \
    requested_time str "$TIME" \
    requested_party_size int "$PARTY" \
    max_time_delta_minutes int "$MAX_TIME_DELTA")
  FACTS_JSON=$(emit_json \
    restaurant str "$RESTAURANT" \
    location str "$LOCATION" \
    seating str "$SEATING" \
    venue_id str "$VENUE_ID" \
    form_identity str "$FORM_ID" \
    selected_date str "$PAGE_DATE" \
    selected_time str "$PAGE_TIME" \
    selected_party_size int "$PAGE_PARTY" \
    payment_required str "$PAYMENT_REQUIRED" \
    payment_terms str "$PAYMENT_TERMS" \
    cancellation_policy str "$CANCELLATION_POLICY" \
    no_show_policy str "$NO_SHOW_POLICY")
  if ! APPROVAL_RESULT=$(approval_state create "$APPROVAL_TTL_SECONDS" "$REQUEST_JSON" "$FACTS_JSON"); then
    APPROVAL_CODE=$(json_field "$APPROVAL_RESULT" error_code)
    APPROVAL_MESSAGE=$(json_field "$APPROVAL_RESULT" message)
    [[ -n "$APPROVAL_CODE" ]] || APPROVAL_CODE=approval_store_unavailable
    [[ -n "$APPROVAL_MESSAGE" ]] || APPROVAL_MESSAGE="Could not persist protected approval state"
    emit_error preview "$APPROVAL_CODE" "$APPROVAL_MESSAGE"
    exit 1
  fi
  APPROVAL_ID=$(json_field "$APPROVAL_RESULT" approval_id)
  APPROVAL_EXPIRES_AT=$(json_field "$APPROVAL_RESULT" expires_at)
  emit_preview ready_to_confirm true "$APPROVAL_ID" "$APPROVAL_EXPIRES_AT"
  exit 0
fi

if [[ "$CORE_COMPLETE" -ne 1 || "$TERMS_COMPLETE" -ne 1 || "$REQUEST_MATCH" -ne 1 || \
      "$RESTAURANT" != "$APPROVED_RESTAURANT" || "$LOCATION" != "$APPROVED_LOCATION" || \
      "$SEATING" != "$APPROVED_SEATING" || "$VENUE_ID" != "$APPROVED_VENUE_ID" || \
      "$FORM_ID" != "$APPROVED_FORM_ID" || "$PAGE_DATE" != "$APPROVED_DATE" || \
      "$PAGE_TIME" != "$APPROVED_TIME" || "$PAGE_PARTY" != "$APPROVED_PARTY" || \
      "$PAYMENT_REQUIRED" != "$APPROVED_PAYMENT_REQUIRED" || "$PAYMENT_TERMS" != "$APPROVED_PAYMENT_TERMS" || \
      "$CANCELLATION_POLICY" != "$APPROVED_CANCELLATION_POLICY" || "$NO_SHOW_POLICY" != "$APPROVED_NO_SHOW_POLICY" ]]; then
  emit_error commit approval_facts_changed "Booking details changed after preview; run a new preview and approve the new facts"
  exit 3
fi

FORM_ID_JS=$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$FORM_ID")
VENUE_ID_JS=$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$VENUE_ID")

# Persist the irreversible boundary before invoking page code. Any uncertainty
# after this point is non-retryable and may represent a completed reservation.
if ! approval_state mutation "$CONFIRM_ID" >/dev/null; then
  emit_error commit approval_state_invalid "Could not persist the mutation boundary; reservation was not submitted"
  exit 3
fi

if ! CLICK_RAW=$(pt "(function(){
  /* OT_STEP_COMPLETE */
  const clean=(value, limit=300) => String(value || '').replace(/\\s+/g, ' ').trim().slice(0, limit);
  const origin=window.location.origin;
  const path=window.location.pathname;
  if(origin !== 'https://www.opentable.com' || !(path === '/booking/details' || path.startsWith('/booking/details/'))) return JSON.stringify({status:'invalid_context'});
  const buttons=Array.from(document.querySelectorAll('form button, [data-test*=\"booking\"] button, [data-testid*=\"booking\"] button'))
    .filter(button => clean(button.textContent, 80).toLowerCase() === 'complete reservation');
  if(buttons.length !== 1) return JSON.stringify({status:'button_count'});
  const button=buttons[0];
  const root=button.closest('form, [data-test*=\"booking\"], [data-testid*=\"booking\"]');
  const params=new URL(window.location.href).searchParams;
  const formIdentity=clean(root?.getAttribute('data-testid') || root?.getAttribute('data-test') || root?.id || root?.getAttribute('action'), 300);
  const venueId=clean(root?.getAttribute('data-restaurant-id') || root?.getAttribute('data-venue-id') || params.get('rid') || params.get('restaurantId') || params.get('venueId'), 160);
  if(formIdentity !== ${FORM_ID_JS} || venueId !== ${VENUE_ID_JS}) return JSON.stringify({status:'identity_mismatch'});
  if(button.disabled) return JSON.stringify({status:'disabled'});
  button.click();
  return JSON.stringify({status:'clicked'});
})()"); then
  emit_unknown "Reservation submission could not be observed; do not retry without reconciling OpenTable reservations"
  exit 4
fi
CLICK=$(printf '%s' "$CLICK_RAW" | normalize_eval_json)
if [[ "$(json_field "$CLICK" "status")" != "clicked" ]]; then
  emit_unknown "Reservation submission outcome is uncertain; do not retry without reconciling OpenTable reservations"
  exit 4
fi

sleep 10

if ! CONFIRM_RAW=$(pt "(function(){
  /* OT_STEP_CONFIRMATION */
  const clean=(value, limit=300) => String(value || '').replace(/\\s+/g, ' ').trim().slice(0, limit);
  const origin=window.location.origin;
  const path=window.location.pathname;
  const root=document.querySelector('[data-test*=\"confirmation\"], [data-testid*=\"confirmation\"], main');
  const text=root?.innerText || '';
  const restaurant=clean(root?.querySelector('h1, [data-test*=\"restaurant-name\"], [data-testid*=\"restaurant-name\"]')?.textContent, 160);
  const dateNode=root?.querySelector('time[datetime], [data-test*=\"date\"], [data-testid*=\"date\"]');
  const dateSource=clean(dateNode?.getAttribute('datetime') || dateNode?.textContent, 100);
  const dateMatch=dateSource.match(/\\d{4}-\\d{2}-\\d{2}/);
  const timeNode=root?.querySelector('[data-test*=\"time\"], [data-testid*=\"time\"], time[datetime]');
  const timeSource=clean(timeNode?.getAttribute('datetime') || timeNode?.textContent, 100);
  const timeMatch=timeSource.match(/(\\d{1,2}:\\d{2} [AP]M)/);
  const partyText=clean(root?.querySelector('[data-test*=\"party\"], [data-testid*=\"party\"], [aria-label*=\"guest\" i]')?.textContent, 160);
  const partyMatch=partyText.match(/\\b([1-9]|1\\d|20)\\b/);
  const params=new URL(window.location.href).searchParams;
  const venueId=clean(root?.getAttribute('data-restaurant-id') || root?.getAttribute('data-venue-id') || params.get('rid') || params.get('restaurantId') || params.get('venueId'), 160);
  const confirmationId=clean(root?.getAttribute('data-confirmation-id') || params.get('reservationId') || params.get('confirmationNumber') || text.match(/confirmation(?: number| #|:)\\s*([A-Za-z0-9-]+)/i)?.[1], 160);
  return JSON.stringify({
    origin,
    path,
    confirmed:/reservation confirmed/i.test(text),
    confirmationId,
    restaurant,
    confirmedDate:clean(dateMatch?.[0], 20),
    confirmedTime:clean(timeMatch?.[1], 20),
    partySize:partyMatch ? parseInt(partyMatch[1], 10) : 0,
    venueId
  });
})()"); then
  emit_unknown "Reservation confirmation could not be checked; do not retry without reconciling OpenTable reservations"
  exit 4
fi
CONFIRM_RESULT=$(printf '%s' "$CONFIRM_RAW" | normalize_eval_json)
CONFIRM_ORIGIN=$(json_field "$CONFIRM_RESULT" origin)
CONFIRM_PATH=$(json_field "$CONFIRM_RESULT" path)
CONFIRMED=$(json_field "$CONFIRM_RESULT" confirmed)
CONFIRMATION_ID=$(json_field "$CONFIRM_RESULT" confirmationId)
CONFIRMED_RESTAURANT=$(json_field "$CONFIRM_RESULT" restaurant)
CONFIRMED_DATE=$(json_field "$CONFIRM_RESULT" confirmedDate)
CONFIRMED_TIME=$(json_field "$CONFIRM_RESULT" "confirmedTime")
CONFIRMED_PARTY=$(json_field "$CONFIRM_RESULT" partySize)
CONFIRMED_VENUE_ID=$(json_field "$CONFIRM_RESULT" venueId)

if ! CONFIRMED_DELTA=$(display_time_delta "$TIME" "$CONFIRMED_TIME"); then
  emit_unknown "Reservation exists but its confirmed time could not be verified; do not retry"
  exit 4
fi
if [[ "$CONFIRM_ORIGIN" != "https://www.opentable.com" || ! "$CONFIRM_PATH" =~ ^/booking/confirmation(/|$) || \
      "$CONFIRMED" != "1" || -z "$CONFIRMATION_ID" || \
      "$CONFIRMED_RESTAURANT" != "$RESTAURANT" || "$CONFIRMED_DATE" != "$PAGE_DATE" || \
      "$CONFIRMED_TIME" != "$PAGE_TIME" || "$CONFIRMED_PARTY" != "$PAGE_PARTY" || \
      "$CONFIRMED_VENUE_ID" != "$VENUE_ID" || "$CONFIRMED_DELTA" -gt "$MAX_TIME_DELTA" ]]; then
  emit_unknown "A reservation may exist, but its confirmation facts did not exactly match the approved preview; do not retry"
  exit 4
fi

approval_state confirmed "$CONFIRM_ID" >/dev/null 2>&1 || true
emit_commit
