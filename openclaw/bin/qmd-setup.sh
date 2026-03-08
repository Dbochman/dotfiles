#!/bin/bash
# qmd-setup.sh — Initialize qmd (Quick Markdown Search) on the Mac Mini.
# Indexes all OpenClaw markdown for hybrid BM25 + vector search.
# Run once after installing qmd: npm install -g @tobilu/qmd
set -euo pipefail

export PATH="/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:/usr/bin:/bin:$PATH"

if ! command -v qmd &>/dev/null; then
  echo "qmd not found. Install with: npm install -g @tobilu/qmd"
  exit 1
fi

echo "=== Adding collections ==="
qmd collection add ~/.openclaw/workspace --name workspace 2>&1
qmd collection add ~/.openclaw/skills --name skills 2>&1
qmd collection add ~/dotfiles/openclaw --name dotfiles-openclaw 2>&1

echo "=== Adding context descriptions ==="
qmd context add qmd://workspace/ "OpenClaw runtime workspace: SOUL.md (agent personality/rules), TOOLS.md (device integrations, BB, Nest, Hue, Cielo, presence), HEARTBEAT.md (periodic health checks)"
qmd context add qmd://skills/ "OpenClaw skill definitions (SKILL.md files): grocery-reorder, gws-gmail, gws-calendar, gws-drive, cielo-ac, mysa-thermostat, roomba, hue, 1password, sag, opentable, and more"
qmd context add qmd://dotfiles-openclaw/ "Dotfiles repo openclaw/ directory: bin scripts (README, weekly-upgrade, refresh-secrets, nest, dashboards), plans, workspace copies, skill definitions, LaunchAgent plists"

echo "=== Setting update command for dotfiles ==="
qmd collection update-cmd dotfiles-openclaw 'cd ~/dotfiles && git stash && git pull --rebase --ff-only && git stash pop'

echo "=== Generating embeddings (downloads models on first run) ==="
qmd embed

echo "=== Done ==="
qmd status
