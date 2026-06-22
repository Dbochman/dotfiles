#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: launchagent-snooze.sh <pause|status|resume>

Temporarily unload browser-based Mac Mini LaunchAgents and restore only the
jobs that were loaded before the snooze.
EOF
}

action="${1:-}"
case "$action" in
  pause|status|resume) ;;
  *)
    usage >&2
    exit 2
    ;;
esac

host="${MAC_MINI_HOST:-mac-mini}"
labels=(
  ai.openclaw.boa-keepalive
  ai.openclaw.boa-browser-heartbeat
  com.openclaw.cielo-refresh
)

ssh -o BatchMode=yes -o ConnectTimeout=10 "$host" \
  bash -s -- "$action" "${labels[@]}" <<'REMOTE'
set -euo pipefail

action="$1"
shift
labels=("$@")
uid="$(id -u)"
domain="gui/$uid"
state_dir="$HOME/.openclaw/state"
state_file="$state_dir/mac-mini-automation-snooze.tsv"

is_loaded() {
  launchctl print "$domain/$1" >/dev/null 2>&1
}

is_disabled() {
  launchctl print-disabled "$domain" 2>/dev/null \
    | grep -Fq '"'"$1"'" => disabled'
}

show_status() {
  if [[ -f "$state_file" ]]; then
    created_at="$(awk -F '\t' '$1 == "created_at" { print $2; exit }' "$state_file")"
    echo "snooze: active${created_at:+ since $created_at}"
  else
    echo "snooze: inactive"
  fi

  for label in "${labels[@]}"; do
    loaded="unloaded"
    override="enabled"
    is_loaded "$label" && loaded="loaded"
    is_disabled "$label" && override="disabled"
    printf '%s: %s, %s\n' "$label" "$loaded" "$override"
  done
}

case "$action" in
  pause)
    mkdir -p "$state_dir"
    chmod 700 "$state_dir"

    if [[ -f "$state_file" ]]; then
      echo "Snooze is already active; preserving its original restore set."
      show_status
      exit 0
    fi

    tmp_file="$(mktemp "$state_file.XXXXXX")"
    trap 'rm -f "$tmp_file"' EXIT
    printf 'version\t1\ncreated_at\t%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" > "$tmp_file"

    for label in "${labels[@]}"; do
      if ! is_loaded "$label"; then
        echo "$label: already unloaded; leaving its override unchanged"
        continue
      fi

      launchctl disable "$domain/$label"
      if launchctl bootout "$domain/$label"; then
        printf 'label\t%s\n' "$label" >> "$tmp_file"
        echo "$label: paused"
      else
        launchctl enable "$domain/$label" || true
        echo "$label: failed to unload; rolled back its disabled override" >&2
        exit 1
      fi
    done

    chmod 600 "$tmp_file"
    mv "$tmp_file" "$state_file"
    trap - EXIT
    show_status
    ;;

  status)
    show_status
    ;;

  resume)
    if [[ ! -f "$state_file" ]]; then
      echo "No active snooze state; nothing to restore."
      show_status
      exit 0
    fi

    failed=0
    while IFS=$'\t' read -r key label; do
      [[ "$key" == "label" ]] || continue
      plist="$HOME/Library/LaunchAgents/$label.plist"

      if [[ ! -f "$plist" ]]; then
        echo "$label: plist missing at $plist" >&2
        failed=1
        continue
      fi

      launchctl enable "$domain/$label"
      if is_loaded "$label" || launchctl bootstrap "$domain" "$plist"; then
        echo "$label: restored"
      else
        launchctl disable "$domain/$label" || true
        echo "$label: restore failed; kept disabled" >&2
        failed=1
      fi
    done < "$state_file"

    if (( failed != 0 )); then
      echo "Snooze state retained because one or more jobs failed to restore." >&2
      exit 1
    fi

    rm -f "$state_file"
    echo "Snooze ended."
    show_status
    ;;
esac
REMOTE
