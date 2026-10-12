# Cabin Magazine

**Port 8555**

**LAN URL:** `http://dylans-mac-mini:8555`

**Tailscale URL:** `http://dylans-mac-mini.tail3e55f9.ts.net:8555`

**Service:** `ai.openclaw.cabin-magazine`

The Cabin Magazine is the private, multi-page issue reader. It serves the
checked-in magazine from the Cabin repository and keeps ordinary checklist
and observation state consistent across Dylan and Julia's devices. It is
reachable on the home LAN and Tailscale tailnet only; no public-internet
exposure or router port forwarding is intended.

## What It Shows

- **October issue** — the eight-page `vol-1-no-02-draft-v2` magazine with its
  cover, monthly work, look-ahead, cooking, gardening, design, long-term, and
  possibilities pages
- **Shared checklists** — punchlist steps, shopping items, recipe completion,
  and other ordinary checkboxes restored from server-side state
- **Shared observations** — `textarea[data-observation]` values restored by
  issue, page, and element ID
- **Existing capture surfaces** — field notes, prompts, and recipe photos
  continue through the Cabin repository's durable capture spool and are not
  written to the checklist-state database

## Data Sources

| Source | Frequency | Data |
|--------|-----------|------|
| Cabin repository `site/` | On page request | Magazine HTML, CSS, scripts, and checked-in media |
| `~/.openclaw/cabin-magazine/state.sqlite` | On control change after 500 ms | Per-issue, per-page checkbox and observation values |
| Cabin capture spool and journal database | On capture submission and five-second ingest poll | Field notes, prompts, photos, receipts, and canonical evidence |
| Compiled Cabin kitchen catalog | On cooking-page request | Reviewed recipes, shopping quantities, and recipe-card metadata |

## State API

`GET /api/state?issue=<issueId>&page=<pageId>` returns element values for one
page. `POST /api/state` upserts one `{issueId, pageId, elementId, value}` JSON
record. The state endpoint is same-origin, while all capture-marked controls
remain on the separate `/api/inputs` spool path.

The LaunchAgent runs:

```text
/usr/local/bin/node /Users/dbochman/Repos/cabin/scripts/serve.js --host=0.0.0.0 --port=8555
```

The all-interface bind makes the service reachable over the LAN and Tailscale;
the Mini remains behind NAT with no public port forwarding.

## Files

| File | Path |
|------|------|
| Server | `/Users/dbochman/Repos/cabin/scripts/serve.js` |
| Shared-state client | `/Users/dbochman/Repos/cabin/site/assets/state-sync.js` |
| Magazine pages | `/Users/dbochman/Repos/cabin/site/vol-1-no-02-draft-v2/` |
| State database | `~/.openclaw/cabin-magazine/state.sqlite` |
| Capture runtime | `/Users/dbochman/Repos/cabin/data/` |
| LaunchAgent | `openclaw/launchagents/ai.openclaw.cabin-magazine.plist` |
| Logs | `~/.openclaw/logs/cabin-magazine.{log,err.log}` |

## Verification

```bash
ssh dylans-mac-mini 'curl -fsS http://127.0.0.1:8555/api/health'
curl -fsS http://dylans-mac-mini:8555/vol-1-no-02-draft-v2/
curl -fsS http://dylans-mac-mini.tail3e55f9.ts.net:8555/vol-1-no-02-draft-v2/
```

Installing or reloading the LaunchAgent is an attended deployment step and is
not performed merely by updating this repository.
