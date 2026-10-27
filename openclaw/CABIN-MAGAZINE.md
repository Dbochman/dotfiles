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

- **November working issue** — the eight-page `vol-1-no-03-draft-v2` magazine with its
  cover, monthly work, look-ahead, cooking, gardening, design, long-term, and
  possibilities pages
- **Shared checklists** — punchlist steps, shopping items, recipe completion,
  and other ordinary checkboxes restored from server-side state
- **Shared observations and text drafts** — saved while typing, with pending
  edits retained locally and restored by issue, page, and stable element ID
- **Readable journal entries** — submitted notes and prompt responses remain
  visible beneath the form after reload, before and after ingest
- **Existing capture surfaces** — field notes, prompts, and recipe photos
  continue through the Cabin repository's durable capture spool and are not
  written to the checklist-state database

## Data Sources

| Source | Frequency | Data |
|--------|-----------|------|
| Cabin repository `site/` | On page request | Magazine HTML, CSS, scripts, and checked-in media |
| `~/.openclaw/cabin-magazine/state.sqlite` | On input/change after 500 ms and page-hide flush | Per-issue, per-page checkbox, observation, and unsubmitted text-draft values |
| Cabin capture spool and journal database | On capture submission and five-second ingest poll | Field notes, prompts, photos, receipts, and canonical evidence |
| Compiled Cabin kitchen catalog | On cooking-page request | Reviewed recipes, shopping quantities, and recipe-card metadata |

## State API

`GET /api/state?issue=<issueId>&page=<pageId>` returns element values for one
page. `POST /api/state` upserts one `{issueId, pageId, elementId, value}` JSON
record. Draft form values use `draft:<article>:<field>` keys; autosaving does
not submit them or make them journal evidence. Explicit submission remains on
the separate `/api/inputs` spool path. `GET /api/journal` requires `issueId`,
`pageId`, and `articleId` and returns recent submitted notes/prompts from the
validated spool and canonical journal, without storage paths or credentials.

The [Cabin magazine skill](skills/cabin-magazine/SKILL.md) documents the two
authoring paths, current catalog/data contracts, and reviewed illustrations.
The generic draft CLI cannot regenerate the authored `-v2` pages. The root URL
still serves historical Volume One; use the explicit November URL above.

The LaunchAgent runs:

```text
/Users/dbochman/.local/share/openclaw/runtimes/node-v22.23.4-darwin-arm64/bin/node /Users/dbochman/Repos/cabin/scripts/serve.js --host=0.0.0.0 --port=8555
```

The all-interface bind makes the service reachable over the LAN and Tailscale;
the Mini remains behind NAT with no public port forwarding.

## Files

| File | Path |
|------|------|
| Server | `/Users/dbochman/Repos/cabin/scripts/serve.js` |
| Shared-state client | `/Users/dbochman/Repos/cabin/site/assets/state-sync.js` |
| Current magazine pages | `/Users/dbochman/Repos/cabin/site/vol-1-no-03-draft-v2/` |
| State database | `~/.openclaw/cabin-magazine/state.sqlite` |
| Capture runtime | `/Users/dbochman/Repos/cabin/data/` |
| LaunchAgent | `openclaw/launchagents/ai.openclaw.cabin-magazine.plist` |
| Installed login LaunchAgent | `~/Library/LaunchAgents/ai.openclaw.cabin-magazine.plist` |
| Logs | `~/.openclaw/logs/cabin-magazine.{log,err.log}` |

## Verification

```bash
ssh dylans-mac-mini 'curl -fsS http://127.0.0.1:8555/api/health'
curl -fsS http://dylans-mac-mini:8555/vol-1-no-03-draft-v2/
curl -fsS http://dylans-mac-mini.tail3e55f9.ts.net:8555/vol-1-no-03-draft-v2/
```

Installing or reloading the LaunchAgent is an attended deployment step and is
not performed merely by updating this repository.

The October 16 persistence repair installs an identical source copy in the
Mini's user LaunchAgents directory without restarting the already-loaded job.
Loading the repository plist directly is not a substitute for installing it
there. `RunAtLoad` and `KeepAlive` apply after `dbochman` logs into the GUI;
the service is not available before that login. The current job can retain its
repository load path until logout/reboot. On the next login, verify its installed
load path, running state, health endpoint, and a magazine page. The development
preview in tmux (`cabin-serve`, loopback port 4173) is not needed for production.
Do not start another server on port 8555 or rerun ingest/build to test startup.
