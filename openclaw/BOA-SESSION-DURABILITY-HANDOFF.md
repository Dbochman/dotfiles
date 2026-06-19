# BoA Session Durability Soak Handoff

**Started:** 2026-06-18

**Status:** The interval-agent experiment is complete. Both BoA interval
LaunchAgents are persistently disabled on the Mini. A one-shot raw-CDP
credential-login trial succeeded without MFA, but a cron re-auth fallback has
not yet been implemented or enabled.

This is the continuation checklist for the Bank of America scrape path. Read
this first, then use `FINANCIAL-DASHBOARD.md` and `LAUNCHAGENTS.md` for the
full implementation and recovery details.

## Objective

The original objective was to determine whether trusted browser activity could
keep the authenticated Pinchtab Chrome session usable for 24-48 hours. That
experiment is complete: it ruled out browser inactivity but found a BoA
server-side absolute or risk timeout after roughly ten hours.

## Deployed State

| Component | Schedule | Purpose |
|---|---:|---|
| `ai.openclaw.boa-keepalive` | Disabled | Retired interval experiment. It verified the tab and persisted the cookie jar every five minutes. |
| `ai.openclaw.boa-browser-heartbeat` | Disabled | Retired interval experiment. It dismissed the two-minute browser inactivity warning every minute. |
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

## Historical Evaluation After 24-48 Hours

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

If both replayed cookies and the live tab have expired, the current deployed
recovery is one interactive login in the Pinchtab Chrome window, followed by
the normal BoA scrape to capture a fresh cookie jar. The interval agents remain
disabled.

A controlled raw-CDP credential-login trial succeeded from the signed-off BoA
page without MFA. That is evidence for a future on-demand fallback, not a
license to run generic `--re-auth` from cron or a LaunchAgent. A future
implementation must use the existing Pinchtab Chrome tab, make exactly one
normal credential submission, and stop with an alert on MFA, a security
challenge, an unavailable form, or any login error. It must not use `op`
inside a LaunchAgent.

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
| About 23:44 | Controlled raw-CDP credential login completed without MFA; independent verification passed and the normal scrape captured a fresh cookie jar. |

The agents ran continuously at their expected one- and five-minute cadences,
and CDP remained available. The warning dialog was dismissed repeatedly,
including just before the HTTP 403. The evidence therefore rules out a
browser-inactivity failure and is consistent with a BoA server-side absolute
or risk timeout after at least 10 hours and 13 minutes of authenticated use.

The cookie file remains mode `0600` and was preserved. Its expiry metadata
was later than the failure, so cookie expiry metadata is not a reliable proxy
for server-side session validity.

Both interval labels were booted out and persistently disabled after the
recovery. Their source plists remain as reference material, but they must not
be reloaded as part of normal BoA recovery. Do not use either the failed soak or
the single successful credential-login trial as evidence of fully autonomous
BoA operation.

## Decision After the Soak

Do not test lower frequency or jitter as a remedy for this failure. The
heartbeat handled the UI's inactivity prompt, but it did not prevent the
server-side timeout.

The next implementation should be a guarded raw-CDP re-auth command invoked
only after the normal scrape reports both stale replayed cookies and an
unauthenticated live tab. The OpenClaw cron agent may supply credentials from
its authorized service-account context; a LaunchAgent must not. The command
must attempt login once, never log values, verify the live tab, run the normal
scrape to capture cookies, and alert rather than retry on MFA or failure.

Until that implementation has passed repeat attempts across session expiries,
the PDF statement parser remains the reliable backstop.

## Worktree Safety

The dotfiles and financial-dashboard repositories currently have unrelated
Plaid worktree changes. Preserve them. Do not use `git reset --hard`, do not
stage unrelated files, and do not assume a dirty file belongs to this BoA
experiment.
