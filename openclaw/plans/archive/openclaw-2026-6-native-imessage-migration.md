# OpenClaw 2026.6 Native iMessage Migration Plan

**Status:** Completed 2026-06-27; BlueBubbles retired and purged; archived
**Owner:** Dylan
**Created:** 2026-06-27
**Last updated:** 2026-06-27
**Primary host:** `mac-mini`
**Current OpenClaw:** `2026.6.10` stable
**Current imsg:** `0.11.1`
**Pre-migration imsg:** `0.5.0`

## Summary

OpenClaw `2026.6.10` removes the BlueBubbles channel. The supported iMessage
path is now `channels.imessage`, backed by the `imsg` CLI over JSON-RPC. The
package upgrade and transport migration were completed on 2026-06-27.

The production decision is to keep SIP disabled and library validation relaxed
for advanced native `imsg` features: reactions, threaded replies, edit/unsend,
message effects, typing indicators, read receipts, and group management.

The migration and retirement are complete. The final native delivery completed
end to end in about three seconds and the Messages database confirmed it as
delivered. The BlueBubbles OpenClaw plugin package, dependency, lock entry,
install record, app, services, app-owned data, local credentials, helpers, and
caches were removed. A post-removal gateway restart loaded the bundled
`imessage` plugin and no BlueBubbles plugin.

Historical Messages attachments whose directory names contain `BlueBubbles`
were deliberately preserved because they belong to chat history, not the
retired application.

## Final Outcome

- OpenClaw `2026.6.10` and `imsg 0.11.1` are the production stack.
- `imsg` reports basic, advanced, and v2 readiness with bridge version 2.
- The gateway starts with 12 plugins including `imessage` and excluding
  BlueBubbles; plugin doctor reports no issues.
- The live SQLite cron store has 25 enabled iMessage deliveries and zero
  BlueBubbles deliveries.
- `vacancy-actions.sh` sends front-door lock alerts through native `imsg` to
  `chat_id:171`; no production caller requires the retired HTTP API.
- The usage dashboard now reports the supported native path through a passive
  iMessage health card and `/api/imessage-health`; the live state is healthy.
- Inbound iMessage attachments are enabled globally, constrained to the local
  Messages attachment root with a 16 MB media cap.
- Tailscale Serve exposes the loopback-only gateway through tailnet-only HTTPS
  and WSS at `dylans-mac-mini.tail3e55f9.ts.net`.
- The old rollback window is closed. Reintroducing BlueBubbles would be a fresh,
  explicit installation from archived documentation rather than an operational
  rollback.

## Current State

### Runtime inventory

- `csrutil status`: System Integrity Protection is disabled.
- `/Library/Preferences/com.apple.security.libraryvalidation.plist DisableLibraryValidation`: `1`.
- OpenClaw package: `openclaw@2026.6.10` under `/opt/homebrew/lib/node_modules`.
- `openclaw --version`: `OpenClaw 2026.6.10 (aa69b12)`.
- Agent primary model: `openai/gpt-5.6-sol` with `agentRuntime.id: "codex"`
  and `params.fastMode: true`; first fallback is `openai/gpt-5.5`.
- `npm view openclaw`: `latest=2026.6.10`, `beta=2026.6.11-beta.1`.
- `imsg`: installed at `/opt/homebrew/bin/imsg`, version `0.11.1`.
- `brew info imsg`: stable `0.11.1` from `steipete/tap/imsg`.
- `imsg chats --limit 5 --json`: reads Messages successfully.
- `imsg status --json`: `basic_features: true`, `advanced_features: true`,
  `v2_ready: true`, `bridge_version: 2`, typing indicators and read receipts
  available.
- `imsg rpc`: `chats.list` JSON-RPC smoke test succeeds.
- `imsg send --chat-id 171` and `imsg send --chat-id 1`: direct test sends
  succeeded after the upgrade.
- Gateway iMessage provider: running after macOS Full Disk Access/Automation
  prompts were approved for `OpenClawGateway.app`.
- `openclaw channels status --json`: iMessage account `default` is enabled,
  configured, running, and has `lastError: null`, `restartPending: false`,
  `reconnectAttempts: 0`.
- Gateway default-model smoke test with `--thinking medium` used
  `openai/gpt-5.6-sol`, returned fallback `false`, and completed successfully.
- Gateway log after the final secret-clearing restart at `2026-06-27 17:04:09 EDT`
  shows 12 loaded plugins including `imessage`, no BlueBubbles plugin, and no
  new `imsg rpc not ready` errors.
- The source-controlled gateway launcher includes system `sbin` paths so the
  Tailscale CLI selects the logged-in macOS GUI/network-extension backend rather
  than the unused Homebrew `tailscaled` backend.
- Tailscale Serve maps tailnet-only HTTPS/WSS on
  `dylans-mac-mini.tail3e55f9.ts.net` to `127.0.0.1:18789`; post-repair gateway
  startup logged `serve enabled` and both local and HTTPS health checks passed.
- OpenClaw cron storage migrated to SQLite at
  `~/.openclaw/state/openclaw.sqlite`.
- `openclaw cron status --json`: `storage: "sqlite"`, `jobs: 38`.
- SQLite cron store: 25 live iMessage delivery jobs and 0 BlueBubbles delivery
  jobs.
- The BlueBubbles Homebrew cask and `/Applications/BlueBubbles.app` are absent.
- No BlueBubbles process, port `1234` listener, launchd service, OpenClaw plugin
  record, pairing credential, watchdog, or local cached secret remains.
- Homebrew and npm BlueBubbles caches were removed. Historical Messages
  attachments and previews were preserved.

### Current OpenClaw config dependencies

`openclaw/openclaw.json` now has:

- `channels.imessage.enabled: true`
- `channels.imessage.cliPath: "/opt/homebrew/bin/imsg"`
- `channels.imessage.dbPath: "/Users/dbochman/Library/Messages/chat.db"`
- `channels.imessage.sendTransport: "auto"`
- `channels.imessage.service: "auto"`
- `channels.imessage.dmPolicy: "open"`
- `channels.imessage.groupPolicy: "open"`
- `channels.imessage.allowFrom: ["*"]`
- `channels.imessage.groupAllowFrom: ["*"]`
- `channels.imessage.groups: { "*": {} }`
- `channels.imessage.sendReadReceipts: false`
- `channels.imessage.includeAttachments: true`
- `channels.imessage.attachmentRoots: ["/Users/dbochman/Library/Messages/Attachments"]`
- `channels.imessage.mediaMaxMb: 16`
- `channels.imessage.actions`: reactions, edit, unsend, reply, effects, group
  management, and outbound attachment sending enabled for advanced native
  operation.
- `plugins.entries.bluebubbles`: removed.
- `plugins.entries.codex`: enabled with `/opt/homebrew/bin/codex`,
  `serviceTier: "priority"`, and default model `openai/gpt-5.6-sol`.

`openclaw/cron/jobs.json` now has 38 tracked definitions:

- 25 definitions with `delivery.channel: "imessage"`.
- 0 definitions with `delivery.channel: "bluebubbles"`.
- The live SQLite store has the same 38 job IDs. The completed June 25, June
  26, and June 27 World Cup definitions were removed from the repo while their
  run-history tombstones remain in SQLite.

Known delivery targets from `jobs.json`:

| Jobs | Legacy pre-cutover target | Current native iMessage target |
|------|----------------|--------------------------|
| Julia morning briefing | `+15084234853` | `chat_id:1` |
| Dylan briefings and World Cup jobs | `+17813544611` | `chat_id:171` |
| Weekly report | `+17813544611` | `chat_id:171` |

Native `imsg chats` currently maps:

- `chat_id:171` -> `dylanbochman@gmail.com`
- `chat_id:1` -> `+15084234853`
- `chat_id:170` -> group identifier `7010feab69b14fa19071a88340495f2f`

Production jobs use `chat_id:*` delivery targets. The OpenClaw `2026.6.10`
iMessage docs recommend explicit chat targets for stable routing.

## Scope

In scope:

- Upgrade `imsg` to a build that exposes `status` and `launch`.
- Validate direct `imsg` read, send, watch, RPC, and private API surfaces.
- Translate `channels.bluebubbles` to `channels.imessage`.
- Update cron delivery channels and stable targets.
- Upgrade OpenClaw from `2026.5.7` to `2026.6.10`.
- Verify direct messages, group messages, attachments if enabled, private API
  actions, cron delivery, dashboards, and gateway health.
- Retire and purge BlueBubbles after successful live validation.
- Record the production decision to keep SIP disabled for advanced native
  iMessage features.

Out of scope for the first cutover:

- Moving Google Workspace CLI off the pinned `0.4.4` version.
- Redesigning cron prompts beyond channel and target migration.
- Reworking the OpenClaw model/provider stack unless the upgrade forces it.

## Stop Conditions

All stop conditions passed; none were triggered. The maintenance-window gates
were:

- `imsg` cannot read chats from the same process context that will run the
  gateway.
- `imsg send` cannot send to Dylan and Julia test chats.
- `imsg rpc` does not stay healthy under OpenClaw probing.
- `openclaw config validate` fails after migration.
- Gateway fails to start or repeatedly restarts.
- Inbound iMessage DMs do not reach OpenClaw.
- Cron delivery cannot send one test message through `channel: "imessage"`.

If only advanced actions fail but basic send/receive works, do not roll back
immediately. Decide whether private API parity is required before production
cutover.

## Phase 0: Maintenance Window And Backups

**Status:** Completed 2026-06-27.

Completed notes:

- Runtime backup created at
  `~/.openclaw/backups/openclaw-2026-6-migration-20260627T132011Z`.
- Backup includes live `openclaw.json`, cron jobs, and runtime helper copies.
- OpenClaw Doctor replaced the original config symlink during an atomic rewrite.
  The live config remains a regular file because it contains machine-local
  memory-secret provider settings; its native iMessage and plugin policy match
  the dotfiles source.
- SIP was intentionally left disabled for private API parity.

Schedule outside the morning automation cluster. Avoid at least:

- 06:15 finance refresh
- 07:00 Julia morning briefing
- 07:35 forecast ledger capture
- 08:00 Dylan morning briefing
- Any active World Cup briefing slot

Create a timestamped local backup before touching binaries or config:

```bash
ts="$(date +%Y%m%d-%H%M%S)"
backup="$HOME/.openclaw/pre-2026-6-imessage-$ts"
export backup
mkdir -p "$backup"

cp -a "$HOME/.openclaw/openclaw.json" "$backup/openclaw.json"
cp -a "$HOME/.openclaw/cron/jobs.json" "$backup/jobs.json"
cp -a "$HOME/.openclaw/devices/paired.json" "$backup/paired.json" 2>/dev/null || true
cp -a "$HOME/Library/LaunchAgents/ai.openclaw.gateway.plist" "$backup/ai.openclaw.gateway.plist"
cp -a "$HOME/Library/LaunchAgents/com.bluebubbles.server.plist" "$backup/com.bluebubbles.server.plist" 2>/dev/null || true
cp -a "$HOME/Library/LaunchAgents/com.openclaw.bb-watchdog.plist" "$backup/com.openclaw.bb-watchdog.plist" 2>/dev/null || true
cp -a "$HOME/Library/LaunchAgents/com.openclaw.poke-messages.plist" "$backup/com.openclaw.poke-messages.plist" 2>/dev/null || true
cp -a "$HOME/Library/LaunchAgents/com.openclaw.bb-lag-summary.plist" "$backup/com.openclaw.bb-lag-summary.plist" 2>/dev/null || true

{
  date
  hostname
  csrutil status 2>&1 || true
  defaults read /Library/Preferences/com.apple.security.libraryvalidation.plist DisableLibraryValidation 2>/dev/null || true
  openclaw --version 2>&1 || true
  openclaw update status 2>&1 || true
  imsg --version 2>&1 || true
  brew list --versions imsg 2>/dev/null || true
  npm list -g --depth 0 2>/dev/null | grep -E 'openclaw|npm' || true
  launchctl list | grep -E 'ai.openclaw.gateway|com.bluebubbles.server|com.openclaw.bb|com.openclaw.poke' || true
} > "$backup/inventory.txt"
```

Keep this shell open through the maintenance window, or re-export `backup` to
the timestamped directory before using later restore commands.

Also keep the repo state separate from runtime state:

```bash
git status --short
git diff -- openclaw/openclaw.json openclaw/cron/jobs.json install.sh openclaw/launchagents
```

## Phase 1: Upgrade And Validate imsg First

**Status:** Completed 2026-06-27.

Completed notes:

- `brew update` succeeded.
- Homebrew required tap trust for `steipete/tap/imsg`; trusted only the specific
  formula with `brew trust --formula steipete/tap/imsg`.
- Upgraded `imsg` from `0.5.0` to `0.11.1`.
- Added `brew "steipete/tap/imsg"` to the repo Brewfile.
- First `imsg launch` timed out because Messages.app was not fully reset.
  `imsg launch --kill-only`, followed by `imsg launch --verbose`, succeeded.
- `imsg status --json` now reports `advanced_features: true`.
- Direct sends to `chat_id:171` and `chat_id:1` succeeded.
- `imsg rpc` responded to `chats.list`.

Pre-migration note: the installed `imsg 0.5.0` was too old for the target
OpenClaw private API probing path and lacked the documented `status` and
`launch` commands. It was upgraded before changing OpenClaw.

```bash
brew update
brew upgrade imsg
imsg --version
imsg --help
imsg status --json | jq .
```

Expected: `imsg` is `0.11.1` or newer, and `status` plus `launch` exist.

### Full Disk Access and Automation

On macOS Tahoe, Homebrew binary updates may invalidate Full Disk Access grants.
Before relying on launchd, validate from the exact process contexts that matter:

```bash
imsg chats --limit 10 --json | jq -s 'length'
imsg history --chat-id 171 --limit 5 --attachments --json | jq -s 'length'
imsg send --chat-id 171 --text "OpenClaw imsg direct test"
imsg send --chat-id 1 --text "OpenClaw imsg direct test"
```

If reads hang or fail:

- Refresh the `~/Applications/imsg.app` wrapper if it points at the old Cellar
  version.
- Re-add or toggle Full Disk Access for `imsg.app`.
- Confirm `OpenClawGateway.app` still has Full Disk Access.
- Confirm Terminal or the VNC-launched shell has Full Disk Access for manual
  tests.

If sends fail with AppleEvents `-1743`:

- Re-grant Automation permission for the process context sending through
  Messages.app.
- Test from a GUI session, not only over SSH.
- Do not continue until direct sends work.

### Private API Bridge

For BlueBubbles parity, verify the advanced bridge:

```bash
csrutil status
defaults read /Library/Preferences/com.apple.security.libraryvalidation.plist DisableLibraryValidation
imsg launch
imsg status --json | jq .
```

Expected:

- SIP is disabled.
- `DisableLibraryValidation` is `1`.
- `imsg launch` succeeds.
- `imsg status --json` reports private API or advanced features available.
- Selectors for required actions are present for reactions, replies, typing,
  read receipts, edit/unsend, and group operations.

If `imsg launch` times out, check library validation before changing more
security controls:

```bash
sudo defaults write /Library/Preferences/com.apple.security.libraryvalidation.plist DisableLibraryValidation -bool true
```

Reboot and retry `imsg launch`.

## Phase 2: Prepare Dotfiles Changes

**Status:** Completed 2026-06-27.

Completed notes:

- `openclaw/openclaw.json` was migrated from `channels.bluebubbles` to
  `channels.imessage`.
- `plugins.entries.bluebubbles` was removed.
- The live-preserving `openai-codex:default` auth profile was retained in repo
  config.
- Cron delivery channels were migrated from `bluebubbles` to `imessage`.
- Dylan deliveries now target `chat_id:171`; Julia deliveries now target
  `chat_id:1`.
- Direct production notification scripts were moved off BlueBubbles HTTP:
  - `openclaw/skills/dog-walk/dog-walk-listener.py`
  - `openclaw/bin/send-audio-briefing`
  - `openclaw/bin/openclaw-weekly-report.py`
  - `openclaw/bin/usage-snapshot.sh`
- Restaurant/OpenTable notification skill docs were updated to use `imsg`.
- Runtime copies of the updated scripts and skills were installed under
  `~/.openclaw`.
- `openclaw/sync-cron-jobs.sh` now supports SQLite-backed cron storage and
  preserves state from either legacy JSON or SQLite.
- Cron payload model IDs were migrated from `openai-codex/gpt-5.5` to the
  Doctor-normalized `openai/gpt-5.5` runtime ID.

Work on a branch and keep the config migration in one focused commit:

```bash
git switch -c feat/openclaw-native-imessage-migration
```

### Config Translation

Replace `channels.bluebubbles` with `channels.imessage` in
`openclaw/openclaw.json`.

Final production block:

```json
{
  "channels": {
    "imessage": {
      "enabled": true,
      "cliPath": "/opt/homebrew/bin/imsg",
      "dbPath": "/Users/dbochman/Library/Messages/chat.db",
      "dmPolicy": "open",
      "groupPolicy": "open",
      "allowFrom": ["*"],
      "groupAllowFrom": ["*"],
      "groups": {
        "*": {}
      },
      "service": "auto",
      "sendTransport": "auto",
      "region": "US",
      "sendReadReceipts": false,
      "includeAttachments": true,
      "attachmentRoots": [
        "/Users/dbochman/Library/Messages/Attachments"
      ],
      "mediaMaxMb": 16,
      "actions": {
        "reactions": true,
        "edit": true,
        "unsend": true,
        "reply": true,
        "sendWithEffect": true,
        "renameGroup": true,
        "setGroupIcon": true,
        "addParticipant": true,
        "removeParticipant": true,
        "leaveGroup": true,
        "sendAttachment": true
      }
    }
  }
}
```

Notes:

- Broad direct-message and group admission is retained by operator decision:
  both policies remain `open`, and both sender allowlists remain `["*"]`.
- Keep `groups: { "*": {} }`. With iMessage `groupPolicy: "allowlist"` this
  block becomes load-bearing. Even with `groupPolicy: "open"`, keeping it avoids
  future footguns if policy is tightened.
- `includeAttachments` is account-wide in OpenClaw `2026.6.10`; there is no
  per-sender attachment allowlist. Global ingestion was explicitly accepted,
  with the local Messages attachment root and a 16 MB cap. The
  `actions.sendAttachment` switch controls outbound sending separately.
- Remove `serverUrl`, `password`, `webhookPath`, and BlueBubbles network config.
- Remove `plugins.entries.bluebubbles`.
- Do not add a plugin entry for iMessage unless `openclaw doctor` or target docs
  require it. The target package includes iMessage.

### Cron Jobs

Update `openclaw/cron/jobs.json`:

- Replace every `delivery.channel: "bluebubbles"` with `"imessage"`.
- Replace Dylan delivery targets with `chat_id:171`.
- Replace Julia delivery targets with `chat_id:1`.
- Leave job prompts alone unless they explicitly say BlueBubbles.
- Preserve `--no-deliver` or delivery suppression decisions already present in
  job definitions.

Audit:

```bash
jq -r '.jobs[]? | select(.delivery.channel == "bluebubbles") | .id' openclaw/cron/jobs.json
jq -r '.jobs[]? | select(.delivery.channel == "imessage") | [.id, .delivery.to] | @tsv' openclaw/cron/jobs.json
```

### Direct BlueBubbles Call Sites

These are not automatically fixed by changing the channel config:

- `openclaw/skills/dog-walk/dog-walk-listener.py`
- `openclaw/bin/send-audio-briefing`
- `openclaw/bin/usage-snapshot.sh`
- `openclaw/bin/openclaw-weekly-report.py`
- `openclaw/bin/usage-dashboard.py`
- `openclaw/bin/openclaw-refresh-secrets`
- `openclaw/bin/openclaw-weekly-upgrade`
- `install.sh` gateway-host LaunchAgent installation block
- `openclaw/launchagents/com.openclaw.bb-watchdog.plist`
- `openclaw/launchagents/com.openclaw.bb-lag-summary.plist`
- `openclaw/launchagents/com.openclaw.poke-messages.plist`
- `openclaw/workspace/scripts/bb-watchdog.sh`
- `openclaw/workspace/scripts/bb-lag-summary.sh`
- `openclaw/workspace/scripts/poke-messages.scpt`
- `openclaw/LAUNCHAGENTS.md`
- `openclaw/DASHBOARDS.md`
- `openclaw/USAGE-DASHBOARD.md`
- `openclaw/workspace/TOOLS.md`
- `openclaw/workspace/SOUL.md`
- `.claude/projects/-Users-dbochman/memory/*`

For the cutover commit, divide them into:

- **Must change before upgrade:** config, cron delivery channel, direct scripts
  that send production notifications through BlueBubbles HTTP.
- **Can degrade temporarily:** dashboards and weekly report BlueBubbles message
  counts/health cards.
- **Retired at conclusion:** watchdog scripts, LaunchAgents, and active
  BlueBubbles operational guidance.

BlueBubbles LaunchAgents were kept through the initial cutover validation, then
removed when the operator closed the rollback window.

### Local Validation Before Runtime Install

```bash
jq empty openclaw/openclaw.json
jq empty openclaw/cron/jobs.json
plutil -lint openclaw/launchagents/*.plist
./install.sh --dry-run
./sync.sh validate
```

## Phase 3: Runtime Cutover And OpenClaw Upgrade

**Status:** Completed 2026-06-27.

Completed notes:

- OpenClaw was upgraded with `npm install -g openclaw@2026.6.10`.
- `openclaw --version` reports `OpenClaw 2026.6.10 (aa69b12)`.
- `openclaw config validate --json` succeeds on the live config. When run
  without the secrets cache, expected warnings appear for secret-backed values:
  `OPENCLAW_GATEWAY_TOKEN`, `OPENAI_API_KEY`, and `ELEVENLABS_API_KEY`.
- OpenClaw Doctor migrated cron/task/flow/delivery queue/memory state into
  SQLite.
- `openclaw/sync-cron-jobs.sh deploy` was validated after the SQLite migration:
  it deployed 38 live jobs, preserved state on all 38, skipped completed
  one-shot jobs for June 25-27, and normalized the store through Doctor.
- OpenClaw Doctor normalized the agent model from `openai-codex/gpt-5.5` to
  `openai/gpt-5.5` with `agentRuntime.id: "codex"` and enabled the bundled
  `codex` plugin.
- The final Codex runtime update added `openai/gpt-5.6-sol`, set it as the
  default model with fast mode enabled, kept `openai/gpt-5.5` as the first
  fallback, and passed a gateway smoke test with `--thinking medium`.
- Initial gateway attempts after package/config cutover failed `imsg rpc`
  readiness probes until macOS permission prompts were approved for
  `OpenClawGateway.app`.
- After permissions and a gateway restart, the iMessage provider stayed running
  with no new `imsg rpc not ready` errors.

Run with secrets loaded so OpenClaw can validate and probe the real gateway:

```bash
export PATH="/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:$HOME/.openclaw/bin:$PATH"
set -a
source "$HOME/.openclaw/.secrets-cache"
set +a
```

### Stop Gateway First

Do this before replacing the package:

```bash
launchctl unload "$HOME/Library/LaunchAgents/ai.openclaw.gateway.plist"
sleep 3
pgrep -fl 'openclaw.*gateway' && echo "Gateway still running" || echo "Gateway stopped"
```

BlueBubbles remained available during the initial cutover and was stopped before
the final native-only restart and retirement verification.

### Apply Migrated Runtime Files

Install or copy the migrated config and cron definitions:

```bash
cp "$HOME/dotfiles/openclaw/openclaw.json" "$HOME/.openclaw/openclaw.json"
mkdir -p "$HOME/.openclaw/cron"
cp "$HOME/dotfiles/openclaw/cron/jobs.json" "$HOME/.openclaw/cron/jobs.json"
```

Validate before package upgrade:

```bash
openclaw config validate --json | jq .
```

### Upgrade OpenClaw

Primary path:

```bash
openclaw update
```

Fallback path if `openclaw update` refuses because package manager detection is
wrong on this host:

```bash
npm install -g openclaw@2026.6.10 --prefix /opt/homebrew
```

After install:

```bash
openclaw --version
openclaw doctor --fix
```

Check whether the custom gateway plist was overwritten:

```bash
diff "$HOME/Library/LaunchAgents/ai.openclaw.gateway.plist" "$backup/ai.openclaw.gateway.plist" >/dev/null \
  && echo "gateway plist unchanged" \
  || cp "$backup/ai.openclaw.gateway.plist" "$HOME/Library/LaunchAgents/ai.openclaw.gateway.plist"
```

### Start Gateway

```bash
launchctl load "$HOME/Library/LaunchAgents/ai.openclaw.gateway.plist"
sleep 10
launchctl list | grep ai.openclaw.gateway
tail -80 "$HOME/.openclaw/logs/gateway.log"
tail -80 "$HOME/.openclaw/logs/gateway.err.log"
```

## Phase 4: Verification Matrix

**Status:** Completed 2026-06-27.

Completed notes:

- `imsg status --json`: `version: "0.11.1"`, `sip: "disabled"`,
  `basic_features: true`, `advanced_features: true`, `v2_ready: true`,
  `bridge_version: 2`, typing indicators and read receipts available.
- OpenClaw gateway started with the bundled `imessage` plugin and without the
  BlueBubbles plugin:
  `bonjour, browser, canvas, device-pair, elevenlabs, file-transfer, imessage,
  memory-core, phone-control, talk-voice`.
- Gateway outbound send to Dylan through `openclaw message send --channel
  imessage --target chat_id:171` succeeded. The local Messages DB confirmed
  GUID `320F4753-C638-4792-A48D-30F06B791DA1`.
- A second post-permission gateway send succeeded with GUID
  `4F12B4D6-CA3F-46CD-86C9-EDE1BB62D098`.
- Dylan replied `"Messaged received"` to the first gateway test.
- Dylan tapbacked the post-permission test; the gateway logged
  `reaction system event queued` for the iMessage session, confirming the
  provider consumed the inbound reaction path.
- An inbound plain-text iMessage was processed successfully. Before the model
  normalization was copied back to the repo and cron payloads were updated, the
  reply included a visible fallback notice from `openai-codex/gpt-5.5` to
  `anthropic/claude-opus-4-6`.
- A post-restart gateway send succeeded with GUID
  `92BE7CE5-B4E3-489E-BF9A-BFF45296EA4B`; the local Messages DB confirmed the
  send and a tapback reaction.
- Direct `imsg` sends to Julia `chat_id:1` succeeded before the OpenClaw
  package cutover.
- `openclaw cron status --json`: `storage: "sqlite"`, `jobs: 38`.
- `openclaw cron list --json`: 25 live iMessage delivery jobs and zero
  BlueBubbles delivery jobs.
- `openclaw cron list --json`: 0 live jobs still use
  `openai-codex/gpt-5.5`; 22 live model-pinned jobs use `openai/gpt-5.5`.
- After uninstalling the retired plugin package, a full gateway restart loaded
  exactly 12 plugins including `imessage` and excluding BlueBubbles.
- The final end-to-end native delivery completed in about three seconds; the
  Messages database recorded it as sent and delivered.
- The active vacancy LaunchAgent context reports `imsg` basic, advanced, and v2
  readiness, validating the direct native lock-notification path.

### Package and Config

```bash
openclaw --version
openclaw config validate --json | jq .
openclaw doctor
openclaw health
openclaw channels status --probe --channel imessage
```

Expected:

- OpenClaw reports `2026.6.10`.
- No `channels.bluebubbles` validation failure remains.
- `imessage` is enabled and probeable.
- `privateApi.available` is true if we are keeping advanced-action parity.

### Direct Message Send

```bash
openclaw message send \
  --channel imessage \
  --target chat_id:171 \
  --message "OpenClaw native iMessage cutover test to Dylan" \
  --json | jq .

openclaw message send \
  --channel imessage \
  --target chat_id:1 \
  --message "OpenClaw native iMessage cutover test to Julia" \
  --json | jq .
```

Confirm messages land in Messages.app and on paired devices.

### Inbound DM

From Dylan and Julia devices, send a short direct message to the bot identity.
Check:

```bash
tail -120 "$HOME/.openclaw/logs/gateway.log" | grep -i 'imessage'
openclaw sessions list | head -20
```

Expected:

- Inbound event is logged as `imessage`.
- Agent replies in the same conversation.
- Read receipt and typing behavior match the selected private API mode.

### Group Chat

Send a test message in the known group chat. If no reply lands, check:

```bash
tail -200 "$HOME/.openclaw/logs/gateway.log" | grep -i 'imessage:.*group\|dropping group\|groupPolicy'
```

Common fix:

- Add `channels.imessage.groups["*"]` or an explicit `groups["<chat_id>"]`
  entry if group registry gating is dropping traffic.

### Private API Actions

In a non-critical test conversation, verify:

- Tapback reaction.
- Threaded reply to a specific message.
- Typing indicator while an agent turn runs.
- Read receipt after accepted inbound message.
- Edit a bot-sent message.
- Unsend a bot-sent test message.
- Send with effect.
- Attachment send.

For group management, test only in a disposable or explicitly approved group.

If a private API action fails:

```bash
imsg launch
imsg status --json | jq .
openclaw channels status --probe --channel imessage
```

### Cron Delivery

First inspect:

```bash
openclaw cron list
```

Then run one low-risk job with delivery disabled if the current CLI supports it:

```bash
openclaw cron run gws-dylan-morning-briefing-0001 --timeout 300000 --expect-final --no-deliver
```

Then send one intentional delivery through the new channel:

```bash
openclaw cron run gws-dylan-morning-briefing-0001 --timeout 300000 --expect-final
```

Confirm exactly one iMessage arrives. Check for duplicate delivery symptoms
before re-enabling confidence in the scheduled jobs.

### Dashboards and Operational Scripts

Minimum checks:

```bash
curl -fsS http://127.0.0.1:8551/ >/dev/null
curl -fsS http://127.0.0.1:8552/ >/dev/null
curl -fsS http://127.0.0.1:8558/ >/dev/null
python3 -m py_compile openclaw/bin/usage-dashboard.py openclaw/bin/openclaw-weekly-report.py
```

Final result:

- Dashboards still serve and compile.
- The retired BlueBubbles health/watchdog panel and API were removed from the
  usage dashboard. A separate native iMessage health card and normalized API
  were added after retirement to monitor the supported OpenClaw channel and
  `imsg` bridge without restoring any BlueBubbles dependency.
- Active scripts no longer read or require `BLUEBUBBLES_PASSWORD`.

## Phase 5: Soak

**Status:** Closed 2026-06-27 by operator decision.

The original plan proposed a 48-hour to 7-day soak. It was not used as a
blocking gate. The operator closed the rollback window after repeated direct,
gateway, inbound, reaction, restart, cron-route, and Messages-database
validation all passed. Ongoing native iMessage monitoring is normal operations,
not an unfinished migration phase.

## Phase 6: Retire BlueBubbles

**Status:** Completed 2026-06-27.

Completed retirement and purge:

- Stopped and removed the BlueBubbles server, watchdog, lag-summary, and
  poke-messages LaunchAgents.
- Uninstalled `@openclaw/bluebubbles`; removed its npm dependency, lock entry,
  current install record, package directory, and registry entry.
- Refreshed the plugin registry and restarted the gateway. The native-only
  startup loaded `imessage` and no BlueBubbles plugin; plugin doctor was clean.
- Migrated `vacancy-actions.sh` lock alerts to
  `/opt/homebrew/bin/imsg send --chat-id 171` and verified `imsg` readiness from
  a launchd job context.
- Removed the BlueBubbles Homebrew cask with its zap stanza, app-owned
  Application Support, preferences, logs, saved state, login item, and local
  launch metadata.
- Removed OpenClaw BlueBubbles state, pairing credentials, watchdog state,
  helper scripts, logs, disabled plists, CrisisMode check, stale migrated
  plugin metadata, and local cached password.
- Removed BlueBubbles Homebrew/npm download caches, temporary caches, and
  app-specific Trash entries.
- Reset the retired bundle's user and system TCC grants, including Accessibility
  and Full Disk Access, and garbage-collected its LaunchServices registration.
- Removed the weekly-upgrade patch, secret refresh requirement, 1Password skill
  reference, dashboard health panel/API, obsolete attachment-send skill, and
  active cron-creation instructions that could recreate BlueBubbles routes.
- Preserved `~/Library/Messages/Attachments/BlueBubbles` and the corresponding
  Messages preview cache because they contain historical chat assets.

Remaining references are limited to archived plans and backups, historical
logs and memory, generated upstream completions/docs, and intentional migration
notes. No runtime caller or startup path uses them.

## SIP Decision

**Production decision:** Option A. Keep SIP disabled and keep library validation
relaxed for advanced native iMessage features.

There are two viable end states.

### Option A: Keep Private API Parity

Keep SIP disabled and keep `DisableLibraryValidation=1`.

Use this if we require:

- Native tapbacks.
- Threaded replies.
- Edit and unsend.
- Message effects.
- Read receipts.
- Typing indicators.
- Group management.

The operator accepted the SIP-disabled/library-validation-relaxed risk on
2026-06-27. No separate hardening record or scheduled review is required. This
preserves the advanced feature set used in production.

### Option B: Re-enable SIP And Run Basic iMessage

Use this if basic text/media send and receive are enough.

Before re-enabling SIP:

- Set private API actions to false or remove them from `channels.imessage`.
- Set `sendTransport: "applescript"` or leave `auto` only after proving it falls
  back cleanly without bridge support.
- Verify `openclaw channels status --probe --channel imessage` reports basic
  send/receive as healthy with the bridge unavailable.

Then, from normal macOS:

```bash
sudo defaults delete /Library/Preferences/com.apple.security.libraryvalidation.plist DisableLibraryValidation 2>/dev/null || true
```

Reboot into Recovery and run:

```bash
csrutil enable
```

After boot:

```bash
csrutil status
defaults read /Library/Preferences/com.apple.security.libraryvalidation.plist DisableLibraryValidation 2>/dev/null || true
imsg status --json | jq .
openclaw channels status --probe --channel imessage
openclaw message send --channel imessage --target chat_id:171 --message "SIP re-enabled iMessage test" --json
```

Expected under Option B:

- SIP enabled.
- Private API unavailable.
- Basic send/receive still works.
- No configured private API action is advertised as required for production
  workflows.

## Rollback

The routine rollback window closed on 2026-06-27 when Phase 6 removed the
BlueBubbles package, app, services, credentials, and active configuration.

Recovery now means repairing the supported native path: validate `imsg`, repair
macOS permissions if needed, restart the gateway, and restore the current
OpenClaw config or SQLite state from backup. Restoring BlueBubbles would require
a fresh, explicit cask, plugin, credential, webhook, LaunchAgent, and channel
configuration installation from the archived pre-migration backup. It is not a
supported operational rollback.

## Completed Post-Migration Improvements

- Added the native iMessage health card and `/api/imessage-health` endpoint on
  2026-06-27. The passive, cached checks cover gateway health, its attached
  `imsg rpc` delivery worker, native bridge readiness, advanced/v2 capability,
  and privacy-safe latest-delivery metadata when readable; they do not send a
  synthetic message.
- Enabled global inbound attachment ingestion with an explicit local Messages
  root and 16 MB cap; the iMessage provider hot-reloaded and probed healthy.
- Repaired Tailscale Serve by upgrading the Homebrew CLI, correcting the
  source/live gateway launcher PATH so it selects the logged-in macOS backend,
  and reapplying the tailnet-only HTTPS/WSS route. A clean gateway restart
  logged `serve enabled`, and the HTTPS Control UI plus `/health` returned 200.
- Audited the SQLite cron cutover, recalculated the Q3 and Q4 double-date jobs
  to their intended 14:00 UTC schedules, removed three consumed World Cup
  definitions, and hardened deployment so stale one-shot runtime timestamps
  trigger a gateway-side schedule recalculation instead of being preserved.

## Resolved Post-Migration Decisions

- Explicit-handle tightening was rejected. Open direct-message and group
  admission, wildcard sender lists, and the wildcard group registry remain in
  production by operator choice.
- Global inbound attachments were approved. This covers trusted contacts and
  family, and—because admission remains open—also covers every other admitted
  sender. OpenClaw `2026.6.10` has no per-sender attachment switch.
- The SIP-disabled/library-validation-relaxed risk is accepted. No separate
  risk record or review cadence is required.
- Tailscale Serve was repaired and retained for private tailnet HTTPS/WSS access
  to the loopback-only gateway.

## Target Documentation Snapshot

The `openclaw@2026.6.10` package docs state:

- BlueBubbles support is removed.
- `channels.bluebubbles` is not a supported runtime config surface.
- Migrate old configs to `channels.imessage`.
- iMessage uses `imsg rpc` over stdio.
- `channels.imessage.includeAttachments` is off by default; this deployment
  explicitly enables it globally.
- Private API actions require `imsg launch` and a successful private API probe.
- Basic text/media send and receive can work without SIP changes.
- Advanced private API mode requires SIP disabled and library validation relaxed.
