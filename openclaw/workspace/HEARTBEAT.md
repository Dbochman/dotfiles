# HEARTBEAT.md
# Fires every 12h from gateway start. Keep this ultra-lean.
# Detailed health checks run via the 9AM/9PM cron job instead.

## On each heartbeat:

1. **BB ping** — `curl -sf http://localhost:1234/api/v1/ping`. If it fails, message Dylan: "BlueBubbles is down."

That's it. Everything else runs via the health-check cron job.
