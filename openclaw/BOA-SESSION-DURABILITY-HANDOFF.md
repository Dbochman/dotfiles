# BoA Session Durability Soak Handoff

**Started:** 2026-06-18

This is the continuation checklist for the active Bank of America session
durability experiment. Read this first, then use
`FINANCIAL-DASHBOARD.md` and `LAUNCHAGENTS.md` for the full implementation
and recovery details.

## Objective

Determine whether the authenticated Pinchtab Chrome session can remain usable
for at least 24-48 hours without human intervention. The experiment must
distinguish a browser-inactivity timeout from a BoA server-side absolute or
risk timeout.

Do not change cadence, add jitter, introduce quiet hours, restart Chrome, or
manually run the agents while the initial measurement is in progress. Those
changes make the result difficult to interpret.

## Deployed State

| Component | Schedule | Purpose |
|---|---:|---|
| `ai.openclaw.boa-keepalive` | 5 minutes | Verifies the authenticated live tab before and after a same-origin account API request, sends trusted browser activity, and atomically stores the current cookie jar. |
| `ai.openclaw.boa-browser-heartbeat` | 1 minute | Sends no account API request. It sends browser activity and dynamically accepts the BoA two-minute inactivity warning's `OK` control when needed. |
| Weekly cron `financial-scrape-0001` | Sunday 04:05 ET | Uses direct cookie replay first, then raw-CDP fallback to the existing Pinchtab tab if replay is rejected. |

The normal BoA scrape first uses `requests` with the mode-`0600` cookie
store at `~/.openclaw/.boa_cookies.json`. On authentication failure it uses
raw CDP, not Playwright `connect_over_cdp`, because Chrome 149 rejects the
Playwright attach path.

Runtime locations on the Mini:

| Item | Path |
|---|---|
| Keep-alive plist | `~/Library/LaunchAgents/ai.openclaw.boa-keepalive.plist` |
| Heartbeat plist | `~/Library/LaunchAgents/ai.openclaw.boa-browser-heartbeat.plist` |
| Keep-alive log | `~/Library/Logs/boa-keepalive.log` |
| Heartbeat log | `~/Library/Logs/boa-browser-heartbeat.log` |
| Cookie store | `~/.openclaw/.boa_cookies.json` |
| Scraper | `~/repos/financial-dashboard/scrape_mortgage.py` |

Source plists and source cron prompt are under `~/dotfiles/openclaw/`.
The runtime cron file is `~/.openclaw/cron/jobs.json`; do not overwrite it
blindly from the tracked source because it can contain newer operational edits.

## Known Validation

- The BoA browser warning was observed live and the heartbeat's accessible
  `OK` handler returned `warning_dismissed`.
- A title alone is not valid BoA authentication evidence. The scraper rejects
  a visible login form even if the title still says "Accounts Overview."
- A successful API response alone is not sufficient either. Use
  `--verify-auth` to confirm the real tab remains authenticated.
- Logs contain status and safe cookie-count or expiry metadata only. Never
  print cookie values, credentials, or account response bodies.

## Evaluation After 24-48 Hours

Run these checks from a machine that can SSH to the Mini:

```bash
ssh dylans-mac-mini 'tail -n 80 ~/Library/Logs/boa-keepalive.log'
ssh dylans-mac-mini 'tail -n 80 ~/Library/Logs/boa-browser-heartbeat.log'
ssh dylans-mac-mini 'cd ~/repos/financial-dashboard && ./venv/bin/python3 scrape_mortgage.py --lender boa --verify-auth'
ssh dylans-mac-mini 'cd ~/repos/financial-dashboard && ./venv/bin/python3 scrape_mortgage.py --lender boa --headless --merge'
```

The result is a success only when all of the following are true:

- Keep-alive entries remain `ok`.
- Heartbeat entries remain `ok` or `warning_dismissed`.
- `--verify-auth` reports `authenticated`.
- The normal BoA scrape succeeds, preferably through cookie replay without
  needing the browser fallback.

Record the earliest non-healthy timestamp, if any. Check for `api_rejected`
or HTTP 4xx metadata as evidence of an API/session expiration, and compare it
with the browser-auth result to identify whether the live tab also expired.

## Failure Procedure

Before any recovery action:

1. Capture the last 80 lines of both logs and run `--verify-auth`.
2. Record whether the failure is `cdp_unavailable`, `not_authenticated`,
   `api_rejected`, `tab_lost_auth`, `warning_unhandled`, or another
   status.
3. Preserve `~/.openclaw/.boa_cookies.json`. Do not delete it, print it, or
   copy its values into a ticket or chat.
4. Do not kill or restart Pinchtab Chrome before collecting evidence. BoA
   session cookies are process-bound and a restart destroys useful evidence.

If both replayed cookies and the live tab have expired, recovery is one
interactive login in the Pinchtab Chrome window. Do not run `--re-auth` for
BoA from cron or a LaunchAgent. After the login reaches the account overview,
run the normal BoA scrape once to capture a fresh cookie jar, then let the two
interval agents resume.

## Observed Outcome: 2026-06-18

The initial soak did not reach 24 hours.

| Time (EDT) | Observation |
|---|---|
| 10:18:28 | First known healthy keep-alive after the fresh login. |
| 20:26:37 | Last healthy keep-alive; this was also the last cookie-store update. |
| 20:31:10 | Heartbeat successfully dismissed the browser inactivity warning. |
| 20:31:38 | Keep-alive received `api_rejected http_status=403`. |
| 20:33:12 | Heartbeat first reported `not_authenticated`. |
| 23:36:26 | Independent `--verify-auth` reported `not_authenticated`. |

The agents ran continuously at their expected one- and five-minute cadences,
and CDP remained available. The warning dialog was dismissed repeatedly,
including just before the HTTP 403. The evidence therefore rules out a
browser-inactivity failure and is consistent with a BoA server-side absolute
or risk timeout after at least 10 hours and 13 minutes of authenticated use.

The cookie file remains mode `0600` and was preserved. Its expiry metadata
was later than the failure, so cookie expiry metadata is not a reliable proxy
for server-side session validity.

Leave the two agents loaded unless log noise becomes a problem. They make no
browser activity change while the tab is unauthenticated and will resume on
their next interval after a later interactive login. Do not use this result as
evidence that the current design can run BoA autonomously.

## Decision After the Soak

Do not test lower frequency or jitter as a remedy for this failure. The
current heartbeat handles the UI's inactivity prompt, but it does not prevent
the server-side timeout. Any future design must plan for an interactive
recovery at least this often or use the PDF statement parser as the reliable
backstop. A second soak may characterize the timeout more precisely after the
next manual login, but it should not be treated as an autonomous solution.

## Worktree Safety

The dotfiles and financial-dashboard repositories currently have unrelated
Plaid worktree changes. Preserve them. Do not use `git reset --hard`, do not
stage unrelated files, and do not assume a dirty file belongs to this BoA
experiment.
