# OpenClaw Workspace — Current State

_Last updated: 2026-03-08_

## Overview

OpenClaw is an AI agent ("Claude Bochman") running on a Mac Mini at the cabin in Phillipston, MA. It manages the Bochman household across two properties via iMessage, with integrations for smart home devices, email, calendar, and more.

## Infrastructure

| Component | Value |
|---|---|
| Version | v2026.3.7 |
| Model | `anthropic/claude-opus-4-6` (fallback: `anthropic/claude-sonnet-4-6`) |
| Hardware | Mac Mini (macOS 26.2.0) |
| Network | Starlink, Tailscale (`dylans-mac-mini`) |
| Gateway | `ws://127.0.0.1:18789` (local), `wss://dylans-mac-mini.tail3e55f9.ts.net` (Tailscale) |
| Browser | Headless Chrome, CDP port 18800 |
| Auth | Claude Max OAuth tokens (not pay-per-token API) |
| Secrets | 1Password service account via `~/.openclaw/.env-token`, cached in `~/.openclaw/.secrets-cache` |
| SIP | Disabled (required for BlueBubbles Private API) |

## Agent Configuration

| Setting | Value |
|---|---|
| Heartbeat | Every 12h (minimal — BB ping only) |
| Session reset | Daily at 4 AM, or after 120 min idle |
| Typing mode | `thinking` (shows typing while processing) |
| DM scope | `per-channel-peer` |
| Compaction | `safeguard` mode |
| Max concurrent | 4 agents, 8 subagents |
| Health check interval | Disabled (`channelHealthCheckMinutes: 0`) |

## Messaging Channel

| Setting | Value |
|---|---|
| Provider | BlueBubbles v1.9.9 |
| Server | `http://localhost:1234` |
| Private API | Enabled (port 45670) — reactions, typing, edit/unsend, effects |
| Proxy | `lan-url` (Cloudflare disabled) |
| iCloud | `clawdbotbochman@gmail.com` |
| SIP | Disabled (required for Private API) |
| Watchdog | BB watchdog handles stale detection (replaces gateway health check) |

## Workspace Files

All tracked in `dotfiles/openclaw/workspace/`. Files with PII use `${PLACEHOLDER}` syntax in dotfiles; Mini copies have real values.

| File | Purpose | Tracked | Notes |
|---|---|---|---|
| `SOUL.md` | Agent personality, trust model, social engineering defense | Yes | Placeholders for phone/email |
| `AGENTS.md` | Workspace conventions, memory management, group chat rules | Yes | No PII |
| `TOOLS.md` | Environment-specific device notes, BB Private API reference | Yes | Symlinked on Mini from dotfiles |
| `IDENTITY.md` | Agent name/role/persona | Yes | Placeholder for address |
| `USER.md` | Household members, pets, locations | Yes | Placeholders for phone/email/address |
| `HEARTBEAT.md` | Minimal heartbeat checklist (BB ping only) | Yes | No PII |
| `MEMORY.md` | Agent's curated long-term memory | No | Agent-managed, changes frequently |
| `memory/` | Daily memory files | No | Agent-managed |

## Trusted Contacts

| Name | Trust Level | Channels |
|---|---|---|
| Dylan Bochman | Full (all actions) | iMessage (email handle only — phone handle fails on this host) |
| Julia | Full (all actions) | iMessage (phone handle) |
| Everyone else | Chat only (no actions) | Unknown contacts trigger alert to Dylan |

## Skills (42 installed)

| Category | Skills |
|---|---|
| Smart Home | `hue-lights`, `cielo-ac`, `nest-thermostat`, `mysa-thermostat`, `samsung-tv`, `roomba`, `google-speakers`, `shortcuts` |
| Communication | `sag` (TTS), `reminders` |
| Google Workspace | `gws-shared`, `gws-gmail`, `gws-gmail-send`, `gws-gmail-triage`, `gws-calendar`, `gws-calendar-agenda`, `gws-calendar-insert`, `gws-drive`, `gws-drive-upload`, `gws-tasks` |
| GWS Recipes | `recipe-create-gmail-filter`, `recipe-create-vacation-responder`, `recipe-find-free-time`, `recipe-label-and-archive-emails`, `recipe-save-email-attachments` |
| Booking | `resy`, `opentable`, `amazon-shopping`, `grocery-reorder` |
| Presence/Location | `presence`, `places`, `cabin-routines`, `crosstown-routines`, `crosstown-network` |
| Utilities | `web-search`, `summarize`, `applescript`, `bluetooth`, `peekaboo` (cameras), `1password` |
| Music | `spotify-speakers`, `echonest` |

### Skill Changes Since March 3

- **Added (15):** `mysa-thermostat`, `grocery-reorder`, `resy`, `gws-calendar`, `gws-calendar-agenda`, `gws-calendar-insert`, `gws-drive`, `gws-drive-upload`, `gws-gmail`, `gws-gmail-send`, `gws-gmail-triage`, `gws-shared`, `gws-tasks`, `recipe-*` (5 recipes)
- **Removed (1):** `gmail` (replaced by `gws-gmail` suite), `calendar` (replaced by `gws-calendar` suite)
- **Net change:** 27 → 42

## Cron Jobs

### Recurring

| Job ID | Schedule (ET) | Delivers to | Purpose |
|---|---|---|---|
| `gws-julia-morning-briefing-0001` | 7 AM daily | Julia (BB) | Gmail triage, calendar preview, inbox cleanup, draft replies |
| `128c4ed0` (health check) | 9 AM & 9 PM daily | Dylan (BB) | BB, cron status, Nest, Cielo, Hue, Mysa (errors only) |
| `weekly-activity-report` | Sun 10 AM | Dylan (BB) | OpenClaw usage/activity summary |
| `weekly-upgrade-verify-0001` | Sun 9:15 AM | Dylan (BB) | Post-upgrade scope/health check, auto-fix scopes |
| `weekly-security-reminder` | Sun 6 PM | Dylan (BB) | Reminder to run security audit from laptop |

### One-Shot — Date Nights (delete after run)

| Job ID | Fires | Cuisine | Area |
|---|---|---|---|
| `datenight-apr-italian` | Apr 1 | Italian | Newton/Brookline (Resy) |
| `datenight-may-mediterranean` | May 1 | Mediterranean | Newton/Brookline |
| `datenight-jun-tapas` | Jun 1 | Spanish/Tapas | Newton/Brookline |
| `datenight-jul-japanese` | Jul 1 | Japanese/Asian | Newton/Brookline |
| `datenight-aug-farmtotable` | Aug 1 | Farm-to-Table | Newton/Brookline |
| `datenight-sep-steakhouse` | Sep 1 | American/Steakhouse | Newton/Brookline |
| `datenight-oct-indian` | Oct 1 | Indian | Newton/Brookline |
| `datenight-nov-american` | Nov 1 | Modern American | Newton/Brookline |
| `datenight-dec-upscale` | Dec 1 | Upscale/Special | Newton/Brookline |

All date nights: party of 2, ~7 PM Friday, book on Resy, create Calendar event, announce in group chat (chat-id 170).

### One-Shot — Double Dates (delete after run)

| Job ID | Fires | Cuisine | Notes |
|---|---|---|---|
| `doubledate-q2-apr-thai` | Apr 1 | Thai | Party of 4 (Dylan, Julia, Will, Ayesha) |
| `doubledate-q3-jul-korean` | Jul 1 | Korean | Brookline |
| `doubledate-q4-oct-mexican` | Oct 1 | Mexican | Brookline |
| `doubledate-q1-jan27-french` | Jan 2, 2027 | French | Brookline |

### One-Shot — Quarterly Group Dinners (delete after run)

| Job ID | Fires | Target Month | Restaurants |
|---|---|---|---|
| `qd-booking-2026-07-june15` | Jun 15 | July | Washington Sq Tavern, Iru, Barlette, Tres Gatos, etc. |
| `qd-booking-2026-10-sep15` | Sep 15 | October | Tres Gatos, Tonino, Clocktower, Ssaanjh, etc. |
| `qd-booking-2027-01-dec15` | Dec 15 | January 2027 | Akami Omakase, Maguro, Ssaanjh, etc. |

All group dinners: party of 4, ~6:30 PM Friday, book on Resy, create Calendar event on Julia's cal.

## LaunchAgents (14 active)

| Agent | Purpose | Type |
|---|---|---|
| `ai.openclaw.gateway` | Main gateway process | Long-running |
| `ai.openclaw.nest-dashboard` | Climate dashboard (port 8550) | Long-running |
| `ai.openclaw.usage-dashboard` | Usage dashboard (port 8551) | Long-running |
| `ai.openclaw.nest-snapshot` | Nest sensor data capture | Every 30 min |
| `ai.openclaw.usage-snapshot` | Anthropic API usage capture | Every 15 min |
| `ai.openclaw.weekly-upgrade` | Auto-upgrade OpenClaw | Sun 9 AM |
| `ai.openclaw.dotfiles-pull` | Sync dotfiles from GitHub | Periodic |
| `com.openclaw.bb-watchdog` | BlueBubbles health watchdog | Periodic |
| `com.openclaw.bb-lag-summary` | BB message lag reporting | Periodic |
| `com.openclaw.cielo-refresh` | Cielo AC token refresh | Every 30 min |
| `com.openclaw.bt-connect` | Bluetooth auto-connect | Periodic |
| `com.openclaw.poke-messages` | Keep Messages.app alive | Periodic |
| `com.openclaw.presence-cabin` | Cabin presence detection | Periodic |
| `com.openclaw.presence-receive` | Receive presence updates from Crosstown | Periodic |

### LaunchAgents Added Since March 3

- `ai.openclaw.usage-dashboard` — serves usage dashboard on port 8551
- `ai.openclaw.usage-snapshot` — captures Anthropic API usage every 15 min

### Remote LaunchAgent (MacBook Pro at Crosstown)

- `ai.openclaw.ccusage-push` — pushes Claude Code token usage to Mini via scp every 30 min

## Dashboards

### Climate Dashboard (port 8550)

- **Nest thermostat & sensor data** — temperature, humidity, HVAC mode, outside weather
- **Mysa baseboard heaters** — 3 devices (Cat Room, Basement door, Movie room) via mysotherm API
- **Cielo AC units** — 2 minisplits via Cielo Home API
- **Presence cards** — occupancy state per location (occupied/partial/vacant) with duration tracking
- **Charts** — 24h temperature history with outside overlay, blue band for heating, orange for cooling
- Auto-refreshes every 5 min

### Usage Dashboard (port 8551)

- **Claude Code token usage** — daily breakdown by model, input/output/cache tokens
- **Utilization gauges** — daily/weekly/monthly usage vs Claude Max limits
- **Multi-machine** — aggregates ccusage data from all machines via scp
- **Anthropic API usage** — OAuth-based usage from `api.anthropic.com/api/oauth/usage`
- History at `~/.openclaw/usage-history/YYYY-MM-DD.jsonl`

## Smart Home Integrations

| Device | Protocol | Notes |
|---|---|---|
| Nest thermostats | Google SDM API | Credentials cached at `~/.cache/nest-sdm/`, 55min token refresh |
| Mysa baseboard heaters (3) | AWS Cognito + REST | Read-only monitoring, auth refreshes every ~30 days |
| Cielo AC (2 minisplits) | `cielo-cli` + REST | Token refresh every 30min via LaunchAgent |
| Hue lights (8 rooms) | `hue` CLI | Entryway, Kitchen, Bedroom, Movie Room, Living room, Office, Cat Room, Downstairs, Master Bath |
| Samsung TV | SmartThings API | Via `samsung-tv` skill |
| Roomba (2 units) | Google Assistant API | Floomba + Philly at Cabin, via `roomba` skill |
| Spotify | `spogo` CLI | Google Home speakers need `catt` wake-up first |

## Presence Detection

- **Sticky/arrival model** — once detected at a location, stays until detected at the other
- **Locations:** Cabin (Phillipston), Crosstown (Newton)
- **States:** Occupied, Partial (one person present), Vacant
- **State duration tracking** — `stateChangedAt` timestamp persisted across scans, resets on state transitions
- **Detection methods:** Hostname match (mDNS), MAC address, IP address
- **Scan sources:** Cabin runs locally, Crosstown scan pushed from MacBook Pro via Tailscale

## Google Workspace (GWS)

- **CLI:** `gws` v0.4.4 (Rust binary, npm `@googleworkspace/cli`)
- **Accounts:** dylanbochman (default), julia.joy.jennings, bochmanspam, clawdbotbochman
- **Auth:** OAuth credentials at `~/.config/gws/`, requires local browser auth then scp to Mini
- **Skills split:** Former monolithic `gmail`/`calendar` skills replaced by granular `gws-*` suite (10 skills + 5 recipes)

## Weekly Upgrade Flow

Automated via `ai.openclaw.weekly-upgrade` LaunchAgent (Sun 9 AM ET) running `~/bin/openclaw-weekly-upgrade`:

1. Check current vs latest version (skip if already current)
2. Backup device files (`paired.json`, `pending.json`)
3. Backup LaunchAgent plist (npm install may overwrite it)
4. **Stop gateway** via `launchctl bootout` (prevents launchd restart mid-install)
5. `npm install -g openclaw@latest`
6. Restore plist from backup
7. Patch BB plugin if needed (broken `parse-finite-number` import in v2026.3.7+)
8. Restart gateway
9. Verify gateway is running, check for scope repair needs
10. Pull latest dotfiles (`git pull --ff-only`) so symlinked workspace files stay current

Post-upgrade verification: cron job `weekly-upgrade-verify-0001` runs at 9:15 AM, auto-fixes missing scopes, reports to Dylan via iMessage.

## Dotfiles Structure

```
openclaw/
├── workspace/           # Agent workspace files (SOUL.md, TOOLS.md, etc.)
│   └── scripts/         # Agent utility scripts (presence-detect.sh, etc.)
├── plans/               # Architecture docs and plans
├── bin/                 # Helper scripts (sag-wrapper, nest-dashboard.py, ccusage-push.sh, etc.)
├── skills/              # Skill definitions (tracked subset)
├── OpenClawGateway.app/ # Gateway wrapper app (for FDA/TCC)
├── openclaw.json        # Main config
├── openclaw-remote.json # Remote config
├── *.plist              # LaunchAgent definitions
└── dotfiles-pull.command
```

## Key Operational Notes

- **Syncing workspace files:** `scp` from local to Mini, then run placeholder substitution. SOUL.md on Mini is a real file (not symlink).
- **TOOLS.md on Mini:** Symlink → `~/dotfiles/openclaw/workspace/TOOLS.md`. Kept current by dotfiles-pull LaunchAgent and weekly upgrade auto-pull.
- **Gateway hot-reloads** workspace file changes without restart.
- **Manual cron trigger:** `openclaw cron run <job-id> --timeout 300000 --expect-final` (default 30s is too short).
- **BB restart API:** `GET /api/v1/server/restart/soft?password=$BLUEBUBBLES_PASSWORD` (note: GET, not POST).
- **Secrets:** Never use `op read` at gateway startup (hangs under launchd). Use cache-only pattern via `~/.openclaw/.secrets-cache`.
- **1Password SSH bypass:** Use `SSH_AUTH_SOCK=""` before scp/ssh in LaunchAgents to avoid biometric prompts (Tailscale SSH handles auth).
- **npm upgrade danger:** `npm install -g openclaw` may run `openclaw install --service` which overwrites the LaunchAgent plist. Weekly upgrade script backs up and restores plist.
- **Ghost cron jobs:** When removing jobs from `jobs.json`, also delete run state at `~/.openclaw/cron/runs/<job-id>.jsonl`.
