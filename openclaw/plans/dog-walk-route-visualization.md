# Dog Walk Route Visualization — Plan

## Goal

Add a simple route view to the dog-walk dashboard that shows:

- per-walk distance
- approximate route overlays
- per-house heatmaps
- a house toggle with `Cabin`, `Crosstown`, and `Both`

This should stay consistent with OpenClaw's current Fi-first dog-walk model and avoid introducing a heavy mapping stack or a fragile live-tracking dependency.

## Non-Goals

- turn-by-turn accurate walk replay
- a single combined map spanning both houses
- a Google Maps-specific implementation
- any dependency on phone-side foreground tracking

Fi collar GPS is good enough for approximate routes and heatmaps, but not for guaranteed high-fidelity street-level traces.

## Product Shape

### Filters

- existing date-range filter
- existing location filter: `Cabin`, `Crosstown`, `Both`
- new layer toggle: `Routes` or `Heatmap`

### Map behavior

- `Cabin`: one full-width map for cabin-origin walks
- `Crosstown`: one full-width map for crosstown-origin walks
- `Both`: two smaller maps side by side, stacked on mobile

Each walk must have an immutable `origin_location` captured at departure. A walk always stays attached to the house it started from, even if Fi later reports a different nearest location.

## Data Model

Persist route geometry separately from JSONL event history:

`~/.openclaw/dog-walk/routes/<origin_location>/<YYYY-MM-DD>/<walk_id>.json`

Suggested shape:

```json
{
  "walk_id": "2026-04-04T12:34:56Z-cabin",
  "origin_location": "cabin",
  "started_at": "2026-04-04T12:34:56Z",
  "ended_at": null,
  "return_signal": null,
  "distance_m": 0,
  "point_count": 0,
  "points": []
}
```

Each point should store only what the dashboard needs:

```json
{
  "ts": "2026-04-04T12:40:00Z",
  "lat": 42.6021,
  "lon": -72.1512
}
```

Keep this intentionally small. Do not persist derived heat values, map bounds, or duplicate home metadata into every file.

## Simplified Capture Strategy

Do not add a new always-on polling loop.

Instead:

1. Create a `walk_id` on confirmed departure.
2. Start a route file immediately.
3. During the existing return-monitor loop, append deduped Fi points when available.
4. On dock or timeout, finalize the file with `ended_at`, `return_signal`, `distance_m`, and `point_count`.

Distance priority:

1. Fi-reported walk distance when present
2. Haversine sum across persisted points

This keeps all route capture inside the lifecycle the listener already owns.

## Dashboard/API Changes

Add to `openclaw/dog-walk-dashboard.py`:

- `/api/homes`
- `/api/routes?days=30&location=cabin|crosstown|all`
- `/api/route?id=<walk_id>`
- `/api/heatmap?days=30&location=cabin|crosstown`

Recommended UI stack:

- Leaflet for maps
- `Leaflet.heat` for heatmaps

Do not use Google Maps heatmap APIs as the feature foundation.

## Rollout

### Phase 1

- `walk_id` and `origin_location`
- route-file persistence
- distance summary in route files and API

### Phase 2

- single-house route map
- `Both` split view
- clickable walk selection from the recent-walk list

### Phase 3

- per-house heatmap mode
- aggregate distance cards

## Open Questions

- Whether Fi's `positions` payload is already enough for most active walks, or if explicit point appends from the listener are still needed every poll
- Whether old walks should be shown without maps, or hidden from route-specific views entirely
