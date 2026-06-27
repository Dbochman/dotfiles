# BoA Session Durability Soak Handoff

**Started:** 2026-06-18

**Status:** The interval-agent experiment is complete. Both BoA interval
LaunchAgents are persistently disabled on the Mini. The weekly cron now has a
guarded raw-CDP re-auth fallback: one credential submission only after stale
cookie replay and an explicitly `not_authenticated` Pinchtab tab.

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
| Weekly cron `financial-scrape-0001` | Sunday 04:05 ET | Uses cookie replay, raw-CDP tab fallback, then one guarded raw-CDP re-auth only when the tab is explicitly signed out. |

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

Source plists and canonical cron definitions are under `~/dotfiles/openclaw/`.
Live cron state is SQLite-backed. Use `openclaw cron list --all --json` to read
it and supported `openclaw cron` commands to mutate it; do not copy a legacy
`~/.openclaw/cron/jobs.json` artifact over runtime state.

## Known Validation

- The BoA browser warning was observed live and the heartbeat's accessible
  `OK` handler returned `warning_dismissed`.
- A title alone is not valid BoA authentication evidence. The scraper rejects
  a visible login form even if the title still says "Accounts Overview."
- A successful API response alone is not sufficient either. `--verify-auth`
  confirms the real tab state before the cron decides whether re-auth is safe.
- The guarded `--boa-re-auth` command is tested against its raw-CDP control
  flow and has one live successful no-MFA trial. It is not an MFA solver.
- Logs contain status and safe cookie-count or expiry metadata only. Never
  print cookie values, credentials, or account response bodies.

## Historical Evaluation

The interval experiment was conclusive: it prevented the browser idle warning
but did not prevent BoA's server-side cutoff. The two logs remain forensic
evidence; they are no longer health signals for a running service.

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

The weekly cron now runs the following guarded sequence after a normal BoA
scrape failure:

1. Run `--verify-auth` against the existing Pinchtab tab.
2. Proceed only when its status is exactly `not_authenticated`. A
   `cdp_unavailable` result is an incident, not permission to submit creds.
3. Supply `SCRAPER_USER` and `SCRAPER_PW` from the cron agent's authorized
   context and run `--boa-re-auth` once.
4. Retry the normal scrape once only after `authenticated` or a race-safe
   `already_authenticated` result.

`--boa-re-auth` does not launch or navigate Chrome, fetch credentials, solve
MFA, or retry. It stops and alerts on MFA, a security challenge, unavailable
form, rejected login, timeout, or CDP failure. Do not use generic
`--re-auth` for BoA, and do not put credential lookup into a LaunchAgent.

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

The next validation is observational: record the first real cron-triggered
re-auth result and whether the single normal-scrape retry succeeds. No retry
loop, MFA automation, or interval agent should be added without new evidence.
Until repeat expiries prove the fallback, the PDF statement parser remains the
reliable backstop.

## Worktree Safety

The dotfiles and financial-dashboard repositories currently have unrelated
Plaid worktree changes. Preserve them. Do not use `git reset --hard`, do not
stage unrelated files, and do not assume a dirty file belongs to this BoA
experiment.
