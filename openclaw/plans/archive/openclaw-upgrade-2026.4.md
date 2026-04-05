# OpenClaw Upgrade Plan: v2026.2.21 → v2026.4.2

## Status: COMPLETED (2026-04-04)

**Result**: Clean upgrade. Plist survived, gateway healthy, BB plugin loaded, cron `--tools exec` confirmed working. Found dual-install issue (npm global prefix vs node@22 keg) — fixed with `--prefix /opt/homebrew/opt/node@22`.

## Scope

40+ releases spanning Feb 22 → Apr 2, 2026. This is the largest version gap since initial install. Includes 3 known breaking-change releases (v2026.2.25, v2026.3.2, v2026.3.31) plus a packaging regression (v2026.3.24).

---

## Breaking Changes (in upgrade order)

### 1. v2026.2.25 — Exec approval config restructured
- Exec approval configuration structure changed
- Subagent allowlists, skills/workspace inheritance, session-id resolution changed
- **Impact**: May affect how OpenClaw resolves tool permissions in cron jobs
- **Fix**: `openclaw doctor --fix` should migrate

### 2. v2026.3.2 — Default tools profile + SecretRef + plugin HTTP API
- New installs default `tools.profile` to `"messaging"` (no broad coding/system tools)
- **Impact**: Existing config should be unaffected (we set tools explicitly), but verify `tools.profile` isn't reset
- `acp.dispatch.enabled` now defaults to `true`
- Plugin HTTP handler API changed from `api.registerHttpHandler(...)` to `api.registerHttpRoute({ path, auth, match, handler })`
- **Impact**: Shouldn't affect us (no custom plugins), but BB plugin could be affected if it uses this API
- SecretRef support across 64 credential surfaces — **good news**, this is the fix we need for skill secrets

### 3. v2026.3.24 — DANGEROUS: Packaging regression
- `npm install -g` overwrites `dist/` in-place while gateway is running → crash loop
- Missing `dist/package.json` for memory-lancedb plugin
- ~40 plugin ID mismatch warnings (cosmetic)
- Missing AGENTS.md template
- **Impact**: HIGH — must stop gateway before upgrade, not after
- **Fix**: v2026.3.28+ fixes this, but we'll go straight to v2026.4.2

### 4. v2026.3.31 — Legacy config aliases removed + gateway auth hardening
- **Removed config aliases**: `talk.voiceId`, `talk.apiKey`, `agents.*.sandbox.perSession`, `browser.ssrfPolicy.allowPrivateNetwork`, `hooks.internal.handlers`, channel/group/room `allow` toggles (replaced by `enabled`)
- **Impact**: Need to check our `openclaw.json` for any of these legacy keys
- `trusted-proxy` rejects mixed shared-token configs; local-direct fallback requires configured token
- Gateway node commands disabled until node pairing approval
- Skills/plugins install: dangerous-code findings now fail closed by default
- **Impact**: May affect how skills are deployed. Verify after upgrade.

### 5. v2026.4.2 — Plugin config paths moved
- xAI web search config: `tools.web.x_search.*` → `plugins.entries.xai.config.xSearch.*`
- Firecrawl web fetch: `tools.web.fetch.firecrawl.*` → `plugins.entries.firecrawl.config.webFetch.*`
- **Impact**: We don't use xAI or Firecrawl, so likely no-op. `openclaw doctor --fix` migrates.

---

## Key Improvements We Want

### Cron per-job tool allowlists (v2026.4.1)
- `openclaw cron --tools exec,read` to specify which tools a cron job can use
- **This may unblock the 8sleep morning briefing** — add `--tools exec` to briefing jobs
- Also: approval resolution from effective host fallback policy for cron exec

### Cron reliability fixes
- v2026.3.7: Replays interrupted recurring jobs on first gateway restart (not second)
- v2026.3.8 fix (PR #43053): Scheduler timer starts before catch-up, preventing inert scheduler
- v2026.3.31-beta.1: Stale cron runs and CLI tasks marked lost on first restart
- `cron.sessionRetention` (default 24h) prunes isolated run-session entries

### Memory/dreaming (v2026.3.31, experimental)
- Weighted recall, managed modes (`off|core|rem|deep`), `/dreaming` command
- Multilingual tagging, Dreams UI

### Task flows (v2026.3.31)
- Background tasks unified control plane
- `/tasks` chat command (v2026.4.1) for session task board
- `openclaw flows list|show|cancel`

### MCP remote HTTP/SSE server support (v2026.3.31)
- `mcp.servers` now supports URL configs for remote MCP servers

### Browser CDP fix (v2026.4.1)
- Screenshot parameter fixed for Chrome 146+ — may fix `existing-session` driver

---

## Known Gotchas from Our Setup

### 1. LaunchAgent plist overwrite (CRITICAL)
`npm install -g openclaw` may run `openclaw install --service` which overwrites our custom gateway plist. Our plist uses the wrapper-based secrets pattern with `OpenClawGateway.app`.

**Mitigation**: Backup plist before upgrade, restore after.

### 2. Gateway must be stopped BEFORE npm install
v2026.3.24 packaging regression showed that `npm install -g` replaces `dist/` in-place. If gateway is running, it crashes on missing hashed modules and launchd throttles restarts.

**Mitigation**: `launchctl unload` gateway plist first.

### 3. Device pairing scopes
New versions may require additional scopes in `~/.openclaw/devices/paired.json`. Missing scopes cause `gateway closed (1008): pairing required` on cron delivery.

**Mitigation**: Check paired.json after upgrade, add any new scopes.

### 4. BB plugin compatibility
BB plugin import bug was fixed in v2026.3.11, so our manual patch is no longer needed. But v2026.3.31 changed plugin SDK internals — verify BB plugin still loads.

### 5. `openclaw update` vs `npm install -g`
`openclaw update` is now the recommended method — it auto-detects install type, runs `openclaw doctor`, and restarts gateway. However, our custom plist/wrapper setup may conflict. Test with `--dry-run` first.

---

## Implementation Steps

### Pre-flight (before touching anything)

```bash
# 1. Backup current state
ssh dylans-mac-mini '
  cp ~/.openclaw/openclaw.json ~/.openclaw/openclaw.json.pre-upgrade
  cp ~/Library/LaunchAgents/ai.openclaw.gateway.plist ~/Library/LaunchAgents/ai.openclaw.gateway.plist.pre-upgrade
  cp ~/.openclaw/devices/paired.json ~/.openclaw/devices/paired.json.pre-upgrade
  cp -r ~/.openclaw/cron/jobs.json ~/.openclaw/cron/jobs.json.pre-upgrade
  echo "Backups created"
'

# 2. Check for legacy config keys that will break
ssh dylans-mac-mini '
  python3 -c "
import json
with open(\"/Users/dbochman/.openclaw/openclaw.json\") as f:
    cfg = json.load(f)
legacy = [\"talk.voiceId\", \"talk.apiKey\", \"hooks.internal.handlers\"]
# Check nested keys
import json
print(json.dumps(cfg, indent=2))
" | grep -E "voiceId|perSession|allowPrivateNetwork|hooks.*internal.*handlers" || echo "No legacy keys found"
'

# 3. Record current cron job state
ssh dylans-mac-mini '
  PATH=/opt/homebrew/opt/node@22/bin:$HOME/.openclaw/bin:$PATH
  set -a && source ~/.openclaw/.secrets-cache && set +a
  openclaw cron list 2>/dev/null
'
```

### Upgrade

```bash
# 4. Stop gateway (MUST be before npm install)
ssh dylans-mac-mini '
  launchctl unload ~/Library/LaunchAgents/ai.openclaw.gateway.plist
  sleep 3
  pgrep -f "openclaw" | head -5 && echo "WARNING: openclaw processes still running" || echo "Gateway stopped"
'

# 5. Install new version
ssh dylans-mac-mini '
  PATH=/opt/homebrew/opt/node@22/bin:$PATH
  npm install -g openclaw@2026.4.2
'

# 6. Restore gateway plist if overwritten
ssh dylans-mac-mini '
  diff ~/Library/LaunchAgents/ai.openclaw.gateway.plist ~/Library/LaunchAgents/ai.openclaw.gateway.plist.pre-upgrade >/dev/null 2>&1
  if [ $? -ne 0 ]; then
    echo "PLIST WAS OVERWRITTEN — restoring"
    cp ~/Library/LaunchAgents/ai.openclaw.gateway.plist.pre-upgrade ~/Library/LaunchAgents/ai.openclaw.gateway.plist
  else
    echo "Plist unchanged"
  fi
'

# 7. Run doctor to migrate config
ssh dylans-mac-mini '
  PATH=/opt/homebrew/opt/node@22/bin:$HOME/.openclaw/bin:$PATH
  set -a && source ~/.openclaw/.secrets-cache && set +a
  openclaw doctor --fix 2>&1
'

# 8. Check paired.json for new required scopes
ssh dylans-mac-mini '
  python3 -c "
import json
with open(\"/Users/dbochman/.openclaw/devices/paired.json\") as f:
    d = json.load(f)
print(\"Current scopes:\", json.dumps(d.get(\"scopes\", []), indent=2))
"
'

# 9. Start gateway
ssh dylans-mac-mini '
  launchctl load ~/Library/LaunchAgents/ai.openclaw.gateway.plist
  sleep 5
  launchctl list | grep ai.openclaw.gateway
'
```

### Post-upgrade verification

```bash
# 10. Check version
ssh dylans-mac-mini '
  PATH=/opt/homebrew/opt/node@22/bin:$PATH openclaw --version
'

# 11. Check gateway health
ssh dylans-mac-mini '
  PATH=/opt/homebrew/opt/node@22/bin:$HOME/.openclaw/bin:$PATH
  set -a && source ~/.openclaw/.secrets-cache && set +a
  openclaw health 2>/dev/null || echo "health command not available, check gateway log"
  tail -20 ~/.openclaw/logs/gateway.log
'

# 12. Verify BB plugin loaded
ssh dylans-mac-mini '
  tail -50 ~/.openclaw/logs/gateway.log | grep -i "bluebubbles\|BB\|plugin"
'

# 13. Verify cron jobs running
ssh dylans-mac-mini '
  PATH=/opt/homebrew/opt/node@22/bin:$HOME/.openclaw/bin:$PATH
  set -a && source ~/.openclaw/.secrets-cache && set +a
  openclaw cron list 2>/dev/null
'

# 14. Test iMessage delivery
ssh dylans-mac-mini '
  # Send a test message through the gateway (not BB API directly)
  # This validates: gateway → BB plugin → BB → iMessage
  echo "Test after upgrade verification"
'

# 15. Verify skills still load
ssh dylans-mac-mini '
  tail -50 ~/.openclaw/logs/gateway.log | grep -i "skill\|loaded"
'
```

### 8sleep briefing test (after upgrade)

```bash
# 16. Test per-job tool allowlist for morning briefing
# First, check if --tools flag is available
ssh dylans-mac-mini '
  PATH=/opt/homebrew/opt/node@22/bin:$HOME/.openclaw/bin:$PATH
  set -a && source ~/.openclaw/.secrets-cache && set +a
  openclaw cron --help 2>&1 | grep -i "tools"
'

# 17. If available, test with a one-off cron run
# Add --tools exec to the briefing job, run manually, check if 8sleep data appears
```

---

## Rollback

If gateway won't start or critical features break:

```bash
ssh dylans-mac-mini '
  launchctl unload ~/Library/LaunchAgents/ai.openclaw.gateway.plist 2>/dev/null
  PATH=/opt/homebrew/opt/node@22/bin:$PATH npm install -g openclaw@2026.2.21
  cp ~/.openclaw/openclaw.json.pre-upgrade ~/.openclaw/openclaw.json
  cp ~/Library/LaunchAgents/ai.openclaw.gateway.plist.pre-upgrade ~/Library/LaunchAgents/ai.openclaw.gateway.plist
  cp ~/.openclaw/devices/paired.json.pre-upgrade ~/.openclaw/devices/paired.json
  launchctl load ~/Library/LaunchAgents/ai.openclaw.gateway.plist
  sleep 5
  launchctl list | grep ai.openclaw.gateway
'
```

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Plist overwritten | HIGH | Gateway crash loop | Backup + restore (step 6) |
| Gateway won't start | MEDIUM | No agent | Doctor --fix + check logs |
| Cron jobs break | MEDIUM | Missed briefings | Manual trigger + verify |
| BB plugin fails | LOW | No iMessage | Check gateway log, restart |
| Skills rejected | LOW | Degraded functionality | Check `fs.realpathSync` path |
| Scope mismatch | MEDIUM | Cron delivery fails | Check paired.json (step 8) |

**Overall**: Medium risk. The biggest concern is the plist overwrite and the packaging regression path (mitigated by stopping gateway first). The cron scheduler fixes alone make this upgrade worthwhile.

---

## Sources

- [OpenClaw Updating Docs](https://docs.openclaw.ai/install/updating)
- [OpenClaw Cron Jobs Docs](https://docs.openclaw.ai/automation/cron-jobs)
- [GitHub CHANGELOG](https://github.com/openclaw/openclaw/blob/main/CHANGELOG.md)
- [v2026.3.24 Gateway Crash Issue](https://github.com/openclaw/openclaw/issues/54790)
- [Cron Jobs Broken in v2026.3.8 Issue](https://github.com/openclaw/openclaw/issues/42883)
- [v2026.3.23-2 Upgrade Checklist](https://www.clawly.org/news/openclaw-2026323-2-plugin-sdk-stabilization-and-upgrade-checklist-for-self-hosters)
- [OpenClaw npm versions](https://www.npmjs.com/package/openclaw?activeTab=versions)
