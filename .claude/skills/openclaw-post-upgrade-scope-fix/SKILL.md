---
name: openclaw-post-upgrade-scope-fix
description: |
  Fix OpenClaw cron delivery failing with "gateway closed (1008): pairing required"
  after an OpenClaw npm upgrade. Use when: (1) cron jobs complete their work but
  "cron announce delivery failed" on the iMessage/delivery step, (2) gateway logs
  show "pairing required" or code 1008 on WebSocket connections from cron subagents,
  (3) security audit logs show "scope-upgrade requested" with reason=scope-upgrade,
  (4) pending.json has an unresolved repair request. Root cause: new OpenClaw versions
  may require additional device scopes (e.g., operator.write) not present in existing
  paired device configs.
author: Claude Code
version: 1.0.0
date: 2026-02-24
---

# OpenClaw Post-Upgrade Scope Fix

## Problem
After upgrading OpenClaw (npm), cron jobs complete their work (e.g., Gmail triage)
but fail at the delivery step with `cron announce delivery failed`. The gateway
rejects the cron subagent's WebSocket connection with code 1008 ("pairing required").

The error message is misleading — it's not a pairing issue, it's a missing scope.

## Context / Trigger Conditions
- OpenClaw was recently upgraded via npm
- Cron jobs run successfully but delivery fails
- Gateway log shows: `gateway closed (1008): pairing required`
- Runtime logs show: `security audit: device access upgrade requested` with `reason=scope-upgrade`
- `~/.openclaw/devices/pending.json` contains a repair request with `"isRepair": true`

## Solution

### 1. Diagnose
```bash
ssh dbochman@dylans-mac-mini

# Check for pending scope repair requests
cat ~/.openclaw/devices/pending.json

# Check current device scopes
cat ~/.openclaw/devices/paired.json | jq '.[] | {scopes, tokenScopes: .tokens.operator.scopes}'

# Check runtime logs for scope-upgrade requests
grep "scope-upgrade" /tmp/openclaw/openclaw-$(date +%Y-%m-%d).log
```

### 2. Fix — Add Missing Scope
```bash
# Backup first
cp ~/.openclaw/devices/paired.json ~/.openclaw/devices/paired.json.bak
cp ~/.openclaw/devices/pending.json ~/.openclaw/devices/pending.json.bak

# Add the missing scope (e.g., operator.write) to BOTH arrays:
# 1. The device's top-level "scopes" array
# 2. The device's "tokens.operator.scopes" array
# Use jq or careful manual editing

# Example with jq (adjust device ID key as needed):
DEVICE_KEY=$(cat ~/.openclaw/devices/paired.json | jq -r 'keys[0]')
jq ".[\"$DEVICE_KEY\"].scopes += [\"operator.write\"] | .[\"$DEVICE_KEY\"].tokens.operator.scopes += [\"operator.write\"]" \
  ~/.openclaw/devices/paired.json > /tmp/paired-fixed.json && \
  mv /tmp/paired-fixed.json ~/.openclaw/devices/paired.json

# Clear the pending repair request
echo '{}' > ~/.openclaw/devices/pending.json
```

### 3. Restart Gateway
```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/ai.openclaw.gateway.plist
sleep 3
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.openclaw.gateway.plist
sleep 5

# Verify
launchctl list | grep openclaw.gateway
tail -20 ~/.openclaw/logs/gateway.log
```

### 4. Test Delivery
```bash
set -a && source ~/.openclaw/.secrets-cache && set +a
PATH=/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:$PATH \
  openclaw agent --to "+15084234853" --channel imessage --deliver --message "Test delivery"
```

## Verification
- Gateway log shows successful startup with no pairing errors
- `launchctl list | grep openclaw.gateway` shows a PID
- Manual `openclaw agent --deliver` sends iMessage successfully
- Next scheduled cron run completes delivery without `cron announce delivery failed`

## Key Files
| File | Purpose |
|------|---------|
| `~/.openclaw/devices/paired.json` | Device scopes and tokens |
| `~/.openclaw/devices/pending.json` | Pending scope-upgrade repair requests |
| `~/.openclaw/logs/gateway.log` | Gateway connection logs |
| `/tmp/openclaw/openclaw-YYYY-MM-DD.log` | Runtime logs with security audit entries |

## Notes
- The 2026.2.21 upgrade added `operator.write` as a required scope for cron announce delivery
- Future upgrades may add additional required scopes — always check `pending.json` after upgrades
- The gateway does NOT auto-approve scope upgrades; they require manual intervention
- Add this to your post-upgrade checklist: verify `paired.json` scopes, check `pending.json`
