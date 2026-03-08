# OpenClaw Weekly Upgrade Script

Automated weekly upgrade of the OpenClaw npm package on the Mac Mini, with safeguards against known post-upgrade breakage.

## Location

- **Dotfiles**: `openclaw/bin/openclaw-weekly-upgrade`
- **Mini**: `~/bin/openclaw-weekly-upgrade`
- **LaunchAgent**: `ai.openclaw.weekly-upgrade` (Sundays 4am ET)
- **Verify cron**: `weekly-upgrade-verify-0001` (9:15am ET, reports result via iMessage)
- **Log**: `~/.openclaw/logs/weekly-upgrade.log`

## Steps

| Step | Action | Why |
|------|--------|-----|
| 1 | Record current version | Baseline for rollback |
| 2 | Check latest npm version | Skip if already current |
| 3 | Backup `paired.json` + `pending.json` | npm install can reset device pairing |
| 4 | Backup LaunchAgent plist | npm install runs `openclaw install --service` which overwrites it |
| 5 | `npm install -g openclaw@latest` | The actual upgrade |
| 6 | Restore plist from backup | Overwritten plist lacks secrets-cache env vars (`BLUEBUBBLES_PASSWORD` etc.) |
| 6.5 | **Re-apply BB plugin patch** | See below |
| 7 | Restart gateway via launchctl | Pick up new version |
| 8 | Verify gateway PID | Catch crash-loop |
| 9 | Check pending scope repairs | New versions may require additional scopes |

## Step 6.5: BB Plugin Patch

**Problem**: OpenClaw v2026.3.7 introduced a broken import in `extensions/bluebubbles/src/monitor-normalize.ts`:

```typescript
import { parseFiniteNumber } from "../../../src/infra/parse-finite-number.js";
```

This path references a dev-only source file absent from the npm package. When the gateway loads, the BB extension fails silently — gateway logs `Unknown channel: bluebubbles` and all iMessage webhooks are dropped into the void.

**Patch**: The upgrade script replaces the broken import with an inline implementation:

```typescript
function parseFiniteNumber(v: unknown): number | undefined {
  const n = typeof v === "number" ? v : typeof v === "string" ? Number(v) : NaN;
  return Number.isFinite(n) ? n : undefined;
}
```

**Behavior**:
- If `monitor-normalize.ts` exists and contains `parse-finite-number` in any import path, applies the `sed` patch
- Verifies the patch took by re-checking for the broken import
- Logs `WARN:bb-patch-failed` if verification fails
- Skips gracefully if the file doesn't exist or the import is already fixed (future version fix)

## Log Status Codes

| Code | Meaning |
|------|---------|
| `SKIP:<ver>` | Already up to date |
| `OK:<old>:<new>` | Upgrade successful |
| `WARN:<old>:<new>:scope-repair-needed` | Upgrade OK but new scopes needed |
| `WARN:bb-patch-failed` | BB plugin patch didn't take |
| `FAIL:<old>:<new>:gateway-down` | Gateway won't start after upgrade |

## Known Dangers

1. **Plist overwrite**: `npm install -g openclaw` may run `openclaw install --service`, replacing the wrapper-based plist with one that has inline env vars missing secrets. The script backs up and restores the plist to prevent this.

2. **BB plugin import bug**: Fresh npm install restores the broken `monitor-normalize.ts`. The step 6.5 patch fixes this automatically.

3. **Scope changes**: New OpenClaw versions may require additional device scopes. Missing scopes cause `gateway closed (1008): pairing required` on cron delivery. The verify cron job at 9:15am checks and auto-fixes this.

## Deploying Changes

After editing the script locally in dotfiles:

```bash
scp openclaw/bin/openclaw-weekly-upgrade dylans-mac-mini:~/bin/openclaw-weekly-upgrade
ssh dylans-mac-mini 'chmod +x ~/bin/openclaw-weekly-upgrade'
```
