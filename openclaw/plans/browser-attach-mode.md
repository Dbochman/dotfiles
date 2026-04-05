# Browser Attach Mode — Pending

## Status: PENDING (revisit on next OpenClaw stable release)

## Goal

Attach OpenClaw to a live Chrome session to reuse signed-in sessions (Star Market, etc.) without re-authenticating.

## Current State (as of 2026-03-14)

OpenClaw v2026.3.13 has `existing-session` driver in config schema but it's NOT functional — the gateway hot-reloads the config but ignores `cdpUrl`, falling back to `DevToolsActivePort` file lookup. Tested 2026-03-14: config accepted, gateway logged reload, but `browser.request` still errored with "Could not find DevToolsActivePort".

## When to Retry

Check changelog for `existing-session` driver fixes on new OpenClaw releases.

## Setup (ready to re-add when driver works)

Chrome remote debugging works fine on Mini (tested CDP at `127.0.0.1:9222`).

Config to add:
```json
{
  "driver": "existing-session",
  "cdpUrl": "http://127.0.0.1:9222",
  "color": "#4285F4"
}
```

Chrome launch flags:
```
--remote-debugging-port=9222 --user-data-dir="$HOME/.chrome-debug-profile"
```

Separate profile required — can't use default Chrome profile.
