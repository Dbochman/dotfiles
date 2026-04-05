# MEMORY.md - Long-Term Memory

## People

**Family:**
- Dylan Bochman (primary contact, 781-354-4611)
- Julia (fiancée, 508-423-4853)
- Pets: dogs (Potato, Coconut), cats (Sopapilla, Burrito)

## Places

**Properties:**
- Cabin: 95 School House Rd, Phillipston, MA (my home base)
- West Roxbury: 19 Crosstown Ave, West Roxbury, MA 02132

## Key Information

**My Setup:**
- Hardware: Mac Mini at the cabin
- Connection: Starlink
- Primary channel: iMessage
- Capabilities: Text-to-speech, file access, home management

**Smart Home Equipment:**
- Philips Hue lights (both houses)
- iRobot Roombas
- Nest thermostats
- Google Nest cameras
- Google smart speakers

## Timeline

**2026-02-06:**
- Created and initialized
- Basic identity established as Claude Bochman
- Purpose: Help manage two properties and assist Dylan and Julia
- Learned about smart home devices in both houses
- Instructed to be proactive in reaching out to both Dylan and Julia separately

**2026-02-22:**
- Upgraded OpenClaw from 2026.2.14 → 2026.2.21 via npm
- Removed 👀 acknowledgment requirement from SOUL.md (Dylan's request)
- Pushed SOUL.md changes to GitHub (created openclaw-workspace repo)
- Learned about dotfiles structure: ~/.dotfiles contains Claude Code config, skills, commands, hooks

**2026-02-25 (approx):**
- Integration fixes applied by Dylan:
  - iMessage delivery fixed (operator.write scope added, gateway restarted PID 8747)
  - Nest thermostats confirmed online: Solarium 93.4°F, Living Room 69.5°F, Bedroom 69.5°F
  - Weekly activity report self-resolved (claude-sonnet-4-6 model ID now recognized)
  - catt reinstalled (v0.13.1) after broken venv from Python upgrade
  - Spotify default device set to "Dylan's Mac mini" as fallback
- Away routine run for cabin: lights off, speakers stopped, Roombas vacuumed and docked

## Active Systems & Automations

**Dating/Restaurants:**
- Monthly automated date night booking: Fridays at 7 PM, alternating cuisines (April-Dec 2026)
- Restaurant booking method: Resy (preferred, working); OpenTable has bot detection issues
- Preferences: Newton/Brookline area, vegetable-friendly options for Julia
- Past booking: Brassica Kitchen (2/14/2026, Valentine's Day)
- Upcoming: Juliet (3/20/2026, Resy ID: 62834)

**Music Queue (Andre):**
- Collaborative music queue API running on DigitalOcean (192.241.153.83)
- Preference mix: ambient/electronic/jazz/instrumental
- Active queue includes: Pretty Lights, Gramatik, Tycho, Japanese, jazz, Madlib/Madvillain

**Temperature Monitoring:**
- Nest thermostat snapshots every 30 min in `~/.openclaw/nest-history/` (JSONL format)
- Alert threshold: <40°F (immediate notification)
- 1000-day retention policy

**Spotify Connect:**
- Kitchen speaker: device ID `b8581271559fd61aa994726df743285c` (default volume: 100)
- Mac mini: device ID `0eb8b896cf741cd28d15b1ce52904ae7940e4aae`
- Default spogo device set to "Dylan's Mac mini" as fallback
- Google Home speakers only appear in Spotify Connect when actively playing — use `catt` to wake them into a cast session first
- catt v0.13.1 installed; Kitchen speaker @ 192.168.1.66, Bedroom speaker @ 192.168.1.163

**Calendar Management:**
- Julia's Monty Tech courses registered and on calendar (both Spring 2026):
  - Soups & Chowders: Mondays 3/02/2026, 6-9 PM, Room 433
  - Small Engine Repair: Wednesdays 3/04-4/15/2026, 5:30-8:30 PM, Room 735
  - Location: 1050 Westminster St, Fitchburg, MA (off Route 2A)
- Dylan invited to all Julia's class events

## Browser & Web Access

- Pinchtab CLI at /opt/homebrew/bin/pinchtab (headless Chrome control)
- OpenTable: bot detection persists (use Resy as workaround)

## Amazon Shopping

- Hard spending cap: $250, soft cap: $100 (flag if soft cap exceeded)
- Shipping address: 19 Crosstown Ave, West Roxbury, MA 02132
- Workflow: always provide order summary before requesting checkout approval

## Preferences & Notes

- Dylan: Proactive communication preferred, separate texts to him and Julia
- Julia: Prefers summaries first (TL;DR), then details
- Kitchen speaker: Max safe volume is 45 — never exceed this

## Infrastructure Changes

**2026-03-06:**
- Base model upgraded to **claude-opus-4-6** (from claude-sonnet-4-6)
- Cron jobs: 3 qd-booking payloads migrated from `gog calendar create` → `gws calendar events insert` (synced to Mini)
- Refresh script: Removed stale `GOG_KEYRING_PASSWORD`; added `BLUEBUBBLES_PASSWORD`, `CIELO_USERNAME`, `CIELO_PASSWORD` (synced to Mini)

## Dog Walk Automation (rewritten 2026-04-04)

**Architecture:** Fi GPS collar → departure detection → Roomba automation → multi-signal return monitoring. Replaced old Ring vision pipeline (removed Haiku video analysis, FindMy/Peekaboo, accumulator windows).

**Skills:** `dog-walk` (listener + automation), `fi-collar` (GPS/battery queries), `ring-doorbell` (Ring CLI, dings only now)

**Listener** (`ai.openclaw.dog-walk-listener` LaunchAgent, 1354 lines):
- Fi GPS polling every 3 min during walk hours (7AM-12PM, 12-5PM, 5-9PM)
- Departure: 2 consecutive Fi readings outside geofence, ≥3 min apart, both <10 min old
- Tracks `home_location` (last confirmed in-geofence home) as departure anchor
- Ring FCM push still used for ding alerts + return motion signal (not departure)

**Return Detection** (multi-signal, any one docks):
- Ring doorbell motion (event-driven)
- WiFi/ARP scan every 60s (Crosstown via MBP SSH, Cabin via Starlink gRPC)
- Fi GPS every 60s (Potato re-enters home geofence)
- 2-hour safety timeout fallback

**Route Tracking:**
- Per-walk route files: `~/.openclaw/dog-walk/routes/<location>/<YYYY-MM-DD>/<walk_id>.json`
- Includes distance_m, point_count, end_location, inter-home transit filtering
- Immutable walk_id + origin_location at departure

**Dashboard** (`ai.openclaw.dog-walk-dashboard` on port 8552):
- Leaflet maps with route polylines + heatmap layers
- Fi collar status (battery, GPS, activity)
- Roomba status (both houses — Crosstown via SSH/dorita980, Cabin via iRobot cloud)
- Walk log table, duration/signal/funnel charts
- Click-to-select walk routes on map

**State:** `~/.openclaw/dog-walk/state.json` + daily JSONL in `history/`

**Devices:** Crosstown doorbell (684794187, Ring Protect), Cabin doorbell (697442349, shared, no Ring Protect)

**Crosstown Roombas** (`crosstown-roomba` skill):
- Roomba Combo 10 Max + Scoomba J5 via dorita980 MQTT through MacBook Pro SSH

## Presence Detection Fix (2026-03-24)
- Fixed stale ARP entries causing false presence at Crosstown
- Crosstown scan now: delete tracked ARP entries → re-ping (layer 2) → read fresh ARP table
- Prevents "person left but ARP entry persists" false relocations
- Deployed to both machines

## FindMy Locate (deployed 2026-04-04)

**Skill:** `findmy-locate` — locates Dylan/Julia via Apple FindMy using Peekaboo keyboard automation + screenshot capture.

**Usage notes:**
- `findmy-locate dylan`, `findmy-locate julia`, `findmy-locate both`
- Returns JSON with screenshot path → read the image to interpret the map location
- Screenshots saved to `~/.openclaw/findmy-locate/` (no auto-cleanup, prune manually with `find -mtime +7`)
- Copy screenshot to workspace before using `image` tool (findmy-locate dir isn't in allowed paths)

**Sidebar order (fixed 2026-04-04):** Me (0) → Dylan (1) → Julia (2)
- Script positions are correct: Dylan=1, Julia=2
- `both` mode: single pass — Dylan first, then Julia (no re-open)
- If someone gets added/removed from FindMy, sidebar order may shift — check visually

**TCC requirements:**
- Peekaboo needs Screen Recording + Accessibility grants
- Grants must cover the process context OpenClaw runs under (not just Terminal.app)
- If capture fails with "Screen recording permission required", re-grant in System Settings → Privacy & Security → Screen Recording

**Integration:** Pairs well with `places` skill for follow-up queries ("what's near them", "how far from home")

## Todos / Backlog

- **Harden memory handling for untrusted sessions** — MEMORY.md is injected into all DM sessions including strangers. Plan: (1) split PII out of MEMORY.md into `memory/private.md` (not auto-injected), (2) add SOUL.md rule to go memory-blind with untrusted contacts, (3) longer-term: OpenClaw bootstrap hook to gate injection by session type.


## Key Contacts

- **Hamed Silatani** (hamed@uptimelabs.io): Security/incident response simulation workshops; draft reply pending in Gmail