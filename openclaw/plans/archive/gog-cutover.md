# GOG → GWS Cutover Plan

Migration from `gog` (gogcli) to `gws` (@googleworkspace/cli) for all Google Workspace operations.

## Status: COMPLETE

All phases done. GOG fully retired. GWS is the sole Google Workspace CLI.

## Phase 1: GWS Setup (DONE)

- [x] Install `gws` on all 3 machines (local, Mac Mini, MacBook Pro)
- [x] Auth 4 accounts: dylanbochman, julia.joy.jennings, bochmanspam, clawdbotbochman
- [x] Sync encrypted credentials + `.encryption_key` to Mini and MBP
- [x] Write `gws-calendar` skill (`openclaw/skills/gws-calendar/SKILL.md`)
- [x] Write `gws-gmail` skill (`openclaw/skills/gws-gmail/SKILL.md`)
- [x] Document GWS in TOOLS.md, mark GOG as legacy

## Phase 2: Cron Job Cutover (DONE)

Legacy GOG jobs and GWS validation jobs replaced with a single consolidated morning briefing.

| Old Jobs (removed) | New Job | Status |
|-----|---------|--------|
| `d7184542` Morning Triage (GOG) | `gws-julia-morning-briefing-0001` @ 7:00 AM ET | Live, delivers to Julia |
| `3060d4f2` Evening Cleanup (GOG) | _(merged into morning briefing)_ | Removed |
| `gws-julia-morning-triage-0001` (validation) | _(replaced)_ | Removed |
| `gws-julia-evening-cleanup-0001` (validation) | _(replaced)_ | Removed |

The new morning briefing combines: calendar preview + inbox triage + cleanup + spam removal.
Delivers to Julia (`+1XXXXXXXXXX`) via BlueBubbles at 7:00 AM ET daily.

## Phase 3: Deploy GWS Skills to OpenClaw (DONE)

All GWS skills deployed to Mini's OpenClaw skills directory (`~/.openclaw/skills/`).

### Custom Skills (3)

- [x] `gws-gmail/SKILL.md` — full Gmail API (search, send, draft, label, threads)
- [x] `gws-calendar/SKILL.md` — full Calendar API (events, free/busy, RRULE, Meet)
- [x] `gws-drive/SKILL.md` — full Drive API (search, upload, download, share, permissions)

### Upstream Helper Skills (7, from `gws generate-skills`)

- [x] `gws-shared/SKILL.md` — auth, global flags, output formatting (customized with our accounts)
- [x] `gws-calendar-agenda/SKILL.md` — `gws calendar +agenda --today/--week`
- [x] `gws-calendar-insert/SKILL.md` — `gws calendar +insert --summary ... --start ... --end ...`
- [x] `gws-gmail-triage/SKILL.md` — `gws gmail +triage` (read-only inbox summary)
- [x] `gws-gmail-send/SKILL.md` — `gws gmail +send --to ... --subject ... --body ...`
- [x] `gws-drive-upload/SKILL.md` — `gws drive +upload file.pdf --parent FOLDER_ID`
- [x] `gws-tasks/SKILL.md` — full Google Tasks API (tasklists + tasks)

### Upstream Recipe Skills (5)

- [x] `recipe-find-free-time/SKILL.md` — free/busy query across calendars
- [x] `recipe-create-vacation-responder/SKILL.md` — Gmail auto-reply setup/teardown
- [x] `recipe-save-email-attachments/SKILL.md` — Gmail attachments → Drive folder
- [x] `recipe-label-and-archive-emails/SKILL.md` — batch label + archive workflow
- [x] `recipe-create-gmail-filter/SKILL.md` — automated inbox filter creation

### Remaining

- [ ] Verify agent picks up new skills (test with "check my email" or "what's on my calendar")
- [x] Remove old `gmail/SKILL.md` and `calendar/SKILL.md` from Mini's skills directory

Note: Date night / double date cron jobs say "create a Google Calendar event" without referencing a specific CLI tool — the agent will naturally use whichever calendar skill is active. No cron job rewrite needed for these.

## Phase 4: Retire GOG

After GWS is confirmed working for all use cases.

- [x] Remove legacy cron jobs (`d7184542`, `3060d4f2`) from `cron/jobs.json`
- [x] Remove GWS validation jobs (`gws-julia-morning-triage-0001`, `gws-julia-evening-cleanup-0001`)
- [x] Delete old skills: `openclaw/skills/gmail/SKILL.md`, `openclaw/skills/calendar/SKILL.md`
- [x] Remove GOG section from `openclaw/workspace/TOOLS.md`
- [x] Remove `GOG_KEYRING_PASSWORD` from `~/.openclaw/.secrets-cache`
- [x] Retire Claude Code skill: `.claude/skills/gog-keyring-headless/SKILL.md`
- [x] Update `.claude/skills/openclaw-stale-session-and-identity-mismatch/SKILL.md` (rewritten for GWS)
- [x] Reconcile cron job files — merged into `openclaw/cron/jobs.json` (single source), deleted `openclaw/cron-jobs.json`
- [x] Uninstall `gog` binary from Mini (`brew uninstall gogcli`)

## Key Differences: GOG vs GWS

| | GOG | GWS |
|---|---|---|
| CLI style | `gog gmail search "query" --account=X --json` | `gws gmail users messages list --params '{"userId":"me","q":"query"}' --account X` |
| Auth | File-based keyring, `GOG_KEYRING_PASSWORD` env var | AES-256-GCM encrypted, `.encryption_key` file |
| Label operations | By name: `--add="OpenClaw/Urgent"` | By ID: `--json '{"addLabelIds":["<id>"]}'` (must look up IDs first) |
| Draft replies | `--reply-to-message-id=<id>` flag | Raw RFC 2822 with `In-Reply-To` + `References` headers, base64url encoded |
| Logout | Per-account: `gog auth remove <email>` | **DANGER**: `gws auth logout` nukes ALL accounts unless `--account <email>` is specified |

## Rollback

If GWS morning briefing fails or auth breaks:
- Re-create legacy GOG jobs from git history (commit `caf51c7` has the last version with GOG jobs)
- GOG credentials are independent of GWS — no cross-contamination
- GOG binary is still installed on Mini until Phase 4 cleanup
