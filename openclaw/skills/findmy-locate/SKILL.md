---
name: findmy-locate
description: Locate Dylan, Julia, or both using Apple FindMy. Returns a screenshot of their map location. Use when asked "where is Dylan/Julia", "find Dylan/Julia", "locate someone", or anything about someone's physical location.
allowed-tools: Bash(findmy-locate:*)
metadata: {"openclaw":{"emoji":"P","requires":{"bins":["peekaboo"]}}}
---

# FindMy Locate

Locate people using Apple **Find My** app via Peekaboo screen automation. Returns a screenshot of the zoomed map view showing the person's location pin.

## Commands

### Locate a person
```bash
findmy-locate dylan     # Dylan Bochman
findmy-locate julia     # Julia Jennings
findmy-locate me        # clawdbotbochman (Mac Mini)
findmy-locate both      # Dylan + Julia (two screenshots)
```

Returns JSON with the screenshot path:
```json
{"success": true, "person": "Dylan Bochman", "capture": "/path/to/screenshot.png", "size": 285432}
```

For `both`, returns an array:
```json
{"results": [{"success": true, "person": "Dylan Bochman", ...}, {"success": true, "person": "Julia Jennings", ...}]}
```

### Interpreting results

The screenshot shows FindMy's zoomed map view centered on the person's location pin. **Read the screenshot image** to determine:
- Street address or neighborhood
- Proximity to known locations (Crosstown at 19 Crosstown Ave, West Roxbury; Cabin at 95 School House Rd, Phillipston)
- Whether the person is moving or stationary (FindMy shows a directional arrow when moving)
- Nearby landmarks visible on the map

## People in FindMy

| Position | Name | Notes |
|----------|------|-------|
| 0 | Me (clawdbotbochman) | The Mac Mini itself — always at home |
| 1 | Dylan Bochman | |
| 2 | Julia Jennings | |

The sidebar order matters — the script navigates via keyboard arrow keys.

## How It Works

1. Opens Find My app and brings it to the front
2. Resets the cursor to position 0 (Me) by pressing Up x3
3. Presses Down N times to reach the target person
4. Waits 3 seconds for the map to animate and zoom to the pin
5. Captures the frontmost window via `peekaboo image --mode frontmost`

**Navigation is relative.** After reset, the cursor starts at position 0. Each `_navigate_and_capture` call moves Down by a step count relative to the *current* cursor position — it does NOT reset between captures. This is why `both` works in a single pass: reset → Down 1 (Dylan, capture) → Down 1 more (Julia, capture).

**FindMy blocks mouse clicks** — accessibility API clicks are silently ignored. Keyboard arrow keys via Peekaboo are the only reliable way to navigate the sidebar.

**If the People tab isn't showing** (Items or Devices tab is active), keyboard navigation won't reach the people list. The script assumes People is the active tab. If captures consistently fail, manually click the People tab once on the Mini.

## Requirements

- **Peekaboo** (`/opt/homebrew/bin/peekaboo`) with TCC grants:
  - Screen Recording (for screenshots)
  - Accessibility (for keyboard input)
- **`~/Applications/Peekaboo.app`** — TCC wrapper that holds the grants
- Must run from **LaunchAgent context or local terminal** — TCC blocks headless SSH sessions
- Find My app must be signed into the shared Apple ID

## Screenshots

Captures are saved to `~/.openclaw/findmy-locate/` with the pattern:
```
findmy-<name>-<unix_timestamp>.png
```

Old captures are not automatically cleaned. Periodically prune:
```bash
find ~/.openclaw/findmy-locate/ -name "*.png" -mtime +7 -delete
```

## Troubleshooting

### "Failed to capture FindMy — check Peekaboo TCC permissions"
Screen Recording or Accessibility not granted to Peekaboo.app. Open System Settings > Privacy & Security on the Mini and grant both.

### Screenshot is too small (< 50KB)
Peekaboo captured an empty or partial window. FindMy may not be fully loaded. Try again — the script has fallbacks for Desktop-saved screenshots.

### Wrong person selected
The sidebar order may have changed (e.g., new person added to FindMy). Update the position constants in the script.

### Keyboard navigation not working
FindMy must be the frontmost app and the People tab must be active. If Items or Devices tab is showing, manually switch to People first.

## Next Steps After Locating

After capturing someone's location, consider whether the **places** skill (`goplaces` CLI) would be useful. Common follow-ups:

- **"What's near them?"** — `goplaces search "restaurants" --lat=<lat> --lng=<lng>` to find nearby places
- **"How far are they from home?"** — `goplaces directions <their location> "19 Crosstown Ave, West Roxbury MA"` for travel time
- **"Find them a coffee shop"** — `goplaces search "coffee" --lat=<lat> --lng=<lng> --open-now`
- **"What neighborhood is that?"** — `goplaces details <place_id>` for area context

Read the FindMy screenshot to estimate coordinates or identify the neighborhood, then pass that to `goplaces` for structured place data, directions, or recommendations.

## Skill Boundaries

This skill locates people via FindMy screenshots. It does NOT:
- Track location continuously (use `fi-collar` for Potato's GPS)
- Determine who is home (use `presence` skill for WiFi-based detection)
- Trigger any automated actions

For related tasks:
- **places**: Search nearby businesses, get directions, find restaurants/shops near someone's location
- **presence**: WiFi-based home/away detection (faster, no screenshot needed)
- **fi-collar**: Potato's GPS location via Fi collar API (structured data, not screenshots)
- **dog-walk**: Automated walk detection + Roomba control
