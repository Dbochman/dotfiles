# Dashboard Home State API — Implementation Plan

## Status: PLANNED (not yet implemented)

## Overview

Add home state data endpoints to the existing nest-dashboard (port 8550) to serve cat weights, sleep data, doorbell battery, and dog walk events alongside climate and presence data.

## Current Architecture

The nest-dashboard is a single-file Python HTTP server (`nest-dashboard.py`) serving on port 8550:
- `/` — embedded HTML SPA with Chart.js
- `/api/data?hours=N` — climate snapshots + presence history (JSONL)
- `/api/current` — latest climate snapshot
- `/api/presence` — current presence state.json

## New Endpoints

### `/api/home-state` — Current home state
- Reads `~/.openclaw/home-state/current.json`
- Returns cat weights, sleep scores, doorbell battery
- Same pattern as `/api/presence`

### `/api/home-state/history?days=N` — Historical daily snapshots
- Reads `~/.openclaw/home-state/YYYY-MM-DD.jsonl` for last N days (default 30)
- Returns `{"meta": {"days": N, "count": X}, "snapshots": [...]}`

### `/api/dog-walks?days=N` — Dog walk event history
- Reads `~/.openclaw/ring-listener/history/YYYY-MM-DD.jsonl` for last N days (default 30)
- Returns `{"meta": {"days": N, "count": X}, "events": [...]}`

### `/api/dog-walks/current` — Current dog walk state
- Reads `~/.openclaw/ring-listener/state.json`
- Returns current dog_walk, roombas, findmy_polling, last_vision state

## Implementation Steps

### Step 1: Backend — Loader Functions (~30 lines)

Add to `nest-dashboard.py`:

```python
HOME_STATE_DIR = Path.home() / ".openclaw/home-state"
RING_STATE_DIR = Path.home() / ".openclaw/ring-listener"

def load_home_state_current():
    path = HOME_STATE_DIR / "current.json"
    if path.exists():
        return json.loads(path.read_text())
    return None

def load_home_state_history(days=30):
    records = []
    for i in range(days):
        date = (datetime.utcnow() - timedelta(days=i)).strftime("%Y-%m-%d")
        path = HOME_STATE_DIR / f"{date}.jsonl"
        if path.exists():
            for line in path.read_text().splitlines():
                if line.strip():
                    records.append(json.loads(line))
    return sorted(records, key=lambda r: r.get("timestamp", ""))

def load_dog_walk_history(days=30):
    records = []
    for i in range(days):
        date = (datetime.utcnow() - timedelta(days=i)).strftime("%Y-%m-%d")
        path = RING_STATE_DIR / "history" / f"{date}.jsonl"
        if path.exists():
            for line in path.read_text().splitlines():
                if line.strip():
                    records.append(json.loads(line))
    return sorted(records, key=lambda r: r.get("timestamp", ""))

def load_dog_walk_current():
    path = RING_STATE_DIR / "state.json"
    if path.exists():
        return json.loads(path.read_text())
    return None
```

### Step 2: Backend — Route Handlers (~30 lines)

Add to `do_GET` in `DashboardHandler`:

```python
elif path == "/api/home-state":
    self._serve_home_state()
elif path == "/api/home-state/history":
    days = int(qs.get("days", ["30"])[0])
    self._serve_home_state_history(days)
elif path == "/api/dog-walks":
    days = int(qs.get("days", ["30"])[0])
    self._serve_dog_walks(days)
elif path == "/api/dog-walks/current":
    self._serve_dog_walks_current()
```

Handler methods:
```python
def _serve_home_state(self):
    state = load_home_state_current()
    self._respond(200, state or {"error": "no home state data"})

def _serve_home_state_history(self, days):
    records = load_home_state_history(min(days, 365))
    self._respond(200, {"meta": {"days": days, "count": len(records)}, "snapshots": records})

def _serve_dog_walks(self, days):
    events = load_dog_walk_history(min(days, 365))
    self._respond(200, {"meta": {"days": days, "count": len(events)}, "events": events})

def _serve_dog_walks_current(self):
    state = load_dog_walk_current()
    self._respond(200, state or {"error": "no dog walk data"})
```

### Step 3: Frontend — Dashboard Panels (~200-300 lines JS/HTML)

Add to the embedded HTML SPA (uses Chart.js already loaded):

#### Cat Weights Panel
- **Chart type**: Line chart
- **X axis**: Date
- **Y axis**: Weight (lbs)
- **Series**: One per cat (Sopaipilla, Burrito)
- **Reference bands**: Healthy range overlay (optional)

#### Sleep Scores Panel
- **Chart type**: Dual line chart
- **X axis**: Date
- **Y axis**: Score (0-100)
- **Series**: Dylan (blue), Julia (pink)
- **Tooltip**: Duration, REM%, deep%, snoring

#### Sleep Duration Panel
- **Chart type**: Grouped bar chart
- **X axis**: Date
- **Y axis**: Hours
- **Series**: Dylan, Julia

#### Doorbell Battery Panel
- **Chart type**: Line chart
- **X axis**: Date
- **Y axis**: Battery %
- **Series**: Crosstown, Cabin
- **Threshold line**: 20% warning (red dashed)

#### Dog Walk Log Panel
- **Chart type**: Timeline/table
- **Columns**: Date, time, duration (departed→returned), people, dogs, trigger, FindMy polls
- **Derived**: Walk duration from departed_at/returned_at

### Step 4: Integration

- Add home-state fetch to the existing 5-min auto-refresh cycle
- Place new panels below the existing climate dashboard
- Section header: "Home State" with sub-sections for cats, sleep, battery, walks

## Data Flow

```
Daily snapshot (9 AM)                Ring listener (event-driven)
        |                                      |
        v                                      v
~/.openclaw/home-state/             ~/.openclaw/ring-listener/
    current.json                        state.json
    YYYY-MM-DD.jsonl                    history/YYYY-MM-DD.jsonl
        |                                      |
        v                                      v
nest-dashboard.py (port 8550)
    /api/home-state          /api/dog-walks
    /api/home-state/history  /api/dog-walks/current
        |
        v
    Embedded SPA (Chart.js)
```

## Downsampling

For large date ranges (>90 days), downsample to latest snapshot per day. Home-state already runs once/day so this is mostly a no-op. Dog walk events could accumulate — cap at latest state per walk (departure + return pair).

## Estimated Effort

| Component | Lines | Notes |
|-----------|-------|-------|
| Backend loaders | ~30 | 4 functions, follows existing pattern |
| Backend routes | ~30 | 4 endpoints + handlers |
| Frontend panels | ~200-300 | 5 chart panels using Chart.js |
| Testing | — | Manual: curl endpoints + visual check |

## Dependencies

- No new deps — reuses Chart.js (already in SPA) and stdlib Python
- Requires home-state snapshot LaunchAgent running (deployed 2026-03-24)
- Requires Ring listener with state tracking (deployed 2026-03-24)
