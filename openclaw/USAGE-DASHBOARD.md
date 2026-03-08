# OpenClaw Usage Dashboard

## Status: v3 (2026-03-08)

Single-file Python HTTP server + embedded Chart.js UI. Serves at port 8551 on Mac Mini, Tailscale-only access.

### Architecture

| Component | Path (Mini) | Purpose |
|-----------|-------------|---------|
| `usage-dashboard.py` | `~/.openclaw/bin/` | HTTP server + embedded HTML/JS dashboard |
| `usage-snapshot.sh` | `~/.openclaw/bin/` | Data collector, runs every 15 min via LaunchAgent |
| History JSONL | `~/.openclaw/usage-history/YYYY-MM-DD.jsonl` | One file per day, append-only snapshots |
| State file | `~/.openclaw/usage-history/.snapshot-state` | Tracks log/cron offsets between snapshots |
| OAuth cache | `~/.openclaw/.anthropic-oauth-cache` | Pushed from MacBook every 30min |

### Data Sources

| Source | What It Provides | Collected By |
|--------|-----------------|--------------|
| Anthropic Usage API | 5h/7d utilization %, per-model %, extra credits, reset times | `fetch_utilization()` via OAuth token |
| Cron run JSONL | Job ID, status, duration, model, token usage, delivered flag | `parse_cron_runs()` via offset tracking |
| Runtime log | Gateway restarts, errors (tslog format) | `parse_runtime_log()` via offset tracking |
| BlueBubbles API | Messages sent/received counts per interval | `fetch_bb_messages()` via `message/query` endpoint |

### Dashboard Features

**Utilization gauges** — SVG ring gauges for 5-Hour, 7-Day, Opus 7d, Sonnet 7d, Extra Credits. Color-coded green/amber/red with pacing labels (chill/on-track/hot) and reset countdowns.

**Stat cards** — Total tokens (in/out), cron runs (with failure count), messages (sent/recv), errors, gateway restarts.

**Charts:**
- Utilization Over Time — line chart with 100% threshold line
- Tokens by Job — doughnut chart
- Token Usage Over Time — stacked bar chart (input/output), adaptive bucket sizes
- Cron Duration Trends — per-job line chart
- Activity — stacked bar chart (sent/received/cron), adaptive bucket sizes

**Cron table** — Recent runs with job ID, status badge, delivered column (✓/✗), model, duration, tokens, time.

**Time controls** — 6h, 24h, 7d, 30d with adaptive chart bucketing.

### Adaptive Chart Bucketing

Bar charts (Activity, Token Usage) use bucket sizes that scale with the time range:

| Time Range | Bucket Size | Rationale |
|------------|-------------|-----------|
| 6h, 24h | Hourly | Fine granularity, bars fill the chart width |
| 7d | 12-hour (AM/PM) | Keeps bars thick enough to read |
| 30d | Daily | One bar per day, clear daily patterns |

### BlueBubbles Message Integration

Added in v3. The snapshot script queries BB's `POST /api/v1/message/query` endpoint with an `after` timestamp (from previous snapshot). Counts `isFromMe=true` as sent, `isFromMe=false` as received. Reads `BLUEBUBBLES_PASSWORD` from `~/.openclaw/.secrets-cache`.

Historical data backfilled from BB for Feb 1 – Mar 7, 2026 (daily granularity). Backfill entries marked with `_backfill: true` and filtered from 6h/24h chart views to avoid false spikes.

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Dashboard HTML |
| `/api/data?hours=N` | GET | Snapshots for last N hours (max 8760). Downsampled to ~hourly beyond 7 days. |
| `/api/current` | GET | Latest snapshot only |

---

## Changelog

### v3 (2026-03-08)
- **BB message counts** — snapshot queries BlueBubbles API for sent/received iMessage counts
- **Delivered column** — cron table shows ✓/✗ for delivery status
- **Activity bar chart** — replaced sparse line chart with hourly-bucketed stacked bars
- **Adaptive buckets** — Activity and Token Usage charts scale bucket size by time range
- **Backfill filtering** — `_backfill` entries excluded from 6h/24h views
- **Historical backfill** — BB message history loaded for Feb 1 – Mar 7, 2026

### v2 (2026-03-07)
- **Full rebuild** — SVG utilization gauges, resolved hex colors, cron table
- **Fixed snapshot parser** — reads `usage.input_tokens` from nested cron JSONL
- **Fixed runtime log parser** — matches tslog format (`_meta.logLevelName`, positional keys)
- **Staleness detection** — warns when data > 30 min old
- **Error banner** — shows on fetch failure

### v1 (pre-2026-03-07)
- Original implementation. Only utilization chart barely visible, all other charts dead.

---

## Known Issues / Future Work

- **Midnight log gap** — Log entries between last snapshot of day and midnight are lost
- **OAuth token staleness** — No fallback when token expires, utilization goes null
- **Cron dedup** — If state file is reset, historical records are re-counted
- **Per-model token attribution** — Opus vs Sonnet token split (data available in cron JSONL `model` field)
- **Agent run counting** — Gateway doesn't log agent session start/stop events; count stays 0
- **Response latencies** — No structured timing data available from gateway
