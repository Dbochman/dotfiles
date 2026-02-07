#!/usr/bin/env bash
# Dotfiles install script
# Run on new machines: git clone <repo> ~/dotfiles && ~/dotfiles/install.sh
#
# Usage: ./install.sh [options]
#
# Options:
#   -n, --dry-run    Preview changes without making them
#   -f, --force      Replace conflicts without prompting
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
}

log_warn() {
  echo -e "${YELLOW}WARNING: $1${NC}" >&2
}

log_error() {
  echo -e "${RED}ERROR: $1${NC}" >&2
}

# === Backup Functions ===

create_backup_dir() {
  [[ -n "$CURRENT_BACKUP_DIR" ]] && return

  local timestamp
  timestamp=$(date +%Y-%m-%d-%H%M%S)
  CURRENT_BACKUP_DIR="$BACKUP_DIR/$timestamp"

  if [[ "$DRY_RUN" = true ]]; then
    log_verbose "  [dry-run] Would create backup: $CURRENT_BACKUP_DIR"
    return
  fi

  mkdir -p "$CURRENT_BACKUP_DIR"
}

backup_item() {
  local src="$1"
  [[ -e "$src" || -L "$src" ]] || return 0

  create_backup_dir

  local relative_path="${src#$HOME/}"
  local dest="$CURRENT_BACKUP_DIR/$relative_path"

  if [[ "$DRY_RUN" = true ]]; then
    log_verbose "  [dry-run] Would backup: $src"
    return
  fi

  mkdir -p "$(dirname "$dest")"
  if [[ -L "$src" ]]; then
    cp -P "$src" "$dest"
  elif [[ -d "$src" ]]; then
    cp -R "$src" "$dest"
  else
    cp "$src" "$dest"
  fi

  BACKED_UP_ITEMS+=("$src")
  ((ITEMS_BACKED_UP++))
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
      backup_item "$dst"
      if [[ "$DRY_RUN" != true ]]; then
        rm "$dst"
      fi
    else
      if ! prompt_conflict "$dst" "symlink to $current_target"; then
        return 0
      fi
      backup_item "$dst"
      if [[ "$DRY_RUN" != true ]]; then
        rm "$dst"
      fi
    fi
  elif [[ -e "$dst" ]]; then
    # Regular file or directory
    local file_type
    file_type=$(get_file_type "$dst")

    if [[ "$FORCE" = true ]]; then
      backup_item "$dst"
      if [[ "$DRY_RUN" != true ]]; then
        rm -rf "$dst"
      fi
    else
      if ! prompt_conflict "$dst" "$file_type"; then
        return 0
      fi
      backup_item "$dst"
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

# === Main Installation ===

install_dotfiles() {
  log "Installing dotfiles from $DOTFILES_DIR"
  log ""

  # === Core Dotfiles ===
  log "${BLUE}Core dotfiles:${NC}"
  link_file "$DOTFILES_DIR/zshrc" "$HOME/.zshrc"
  link_file "$DOTFILES_DIR/gitconfig" "$HOME/.gitconfig"

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
  link_file "$DOTFILES_DIR/.codex/config.toml" "$HOME/.codex/config.toml"
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

    for skill_path in "$skills_repo"/*/; do
      [[ -d "$skill_path" ]] || continue
      local skill_name
      skill_name=$(basename "$skill_path")
      link_file "$skill_path" "$skills_target/$skill_name"
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

  # Hooks - per-file symlinks
  log "${BLUE}Hooks (per-item):${NC}"
  local hooks_repo="$DOTFILES_DIR/.claude/hooks"
  local hooks_target="$HOME/.claude/hooks"

  if [[ -d "$hooks_repo" ]]; then
    migrate_directory_symlink "$hooks_target" "$hooks_repo" || true

    for hook_path in "$hooks_repo"/*; do
      [[ -f "$hook_path" ]] || continue
      local hook_name
      hook_name=$(basename "$hook_path")
      link_file "$hook_path" "$hooks_target/$hook_name"
    done
  fi
  log ""

  # === Directory Symlinks (Rules, Docs) ===
  log "${BLUE}Directory symlinks:${NC}"
  link_file "$DOTFILES_DIR/.claude/rules" "$HOME/.claude/rules"
  link_file "$DOTFILES_DIR/.claude/docs" "$HOME/.claude/docs"
  log ""

  # === Plugins ===
  log "${BLUE}Plugins:${NC}"
  if [[ "$DRY_RUN" != true ]]; then
    mkdir -p "$HOME/.claude/plugins"
  fi
  link_file "$DOTFILES_DIR/.claude/plugins/known_marketplaces.json" "$HOME/.claude/plugins/known_marketplaces.json"
  log ""

  # === OpenClaw Config ===
  log "${BLUE}OpenClaw config:${NC}"
  if [[ -d "$DOTFILES_DIR/openclaw" ]]; then
    if [[ "$DRY_RUN" != true ]]; then
      mkdir -p "$HOME/.openclaw"
    fi

    # Detect if this is the gateway host (dylans-mac-mini) or a remote client
    local hostname
    hostname=$(hostname -s 2>/dev/null || echo "unknown")

    if [[ "$hostname" = "Dylans-Mac-mini" ]]; then
      log "  Detected gateway host: $hostname"
      if [[ "$DRY_RUN" != true ]]; then
        mkdir -p "$HOME/Applications"
        mkdir -p "$HOME/Library/LaunchAgents"
      fi

      # Gateway config (full config with local gateway, channels, skills)
      link_file "$DOTFILES_DIR/openclaw/openclaw.json" "$HOME/.openclaw/openclaw.json"

      # FDA .app wrapper for gateway LaunchAgent
      link_file "$DOTFILES_DIR/openclaw/OpenClawGateway.app" "$HOME/Applications/OpenClawGateway.app"

      # LaunchAgent plist
      link_file "$DOTFILES_DIR/openclaw/ai.openclaw.gateway.plist" "$HOME/Library/LaunchAgents/ai.openclaw.gateway.plist"
    else
      log "  Detected remote client: $hostname"

      # Remote config (thin client pointing to gateway over Tailscale)
      link_file "$DOTFILES_DIR/openclaw/openclaw-remote.json" "$HOME/.openclaw/openclaw.json"
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

Installs dotfiles by creating symlinks from home directory to this repo.

Options:
  -n, --dry-run    Preview changes without making them
  -f, --force      Replace conflicts without prompting
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
  ./install.sh --force        # Replace all conflicts
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
