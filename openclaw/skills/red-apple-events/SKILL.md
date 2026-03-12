---
name: red-apple-events
description: Fetch upcoming events from Red Apple Farm and Brew Barn (near the cabin in Phillipston, MA), then sync them to the clawdbotbochman calendar. Use when asked about farm events, Brew Barn events, or cabin-area happenings.
allowed-tools: Bash(calendar:*), Bash(fetch:*), Browser
metadata: {"openclaw":{"emoji":"🍎","requires":{"bins":["gws"]}}}
---

# Red Apple Farm & Brew Barn Events

Fetch upcoming events from two sources near the cabin in Phillipston, MA, and sync them to the `clawdbotbochman@gmail.com` Google Calendar.

## Event Sources

| Source | URL | Type |
|--------|-----|------|
| Red Apple Farm | https://www.redapplefarm.com/events | Static HTML (Squarespace) |
| Brew Barn | https://www.brewbarnma.com/events | Wix (requires JS rendering) |

## Step 1: Fetch Red Apple Farm Events

Red Apple Farm's events page is static HTML. Use `curl` or the `web_search` tool to read it:

```bash
curl -s https://www.redapplefarm.com/events
```

Parse the event listing. Events are in a `.user-items-list-item-container` structure with:
- `.list-item-content__title` — event name
- `.list-item-content__description` — date and details

Annual events typically include:
- Northfolk Night Market (Feb)
- Enchanted Orchard Renaissance Faire (May)
- Summer Strings Music Festival (Jun)
- Blueberry Jamboree (Jul)
- Sunflower Festival (Aug)
- Appleseed Country Fair (Labor Day)
- Applefest (Oct, at Wachusett Mountain)
- Thanksgiving Harvest Festival (Nov)
- Winter Lights (Nov-Dec)

## Step 2: Fetch Brew Barn Events

Brew Barn is a Wix site — events are rendered inside an **iframe** (Boom Calendar widget) that standard snapshots cannot read. **You must use the screenshot approach:**

1. Navigate to `https://www.brewbarnma.com/events`
2. Wait 5+ seconds for the calendar iframe to render
3. **Take a screenshot** — the events are visible in the rendered page as a list with date, time, and event name
4. Read the event details from the screenshot image

The calendar shows an "Upcoming Events" agenda view with columns: date, time range, and event name + description.

**IMPORTANT:** The `snapshot` tool (aria/accessibility tree) will NOT show events — they are inside a cross-origin iframe from `calendar.boomte.ch`. You MUST use `screenshot` to visually read the events.

Brew Barn typically hosts live music nights (Thu-Sun), trivia, bingo, fish fry, and seasonal events. Events are usually:
- **Thursdays** — Ryan's Trivia Night (6-8pm)
- **Fridays** — Fish Fry + live music (3-8pm / 6-8pm)
- **Saturdays** — Live music + Dan-O Music Bingo (12-3pm / 6-8pm)
- **Sundays** — Live music or seasonal events (4-7pm)

## Step 3: Deduplicate

Some events appear on both sites (e.g., major festivals). Deduplicate by matching event names and dates before creating calendar entries.

## Step 4: Sync to Calendar

Create events on the `clawdbotbochman@gmail.com` calendar using `gws`:

```bash
gws calendar events insert --params '{"calendarId": "primary"}' --json '{
  "summary": "🍎 [Event Name]",
  "description": "Source: Red Apple Farm / Brew Barn\nhttps://www.redapplefarm.com/events",
  "location": "Red Apple Farm, 455 Highland Ave, Phillipston, MA 01331",
  "start": {"date": "2026-07-19"},
  "end": {"date": "2026-07-20"}
}' --account clawdbotbochman@gmail.com
```

### For timed events (e.g., Brew Barn music nights):
```bash
gws calendar events insert --params '{"calendarId": "primary"}' --json '{
  "summary": "🍺 Live Music at Brew Barn - [Artist]",
  "description": "Source: Brew Barn\nhttps://www.brewbarnma.com/events",
  "location": "Brew Barn at Red Apple Farm, 455 Highland Ave, Phillipston, MA 01331",
  "start": {"dateTime": "2026-07-19T19:00:00-04:00", "timeZone": "America/New_York"},
  "end": {"dateTime": "2026-07-19T22:00:00-04:00", "timeZone": "America/New_York"}
}' --account clawdbotbochman@gmail.com
```

### Before creating, check for existing events to avoid duplicates:
```bash
gws calendar events list --params '{
  "calendarId": "primary",
  "q": "Red Apple",
  "timeMin": "2026-01-01T00:00:00-05:00",
  "timeMax": "2026-12-31T23:59:59-05:00",
  "singleEvents": true,
  "orderBy": "startTime"
}' --account clawdbotbochman@gmail.com
```

## Emoji Prefixes

Use these prefixes in event summaries for quick identification:
- 🍎 — Red Apple Farm events
- 🍺 — Brew Barn events
- 🎵 — Live music events
- 🎃 — Fall/Halloween events
- 🎄 — Holiday/winter events

## Notes

- All events go to `clawdbotbochman@gmail.com` (OpenClaw's calendar), NOT Dylan's personal calendar
- Location for all events: **455 Highland Ave, Phillipston, MA 01331** (unless specified otherwise, e.g., Applefest is at Wachusett Mountain)
- Times are Eastern (America/New_York) — use -05:00 for EST, -04:00 for EDT
- For annual events without exact dates yet, create all-day events on the approximate date with "(Date TBD)" in the description
- When exact dates are announced, update existing events rather than creating duplicates
- `gws` outputs JSON — pipe through `jq` if needed for parsing
