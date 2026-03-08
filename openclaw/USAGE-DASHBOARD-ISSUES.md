# OpenClaw Usage Dashboard: Issues & Redesign Plan

## Status: v2 Deployed (2026-03-07)

Commit `94b5628` — rebuilt dashboard with gauges, fixed snapshot parser, deployed to Mini.

**What's working now:**
- SVG utilization gauges with pacing indicators (chill/on-track/hot)
- Utilization over time chart with resolved hex colors (visible now)
- Cron job table with status badges, duration, model
- Tokens-by-job doughnut chart + token usage over time bar chart
- Cron duration trends chart
- Activity chart (agents/messages/cron)
- Staleness detection (warns if data >30 min old)
- Error banner on fetch failure

**Token counts will populate going forward** — the parser fix reads `usage.input_tokens`
from cron JSONL correctly now, but old snapshots were collected with the broken parser.

## Original State (pre-v2)

Only **1 of 5 charts worked** (Utilization Over Time, barely visible). Token Usage, Activity,
Response Times, and Errors were all effectively dead. Cards showed 0 tokens and 0 agent/message
activity. The dashboard provided almost no value beyond what the raw Anthropic usage API shows.

---

## Issues: Data Collection (`usage-snapshot.sh`)

### 1. Token counts are always zero

The cron run JSONL records `usage.input_tokens`, `usage.output_tokens`, `usage.total_tokens`
(nested under `usage`), but the snapshot parser looks for top-level `inputTokens`/`input_tokens`.
The field names don't match, so every cron job reports 0 tokens.

**Evidence** (Julia morning briefing JSONL):
```json
{"action":"finished", "usage":{"input_tokens":19,"output_tokens":10408,"total_tokens":46331}, ...}
```

**Snapshot parser expects** (`usage-snapshot.sh:238-240`):
```python
it = rec.get("inputTokens", rec.get("input_tokens", 0)) or 0  # looks at top level
```

**Fix**: Look inside `rec.get("usage", {})` for token fields.

### 2. Runtime log format mismatch kills Activity, Errors, and Response Times

The log parser pattern-matches on `"agent run"`, `"message sent"`, `"delivered"`, `"error"`.
But the actual OpenClaw runtime log uses a tslog-style format with:
- Messages in `"0"`, `"1"`, `"2"` numbered keys (not `"msg"`)
- Log level in `_meta.logLevelName` (not `"level"`)
- No `response_time_ms` or `duration_ms` fields

**Actual log format**:
```json
{
  "0": "{\"subsystem\":\"gateway\"}",
  "1": "listening on ws://127.0.0.1:18789",
  "_meta": {"logLevelId": 3, "logLevelName": "INFO", "date": "..."},
  "time": "2026-03-06T06:50:45.694-05:00"
}
```

**Parser looks for** (`usage-snapshot.sh:165-178`):
```python
msg = rec.get("msg", "").lower()      # always ""
level = rec.get("level", "")          # always ""
```

**Result**: `agent_runs`, `messages_sent`, `errors`, `gateway_restarts` are always 0.
`response_times_ms` is always empty. This kills 4 of 5 charts.

### 3. Runtime log is 99% noise

Today's log has **145,986 lines**. Almost all are:
- `"cron: timer armed"` — fires every 60 seconds, ~1440/day
- `"Config invalid" / "Missing env var"` — crash-loop spam at midnight before wrapper starts

There are no log entries for agent runs, message sends, or delivery events in the runtime log.
These events are only tracked in the **cron run JSONL files** (`~/.openclaw/cron/runs/*.jsonl`).

### 4. Midnight gap in log parsing

`log_offset` tracks position in today's log file. But the snapshot runs every 15 minutes.
If the last snapshot of the day runs at 11:45 PM, log entries between 11:45 PM and midnight
are never read — the next day starts with offset 0 on a new file.

### 5. Utilization data is null when OAuth token expires

The first snapshot today shows `"utilization": null`. The OAuth access token from the MacBook
(pushed every 30 min via `usage-token-push`) can expire or stale. No fallback, no warning.

### 6. No deduplication of cron run records

If the snapshot state file is lost or reset, all historical cron run records are re-counted,
inflating totals. The offset-based tracking has no idempotency.

---

## Issues: Dashboard UI (`usage-dashboard.py`)

### 7. CSS variables don't work in Chart.js canvas

`buildUtilChart` uses `borderColor: 'var(--blue)'` — but Chart.js renders on `<canvas>`, which
doesn't resolve CSS custom properties. Lines render as black/invisible on dark backgrounds.
This is why the Utilization chart lines are barely visible in the screenshot.

### 8. Charts destroy/recreate on every refresh

Every 5 minutes, all 5 Chart instances are `.destroy()`ed and recreated, causing a visual flash.
Should update datasets in-place using `chart.data.datasets = ...` + `chart.update()`.

### 9. No staleness indicator

The "Updated" timestamp shows when the latest *snapshot* was taken, not when the dashboard last
polled. If the snapshot LaunchAgent stops, the dashboard shows stale data with no warning.

### 10. No error/empty state

If `/api/data` returns no snapshots or fails, the UI shows "Loading..." forever. No error
message, no retry button.

### 11. Card aggregation sums deltas but labels suggest totals

`aggregateForCards()` sums `tokens` and `activity` across snapshots. Since each snapshot is
a delta (new events since last snapshot), the sum is correct for the period. But with tokens
always at 0, this is moot. The real issue is that the cards aren't useful when the data is empty.

### 12. X-axis labels are unreadable

The screenshot shows overlapping, rotated timestamps on all charts. The time axis doesn't
adapt its tick density to the chart width.

---

## What We Actually Have (Reliable Data Sources)

| Source | Data Available | Reliability |
|--------|---------------|-------------|
| Anthropic Usage API | 5h/7d utilization %, per-model %, extra credits, reset times | Good (when OAuth valid) |
| Cron run JSONL | Job ID, status, duration, model, token usage, delivery status, summaries | Good |
| Runtime log | Gateway start/stop, cron timer, subsystem lifecycle, errors | Noisy but parseable |

**Not available anywhere**: Ad-hoc agent runs, interactive message counts, response latencies.
These would require OpenClaw to emit structured events (it doesn't currently).

---

## Redesign Priorities

Based on user priorities: **Rate Limits > Agent/Cron Activity > Token Attribution**

### Priority 1: Rate Limit Monitoring (TokenEater-inspired)

- **Utilization gauges** — Circular progress indicators for 5h and 7d limits (like TokenEater)
- **Pacing indicator** — "chill" / "on track" / "hot" based on burn rate vs. time remaining
- **Reset countdowns** — Prominent countdown timers for each limit window
- **Per-model breakdown** — Opus vs Sonnet 7d utilization side-by-side
- **Extra credits tracking** — Current spend vs monthly limit
- **Threshold alerts** — Visual warning at 50%, 80%, 100% utilization

### Priority 2: Agent/Cron Activity

- **Cron job table** — List of recent runs with status, duration, model, token cost
- **Job timeline** — Gantt-style view of cron executions over time
- **Success/failure rates** — Per-job success rate badges
- **Duration trends** — Are jobs getting slower over time?

### Priority 3: Token Attribution

- **Per-job token breakdown** — Which cron jobs consume the most tokens?
- **Model split** — Opus vs Sonnet token consumption
- **Daily/weekly token totals** — Aggregate from cron JSONL (the only reliable source)

### UX Improvements (from TokenEater)

- **Color-coded zones** — Green/amber/red thresholds throughout
- **Dark theme done right** — Resolve actual hex colors, not CSS vars, for canvas
- **Compact layout** — Fewer massive empty charts, more information density
- **Auto-refresh without flash** — Update chart data in-place
- **Staleness detection** — Show warning if last snapshot is >30 minutes old
- **Mobile-friendly** — Responsive cards and charts

---

## Implementation Order

1. ~~**Fix snapshot parser** — Match actual cron JSONL token field paths (`usage.input_tokens`)~~ DONE
2. ~~**Fix runtime log parser** — Match tslog format (`_meta.logLevelName`, `"0"`/`"1"` keys)~~ DONE
3. ~~**Fix Chart.js CSS vars** — Use resolved hex colors~~ DONE
4. ~~**Redesign dashboard layout** — Utilization gauges + cron table + token chart~~ DONE
5. ~~**Add pacing/burn-rate** — Derived from utilization percentage thresholds~~ DONE
6. ~~**Add staleness detection** — Compare last snapshot time vs now~~ DONE

## Remaining / Future Work

- **Midnight log gap** — Log entries between last snapshot of day and midnight are lost (issue #4)
- **OAuth token staleness** — No fallback when token expires, utilization goes null (issue #5)
- **Cron dedup** — If state file is reset, historical records are re-counted (issue #6)
- **More granular pacing** — Current pacing is threshold-based; could use slope of utilization history
- **Per-model token attribution** — Show Opus vs Sonnet token split (data is in cron JSONL `model` field)
