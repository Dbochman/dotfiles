#!/usr/bin/env bash
# sync.sh - Synchronization tool for Claude Code configuration
# Usage: ./sync.sh [command] [options]
#
# Commands:
#   status              Show sync status (default)
#   add TYPE NAME       Add local item to repo (skill|command|hook)
#   remove TYPE NAME    Remove from repo, keep local copy
#   pull                Pull latest and reinstall
#   push "message"      Commit and push changes
#   undo                Restore from last backup
#   validate            Validate all managed items
#
# Options:
#   -n, --dry-run       Preview changes without making them
#   -f, --force         Replace conflicts without prompting
#   -h, --help          Show this help message

# Note: Not using set -e due to complex control flow with subshells
set -o pipefail

# === Configuration ===
DOTFILES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="$HOME/.claude"
CODEX_DIR="$HOME/.codex"
BACKUP_DIR="$DOTFILES_DIR/.backup"
MAX_BACKUPS=10

# State
DRY_RUN=false
FORCE=false
COMMAND=""
ITEM_TYPE=""
ITEM_NAME=""
COMMIT_MSG=""

# Colors (disabled if not TTY)
if [[ -t 1 ]]; then
  RED='\033[0;31m'
  GREEN='\033[0;32m'
  YELLOW='\033[0;33m'
  BLUE='\033[0;34m'
  NC='\033[0m' # No Color
else
  RED=''
  GREEN=''
  YELLOW=''
  BLUE=''
  NC=''
fi

# === Portability Shims ===

# readlink -f replacement for macOS (resolves symlinks in path components)
resolve_path() {
  local path="$1"
  # Prefer GNU readlink if available (brew install coreutils)
  if command -v greadlink &>/dev/null; then
    greadlink -f "$path"
    return
  fi
  # GNU readlink -f works on Linux
  if readlink -f / &>/dev/null 2>&1; then
    readlink -f "$path"
    return
  fi
  # Pure POSIX fallback - handles symlinks in path components
  local result=""
  if [[ "$path" = /* ]]; then
    result=""
  else
    result="$(pwd)"
  fi
  # Process each component
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
      # Resolve symlink if it is one
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

# Get symlink target (BSD and GNU compatible)
get_symlink_target() {
  local path="$1"
  readlink "$path"
}

# Get file type using bash built-ins
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

# === Logging ===

log() {
  echo -e "$1"
}

log_info() {
  echo -e "${BLUE}$1${NC}"
}

log_success() {
  echo -e "${GREEN}$1${NC}"
}

log_warn() {
  echo -e "${YELLOW}WARNING: $1${NC}" >&2
}

log_error() {
  echo -e "${RED}ERROR: $1${NC}" >&2
}

# === Backup Functions ===

create_backup_dir() {
  local timestamp
  timestamp=$(date +%Y-%m-%d-%H%M%S)
  local backup_path="$BACKUP_DIR/$timestamp"

  if [[ "$DRY_RUN" = true ]]; then
    log "  [dry-run] Would create backup directory: $backup_path"
    echo "$backup_path"
    return
  fi

  mkdir -p "$backup_path"
  echo "$backup_path"
}

backup_item() {
  local src="$1"
  local backup_path="$2"
  local relative_path="${src#$HOME/}"
  local dest="$backup_path/$relative_path"

  if [[ "$DRY_RUN" = true ]]; then
    log "  [dry-run] Would backup: $src"
    return
  fi

  mkdir -p "$(dirname "$dest")"
  if [[ -L "$src" ]]; then
    # Backup symlink itself
    cp -P "$src" "$dest"
  elif [[ -d "$src" ]]; then
    cp -R "$src" "$dest"
  else
    cp "$src" "$dest"
  fi
}

prune_old_backups() {
  if [[ "$DRY_RUN" = true ]]; then
    return
  fi

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
  local backup_path="$1"
  shift
  local operation="$1"
  shift
  local items_backed_up=("$@")

  if [[ "$DRY_RUN" = true ]]; then
    return
  fi

  local manifest="$backup_path/manifest.json"
  local hostname
  hostname=$(hostname -s 2>/dev/null || echo "unknown")
  local timestamp
  timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  # Build JSON manually (no jq dependency)
  {
    echo "{"
    echo "  \"timestamp\": \"$timestamp\","
    echo "  \"operation\": \"$operation\","
    echo "  \"hostname\": \"$hostname\","
    echo "  \"items_backed_up\": ["
    local first=true
    for item in "${items_backed_up[@]}"; do
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

get_latest_backup() {
  [[ -d "$BACKUP_DIR" ]] || return 1
  find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d | sort -r | head -n 1
}

# === Status Functions ===

get_item_status() {
  local target_path="$1"
  local repo_path="$2"

  if [[ ! -e "$target_path" && ! -L "$target_path" ]]; then
    echo "missing"
    return
  fi

  if [[ -L "$target_path" ]]; then
    local link_target
    link_target=$(get_symlink_target "$target_path")

    # Resolve to absolute
    local resolved
    if [[ "$link_target" = /* ]]; then
      resolved="$link_target"
    else
      resolved="$(dirname "$target_path")/$link_target"
    fi
    resolved=$(resolve_path "$resolved")

    local expected
    expected=$(resolve_path "$repo_path")

    if [[ ! -e "$resolved" ]]; then
      echo "broken"
    elif [[ "$resolved" = "$expected" ]]; then
      echo "synced"
    else
      echo "external"
    fi
  else
    echo "local"
  fi
}

show_status() {
  log "Claude Code Sync Status"
  log "======================="
  log ""

  local synced_skills=0
  local local_skills=0
  local synced_commands=0
  local local_commands=0
  local synced_hooks=0
  local local_hooks=0

  # Skills
  log "Skills:"
  local skills_dir="$DOTFILES_DIR/.claude/skills"
  local target_skills_dir="$CLAUDE_DIR/skills"

  if [[ -d "$skills_dir" ]]; then
    for skill_path in "$skills_dir"/*/; do
      [[ -d "$skill_path" ]] || continue
      local skill_name
      skill_name=$(basename "$skill_path")
      local target_path="$target_skills_dir/$skill_name"
      local status
      status=$(get_item_status "$target_path" "$skill_path")

      case "$status" in
        synced)
          log "  ${GREEN}✓${NC} $skill_name (synced)"
          ((synced_skills++))
          ;;
        local)
          log "  ${YELLOW}○${NC} $skill_name (local only)"
          ((local_skills++))
          ;;
        broken)
          log "  ${RED}⚠${NC} $skill_name (broken symlink)"
          ;;
        external)
          log "  ${BLUE}→${NC} $skill_name (points elsewhere)"
          ;;
        missing)
          log "  ${YELLOW}○${NC} $skill_name (not installed)"
          ;;
      esac
    done
  fi

  # Check for local-only skills in target
  if [[ -d "$target_skills_dir" ]]; then
    for skill_path in "$target_skills_dir"/*/; do
      [[ -d "$skill_path" ]] || continue
      local skill_name
      skill_name=$(basename "$skill_path")
      if [[ ! -d "$skills_dir/$skill_name" ]]; then
        local status
        status=$(get_file_type "$skill_path")
        if [[ "$status" = "directory" ]]; then
          log "  ${YELLOW}○${NC} $skill_name (local only)"
          ((local_skills++))
        fi
      fi
    done
  fi

  log ""

  # Commands
  log "Commands:"
  local commands_dir="$DOTFILES_DIR/.claude/commands"
  local target_commands_dir="$CLAUDE_DIR/commands"

  if [[ -d "$commands_dir" ]]; then
    for cmd_path in "$commands_dir"/*.md; do
      [[ -f "$cmd_path" ]] || continue
      local cmd_name
      cmd_name=$(basename "$cmd_path")
      local target_path="$target_commands_dir/$cmd_name"
      local status
      status=$(get_item_status "$target_path" "$cmd_path")

      case "$status" in
        synced)
          log "  ${GREEN}✓${NC} $cmd_name (synced)"
          ((synced_commands++))
          ;;
        local|missing)
          log "  ${YELLOW}○${NC} $cmd_name (not installed)"
          ;;
        broken)
          log "  ${RED}⚠${NC} $cmd_name (broken symlink)"
          ;;
        external)
          log "  ${BLUE}→${NC} $cmd_name (points elsewhere)"
          ;;
      esac
    done
  fi

  log ""

  # Hooks
  log "Hooks:"
  local hooks_dir="$DOTFILES_DIR/.claude/hooks"
  local target_hooks_dir="$CLAUDE_DIR/hooks"

  if [[ -d "$hooks_dir" ]]; then
    for hook_path in "$hooks_dir"/*; do
      [[ -f "$hook_path" ]] || continue
      local hook_name
      hook_name=$(basename "$hook_path")
      local target_path="$target_hooks_dir/$hook_name"
      local status
      status=$(get_item_status "$target_path" "$hook_path")

      case "$status" in
        synced)
          log "  ${GREEN}✓${NC} $hook_name (synced)"
          ((synced_hooks++))
          ;;
        local|missing)
          log "  ${YELLOW}○${NC} $hook_name (not installed)"
          ;;
        broken)
          log "  ${RED}⚠${NC} $hook_name (broken symlink)"
          ;;
        external)
          log "  ${BLUE}→${NC} $hook_name (points elsewhere)"
          ;;
      esac
    done
  fi

  log ""

  # Directory symlinks
  log "Directory symlinks:"
  for dir in rules docs; do
    local repo_dir="$DOTFILES_DIR/.claude/$dir"
    local target_dir="$CLAUDE_DIR/$dir"

    if [[ -d "$repo_dir" ]]; then
      local status
      status=$(get_item_status "$target_dir" "$repo_dir")
      case "$status" in
        synced)
          log "  ${GREEN}✓${NC} $dir (synced)"
          ;;
        *)
          log "  ${YELLOW}○${NC} $dir ($status)"
          ;;
      esac
    fi
  done

  log ""
  log "Legend: ${GREEN}✓${NC} synced | ${YELLOW}○${NC} local only | ${RED}⚠${NC} broken | ${BLUE}→${NC} external"
}

# === Add/Remove Functions ===

validate_item_name() {
  local name="$1"
  # Reject empty, path traversal, absolute paths, and leading dash
  if [[ -z "$name" ]]; then
    log_error "Item name cannot be empty"
    return 1
  fi
  if [[ "$name" == *".."* ]]; then
    log_error "Item name cannot contain '..'"
    return 1
  fi
  if [[ "$name" == /* ]]; then
    log_error "Item name cannot be an absolute path"
    return 1
  fi
  if [[ "$name" == -* ]]; then
    log_error "Item name cannot start with '-'"
    return 1
  fi
  if [[ "$name" == *"/"* ]]; then
    log_error "Item name cannot contain '/'"
    return 1
  fi
  return 0
}

add_item() {
  local item_type="$1"
  local item_name="$2"

  # Validate item name
  validate_item_name "$item_name" || exit 1

  local source_path=""
  local repo_path=""

  case "$item_type" in
    skill)
      source_path="$CLAUDE_DIR/skills/$item_name"
      repo_path="$DOTFILES_DIR/.claude/skills/$item_name"
      ;;
    command)
      # Add .md extension if not present
      [[ "$item_name" == *.md ]] || item_name="$item_name.md"
      source_path="$CLAUDE_DIR/commands/$item_name"
      repo_path="$DOTFILES_DIR/.claude/commands/$item_name"
      ;;
    hook)
      source_path="$CLAUDE_DIR/hooks/$item_name"
      repo_path="$DOTFILES_DIR/.claude/hooks/$item_name"
      ;;
    *)
      log_error "Unknown item type: $item_type (use: skill, command, hook)"
      exit 1
      ;;
  esac

  # Check source exists
  if [[ ! -e "$source_path" ]]; then
    log_error "Source not found: $source_path"
    exit 1
  fi

  # Check if already a symlink to repo
  if [[ -L "$source_path" ]]; then
    local link_target
    link_target=$(get_symlink_target "$source_path")
    # Resolve relative symlinks from the symlink's directory
    if [[ "$link_target" != /* ]]; then
      link_target="$(dirname "$source_path")/$link_target"
    fi
    link_target=$(resolve_path "$link_target")
    local expected
    expected=$(resolve_path "$repo_path")
    if [[ "$link_target" = "$expected" ]]; then
      log_warn "$item_name is already synced"
      exit 0
    fi
  fi

  # Check if already exists in repo
  if [[ -e "$repo_path" ]]; then
    log_error "$item_name already exists in repo: $repo_path"
    log "  Use 'remove' first if you want to replace it"
    exit 1
  fi

  log_info "Adding $item_type: $item_name"

  # Create backup
  local backup_path
  backup_path=$(create_backup_dir)
  backup_item "$source_path" "$backup_path"
  write_manifest "$backup_path" "add" "$source_path"

  if [[ "$DRY_RUN" = true ]]; then
    log "  [dry-run] Would copy $source_path to $repo_path"
    log "  [dry-run] Would create symlink $source_path -> $repo_path"
    return
  fi

  # Copy to repo
  mkdir -p "$(dirname "$repo_path")"
  if [[ -d "$source_path" ]]; then
    cp -R "$source_path" "$repo_path"
  else
    cp "$source_path" "$repo_path"
  fi

  # Replace with symlink
  rm -rf "$source_path"
  ln -s "$repo_path" "$source_path"

  prune_old_backups

  log_success "Added $item_name to repo"
  log "  Repo: $repo_path"
  log "  Symlink: $source_path -> $repo_path"
  log ""
  log "Run './sync.sh push \"Add $item_name\"' to commit"
}

remove_item() {
  local item_type="$1"
  local item_name="$2"

  # Validate item name
  validate_item_name "$item_name" || exit 1

  local target_path=""
  local repo_path=""

  case "$item_type" in
    skill)
      target_path="$CLAUDE_DIR/skills/$item_name"
      repo_path="$DOTFILES_DIR/.claude/skills/$item_name"
      ;;
    command)
      [[ "$item_name" == *.md ]] || item_name="$item_name.md"
      target_path="$CLAUDE_DIR/commands/$item_name"
      repo_path="$DOTFILES_DIR/.claude/commands/$item_name"
      ;;
    hook)
      target_path="$CLAUDE_DIR/hooks/$item_name"
      repo_path="$DOTFILES_DIR/.claude/hooks/$item_name"
      ;;
    *)
      log_error "Unknown item type: $item_type (use: skill, command, hook)"
      exit 1
      ;;
  esac

  # Check repo path exists
  if [[ ! -e "$repo_path" ]]; then
    log_error "Not found in repo: $repo_path"
    exit 1
  fi

  log_info "Removing $item_type: $item_name"

  # Create backup
  local backup_path
  backup_path=$(create_backup_dir)
  backup_item "$repo_path" "$backup_path"
  [[ -e "$target_path" ]] && backup_item "$target_path" "$backup_path"
  write_manifest "$backup_path" "remove" "$repo_path" "$target_path"

  if [[ "$DRY_RUN" = true ]]; then
    log "  [dry-run] Would remove symlink: $target_path"
    log "  [dry-run] Would copy $repo_path to $target_path"
    log "  [dry-run] Would remove from repo: $repo_path"
    return
  fi

  # Remove symlink if it exists
  if [[ -L "$target_path" ]]; then
    rm "$target_path"
  fi

  # Copy from repo to local
  if [[ -d "$repo_path" ]]; then
    cp -R "$repo_path" "$target_path"
  else
    cp "$repo_path" "$target_path"
  fi

  # Remove from repo
  rm -rf "$repo_path"

  prune_old_backups

  log_success "Removed $item_name from repo (kept local copy)"
  log "  Local: $target_path"
  log ""
  log "Run './sync.sh push \"Remove $item_name\"' to commit"
}

# === Git Operations ===

pull_changes() {
  log_info "Pulling changes..."

  cd "$DOTFILES_DIR"

  # Check for uncommitted changes
  if ! git diff --quiet || ! git diff --cached --quiet; then
    log_error "Uncommitted changes in dotfiles repo"
    log "  Commit or stash changes first:"
    log "    git stash"
    log "    ./sync.sh pull"
    log "    git stash pop"
    exit 1
  fi

  if [[ "$DRY_RUN" = true ]]; then
    log "  [dry-run] Would run: git pull --ff-only"
    log "  [dry-run] Would run: ./install.sh"
    return
  fi

  # Pull with fast-forward only
  if ! git pull --ff-only; then
    log_error "Pull failed (remote has diverged)"
    log "  Try: git fetch && git rebase origin/$(git branch --show-current)"
    exit 1
  fi

  log_success "Pull successful"
  log ""

  # Run install
  local install_args=""
  [[ "$FORCE" = true ]] && install_args="--force"
  "$DOTFILES_DIR/install.sh" $install_args
}

push_changes() {
  local message="$1"

  if [[ -z "$message" ]]; then
    log_error "Commit message required"
    log "  Usage: ./sync.sh push \"Your commit message\""
    exit 1
  fi

  log_info "Pushing changes..."

  cd "$DOTFILES_DIR"

  # Check remote exists
  if ! git remote get-url origin &>/dev/null; then
    log_error "No remote 'origin' configured"
    exit 1
  fi

  if [[ "$DRY_RUN" = true ]]; then
    log "  [dry-run] Would run: git add -A"
    log "  [dry-run] Would run: git commit -m \"$message\""
    log "  [dry-run] Would run: git push"
    return
  fi

  # Stage all changes
  git add -A

  # Check if there's anything to commit
  if git diff --cached --quiet; then
    log_warn "No changes to commit"
    exit 0
  fi

  # Commit
  git commit -m "$message"

  # Push
  if ! git push; then
    log_error "Push failed"
    log "  Remote may have diverged. Try: ./sync.sh pull"
    exit 1
  fi

  log_success "Changes pushed successfully"
}

# === Undo Function ===

undo_last() {
  local latest_backup
  latest_backup=$(get_latest_backup)

  if [[ -z "$latest_backup" ]]; then
    log_error "No backups available"
    exit 1
  fi

  log_info "Restoring from backup: $(basename "$latest_backup")"

  local manifest="$latest_backup/manifest.json"
  if [[ ! -f "$manifest" ]]; then
    log_error "Backup manifest not found: $manifest"
    exit 1
  fi

  if [[ "$DRY_RUN" = true ]]; then
    log "  [dry-run] Would restore from: $latest_backup"
    log "  [dry-run] Manifest:"
    cat "$manifest"
    return
  fi

  # Read backed up items from manifest (line-oriented to handle spaces in paths)
  grep -o '"path": "[^"]*"' "$manifest" | cut -d'"' -f4 | while IFS= read -r item_path; do
    [[ -z "$item_path" ]] && continue

    # Expand ~ to $HOME
    item_path="${item_path/#\~/$HOME}"
    local relative_path="${item_path#$HOME/}"
    local backup_item_path="$latest_backup/$relative_path"

    if [[ -e "$backup_item_path" ]]; then
      log "  Restoring: $item_path"

      # Remove current
      rm -rf "$item_path"

      # Restore from backup
      mkdir -p "$(dirname "$item_path")"
      if [[ -d "$backup_item_path" ]]; then
        cp -R "$backup_item_path" "$item_path"
      elif [[ -L "$backup_item_path" ]]; then
        cp -P "$backup_item_path" "$item_path"
      else
        cp "$backup_item_path" "$item_path"
      fi
    fi
  done

  # Remove the backup directory
  rm -rf "$latest_backup"

  log_success "Restored from backup"
}

# === Validation Functions ===

validate_skill() {
  local skill_dir="$1"
  local skill_name
  skill_name=$(basename "$skill_dir")
  local skill_md="$skill_dir/SKILL.md"
  local -a errors
  errors=()

  # Check SKILL.md exists
  if [[ ! -f "$skill_md" ]]; then
    errors+=("SKILL.md not found")
    printf '%s\n' "${errors[@]}"
    return
  fi

  # Check frontmatter delimiters
  local first_line
  first_line=$(head -n1 "$skill_md")
  if [[ "$first_line" != "---" ]]; then
    errors+=("Missing opening frontmatter delimiter (---)")
  fi

  # Extract frontmatter
  local frontmatter
  frontmatter=$(sed -n '/^---$/,/^---$/p' "$skill_md" | sed '1d;$d')

  if [[ -z "$frontmatter" ]]; then
    errors+=("Empty or missing frontmatter")
  else
    # Check required fields
    if ! echo "$frontmatter" | grep -qE '^name:\s*.+'; then
      errors+=("Missing or empty 'name' field")
    fi

    if ! echo "$frontmatter" | grep -qE '^description:\s*.+'; then
      errors+=("Missing or empty 'description' field")
    fi
  fi

  [[ ${#errors[@]} -gt 0 ]] && printf '%s\n' "${errors[@]}"
}

validate_command() {
  local cmd_file="$1"
  local -a errors
  errors=()

  # Must end in .md
  if [[ "$cmd_file" != *.md ]]; then
    errors+=("Filename must end in .md")
  fi

  # Must be non-empty
  if [[ ! -s "$cmd_file" ]]; then
    errors+=("File is empty")
  fi

  [[ ${#errors[@]} -gt 0 ]] && printf '%s\n' "${errors[@]}"
}

validate_hook() {
  local hook_file="$1"
  local -a errors
  errors=()

  # Must be executable
  if [[ ! -x "$hook_file" ]]; then
    errors+=("File is not executable (run: chmod +x)")
  fi

  # Must have shebang
  local first_line
  first_line=$(head -n1 "$hook_file")
  if [[ "$first_line" != "#!"* ]]; then
    errors+=("Missing shebang (e.g., #!/usr/bin/env bash)")
  fi

  [[ ${#errors[@]} -gt 0 ]] && printf '%s\n' "${errors[@]}"
}

validate_all() {
  log "Validating..."
  log ""

  local total_errors=0

  # Validate skills
  local skills_dir="$DOTFILES_DIR/.claude/skills"
  if [[ -d "$skills_dir" ]]; then
    for skill_path in "$skills_dir"/*/; do
      [[ -d "$skill_path" ]] || continue
      local skill_name
      skill_name=$(basename "$skill_path")
      local errors
      errors=$(validate_skill "$skill_path")

      if [[ -z "$errors" ]]; then
        log "  ${GREEN}✓${NC} skills/$skill_name"
      else
        log "  ${RED}✗${NC} skills/$skill_name:"
        while IFS= read -r error; do
          [[ -n "$error" ]] && log "      - $error"
          ((total_errors++)) || true
        done <<< "$errors"
      fi
    done
  fi

  # Validate commands
  local commands_dir="$DOTFILES_DIR/.claude/commands"
  if [[ -d "$commands_dir" ]]; then
    for cmd_path in "$commands_dir"/*.md; do
      [[ -f "$cmd_path" ]] || continue
      local cmd_name
      cmd_name=$(basename "$cmd_path")
      local errors
      errors=$(validate_command "$cmd_path")

      if [[ -z "$errors" ]]; then
        log "  ${GREEN}✓${NC} commands/$cmd_name"
      else
        log "  ${RED}✗${NC} commands/$cmd_name:"
        while IFS= read -r error; do
          [[ -n "$error" ]] && log "      - $error"
          ((total_errors++)) || true
        done <<< "$errors"
      fi
    done
  fi

  # Validate hooks
  local hooks_dir="$DOTFILES_DIR/.claude/hooks"
  if [[ -d "$hooks_dir" ]]; then
    for hook_path in "$hooks_dir"/*; do
      [[ -f "$hook_path" ]] || continue
      local hook_name
      hook_name=$(basename "$hook_path")
      local errors
      errors=$(validate_hook "$hook_path")

      if [[ -z "$errors" ]]; then
        log "  ${GREEN}✓${NC} hooks/$hook_name"
      else
        log "  ${RED}✗${NC} hooks/$hook_name:"
        while IFS= read -r error; do
          [[ -n "$error" ]] && log "      - $error"
          ((total_errors++)) || true
        done <<< "$errors"
      fi
    done
  fi

  # Validate symlinks
  log ""
  log "  Checking symlinks..."
  local symlink_errors=0
  local symlink_total=0

  # Check all symlinks in target dirs
  for target_dir in "$CLAUDE_DIR/skills" "$CLAUDE_DIR/commands" "$CLAUDE_DIR/hooks"; do
    [[ -d "$target_dir" ]] || continue
    for item in "$target_dir"/*; do
      [[ -e "$item" || -L "$item" ]] || continue
      if [[ -L "$item" ]]; then
        ((symlink_total++))
        if [[ ! -e "$item" ]]; then
          log "    ${RED}✗${NC} Broken: $item"
          ((symlink_errors++))
        fi
      fi
    done
  done

  if [[ "$symlink_errors" -eq 0 ]]; then
    log "  ${GREEN}✓${NC} symlinks: $symlink_total valid"
  else
    log "  ${RED}✗${NC} symlinks: $symlink_errors broken out of $symlink_total"
    ((total_errors += symlink_errors))
  fi

  log ""
  if [[ "$total_errors" -eq 0 ]]; then
    log_success "Validation passed"
    exit 0
  else
    log_error "Validation: $total_errors error(s) found"
    exit 1
  fi
}

# === Help ===

usage() {
  cat << 'EOF'
Usage: ./sync.sh [command] [options]

Commands:
  status              Show sync status (default)
  add TYPE NAME       Add local item to repo
                      TYPE: skill | command | hook
  remove TYPE NAME    Remove from repo, keep local copy
  pull                Pull latest changes and reinstall
  push "message"      Commit and push changes
  undo                Restore from last backup
  validate            Validate all managed items

Options:
  -n, --dry-run       Preview changes without making them
  -f, --force         Replace conflicts without prompting
  -h, --help          Show this help message

Examples:
  ./sync.sh                           # Show status
  ./sync.sh add skill my-skill        # Add local skill to repo
  ./sync.sh remove command foo        # Remove command from repo
  ./sync.sh pull                      # Pull and reinstall
  ./sync.sh push "Add new skill"      # Commit and push
  ./sync.sh validate                  # Check all items
  ./sync.sh -n add skill test         # Dry-run add
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
      -h|--help)
        usage
        exit 0
        ;;
      status)
        COMMAND="status"
        shift
        ;;
      add)
        COMMAND="add"
        shift
        if [[ $# -lt 2 ]]; then
          log_error "Usage: ./sync.sh add TYPE NAME"
          exit 1
        fi
        ITEM_TYPE="$1"
        ITEM_NAME="$2"
        shift 2
        ;;
      remove)
        COMMAND="remove"
        shift
        if [[ $# -lt 2 ]]; then
          log_error "Usage: ./sync.sh remove TYPE NAME"
          exit 1
        fi
        ITEM_TYPE="$1"
        ITEM_NAME="$2"
        shift 2
        ;;
      pull)
        COMMAND="pull"
        shift
        ;;
      push)
        COMMAND="push"
        shift
        if [[ $# -lt 1 ]]; then
          log_error "Usage: ./sync.sh push \"commit message\""
          exit 1
        fi
        COMMIT_MSG="$1"
        shift
        ;;
      undo)
        COMMAND="undo"
        shift
        ;;
      validate)
        COMMAND="validate"
        shift
        ;;
      *)
        log_error "Unknown command or option: $1"
        usage
        exit 1
        ;;
    esac
  done
}

# === Main ===

main() {
  parse_args "$@"

  case "${COMMAND:-status}" in
    status)
      show_status
      ;;
    add)
      add_item "$ITEM_TYPE" "$ITEM_NAME"
      ;;
    remove)
      remove_item "$ITEM_TYPE" "$ITEM_NAME"
      ;;
    pull)
      pull_changes
      ;;
    push)
      push_changes "$COMMIT_MSG"
      ;;
    undo)
      undo_last
      ;;
    validate)
      validate_all
      ;;
  esac
}

main "$@"
