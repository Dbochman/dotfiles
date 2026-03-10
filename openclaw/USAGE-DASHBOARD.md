# OpenClaw Usage Dashboard

## Status: v7.1 (2026-03-10)

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
| **Gateway sessions.usage RPC** | **Per-session tokens, costs, cache, latency, tool usage, model split, daily aggregates** | **`openclaw gateway call sessions.usage --json` via WebSocket** |
| Cron run JSONL | Job ID, status, duration, model, token usage, delivered flag | `parse_cron_runs()` via offset tracking |
| Runtime log | Gateway restarts, errors (tslog format) | `parse_runtime_log()` via offset tracking |
| BlueBubbles API | Messages sent/received counts per interval | `fetch_bb_messages()` via `message/query` endpoint |
| ccusage (Claude Code) | Daily token totals, per-model breakdown, cache stats | `ccusage-push.sh` via `npx ccusage daily --json` on MacBook |

### Dashboard Features

**Utilization gauges** — SVG ring gauges for 5-Hour, 7-Day. Sonnet 7d gauge only appears when there's active Sonnet usage. Color-coded green/amber/red with pacing labels (chill/on-track/hot) and reset countdowns. Two additional gauges show OpenClaw (orange) and Claude Code (blue) token share of combined usage. Entire section auto-hides when utilization data is unavailable (e.g., stale OAuth token).

**Stat cards** — All cards auto-hide when their value is zero for the selected time range. Available: Total Cost ($), Total Tokens (in/out with cache), Cron Runs (with failure count), Messages (sent/recv), Sessions (with tool calls), Errors, Gateway Restarts. Cost/sessions require gateway RPC data; falls back to "No Activity" when nothing to show.

**Charts** — all charts auto-hide when they have no data to display:
- Utilization Over Time — line chart with 100% threshold line
- Tokens by Job — doughnut chart; when hidden, Token Usage expands to full width
- Token Usage Over Time — stacked bar chart with adaptive buckets. At 6h/24h shows OpenClaw only (hourly); at 7d/30d adds Claude Code (daily ccusage data doesn't have sub-day granularity)
- Activity — stacked bar chart (sent/received/cron), adaptive bucket sizes
- Cost Over Time — stacked bar chart (cache write/read/output/input cost per day) from gateway RPC
- Model Split — doughnut chart (per-model token share: Opus/Sonnet/etc.) from gateway RPC
- Tool Usage — horizontal bar chart (call counts per tool: exec, message, read, etc.) from gateway RPC

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
| `/api/services` | GET | LaunchAgent service status |
| `/api/cron` | GET | Upcoming cron job schedule |
| `/api/gateway-usage` | GET | Gateway sessions.usage RPC (5-min cached). Returns totals, sessions, daily, aggregates. |

---

## Changelog

### v7.1 (2026-03-10)
- **Downsampling data loss fix** — `_downsample_hourly` (used for 30d view) was dropping activity deltas and cron job entries from non-kept snapshots, causing 30d to show fewer cron runs/messages than 7d. Now merges activity counts and `cron_jobs` from dropped snapshots into the kept one per hourly bucket.
- **Utilization gauges auto-hide** — replaced "No utilization data" banner with silent auto-hide when utilization is null (e.g., expired OAuth token), consistent with all other dashboard sections.

### v7 (2026-03-09)
- **Gateway sessions.usage RPC integration** — new `/api/gateway-usage` endpoint calls `openclaw gateway call sessions.usage --json` with 5-minute cache. Provides per-session tokens, costs, tool usage, and model split.
- **New charts** — Cost Over Time (stacked bar by cost type), Model Split (doughnut: Opus/Sonnet/etc.), Tool Usage (horizontal bar by tool name). All powered by gateway RPC, all filtered by selected time range, all auto-hide when no data.
- **Time-range filtering for gateway data** — gateway RPC returns all-time data; charts and stat cards now filter client-side by `currentHours` cutoff. Daily arrays filtered by date, sessions filtered by `activityDates`, model/tool usage re-aggregated from matching sessions.
- **Stat cards auto-hide** — each card only appears when its value is non-zero for the selected time range. Shows "No Activity" placeholder when all values are zero.
- **Enhanced stat cards** — Total Cost ($), Sessions count (with tool calls). Gateway data preferred; falls back to snapshot-based stats when unavailable.
- **Parallel data fetch** — `refresh()` now fetches snapshot data and gateway RPC in parallel via `Promise.all` for faster load.
- **Control UI pairing** — added `gateway.controlUi.allowedOrigins` for Tailscale URL access to OpenClaw Control UI dashboard.

### v6 (2026-03-09)
- **Empty chart auto-hide** — all charts (Utilization, Tokens by Job, Token Usage, Activity) hide entirely when they have no data, instead of rendering empty/placeholder content
- **Token Usage adaptive bucketing** — now uses hourly/12h/daily buckets matching Activity chart. Claude Code data only shown at 7d/30d (daily ccusage lacks sub-day granularity)
- **Token Usage full-width** — expands to span both grid columns when Tokens by Job is hidden
- **Removed Cron Duration Trends chart** — replaced by inline trend column in cron table
- **Removed stale root copy** — `openclaw/usage-dashboard.py` deleted; canonical source is `openclaw/bin/usage-dashboard.py`

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

## Gateway sessions.usage RPC (v7 plan)

Discovered 2026-03-09. The gateway exposes a WebSocket RPC method `sessions.usage` that returns rich per-session usage data — far more detailed than the cron JSONL + runtime log parsing we currently rely on.

### How to query

```bash
openclaw gateway call sessions.usage --json --timeout 30000
```

Returns all sessions (currently ~50) with no date filtering (filter client-side). Also available: `usage.cost` for daily totals only.

### Response structure

```
{
  totals:     { input, output, cacheRead, cacheWrite, totalTokens, totalCost, ...perCostType }
  daily:      [{ date, input, output, cacheRead, cacheWrite, totalTokens, totalCost }]
  sessions:   [{ sessionId, agentId, channel, chatType, origin, model, usage: {
                   durationMs, firstActivity, lastActivity, activityDates,
                   dailyBreakdown, dailyMessageCounts, dailyLatency, dailyModelUsage,
                   messageCounts: { total, user, assistant, toolCalls, errors },
                   toolUsage: { totalCalls, uniqueTools, tools: [{ name, count }] },
                   modelUsage: [{ provider, model, count, totals }],
                   latency: { count, avgMs, p95Ms, minMs, maxMs },
                   input, output, cacheRead, cacheWrite, totalTokens, totalCost
                 }}]
  aggregates: {
    messages:      { total, user, assistant, toolCalls, errors }
    tools:         { totalCalls, uniqueTools, tools: [{ name, count }] }
    byModel:       [{ provider, model, count, totals }]
    byProvider:    [{ provider, count, totals }]
    byAgent:       [{ agentId, totals }]
    byChannel:     [{ ... }]
    latency:       { count, avgMs, p95Ms, minMs, maxMs }
    dailyLatency:  [{ date, count, avgMs, p95Ms, minMs, maxMs }]
    modelDaily:    [{ date, provider, model, tokens, cost, count }]
    daily:         [{ date, input, output, cacheRead, cacheWrite, totalTokens, totalCost }]
  }
}
```

### What this unlocks for the dashboard

**Existing charts — improved data quality:**

| Chart | Current Source | Gateway RPC Improvement |
|-------|---------------|------------------------|
| Token Usage Over Time | Cron JSONL (cron jobs only) | `aggregates.daily` — captures ALL sessions (cron + ad-hoc + DM conversations), includes cache tokens |
| Tokens by Job | Cron JSONL `total_tokens` | `sessions[].usage.totalTokens` — per-session totals with cache breakdown, not just cron |
| Stat cards (Total Tokens) | Cron JSONL sums | `totals` — accurate input/output/cacheRead/cacheWrite with cost |
| Stat cards (Errors) | Runtime log grep | `aggregates.messages.errors` — structured error count |

**New charts (implemented in v7):**

| Chart | Data Source | Status |
|-------|------------|--------|
| **Cost Over Time** | `aggregates.daily[]` filtered by cutoff | DONE — stacked bar (cache write/read/output/input cost) |
| **Model Split** | `sessions[].usage.modelUsage` re-aggregated from filtered sessions | DONE — doughnut chart |
| **Tool Usage** | `sessions[].usage.toolUsage` re-aggregated from filtered sessions | DONE — horizontal bar chart |
| **Cache Efficiency** | `totals.cacheRead` vs `totals.cacheWrite` vs `totals.input` | DEFERRED — stretch goal |
| **Session Activity** | `sessions[].usage.durationMs`, `firstActivity`, `lastActivity` | DEFERRED — stretch goal |

**Stat cards — new or improved:**

| Card | Source | Status |
|------|--------|--------|
| Total Cost | `aggregates.daily` summed by cutoff | DONE — auto-hides when $0 |
| Sessions | Filtered `sessions.length` | DONE — shows tool call count in sub |
| Total Tokens | `aggregates.daily` summed by cutoff | DONE — includes cache tokens |
| Errors / Gateway Restarts / Cron / Messages | Snapshot data | DONE — all auto-hide when zero |

### Implementation status

1. **`/api/gateway-usage` endpoint** — DONE. Shells out to `openclaw gateway call sessions.usage --json`, caches result for 5 minutes via `_gw_usage_cache` with threading lock. Loads secrets from `~/.openclaw/.secrets-cache` for gateway auth.
2. **Frontend parallel fetch** — DONE. `refresh()` calls `fetchData()` and `fetchGatewayUsage()` in parallel via `Promise.all`.
3. **Stat cards upgraded** — DONE. All cards auto-hide when zero. Gateway-powered: Total Cost, Sessions (with tool calls). Snapshot-powered: Cron Runs, Messages, Errors, Gateway Restarts. Falls back to "No Activity" placeholder when all are zero.
4. **New charts** — DONE. Cost Over Time (stacked bar: cache write/read/output/input cost), Model Split (doughnut: per-model tokens), Tool Usage (horizontal bar: call counts per tool). All filtered by time range, all auto-hide when no data.
5. **Time-range filtering** — DONE. Gateway RPC returns all-time data. Client filters: daily arrays by date, sessions by `activityDates`, then re-aggregates model/tool usage from matching sessions.
6. **Stretch goals** — DEFERRED. Cache Efficiency gauge and Session Activity timeline/heatmap.

### Known issues this resolves

- ~~**Agent run counting** — Gateway doesn't log agent session start/stop events; count stays 0~~ → `sessions.length` from RPC
- ~~**Response latencies** — No structured timing data available from gateway~~ → `aggregates.latency` and `aggregates.dailyLatency`
- ~~**Per-model token attribution** — Anthropic Usage API doesn't always return `seven_day_opus` separately~~ → `aggregates.byModel` gives exact per-model tokens

---

## Known Issues / Future Work

- **Midnight log gap** — Log entries between last snapshot of day and midnight are lost
- **OAuth token staleness** — No fallback when token expires; gauges auto-hide (no longer shows error banner). Token refreshes when MacBook pushes via `usage-token-push` LaunchAgent
- **Cron dedup** — If state file is reset, historical records are re-counted
