# BlueBubbles Implementation: Current State

**As of:** March 10, 2026 (EST)
**Scope:** OpenClaw + BlueBubbles integration on `dylans-mac-mini`

## 1. High-level architecture

BlueBubbles is the iMessage transport for OpenClaw channel `bluebubbles`.

Data path:
1. OpenClaw gateway runs locally on the Mac Mini (`127.0.0.1:18789`)
2. OpenClaw BlueBubbles provider talks to BlueBubbles server (`http://localhost:1234`)
3. BlueBubbles emits webhooks to OpenClaw (`/bluebubbles-webhook`)
4. Remote clients connect to OpenClaw gateway over Tailscale Serve (`wss://dylans-mac-mini.tail3e55f9.ts.net`)

## 2. Key config in dotfiles

### OpenClaw channel config
File: `openclaw/openclaw.json`

Important keys:
- `channels.bluebubbles.enabled: true`
- `channels.bluebubbles.serverUrl: "http://localhost:1234"`
- `channels.bluebubbles.webhookPath: "/bluebubbles-webhook"`
- `channels.bluebubbles.password: "${BLUEBUBBLES_PASSWORD}"`
- `channels.bluebubbles.sendReadReceipts: true`
- `session.typingMode: "thinking"` (sends typing indicator while processing)
- `channels.bluebubbles.actions`: all set to `true` (reactions, edit, unsend, reply, effects, group mgmt)

### Gateway service
Files:
- `openclaw/ai.openclaw.gateway.plist`
- `openclaw/OpenClawGateway.app/Contents/MacOS/OpenClawGateway`

Behavior:
- Gateway loads secrets from `~/.openclaw/.secrets-cache`
- Launches OpenClaw gateway process with port `18789`
- Keeps service alive via LaunchAgent `ai.openclaw.gateway`
- Gateway hot-reloads config changes without restart
- Entry point: `dist/entry.js` (changed from `dist/index.js` as of v2026.3.2)

### BlueBubbles watchdog
Files:
- `openclaw/com.openclaw.bb-watchdog.plist`
- `openclaw/workspace/scripts/bb-watchdog.sh`
- `openclaw/com.openclaw.poke-messages.plist`
- `openclaw/workspace/scripts/poke-messages.scpt`
- `openclaw/com.openclaw.bb-lag-summary.plist`
- `openclaw/workspace/scripts/bb-lag-summary.sh`

## 3. Current state (March 10, 2026)

- SIP: **disabled** (required for Private API, done March 2)
- BlueBubbles server version: `1.9.9`
- `private_api: true`
- `helper_connected: true`
- `proxy_service: lan-url` (Cloudflare disabled March 3)
- Private API port: `45670` (raw IPC socket for Messages.app, NOT HTTP)
- Gateway health monitor: **disabled** (`channelHealthCheckMinutes: 0`)
- All Private API features operational: typing, read receipts, reactions, edit, unsend, effects, group management

## 4. Watchdog detection modes

The watchdog (`bb-watchdog.sh`) runs every 60 seconds and detects four distinct failure modes:

### A) Private API helper disconnected

**Symptom:** Messages marked as read (standard API), but OpenClaw can't send responses.

**Detection:** `helper_connected: false` from `/api/v1/server/info`

**Recovery:** Full BB restart (quit + relaunch + 15s init), then gateway restart. A full restart is required — soft restart (`/server/restart/soft`) only reconnects the helper but does NOT restart the chat.db file system observer, which often co-stalls with the helper. Soft restart leaves BB unable to detect new messages even though `helper_connected` returns `true`.

**Cooldown:** 15 minutes between restarts.

### B) Chat.db observer stall

**Symptom:** New messages exist in chat.db but BB doesn't detect or webhook them.

**Detection:** Message GUID changes in BB API but no recent webhook dispatch in BB log. Uses poke-first recovery (Messages chat-count query via AppleScript) before escalating to full restart.

**Recovery:** Poke → retry (up to 3 checks) → full BB restart + gateway restart.

### C) Webhook service dead

**Symptom:** BB's webhook dispatch loop has stopped entirely.

**Detection:** No webhook dispatch in BB log for 30+ minutes but new messages are arriving (GUID changing).

**Recovery:** Full BB restart + gateway restart (skip poke — won't help with dead webhook service).

### D) Gateway BB plugin dead

**Symptom:** BB dispatches webhooks successfully but the gateway's BB plugin isn't loaded or processing them.

**Detection:** Cross-checks gateway runtime log for recent BB activity (`bluebubbles` + `webhook listening`/`inbound`/`new-message`). If BB dispatched webhooks recently (< 10 min) but gateway has no BB activity in 60+ min, the plugin is dead.

**Recovery:** Gateway-only restart (BB is healthy, just gateway needs reload).

**Known issue (fixed March 10):** Gateway log is named by local date (`openclaw-YYYY-MM-DD.log`) but the watchdog was computing the date in UTC. After 8 PM ET (midnight UTC), the watchdog couldn't find the log file, causing `gatewayBbAliveMin` to always be 999 and triggering false "gateway BB plugin dead" restarts nightly. Fixed by checking both today's and yesterday's log files using local time.

### Cooldown and state

- All restarts share a 15-minute cooldown to prevent restart loops
- State file: `~/.openclaw/bb-watchdog/state.json`
- Tracks: `allGuid`, `allSeenAt`, `lastRestart`, `pendingGuid`, `pendingChecks`
- Ingest lag alerts logged to `/tmp/bb-ingest-lag.log` (threshold: 90s)
- Daily lag summaries at `/tmp/bb-lag-summary.log` (08:05 local time)

## 5. Known failure modes and gotchas

### BB soft restart vs full restart

| Operation | What it restarts | What it does NOT restart |
|-----------|-----------------|------------------------|
| Soft restart (`/server/restart/soft`) | Network services, Private API helper, socket connections | Chat.db file system observer |
| Full restart (quit + relaunch) | Everything | — |

**Rule:** Always use full restart for reliability. Soft restart is unreliable for production recovery because the chat.db observer often co-stalls with other components.

### BB plugin import bug (v2026.3.7)

`extensions/bluebubbles/src/monitor-normalize.ts` imports from `../../../src/infra/parse-finite-number.js` — a dev-only source path absent in the npm package. Gateway logs `Unknown channel: bluebubbles` and silently drops all iMessage webhooks. **Patch:** replace import with inline `parseFiniteNumber` function. Weekly upgrade script (step 6.5) auto-applies this patch after each `npm install`.

### Phone-handle send failure

- `chatGuid: any;-;+17813544611` fails (AppleScript error)
- `chatGuid: any;-;dylanbochman@gmail.com` succeeds
- **Policy:** Always use email handle for Dylan DMs

### BB + gateway restart sequencing

After BB restart, the gateway holds a stale webhook registration. Must restart gateway after BB to re-register the webhook. The watchdog handles this automatically with a 15s init wait between BB relaunch and gateway restart.

### Cloudflare daemon

BB runs a Cloudflare daemon even with `lan-url` proxy. Crash-loops can corrupt BB's event loop, killing webhook dispatch without killing the BB process itself. Watchdog detects this via webhook-dead check (mode C).

## 6. Routing policy

For BlueBubbles direct messages to Dylan:
- Use: `dylanbochman@gmail.com`
- Do not use: `+17813544611`

DM GUIDs use `any;-;` prefix; group GUIDs use `iMessage;+;` prefix.

## 7. Operational checks

Quick health checks:
1. OpenClaw gateway:
   - `launchctl list ai.openclaw.gateway`
2. BlueBubbles API:
   - `curl "http://localhost:1234/api/v1/ping?password=$BLUEBUBBLES_PASSWORD"`
3. BlueBubbles server + Private API status:
   - `curl "http://localhost:1234/api/v1/server/info?password=$BLUEBUBBLES_PASSWORD"`
   - Check: `private_api: true`, `helper_connected: true`
4. Watchdog:
   - `tail -n 20 /tmp/bb-watchdog.log`
5. Ingest lag metrics:
   - `tail -n 20 /tmp/bb-ingest-lag.log`
6. Daily lag summary:
   - `tail -n 10 /tmp/bb-lag-summary.log`
7. Gateway runtime log:
   - `tail -n 50 /tmp/openclaw/openclaw-$(date +%Y-%m-%d).log`
8. Gateway error log:
   - `tail -n 20 ~/.openclaw/logs/gateway.err.log`

## 8. React types (tapbacks)

Available via Private API: `love`, `like`, `dislike`, `laugh`, `emphasize`, `question`

No custom emoji (Apple limitation). Use native `message` tool `action: "react"` (requires `message_id` in inbound metadata).

## 9. Changelog

### March 10, 2026
- **Watchdog: Private API helper detection** — new check queries `/api/v1/server/info` for `helper_connected: false`. Triggers full BB restart (not soft restart) because soft restart doesn't fix chat.db observer co-stalls.
- **Watchdog: UTC date bug fix** — gateway log date lookup now uses local time and checks both today and yesterday's files, preventing false "gateway BB plugin dead" restarts after 8 PM ET.

### March 3, 2026
- Cloudflare proxy disabled, switched to `lan-url`
- Gateway health monitor disabled (`channelHealthCheckMinutes: 0`)
- Heartbeat interval changed from 6h to 12h

### March 2, 2026
- SIP disabled, Private API enabled
- All Private API features enabled in config
- `typingMode` set to `"thinking"`
- Watchdog synced from dotfiles, state migrated
- Phone-handle routing policy established
