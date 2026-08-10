#!/bin/bash
# dotfiles-pull.command — Auto-pull dotfiles repo and deploy skills
# Runs as a LaunchAgent daily via Terminal.app (for git credential access)
# Skills are real copies (not symlinks) because OpenClaw v2026.3.7+ rejects
# symlinks whose realPath resolves outside the configured rootDir.

LOG="$HOME/.openclaw/logs/dotfiles-pull.log"
REPO="$HOME/dotfiles"

set -euo pipefail
trap 'echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) FATAL: dotfiles-pull failed at line $LINENO" >> "$LOG"' ERR

cd "$REPO" || exit 1

# Pre-flight: detect an unresolved merge state left over from a prior run.
# Files in U?/?U/AA/DD/UU state can't be stashed (git stash push silently
# omits them) and prevent `git pull --ff-only` from advancing. If we don't
# bail here, the next steps would: (a) stash nothing, (b) fail to pull,
# (c) the script would proceed to deploy with the stale local checkout,
# silently reverting any commits pushed since the conflict. Bail loudly so
# the operator notices and resolves manually.
UNRESOLVED=$(git status --porcelain | awk '/^(U[ADMU]|[ADMU]U|AA|DD) /')
if [ -n "$UNRESOLVED" ]; then
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) ABORT: unresolved merge state from prior run; refusing to deploy stale files:" >> "$LOG"
  echo "$UNRESOLVED" >> "$LOG"
  exit 1
fi

DIRTY=$(git status --porcelain)

# Disable the ERR trap inside the stash block. Each git command's exit
# code is captured and logged explicitly here — the trap firing on every
# non-zero would just add noise like "FATAL: line 41" for handled
# branches (set +e suppresses errexit but does NOT suppress the trap).
trap - ERR
set +e
PULL_STATUS=99   # sentinel: unset until the pull actually runs
if [ -n "$DIRTY" ]; then
  # Stash local changes, pull, then reapply. Capture stash-list count
  # before/after so we know whether stash push actually created an entry
  # (push returns 0 even when nothing was stashed — e.g., dirty state was
  # only untracked files, which push doesn't capture by default).
  STASH_COUNT_BEFORE=$(git stash list 2>/dev/null | wc -l | tr -d ' ')
  STASH_OUT=$(git stash push -m "auto-stash before daily pull" 2>&1)
  STASH_COUNT_AFTER=$(git stash list 2>/dev/null | wc -l | tr -d ' ')
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Stashed local changes: $STASH_OUT" >> "$LOG"

  PULL_OUT=$(git pull --ff-only origin main 2>&1)
  PULL_STATUS=$?
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) exit=$PULL_STATUS $PULL_OUT" >> "$LOG"

  # Only pop if push actually created a stash entry
  if [ "$STASH_COUNT_AFTER" -gt "$STASH_COUNT_BEFORE" ]; then
    POP_OUT=$(git stash pop 2>&1)
    POP_STATUS=$?
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) stash-pop exit=$POP_STATUS $POP_OUT" >> "$LOG"

    if [ $POP_STATUS -ne 0 ]; then
      echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) WARNING: stash pop had conflicts, dropping stash" >> "$LOG"
      git checkout --theirs . 2>/dev/null
      git stash drop 2>/dev/null
    fi
  else
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) stash-push created no entry (likely untracked-only); skipping pop" >> "$LOG"
  fi
else
  PULL_OUT=$(git pull --ff-only origin main 2>&1)
  PULL_STATUS=$?
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) exit=$PULL_STATUS $PULL_OUT" >> "$LOG"
fi
set -e
trap 'echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) FATAL: dotfiles-pull failed at line $LINENO" >> "$LOG"' ERR

# Refuse to deploy stale files if pull failed. Without this guard, a
# silently-failed pull (network blip, merge conflict, ff-only refusal)
# would result in re-deploying the local checkout's frozen state, which
# silently reverts commits that have been pushed upstream. This was the
# root cause of the bb-watchdog fix being un-deployed on 2026-05-12 when
# an unresolved UU zshrc state blocked git pull at 06:00.
if [ "$PULL_STATUS" -ne 0 ]; then
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) ABORT: git pull failed (exit=$PULL_STATUS); refusing to deploy stale files" >> "$LOG"
  exit 1
fi

GATEWAY_APP_WRAPPER="$REPO/openclaw/OpenClawGateway.app/Contents/MacOS/OpenClawGateway"
GATEWAY_WRAPPER_HASH_STATE="$HOME/.openclaw/state/gateway-wrapper.sha256"
GATEWAY_WRAPPER_HASH=""
if [ -f "$GATEWAY_APP_WRAPPER" ]; then
  GATEWAY_WRAPPER_HASH=$(/usr/bin/shasum -a 256 "$GATEWAY_APP_WRAPPER" | /usr/bin/awk '{print $1}')
fi
GATEWAY_ACTIVATED_HASH=""
if [ -f "$GATEWAY_WRAPPER_HASH_STATE" ] && [ ! -L "$GATEWAY_WRAPPER_HASH_STATE" ] \
  && [ "$(/usr/bin/stat -f '%u %Lp' "$GATEWAY_WRAPPER_HASH_STATE" 2>/dev/null || true)" = "$(/usr/bin/id -u) 600" ]; then
  IFS= read -r GATEWAY_ACTIVATED_HASH < "$GATEWAY_WRAPPER_HASH_STATE" || true
fi
GATEWAY_RESTART_REQUIRED=0
if [ -n "$GATEWAY_WRAPPER_HASH" ] && [ "$GATEWAY_ACTIVATED_HASH" != "$GATEWAY_WRAPPER_HASH" ]; then
  GATEWAY_RESTART_REQUIRED=1
fi

# shellcheck source=../lib/deployment.sh
source "$REPO/openclaw/lib/deployment.sh"

# Sync files the Crosstown MBP runs (it has no dotfiles auto-pull of its own;
# without this, scripts on MBP go stale relative to dotfiles). The Mini owns
# the dedicated SSH key, so existence of the key implies we're on the Mini
# and the MBP is the intended target.
# Format: "<repo-relative-src>:<MBP-home-relative-dst>"
MBP_SSH_KEY="$HOME/.ssh/id_mini_to_mbp"
MBP_HOST="dylans-macbook-pro"
MBP_SYNC_PAIRS=(
  "openclaw/workspace/scripts/presence-detect.sh:.openclaw/workspace/scripts/presence-detect.sh"
  "openclaw/skills/august-lock/august-cmd.js:.openclaw/august/august-cmd.js"
  "openclaw/rest980/start-10max.sh:.openclaw/rest980/start-10max.sh"
  "openclaw/rest980/start-j5.sh:.openclaw/rest980/start-j5.sh"
  "openclaw/rest980/roomba-cmd.js:.openclaw/rest980/roomba-cmd.js"
)

HOST_KEY=$(hostname -s 2>/dev/null | tr '[:upper:]' '[:lower:]')
IS_GATEWAY_HOST=0
case "$HOST_KEY" in
  mac-mini|mac-mini-[0-9]*|dylans-mac-mini|dylans-mac-mini-[0-9]*) IS_GATEWAY_HOST=1 ;;
esac
if [ "$IS_GATEWAY_HOST" -eq 1 ] && [ ! -f "$MBP_SSH_KEY" ]; then
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) mbp-sync: FATAL dedicated SSH key is missing; refusing protocol-dependent local deployment" >> "$LOG"
  exit 1
fi
if [ -f "$MBP_SSH_KEY" ]; then
  MBP_PROTOCOL_SYNC_FAILED=0
  MBP_PRESENCE_ACTIVATION_FAILED=0
  MBP_SYNC_OK=0
  MBP_SYNC_TOTAL=0
  MBP_SYNC_ERR=""
  MBP_SYNC_LEVEL="WARN"
  for pair in "${MBP_SYNC_PAIRS[@]}"; do
    src_rel="${pair%%:*}"
    dst_rel="${pair##*:}"
    src="$REPO/$src_rel"
    [ -f "$src" ] || continue
    MBP_SYNC_TOTAL=$((MBP_SYNC_TOTAL + 1))
    PROTOCOL_PAIR=0
    case "$src_rel" in
      openclaw/skills/august-lock/august-cmd.js|openclaw/rest980/roomba-cmd.js)
        PROTOCOL_PAIR=1
        ;;
    esac
    if [ "$src_rel" = "openclaw/workspace/scripts/presence-detect.sh" ]; then
      if ! PRESENCE_CANDIDATE=$(openclaw_stage_presence_scanner "$src"); then
        MBP_SYNC_ERR="Tracked presence scanner could not be staged safely; preserved prior scanner"
        MBP_SYNC_LEVEL="WARN"
        continue
      fi
      if ! openclaw_presence_scanner_has_strict_deployment_contract "$PRESENCE_CANDIDATE"; then
        /bin/rm -f "$PRESENCE_CANDIDATE"
        MBP_SYNC_ERR="Tracked presence scanner lacks the strict binding contract; preserved prior scanner"
        MBP_SYNC_LEVEL="WARN"
        continue
      fi
      if ! PRESENCE_SCANNER_HASH=$(openclaw_presence_scanner_sha256 "$PRESENCE_CANDIDATE"); then
        /bin/rm -f "$PRESENCE_CANDIDATE"
        MBP_SYNC_ERR="Tracked presence scanner hash is unavailable; preserved prior scanner"
        MBP_SYNC_LEVEL="WARN"
        continue
      fi
      if ! ssh -i "$MBP_SSH_KEY" -o IdentityAgent=none \
               -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 \
               "$MBP_HOST" \
               'PRESENCE_DEVICE_CONFIG="$HOME/.openclaw/presence-devices.json" /bin/bash -s -- validate-config crosstown' \
               < "$PRESENCE_CANDIDATE" \
               >/dev/null 2>&1; then
        /bin/rm -f "$PRESENCE_CANDIDATE"
        MBP_SYNC_ERR="Crosstown presence bindings missing or invalid; preserved prior scanner"
        MBP_SYNC_LEVEL="WARN"
        continue
      fi

      PRESENCE_APPROVAL_STATUS=0
      if ssh -i "$MBP_SSH_KEY" -o IdentityAgent=none \
             -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 \
             "$MBP_HOST" \
             "PRESENCE_EXPECTED_SCANNER_HASH=$PRESENCE_SCANNER_HASH /bin/bash -s" \
             >/dev/null 2>&1 <<'PRESENCE_APPROVAL'
set -u
approval_file="$HOME/.openclaw/presence-scanner-approved.sha256"
expected_hash="${PRESENCE_EXPECTED_SCANNER_HASH:-}"
[[ "$expected_hash" =~ ^[0-9a-f]{64}$ ]] || exit 21
if [ ! -e "$approval_file" ] && [ ! -L "$approval_file" ]; then
  exit 20
fi
[ -f "$approval_file" ] && [ ! -L "$approval_file" ] || exit 21
metadata=$(/usr/bin/stat -f '%u %Lp %l %z' "$approval_file" 2>/dev/null) || exit 21
[ "$metadata" = "$(/usr/bin/id -u) 600 1 65" ] || exit 21
line_count=$(/usr/bin/wc -l < "$approval_file" | /usr/bin/tr -d '[:space:]') || exit 21
[ "$line_count" = "1" ] || exit 21
IFS= read -r approved < "$approval_file" || exit 21
[[ "$approved" =~ ^[0-9a-f]{64}$ ]] || exit 21
[ "$approved" = "$expected_hash" ] || exit 20
PRESENCE_APPROVAL
      then
        :
      else
        PRESENCE_APPROVAL_STATUS=$?
      fi
      if [ "$PRESENCE_APPROVAL_STATUS" -ne 0 ]; then
        /bin/rm -f "$PRESENCE_CANDIDATE"
        if [ "$PRESENCE_APPROVAL_STATUS" -eq 20 ]; then
          MBP_SYNC_ERR="Crosstown strict presence scanner awaits exact canary approval; preserved legacy scanner"
          MBP_SYNC_LEVEL="INFO"
        else
          MBP_SYNC_ERR="Crosstown presence scanner approval is invalid or could not be verified; preserved legacy scanner"
          MBP_SYNC_LEVEL="WARN"
        fi
        continue
      fi

      if ! PRESENCE_REMOTE_CANDIDATE=$(ssh -i "$MBP_SSH_KEY" -o IdentityAgent=none \
               -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 \
               "$MBP_HOST" \
               'umask 077; cd "$HOME" && /usr/bin/mktemp ".openclaw/workspace/scripts/.presence-detect.sh.XXXXXX"' \
               2>/dev/null) \
          || [[ ! "$PRESENCE_REMOTE_CANDIDATE" =~ ^\.openclaw/workspace/scripts/\.presence-detect\.sh\.[A-Za-z0-9]+$ ]]; then
        /bin/rm -f "$PRESENCE_CANDIDATE"
        MBP_SYNC_ERR="Crosstown approved scanner could not create a protected staging file; preserved legacy scanner"
        MBP_SYNC_LEVEL="WARN"
        MBP_PRESENCE_ACTIVATION_FAILED=1
        continue
      fi
      if ! scp -i "$MBP_SSH_KEY" -o IdentityAgent=none \
               -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -q \
               "$PRESENCE_CANDIDATE" "$MBP_HOST:$PRESENCE_REMOTE_CANDIDATE" \
               >/dev/null 2>&1; then
        ssh -i "$MBP_SSH_KEY" -o IdentityAgent=none \
            -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 \
            "$MBP_HOST" \
            "PRESENCE_REMOTE_CANDIDATE=$PRESENCE_REMOTE_CANDIDATE /bin/bash -s" \
            >/dev/null 2>&1 <<'PRESENCE_CLEANUP' || true
case "${PRESENCE_REMOTE_CANDIDATE:-}" in
  .openclaw/workspace/scripts/.presence-detect.sh.*)
    /bin/rm -f "$HOME/$PRESENCE_REMOTE_CANDIDATE"
    ;;
esac
PRESENCE_CLEANUP
        /bin/rm -f "$PRESENCE_CANDIDATE"
        MBP_SYNC_ERR="Crosstown approved scanner upload failed; preserved legacy scanner"
        MBP_SYNC_LEVEL="WARN"
        MBP_PRESENCE_ACTIVATION_FAILED=1
        continue
      fi
      if ! ssh -i "$MBP_SSH_KEY" -o IdentityAgent=none \
               -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 \
               "$MBP_HOST" \
               "PRESENCE_EXPECTED_SCANNER_HASH=$PRESENCE_SCANNER_HASH PRESENCE_REMOTE_CANDIDATE=$PRESENCE_REMOTE_CANDIDATE /bin/bash -s" \
               >/dev/null 2>&1 <<'PRESENCE_FINALIZE'
set -euo pipefail
expected_hash="${PRESENCE_EXPECTED_SCANNER_HASH:-}"
relative_candidate="${PRESENCE_REMOTE_CANDIDATE:-}"
[[ "$expected_hash" =~ ^[0-9a-f]{64}$ ]]
[[ "$relative_candidate" =~ ^\.openclaw/workspace/scripts/\.presence-detect\.sh\.[A-Za-z0-9]+$ ]]
candidate="$HOME/$relative_candidate"
destination="$HOME/.openclaw/workspace/scripts/presence-detect.sh"
approval_file="$HOME/.openclaw/presence-scanner-approved.sha256"
cleanup() { /bin/rm -f "$candidate"; }
trap cleanup EXIT

[ -f "$candidate" ] && [ ! -L "$candidate" ]
metadata=$(/usr/bin/stat -f '%u %Lp %l %z' "$candidate")
read -r owner mode links size <<< "$metadata"
[ "$owner" = "$(/usr/bin/id -u)" ] && [ "$links" = "1" ]
[ "$size" -gt 0 ] && [ "$size" -le 2097152 ]
(( (8#$mode & 0022) == 0 ))
actual_hash=$(/usr/bin/shasum -a 256 "$candidate" | /usr/bin/awk 'NR == 1 { print $1 }')
[ "$actual_hash" = "$expected_hash" ]
/usr/bin/grep -Fqx \
  'PRESENCE_SCANNER_DEPLOYMENT_CONTRACT="strict-site-bindings-v1"' \
  "$candidate"

[ -f "$approval_file" ] && [ ! -L "$approval_file" ]
[ "$(/usr/bin/stat -f '%u %Lp %l %z' "$approval_file")" \
  = "$(/usr/bin/id -u) 600 1 65" ]
[ "$(/usr/bin/wc -l < "$approval_file" | /usr/bin/tr -d '[:space:]')" = "1" ]
IFS= read -r approved < "$approval_file"
[ "$approved" = "$expected_hash" ]

PRESENCE_DEVICE_CONFIG="$HOME/.openclaw/presence-devices.json" \
  /bin/bash "$candidate" validate-config crosstown >/dev/null 2>&1
if [ -e "$destination" ] || [ -L "$destination" ]; then
  [ -f "$destination" ] && [ ! -L "$destination" ]
  [ "$(/usr/bin/stat -f '%u %l' "$destination")" \
    = "$(/usr/bin/id -u) 1" ]
fi
/bin/chmod 755 "$candidate"
/usr/bin/python3 -c \
  'import os, sys; os.replace(sys.argv[1], sys.argv[2])' \
  "$candidate" "$destination"
[ "$(/usr/bin/stat -f '%u %Lp %l' "$destination")" \
  = "$(/usr/bin/id -u) 755 1" ]
[ "$(/usr/bin/shasum -a 256 "$destination" | /usr/bin/awk 'NR == 1 { print $1 }')" \
  = "$expected_hash" ]
trap - EXIT
PRESENCE_FINALIZE
      then
        /bin/rm -f "$PRESENCE_CANDIDATE"
        MBP_SYNC_ERR="Crosstown approved scanner failed final verification; inspect the last atomic runtime before retry"
        MBP_SYNC_LEVEL="WARN"
        MBP_PRESENCE_ACTIVATION_FAILED=1
        continue
      fi
      /bin/rm -f "$PRESENCE_CANDIDATE"
      MBP_SYNC_OK=$((MBP_SYNC_OK + 1))
      continue
    fi
    if [ "$src_rel" = "openclaw/skills/august-lock/august-cmd.js" ]; then
      if ! ssh -i "$MBP_SSH_KEY" -o IdentityAgent=none \
               -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 \
               "$MBP_HOST" \
               'p="$HOME/.openclaw/august/config.json"; [ -f "$p" ] && [ ! -L "$p" ] && [ "$(stat -f "%u %Lp" "$p")" = "$(id -u) 600" ]' \
               >/dev/null 2>&1; then
        MBP_SYNC_ERR="August config missing or insecure"
        MBP_SYNC_LEVEL="WARN"
        MBP_PROTOCOL_SYNC_FAILED=1
        continue
      fi
    fi
    if [ "$src_rel" = "openclaw/rest980/roomba-cmd.js" ]; then
      if ! ssh -i "$MBP_SSH_KEY" -o IdentityAgent=none \
               -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 \
               "$MBP_HOST" \
               'for p in "$HOME/.openclaw/rest980/env-10max" "$HOME/.openclaw/rest980/env-j5"; do [ -f "$p" ] && [ ! -L "$p" ] && [ "$(stat -f "%u %Lp" "$p")" = "$(id -u) 600" ] || exit 1; done' \
               >/dev/null 2>&1; then
        MBP_SYNC_ERR="Roomba credential files missing or insecure"
        MBP_SYNC_LEVEL="WARN"
        MBP_PROTOCOL_SYNC_FAILED=1
        continue
      fi
    fi
    if scp_err=$(scp -i "$MBP_SSH_KEY" -o IdentityAgent=none \
                     -o StrictHostKeyChecking=accept-new \
                     -o ConnectTimeout=10 -q \
                     "$src" "$MBP_HOST:$dst_rel" 2>&1); then
      MBP_SYNC_OK=$((MBP_SYNC_OK + 1))
    else
      MBP_SYNC_ERR="$scp_err"
      MBP_SYNC_LEVEL="WARN"
      if [ "$PROTOCOL_PAIR" -eq 1 ]; then
        MBP_PROTOCOL_SYNC_FAILED=1
      fi
    fi
  done
  if [ "$MBP_PROTOCOL_SYNC_FAILED" -ne 0 ]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) mbp-sync: FATAL protocol counterpart was not synchronized: ${MBP_SYNC_ERR:-unknown error}" >> "$LOG"
    exit 1
  fi
  if [ "$MBP_PRESENCE_ACTIVATION_FAILED" -ne 0 ]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) mbp-sync: FATAL approved presence scanner activation failed: ${MBP_SYNC_ERR:-unknown error}" >> "$LOG"
    exit 1
  fi
  if [ "$MBP_SYNC_OK" -eq "$MBP_SYNC_TOTAL" ]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) mbp-sync: synced $MBP_SYNC_OK/$MBP_SYNC_TOTAL files to $MBP_HOST" >> "$LOG"
  else
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) mbp-sync: $MBP_SYNC_LEVEL synced $MBP_SYNC_OK/$MBP_SYNC_TOTAL files to $MBP_HOST: ${MBP_SYNC_ERR:-unknown error}" >> "$LOG"
  fi
fi

# Deploy skills as real copies (OpenClaw rejects symlinks via realPath check)
SKILLS_SRC="$REPO/openclaw/skills"
SKILLS_DST="$HOME/.openclaw/skills"
if [ -d "$SKILLS_SRC" ]; then
  mkdir -p "$SKILLS_DST"

  # Remove only known catalog entries retired from the tracked skills tree.
  # Real-copy deployments otherwise survive after their sources are removed.
  for retired_skill_name in TEMPLATE gws-shared; do
    retired_skill_path="$SKILLS_DST/$retired_skill_name"
    if [ -e "$retired_skill_path" ] || [ -L "$retired_skill_path" ]; then
      rm -rf -- "$retired_skill_path"
      echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) skills: removed retired $retired_skill_name" >> "$LOG"
    fi
  done

  DEPLOYED=0
  for skill_dir in "$SKILLS_SRC"/*/; do
    [ -d "$skill_dir" ] || continue
    [ -f "$skill_dir/SKILL.md" ] || continue
    skill_name=$(basename "$skill_dir")
    rm -rf -- "$SKILLS_DST/$skill_name"
    copy_openclaw_skill_tree "$skill_dir" "$SKILLS_DST/$skill_name"
    # Preserve executable bit on CLI wrappers inside skills
    find "$SKILLS_DST/$skill_name" -maxdepth 1 -type f ! -name "*.md" ! -name "*.json" ! -name "*.yaml" -exec chmod +x {} +
    DEPLOYED=$((DEPLOYED + 1))
  done
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) skills: deployed $DEPLOYED skills to $SKILLS_DST" >> "$LOG"
fi

MIDEA_SKILL_DIR="$SKILLS_DST/midea-ac"
MIDEA_VENV_DIR="$HOME/.openclaw/venvs/midea-ac"
if [ ! -f "$MIDEA_SKILL_DIR/pyproject.toml" ] \
  || [ -L "$MIDEA_SKILL_DIR/pyproject.toml" ] \
  || [ ! -f "$MIDEA_SKILL_DIR/uv.lock" ] \
  || [ -L "$MIDEA_SKILL_DIR/uv.lock" ] \
  || [ ! -x /opt/homebrew/bin/uv ] \
  || [ -L "$HOME/.openclaw/venvs" ] \
  || [ -L "$MIDEA_VENV_DIR" ]; then
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) midea-ac: FATAL locked runtime inputs are unavailable or unsafe" >> "$LOG"
  exit 1
fi
mkdir -p "$HOME/.openclaw/venvs"
if ! UV_PROJECT_ENVIRONMENT="$MIDEA_VENV_DIR" \
  /opt/homebrew/bin/uv sync --frozen --no-dev --project "$MIDEA_SKILL_DIR" \
  >> "$LOG" 2>&1; then
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) midea-ac: FATAL locked runtime sync failed" >> "$LOG"
  exit 1
fi
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) midea-ac: locked runtime ready" >> "$LOG"

AIRTHINGS_SKILL_DIR="$SKILLS_DST/airthings-monitor"
AIRTHINGS_VENV_DIR="$HOME/.openclaw/venvs/airthings-monitor"
if [ ! -f "$AIRTHINGS_SKILL_DIR/pyproject.toml" ] \
  || [ -L "$AIRTHINGS_SKILL_DIR/pyproject.toml" ] \
  || [ ! -f "$AIRTHINGS_SKILL_DIR/uv.lock" ] \
  || [ -L "$AIRTHINGS_SKILL_DIR/uv.lock" ] \
  || [ ! -x /opt/homebrew/bin/uv ] \
  || [ ! -x /opt/homebrew/bin/python3.14 ] \
  || [ -L "$HOME/.openclaw/venvs" ] \
  || [ -L "$AIRTHINGS_VENV_DIR" ]; then
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) airthings: FATAL locked runtime inputs are unavailable or unsafe" >> "$LOG"
  exit 1
fi
mkdir -p "$HOME/.openclaw/venvs"
if ! UV_PROJECT_ENVIRONMENT="$AIRTHINGS_VENV_DIR" \
  UV_PYTHON=/opt/homebrew/bin/python3.14 \
  /opt/homebrew/bin/uv sync --frozen --no-dev --project "$AIRTHINGS_SKILL_DIR" \
  >> "$LOG" 2>&1; then
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) airthings: FATAL locked runtime sync failed" >> "$LOG"
  exit 1
fi
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) airthings: locked runtime ready" >> "$LOG"

# Deploy CLI wrappers and scripts to ~/.openclaw/bin/
BIN_SRC="$REPO/openclaw/bin"
BIN_DST="$HOME/.openclaw/bin"

# Schema changes are attended migrations. A routine pull may refresh the
# tracked source, but it must preserve the last compatible runtime binaries
# and loaded jobs until the protected production database has already been
# migrated and verified by an operator.
HOME_EVENT_SOURCE_SCHEMA=$(
  /usr/bin/awk '$1 == "SCHEMA_VERSION" && $2 == "=" && $3 ~ /^[0-9]+$/ { print $3; exit }' \
    "$BIN_SRC/home_event_bus.py"
)
NEST_EVENT_SOURCE_SCHEMA=$(
  /usr/bin/awk '$1 == "SCHEMA_VERSION" && $2 == "=" && $3 ~ /^[0-9]+$/ { print $3; exit }' \
    "$BIN_SRC/nest-event-listener.py"
)
case "$HOME_EVENT_SOURCE_SCHEMA:$NEST_EVENT_SOURCE_SCHEMA" in
  *[!0-9:]*|:*|*:)
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) schema-guard: FATAL source schema version is invalid" >> "$LOG"
    exit 1
    ;;
esac

protected_sqlite_schema_matches() {
  local database="$1"
  local query="$2"
  local expected="$3"
  local actual

  if [ ! -e "$database" ] && [ ! -L "$database" ]; then
    return 0
  fi
  [ -f "$database" ] && [ ! -L "$database" ] \
    && [ "$(/usr/bin/stat -f '%u %Lp' "$database" 2>/dev/null || true)" = "$(/usr/bin/id -u) 600" ] \
    && [ -x /usr/bin/sqlite3 ] \
    || return 1
  actual=$(/usr/bin/sqlite3 "$database" "$query" 2>/dev/null) || return 1
  [ "$actual" = "$expected" ]
}

HOME_EVENT_SCHEMA_DEPLOY_READY=1
if ! protected_sqlite_schema_matches \
    "$HOME/.openclaw/home-events/state/events.sqlite3" \
    'SELECT group_concat(version, ",") FROM schema_migrations' \
    "$HOME_EVENT_SOURCE_SCHEMA"; then
  HOME_EVENT_SCHEMA_DEPLOY_READY=0
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) schema-guard: WARN home-event migration required; preserving prior runtime" >> "$LOG"
fi

NEST_EVENT_SCHEMA_DEPLOY_READY=1
if ! protected_sqlite_schema_matches \
    "$HOME/.openclaw/nest-events/state/events.sqlite3" \
    'SELECT group_concat(version, ",") FROM schema_meta' \
    "$NEST_EVENT_SOURCE_SCHEMA"; then
  NEST_EVENT_SCHEMA_DEPLOY_READY=0
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) schema-guard: WARN Nest listener migration required; preserving prior runtime" >> "$LOG"
fi

atomic_install_managed_file() {
  local src="$1"
  local dst="$2"
  local mode="$3"
  local tmp

  tmp=$(mktemp "$(dirname "$dst")/.${dst##*/}.XXXXXX")
  if cp "$src" "$tmp" && chmod "$mode" "$tmp" && mv -f "$tmp" "$dst"; then
    return 0
  fi
  rm -f "$tmp"
  return 1
}

atomic_install_executable() {
  atomic_install_managed_file "$1" "$2" 755
}

WRAPPER_DEPLOYED=0
DEPLOYED_WRAPPERS=""
HOME_EVENT_INGEST_CHANGED=0
HOME_EVENT_CORRELATOR_CHANGED=0
HOME_EVENT_AUGUST_CHANGED=0
HOME_EVENT_NEST_CHANGED=0
HOME_EVENT_LOCAL_PRESENCE_CHANGED=0
HOME_EVENT_DELIVERY_CHANGED=0
HOME_EVENT_CAMERA_CHANGED=0
for wrapper in "$BIN_SRC"/*; do
  [ -f "$wrapper" ] || continue
  fname=$(basename "$wrapper")
  # Skip files with extensions (deployed separately or not wrappers) and non-executables
  case "$fname" in
    *.py|*.sh|*.command|*.md|*.json|*.yaml) continue ;;
  esac
  [ -x "$wrapper" ] || continue
  if [ "$fname" = "home-eventctl" ] && [ "$HOME_EVENT_SCHEMA_DEPLOY_READY" -ne 1 ]; then
    continue
  fi
  case "$fname" in
    home-eventctl)
      if [ ! -f "$BIN_DST/$fname" ] || ! cmp -s "$wrapper" "$BIN_DST/$fname"; then
        HOME_EVENT_INGEST_CHANGED=1
        HOME_EVENT_AUGUST_CHANGED=1
        HOME_EVENT_NEST_CHANGED=1
        HOME_EVENT_LOCAL_PRESENCE_CHANGED=1
      fi
      ;;
  esac
  atomic_install_executable "$wrapper" "$BIN_DST/$fname"
  WRAPPER_DEPLOYED=$((WRAPPER_DEPLOYED + 1))
  DEPLOYED_WRAPPERS="$DEPLOYED_WRAPPERS $fname"
done
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) wrappers: deployed $WRAPPER_DEPLOYED to $BIN_DST" >> "$LOG"

# OpenClaw's standalone skill checker and doctor do not inherit the gateway's
# ~/.openclaw/bin PATH. Doctor persists enabled=false for skills whose required
# binaries appear unavailable, so publish managed skill wrappers in Homebrew's
# PATH too.
STANDALONE_SKILL_WRAPPERS=(
  airthings
  cielo
  cielo-reauth
  midea-ac
  reachyctl
  opentable-book
  opentable-reservations
  pinchtab-headless-instance
  plant-tracker
  reolink-camera
  restaurant-book
  restaurant-snipe
  resy-read
)
for skill_wrapper in "${STANDALONE_SKILL_WRAPPERS[@]}"; do
  if [ -x "$BIN_DST/$skill_wrapper" ]; then
    ln -sfn "$BIN_DST/$skill_wrapper" "/opt/homebrew/bin/$skill_wrapper"
  fi
done

# Preserve the documented maintenance path while ~/.openclaw/bin remains the
# canonical wrapper directory. This must be a real copy because ~/bin predates
# the current deployment layout and is still used by operator runbooks.
if [ -x "$BIN_SRC/openclaw-refresh-secrets" ]; then
  mkdir -p "$HOME/bin"
  cp "$BIN_SRC/openclaw-refresh-secrets" "$HOME/bin/openclaw-refresh-secrets"
  chmod +x "$HOME/bin/openclaw-refresh-secrets"
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) wrappers: refreshed ~/bin/openclaw-refresh-secrets compatibility copy" >> "$LOG"
fi

# Deploy dashboard and utility scripts to ~/.openclaw/bin/
SCRIPTS_DEPLOYED=0
NEST_EVENT_RUNTIME_CHANGED=0
NEST_ACTIVITY_RUNTIME_CHANGED=0
CABIN_ENTRY_RUNTIME_CHANGED=0
OLA_BRIDGE_RUNTIME_CHANGED=0
for script in "$BIN_SRC"/*.py "$BIN_SRC"/*.sh; do
  [ -f "$script" ] || continue
  fname=$(basename "$script")
  case "$fname" in
    home_event_bus.py|home-event-correlator.py|home-event-service-wrapper.sh|home-event-delivery.py|home-event-delivery-wrapper.sh|home-event-camera.py|home-event-camera-wrapper.sh|august-event-adapter.py|presence-local-event-adapter.py)
      [ "$HOME_EVENT_SCHEMA_DEPLOY_READY" -eq 1 ] || continue
      ;;
    nest-home-event-bridge.py)
      if [ "$HOME_EVENT_SCHEMA_DEPLOY_READY" -ne 1 ] \
        || [ "$NEST_EVENT_SCHEMA_DEPLOY_READY" -ne 1 ]; then
        continue
      fi
      ;;
    nest-event-listener.py|nest-event-listener-wrapper.sh|nest-activity-reviewer.py|nest-activity-reviewer-wrapper.sh)
      [ "$NEST_EVENT_SCHEMA_DEPLOY_READY" -eq 1 ] || continue
      ;;
    cabin-entry-verifier.py|cabin-entry-verifier-wrapper.sh)
      if [ "$HOME_EVENT_SCHEMA_DEPLOY_READY" -ne 1 ] \
        || [ "$NEST_EVENT_SCHEMA_DEPLOY_READY" -ne 1 ]; then
        continue
      fi
      ;;
  esac
  case "$fname" in
    nest-event-listener.py|nest-event-listener-wrapper.sh)
      if [ ! -f "$BIN_DST/$fname" ] || ! cmp -s "$script" "$BIN_DST/$fname"; then
        NEST_EVENT_RUNTIME_CHANGED=1
      fi
      ;;
    nest-activity-reviewer.py|nest-activity-reviewer-wrapper.sh)
      if [ ! -f "$BIN_DST/$fname" ] || ! cmp -s "$script" "$BIN_DST/$fname"; then
        NEST_ACTIVITY_RUNTIME_CHANGED=1
      fi
      ;;
    cabin-entry-verifier.py|cabin-entry-verifier-wrapper.sh)
      if [ ! -f "$BIN_DST/$fname" ] || ! cmp -s "$script" "$BIN_DST/$fname"; then
        CABIN_ENTRY_RUNTIME_CHANGED=1
      fi
      ;;
    ola-webhook-bridge.py|ola-webhook-bridge-wrapper.sh)
      if [ ! -f "$BIN_DST/$fname" ] || ! cmp -s "$script" "$BIN_DST/$fname"; then
        OLA_BRIDGE_RUNTIME_CHANGED=1
      fi
      ;;
    home_event_bus.py)
      if [ ! -f "$BIN_DST/$fname" ] || ! cmp -s "$script" "$BIN_DST/$fname"; then
        HOME_EVENT_INGEST_CHANGED=1
        HOME_EVENT_CORRELATOR_CHANGED=1
        HOME_EVENT_NEST_CHANGED=1
        HOME_EVENT_LOCAL_PRESENCE_CHANGED=1
        HOME_EVENT_DELIVERY_CHANGED=1
        HOME_EVENT_CAMERA_CHANGED=1
      fi
      ;;
    home-event-correlator.py)
      if [ ! -f "$BIN_DST/$fname" ] || ! cmp -s "$script" "$BIN_DST/$fname"; then
        HOME_EVENT_CORRELATOR_CHANGED=1
      fi
      ;;
    home-event-delivery.py|home-event-delivery-wrapper.sh)
      if [ ! -f "$BIN_DST/$fname" ] || ! cmp -s "$script" "$BIN_DST/$fname"; then
        HOME_EVENT_DELIVERY_CHANGED=1
      fi
      ;;
    home-event-camera.py|home-event-camera-wrapper.sh)
      if [ ! -f "$BIN_DST/$fname" ] || ! cmp -s "$script" "$BIN_DST/$fname"; then
        HOME_EVENT_CAMERA_CHANGED=1
      fi
      ;;
    august-event-adapter.py)
      if [ ! -f "$BIN_DST/$fname" ] || ! cmp -s "$script" "$BIN_DST/$fname"; then
        HOME_EVENT_AUGUST_CHANGED=1
      fi
      ;;
    presence-local-event-adapter.py)
      if [ ! -f "$BIN_DST/$fname" ] || ! cmp -s "$script" "$BIN_DST/$fname"; then
        HOME_EVENT_LOCAL_PRESENCE_CHANGED=1
      fi
      ;;
    nest-home-event-bridge.py)
      if [ ! -f "$BIN_DST/$fname" ] || ! cmp -s "$script" "$BIN_DST/$fname"; then
        HOME_EVENT_NEST_CHANGED=1
      fi
      ;;
    home-event-service-wrapper.sh)
      if [ ! -f "$BIN_DST/$fname" ] || ! cmp -s "$script" "$BIN_DST/$fname"; then
        HOME_EVENT_INGEST_CHANGED=1
        HOME_EVENT_CORRELATOR_CHANGED=1
        HOME_EVENT_AUGUST_CHANGED=1
        HOME_EVENT_NEST_CHANGED=1
        HOME_EVENT_LOCAL_PRESENCE_CHANGED=1
      fi
      ;;
  esac
  atomic_install_executable "$script" "$BIN_DST/$fname"
  SCRIPTS_DEPLOYED=$((SCRIPTS_DEPLOYED + 1))
done
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) scripts: deployed $SCRIPTS_DEPLOYED to $BIN_DST" >> "$LOG"

# Deploy the standing-authorized restaurant scopes as a protected regular
# file. The coordinator rejects symlinks, loose permissions, and unknown job
# IDs, so the runtime registry must move atomically with the public wrapper.
RESTAURANT_SCOPES_SRC="$REPO/openclaw/cron/restaurant-booking-scopes.json"
RESTAURANT_SCOPES_DST="$HOME/.openclaw/restaurant-bookings/scopes.json"
if [ -f "$RESTAURANT_SCOPES_SRC" ] && [ ! -L "$RESTAURANT_SCOPES_SRC" ]; then
  mkdir -p "$(dirname "$RESTAURANT_SCOPES_DST")"
  atomic_install_managed_file "$RESTAURANT_SCOPES_SRC" "$RESTAURANT_SCOPES_DST" 600
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) restaurant-book: deployed protected scope registry" >> "$LOG"
else
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) restaurant-book: FATAL scope registry is unavailable" >> "$LOG"
  exit 1
fi

# Keep the reboot recovery LaunchAgent synchronized. Most OpenClaw plists are
# deployed manually because they carry service-specific state; this watchdog
# is intentionally repo-managed and safe to reload whenever its source changes.
IMSG_WATCHDOG_LABEL="ai.openclaw.imsg-bridge-ensure"
IMSG_WATCHDOG_SRC="$REPO/openclaw/launchagents/$IMSG_WATCHDOG_LABEL.plist"
IMSG_WATCHDOG_DST="$HOME/Library/LaunchAgents/$IMSG_WATCHDOG_LABEL.plist"
if [ -f "$IMSG_WATCHDOG_SRC" ]; then
  IMSG_WATCHDOG_CHANGED=0
  if [ ! -f "$IMSG_WATCHDOG_DST" ] || ! cmp -s "$IMSG_WATCHDOG_SRC" "$IMSG_WATCHDOG_DST"; then
    cp "$IMSG_WATCHDOG_SRC" "$IMSG_WATCHDOG_DST.new"
    chmod 644 "$IMSG_WATCHDOG_DST.new"
    mv "$IMSG_WATCHDOG_DST.new" "$IMSG_WATCHDOG_DST"
    IMSG_WATCHDOG_CHANGED=1
  fi

  IMSG_WATCHDOG_DOMAIN="gui/$(id -u)"
  if launchctl print "$IMSG_WATCHDOG_DOMAIN/$IMSG_WATCHDOG_LABEL" >/dev/null 2>&1; then
    if [ "$IMSG_WATCHDOG_CHANGED" -eq 1 ]; then
      launchctl bootout "$IMSG_WATCHDOG_DOMAIN/$IMSG_WATCHDOG_LABEL" >/dev/null 2>&1 || true
      launchctl bootstrap "$IMSG_WATCHDOG_DOMAIN" "$IMSG_WATCHDOG_DST"
      echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) launchagent: reloaded $IMSG_WATCHDOG_LABEL" >> "$LOG"
    fi
  else
    launchctl bootstrap "$IMSG_WATCHDOG_DOMAIN" "$IMSG_WATCHDOG_DST"
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) launchagent: bootstrapped $IMSG_WATCHDOG_LABEL" >> "$LOG"
  fi
fi

# Refresh the Ola HMAC bridge only after attended secret enrollment and
# bootstrap have installed its plist. A routine pull must never publish a new
# callback listener merely because its tracked implementation exists.
OLA_BRIDGE_LABEL="ai.openclaw.ola-webhook-bridge"
OLA_BRIDGE_SRC="$REPO/openclaw/launchagents/$OLA_BRIDGE_LABEL.plist"
OLA_BRIDGE_DST="$HOME/Library/LaunchAgents/$OLA_BRIDGE_LABEL.plist"
if [ -e "$OLA_BRIDGE_DST" ] || [ -L "$OLA_BRIDGE_DST" ]; then
  if [ ! -f "$OLA_BRIDGE_SRC" ] || [ -L "$OLA_BRIDGE_SRC" ] \
    || [ ! -f "$OLA_BRIDGE_DST" ] || [ -L "$OLA_BRIDGE_DST" ]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) ola-webhook: FATAL installed LaunchAgent is unavailable or unsafe" >> "$LOG"
    exit 1
  fi

  OLA_BRIDGE_AGENT_CHANGED=0
  if ! cmp -s "$OLA_BRIDGE_SRC" "$OLA_BRIDGE_DST"; then
    atomic_install_managed_file "$OLA_BRIDGE_SRC" "$OLA_BRIDGE_DST" 644
    OLA_BRIDGE_AGENT_CHANGED=1
  fi

  OLA_BRIDGE_DOMAIN="gui/$(id -u)"
  if launchctl print "$OLA_BRIDGE_DOMAIN/$OLA_BRIDGE_LABEL" >/dev/null 2>&1; then
    if [ "$OLA_BRIDGE_AGENT_CHANGED" -eq 1 ] || [ "$OLA_BRIDGE_RUNTIME_CHANGED" -eq 1 ]; then
      launchctl bootout "$OLA_BRIDGE_DOMAIN/$OLA_BRIDGE_LABEL" >/dev/null 2>&1 || true
      OLA_BRIDGE_RELOAD_OK=0
      for OLA_BRIDGE_RELOAD_ATTEMPT in 1 2 3; do
        if launchctl bootstrap "$OLA_BRIDGE_DOMAIN" "$OLA_BRIDGE_DST"; then
          OLA_BRIDGE_RELOAD_OK=1
          break
        fi
        sleep 1
      done
      if [ "$OLA_BRIDGE_RELOAD_OK" -ne 1 ]; then
        echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) ola-webhook: FATAL bridge reload failed" >> "$LOG"
        exit 1
      fi
      OLA_BRIDGE_HEALTH_OK=0
      for OLA_BRIDGE_HEALTH_ATTEMPT in 1 2 3 4 5 6 7 8 9 10; do
        if [ "$(/usr/bin/curl -fsS --max-time 2 http://127.0.0.1:18790/healthz 2>/dev/null || true)" = '{"ok":true}' ]; then
          OLA_BRIDGE_HEALTH_OK=1
          break
        fi
        sleep 1
      done
      if [ "$OLA_BRIDGE_HEALTH_OK" -ne 1 ]; then
        echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) ola-webhook: FATAL bridge reload did not become healthy" >> "$LOG"
        exit 1
      fi
      echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) ola-webhook: reloaded installed bridge" >> "$LOG"
    fi
  elif [ "$OLA_BRIDGE_AGENT_CHANGED" -eq 1 ] || [ "$OLA_BRIDGE_RUNTIME_CHANGED" -eq 1 ]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) ola-webhook: refreshed installed files; LaunchAgent remains unloaded pending explicit bootstrap" >> "$LOG"
  fi
fi

# Refresh the Nest event listener plist only after its attended bootstrap has
# installed it. The first bootstrap also owns the private config, credential,
# state directories, and dedicated Python environment; daily pulls must never
# synthesize those prerequisites or silently activate this listener.
NEST_EVENT_AGENT_LABEL="ai.openclaw.nest-event-listener"
NEST_EVENT_AGENT_SRC="$REPO/openclaw/launchagents/$NEST_EVENT_AGENT_LABEL.plist"
NEST_EVENT_AGENT_DST="$HOME/Library/LaunchAgents/$NEST_EVENT_AGENT_LABEL.plist"
if [ -e "$NEST_EVENT_AGENT_DST" ] || [ -L "$NEST_EVENT_AGENT_DST" ]; then
  if [ ! -f "$NEST_EVENT_AGENT_SRC" ] || [ -L "$NEST_EVENT_AGENT_SRC" ] \
    || [ ! -f "$NEST_EVENT_AGENT_DST" ] || [ -L "$NEST_EVENT_AGENT_DST" ]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) nest-events: FATAL installed LaunchAgent is unavailable or unsafe" >> "$LOG"
    exit 1
  fi

  NEST_EVENT_AGENT_CHANGED=0
  if ! cmp -s "$NEST_EVENT_AGENT_SRC" "$NEST_EVENT_AGENT_DST"; then
    atomic_install_managed_file "$NEST_EVENT_AGENT_SRC" "$NEST_EVENT_AGENT_DST" 644
    NEST_EVENT_AGENT_CHANGED=1
  fi

  NEST_EVENT_AGENT_DOMAIN="gui/$(id -u)"
  if launchctl print "$NEST_EVENT_AGENT_DOMAIN/$NEST_EVENT_AGENT_LABEL" >/dev/null 2>&1; then
    if [ "$NEST_EVENT_AGENT_CHANGED" -eq 1 ] || [ "$NEST_EVENT_RUNTIME_CHANGED" -eq 1 ]; then
      launchctl bootout "$NEST_EVENT_AGENT_DOMAIN/$NEST_EVENT_AGENT_LABEL" >/dev/null 2>&1 || true
      # The supervised listener may need a moment to close its private log
      # FIFOs after bootout. launchd can otherwise return a transient EIO even
      # though the old job is already absent. Keep this retry bounded and fail
      # the pull rather than silently leaving the event consumer unloaded.
      NEST_EVENT_RELOAD_OK=0
      for NEST_EVENT_RELOAD_ATTEMPT in 1 2 3 4 5; do
        if launchctl bootstrap "$NEST_EVENT_AGENT_DOMAIN" "$NEST_EVENT_AGENT_DST"; then
          NEST_EVENT_RELOAD_OK=1
          break
        fi
        sleep 1
      done
      if [ "$NEST_EVENT_RELOAD_OK" -ne 1 ]; then
        echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) nest-events: FATAL listener reload failed" >> "$LOG"
        exit 1
      fi
      echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) nest-events: reloaded installed listener" >> "$LOG"
    fi
  elif [ "$NEST_EVENT_AGENT_CHANGED" -eq 1 ] || [ "$NEST_EVENT_RUNTIME_CHANGED" -eq 1 ]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) nest-events: refreshed installed files; LaunchAgent remains unloaded pending explicit bootstrap" >> "$LOG"
  fi
fi

# Refresh the Cabin activity reviewer only after an attended initialization and
# bootstrap have installed its plist.  Daily pulls must never activate image
# analysis or messaging merely because the tracked implementation changed.
NEST_ACTIVITY_AGENT_LABEL="ai.openclaw.nest-activity-reviewer"
NEST_ACTIVITY_AGENT_SRC="$REPO/openclaw/launchagents/$NEST_ACTIVITY_AGENT_LABEL.plist"
NEST_ACTIVITY_AGENT_DST="$HOME/Library/LaunchAgents/$NEST_ACTIVITY_AGENT_LABEL.plist"
if [ -e "$NEST_ACTIVITY_AGENT_DST" ] || [ -L "$NEST_ACTIVITY_AGENT_DST" ]; then
  if [ ! -f "$NEST_ACTIVITY_AGENT_SRC" ] || [ -L "$NEST_ACTIVITY_AGENT_SRC" ] \
    || [ ! -f "$NEST_ACTIVITY_AGENT_DST" ] || [ -L "$NEST_ACTIVITY_AGENT_DST" ]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) nest-activity: FATAL installed LaunchAgent is unavailable or unsafe" >> "$LOG"
    exit 1
  fi

  NEST_ACTIVITY_AGENT_CHANGED=0
  if ! cmp -s "$NEST_ACTIVITY_AGENT_SRC" "$NEST_ACTIVITY_AGENT_DST"; then
    atomic_install_managed_file "$NEST_ACTIVITY_AGENT_SRC" "$NEST_ACTIVITY_AGENT_DST" 644
    NEST_ACTIVITY_AGENT_CHANGED=1
  fi

  NEST_ACTIVITY_AGENT_DOMAIN="gui/$(id -u)"
  if launchctl print "$NEST_ACTIVITY_AGENT_DOMAIN/$NEST_ACTIVITY_AGENT_LABEL" >/dev/null 2>&1; then
    if [ "$NEST_ACTIVITY_AGENT_CHANGED" -eq 1 ] || [ "$NEST_ACTIVITY_RUNTIME_CHANGED" -eq 1 ]; then
      launchctl bootout "$NEST_ACTIVITY_AGENT_DOMAIN/$NEST_ACTIVITY_AGENT_LABEL" >/dev/null 2>&1 || true
      NEST_ACTIVITY_RELOAD_OK=0
      for NEST_ACTIVITY_RELOAD_ATTEMPT in 1 2 3 4 5; do
        if launchctl bootstrap "$NEST_ACTIVITY_AGENT_DOMAIN" "$NEST_ACTIVITY_AGENT_DST"; then
          NEST_ACTIVITY_RELOAD_OK=1
          break
        fi
        sleep 1
      done
      if [ "$NEST_ACTIVITY_RELOAD_OK" -ne 1 ]; then
        echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) nest-activity: FATAL reviewer reload failed" >> "$LOG"
        exit 1
      fi
      echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) nest-activity: reloaded installed reviewer" >> "$LOG"
    fi
  elif [ "$NEST_ACTIVITY_AGENT_CHANGED" -eq 1 ] || [ "$NEST_ACTIVITY_RUNTIME_CHANGED" -eq 1 ]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) nest-activity: refreshed installed files; LaunchAgent remains unloaded pending explicit bootstrap" >> "$LOG"
  fi
fi

# Refresh the ordered Ring-to-Kitchen verifier only after an attended
# initialization, consumer registration, and bootstrap have installed its
# plist. Routine pulls must never create or activate this camera path.
CABIN_ENTRY_AGENT_LABEL="ai.openclaw.cabin-entry-verifier"
CABIN_ENTRY_AGENT_SRC="$REPO/openclaw/launchagents/$CABIN_ENTRY_AGENT_LABEL.plist"
CABIN_ENTRY_AGENT_DST="$HOME/Library/LaunchAgents/$CABIN_ENTRY_AGENT_LABEL.plist"
if [ -e "$CABIN_ENTRY_AGENT_DST" ] || [ -L "$CABIN_ENTRY_AGENT_DST" ]; then
  if [ ! -f "$CABIN_ENTRY_AGENT_SRC" ] || [ -L "$CABIN_ENTRY_AGENT_SRC" ] \
    || [ ! -f "$CABIN_ENTRY_AGENT_DST" ] || [ -L "$CABIN_ENTRY_AGENT_DST" ]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) cabin-entry: FATAL installed LaunchAgent is unavailable or unsafe" >> "$LOG"
    exit 1
  fi

  CABIN_ENTRY_AGENT_CHANGED=0
  if ! cmp -s "$CABIN_ENTRY_AGENT_SRC" "$CABIN_ENTRY_AGENT_DST"; then
    atomic_install_managed_file "$CABIN_ENTRY_AGENT_SRC" "$CABIN_ENTRY_AGENT_DST" 644
    CABIN_ENTRY_AGENT_CHANGED=1
  fi

  CABIN_ENTRY_AGENT_DOMAIN="gui/$(id -u)"
  if launchctl print "$CABIN_ENTRY_AGENT_DOMAIN/$CABIN_ENTRY_AGENT_LABEL" >/dev/null 2>&1; then
    if [ "$CABIN_ENTRY_AGENT_CHANGED" -eq 1 ] || [ "$CABIN_ENTRY_RUNTIME_CHANGED" -eq 1 ]; then
      launchctl bootout "$CABIN_ENTRY_AGENT_DOMAIN/$CABIN_ENTRY_AGENT_LABEL" >/dev/null 2>&1 || true
      CABIN_ENTRY_RELOAD_OK=0
      for CABIN_ENTRY_RELOAD_ATTEMPT in 1 2 3 4 5; do
        if launchctl bootstrap "$CABIN_ENTRY_AGENT_DOMAIN" "$CABIN_ENTRY_AGENT_DST"; then
          CABIN_ENTRY_RELOAD_OK=1
          break
        fi
        sleep 1
      done
      if [ "$CABIN_ENTRY_RELOAD_OK" -ne 1 ]; then
        echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) cabin-entry: FATAL verifier reload failed" >> "$LOG"
        exit 1
      fi
      echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) cabin-entry: reloaded installed verifier" >> "$LOG"
    fi
  elif [ "$CABIN_ENTRY_AGENT_CHANGED" -eq 1 ] || [ "$CABIN_ENTRY_RUNTIME_CHANGED" -eq 1 ]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) cabin-entry: refreshed installed files; LaunchAgent remains unloaded pending explicit bootstrap" >> "$LOG"
  fi
fi

# Refresh home-event LaunchAgents only after an attended install has placed
# each plist. Daily pulls may update or reload an installed shadow service, but
# must never initialize private state or silently bootstrap a new producer.
for HOME_EVENT_AGENT_LABEL in \
  ai.openclaw.home-event-ingest \
  ai.openclaw.home-event-correlator \
  ai.openclaw.home-event-delivery \
  ai.openclaw.home-event-camera \
  ai.openclaw.august-event-adapter \
  ai.openclaw.nest-home-event-bridge \
  ai.openclaw.presence-local-event-adapter; do
  HOME_EVENT_AGENT_SRC="$REPO/openclaw/launchagents/$HOME_EVENT_AGENT_LABEL.plist"
  HOME_EVENT_AGENT_DST="$HOME/Library/LaunchAgents/$HOME_EVENT_AGENT_LABEL.plist"
  if [ -e "$HOME_EVENT_AGENT_DST" ] || [ -L "$HOME_EVENT_AGENT_DST" ]; then
    if [ ! -f "$HOME_EVENT_AGENT_SRC" ] || [ -L "$HOME_EVENT_AGENT_SRC" ] \
      || [ ! -f "$HOME_EVENT_AGENT_DST" ] || [ -L "$HOME_EVENT_AGENT_DST" ]; then
      echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) home-events: FATAL installed LaunchAgent is unavailable or unsafe" >> "$LOG"
      exit 1
    fi
    HOME_EVENT_AGENT_CHANGED=0
    HOME_EVENT_AGENT_CANDIDATE="$HOME_EVENT_AGENT_SRC"
    HOME_EVENT_AGENT_CANDIDATE_TMP=""
    if [ "$HOME_EVENT_AGENT_LABEL" = "ai.openclaw.august-event-adapter" ]; then
      HOME_EVENT_AUGUST_ENABLED=$(
        /usr/libexec/PlistBuddy \
          -c 'Print :EnvironmentVariables:HOME_EVENTS_AUGUST_ENABLED' \
          "$HOME_EVENT_AGENT_DST" 2>/dev/null || printf '0'
      )
      case "$HOME_EVENT_AUGUST_ENABLED" in
        0|1) ;;
        *)
          echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) home-events: FATAL installed August enable flag is invalid" >> "$LOG"
          exit 1
          ;;
      esac
      HOME_EVENT_AGENT_CANDIDATE_TMP=$(mktemp \
        "$HOME/Library/LaunchAgents/.${HOME_EVENT_AGENT_LABEL}.candidate.XXXXXX")
      if ! cp "$HOME_EVENT_AGENT_SRC" "$HOME_EVENT_AGENT_CANDIDATE_TMP" \
        || ! /usr/libexec/PlistBuddy \
          -c "Set :EnvironmentVariables:HOME_EVENTS_AUGUST_ENABLED $HOME_EVENT_AUGUST_ENABLED" \
          "$HOME_EVENT_AGENT_CANDIDATE_TMP" >/dev/null; then
        rm -f "$HOME_EVENT_AGENT_CANDIDATE_TMP"
        echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) home-events: FATAL could not preserve installed August enable flag" >> "$LOG"
        exit 1
      fi
      HOME_EVENT_AGENT_CANDIDATE="$HOME_EVENT_AGENT_CANDIDATE_TMP"
    elif [ "$HOME_EVENT_AGENT_LABEL" = "ai.openclaw.nest-home-event-bridge" ]; then
      HOME_EVENT_NEST_ENABLED=$(
        /usr/libexec/PlistBuddy \
          -c 'Print :EnvironmentVariables:HOME_EVENTS_NEST_ENABLED' \
          "$HOME_EVENT_AGENT_DST" 2>/dev/null || printf '0'
      )
      case "$HOME_EVENT_NEST_ENABLED" in
        0|1) ;;
        *)
          echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) home-events: FATAL installed Nest enable flag is invalid" >> "$LOG"
          exit 1
          ;;
      esac
      HOME_EVENT_AGENT_CANDIDATE_TMP=$(mktemp \
        "$HOME/Library/LaunchAgents/.${HOME_EVENT_AGENT_LABEL}.candidate.XXXXXX")
      if ! cp "$HOME_EVENT_AGENT_SRC" "$HOME_EVENT_AGENT_CANDIDATE_TMP" \
        || ! /usr/libexec/PlistBuddy \
          -c "Set :EnvironmentVariables:HOME_EVENTS_NEST_ENABLED $HOME_EVENT_NEST_ENABLED" \
          "$HOME_EVENT_AGENT_CANDIDATE_TMP" >/dev/null; then
        rm -f "$HOME_EVENT_AGENT_CANDIDATE_TMP"
        echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) home-events: FATAL could not preserve installed Nest enable flag" >> "$LOG"
        exit 1
      fi
      HOME_EVENT_AGENT_CANDIDATE="$HOME_EVENT_AGENT_CANDIDATE_TMP"
    elif [ "$HOME_EVENT_AGENT_LABEL" = "ai.openclaw.presence-local-event-adapter" ]; then
      HOME_EVENT_LOCAL_CABIN_ENABLED=$(
        /usr/libexec/PlistBuddy \
          -c 'Print :EnvironmentVariables:HOME_EVENTS_LOCAL_PRESENCE_CABIN_ENABLED' \
          "$HOME_EVENT_AGENT_DST" 2>/dev/null || printf '0'
      )
      HOME_EVENT_LOCAL_CROSSTOWN_ENABLED=$(
        /usr/libexec/PlistBuddy \
          -c 'Print :EnvironmentVariables:HOME_EVENTS_LOCAL_PRESENCE_CROSSTOWN_ENABLED' \
          "$HOME_EVENT_AGENT_DST" 2>/dev/null || printf '0'
      )
      case "$HOME_EVENT_LOCAL_CABIN_ENABLED:$HOME_EVENT_LOCAL_CROSSTOWN_ENABLED" in
        0:0|0:1|1:0|1:1) ;;
        *)
          echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) home-events: FATAL installed local-presence enable flag is invalid" >> "$LOG"
          exit 1
          ;;
      esac
      HOME_EVENT_AGENT_CANDIDATE_TMP=$(mktemp \
        "$HOME/Library/LaunchAgents/.${HOME_EVENT_AGENT_LABEL}.candidate.XXXXXX")
      if ! cp "$HOME_EVENT_AGENT_SRC" "$HOME_EVENT_AGENT_CANDIDATE_TMP" \
        || ! /usr/libexec/PlistBuddy \
          -c "Set :EnvironmentVariables:HOME_EVENTS_LOCAL_PRESENCE_CABIN_ENABLED $HOME_EVENT_LOCAL_CABIN_ENABLED" \
          "$HOME_EVENT_AGENT_CANDIDATE_TMP" >/dev/null \
        || ! /usr/libexec/PlistBuddy \
          -c "Set :EnvironmentVariables:HOME_EVENTS_LOCAL_PRESENCE_CROSSTOWN_ENABLED $HOME_EVENT_LOCAL_CROSSTOWN_ENABLED" \
          "$HOME_EVENT_AGENT_CANDIDATE_TMP" >/dev/null; then
        rm -f "$HOME_EVENT_AGENT_CANDIDATE_TMP"
        echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) home-events: FATAL could not preserve installed local-presence enable flags" >> "$LOG"
        exit 1
      fi
      HOME_EVENT_AGENT_CANDIDATE="$HOME_EVENT_AGENT_CANDIDATE_TMP"
    fi
    if ! cmp -s "$HOME_EVENT_AGENT_CANDIDATE" "$HOME_EVENT_AGENT_DST"; then
      atomic_install_managed_file "$HOME_EVENT_AGENT_CANDIDATE" "$HOME_EVENT_AGENT_DST" 644
      HOME_EVENT_AGENT_CHANGED=1
    fi
    [ -z "$HOME_EVENT_AGENT_CANDIDATE_TMP" ] \
      || rm -f "$HOME_EVENT_AGENT_CANDIDATE_TMP"
    HOME_EVENT_AGENT_DOMAIN="gui/$(id -u)"
    case "$HOME_EVENT_AGENT_LABEL" in
      ai.openclaw.home-event-ingest)
        HOME_EVENT_RUNTIME_CHANGED="$HOME_EVENT_INGEST_CHANGED"
        ;;
      ai.openclaw.home-event-correlator)
        HOME_EVENT_RUNTIME_CHANGED="$HOME_EVENT_CORRELATOR_CHANGED"
        ;;
      ai.openclaw.home-event-delivery)
        HOME_EVENT_RUNTIME_CHANGED="$HOME_EVENT_DELIVERY_CHANGED"
        ;;
      ai.openclaw.home-event-camera)
        HOME_EVENT_RUNTIME_CHANGED="$HOME_EVENT_CAMERA_CHANGED"
        ;;
      ai.openclaw.august-event-adapter)
        HOME_EVENT_RUNTIME_CHANGED="$HOME_EVENT_AUGUST_CHANGED"
        ;;
      ai.openclaw.nest-home-event-bridge)
        HOME_EVENT_RUNTIME_CHANGED="$HOME_EVENT_NEST_CHANGED"
        ;;
      ai.openclaw.presence-local-event-adapter)
        HOME_EVENT_RUNTIME_CHANGED="$HOME_EVENT_LOCAL_PRESENCE_CHANGED"
        ;;
    esac
    if launchctl print "$HOME_EVENT_AGENT_DOMAIN/$HOME_EVENT_AGENT_LABEL" >/dev/null 2>&1; then
      if [ "$HOME_EVENT_AGENT_CHANGED" -eq 1 ] || [ "$HOME_EVENT_RUNTIME_CHANGED" -eq 1 ]; then
        launchctl bootout "$HOME_EVENT_AGENT_DOMAIN/$HOME_EVENT_AGENT_LABEL" >/dev/null 2>&1 || true
        HOME_EVENT_RELOAD_OK=0
        for HOME_EVENT_RELOAD_ATTEMPT in 1 2 3 4 5; do
          if launchctl bootstrap "$HOME_EVENT_AGENT_DOMAIN" "$HOME_EVENT_AGENT_DST"; then
            HOME_EVENT_RELOAD_OK=1
            break
          fi
          sleep 1
        done
        if [ "$HOME_EVENT_RELOAD_OK" -ne 1 ]; then
          echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) home-events: FATAL $HOME_EVENT_AGENT_LABEL reload failed" >> "$LOG"
          exit 1
        fi
        echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) home-events: reloaded $HOME_EVENT_AGENT_LABEL" >> "$LOG"
      fi
    elif [ "$HOME_EVENT_AGENT_CHANGED" -eq 1 ] || [ "$HOME_EVENT_RUNTIME_CHANGED" -eq 1 ]; then
      echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) home-events: refreshed $HOME_EVENT_AGENT_LABEL; remains unloaded pending explicit bootstrap" >> "$LOG"
    fi
  fi
done

# Symlink top-level bin/ scripts into /opt/homebrew/bin/ so they track dotfiles HEAD.
# Matches the pattern set by install.sh for hue/nest/speaker — replaces any stale
# regular-file copies so a fix committed to bin/<cli> propagates on the next pull.
TOP_BIN_SRC="$REPO/bin"
TOP_BIN_DST="/opt/homebrew/bin"
TOP_BIN_LINKED=0
if [ -d "$TOP_BIN_SRC" ]; then
  for script in "$TOP_BIN_SRC"/*; do
    [ -f "$script" ] || continue
    [ -x "$script" ] || continue
    fname=$(basename "$script")
    # Skip if already pointing at the right place
    if [ -L "$TOP_BIN_DST/$fname" ] && [ "$(readlink "$TOP_BIN_DST/$fname")" = "$script" ]; then
      continue
    fi
    ln -sfn "$script" "$TOP_BIN_DST/$fname"
    TOP_BIN_LINKED=$((TOP_BIN_LINKED + 1))
  done
fi
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) top-bin: linked $TOP_BIN_LINKED to $TOP_BIN_DST" >> "$LOG"

# Smoke test under the final PATH exported by OpenClawGateway.app. Keep this
# literal synchronized with the app wrapper: it is the last PATH authority in
# both the generated service-env and recovery-plist launch chains.
GATEWAY_RUNTIME_PATH="$HOME/.openclaw/bin:/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PATH="$GATEWAY_RUNTIME_PATH"
SMOKE_FAIL=0
DEPLOYMENT_SMOKE_FAILED=0
for cmd in $DEPLOYED_WRAPPERS; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) WARN: $cmd not on PATH" >> "$LOG"
    SMOKE_FAIL=$((SMOKE_FAIL + 1))
  fi
done
# Also check external CLIs expected on PATH
for cmd in hue speaker goplaces; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) WARN: $cmd not on PATH" >> "$LOG"
    SMOKE_FAIL=$((SMOKE_FAIL + 1))
  fi
done

if [ $SMOKE_FAIL -gt 0 ]; then
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) wrappers: smoke test FAILED ($SMOKE_FAIL failures)" >> "$LOG"
  DEPLOYMENT_SMOKE_FAILED=1
else
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) wrappers: smoke test PASSED" >> "$LOG"
fi

# Deploy CrisisMode config and check plugins
CRISISMODE_SRC="$REPO/openclaw/crisismode"
CRISISMODE_DST="$HOME/.crisismode"
if [ -d "$CRISISMODE_SRC" ]; then
  mkdir -p "$CRISISMODE_DST"
  cp "$CRISISMODE_SRC/crisismode.yaml" "$CRISISMODE_DST/crisismode.yaml"
  # Deploy custom check plugins
  if [ -d "$CRISISMODE_SRC/checks" ]; then
    mkdir -p "$CRISISMODE_DST/checks"
    CHECKS_DEPLOYED=0
    for check_dir in "$CRISISMODE_SRC/checks"/*/; do
      check_name=$(basename "$check_dir")
      rm -rf "$CRISISMODE_DST/checks/$check_name"
      cp -R "$check_dir" "$CRISISMODE_DST/checks/$check_name"
      chmod +x "$CRISISMODE_DST/checks/$check_name/check.sh" 2>/dev/null
      CHECKS_DEPLOYED=$((CHECKS_DEPLOYED + 1))
    done
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) crisismode: deployed config + $CHECKS_DEPLOYED check plugins" >> "$LOG"
  else
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) crisismode: deployed config to $CRISISMODE_DST" >> "$LOG"
  fi
fi

# Deploy workspace files (SOUL.md, TOOLS.md, etc.)
WORKSPACE_SRC="$REPO/openclaw/workspace"
WORKSPACE_DST="$HOME/.openclaw/workspace"
if [ -d "$WORKSPACE_SRC" ] && [ -d "$WORKSPACE_DST" ]; then
  for f in TOOLS.md HEARTBEAT.md; do
    if [ -f "$WORKSPACE_SRC/$f" ]; then
      # Remove symlinks first — cp fails if dst is a symlink to src
      [ -L "$WORKSPACE_DST/$f" ] && rm -f "$WORKSPACE_DST/$f"
      cp "$WORKSPACE_SRC/$f" "$WORKSPACE_DST/$f"
    fi
  done
  # SOUL.md has real values on Mini (not placeholders) — don't overwrite
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) workspace: deployed TOOLS.md, HEARTBEAT.md" >> "$LOG"

  # Deploy workspace scripts (presence-detect, grocery-reorder, etc.)
  SCRIPTS_SRC="$REPO/openclaw/workspace/scripts"
  SCRIPTS_DST="$WORKSPACE_DST/scripts"
  if [ -d "$SCRIPTS_SRC" ] && [ -d "$SCRIPTS_DST" ]; then
    WS_SCRIPTS_DEPLOYED=0
    for script in "$SCRIPTS_SRC"/*; do
      [ -f "$script" ] || continue
      fname=$(basename "$script")
      if [ "$fname" = "presence-detect.sh" ] && [ "$IS_GATEWAY_HOST" -eq 1 ]; then
        if ! openclaw_presence_scanner_has_strict_deployment_contract "$script"; then
          echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) workspace: WARN tracked presence scanner lacks the strict binding contract; preserved prior scanner" >> "$LOG"
          continue
        fi
        PRESENCE_BINDING_CONFIG="$HOME/.openclaw/presence-devices.json"
        if [ ! -e "$PRESENCE_BINDING_CONFIG" ] \
            && [ ! -L "$PRESENCE_BINDING_CONFIG" ]; then
          echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) workspace: INFO Cabin strict presence enrollment pending; preserved legacy scanner" >> "$LOG"
          continue
        fi
        if ! PRESENCE_CANDIDATE=$(openclaw_stage_presence_scanner "$script"); then
          echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) workspace: WARN tracked presence scanner could not be staged safely; preserved prior scanner" >> "$LOG"
          continue
        fi
        if ! openclaw_presence_scanner_has_strict_deployment_contract "$PRESENCE_CANDIDATE"; then
          /bin/rm -f "$PRESENCE_CANDIDATE"
          echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) workspace: WARN staged presence scanner lacks the strict binding contract; preserved prior scanner" >> "$LOG"
          continue
        fi
        if ! PRESENCE_DEVICE_CONFIG="$PRESENCE_BINDING_CONFIG" \
             /bin/bash "$PRESENCE_CANDIDATE" validate-config cabin >/dev/null 2>&1; then
          /bin/rm -f "$PRESENCE_CANDIDATE"
          echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) workspace: WARN Cabin strict presence bindings invalid or insecure; preserved prior scanner" >> "$LOG"
          continue
        fi
        if ! PRESENCE_SCANNER_HASH=$(openclaw_presence_scanner_sha256 "$PRESENCE_CANDIDATE"); then
          /bin/rm -f "$PRESENCE_CANDIDATE"
          echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) workspace: WARN tracked presence scanner hash is unavailable; preserved prior scanner" >> "$LOG"
          continue
        fi
        PRESENCE_SCANNER_APPROVAL="$HOME/.openclaw/presence-scanner-approved.sha256"
        PRESENCE_APPROVAL_STATUS=0
        if openclaw_presence_scanner_approval_status \
            "$PRESENCE_SCANNER_HASH" "$PRESENCE_SCANNER_APPROVAL"; then
          :
        else
          PRESENCE_APPROVAL_STATUS=$?
        fi
        if [ "$PRESENCE_APPROVAL_STATUS" -ne 0 ]; then
          /bin/rm -f "$PRESENCE_CANDIDATE"
          if [ "$PRESENCE_APPROVAL_STATUS" -eq 20 ]; then
            echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) workspace: INFO Cabin strict presence scanner awaits exact canary approval; preserved legacy scanner" >> "$LOG"
          else
            echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) workspace: WARN Cabin presence scanner approval is invalid; preserved prior scanner" >> "$LOG"
          fi
          continue
        fi
        if ! openclaw_atomic_install_presence_scanner \
            "$PRESENCE_CANDIDATE" "$SCRIPTS_DST/$fname" "$PRESENCE_SCANNER_HASH"; then
          /bin/rm -f "$PRESENCE_CANDIDATE"
          echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) workspace: FATAL approved Cabin presence scanner failed exact-byte installation; inspect the last atomic runtime before retry" >> "$LOG"
          exit 1
        fi
        /bin/rm -f "$PRESENCE_CANDIDATE"
        WS_SCRIPTS_DEPLOYED=$((WS_SCRIPTS_DEPLOYED + 1))
        continue
      fi
      [ -L "$SCRIPTS_DST/$fname" ] && rm -f "$SCRIPTS_DST/$fname"
      atomic_install_executable "$script" "$SCRIPTS_DST/$fname"
      WS_SCRIPTS_DEPLOYED=$((WS_SCRIPTS_DEPLOYED + 1))
    done
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) workspace: deployed $WS_SCRIPTS_DEPLOYED scripts to $SCRIPTS_DST" >> "$LOG"
  fi
fi

# Deploy updated cron job definitions (preserves runtime state)
if [ -x "$REPO/openclaw/sync-cron-jobs.sh" ]; then
  SYNC_OUT=$("$REPO/openclaw/sync-cron-jobs.sh" deploy 2>&1)
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) sync-cron-jobs: $SYNC_OUT" >> "$LOG"
fi

# Self-update: keep the deployed copy of this script in sync with repo HEAD.
# Without this, fixes to dotfiles-pull.command itself (and anything its later
# blocks deploy) never reach the Mini — launchd runs the DEPLOYED copy, and
# the wrapper-deploy loop above skips *.command. Use cp+mv for atomic replace
# so the still-running bash process keeps reading the old inode.
SELF_SRC="$REPO/openclaw/bin/dotfiles-pull.command"
SELF_DST="$HOME/.openclaw/bin/dotfiles-pull.command"
if [ -f "$SELF_SRC" ] && ! cmp -s "$SELF_SRC" "$SELF_DST"; then
  cp "$SELF_SRC" "$SELF_DST.new"
  chmod +x "$SELF_DST.new"
  mv "$SELF_DST.new" "$SELF_DST"
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) self: updated $SELF_DST from repo" >> "$LOG"
fi

# A running gateway retains the old process environment. Reload only when the
# final app wrapper changed, then require the replacement process to become
# healthy before inspecting its live skill catalog.
GATEWAY_READY=1
if [ "$GATEWAY_RESTART_REQUIRED" -ne 0 ]; then
  GATEWAY_LABEL="ai.openclaw.gateway"
  GATEWAY_DOMAIN="gui/$(/usr/bin/id -u)"
  GATEWAY_READY=0
  GATEWAY_PID_BEFORE=$(/bin/launchctl print "$GATEWAY_DOMAIN/$GATEWAY_LABEL" 2>/dev/null \
    | /usr/bin/awk '/^[[:space:]]*pid = [0-9]+$/ { print $3; exit }' || true)
  if /bin/launchctl kickstart -k "$GATEWAY_DOMAIN/$GATEWAY_LABEL"; then
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
      GATEWAY_PID_AFTER=$(/bin/launchctl print "$GATEWAY_DOMAIN/$GATEWAY_LABEL" 2>/dev/null \
        | /usr/bin/awk '/^[[:space:]]*pid = [0-9]+$/ { print $3; exit }' || true)
      if [ -n "$GATEWAY_PID_AFTER" ] && [ "$GATEWAY_PID_AFTER" != "$GATEWAY_PID_BEFORE" ] \
        && GATEWAY_HEALTH=$(/usr/bin/curl -fsS --max-time 2 http://127.0.0.1:18789/health 2>/dev/null); then
        case "$GATEWAY_HEALTH" in
          *'"ok":true'*)
            GATEWAY_READY=1
            break
            ;;
        esac
      fi
      /bin/sleep 1
    done
    if [ "$GATEWAY_READY" -ne 1 ]; then
      echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) WARN: gateway app wrapper changed but replacement did not become healthy" >> "$LOG"
      DEPLOYMENT_SMOKE_FAILED=1
    fi
  else
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) WARN: gateway app wrapper changed but restart failed" >> "$LOG"
    DEPLOYMENT_SMOKE_FAILED=1
  fi
fi

# Query the active Gateway explicitly. `openclaw skills check` can silently
# fall back to a local-process report, which does not prove that the service
# inherited the deployed PATH. The RPC is evaluated by the active Gateway and
# therefore exercises its own PATH-based `requires.bins` checks.
GATEWAY_REQUIRED_SKILLS=(opentable restaurant-book restaurant-snipe resy)
GATEWAY_SECRETS_CACHE="$HOME/.openclaw/.secrets-cache"

read_active_gateway_skills() {
  local cache_owner_mode

  [ -f "$GATEWAY_SECRETS_CACHE" ] && [ ! -L "$GATEWAY_SECRETS_CACHE" ] || return 1
  cache_owner_mode=$(/usr/bin/stat -f '%u %Lp' "$GATEWAY_SECRETS_CACHE" 2>/dev/null) || return 1
  [ "$cache_owner_mode" = "$(/usr/bin/id -u) 600" ] || return 1

  (
    set +a
    # The generated cache contains plain shell assignments. Export only the
    # Gateway token to the read-only RPC child, not the other cached values.
    # shellcheck disable=SC1090
    if ! . "$GATEWAY_SECRETS_CACHE" >/dev/null 2>&1; then
      exit 1
    fi
    [ -n "${OPENCLAW_GATEWAY_TOKEN:-}" ] || exit 1
    export OPENCLAW_GATEWAY_TOKEN
    /opt/homebrew/bin/openclaw gateway call skills.status --json --timeout 5000
  )
}

GATEWAY_SKILLS_READY=0
GATEWAY_SKILLS_FAILURE="gateway RPC unavailable"
if [ "$GATEWAY_READY" -eq 1 ]; then
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if GATEWAY_SKILLS_JSON=$(read_active_gateway_skills 2>/dev/null); then
      if GATEWAY_MISSING_SKILLS=$(printf '%s' "$GATEWAY_SKILLS_JSON" \
        | openclaw_gateway_missing_skills_from_status "${GATEWAY_REQUIRED_SKILLS[@]}"); then
        if [ -z "$GATEWAY_MISSING_SKILLS" ]; then
          GATEWAY_SKILLS_READY=1
          break
        fi
        GATEWAY_SKILLS_FAILURE="not eligible/model-visible: $GATEWAY_MISSING_SKILLS"
      else
        GATEWAY_SKILLS_FAILURE="invalid skills.status response"
      fi
    fi
    /bin/sleep 1
  done
fi

if [ "$GATEWAY_SKILLS_READY" -eq 1 ]; then
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) gateway: active restaurant skill catalog passed" >> "$LOG"
  if [ "$GATEWAY_RESTART_REQUIRED" -ne 0 ]; then
    GATEWAY_HASH_STATE_DIR=$(dirname "$GATEWAY_WRAPPER_HASH_STATE")
    mkdir -p "$GATEWAY_HASH_STATE_DIR"
    GATEWAY_HASH_STATE_TMP=$(mktemp "$GATEWAY_HASH_STATE_DIR/.gateway-wrapper.sha256.XXXXXX")
    if printf '%s\n' "$GATEWAY_WRAPPER_HASH" > "$GATEWAY_HASH_STATE_TMP" \
      && chmod 600 "$GATEWAY_HASH_STATE_TMP" \
      && mv -f "$GATEWAY_HASH_STATE_TMP" "$GATEWAY_WRAPPER_HASH_STATE"; then
      echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) gateway: activated changed app wrapper after live skill verification" >> "$LOG"
    else
      rm -f "$GATEWAY_HASH_STATE_TMP"
      echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) WARN: gateway live skills passed but activated-wrapper state could not be recorded" >> "$LOG"
      DEPLOYMENT_SMOKE_FAILED=1
    fi
  fi
else
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) WARN: active gateway restaurant skill catalog failed: $GATEWAY_SKILLS_FAILURE" >> "$LOG"
  DEPLOYMENT_SMOKE_FAILED=1
fi

# Finish deployment and self-update before surfacing a smoke failure, otherwise
# a stale deployed copy could never install the fix for its own failing check.
if [ "$DEPLOYMENT_SMOKE_FAILED" -ne 0 ]; then
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) ABORT: deployment completed with a failed gateway wrapper/skill smoke test" >> "$LOG"
  exit 1
fi

# Close this Terminal window after completion
osascript -e 'tell application "Terminal" to close (every window whose name contains "dotfiles-pull")' &>/dev/null &
