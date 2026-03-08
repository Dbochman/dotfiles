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
qmd context add qmd://skills/ "OpenClaw skill definitions (SKILL.md files) with full API details, CLI commands, env vars, and troubleshooting for: grocery-reorder (Star Market), gws-gmail/calendar/drive (Google Workspace), cielo-ac (smart AC), mysa-thermostat (baseboard heaters), hue-lights, nest-thermostat (+ DASHBOARD.md), roomba, spotify-speakers, presence detection, opentable/resy booking, sag (TTS), peekaboo (cameras), samsung-tv, 1password, bluetooth, shortcuts, applescript, web-search, summarize, google-speakers, echonest, amazon-shopping, places, cabin/crosstown routines"
qmd context add qmd://dotfiles-openclaw/ "Dotfiles repo openclaw/ directory containing: plans/ with BlueBubbles implementation deep-dive (bluebubbles-implementation-current-state.md — watchdog, webhook architecture, Private API, restart sequencing, Cloudflare issues), BlueBubbles Private API reference (bluebubbles-private-api.md — curl examples, typing, reactions, effects), OpenClaw workspace state overview (openclaw-workspace-state.md). Also: bin/ scripts (README.md, WEEKLY-UPGRADE.md — upgrade steps, BB plugin patch, plist backup), Mysa plan, GOG cutover history, usage dashboard issues, and copies of all skill SKILL.md files and workspace markdown"

echo "=== Setting update command for dotfiles ==="
qmd collection update-cmd dotfiles-openclaw 'cd ~/dotfiles && git stash; git pull --rebase --ff-only; git stash pop 2>/dev/null; true'

echo "=== Generating embeddings (downloads models on first run) ==="
qmd embed

echo "=== Done ==="
qmd status
