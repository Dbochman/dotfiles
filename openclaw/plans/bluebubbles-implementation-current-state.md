# BlueBubbles Implementation: Current State

**As of:** March 2, 2026 (EST)  
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

**Config drift note:** Dotfiles now tracks the mitigated state (Private API disabled). When Private API is enabled, flip `sendReadReceipts` to `true`, `session.typingMode` to `"auto"`, and all `actions.*` back to `true` (except `sendAttachment` which stays `true` regardless).

Current mitigation keys:
- `session.typingMode: "never"` (suppresses typing calls while Private API is disabled)
- `channels.bluebubbles.sendReadReceipts: false`
- `channels.bluebubbles.actions` for Private-API features set to `false`:
  - `reactions`, `edit`, `unsend`, `reply`, `sendWithEffect`, `renameGroup`, `setGroupIcon`, `addParticipant`, `removeParticipant`, `leaveGroup`

### Gateway service
Files:
- `openclaw/ai.openclaw.gateway.plist`
- `openclaw/OpenClawGateway.app/Contents/MacOS/OpenClawGateway`

Behavior:
- Gateway loads secrets from `~/.openclaw/.secrets-cache`
- Launches OpenClaw gateway process with port `18789`
- Keeps service alive via LaunchAgent `ai.openclaw.gateway`

### BlueBubbles watchdog
Files:
- `openclaw/com.openclaw.bb-watchdog.plist`
- `openclaw/workspace/scripts/bb-watchdog.sh`
- `openclaw/com.openclaw.poke-messages.plist`
- `openclaw/workspace/scripts/poke-messages.scpt`

Behavior:
- Runs every 1 minute
- Detects probable observer stalls
- Logs ingest lag alerts to `/tmp/bb-ingest-lag.log` (default threshold: 90s)
- Uses poke-first recovery (`Messages` chat-count query) before restart
- Restarts BlueBubbles only after repeated unresolved lag checks
- Uses cooldown logic to prevent restart loops

## 3. Host/runtime state validated on March 2, 2026

Validated directly on Mac Mini:
- OpenClaw gateway running under launchd
- BlueBubbles API reachable (`/api/v1/ping`)
- BlueBubbles server version `1.9.9`
- BlueBubbles status:
  - `private_api: false`
  - `helper_connected: false`

This means base messaging works, but Private-API-only features are not available.

## 4. Known constraints and failure modes

### A) Private API disabled

Observed errors:
- `typing start failed (500)`
- `mark read failed (500)`
- Error message: `iMessage Private API is not enabled`

Impact:
- Typing indicators / read-receipt operations fail
- Reactions, edits, unsend, and related advanced controls unavailable

Mitigation currently in place:
- Typing + read receipt behavior suppressed in OpenClaw config
- Private-API actions disabled in channel actions

Long-term fix:
- Enable BlueBubbles Private API (requires SIP-related physical Mac workflow documented in `openclaw/plans/bluebubbles-private-api.md`)

### B) Phone-handle send failure (`+17813544611`)

Reproduced directly via BlueBubbles API:
- `chatGuid: any;-;+17813544611` fails
- `chatGuid: any;-;dylanbochman@gmail.com` succeeds

Error:
- AppleScript path fails with:
  - `set targetService to 1st account whose service type = any`
  - `Can’t make any into type constant. (-1700)`

Interpretation:
- On this host, BlueBubbles AppleScript sending to that phone-number handle is unreliable/broken
- Email-handle routing is healthy and should be treated as canonical for Dylan direct sends on BlueBubbles

## 5. Incident summary: March 2, 2026 around 7:00 AM EST

Timeline:
1. ~07:01 EST: outbound message flow succeeded
2. ~07:15 and ~07:35 EST: watchdog triggered BlueBubbles restarts
3. ~07:22–07:24 EST (and other windows): recurring Private API 500s

Contributing issues:
- Older watchdog script version on host (drift from dotfiles) causing false stall decisions
- Private API disabled (generated noisy but expected 500s)
- Legacy failed deliveries queued to phone handle retried during recovery

Fixes applied during incident:
1. Synced newer `bb-watchdog.sh` to host (state migrated to new format)
2. Suppressed Private-API-dependent OpenClaw behavior
3. Forced weekly security reminder routing to Dylan email handle
4. Cleared stale queue entries targeting failing phone handle

## 6. Routing policy (important)

For BlueBubbles direct messages to Dylan:
- Use: `dylanbochman@gmail.com`
- Do not use: `+17813544611`

This policy is explicitly encoded in weekly reminder job prompts in:
- `openclaw/cron-jobs.json`
- `openclaw/cron/jobs.json`

## 7. Operational checks

Quick health checks:
1. OpenClaw gateway:
   - `launchctl print gui/$(id -u)/ai.openclaw.gateway`
2. BlueBubbles API:
   - `curl "http://localhost:1234/api/v1/ping?password=$BLUEBUBBLES_PASSWORD"`
3. BlueBubbles server capabilities:
   - `curl "http://localhost:1234/api/v1/server/info?password=$BLUEBUBBLES_PASSWORD"`
4. Watchdog:
   - `tail -n 100 /tmp/bb-watchdog.log`
5. Ingest lag metrics:
   - `tail -n 100 /tmp/bb-ingest-lag.log`
6. OpenClaw errors:
   - `tail -n 200 ~/.openclaw/logs/gateway.err.log`

## 8. Health snapshot procedure

Use this to capture a point-in-time status check and compare against prior snapshots.

### A) Run the snapshot

On your laptop:
```bash
ssh dbochman@100.93.66.71 '
echo "=== timestamp ==="; date;
echo "=== gateway status ==="; launchctl print gui/$(id -u)/ai.openclaw.gateway | egrep "state =|pid =";
echo "=== bluebubbles server info ===";
if [[ -f ~/.openclaw/.secrets-cache ]]; then set -a; source ~/.openclaw/.secrets-cache; set +a; fi;
curl -sS --max-time 8 "http://localhost:1234/api/v1/server/info?password=${BLUEBUBBLES_PASSWORD:-}" | python3 -m json.tool;
echo "=== latest gateway error ==="; tail -n 1 ~/.openclaw/logs/gateway.err.log;
echo "=== latest watchdog line ==="; tail -n 1 /tmp/bb-watchdog.log;
echo "=== delivery queue ==="; ls -la ~/.openclaw/delivery-queue ~/.openclaw/delivery-queue/archived 2>/dev/null || true;
'
```

### B) Interpret results

Healthy snapshot indicators:
1. Gateway `state = running`
2. BlueBubbles API responds successfully
3. `private_api` / `helper_connected` are expected values for current mode
4. Watchdog latest line is `OK:` (idle or recent webhook)
5. Active `~/.openclaw/delivery-queue/` has no `.json` files pending

Warning indicators:
1. New `BlueBubbles send failed (500)` lines after prior known timestamp
2. Repeated watchdog `STALL DETECTED` entries in short intervals
3. New `typing start failed` / `mark read failed` entries if typing/read mitigations are enabled
4. Growing active delivery queue

### C) Minimal delta check (fast)

For quick follow-ups:
```bash
ssh dbochman@100.93.66.71 '
echo "last gateway.err line:"; tail -n 1 ~/.openclaw/logs/gateway.err.log;
echo "last send-fail line:"; grep "BlueBubbles send failed (500)" ~/.openclaw/logs/gateway.err.log | tail -n 1;
echo "last watchdog line:"; tail -n 1 /tmp/bb-watchdog.log;
echo "last lag metric:"; tail -n 1 /tmp/bb-ingest-lag.log 2>/dev/null || echo "none";
'
```

### D) Recent snapshot example (March 2, 2026, 11:39 AM EST)

Observed during live check:
1. BlueBubbles showed inbound ingest delay, then recovered:
   - `11:38:43 EST`: `New Message from dy**********@gmail.com, "Just checking i..."; Date: 3/2/2026, 11:34:02 AM`
   - `11:38:43 EST`: webhook dispatched to OpenClaw (`/bluebubbles-webhook`)
2. OpenClaw processed immediately after webhook arrival:
   - `16:38:44Z` (`11:38:44 EST`): lane enqueue/dequeue and embedded run start
   - `16:39:06Z` (`11:39:06 EST`): embedded run completed and response sent
3. End-to-end conclusion:
   - Primary delay was upstream of OpenClaw response generation (message reached BlueBubbles late).
   - Once webhook fired, OpenClaw turnaround was normal (~22s run time including one transient LLM retry).

Use this as a reference pattern for future incidents:
1. Confirm BlueBubbles `New Message` timestamp vs message `Date:` inside the same line
2. Confirm webhook dispatch timestamp
3. Confirm OpenClaw enqueue/start/end timestamps in `/tmp/openclaw/openclaw-YYYY-MM-DD.log`
4. Attribute delay to ingress (BB-side) vs processing (OpenClaw-side)

## 9. Remaining gaps

Current telemetry is usable but not comprehensive:
1. No automatic alert on send-failure spikes by recipient handle
2. No explicit periodic canary for both email and phone handle send paths
3. No structured weekly error taxonomy for BlueBubbles-specific failures

These are candidates for future hardening if reliability work continues.
