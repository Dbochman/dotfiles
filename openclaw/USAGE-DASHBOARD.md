# OpenClaw Usage Dashboard

## Status: v5 (2026-03-08)

Single-file Python HTTP server + embedded Chart.js UI. Serves at port 8551 on Mac Mini, Tailscale-only access.

### Architecture

| Component | Path | Purpose |
|-----------|------|---------|
| `usage-dashboard.py` | Mini: `~/.openclaw/bin/` | HTTP server + embedded HTML/JS dashboard |
| `usage-snapshot.sh` | Mini: `~/.openclaw/bin/` | OpenClaw data collector, runs every 15 min via LaunchAgent |
| `ccusage-push.sh` | Any machine: `dotfiles/openclaw/bin/` | Claude Code usage collector, pushes to Mini every 30 min |
| `ccusage-setup.sh` | Any machine: `dotfiles/openclaw/bin/` | Installs ccusage-push LaunchAgent with correct paths |
| History JSONL | Mini: `~/.openclaw/usage-history/YYYY-MM-DD.jsonl` | One file per day, append-only OpenClaw snapshots |
| ccusage JSON | Mini: `~/.openclaw/usage-history/ccusage-{hostname}.json` | Per-machine Claude Code daily token usage, merged by dashboard |
| State file | Mini: `~/.openclaw/usage-history/.snapshot-state` | Tracks log/cron offsets between snapshots |
| OAuth cache | Mini: `~/.openclaw/.anthropic-oauth-cache` | Pushed from MacBook every 30min |

### Data Sources

| Source | What It Provides | Collected By |
|--------|-----------------|--------------|
| Anthropic Usage API | 5h/7d utilization %, per-model %, reset times | `fetch_utilization()` via OAuth token |
| Cron run JSONL | Job ID, status, duration, model, token usage, delivered flag | `parse_cron_runs()` via offset tracking |
| Runtime log | Gateway restarts, errors (tslog format) | `parse_runtime_log()` via offset tracking |
| BlueBubbles API | Messages sent/received counts per interval | `fetch_bb_messages()` via `message/query` endpoint |
| ccusage (Claude Code) | Daily token totals, per-model breakdown, cache stats | `ccusage-push.sh` via `npx ccusage daily --json` on MacBook |

### Dashboard Features

**Utilization gauges** — SVG ring gauges for 5-Hour, 7-Day. Sonnet 7d gauge only appears when there's active Sonnet usage. Color-coded green/amber/red with pacing labels (chill/on-track/hot) and reset countdowns. Two additional gauges show OpenClaw (orange) and Claude Code (blue) token share of combined usage.

**Stat cards** — Total tokens (in/out), cron runs (with failure count), messages (sent/recv), errors, gateway restarts.

**Charts:**
- Utilization Over Time — line chart with 100% threshold line
- Tokens by Job — doughnut chart
- Token Usage Over Time — stacked bar chart (OpenClaw orange / Claude Code blue), daily buckets. OpenClaw tokens from cron snapshot data, Claude Code tokens from ccusage daily JSON.
- Cron Duration Trends — per-job line chart, deduped to last run per day, filtered by time range, sorted chronologically
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

### Claude Code Usage Integration

Added in v5. The `ccusage-push.sh` script runs on the MacBook (where Claude Code session logs live at `~/.claude/projects/`) every 30 minutes via `ai.openclaw.ccusage-push` LaunchAgent. It runs `npx ccusage daily --json --breakdown --offline --since <90 days ago>`, producing a JSON file with per-day per-model token breakdowns (input, output, cache creation, cache read, total, cost). The file is pushed to Mini via scp at `~/.openclaw/usage-history/ccusage-daily.json`.

The dashboard `/api/data` endpoint includes the ccusage data in its response. The Token Usage Over Time chart shows OpenClaw (orange) and Claude Code (blue) as stacked bars with daily granularity. Two gauges show each source's percentage of combined token usage.

Since ccusage data is daily granularity and the push runs on a laptop (not always on), gaps during sleep don't cause data loss — the next push captures complete daily totals.

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Dashboard HTML |
| `/api/data?hours=N` | GET | Snapshots + ccusage for last N hours (max 8760). Downsampled to ~hourly beyond 7 days. |
| `/api/current` | GET | Latest snapshot only |

---

## Changelog

### v5 (2026-03-08)
- **Claude Code usage integration** — `ccusage-push.sh` runs on MacBook every 30 min, pushes daily token data to Mini via scp. Dashboard reads `ccusage-daily.json` alongside OpenClaw snapshot data.
- **OpenClaw + Claude Code gauges** — replaced Extra Credits gauge with two separate gauges showing each source's share of combined token usage (OpenClaw orange, Claude Code blue)
- **Token Usage Over Time rework** — chart now shows OpenClaw vs Claude Code stacked bars (daily buckets) instead of input/output split
- **LaunchAgent** — `ai.openclaw.ccusage-push` on MacBook (30-min interval), uses `npx ccusage daily --json --breakdown --offline`

### v4 (2026-03-08)
- **Input token derivation** — `input_tokens` from OpenClaw is misleadingly small (counts turns); now derived as `total - output` in both snapshot script and dashboard render
- **Gauge cleanup** — 7-Day gauge uses `seven_day_opus` when available, falls back to `seven_day`; Sonnet gauge hidden unless it has active usage
- **Duration chart fixes** — timestamps converted from epoch ms to ISO for Chart.js; deduped to last run per job per day; filtered by selected time range; series sorted chronologically to prevent line doubling
- **Human-readable cron IDs** — all UUID-based job IDs renamed (e.g., `743db947...` → `datenight-apr-italian`); run state files and history JSONL updated to match

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
- **Per-model token attribution** — Anthropic Usage API doesn't always return `seven_day_opus` separately
- **Agent run counting** — Gateway doesn't log agent session start/stop events; count stays 0
- **Response latencies** — No structured timing data available from gateway
