---
name: openclaw-channel-send-timeout-false-failure
description: |
  Fix OpenClaw channel sends (BlueBubbles/iMessage, and likely Telegram/Slack/Discord)
  that report "request timed out" or "message failed" but actually deliver — causing
  the agent to retry and produce duplicate messages. Use when: (1) recipient confirms
  they received a message that OpenClaw logged as failed, (2) duplicate messages
  arrive after the agent says "let me retry", (3) `/tmp/openclaw/openclaw-*.log`
  shows `[tools] message failed: request timed out` paired with a `subsystem:
  fetch-timeout` entry whose `elapsedMs ≈ timeoutMs` and `operation:
  fetchWithSsrFGuard`, (4) the timed-out URL is the channel's localhost server
  (e.g., `http://localhost:1234/api/v1/chat/new` for BlueBubbles). Root cause: the
  channel plugin's hardcoded 30s send timeout is too short for endpoints that
  round-trip an external service (BB → Apple iMessage backend for new-chat
  creation). The channel server queues the work before responding, so the send
  succeeds despite OpenClaw aborting. Fix: bump the per-channel `sendTimeoutMs`
  in openclaw.json (BB) or analogous config field on other channels.
author: Claude Code
version: 1.0.0
date: 2026-05-23
---

# OpenClaw Channel Send Timeout Causes False-Failure + Duplicate Sends

## Problem

OpenClaw's channel plugins (BlueBubbles, Telegram, Slack, Discord) wrap outbound message sends in a per-channel HTTP timeout. The hardcoded default is **30 seconds** for BlueBubbles (`@openclaw/bluebubbles/dist/reactions-D1ETgpPi.js:326` shows `opts.timeoutMs ?? sendTimeoutMs ?? 3e4`). Some legitimate sends take longer:

- BlueBubbles' `/api/v1/chat/new` endpoint round-trips Apple's iMessage backend to verify the target address — observed 30-90s on new/uncached recipients.
- Telegram's getUpdates, file uploads, and similar long-poll endpoints can also exceed the default.

The channel server (BB, Telegram, etc.) typically **queues the message internally before responding**, so the send happens regardless of whether OpenClaw waited for the HTTP response. From the agent's perspective the tool call returns "request timed out", so it retries — producing duplicate messages on the recipient's end.

## Context / Trigger Conditions

- Recipient confirms receiving a message that OpenClaw logged as failed
- Duplicate messages arrive after the agent says "let me retry" / "BB is being stubborn" / similar
- `/tmp/openclaw/openclaw-YYYY-MM-DD.log` contains a pair of entries close in time:
  ```
  {"subsystem":"fetch-timeout"}, {"timeoutMs":30000,"elapsedMs":30002,
    "operation":"fetchWithSsrFGuard",
    "url":"http://localhost:1234/api/v1/chat/new"}
  [tools] message failed: request timed out raw_params={"action":"send",
    "channel":"bluebubbles", ...}
  ```
- The `elapsedMs` is essentially equal to `timeoutMs` (a few ms over) — this is the signature of a timeout, not a real failure
- The URL is the channel's *own* localhost server, not an external API

## Solution

### 1. Identify the channel and the right config field

| Channel | Config path | Default |
|---------|-------------|---------|
| BlueBubbles | `channels.bluebubbles.sendTimeoutMs` | 30000 ms |
| Telegram | `channels.telegram.timeoutSeconds` (note: seconds, not ms; the plugin has `resolveTelegramRequestTimeoutMs`) | varies by method |
| Slack/Discord | grep `~/.openclaw/npm/node_modules/@openclaw/<channel>/dist/` for `timeoutMs` / `TIMEOUT_MS` constants | varies |

Confirm the config knob exists in the plugin's `config-schema-*.js`:
```bash
grep -n "sendTimeoutMs\|timeoutSeconds\|requestTimeoutMs" \
  ~/.openclaw/npm/node_modules/@openclaw/<channel>/dist/config-schema-*.js
```

### 2. Set a higher timeout in openclaw.json

For BlueBubbles (the verified case):
```json
"channels": {
  "bluebubbles": {
    "enabled": true,
    "serverUrl": "http://localhost:1234",
    "password": "${BLUEBUBBLES_PASSWORD}",
    "sendTimeoutMs": 90000,
    ...
  }
}
```

90s is a reasonable starting point — 3× the default, comfortable headroom for new-chat creation, still short enough that a real channel outage fails fast.

### 3. Deploy the config

Per the project's pattern, `~/.openclaw/openclaw.json` is **not** symlinked from the dotfiles repo — `dotfiles-pull.command` does not deploy it. Update both copies:
```bash
# Edit the dotfiles copy
$EDITOR ~/dotfiles/openclaw/openclaw.json
# Push to the Mini (or wherever the gateway runs)
scp -i ~/.ssh/id_launchd -o IdentitiesOnly=yes \
  ~/dotfiles/openclaw/openclaw.json \
  dbochman@dylans-mac-mini:.openclaw/openclaw.json
```

### 4. Verify the gateway picks it up

Per OpenClaw memory: "Gateway hot-reloads config changes without restart." The BB plugin specifically re-reads `account.config.sendTimeoutMs` on every send (`@openclaw/bluebubbles/dist/probe-B4I0cEVm.js:172` calls `resolveBlueBubblesServerAccount` per-request). So the next send picks up the new value with no restart.

```bash
ssh dbochman@dylans-mac-mini 'grep -n "sendTimeoutMs" ~/.openclaw/openclaw.json'
# Expect: 78:      "sendTimeoutMs": 90000,
```

## Verification

After the change, a slow send (e.g., to a fresh recipient who has never received from this account before) should:

1. Take whatever wall-clock time the channel server needs (potentially 30-60s)
2. Return success — no `fetch-timeout` log entry, no `[tools] message failed`
3. Deliver exactly once to the recipient

If the symptom persists after a config bump, check that the gateway actually reloaded:
```bash
tail -50 ~/.openclaw/logs/gateway.log | grep -iE "config|reload"
```

If the file change isn't being detected (rare), restart the gateway:
```bash
launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway
```

## Notes

- This is a tradeoff. Raising the timeout means a *genuinely* failed send (BB server crashed, network down) takes longer to surface to the agent. 90s is forgiving; don't go above 180s without good reason.
- The same pattern likely applies to *any* OpenClaw fetch that crosses a slow boundary. Search the plugin source for `*Timeout*` constants and check `config-schema-*.js` for the override field.
- **Do NOT** try to fix this by making the agent more aggressive about not retrying — the agent has no way to know whether "request timed out" means "abort" or "actually it worked, just be patient." The right fix is at the timeout layer.
- The 30002 ms (vs exactly 30000) is the AbortController firing a tick after the deadline. Any `elapsedMs` within ~5ms of `timeoutMs` is a timeout, not a real failure.
- BB's `/api/v1/chat/new` is the most common slow endpoint because it has to verify iMessage capability with Apple. Once a chat GUID is established, subsequent sends to that GUID via `/api/v1/message/text` are typically <1s. A long-term optimization is to cache chat GUIDs locally and skip `/chat/new`, but that's an OpenClaw-side change, not a user-side fix.

## Diagnostic recipe

When a user reports "OpenClaw sent the same iMessage twice":

```bash
# 1. Pull the most recent openclaw log
ssh dbochman@dylans-mac-mini 'ls -t /tmp/openclaw/openclaw-*.log | head -1' \
  | xargs -I{} ssh dbochman@dylans-mac-mini 'tail -500 {}' \
  | grep -E "fetch-timeout|message failed"

# 2. Look for the elapsedMs ≈ timeoutMs signature
# 3. Note the URL — confirms which channel + which endpoint is slow
# 4. Bump the channel's sendTimeoutMs (or equivalent) in openclaw.json
# 5. scp to Mini, verify, done — no restart needed
```

## References

- OpenClaw `@openclaw/bluebubbles` plugin: `~/.openclaw/npm/node_modules/@openclaw/bluebubbles/dist/`
  - `reactions-D1ETgpPi.js:326` — hardcoded 3e4 default
  - `probe-B4I0cEVm.js:172` — per-request `sendTimeoutMs` resolution
  - `config-schema-a7F7uzDv.js:77` — Zod schema entry
- BlueBubbles HTTP API: <https://documenter.getpostman.com/view/765844/UV5RnfwM>
- Related: see [[openclaw-ssrf-localhost-plugins]] for the `dangerouslyAllowPrivateNetwork` config that lets the BB plugin reach localhost in the first place.
