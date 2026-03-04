# OpenClaw Workspace — Current State

_Last updated: 2026-03-03_

## Overview

OpenClaw is an AI agent ("Claude Bochman") running on a Mac Mini at the cabin in Phillipston, MA. It manages the Bochman household across two properties via iMessage, with integrations for smart home devices, email, calendar, and more.

## Infrastructure

| Component | Value |
|---|---|
| Version | v2026.3.2-beta.1 |
| Model | `anthropic/claude-sonnet-4-6` |
| Hardware | Mac Mini (macOS 26.2.0) |
| Network | Starlink, Tailscale (`dylans-mac-mini`) |
| Gateway | `ws://127.0.0.1:18789` (local), `wss://dylans-mac-mini.tail3e55f9.ts.net` (Tailscale) |
| Browser | Headless Chrome, CDP port 18800 |
| Auth | Claude Max OAuth tokens (not pay-per-token API) |
| Secrets | 1Password service account via `~/.openclaw/.env-token`, cached in `~/.openclaw/.secrets-cache` |

## Agent Configuration

| Setting | Value |
|---|---|
| Heartbeat | Every 12h (minimal — BB ping only) |
| Session reset | Daily at 4 AM, or after 120 min idle |
| Typing mode | `thinking` (shows typing while processing) |
| DM scope | `per-channel-peer` |
| Compaction | `safeguard` mode |
| Max concurrent | 4 agents, 8 subagents |

## Messaging Channel

| Setting | Value |
|---|---|
| Provider | BlueBubbles v1.9.9 |
| Server | `http://localhost:1234` |
| Private API | Enabled (port 45670) |
| Proxy | `lan-url` (Cloudflare disabled) |
| iCloud | `clawdbotbochman@gmail.com` |
| SIP | Disabled (required for Private API) |

## Workspace Files

All tracked in `dotfiles/openclaw/workspace/`. Files with PII use `${PLACEHOLDER}` syntax in dotfiles; Mini copies have real values.

| File | Purpose | Tracked | Notes |
|---|---|---|---|
| `SOUL.md` | Agent personality, trust model, social engineering defense | Yes | Placeholders for phone/email |
| `AGENTS.md` | Workspace conventions, memory management, group chat rules | Yes | No PII |
| `TOOLS.md` | Environment-specific device notes, BB Private API reference | Yes | Placeholders for BB password |
| `IDENTITY.md` | Agent name/role/persona | Yes | Placeholder for address |
| `USER.md` | Household members, pets, locations | Yes | Placeholders for phone/email/address |
| `HEARTBEAT.md` | Minimal heartbeat checklist (BB ping only) | Yes | No PII |
| `MEMORY.md` | Agent's curated long-term memory | No | Agent-managed, changes frequently |
| `BOOTSTRAP.md` | One-time onboarding script | No | Should be deleted after first run |
| `memory/` | Daily memory files | No | Agent-managed |

## Trusted Contacts

| Name | Trust Level | Channels |
|---|---|---|
| Dylan Bochman | Full (all actions) | iMessage (email handle only — phone handle fails on this host) |
| Julia | Full (all actions) | iMessage (phone handle) |
| Everyone else | Chat only (no actions) | Unknown contacts trigger alert to Dylan |

## Skills (27 installed)

| Category | Skills |
|---|---|
| Smart Home | `hue-lights`, `cielo-ac`, `nest-thermostat`, `samsung-tv`, `roomba`, `google-speakers`, `shortcuts` |
| Communication | `gmail`, `calendar`, `reminders`, `sag` (TTS) |
| Booking | `resy`, `opentable`, `amazon-shopping` |
| Presence/Location | `presence`, `places`, `cabin-routines`, `crosstown-routines`, `crosstown-network` |
| Utilities | `web-search`, `summarize`, `applescript`, `bluetooth`, `peekaboo` (cameras), `1password` |
| Music | `spotify-speakers`, `echonest` |

## Cron Jobs

### Recurring

| Job | Schedule (ET) | Delivers to | Purpose |
|---|---|---|---|
| Julia Gmail Morning Triage | 7 AM daily | Julia (BB) | Inbox triage, flagging, organization |
| Julia Gmail Evening Cleanup | 8 PM daily | Julia (BB) | Archive old emails, evening digest |
| System Health Check | 9 AM & 9 PM daily | Dylan (BB) | BB, cron status, Nest, Cielo, Hue (errors only) |
| Weekly Activity Report | Sun 10 AM | Dylan (iMsg) | OpenClaw usage/activity summary |
| Weekly Upgrade Verification | Sun 9:15 AM | Dylan (iMsg) | Post-upgrade scope/health check |

### One-Shot (delete after run)

| Job | Scheduled | Purpose |
|---|---|---|
| Date Night — April (Italian) | Apr 1 | Book via OpenTable |
| Date Night — May (Mediterranean) | May 1 | Book via OpenTable |
| Date Night — June (Spanish/Tapas) | Jun 1 | Book via OpenTable |
| Date Night — July (Japanese/Asian) | Jul 1 | Book via OpenTable |
| Date Night — August (Farm-to-Table) | Aug 1 | Book via OpenTable |
| Date Night — September (American/Steakhouse) | Sep 1 | Book via OpenTable |
| Date Night — October (Indian) | Oct 1 | Book via OpenTable |
| Date Night — November (Modern American) | Nov 1 | Book via OpenTable |
| Date Night — December (Upscale/Special) | Dec 1 | Book via OpenTable |
| Group Dinner — July | Jun 15 | Book quarterly group dinner |
| Group Dinner — October | Sep 15 | Book quarterly group dinner |
| Group Dinner — January 2027 | Dec 15 | Book quarterly group dinner |

## LaunchAgents

| Agent | Prefix | Purpose | Type |
|---|---|---|---|
| `ai.openclaw.gateway` | ai | Main gateway process | Long-running |
| `ai.openclaw.nest-dashboard` | ai | Nest temp dashboard (port 8550) | Long-running |
| `ai.openclaw.nest-snapshot` | ai | Nest sensor data capture | Every 30 min |
| `ai.openclaw.weekly-upgrade` | ai | Auto-upgrade OpenClaw | Sun 9 AM |
| `ai.openclaw.dotfiles-pull` | ai | Sync dotfiles from GitHub | Periodic |
| `ai.openclaw.imessage-group-sync` | ai | Sync iMessage group metadata | Periodic |
| `com.openclaw.bb-watchdog` | com | BlueBubbles health watchdog | Periodic |
| `com.openclaw.bb-lag-summary` | com | BB message lag reporting | Periodic |
| `com.openclaw.cielo-refresh` | com | Cielo AC token refresh | Every 30 min |
| `com.openclaw.bt-connect` | com | Bluetooth auto-connect | Periodic |
| `com.openclaw.poke-messages` | com | Keep Messages.app alive | Periodic |
| `com.openclaw.presence-cabin` | com | Cabin presence detection | Periodic |
| `com.openclaw.presence-receive` | com | Receive presence updates | Periodic |

## Dotfiles Structure

```
openclaw/
├── workspace/           # Agent workspace files (SOUL.md, TOOLS.md, etc.)
│   └── scripts/         # Agent utility scripts
├── plans/               # Architecture docs and plans
├── bin/                 # Helper scripts (sag-wrapper, openclaw-refresh-secrets)
├── cron/                # Cron job definitions
├── skills/              # Skill definitions (not yet tracked)
├── OpenClawGateway.app/ # Gateway wrapper app (for FDA/TCC)
├── openclaw.json        # Main config
├── openclaw-remote.json # Remote config
├── cron-jobs.json       # Cron job definitions
├── git-sync.sh          # Workspace/dotfiles sync script
├── sync-cron-jobs.sh    # Cron job sync script
├── *.plist              # LaunchAgent definitions
└── dotfiles-pull.command
```

## Key Operational Notes

- **Syncing workspace files:** `scp` from local to Mini, then run placeholder substitution (Python script). SOUL.md on Mini is a real file (not symlink) since `sed -i` doesn't work on symlinks.
- **TOOLS.md on Mini:** Still a symlink → `~/dotfiles/openclaw/workspace/TOOLS.md` (no PII substitution needed since it uses env vars at runtime).
- **Gateway hot-reloads** workspace file changes without restart.
- **Manual cron trigger:** `openclaw agent --to "<target>" --channel imessage --deliver --message "<prompt>"` (the `openclaw cron run` CLI often times out).
- **BB restart API:** `GET /api/v1/server/restart/soft?password=$BLUEBUBBLES_PASSWORD` (note: GET, not POST).
- **Secrets:** Never use `op read` at gateway startup (hangs under launchd). Use cache-only pattern via `~/.openclaw/.secrets-cache`.
