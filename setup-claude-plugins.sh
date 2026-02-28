#!/usr/bin/env bash
# Automated Claude Code plugin setup
# Clones marketplaces, installs plugins to cache, and writes installed_plugins.json
#
# Run after install.sh has symlinked settings.json and known_marketplaces.json
#
# Usage: ./setup-claude-plugins.sh

set -euo pipefail

PLUGINS_DIR="$HOME/.claude/plugins"
MARKETPLACES_DIR="$PLUGINS_DIR/marketplaces"
CACHE_DIR="$PLUGINS_DIR/cache"
INSTALLED_FILE="$PLUGINS_DIR/installed_plugins.json"

# Colors
if [[ -t 1 ]]; then
  GREEN='\033[0;32m'
  BLUE='\033[0;34m'
  YELLOW='\033[0;33m'
  RED='\033[0;31m'
  NC='\033[0m'
else
  GREEN='' BLUE='' YELLOW='' RED='' NC=''
fi

log() { echo -e "$1"; }

# Plugin definitions: marketplace_name|github_repo|plugin_name|version
PLUGINS=(
  "claude-plugins-official|anthropics/claude-plugins-official|code-simplifier|1.0.0"
  "claude-hud|jarrodwatts/claude-hud|claude-hud|0.0.4"
  "jarrodwatts-claude-delegator|jarrodwatts/claude-delegator|claude-delegator|1.0.0"
)

# Additional marketplaces to clone (even if no plugins installed from them)
EXTRA_MARKETPLACES=(
)

mkdir -p "$MARKETPLACES_DIR" "$CACHE_DIR"

# --- Clone/update marketplaces ---
clone_marketplace() {
  local name="$1"
  local repo="$2"
  local dest="$MARKETPLACES_DIR/$name"

  if [[ -d "$dest/.git" ]]; then
    log "  ${GREEN}✓${NC} $name (already cloned, pulling)"
    git -C "$dest" pull --ff-only --quiet 2>/dev/null || true
  else
    local url
    if [[ "$repo" == https://* ]]; then
      url="$repo"
    else
      url="https://github.com/$repo.git"
    fi
    log "  ${BLUE}↓${NC} Cloning $name from $url"
    git clone --quiet "$url" "$dest"
  fi
}

log "${BLUE}Cloning marketplaces...${NC}"
# Marketplaces from plugin definitions
seen_marketplaces=""
for entry in "${PLUGINS[@]}"; do
  IFS='|' read -r mp_name gh_repo plugin_name version <<< "$entry"
  if ! echo "$seen_marketplaces" | grep -qw "$mp_name"; then
    clone_marketplace "$mp_name" "$gh_repo"
    seen_marketplaces="$seen_marketplaces $mp_name"
  fi
done

# Extra marketplaces
for entry in "${EXTRA_MARKETPLACES[@]}"; do
  IFS='|' read -r mp_name repo <<< "$entry"
  if ! echo "$seen_marketplaces" | grep -qw "$mp_name"; then
    clone_marketplace "$mp_name" "$repo"
    seen_marketplaces="$seen_marketplaces $mp_name"
  fi
done
log ""

# --- Install plugins to cache ---
log "${BLUE}Installing plugins to cache...${NC}"
for entry in "${PLUGINS[@]}"; do
  IFS='|' read -r mp_name gh_repo plugin_name version <<< "$entry"

  local_cache="$CACHE_DIR/$mp_name/$plugin_name/$version"

  if [[ -d "$local_cache" ]]; then
    log "  ${GREEN}✓${NC} $plugin_name@$version (cached)"
    continue
  fi

  # Find plugin source in marketplace
  mp_dir="$MARKETPLACES_DIR/$mp_name"
  plugin_src=""

  # Check if it's a single-plugin marketplace (plugin is the repo root)
  if [[ -f "$mp_dir/CLAUDE.md" ]] && [[ ! -d "$mp_dir/plugins/$plugin_name" ]]; then
    plugin_src="$mp_dir"
  # Check plugins/ subdirectory (official marketplace style)
  elif [[ -d "$mp_dir/plugins/$plugin_name" ]]; then
    plugin_src="$mp_dir/plugins/$plugin_name"
  else
    log "  ${RED}✗${NC} $plugin_name not found in $mp_name marketplace"
    continue
  fi

  log "  ${BLUE}↓${NC} Installing $plugin_name@$version"
  mkdir -p "$local_cache"
  cp -R "$plugin_src"/* "$local_cache"/
done
log ""

# --- Write installed_plugins.json ---
log "${BLUE}Writing installed_plugins.json...${NC}"
now=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)

# Build JSON
json='{\n  "version": 2,\n  "plugins": {'
first=true
for entry in "${PLUGINS[@]}"; do
  IFS='|' read -r mp_name gh_repo plugin_name version <<< "$entry"
  plugin_key="$plugin_name@$mp_name"
  install_path="$CACHE_DIR/$mp_name/$plugin_name/$version"

  [[ -d "$install_path" ]] || continue

  if [[ "$first" = true ]]; then
    first=false
  else
    json="$json,"
  fi

  json="$json\n    \"$plugin_key\": [\n      {\n        \"scope\": \"user\",\n        \"installPath\": \"$install_path\",\n        \"version\": \"$version\",\n        \"installedAt\": \"$now\",\n        \"lastUpdated\": \"$now\"\n      }\n    ]"
done
json="$json\n  }\n}"

echo -e "$json" > "$INSTALLED_FILE"
log "  ${GREEN}✓${NC} $INSTALLED_FILE"
log ""

# --- Update known_marketplaces.json timestamps ---
log "${BLUE}Updating known_marketplaces.json timestamps...${NC}"
if command -v python3 &>/dev/null && [[ -f "$PLUGINS_DIR/known_marketplaces.json" ]]; then
  python3 -c "
import json, datetime
now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
with open('$PLUGINS_DIR/known_marketplaces.json', 'r') as f:
    data = json.load(f)
home = '$HOME'
for name, mp in data.items():
    mp['installLocation'] = f'{home}/.claude/plugins/marketplaces/{name}'
    mp['lastUpdated'] = now
with open('$PLUGINS_DIR/known_marketplaces.json', 'w') as f:
    json.dump(data, f, indent=2)
    f.write('\n')
"
  log "  ${GREEN}✓${NC} Timestamps and paths updated"
else
  log "  ${YELLOW}⚠${NC} Skipped (python3 not available)"
fi
log ""

log "${GREEN}Done!${NC} Plugins installed:"
for entry in "${PLUGINS[@]}"; do
  IFS='|' read -r mp_name gh_repo plugin_name version <<< "$entry"
  log "  - $plugin_name ($mp_name) v$version"
done
log ""
log "Start Claude Code to verify: ${BLUE}claude${NC}"
