#!/usr/bin/env bash
# findmy-locate — Verify an exact Find My person through accessibility, then capture.

set -euo pipefail
export PATH="/opt/homebrew/bin:$PATH"
umask 077

PEEKABOO_BIN="${FINDMY_PEEKABOO_BIN:-peekaboo}"
OPEN_BIN="${FINDMY_OPEN_BIN:-open}"
NOHUP_BIN="${FINDMY_NOHUP_BIN:-nohup}"
SLEEP_BIN="${FINDMY_SLEEP_BIN:-sleep}"
CAPTURE_DIR="${FINDMY_CAPTURE_DIR:-$HOME/.openclaw/findmy-locate}"
CAPTURE_TTL_SECONDS="${FINDMY_CAPTURE_TTL_SECONDS:-300}"
MIN_CAPTURE_BYTES="${FINDMY_MIN_CAPTURE_BYTES:-50000}"

emit_error() {
  python3 - "$1" "$2" "${3:-}" <<'PY'
import json
import sys

payload = {"success": False, "error": sys.argv[1], "message": sys.argv[2]}
if sys.argv[3]:
    payload["person"] = sys.argv[3]
print(json.dumps(payload, separators=(",", ":")))
PY
}

emit_capture() {
  python3 - "$1" "$2" "$3" "$4" <<'PY'
import datetime as dt
import json
import sys

print(json.dumps({
    "success": True,
    "person": sys.argv[1],
    "capture": sys.argv[2],
    "size": int(sys.argv[3]),
    "delete_after_seconds": int(sys.argv[4]),
    "timestamp": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}, separators=(",", ":")))
PY
}

if [[ ! "$CAPTURE_TTL_SECONDS" =~ ^[0-9]+$ ]] || \
   (( CAPTURE_TTL_SECONDS < 30 || CAPTURE_TTL_SECONDS > 3600 )); then
  emit_error invalid_ttl "FINDMY_CAPTURE_TTL_SECONDS must be between 30 and 3600"
  exit 2
fi
if [[ ! "$MIN_CAPTURE_BYTES" =~ ^[0-9]+$ ]] || (( MIN_CAPTURE_BYTES < 1 )); then
  emit_error invalid_capture_threshold "FINDMY_MIN_CAPTURE_BYTES must be a positive integer"
  exit 2
fi

mkdir -p "$CAPTURE_DIR"
chmod 700 "$CAPTURE_DIR"

cleanup_expired() {
  python3 - "$CAPTURE_DIR" "$CAPTURE_TTL_SECONDS" <<'PY'
import os
from pathlib import Path
import stat
import sys
import time

directory = Path(sys.argv[1])
ttl = int(sys.argv[2])
now = time.time()
for path in directory.glob("findmy-*.png"):
    try:
        info = path.lstat()
        if stat.S_ISREG(info.st_mode) and now - info.st_mtime >= ttl:
            path.unlink()
    except (FileNotFoundError, OSError):
        pass
PY
}

cleanup_all() {
  python3 - "$CAPTURE_DIR" <<'PY'
import json
from pathlib import Path
import stat
import sys

deleted = 0
for path in Path(sys.argv[1]).glob("findmy-*.png"):
    try:
        if stat.S_ISREG(path.lstat().st_mode):
            path.unlink()
            deleted += 1
    except (FileNotFoundError, OSError):
        pass
print(json.dumps({"success": True, "deleted": deleted}, separators=(",", ":")))
PY
}

schedule_cleanup() {
  local capture_path="$1"
  local cleanup_runner="$NOHUP_BIN"
  if [[ "$cleanup_runner" != */* ]]; then
    cleanup_runner=$(command -v "$cleanup_runner") || return 1
  fi
  [[ -x "$cleanup_runner" ]] || return 1
  "$cleanup_runner" /bin/sh -c 'sleep "$1"; rm -f -- "$2"' findmy-cleanup \
    "$CAPTURE_TTL_SECONDS" "$capture_path" >/dev/null 2>&1 &
}

# Parse a Peekaboo accessibility snapshot without persisting it. The parser
# accepts schema variations but requires one selected People tab and one exact
# target name with a stable element ID. Verification additionally requires the
# target row itself to be selected.
snapshot_person_id() {
  local mode="$1"
  shift
  python3 -c '
import json
import sys

mode = sys.argv[1]
expected = {" ".join(value.split()).casefold() for value in sys.argv[2:]}

try:
    payload = json.load(sys.stdin)
except (json.JSONDecodeError, UnicodeDecodeError):
    raise SystemExit(10)

def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)

def text_values(node):
    fields = {"label", "name", "title", "text", "description", "axlabel", "axtitle"}
    for key, value in node.items():
        if key.casefold() in fields and isinstance(value, str):
            normalized = " ".join(value.split())
            if normalized:
                yield normalized

def element_id(node):
    fields = ("id", "elementId", "element_id", "uid", "identifier")
    for field in fields:
        value = node.get(field)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return ""

def selected(node):
    truthy = {True, 1, "1", "true", "yes", "selected", "on"}
    for key, value in node.items():
        folded = key.casefold().replace("_", "")
        if "selected" in folded and isinstance(value, (bool, int, str)) and value in truthy:
            return True
        if folded in {"state", "traits"}:
            if isinstance(value, str) and "selected" in value.casefold():
                return True
            if isinstance(value, list) and any(
                isinstance(item, str) and "selected" in item.casefold()
                for item in value
            ):
                return True
        if isinstance(value, dict) and selected(value):
            return True
    return False

def role(node):
    for field in ("role", "subrole", "type"):
        value = node.get(field)
        if isinstance(value, str):
            return value.casefold()
    return ""

nodes = list(walk(payload))
people_tabs = []
for node in nodes:
    labels = {value.casefold() for value in text_values(node)}
    node_role = role(node)
    if "people" in labels and (
        not node_role or any(kind in node_role for kind in ("tab", "radio", "button"))
    ):
        people_tabs.append(node)
if len(people_tabs) != 1 or not selected(people_tabs[0]):
    raise SystemExit(11)

matches = {}
for node in nodes:
    labels = {value.casefold() for value in text_values(node)}
    if labels.isdisjoint(expected):
        continue
    identifier = element_id(node)
    if identifier:
        matches[identifier] = node
if len(matches) != 1:
    raise SystemExit(12)

identifier, node = next(iter(matches.items()))
if mode == "verify" and not selected(node):
    raise SystemExit(13)
print(identifier)
' "$mode" "$@"
}

open_findmy() {
  if ! "$OPEN_BIN" -a "FindMy" >/dev/null 2>&1 && ! "$OPEN_BIN" -a "Find My" >/dev/null 2>&1; then
    emit_error app_unavailable "Find My could not be opened"
    return 1
  fi
  "$SLEEP_BIN" 2
}

capture_person() {
  local person="$1"
  local tag="$2"
  shift 2
  local labels=("$@")
  local snapshot element_id verify_snapshot verified_id

  if ! snapshot=$("$PEEKABOO_BIN" see --app "FindMy" --json 2>/dev/null); then
    emit_error accessibility_unavailable "Find My accessibility snapshot failed" "$person"
    return 1
  fi
  if ! element_id=$(printf '%s' "$snapshot" | snapshot_person_id locate "${labels[@]}" 2>/dev/null); then
    emit_error person_not_verified "People tab or exact person name could not be verified" "$person"
    return 1
  fi
  if ! "$PEEKABOO_BIN" click --on "$element_id" --app "FindMy" >/dev/null 2>&1; then
    emit_error selection_failed "Exact Find My person row could not be selected" "$person"
    return 1
  fi
  "$SLEEP_BIN" 3

  if ! verify_snapshot=$("$PEEKABOO_BIN" see --app "FindMy" --json 2>/dev/null); then
    emit_error selection_unverified "Selected Find My person could not be re-verified" "$person"
    return 1
  fi
  if ! verified_id=$(printf '%s' "$verify_snapshot" | snapshot_person_id verify "${labels[@]}" 2>/dev/null) || \
     [[ "$verified_id" != "$element_id" ]]; then
    emit_error selection_mismatch "Find My selected person did not match the requested name" "$person"
    return 1
  fi

  local timestamp capture_path size
  timestamp=$(date +%s)
  capture_path="$CAPTURE_DIR/findmy-${tag}-${timestamp}-$$.png"
  if ! "$PEEKABOO_BIN" image --app "FindMy" --path "$capture_path" >/dev/null 2>&1; then
    rm -f -- "$capture_path"
    emit_error capture_failed "Find My capture failed" "$person"
    return 1
  fi
  chmod 600 "$capture_path" 2>/dev/null || {
    rm -f -- "$capture_path"
    emit_error capture_permissions "Find My capture could not be protected" "$person"
    return 1
  }
  size=$(wc -c < "$capture_path" | tr -d '[:space:]')
  if [[ ! "$size" =~ ^[0-9]+$ ]] || (( size < MIN_CAPTURE_BYTES )); then
    rm -f -- "$capture_path"
    emit_error capture_incomplete "Find My capture was incomplete" "$person"
    return 1
  fi

  if ! schedule_cleanup "$capture_path"; then
    rm -f -- "$capture_path"
    emit_error cleanup_schedule_failed "Find My capture cleanup could not be scheduled" "$person"
    return 1
  fi
  emit_capture "$person" "$capture_path" "$size" "$CAPTURE_TTL_SECONDS"
}

cleanup_expired

NAME="${1:-}"
if [[ "$NAME" == "cleanup" ]]; then
  [[ $# -eq 1 ]] || {
    emit_error invalid_arguments "Usage: findmy-locate cleanup"
    exit 2
  }
  cleanup_all
  exit 0
fi
if [[ -z "$NAME" || $# -ne 1 ]]; then
  emit_error invalid_arguments "Usage: findmy-locate <dylan|julia|me|both|cleanup>"
  exit 2
fi

NAME_LOWER=$(printf '%s' "$NAME" | tr '[:upper:]' '[:lower:]')
case "$NAME_LOWER" in
  dylan|"dylan bochman"|db|julia|"julia jennings"|jj|me|clawdbot|clawdbotbochman|both|all)
    ;;
  *)
    emit_error unknown_person "Use dylan, julia, me, both, or cleanup"
    exit 2
    ;;
esac
if ! open_findmy; then
  exit 1
fi

if [[ "$NAME_LOWER" == "both" || "$NAME_LOWER" == "all" ]]; then
  DYLAN_STATUS=0
  JULIA_STATUS=0
  if ! RESULT_DYLAN=$(capture_person "Dylan Bochman" dylan "Dylan Bochman"); then
    DYLAN_STATUS=$?
    [[ "$DYLAN_STATUS" -ne 0 ]] || DYLAN_STATUS=1
  fi
  if ! RESULT_JULIA=$(capture_person "Julia Jennings" julia "Julia Jennings"); then
    JULIA_STATUS=$?
    [[ "$JULIA_STATUS" -ne 0 ]] || JULIA_STATUS=1
  fi
  python3 - "$RESULT_DYLAN" "$RESULT_JULIA" <<'PY'
import json
import sys

print(json.dumps({"results": [json.loads(sys.argv[1]), json.loads(sys.argv[2])]}, separators=(",", ":")))
PY
  if (( DYLAN_STATUS != 0 || JULIA_STATUS != 0 )); then
    exit 1
  fi
  exit 0
fi

case "$NAME_LOWER" in
  dylan|"dylan bochman"|db)
    capture_person "Dylan Bochman" dylan "Dylan Bochman"
    ;;
  julia|"julia jennings"|jj)
    capture_person "Julia Jennings" julia "Julia Jennings"
    ;;
  me|clawdbot|clawdbotbochman)
    capture_person "Me" me "Me" "clawdbotbochman"
    ;;
  *)
    emit_error unknown_person "Use dylan, julia, me, both, or cleanup"
    exit 2
    ;;
esac
