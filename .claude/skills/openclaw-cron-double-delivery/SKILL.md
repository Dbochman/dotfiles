---
name: openclaw-cron-double-delivery
description: |
  Fix OpenClaw cron jobs delivering messages multiple times (2x or 3x) to the recipient.
  Use when: (1) recipient reports receiving the same cron job output multiple times,
  (2) cron job shows status "error" with "Message failed" but delivery shows "delivered",
  (3) agent prompt instructs the agent to send messages directly (via message tool, imsg,
  or similar) AND the job has a delivery config — causing both the agent AND the cron
  delivery system to send separately, (4) gateway logs show "Unknown channel: bluebubbles"
  or "Unknown channel: imessage" errors from agent tool calls during cron execution.
author: Claude Code
version: 1.0.0
date: 2026-03-09
---

# OpenClaw Cron Double/Triple Delivery

## Problem
A cron job delivers its output to the recipient multiple times per run. The job may also
report `status: error` with `"Message failed"` even though delivery succeeds, and
`consecutiveErrors` increments causing backoff delays.

## Context / Trigger Conditions
- Recipient reports receiving the same briefing/message 2-3 times
- Run history (JSONL) shows: `"status":"error"`, `"error":"Message failed"`, but
  `"delivered":true`, `"deliveryStatus":"delivered"`
- Gateway logs show: `[tools] message failed: Unknown channel: bluebubbles` or
  `[tools] message failed: Unknown target "X" for imessage`
- The job prompt tells the agent to send messages directly (e.g., `imsg send --to`,
  or instructs it to use the `message` tool)
- The job also has a `delivery` config block in jobs.json

## Root Cause
OpenClaw cron jobs have **two independent delivery paths**:

1. **Agent tool calls**: The agent can use the `message` tool or `imsg` CLI during
   execution to send messages directly
2. **Cron delivery system**: After the job finishes, the cron system reads the job's
   `delivery` config and sends the agent's final summary text to the specified target

When the prompt instructs the agent to send messages AND the job has a delivery config,
both paths fire. If the agent's message tool call also fails (e.g., channel mismatch),
the agent may retry, creating additional deliveries. Meanwhile the cron delivery system
still sends successfully, marking `delivered: true`.

The `status: error` comes from the agent's failed tool call, not from the cron delivery.
This is why the job shows "error" but "delivered" simultaneously.

## Solution

**The fix is simple: never have the agent send messages directly in cron job prompts.**
Let the cron delivery system handle all message delivery.

### In the prompt, add a delivery directive:
```
DELIVERY: Do NOT use the message tool or imsg. Your final text output IS the briefing
-- the cron system delivers it automatically.
```

### Remove any direct send instructions:
- Remove `imsg send --to ...` commands
- Remove instructions to "send a message to Julia/Dylan"
- Change "Send error to Dylan" to "Output: [error message]" (cron delivery handles it)

### For error notifications:
Instead of having the agent send error messages to a different person, just have it
output the error text. The cron delivery will send it to the configured recipient.
If you need errors routed to a different person, create a separate error-notification
job or handle it at the gateway level.

### After fixing the prompt:
1. Reset error state on Mini:
   ```python
   j["state"]["consecutiveErrors"] = 0
   j["state"]["lastRunStatus"] = "ok"
   j["state"]["lastStatus"] = "ok"
   del j["state"]["lastError"]  # if present
   ```
2. Bump `updatedAtMs` to `int(time.time() * 1000)` for gateway hot-reload
3. Verify `nextRunAtMs` is correct (scp from dotfiles may overwrite Mini's live value)

### Reference pattern (Dylan's working briefing job):
```
"message": "You are Dylan's morning briefing assistant...
  ...Compose Briefing\n\nFormat a concise iMessage-friendly summary:..."
```
No mention of sending, messaging, or delivery. The agent just outputs text, and the
cron delivery config handles the rest.

## Verification
- Next cron run should show `"status":"ok"` in the JSONL run history
- Recipient receives exactly one message
- No `[tools] message failed` errors in gateway log for that job's session

## Example
Before (broken):
```
If this fails with any auth error:
- Send error to Dylan via iMessage: imsg send --to dylanbochman@gmail.com "AUTH ERROR..."
...
The briefing will be delivered to Julia via iMessage automatically.
```

After (fixed):
```
DELIVERY: Do NOT use the message tool or imsg. Your final text output IS the briefing
-- the cron system delivers it automatically.

If this fails with any auth error:
- Output: "Morning briefing skipped -- auth error. Dylan: re-auth and scp credentials."
...
Keep it brief and scannable.
```

## Notes
- The cron `delivery.mode: "announce"` sends the agent's final summary text as a message
- `delivery.channel: "bluebubbles"` and `delivery.to` control where it goes
- The agent's `message` tool and the cron delivery are completely independent — there is
  no deduplication between them
- This pattern applies to ALL cron jobs, not just briefings — any job whose prompt tells
  the agent to send messages while also having a delivery config will double-deliver
- Related skill: `openclaw-cron-job-creation` covers jobs.json schema and creation
- Related skill: `openclaw-cron-ghost-jobs` covers jobs running after removal
