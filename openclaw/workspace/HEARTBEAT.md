# HEARTBEAT.md
# Fires every 12h from gateway start. Keep this ultra-lean.
# Do not duplicate scheduled cron/reporting work.

## On each heartbeat:

No routine actions. Stay silent unless the gateway provides a current failure that clearly needs attention.

Health ownership:
- Native `imsg`-backed iMessage is the only active Messages transport.
- Weekly health/security reporting runs through `weekly-report-0001` and `~/.openclaw/bin/openclaw-weekly-report.py`.
- Do not run retired BlueBubbles checks. Run CrisisMode only when Dylan asks or a current incident points there.
