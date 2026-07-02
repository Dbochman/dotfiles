#!/usr/bin/env bash
# Dotfiles install script
# Run on new machines: git clone <repo> ~/dotfiles && ~/dotfiles/install.sh
#
# Usage: ./install.sh [options]
#
# Options:
#   -n, --dry-run    Preview changes without making them
#   -f, --force      Replace conflicts without prompting (runtime-owned files remain protected)
#   -v, --verbose    Detailed output
#   -q, --quiet      Minimal output
#   -h, --help       Show this help message

# Note: Not using set -e due to complex control flow with subshells and prompts
set -o pipefail

DOTFILES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_DIR="$DOTFILES_DIR/.backup"
MAX_BACKUPS=10

# State
DRY_RUN=false
FORCE=false
VERBOSE=false
QUIET=false
CURRENT_BACKUP_DIR=""
ITEMS_LINKED=0
ITEMS_SKIPPED=0
ITEMS_BACKED_UP=0
BACKED_UP_ITEMS=()
EXIT_CODE=0

# Colors (disabled if not TTY)
if [[ -t 1 ]]; then
  RED='\033[0;31m'
  GREEN='\033[0;32m'
  YELLOW='\033[0;33m'
  BLUE='\033[0;34m'
  NC='\033[0m'
else
  RED=''
  GREEN=''
  YELLOW=''
  BLUE=''
  NC=''
fi

# === Portability Shims ===

resolve_path() {
  local path="$1"
  if command -v greadlink &>/dev/null; then
    greadlink -f "$path"
    return
  fi
  if readlink -f / &>/dev/null 2>&1; then
    readlink -f "$path"
    return
  fi
  local result=""
  if [[ "$path" = /* ]]; then
    result=""
  else
    result="$(pwd)"
  fi
  local oldIFS="$IFS"
  IFS=/
  for component in $path; do
    IFS="$oldIFS"
    [[ -z "$component" ]] && continue
    if [[ "$component" = "." ]]; then
      continue
    elif [[ "$component" = ".." ]]; then
      result="${result%/*}"
    else
      result="$result/$component"
      if [[ -L "$result" ]]; then
        local link_target
        link_target="$(readlink "$result")"
        if [[ "$link_target" = /* ]]; then
          result="$link_target"
        else
          result="${result%/*}/$link_target"
        fi
      fi
    fi
  done
  IFS="$oldIFS"
  echo "${result:-/}"
}

get_symlink_target() {
  readlink "$1"
}

get_file_type() {
  local path="$1"
  if [[ -L "$path" ]]; then
    echo "symlink"
  elif [[ -d "$path" ]]; then
    echo "directory"
  elif [[ -f "$path" ]]; then
    echo "file"
  else
    echo "none"
  fi
}

is_tty() {
  [[ -t 0 ]]
}

# === Logging ===

log() {
  [[ "$QUIET" = true ]] && return
  echo -e "$1"
}

log_verbose() {
  [[ "$VERBOSE" = true ]] && echo -e "$1"
  return 0
}

log_warn() {
  echo -e "${YELLOW}WARNING: $1${NC}" >&2
}

log_error() {
  echo -e "${RED}ERROR: $1${NC}" >&2
}

# === Backup Functions ===

create_backup_dir() {
  [[ -n "$CURRENT_BACKUP_DIR" ]] && return 0

  local timestamp
  timestamp=$(date +%Y-%m-%d-%H%M%S)
  CURRENT_BACKUP_DIR="$BACKUP_DIR/$timestamp"

  if [[ "$DRY_RUN" = true ]]; then
    log_verbose "  [dry-run] Would create backup: $CURRENT_BACKUP_DIR"
    return 0
  fi

  if ! mkdir -p "$CURRENT_BACKUP_DIR"; then
    log_error "Could not create backup directory: $CURRENT_BACKUP_DIR"
    CURRENT_BACKUP_DIR=""
    EXIT_CODE=1
    return 1
  fi
  if ! chmod 700 "$BACKUP_DIR" "$CURRENT_BACKUP_DIR"; then
    log_error "Could not secure backup directory: $CURRENT_BACKUP_DIR"
    CURRENT_BACKUP_DIR=""
    EXIT_CODE=1
    return 1
  fi
  return 0
}

backup_item() {
  local src="$1"
  [[ -e "$src" || -L "$src" ]] || return 0

  create_backup_dir || return 1

  local relative_path="${src#$HOME/}"
  local dest="$CURRENT_BACKUP_DIR/$relative_path"

  if [[ "$DRY_RUN" = true ]]; then
    log_verbose "  [dry-run] Would backup: $src"
    return 0
  fi

  local previous_umask
  previous_umask=$(umask)
  umask 077
  if ! mkdir -p "$(dirname "$dest")"; then
    umask "$previous_umask"
    log_error "Could not create backup parent for: $src"
    EXIT_CODE=1
    return 1
  fi

  local copy_status=0
  if [[ -L "$src" ]]; then
    cp -P "$src" "$dest" || copy_status=$?
  elif [[ -d "$src" ]]; then
    cp -R "$src" "$dest" || copy_status=$?
  else
    cp "$src" "$dest" || copy_status=$?
  fi
  umask "$previous_umask"

  if [[ "$copy_status" -ne 0 ]]; then
    log_error "Could not back up before replacement: $src"
    EXIT_CODE=1
    return 1
  fi

  BACKED_UP_ITEMS+=("$src")
  ITEMS_BACKED_UP=$((ITEMS_BACKED_UP + 1))
  return 0
}

prune_old_backups() {
  [[ "$DRY_RUN" = true ]] && return
  [[ -d "$BACKUP_DIR" ]] || return 0

  local count
  count=$(find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')

  if [[ "$count" -gt "$MAX_BACKUPS" ]]; then
    local to_remove=$((count - MAX_BACKUPS))
    find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d | sort | head -n "$to_remove" | while read -r dir; do
      rm -rf "$dir"
    done
  fi
}

write_manifest() {
  [[ "$DRY_RUN" = true ]] && return
  [[ -z "$CURRENT_BACKUP_DIR" ]] && return
  [[ ${#BACKED_UP_ITEMS[@]} -eq 0 ]] && return

  local manifest="$CURRENT_BACKUP_DIR/manifest.json"
  local hostname
  hostname=$(hostname -s 2>/dev/null || echo "unknown")
  local timestamp
  timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  {
    echo "{"
    echo "  \"timestamp\": \"$timestamp\","
    echo "  \"operation\": \"install\","
    echo "  \"hostname\": \"$hostname\","
    echo "  \"items_backed_up\": ["
    local first=true
    for item in "${BACKED_UP_ITEMS[@]}"; do
      if [[ "$first" = true ]]; then
        first=false
      else
        echo ","
      fi
      local item_type
      item_type=$(get_file_type "$item")
      printf "    {\"path\": \"%s\", \"type\": \"%s\"}" "$item" "$item_type"
    done
    echo ""
    echo "  ]"
    echo "}"
  } > "$manifest"
}

# === Conflict Handling ===

prompt_conflict() {
  local path="$1"
  local conflict_type="$2"

  if ! is_tty; then
    log_warn "Conflict (non-interactive): $path - skipping"
    ((ITEMS_SKIPPED++))
    EXIT_CODE=2
    return 1
  fi

  echo ""
  echo -e "${YELLOW}Conflict: $path exists as $conflict_type${NC}"
  echo "  [r]eplace (backup existing)"
  echo "  [k]eep existing (skip)"
  echo "  [d]iff (show differences)"
  echo "  [q]uit"

  while true; do
    read -rp "Choice [r/k/d/q]: " choice
    case "$choice" in
      r|R)
        return 0
        ;;
      k|K)
        ((ITEMS_SKIPPED++))
        return 1
        ;;
      d|D)
        echo "--- Current: $path"
        if [[ -L "$path" ]]; then
          echo "Symlink -> $(get_symlink_target "$path")"
        elif [[ -d "$path" ]]; then
          ls -la "$path" | head -20
        else
          head -20 "$path"
        fi
        echo ""
        ;;
      q|Q)
        log "Aborted by user"
        exit 1
        ;;
      *)
        echo "Invalid choice"
        ;;
    esac
  done
}

# === Linking Functions ===

link_file() {
  local src="$1"
  local dst="$2"

  [[ -e "$src" ]] || {
    log_verbose "  Source not found: $src"
    return 0
  }

  local expected_target
  expected_target=$(resolve_path "$src")

  # Check current state
  if [[ -L "$dst" ]]; then
    local current_target
    current_target=$(get_symlink_target "$dst")
    if [[ "$current_target" = /* ]]; then
      current_target=$(resolve_path "$current_target")
    else
      current_target=$(resolve_path "$(dirname "$dst")/$current_target")
    fi

    if [[ "$current_target" = "$expected_target" ]]; then
      log_verbose "  Already linked: $dst"
      return 0
    fi

    # Wrong target
    if [[ ! -e "$dst" ]]; then
      # Broken symlink - auto-replace
      log_verbose "  Replacing broken symlink: $dst"
      if [[ "$DRY_RUN" != true ]]; then
        rm "$dst"
      fi
    elif [[ "$FORCE" = true ]]; then
      backup_item "$dst" || return 1
      if [[ "$DRY_RUN" != true ]]; then
        rm "$dst"
      fi
    else
      if ! prompt_conflict "$dst" "symlink to $current_target"; then
        return 0
      fi
      backup_item "$dst" || return 1
      if [[ "$DRY_RUN" != true ]]; then
        rm "$dst"
      fi
    fi
  elif [[ -e "$dst" ]]; then
    # Regular file or directory
    local file_type
    file_type=$(get_file_type "$dst")

    if [[ "$FORCE" = true ]]; then
      backup_item "$dst" || return 1
      if [[ "$DRY_RUN" != true ]]; then
        rm -rf "$dst"
      fi
    else
      if ! prompt_conflict "$dst" "$file_type"; then
        return 0
      fi
      backup_item "$dst" || return 1
      if [[ "$DRY_RUN" != true ]]; then
        rm -rf "$dst"
      fi
    fi
  fi

  # Create parent directory
  if [[ "$DRY_RUN" != true ]]; then
    mkdir -p "$(dirname "$dst")"
  fi

  # Create symlink
  if [[ "$DRY_RUN" = true ]]; then
    log "  [dry-run] Would link: $dst -> $src"
  else
    ln -s "$src" "$dst"
    log "  Linked: $dst"
  fi

  ((ITEMS_LINKED++))
}

discard_staging_root() {
  local staging_root="$1"

  if [[ -d "$staging_root" ]] && ! rm -rf "$staging_root"; then
    log_warn "Could not remove installer staging directory: $staging_root"
    EXIT_CODE=1
    return 1
  fi

  return 0
}

swap_staged_path() {
  local staging_root="$1"
  local staged="$2"
  local dst="$3"
  local label="$4"
  local original="$staging_root/original"
  local had_original=false

  if [[ -e "$dst" || -L "$dst" ]]; then
    if ! mv "$dst" "$original"; then
      log_error "Could not move existing $label aside: $dst"
      EXIT_CODE=1
      discard_staging_root "$staging_root" || true
      return 1
    fi
    had_original=true
  fi

  if ! mv "$staged" "$dst"; then
    log_error "Could not install staged $label: $dst"
    EXIT_CODE=1
    if [[ "$had_original" = true ]]; then
      if ! mv "$original" "$dst"; then
        log_error "Could not restore original $label; it remains at: $original"
        return 1
      fi
    fi
    discard_staging_root "$staging_root" || true
    return 1
  fi

  discard_staging_root "$staging_root" || return 1
  return 0
}

stage_symlink_replacement() {
  local src="$1"
  local dst="$2"
  local label="$3"
  local parent
  parent=$(dirname "$dst")

  if ! mkdir -p "$parent"; then
    log_error "Could not create parent directory for $label: $parent"
    EXIT_CODE=1
    return 1
  fi

  local staging_root
  if ! staging_root=$(mktemp -d "$parent/.codex-install.XXXXXX"); then
    log_error "Could not create staging directory for $label: $dst"
    EXIT_CODE=1
    return 1
  fi

  local staged="$staging_root/replacement"
  if ! ln -s "$src" "$staged"; then
    log_error "Could not stage symlink for $label: $dst"
    EXIT_CODE=1
    discard_staging_root "$staging_root" || true
    return 1
  fi

  swap_staged_path "$staging_root" "$staged" "$dst" "$label"
}

link_skill_directory() {
  local src="${1%/}"
  local dst="$2"

  # Older installs copied skills into place. If the copy is identical, migrate
  # it without turning a harmless stale deployment into an interactive conflict.
  if [[ -d "$dst" && ! -L "$dst" ]] && diff -qr "$src" "$dst" &>/dev/null; then
    if [[ "$DRY_RUN" = true ]]; then
      log "  [dry-run] Would replace identical skill copy with symlink: $dst"
    else
      stage_symlink_replacement "$src" "$dst" "skill symlink" || return 1
      log "  Linked identical skill copy: $dst"
    fi
    ((ITEMS_LINKED++))
    return 0
  fi

  link_file "$src" "$dst"
}

directory_matches_copy_without_symlinks() {
  local src="${1%/}"
  local dst="$2"
  [[ -d "$src" && -d "$dst" && ! -L "$dst" ]] || return 1

  local rel src_item dst_item
  while IFS= read -r -d '' rel; do
    [[ "$rel" = "." ]] && continue
    rel="${rel#./}"
    src_item="$src/$rel"
    dst_item="$dst/$rel"

    if [[ -d "$src_item" && ! -L "$src_item" ]]; then
      [[ -d "$dst_item" && ! -L "$dst_item" ]] || return 1
    elif [[ -f "$src_item" && ! -L "$src_item" ]]; then
      [[ -f "$dst_item" && ! -L "$dst_item" ]] || return 1
      cmp -s "$src_item" "$dst_item" || return 1
    else
      return 1
    fi
  done < <(cd "$src" && find . ! -type l -print0)

  # The deployed copy must not contain extra files or any nested symlink.
  while IFS= read -r -d '' rel; do
    [[ "$rel" = "." ]] && continue
    rel="${rel#./}"
    dst_item="$dst/$rel"
    src_item="$src/$rel"
    [[ ! -L "$dst_item" ]] || return 1
    [[ -e "$src_item" && ! -L "$src_item" ]] || return 1
  done < <(cd "$dst" && find . -print0)

  return 0
}

validate_openclaw_skill_source() {
  local src="${1%/}"
  local nested_link

  while IFS= read -r -d '' nested_link; do
    # Legacy tracked artifact from the original Peekaboo sync test. It points
    # back at its own skill directory and is intentionally omitted from copies.
    if [[ "$(basename "$src")" = "peekaboo" && "$nested_link" = "$src/peekaboo" ]]; then
      local link_target
      link_target=$(readlink "$nested_link") || link_target=""
      if [[ -n "$link_target" ]]; then
        [[ "$link_target" != /* ]] && link_target="$(dirname "$nested_link")/$link_target"
        if [[ "$(resolve_path "$link_target")" = "$(resolve_path "$src")" ]]; then
          continue
        fi
      fi
    fi

    log_error "OpenClaw skill source contains a nested symlink: $nested_link"
    log_error "Remove or replace it before deploying the skill copy."
    EXIT_CODE=1
    return 1
  done < <(find "$src" -mindepth 1 -type l -print0)

  return 0
}

normalize_openclaw_skill_copy_permissions() {
  local dst="$1"
  [[ "$DRY_RUN" = true ]] && return 0

  if ! find "$dst" -maxdepth 1 -type f ! -name "*.md" ! -name "*.json" ! -name "*.yaml" -exec chmod +x {} +; then
    log_error "Could not normalize OpenClaw skill wrapper permissions: $dst"
    EXIT_CODE=1
    return 1
  fi

  return 0
}

stage_openclaw_skill_copy() {
  local src="$1"
  local dst="$2"
  local parent
  parent=$(dirname "$dst")

  if ! mkdir -p "$parent"; then
    log_error "Could not create OpenClaw skills directory: $parent"
    EXIT_CODE=1
    return 1
  fi

  local staging_root
  if ! staging_root=$(mktemp -d "$parent/.codex-install.XXXXXX"); then
    log_error "Could not create staging directory for OpenClaw skill: $dst"
    EXIT_CODE=1
    return 1
  fi

  local staged="$staging_root/replacement"
  if ! cp -R "$src" "$staged"; then
    log_error "Could not stage OpenClaw skill copy: $dst"
    EXIT_CODE=1
    discard_staging_root "$staging_root" || true
    return 1
  fi
  if ! find "$staged" -type l -delete; then
    log_error "Could not remove nested symlinks from staged OpenClaw skill: $dst"
    EXIT_CODE=1
    discard_staging_root "$staging_root" || true
    return 1
  fi
  if ! normalize_openclaw_skill_copy_permissions "$staged"; then
    discard_staging_root "$staging_root" || true
    return 1
  fi

  swap_staged_path "$staging_root" "$staged" "$dst" "OpenClaw skill copy"
}

deploy_openclaw_skill_copy() {
  local src="${1%/}"
  local dst="$2"

  validate_openclaw_skill_source "$src" || return 1

  if directory_matches_copy_without_symlinks "$src" "$dst"; then
    normalize_openclaw_skill_copy_permissions "$dst" || return 1
    log_verbose "  Already deployed as copy: $dst"
    return 0
  fi

  # Migrate the pre-2026.3.7 symlink contract to the real-copy contract.
  if [[ -L "$dst" ]]; then
    local current_target
    current_target=$(get_symlink_target "$dst")
    [[ "$current_target" != /* ]] && current_target="$(dirname "$dst")/$current_target"
    current_target=$(resolve_path "$current_target")
    if [[ "$current_target" = "$(resolve_path "$src")" ]]; then
      log "  Migrating OpenClaw skill symlink to copy: $dst"
    elif [[ "$FORCE" = true ]]; then
      backup_item "$dst" || return 1
    else
      if ! prompt_conflict "$dst" "symlink to $current_target"; then
        return 0
      fi
      backup_item "$dst" || return 1
    fi
  elif [[ -e "$dst" ]]; then
    if [[ "$FORCE" = true ]]; then
      backup_item "$dst" || return 1
    else
      if ! prompt_conflict "$dst" "$(get_file_type "$dst")"; then
        return 0
      fi
      backup_item "$dst" || return 1
    fi
  fi

  if [[ "$DRY_RUN" = true ]]; then
    log "  [dry-run] Would deploy OpenClaw skill copy: $dst"
  else
    stage_openclaw_skill_copy "$src" "$dst" || return 1
    log "  Deployed OpenClaw skill copy: $dst"
  fi
  ((ITEMS_LINKED++))
}

stage_local_file() {
  local src="$1"
  local dst="$2"
  local label="$3"
  local parent
  parent=$(dirname "$dst")

  if ! mkdir -p "$parent"; then
    log_error "Could not create parent directory for $label: $parent"
    EXIT_CODE=1
    return 1
  fi

  local staging_root
  if ! staging_root=$(mktemp -d "$parent/.codex-install.XXXXXX"); then
    log_error "Could not create staging directory for $label: $dst"
    EXIT_CODE=1
    return 1
  fi

  local staged="$staging_root/replacement"
  if ! cp "$src" "$staged"; then
    log_error "Could not stage $label: $dst"
    EXIT_CODE=1
    discard_staging_root "$staging_root" || true
    return 1
  fi
  if ! chmod 600 "$staged"; then
    log_error "Could not secure staged $label: $dst"
    EXIT_CODE=1
    discard_staging_root "$staging_root" || true
    return 1
  fi

  swap_staged_path "$staging_root" "$staged" "$dst" "$label"
}

preserve_or_seed_local_file() {
  local src="$1"
  local dst="$2"
  local label="$3"

  # OpenClaw rewrites this file atomically and keeps machine-local settings in
  # it. Once it is a regular file, the runtime copy is authoritative.
  if [[ -f "$dst" && ! -L "$dst" ]]; then
    log_verbose "  Preserving machine-local $label: $dst"
    return 0
  fi

  if [[ -L "$dst" ]]; then
    local current_target
    current_target=$(get_symlink_target "$dst")
    [[ "$current_target" != /* ]] && current_target="$(dirname "$dst")/$current_target"
    current_target=$(resolve_path "$current_target")
    if [[ "$current_target" = "$(resolve_path "$src")" ]]; then
      if [[ "$DRY_RUN" = true ]]; then
        log "  [dry-run] Would convert $label symlink to local file: $dst"
      else
        stage_local_file "$src" "$dst" "$label" || return 1
        log "  Converted $label to local file: $dst"
      fi
      ((ITEMS_LINKED++))
      return 0
    fi
  fi

  if [[ -e "$dst" || -L "$dst" ]]; then
    if [[ "$FORCE" = true ]]; then
      backup_item "$dst" || return 1
    else
      if ! prompt_conflict "$dst" "$(get_file_type "$dst")"; then
        return 0
      fi
      backup_item "$dst" || return 1
    fi
  fi

  if [[ "$DRY_RUN" = true ]]; then
    log "  [dry-run] Would seed local $label: $dst"
  else
    stage_local_file "$src" "$dst" "$label" || return 1
    log "  Seeded local $label: $dst"
  fi
  ((ITEMS_LINKED++))
}

is_generated_openclaw_gateway_plist() {
  local path="$1"
  [[ -f "$path" && ! -L "$path" ]] || return 1

  local label program
  label=$(/usr/libexec/PlistBuddy -c 'Print :Label' "$path" 2>/dev/null) || return 1
  program=$(/usr/libexec/PlistBuddy -c 'Print :ProgramArguments:0' "$path" 2>/dev/null) || return 1
  [[ "$label" = "ai.openclaw.gateway" ]] || return 1
  [[ "$program" = "$HOME/.openclaw/service-env/ai.openclaw.gateway-env-wrapper.sh" ]]
}

install_openclaw_gateway_plist() {
  local src="$1"
  local dst="$2"

  if is_generated_openclaw_gateway_plist "$dst"; then
    log_verbose "  Preserving generated OpenClaw gateway plist: $dst"
    return 0
  fi
  if [[ -f "$dst" && ! -L "$dst" ]] && cmp -s "$src" "$dst"; then
    log_verbose "  Preserving identical gateway plist copy: $dst"
    return 0
  fi
  link_file "$src" "$dst"
}

install_managed_file_copy() {
  local src="$1"
  local dst="$2"
  local mode="$3"
  local label="$4"

  if [[ -f "$dst" && ! -L "$dst" ]] && cmp -s "$src" "$dst"; then
    chmod "$mode" "$dst" 2>/dev/null || true
    log_verbose "  Preserving identical $label: $dst"
    return 0
  fi

  if [[ "$DRY_RUN" = true ]]; then
    log "  [dry-run] Would install $label: $dst"
  else
    local tmp="$dst.tmp.$$"
    mkdir -p "$(dirname "$dst")"
    cp "$src" "$tmp"
    chmod "$mode" "$tmp"
    mv "$tmp" "$dst"
    log "  Installed $label: $dst"
  fi
  ((ITEMS_LINKED++))
}

install_managed_launchagent() {
  local src="$1"
  local dst="$2"
  local label="$3"
  local domain="gui/$(id -u)"
  local changed=false

  if [[ ! -f "$dst" || -L "$dst" ]] || ! cmp -s "$src" "$dst"; then
    changed=true
    install_managed_file_copy "$src" "$dst" 644 "$label LaunchAgent"
  fi

  if [[ "$DRY_RUN" = true ]]; then
    if ! launchctl print "$domain/$label" >/dev/null 2>&1; then
      log "  [dry-run] Would bootstrap $label"
    elif [[ "$changed" = true ]]; then
      log "  [dry-run] Would reload $label"
    fi
    return 0
  fi

  if launchctl print "$domain/$label" >/dev/null 2>&1; then
    if [[ "$changed" = true ]]; then
      launchctl bootout "$domain/$label"
      launchctl bootstrap "$domain" "$dst"
      log "  Reloaded LaunchAgent: $label"
    fi
  else
    launchctl bootstrap "$domain" "$dst"
    log "  Bootstrapped LaunchAgent: $label"
  fi
}

# === Migration Functions ===

migrate_directory_symlink() {
  local target_dir="$1"
  local repo_dir="$2"

  if [[ -L "$target_dir" ]]; then
    local link_target
    link_target=$(get_symlink_target "$target_dir")

    local resolved_target
    if [[ "$link_target" = /* ]]; then
      resolved_target="$link_target"
    else
      resolved_target="$(dirname "$target_dir")/$link_target"
    fi
    resolved_target=$(resolve_path "$resolved_target")

    local expected
    expected=$(resolve_path "$repo_dir")

    # Match exact path or subpath (with path boundary to avoid matching skills_old, etc.)
    if [[ "$resolved_target" = "$expected" ]] || [[ "$resolved_target" = "$expected/"* ]]; then
      log "  Migrating $target_dir from directory symlink to per-item symlinks"
      if [[ "$DRY_RUN" = true ]]; then
        log "    [dry-run] Would remove symlink and create directory"
      else
        rm "$target_dir"
        mkdir -p "$target_dir"
      fi
    else
      log_warn "$target_dir is symlink to external location: $resolved_target"
      if [[ "$FORCE" = true ]]; then
        log "  --force: Removing external symlink"
        if [[ "$DRY_RUN" != true ]]; then
          rm "$target_dir"
          mkdir -p "$target_dir"
        fi
      else
        log "  Skipping. Use --force to override."
        return 1
      fi
    fi
  elif [[ -d "$target_dir" ]]; then
    log_verbose "  $target_dir is already a directory"
  else
    log_verbose "  Creating $target_dir"
    if [[ "$DRY_RUN" != true ]]; then
      mkdir -p "$target_dir"
    fi
  fi
  return 0
}

prune_stale_managed_symlinks() {
  local target_dir="$1"
  local repo_dir="$2"

  [[ -d "$target_dir" && -d "$repo_dir" ]] || return 0

  local expected_root
  expected_root=$(resolve_path "$repo_dir")
  for target_path in "$target_dir"/*; do
    [[ -L "$target_path" && ! -e "$target_path" ]] || continue

    local link_target resolved_target
    link_target=$(get_symlink_target "$target_path")
    # link_file creates absolute source links. Ignore unknown relative links rather
    # than trying to canonicalize a path whose target was intentionally deleted.
    [[ "$link_target" = /* ]] || continue
    resolved_target="${link_target%/}"

    # Only prune links to a deleted item inside this repository's managed directory.
    if [[ "$resolved_target" = "$expected_root/"* ]]; then
      if [[ "$DRY_RUN" = true ]]; then
        log "  [dry-run] Would remove stale managed symlink: $target_path"
      else
        rm "$target_path"
        log "  Removed stale managed symlink: $target_path"
      fi
    fi
  done
}

# === Main Installation ===

install_dotfiles() {
  log "Installing dotfiles from $DOTFILES_DIR"
  log ""

  # === Core Dotfiles ===
  log "${BLUE}Core dotfiles:${NC}"
  link_file "$DOTFILES_DIR/zshrc" "$HOME/.zshrc"
  link_file "$DOTFILES_DIR/gitconfig" "$HOME/.gitconfig"
  link_file "$DOTFILES_DIR/tmux.conf" "$HOME/.tmux.conf"

  # SSH config
  if [[ "$DRY_RUN" != true ]]; then
    mkdir -p "$HOME/.ssh"
    chmod 700 "$HOME/.ssh"
  fi
  link_file "$DOTFILES_DIR/ssh_config" "$HOME/.ssh/config"
  log ""

  # === Codex Config ===
  log "${BLUE}Codex config:${NC}"
  if [[ "$DRY_RUN" != true ]]; then
    mkdir -p "$HOME/.codex"
  fi

  # Project config is discovered in-place. Keep the user config mutable so
  # Codex can store machine-local providers, plugins, and trusted projects.
  if [[ -L "$HOME/.codex/config.toml" ]]; then
    local codex_config_target
    codex_config_target=$(get_symlink_target "$HOME/.codex/config.toml")
    if [[ "$codex_config_target" != /* ]]; then
      codex_config_target="$(dirname "$HOME/.codex/config.toml")/$codex_config_target"
    fi
    codex_config_target=$(resolve_path "$codex_config_target")

    if [[ "$codex_config_target" = "$(resolve_path "$DOTFILES_DIR/.codex/config.toml")" ]]; then
      if [[ "$DRY_RUN" = true ]]; then
        log "  [dry-run] Would convert to local file: $HOME/.codex/config.toml"
      else
        rm "$HOME/.codex/config.toml"
        cp "$DOTFILES_DIR/.codex/config.toml" "$HOME/.codex/config.toml"
        log "  Converted to local file: $HOME/.codex/config.toml"
      fi
    fi
  fi

  local codex_profiles="$DOTFILES_DIR/.codex/profiles"
  local profile_path profile_target
  for profile_path in "$codex_profiles"/*.config.toml; do
    [[ -f "$profile_path" ]] || continue
    profile_target="$HOME/.codex/$(basename "$profile_path")"

    # A profile may have been staged before this checkout reached the machine.
    # Convert an identical copy without treating it as a user conflict.
    if [[ ! -L "$profile_target" && -f "$profile_target" ]] && cmp -s "$profile_path" "$profile_target"; then
      if [[ "$DRY_RUN" = true ]]; then
        log "  [dry-run] Would replace identical profile with symlink: $profile_target"
      else
        rm "$profile_target"
        ln -s "$profile_path" "$profile_target"
        log "  Linked identical profile: $profile_target"
      fi
      ((ITEMS_LINKED++))
      continue
    fi

    link_file "$profile_path" "$profile_target"
  done
  link_file "$DOTFILES_DIR/.codex/AGENTS.md" "$HOME/.codex/AGENTS.md"
  link_file "$DOTFILES_DIR/.codex/rules" "$HOME/.codex/rules"
  log ""

  # === Claude Code Config ===
  log "${BLUE}Claude Code config:${NC}"
  if [[ "$DRY_RUN" != true ]]; then
    mkdir -p "$HOME/.claude"
  fi

  # Individual files
  link_file "$DOTFILES_DIR/.claude/CLAUDE.md" "$HOME/.claude/CLAUDE.md"
  link_file "$DOTFILES_DIR/.claude/preferences.md" "$HOME/.claude/preferences.md"
  link_file "$DOTFILES_DIR/.claude/settings.json" "$HOME/.claude/settings.json"
  link_file "$DOTFILES_DIR/.claude/session-notes.md" "$HOME/.claude/session-notes.md"
  log ""

  # === Per-item Symlinks (Skills, Commands, Hooks) ===

  # Skills - per-skill directory symlinks
  log "${BLUE}Skills (per-item):${NC}"
  local skills_repo="$DOTFILES_DIR/.claude/skills"
  local skills_target="$HOME/.claude/skills"

  if [[ -d "$skills_repo" ]]; then
    migrate_directory_symlink "$skills_target" "$skills_repo" || true
    prune_stale_managed_symlinks "$skills_target" "$skills_repo"

    for skill_path in "$skills_repo"/*/; do
      [[ -d "$skill_path" ]] || continue
      local skill_name
      skill_name=$(basename "$skill_path")
      link_skill_directory "$skill_path" "$skills_target/$skill_name"
    done
  fi
  log ""

  # Commands - per-file symlinks
  log "${BLUE}Commands (per-item):${NC}"
  local commands_repo="$DOTFILES_DIR/.claude/commands"
  local commands_target="$HOME/.claude/commands"

  if [[ -d "$commands_repo" ]]; then
    migrate_directory_symlink "$commands_target" "$commands_repo" || true

    for cmd_path in "$commands_repo"/*.md; do
      [[ -f "$cmd_path" ]] || continue
      local cmd_name
      cmd_name=$(basename "$cmd_path")
      link_file "$cmd_path" "$commands_target/$cmd_name"
    done
  fi
  log ""

  # Hooks - deployed as copies (not symlinks) because OpenClaw v2026.3.7+
  # rejects symlinks resolving outside rootDir, and Claude Code hook loading
  # may also have issues with symlinks.
  log "${BLUE}Hooks (copies):${NC}"
  local hooks_repo="$DOTFILES_DIR/.claude/hooks"
  local hooks_target="$HOME/.claude/hooks"

  if [[ -d "$hooks_repo" ]]; then
    if [[ "$DRY_RUN" != true ]]; then
      mkdir -p "$hooks_target"
    fi

    for hook_path in "$hooks_repo"/*; do
      [[ -f "$hook_path" ]] || continue
      local hook_name
      hook_name=$(basename "$hook_path")
      local hook_dst="$hooks_target/$hook_name"

      # Skip if already an identical copy
      if [[ -f "$hook_dst" ]] && ! [[ -L "$hook_dst" ]] && diff -q "$hook_path" "$hook_dst" &>/dev/null; then
        log_verbose "  Already synced: $hook_dst"
        continue
      fi

      # If it's a symlink pointing to our repo file, replace with a copy
      # (migration from old symlink-based deployment)
      if [[ -L "$hook_dst" ]]; then
        local hook_link_target
        hook_link_target=$(get_symlink_target "$hook_dst")
        [[ "$hook_link_target" != /* ]] && hook_link_target="$(dirname "$hook_dst")/$hook_link_target"
        hook_link_target=$(resolve_path "$hook_link_target")
        local hook_expected
        hook_expected=$(resolve_path "$hook_path")
        if [[ "$hook_link_target" = "$hook_expected" ]]; then
          log "  Migrating symlink to copy: $hook_dst"
          if [[ "$DRY_RUN" != true ]]; then
            rm "$hook_dst"
            cp "$hook_path" "$hook_dst"
            chmod +x "$hook_dst"
          fi
          ((ITEMS_LINKED++))
          continue
        fi
      fi

      # Back up existing if present
      if [[ -e "$hook_dst" || -L "$hook_dst" ]]; then
        if [[ "$FORCE" = true ]]; then
          if ! backup_item "$hook_dst"; then
            continue
          fi
          if [[ "$DRY_RUN" != true ]]; then
            rm -f "$hook_dst"
          fi
        else
          if ! prompt_conflict "$hook_dst" "$(get_file_type "$hook_dst")"; then
            continue
          fi
          if ! backup_item "$hook_dst"; then
            continue
          fi
          if [[ "$DRY_RUN" != true ]]; then
            rm -f "$hook_dst"
          fi
        fi
      fi

      if [[ "$DRY_RUN" = true ]]; then
        log "  [dry-run] Would copy: $hook_path -> $hook_dst"
      else
        cp "$hook_path" "$hook_dst"
        chmod +x "$hook_dst"
        log "  Copied: $hook_dst"
      fi
      ((ITEMS_LINKED++))
    done
  fi
  log ""

  # === Directory Symlinks (Rules, Docs) ===
  log "${BLUE}Directory symlinks:${NC}"
  link_file "$DOTFILES_DIR/.claude/rules" "$HOME/.claude/rules"
  link_file "$DOTFILES_DIR/.claude/docs" "$HOME/.claude/docs"
  log ""

  # === Plugins ===
  # known_marketplaces.json is machine-local (absolute installLocation paths) and gitignored.
  # On a fresh install, re-add the marketplaces from inside Claude Code:
  #   /plugin marketplace add anthropics/claude-plugins-official
  #   /plugin marketplace add jarrodwatts/claude-hud
  #   /plugin marketplace add jarrodwatts/claude-delegator
  log "${BLUE}Plugins:${NC}"
  if [[ "$DRY_RUN" != true ]]; then
    mkdir -p "$HOME/.claude/plugins"
  fi
  if [[ ! -f "$HOME/.claude/plugins/known_marketplaces.json" ]]; then
    log "  Marketplaces not yet registered. Inside Claude Code, run:"
    log "    /plugin marketplace add anthropics/claude-plugins-official"
    log "    /plugin marketplace add jarrodwatts/claude-hud"
    log "    /plugin marketplace add jarrodwatts/claude-delegator"
  fi
  log ""

  # === OpenClaw Config ===
  log "${BLUE}OpenClaw config:${NC}"
  if [[ -d "$DOTFILES_DIR/openclaw" ]]; then
    if [[ "$DRY_RUN" != true ]]; then
      mkdir -p "$HOME/.openclaw"
    fi

    # Detect if this is the gateway host or a remote client.
    # macOS may append Bonjour conflict suffixes (for example, -2 or -3), and
    # this machine may also be intentionally named mac-mini.
    local hostname
    hostname=$(hostname -s 2>/dev/null || echo "unknown")
    local hostname_key
    hostname_key=$(printf '%s' "$hostname" | tr '[:upper:]' '[:lower:]')

    if [[ "$hostname_key" = "mac-mini" || "$hostname_key" == mac-mini-[0-9]* || "$hostname_key" = "dylans-mac-mini" || "$hostname_key" == dylans-mac-mini-[0-9]* ]]; then
      log "  Detected gateway host: $hostname"
      if [[ "$DRY_RUN" != true ]]; then
        mkdir -p "$HOME/Applications"
        mkdir -p "$HOME/Library/LaunchAgents"
        mkdir -p "$HOME/.openclaw/workspace/scripts"
      fi

      # Seed the gateway config, then preserve OpenClaw's machine-local regular
      # file. OpenClaw Doctor may replace an old symlink during atomic rewrites.
      preserve_or_seed_local_file \
        "$DOTFILES_DIR/openclaw/openclaw.json" \
        "$HOME/.openclaw/openclaw.json" \
        "OpenClaw gateway config"

      # FDA .app wrapper for gateway LaunchAgent
      link_file "$DOTFILES_DIR/openclaw/OpenClawGateway.app" "$HOME/Applications/OpenClawGateway.app"

      # The tracked plist is a recovery source. A generated service-env plist
      # installed by OpenClaw is an equally valid live contract and is preserved.
      install_openclaw_gateway_plist \
        "$DOTFILES_DIR/openclaw/launchagents/ai.openclaw.gateway.plist" \
        "$HOME/Library/LaunchAgents/ai.openclaw.gateway.plist"

      # The native imsg bridge injection does not survive a reboot. This
      # one-shot/interval agent repairs bridge v2 in the Aqua session and only
      # restarts the gateway after the bridge is confirmed ready.
      install_managed_file_copy \
        "$DOTFILES_DIR/openclaw/bin/imsg-bridge-ensure" \
        "$HOME/.openclaw/bin/imsg-bridge-ensure" \
        755 \
        "imsg bridge watchdog"
      install_managed_launchagent \
        "$DOTFILES_DIR/openclaw/launchagents/ai.openclaw.imsg-bridge-ensure.plist" \
        "$HOME/Library/LaunchAgents/ai.openclaw.imsg-bridge-ensure.plist" \
        "ai.openclaw.imsg-bridge-ensure"

      # BlueBubbles and its watchdogs are intentionally retired. Current
      # OpenClaw owns Messages through the native imsg bridge; launching both
      # helpers makes outbound iMessage delivery hang.

      # OpenClaw skills (gateway host only)
      if [[ -d "$DOTFILES_DIR/openclaw/skills" ]]; then
        if [[ "$DRY_RUN" != true ]]; then
          mkdir -p "$HOME/.openclaw/skills"
        fi
        for skill_dir in "$DOTFILES_DIR/openclaw/skills"/*/; do
          [[ -d "$skill_dir" ]] || continue
          local skill_name
          skill_name=$(basename "$skill_dir")
          deploy_openclaw_skill_copy "$skill_dir" "$HOME/.openclaw/skills/$skill_name"
        done
      fi
    else
      log "  Detected remote client: $hostname"

      # Remote config (thin client pointing to gateway over Tailscale). Keep it
      # local after seeding so runtime-managed settings never modify the repo.
      preserve_or_seed_local_file \
        "$DOTFILES_DIR/openclaw/openclaw-remote.json" \
        "$HOME/.openclaw/openclaw.json" \
        "OpenClaw remote config"
    fi
  fi
  log ""

  # === Claude Memory (per-project) ===
  log "${BLUE}Claude memory:${NC}"
  local memory_repo="$DOTFILES_DIR/.claude/projects/-Users-dbochman/memory"
  local memory_target="$HOME/.claude/projects/-Users-dbochman/memory"

  if [[ -d "$memory_repo" ]]; then
    if [[ "$DRY_RUN" != true ]]; then
      mkdir -p "$memory_target"
    fi

    for mem_file in "$memory_repo"/*.md; do
      [[ -f "$mem_file" ]] || continue
      local mem_name
      mem_name=$(basename "$mem_file")
      link_file "$mem_file" "$memory_target/$mem_name"
    done
  fi
  log ""

  # === Bin scripts (symlinked to /opt/homebrew/bin) ===
  log "${BLUE}Bin scripts:${NC}"
  if [[ -d "$DOTFILES_DIR/bin" ]]; then
    for script in "$DOTFILES_DIR/bin"/*; do
      [[ -f "$script" ]] && link_file "$script" "/opt/homebrew/bin/$(basename "$script")"
    done
  fi
  log ""

  # === Local bin scripts ===
  log "${BLUE}Local bin scripts:${NC}"
  if [[ -d "$DOTFILES_DIR/.local/bin" ]]; then
    if [[ "$DRY_RUN" != true ]]; then
      mkdir -p "$HOME/.local/bin"
    fi
    for script in "$DOTFILES_DIR/.local/bin"/*; do
      [[ -f "$script" ]] && link_file "$script" "$HOME/.local/bin/$(basename "$script")"
    done
  fi
  log ""

  # Write manifest and prune
  write_manifest
  prune_old_backups

  # Summary
  log "================================"
  log "${GREEN}Installation complete!${NC}"
  log "  Items linked:  $ITEMS_LINKED"
  [[ "$ITEMS_SKIPPED" -gt 0 ]] && log "  Items skipped: $ITEMS_SKIPPED"
  [[ "$ITEMS_BACKED_UP" -gt 0 ]] && log "  Items backed up: $ITEMS_BACKED_UP"
  [[ -n "$CURRENT_BACKUP_DIR" ]] && log "  Backup: $CURRENT_BACKUP_DIR"
  log ""
  log "Restart your shell or run: source ~/.zshrc"
  log ""
  log "To check sync status: ./sync.sh"
  log "To validate config:   ./sync.sh validate"
}

# === Help ===

usage() {
  cat << 'EOF'
Usage: ./install.sh [options]

Installs dotfiles using symlinks, runtime-safe copies, and machine-local seeds.

Options:
  -n, --dry-run    Preview changes without making them
  -f, --force      Replace conflicts without prompting; runtime-owned OpenClaw
                   config and generated service files remain protected
  -v, --verbose    Detailed output
  -q, --quiet      Minimal output
  -h, --help       Show this help message

Exit codes:
  0  Success
  1  Error
  2  Partial success (some items skipped due to conflicts)

Examples:
  ./install.sh                # Interactive install
  ./install.sh --dry-run      # Preview what would happen
  ./install.sh --force        # Replace unprotected conflicts
  ./install.sh -fq            # Force + quiet (for scripts)
EOF
}

# === Argument Parsing ===

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -n|--dry-run)
        DRY_RUN=true
        shift
        ;;
      -f|--force)
        FORCE=true
        shift
        ;;
      -v|--verbose)
        VERBOSE=true
        shift
        ;;
      -q|--quiet)
        QUIET=true
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      -*)
        # Handle combined flags like -fq
        local flags="${1#-}"
        shift
        while [[ -n "$flags" ]]; do
          local flag="${flags:0:1}"
          flags="${flags:1}"
          case "$flag" in
            n) DRY_RUN=true ;;
            f) FORCE=true ;;
            v) VERBOSE=true ;;
            q) QUIET=true ;;
            h) usage; exit 0 ;;
            *) log_error "Unknown flag: -$flag"; exit 1 ;;
          esac
        done
        ;;
      *)
        log_error "Unknown argument: $1"
        usage
        exit 1
        ;;
    esac
  done
}

# === Main ===

main() {
  parse_args "$@"

  if [[ "$DRY_RUN" = true ]]; then
    log "${YELLOW}=== DRY RUN MODE ===${NC}"
    log ""
  fi

  install_dotfiles

  exit $EXIT_CODE
}

main "$@"
