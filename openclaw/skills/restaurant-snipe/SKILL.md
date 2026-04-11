---
name: restaurant-snipe
description: Set up background reservation sniping for hard-to-book restaurants on OpenTable or Resy. Use when asked to monitor for cancellations, auto-book when a slot opens, snipe a reservation, or set up a recurring availability check for a specific restaurant.
allowed-tools: Bash(opentable:*),Bash(resy:*),Bash(launchctl:*),Bash(cat:*),Bash(tee:*),Bash(chmod:*),Bash(rm:*),Bash(ls:*),Bash(tail:*),Bash(pgrep:*),Bash(pkill:*),Bash(curl:*)
metadata: {"openclaw":{"emoji":"🎯","requires":{"bins":["opentable"]}}}
---

# Restaurant Snipe

Set up background LaunchAgent-based sniping for hard-to-book restaurants. Polls for cancellations on a schedule and auto-books when a matching slot appears. Sends an iMessage notification on success.

## When to Use

- A specific restaurant + date is fully booked and the user wants to watch for cancellations
- The user asks to "snipe", "monitor", or "watch for" a reservation
- The user wants automated polling over hours/days (not a one-shot check)

## Supported Platforms

| Platform | CLI | Snipe Support |
|----------|-----|---------------|
| OpenTable | `opentable snipe` | Built-in `--confirm` flag |
| Resy | `resy availability` + `resy book` | Manual: check + book in script |

## Setup a Snipe

### 1. Get the restaurant ID

**OpenTable:** Extract from URL (`opentable.com/r/restaurant-name?rid=12345` → ID is `12345`), or ask the user. Verify with:
```bash
opentable info <restaurant_id>
```

**Resy:** Get venue ID from search:
```bash
resy search "restaurant name"
```

### 2. Create the snipe script

Write to `~/.openclaw/bin/<name>-snipe.sh`. Template:

```bash
#!/bin/bash
# <restaurant-name> snipe — auto-book when slot appears
# Runs every <interval> via ai.openclaw.<name>-snipe LaunchAgent

set -a
source ~/.openclaw/.secrets-cache 2>/dev/null
set +a
export PATH=/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:$PATH
export HOME=/Users/dbochman

# Use 1Password service account (headless, no GUI popup)
if [[ -f "$HOME/.openclaw/.env-token" ]]; then
  export OP_SERVICE_ACCOUNT_TOKEN=$(cat "$HOME/.openclaw/.env-token")
fi

RESTAURANT_ID=<id>
PARTY_SIZE=<n>
TARGET_TIME="<HH:MM>"
DATES="<YYYY-MM-DD YYYY-MM-DD ...>"
LOG=~/.openclaw/logs/<name>-snipe.log

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }

log "=== <Restaurant> snipe starting ==="
log "Dates: $DATES | Party: $PARTY_SIZE | Target: $TARGET_TIME"

for date in $DATES; do
    log "Checking $date..."

    output=$(opentable snipe "$RESTAURANT_ID" "$date" "$PARTY_SIZE" \
        --time "$TARGET_TIME" --duration 60 --confirm 2>&1)

    if echo "$output" | grep -q "BOOKED\|booked\|Reservation booked"; then
        log "BOOKED on $date!"
        log "$output"

        # Notify via iMessage (group chat or DM)
        curl -s -X POST "http://localhost:1234/api/v1/message/text?password=$BLUEBUBBLES_PASSWORD" \
            -H "Content-Type: application/json" \
            -d "{
                \"chatGuid\": \"<chat_guid>\",
                \"message\": \"<Restaurant> booked! $date around <time> for <n> people. Check OpenTable for confirmation.\",
                \"tempGuid\": \"snipe-$(date +%s)\"
            }" >> "$LOG" 2>&1

        # Self-remove after successful booking
        launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/ai.openclaw.<name>-snipe.plist 2>/dev/null
        exit 0
    fi

    log "No slots on $date"
done

log "=== No slots found this run ==="
```

### 3. Create the LaunchAgent plist

Write to `~/Library/LaunchAgents/ai.openclaw.<name>-snipe.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>ai.openclaw.<name>-snipe</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/Users/dbochman/.openclaw/bin/<name>-snipe.sh</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>HOME</key>
        <string>/Users/dbochman</string>
        <key>PATH</key>
        <string>/Users/dbochman/.openclaw/bin:/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
    <key>StartInterval</key>
    <integer>1800</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/dbochman/.openclaw/logs/<name>-snipe.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/dbochman/.openclaw/logs/<name>-snipe.err.log</string>
</dict>
</plist>
```

### 4. Deploy and load

```bash
# Deploy script
scp <name>-snipe.sh dylans-mac-mini:~/.openclaw/bin/
ssh dylans-mac-mini 'chmod +x ~/.openclaw/bin/<name>-snipe.sh'

# Deploy plist (if not already on Mini)
scp ai.openclaw.<name>-snipe.plist dylans-mac-mini:~/Library/LaunchAgents/

# Load
ssh dylans-mac-mini 'launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.openclaw.<name>-snipe.plist'
```

### 5. Verify

```bash
ssh dylans-mac-mini 'launchctl list | grep snipe'
ssh dylans-mac-mini 'tail -20 ~/.openclaw/logs/<name>-snipe.log'
```

## Manage Running Snipes

### List active snipes
```bash
ssh dylans-mac-mini 'launchctl list | grep snipe'
```

### Check logs
```bash
ssh dylans-mac-mini 'tail -30 ~/.openclaw/logs/<name>-snipe.log'
```

### Stop a snipe
```bash
ssh dylans-mac-mini 'launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/ai.openclaw.<name>-snipe.plist'
```

### Clean up after target dates pass
```bash
ssh dylans-mac-mini 'launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/ai.openclaw.<name>-snipe.plist 2>/dev/null; rm ~/Library/LaunchAgents/ai.openclaw.<name>-snipe.plist ~/.openclaw/bin/<name>-snipe.sh'
```

## Critical Rules

1. **Always set `OP_SERVICE_ACCOUNT_TOKEN`** in the snipe script — without it, `op read` tries the 1Password desktop app and triggers GUI popups every run
2. **Always set `HOME` and `PATH`** in the plist `EnvironmentVariables` — LaunchAgents have minimal environment
3. **Use `--confirm` flag** with `opentable snipe` to actually book (without it, only reports matches)
4. **Self-remove on success** — add `launchctl bootout` after booking to stop the snipe
5. **Clean up when target dates pass** — snipes that outlive their dates waste cycles and may trigger 1Password popups
6. **iMessage notification target**: use group chat GUID for shared plans (e.g., `iMessage;+;chat7010feab69b14fa19071a88340495f2f` for date nights), or DM GUID for personal alerts
7. **Poll interval**: 1800s (30min) is a good default. For high-demand restaurants, consider 900s (15min). Don't go below 300s to avoid rate limiting.

## Resy Variant

For Resy restaurants, replace the snipe loop body:

```bash
for date in $DATES; do
    log "Checking $date..."

    avail=$(resy availability "$VENUE_ID" "$date" "$PARTY_SIZE" --json 2>&1)

    # Check for matching time slot
    match=$(echo "$avail" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for slot in data.get('results', {}).get('venues', [{}])[0].get('slots', []):
    t = slot.get('date', {}).get('start', '')
    if '$TARGET_TIME' in t:
        print(slot.get('config', {}).get('token', ''))
        break
" 2>/dev/null)

    if [[ -n "$match" ]]; then
        log "MATCH on $date — booking token: $match"
        resy book "$match" "$date" "$PARTY_SIZE" 2>&1 | tee -a "$LOG"
        # Notify + self-remove (same as OpenTable variant)
        exit 0
    fi

    log "No slots on $date"
done
```

## Known Restaurants

| Restaurant | Platform | ID | Notes |
|------------|----------|----|-------|
| Mahaniyom (Brookline) | OpenTable | 1267699 | Michelin Bib Gourmand, Thai |
