#!/bin/bash
# water-scrape-sync.sh — Scrape BWSC portal and sync water data to Mac Mini
#
# Runs the Playwright scraper locally (where the browser session exists),
# imports into local SQLite, then copies the DB to the Mac Mini and restarts
# the dashboard.
#
# Usage:
#   water-scrape-sync.sh              # Full scrape + sync
#   water-scrape-sync.sh --dry-run    # Scrape only, don't sync to Mini

REPO_DIR="${HOME}/repos/financial-dashboard"
MINI_HOST="dylans-mac-mini"
MINI_REPO="/Users/dbochman/repos/financial-dashboard"
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=10"
LAST_RUN_FILE="${REPO_DIR}/.water-scrape-last-run"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

trap 'log "ERROR: script failed at line $LINENO (exit $?)"' ERR

cd "$REPO_DIR"

# --- Skip if already succeeded this month ---
CURRENT_MONTH=$(date '+%Y-%m')
if [[ "${1:-}" != "--force" && "${1:-}" != "--dry-run" && -f "$LAST_RUN_FILE" ]]; then
  LAST_MONTH=$(cat "$LAST_RUN_FILE" 2>/dev/null || echo "")
  if [[ "$LAST_MONTH" == "$CURRENT_MONTH" ]]; then
    log "Already synced for $CURRENT_MONTH — skipping (use --force to override)"
    exit 0
  fi
fi

# --- Step 1: Scrape BWSC portal ---
log "Scraping BWSC portal..."
if ! python3 scrape_bwsc.py --headless --merge; then
  log "ERROR: Scrape failed"
  exit 1
fi

# --- Step 2: Import into local SQLite ---
log "Importing into SQLite..."
if ! python3 update_data.py import-json-water; then
  log "ERROR: Import failed"
  exit 1
fi

# --- Step 3: Sync to Mac Mini ---
if [[ "${1:-}" == "--dry-run" ]]; then
  log "Dry run — skipping sync to Mini"
  exit 0
fi

log "Copying finance.db to Mini..."
if ! scp $SSH_OPTS "$REPO_DIR/finance.db" "${MINI_HOST}:${MINI_REPO}/finance.db"; then
  log "ERROR: scp to Mini failed (network down or SSH key unavailable)"
  exit 1
fi

log "Restarting dashboard on Mini..."
if ! ssh $SSH_OPTS "$MINI_HOST" "finance dashboard restart"; then
  log "ERROR: dashboard restart on Mini failed"
  exit 1
fi

# Mark this month as done (prevents re-runs until next month)
echo "$CURRENT_MONTH" > "$LAST_RUN_FILE"
log "Done. Water data synced successfully."
