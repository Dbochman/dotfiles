# GOG → GWS Cutover Plan

Migration from `gog` (gogcli) to `gws` (@googleworkspace/cli) for all Google Workspace operations.

## Status: In Progress

GWS skills written and deployed. Parallel cron jobs running for validation.

## Phase 1: GWS Setup (DONE)

- [x] Install `gws` on all 3 machines (local, Mac Mini, MacBook Pro)
- [x] Auth 4 accounts: dylanbochman, julia.joy.jennings, bochmanspam, clawdbotbochman
- [x] Sync encrypted credentials + `.encryption_key` to Mini and MBP
- [x] Write `gws-calendar` skill (`openclaw/skills/gws-calendar/SKILL.md`)
- [x] Write `gws-gmail` skill (`openclaw/skills/gws-gmail/SKILL.md`)
- [x] Document GWS in TOOLS.md, mark GOG as legacy

## Phase 2: Parallel Cron Jobs (IN PROGRESS)

New GWS-based cron jobs running alongside legacy GOG jobs for validation.

| Job | Legacy (GOG) | New (GWS) | Status |
|-----|-------------|-----------|--------|
| Julia Morning Triage | `d7184542` @ 7:00 AM ET | `gws-julia-morning-triage-0001` @ 7:10 AM ET | Validating (delivers to Dylan) |
| Julia Evening Cleanup | `3060d4f2` @ 8:00 PM ET | `gws-julia-evening-cleanup-0001` @ 8:10 PM ET | Validating (delivers to Dylan) |

### Validation Checklist

- [ ] GWS morning triage runs successfully (check Dylan's iMessage)
- [ ] GWS evening cleanup runs successfully (check Dylan's iMessage)
- [ ] Labels created/applied correctly on Julia's account
- [ ] Draft replies created correctly (check Julia's Gmail drafts)
- [ ] No auth errors over 3+ consecutive runs
- [ ] Output quality matches or exceeds legacy job output

### After Validation

1. Update GWS jobs: change delivery target from `dylanbochman@gmail.com` → `+15084234853` (Julia)
2. Update GWS jobs: change schedule from +10 min offset back to on-the-hour (`0 7` / `0 20`)
3. Disable legacy GOG jobs: set `"enabled": false` on `d7184542` and `3060d4f2`
4. Deploy updated `cron/jobs.json` to Mini

## Phase 3: Deploy GWS Skills to OpenClaw

Once cron jobs are validated, deploy the new skills so the agent uses `gws` for ad-hoc requests too.

- [ ] Copy `gws-gmail/SKILL.md` to Mini's OpenClaw skills directory
- [ ] Copy `gws-calendar/SKILL.md` to Mini's OpenClaw skills directory
- [ ] Verify agent picks up new skills (test with "check my email" or "what's on my calendar")
- [ ] Remove old `gmail/SKILL.md` and `calendar/SKILL.md` from Mini's skills directory

Note: Date night / double date cron jobs say "create a Google Calendar event" without referencing a specific CLI tool — the agent will naturally use whichever calendar skill is active. No cron job rewrite needed for these.

## Phase 4: Retire GOG

After GWS is confirmed working for all use cases.

- [ ] Remove legacy cron jobs (`d7184542`, `3060d4f2`) from `cron/jobs.json`
- [ ] Delete old skills: `openclaw/skills/gmail/SKILL.md`, `openclaw/skills/calendar/SKILL.md`
- [ ] Remove GOG section from `openclaw/workspace/TOOLS.md`
- [ ] Remove `GOG_KEYRING_PASSWORD` from `~/.openclaw/.secrets-cache` (no longer needed)
- [ ] Retire Claude Code skill: `.claude/skills/gog-keyring-headless/SKILL.md`
- [ ] Update `.claude/skills/openclaw-stale-session-and-identity-mismatch/SKILL.md` (remove gog references)
- [ ] Delete stale `openclaw/cron-jobs.json` (old copy of jobs file)
- [ ] Consider uninstalling `gog` binary from Mini (`npm uninstall -g gogcli`)
- [ ] Delete this file

## Key Differences: GOG vs GWS

| | GOG | GWS |
|---|---|---|
| CLI style | `gog gmail search "query" --account=X --json` | `gws gmail users messages list --params '{"userId":"me","q":"query"}' --account X` |
| Auth | File-based keyring, `GOG_KEYRING_PASSWORD` env var | AES-256-GCM encrypted, `.encryption_key` file |
| Label operations | By name: `--add="OpenClaw/Urgent"` | By ID: `--json '{"addLabelIds":["<id>"]}'` (must look up IDs first) |
| Draft replies | `--reply-to-message-id=<id>` flag | Raw RFC 2822 with `In-Reply-To` + `References` headers, base64url encoded |
| Logout | Per-account: `gog auth remove <email>` | **DANGER**: `gws auth logout` nukes ALL accounts unless `--account <email>` is specified |

## Rollback

If GWS jobs fail or auth breaks:
- Legacy GOG jobs are still running at original times — Julia's triage continues uninterrupted
- GOG credentials are independent of GWS — no cross-contamination
- Re-enable GOG jobs if they were disabled: set `"enabled": true` and redeploy
