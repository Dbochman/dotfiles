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
# Four collections — workspace and skills from runtime paths, plans and bin-scripts
# from dotfiles. We intentionally avoid indexing ~/dotfiles/openclaw/**/*.md as a
# single collection because it duplicates skills/ and workspace/ content, wasting
# result slots on identical docs.
qmd collection add ~/.openclaw/workspace --name workspace 2>&1
qmd collection add ~/.openclaw/skills --name skills 2>&1
qmd collection add ~/dotfiles/openclaw/plans --name plans 2>&1
qmd collection add ~/dotfiles/openclaw/bin --name bin-scripts 2>&1

echo "=== Adding context descriptions ==="
qmd context add qmd://workspace/ "OpenClaw runtime workspace: SOUL.md (agent personality/rules), TOOLS.md (native iMessage plus device integrations), HEARTBEAT.md (periodic health ownership)"
qmd context add qmd://skills/ "OpenClaw skill definitions (SKILL.md files) with full API details, CLI commands, env vars, and troubleshooting for: grocery-reorder (Star Market), gws-gmail/calendar/drive (Google Workspace), cielo-ac (smart AC), mysa-thermostat (baseboard heaters), hue-lights, nest-thermostat (+ DASHBOARD.md), roomba, spotify-speakers, presence detection, opentable/resy booking, sag (TTS), peekaboo (cameras), samsung-tv, 1password, bluetooth, shortcuts, applescript, web-search, summarize, google-speakers, echonest, amazon-shopping, places, cabin/crosstown routines"
qmd context add qmd://plans/ "OpenClaw current plans and archived implementation records, including the completed native iMessage migration, infrastructure, cron, LaunchAgents, dashboards, and historical transports"
qmd context add qmd://bin-scripts/ "OpenClaw helper-script documentation for gateway maintenance, dashboards, Nest, usage and cron metrics, Bluetooth, native iMessage, dotfiles sync, and qmd setup"

echo "=== Setting update commands ==="
DOTFILES_PULL='cd ~/dotfiles && if [ "$(git branch --show-current)" = main ] && [ -z "$(git status --porcelain)" ]; then git pull --ff-only origin main; fi'
qmd collection update-cmd plans "$DOTFILES_PULL"
qmd collection update-cmd bin-scripts "$DOTFILES_PULL"

echo "=== Generating embeddings (downloads models on first run) ==="
qmd embed

echo "=== Done ==="
qmd status
