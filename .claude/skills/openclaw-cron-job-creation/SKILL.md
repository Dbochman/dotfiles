---
name: openclaw-cron-job-creation
description: |
  Create and debug OpenClaw cron jobs by editing jobs.json directly. Use when:
  (1) gateway/cron subsystem fails with "TypeError: Cannot read properties of undefined
  (reading 'trim')" after adding jobs, (2) creating recurring cron schedule jobs (not
  one-shot "at" jobs), (3) openclaw cron CLI commands fail with missing env vars,
  (4) need to manually run/test a cron job. Covers the correct jobs.json schema,
  schedule field naming (expr NOT cron), env var requirements, and gateway restart.
author: Claude Code
version: 2.0.0
date: 2026-03-06
---

# OpenClaw Cron Job Creation

## Problem
Creating OpenClaw cron jobs by editing `~/.openclaw/cron/jobs.json` directly can cause
the gateway's cron subsystem to crash on startup with a misleading error if the JSON
schema doesn't match what the gateway expects.

## Context / Trigger Conditions
- Gateway log shows: `[gateway/cron] failed to start: TypeError: Cannot read properties of undefined (reading 'trim')`
- Adding a new cron job to `jobs.json` and restarting gateway
- The `openclaw cron add` CLI fails because it needs env vars (`OPENCLAW_GATEWAY_TOKEN`, `OPENAI_API_KEY`, `ELEVENLABS_API_KEY`, etc.) that are only available inside the gateway wrapper

## Solution

### Correct jobs.json Schema for Recurring Cron Jobs

```json
{
  "id": "<uuid4>",
  "agentId": "main",
  "name": "My Cron Job",
  "enabled": true,
  "createdAtMs": 1771191000000,
  "updatedAtMs": 1771191000000,
  "schedule": {
    "kind": "cron",
    "expr": "0 7 * * *",
    "tz": "America/New_York"
  },
  "sessionTarget": "isolated",
  "wakeMode": "next-heartbeat",
  "payload": {
    "kind": "agentTurn",
    "message": "Your agent prompt here"
  },
  "delivery": {
    "mode": "announce",
    "channel": "bluebubbles",
    "to": "+15551234567"
  },
  "state": {}
}
```

### Critical Field Names

| Field | Correct | Wrong | Notes |
|-------|---------|-------|-------|
| Cron expression | `schedule.expr` | `schedule.cron` | Source code: `coerceSchedule()` checks `schedule.expr` |
| Schedule type | `schedule.kind: "cron"` | - | Other kinds: `"at"`, `"every"` |
| Timezone | `schedule.tz` | `schedule.timezone` | IANA format, optional |

### Schedule Kinds Reference

```json
// Recurring cron
{ "kind": "cron", "expr": "0 7 * * *", "tz": "America/New_York" }

// One-shot at time
{ "kind": "at", "at": "2026-04-01T12:00:00.000Z" }

// Interval
{ "kind": "every", "everyMs": 3600000 }
```

### Adding/Editing Jobs Workflow

**CRITICAL**: The gateway holds jobs.json in memory and writes its in-memory copy back to
disk whenever it updates job state (lastStatus, lastRunAtMs, etc.). If you edit the file
while the gateway is running, the gateway will overwrite your changes on the next state write.

1. **Stop gateway**: `launchctl bootout gui/$(id -u)/ai.openclaw.gateway`
2. **Confirm stopped**: `pgrep -fl openclaw-gateway` (should return nothing)
3. **Backup**: `cp ~/.openclaw/cron/jobs.json ~/.openclaw/cron/jobs.json.bak`
4. **Edit**: Add/modify jobs in the `jobs` array (use Python script for complex prompts)
5. **Start gateway**: `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.openclaw.gateway.plist`
6. **Verify**: Check `/tmp/openclaw/openclaw-YYYY-MM-DD.log` for cron startup line:
   - Success: `{'enabled': True, 'jobs': N, 'nextWakeAtMs': ...}`
   - Failure: `failed to start: TypeError: Cannot read properties of undefined (reading 'trim')`

**Do NOT use `launchctl kickstart -k`** for config changes — the restart is too fast and the
gateway may read the old file from OS disk cache before the write flushes. Stop, edit, start.

### Running Jobs Manually

Use `openclaw cron run` with secrets sourced and a long timeout:
```bash
set -a && source ~/.openclaw/.secrets-cache && set +a && \
PATH=/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:$PATH \
openclaw cron run <jobId> --timeout 300000 --expect-final
```

Key flags:
- `--timeout 300000` — 5 minutes (default 30s is too short for most jobs)
- `--expect-final` — waits for the job to complete and returns `{"ok":true,"ran":true}`

This is the **correct** way to manually trigger a job. It:
- Runs through the gateway's cron scheduler (single execution, no duplicates)
- Uses the exact prompt, delivery channel, and target from `jobs.json`
- Returns synchronous feedback on success/failure

**Do NOT use `openclaw agent --deliver`** for manual cron testing — it spawns independent
async agents with no dedup. Each invocation creates a separate session that runs all side
effects (labeling, archiving, sending) independently, causing duplicate deliveries if
retried.

### Removing Jobs

When removing a job from `jobs.json`, **also delete its run state file**:
```bash
trash ~/.openclaw/cron/runs/<jobId>.jsonl
```

The cron subsystem persists `nextRunAtMs` in these JSONL files. If a run file exists with
a future `nextRunAtMs`, the job will keep executing even after its definition is removed
from `jobs.json`. See the `openclaw-cron-ghost-jobs` skill for diagnosis and cleanup.

### Fields NOT to Include

- `deleteAfterRun`: Only for one-shot `"at"` jobs (auto-added by gateway for `"at"` kind)
- `timeoutSeconds`: Goes inside `payload`, not at the top level (gateway handles via `coercePayload`)

## Verification

After restarting the gateway, check the detailed log:
```bash
grep "cron" /tmp/openclaw/openclaw-$(date +%Y-%m-%d).log | python3 -c "
import sys, json
for line in sys.stdin:
    d = json.loads(line)
    print(d.get('time','') + ': ' + str(d.get('1', d.get('0','')))[:150])
"
```

Success shows: `{'enabled': True, 'jobs': <count>, 'nextWakeAtMs': <timestamp>}`

## Example

Adding a daily Gmail triage job for Julia:
```python
import json, uuid, time

with open("/Users/dbochman/.openclaw/cron/jobs.json") as f:
    data = json.load(f)

job = {
    "id": str(uuid.uuid4()),
    "agentId": "main",
    "name": "Julia Gmail Morning Triage",
    "enabled": True,
    "createdAtMs": int(time.time() * 1000),
    "updatedAtMs": int(time.time() * 1000),
    "schedule": {
        "kind": "cron",
        "expr": "0 7 * * *",       # NOT "cron": "0 7 * * *"
        "tz": "America/New_York"
    },
    "sessionTarget": "isolated",
    "wakeMode": "next-heartbeat",
    "payload": {
        "kind": "agentTurn",
        "message": "Your prompt here..."
    },
    "delivery": {
        "mode": "announce",
        "channel": "bluebubbles",
        "to": "+15084234853"
    },
    "state": {}
}

data["jobs"].append(job)
with open("/Users/dbochman/.openclaw/cron/jobs.json", "w") as f:
    json.dump(data, f, indent=2)
```

## Notes

- **Gateway in-memory clobbering**: The gateway loads jobs.json into memory at startup
  and writes its copy back to disk on every state update. Edits made while the gateway is
  running WILL be overwritten. Always stop → edit → start. Error `"Unsupported channel: X"`
  after a channel migration is a telltale sign the gateway wrote back stale config.
- **Channel name**: Delivery channel is `bluebubbles` (NOT `imessage`). All cron jobs use
  `"delivery": {"channel": "bluebubbles", ...}`. The dotfiles source copy is at
  `~/repos/dotfiles/openclaw/cron/jobs.json` — keep it in sync with Mini's
  `~/.openclaw/cron/jobs.json`.
- The gateway wrapper (`~/Applications/OpenClawGateway.app/Contents/MacOS/OpenClawGateway`) sources secrets from `~/.openclaw/.secrets-cache` (KEY=VALUE format, chmod 600)
- Python f-strings with `j["name"]` inside SSH-piped python one-liners cause `NameError` due to shell escaping — use script files (`scp` + run) for complex JSON manipulation
- The `delivery.to` field accepts both E.164 phone numbers and OpenClaw contact UUIDs
- Gateway auto-adds `deleteAfterRun: true` for `"at"` schedule jobs during normalization
- Source code reference: `loader-n6BPnYom.js` lines 9183-9470 contain cron normalization logic
