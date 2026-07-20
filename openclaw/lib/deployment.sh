#!/usr/bin/env bash

# Shared, side-effect-bounded helpers for copying OpenClaw skill trees.

openclaw_skill_path_is_deployment_artifact() {
  local relative_path="${1#./}"
  local basename="${relative_path##*/}"

  case "/$relative_path/" in
    */__pycache__/*|*/.pytest_cache/*|*/.mypy_cache/*|*/.ruff_cache/*)
      return 0
      ;;
  esac

  case "$basename" in
    *.pyc|*.pyo|.DS_Store|*.local.md)
      return 0
      ;;
  esac

  return 1
}

prune_openclaw_skill_copy() {
  local root="$1"
  [[ -d "$root" && ! -L "$root" ]] || return 1

  find "$root" -depth \
    \( -type d \( -name __pycache__ -o -name .pytest_cache -o -name .mypy_cache -o -name .ruff_cache \) \
       -o -type f \( -name '*.pyc' -o -name '*.pyo' -o -name .DS_Store -o -name '*.local.md' \) \) \
    -exec rm -rf -- {} +
}

copy_openclaw_skill_tree() {
  local src="${1%/}"
  local dst="$2"
  [[ -d "$src" && ! -L "$src" && ! -e "$dst" && ! -L "$dst" ]] || return 1

  cp -R "$src" "$dst" || return 1
  find "$dst" -type l -delete || return 1
  prune_openclaw_skill_copy "$dst"
}

# Read an authoritative `skills.status` Gateway RPC response from stdin and
# print the requested skill names that are not both eligible and model-visible.
# Malformed or unexpected responses fail closed with status 2.
openclaw_gateway_missing_skills_from_status() {
  [[ "$#" -gt 0 ]] || return 2

  /usr/bin/python3 -c '
import json
import sys

required = list(dict.fromkeys(sys.argv[1:]))
try:
    payload = json.load(sys.stdin)
    skills = payload["skills"]
    if not isinstance(skills, list):
        raise TypeError
except (json.JSONDecodeError, KeyError, TypeError, ValueError):
    raise SystemExit(2)

status = {}
for skill in skills:
    if not isinstance(skill, dict):
        continue
    name = skill.get("name")
    if name not in required:
        continue
    if name in status:
        raise SystemExit(2)
    status[name] = skill.get("eligible") is True and skill.get("modelVisible") is True

print(",".join(name for name in required if not status.get(name, False)))
' "$@"
}

# Presence scanner deployment is a high-impact boundary. A candidate must
# advertise the strict binding contract, hash cleanly, and match a site-local
# owner-only approval containing the exact canaried source hash.
openclaw_presence_scanner_has_strict_deployment_contract() {
  local scanner="$1"
  [ -f "$scanner" ] && [ ! -L "$scanner" ] \
    && /usr/bin/grep -Fqx \
      'PRESENCE_SCANNER_DEPLOYMENT_CONTRACT="strict-site-bindings-v1"' \
      "$scanner"
}

openclaw_presence_scanner_sha256() {
  local scanner="$1" digest
  digest=$(/usr/bin/shasum -a 256 "$scanner" 2>/dev/null \
    | /usr/bin/awk 'NR == 1 && $1 ~ /^[0-9a-f]{64}$/ { print $1 }') || return 1
  [ -n "$digest" ] || return 1
  printf '%s\n' "$digest"
}

openclaw_stage_presence_scanner() {
  local scanner="$1" staged metadata owner links size
  [ -f "$scanner" ] && [ ! -L "$scanner" ] || return 1
  metadata=$(/usr/bin/stat -f '%u %l %z' "$scanner" 2>/dev/null) || return 1
  read -r owner links size <<< "$metadata"
  [ "$owner" = "$(/usr/bin/id -u)" ] \
    && [ "$links" = "1" ] \
    && [ "$size" -gt 0 ] \
    && [ "$size" -le 2097152 ] || return 1

  staged=$(/usr/bin/mktemp "${TMPDIR:-/tmp}/openclaw-presence-scanner.XXXXXX") \
    || return 1
  if /bin/cp "$scanner" "$staged" \
      && /bin/chmod 500 "$staged" \
      && [ "$(/usr/bin/stat -f '%u %Lp %l %z' "$staged" 2>/dev/null)" \
           = "$(/usr/bin/id -u) 500 1 $size" ]; then
    printf '%s\n' "$staged"
    return 0
  fi
  /bin/rm -f "$staged"
  return 1
}

openclaw_atomic_install_presence_scanner() {
  local candidate="$1" destination="$2" expected_hash="$3"
  local directory temporary actual_hash destination_metadata
  [[ "$expected_hash" =~ ^[0-9a-f]{64}$ ]] || return 1
  openclaw_presence_scanner_has_strict_deployment_contract "$candidate" \
    || return 1
  [ "$(openclaw_presence_scanner_sha256 "$candidate")" = "$expected_hash" ] \
    || return 1

  directory=$(/usr/bin/dirname "$destination")
  [ -d "$directory" ] && [ ! -L "$directory" ] || return 1
  if [ -e "$destination" ] || [ -L "$destination" ]; then
    [ -f "$destination" ] && [ ! -L "$destination" ] || return 1
    destination_metadata=$(/usr/bin/stat -f '%u %l' "$destination" 2>/dev/null) \
      || return 1
    [ "$destination_metadata" = "$(/usr/bin/id -u) 1" ] || return 1
  fi
  temporary=$(/usr/bin/mktemp "$directory/.${destination##*/}.XXXXXX") \
    || return 1
  if /bin/cp "$candidate" "$temporary" \
      && /bin/chmod 755 "$temporary"; then
    actual_hash=$(openclaw_presence_scanner_sha256 "$temporary") || true
    if [ "$actual_hash" = "$expected_hash" ] \
        && /usr/bin/python3 -c \
          'import os, sys; os.replace(sys.argv[1], sys.argv[2])' \
          "$temporary" "$destination" \
        && [ "$(/usr/bin/stat -f '%u %Lp %l' "$destination" 2>/dev/null)" \
             = "$(/usr/bin/id -u) 755 1" ] \
        && [ "$(openclaw_presence_scanner_sha256 "$destination")" \
             = "$expected_hash" ]; then
      return 0
    fi
  fi
  /bin/rm -f "$temporary"
  return 1
}

openclaw_presence_scanner_approval_status() {
  local expected_hash="$1" approval_file="$2" metadata approved line_count
  [[ "$expected_hash" =~ ^[0-9a-f]{64}$ ]] || return 21
  if [ ! -e "$approval_file" ] && [ ! -L "$approval_file" ]; then
    return 20
  fi
  [ -f "$approval_file" ] && [ ! -L "$approval_file" ] || return 21
  metadata=$(/usr/bin/stat -f '%u %Lp %l %z' "$approval_file" 2>/dev/null) \
    || return 21
  [ "$metadata" = "$(/usr/bin/id -u) 600 1 65" ] || return 21
  line_count=$(/usr/bin/wc -l < "$approval_file" | /usr/bin/tr -d '[:space:]') \
    || return 21
  [ "$line_count" = "1" ] || return 21
  IFS= read -r approved < "$approval_file" || return 21
  [[ "$approved" =~ ^[0-9a-f]{64}$ ]] || return 21
  [ "$approved" = "$expected_hash" ] || return 20
  return 0
}

openclaw_presence_scanner_approval_matches() {
  openclaw_presence_scanner_approval_status "$1" "$2"
}
