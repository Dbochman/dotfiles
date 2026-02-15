---
name: places
description: Search for places, get directions, find nearby businesses, and look up place details using Google Places API. Use when asked about restaurants, stores, businesses, directions, travel time, or finding places.
allowed-tools: Bash(places:*)
metadata: {"openclaw":{"emoji":"P","requires":{"bins":["goplaces"]}}}
---

# Places

Search and explore places via the `goplaces` CLI (v0.3.0), powered by the Google Places API (New) and Routes API.

## Search Places

```bash
goplaces search "Italian restaurants in Newton MA"
goplaces search "coffee shops near Brookline" --open-now
goplaces search "pharmacies" --lat=42.33 --lng=-71.21 --radius-m=2000
goplaces search "date night restaurants" --min-rating=4.0 --price-level=2,3
goplaces search "pet stores" --type=pet_store --limit=5
goplaces search "brunch spots Newton" --json
```

| Flag | Description |
|------|-------------|
| `--limit=N` | Max results 1-20 (default 10) |
| `--open-now` | Only currently open places |
| `--min-rating=N` | Minimum rating 0-5 |
| `--price-level=N,...` | Price levels 0-4 (repeatable) |
| `--type=TYPE` | Place type filter (repeatable) |
| `--lat`, `--lng`, `--radius-m` | Location bias |
| `--keyword=STRING` | Keyword to append to query |

## Nearby Search

Find places around a specific location (requires coordinates):

```bash
goplaces nearby --lat=42.33 --lng=-71.21 --radius-m=1000 --type=restaurant
goplaces nearby --lat=42.33 --lng=-71.21 --radius-m=500 --type=grocery_store --limit=5
```

## Directions

```bash
goplaces directions --from="Newton MA" --to="Boston MA" --mode=drive
goplaces directions --from="123 Main St, Newton" --to="Logan Airport" --mode=drive --units=imperial
goplaces directions --from="Newton MA" --to="Cambridge MA" --mode=transit --steps
goplaces directions --from="Newton MA" --to="Brookline MA" --mode=drive --compare=transit
```

| Flag | Description |
|------|-------------|
| `--from`, `--to` | Origin and destination (address or place name) |
| `--mode` | `walk`, `drive`, `bicycle`, `transit` (default: walk) |
| `--compare=MODE` | Compare with another travel mode |
| `--steps` | Include turn-by-turn instructions |
| `--units` | `metric` or `imperial` |

Can also use place IDs or coordinates:
```bash
goplaces directions --from-place-id=ChIJs4leeXiC44kRlTBRn5-ln2o --to="Boston MA"
goplaces directions --from-lat=42.33 --from-lng=-71.21 --to="Boston MA"
```

## Place Details

```bash
goplaces details ChIJs4leeXiC44kRlTBRn5-ln2o
goplaces details ChIJs4leeXiC44kRlTBRn5-ln2o --reviews
goplaces details ChIJs4leeXiC44kRlTBRn5-ln2o --reviews --photos --json
```

Get a place ID from search results, then fetch full details including hours, phone, website, reviews.

## Route Search

Find places along a driving route:

```bash
goplaces route "gas stations" --from="Newton MA" --to="Cape Cod MA"
goplaces route "rest stops" --from="Newton MA" --to="New York NY" --radius-m=2000
goplaces route "coffee" --from="Newton MA" --to="Providence RI" --mode=DRIVE --limit=3
```

## Resolve Location

Convert a free-form address to place candidates:

```bash
goplaces resolve "Farmstead Table Newton"
goplaces resolve "123 Main St Newton MA" --json
```

## Autocomplete

Get place suggestions from partial input:

```bash
goplaces autocomplete "farm" --lat=42.33 --lng=-71.21 --radius-m=5000
goplaces autocomplete "MIDA new" --limit=3
```

## Global Flags

| Flag | Description |
|------|-------------|
| `--json` | Output as JSON (best for scripting) |
| `--language=CODE` | BCP-47 language code (e.g. `en`) |
| `--region=CODE` | CLDR region code (e.g. `US`) |
| `--verbose` | Verbose logging |

## Notes

- Home location: Newton, MA (42.33, -71.21) — use for location bias when no location specified
- Place IDs from search results can be used with `details` and `directions`
- The `--json` flag is useful when you need to extract specific fields
- Price levels: 0=Free, 1=Inexpensive, 2=Moderate, 3=Expensive, 4=Very Expensive
- `search` uses text-based search (best for natural language queries); `nearby` requires coordinates and types
- API key is set via `GOOGLE_PLACES_API_KEY` env var (managed by gateway wrapper)
