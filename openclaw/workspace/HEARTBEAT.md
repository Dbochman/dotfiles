# HEARTBEAT.md
# Fires every 12h from gateway start. Keep this ultra-lean.
# Do not duplicate scheduled cron/reporting work.

## On each heartbeat:

No routine actions. Stay silent unless the gateway provides a current failure that clearly needs attention.

Health ownership:
- Native iMessage is the active transport; BlueBubbles is rollback-only.
- Weekly health/security reporting runs through `weekly-report-0001` and `~/.openclaw/bin/openclaw-weekly-report.py`.
- Do not run BlueBubbles or CrisisMode checks from heartbeat unless Dylan asks or a current incident points there.
