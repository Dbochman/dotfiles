# OpenClaw Cron Jobs

Reference for all cron jobs defined in `~/.openclaw/cron/jobs.json` on the Mac Mini.

> **Important**: When removing jobs from `jobs.json`, also delete their run state files at `~/.openclaw/cron/runs/<job-id>.jsonl` — the cron subsystem persists `nextRunAtMs` independently and will keep executing ghost jobs otherwise.

## Recurring Jobs

| ID | Schedule | Delivery | Description |
|----|----------|----------|-------------|
| `gws-julia-morning-briefing-0001` | Daily 7 AM ET | silent (self-delivers text via BB API) | Julia's morning briefing: calendar preview, inbox triage (label/archive/draft), text summary to Julia via iMessage |
| `gws-dylan-morning-briefing-0001` | Daily 8 AM ET | announce to Dylan via BB | Dylan's morning briefing: calendar (7-day) + inbox summary (24h). Read-only, no email actions |
| `weekly-report-0001` | Sundays 3 PM ET | announce to Dylan via BB | Combined weekly activity report (log parsing, API calls, sessions, week-over-week), security check (gateway, BB, auth, disk, services), and CrisisMode health scan |

## One-Shot Date Night Bookings

Monthly date nights for Dylan and Julia (2 people, Fridays at 7 PM, Newton/Brookline area via Resy). All `deleteAfterRun: true`, delivered to group chat (chat-id 170).

| ID | Fires On | Cuisine |
|----|----------|---------|
| `datenight-apr-italian` | Apr 1, 2026 | Italian |
| `datenight-may-mediterranean` | May 1, 2026 | Mediterranean |
| `datenight-jun-tapas` | Jun 1, 2026 | Spanish/Tapas |
| `datenight-jul-japanese` | Jul 1, 2026 | Japanese/Asian |
| `datenight-aug-farmtotable` | Aug 1, 2026 | Farm-to-Table |
| `datenight-sep-steakhouse` | Sep 1, 2026 | American/Steakhouse |
| `datenight-oct-indian` | Oct 1, 2026 | Indian |
| `datenight-nov-american` | Nov 1, 2026 | Modern American |
| `datenight-dec-upscale` | Dec 1, 2026 | Upscale (French/Italian/Contemporary) |

## One-Shot Double Date Bookings

Quarterly double dates for 4 (Dylan, Julia, Will, Ayesha). Thursdays or Fridays at 7 PM, Brookline, via OpenTable or Resy. All `deleteAfterRun: true`, delivered to group chat.

| ID | Fires On | Cuisine |
|----|----------|---------|
| `doubledate-q2-apr-thai` | Apr 1, 2026 | Thai |
| `doubledate-q3-jul-korean` | Jul 1, 2026 | Korean |
| `doubledate-q4-oct-mexican` | Oct 1, 2026 | Mexican |
| `doubledate-q1-jan27-french` | Jan 2, 2027 | French |

## One-Shot Quarterly Group Dinner Bookings

Quarterly group dinners for 4 via Resy. Party of 4 at 6:30 PM on Fridays, Brookline/JP area. Booked ~2 weeks before the target month. All `deleteAfterRun: true`, delivered to group chat. Calendar events created on Julia's calendar inviting Dylan.

| ID | Fires On | Target Month |
|----|----------|--------------|
| `qd-booking-2026-07-june15` | Jun 15, 2026 | July 2026 |
| `qd-booking-2026-10-sep15` | Sep 15, 2026 | October 2026 |
| `qd-booking-2027-01-dec15` | Dec 15, 2026 | January 2027 |

## Removed Jobs (Historical)

| ID | Removed | Reason |
|----|---------|--------|
| `crisismode-health-scan-0001` | 2026-03-24 | Consolidated into `weekly-report-0001` |
| `weekly-activity-report` | 2026-03-24 | Consolidated into `weekly-report-0001` |
| `weekly-security-reminder` | 2026-03-24 | Consolidated into `weekly-report-0001` |
| `weekly-upgrade-verify-0001` | 2026-03-12 | Weekly auto-upgrade removed; upgrades now manual |
