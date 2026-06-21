---
name: openclaw-cron-job-creation
description: >-
  Create and debug OpenClaw cron jobs by editing jobs.json directly.
author: Claude Code
version: 2.1.0
date: 2026-06-21
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

The repo file is canonical. Do not make a live-only edit and assume it is
durable; the next scheduled or manual dotfiles deployment replaces it.

1. Edit `~/dotfiles/openclaw/cron/jobs.json`.
2. Validate it: `python3 -m json.tool ~/dotfiles/openclaw/cron/jobs.json >/dev/null`.
3. Deploy it: `~/dotfiles/openclaw/sync-cron-jobs.sh deploy`.
4. For an immediate CLI edit, apply the same change with `openclaw cron edit`
   and still commit the repo copy.
5. Verify both `openclaw cron list --json` and
   `~/.openclaw/cron/jobs.json`; a gateway restart is normally unnecessary.

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

Remove the job from both gateway state and the canonical repo definition:

```bash
openclaw cron rm <jobId>
# Delete the matching object from ~/dotfiles/openclaw/cron/jobs.json and commit it.
~/dotfiles/openclaw/sync-cron-jobs.sh deploy
```

Keep `~/.openclaw/cron/runs/<jobId>.jsonl` as append-only audit history. For
successful `deleteAfterRun` one-shots, `sync-cron-jobs.sh deploy` uses that
record as a tombstone and refuses to restore the completed repo definition.
See the `openclaw-cron-ghost-jobs` skill for diagnosis.

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
        "to": "+1XXXXXXXXXX"
    },
    "state": {}
}

data["jobs"].append(job)
with open("/Users/dbochman/.openclaw/cron/jobs.json", "w") as f:
    json.dump(data, f, indent=2)
```

## Notes

- **Repo/live drift**: CLI edits can be correct in gateway memory and the live
  file yet still be reverted by the next repo deployment. Make the repo change
  in the same operation.
- **Channel name**: Delivery channel is `bluebubbles` (NOT `imessage`). All cron jobs use
  `"delivery": {"channel": "bluebubbles", ...}`. The dotfiles source copy is at
  `~/dotfiles/openclaw/cron/jobs.json` — keep it in sync with Mini's
  `~/.openclaw/cron/jobs.json`.
- The gateway wrapper (`~/Applications/OpenClawGateway.app/Contents/MacOS/OpenClawGateway`) sources secrets from `~/.openclaw/.secrets-cache` (KEY=VALUE format, chmod 600)
- Python f-strings with `j["name"]` inside SSH-piped python one-liners cause `NameError` due to shell escaping — use script files (`scp` + run) for complex JSON manipulation
- The `delivery.to` field accepts both E.164 phone numbers and OpenClaw contact UUIDs
- Gateway auto-adds `deleteAfterRun: true` for `"at"` schedule jobs during normalization
- Side-effecting one-shots must also use `delivery.mode: none` and an external-system plus calendar idempotency check. `deleteAfterRun` alone is not a duplicate-prevention strategy.
