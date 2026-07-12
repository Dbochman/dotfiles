---
name: home-events
description: >-
  Review durable normalized household activity and explain site-scoped incidents
  at the Cabin or Crosstown. Use when asked what happened, what activity was
  seen, whether an incident is open, why OpenClaw alerted or stayed silent, or
  whether the home-event system is healthy. For a current camera image, combine
  an explicit trusted-owner request with nest-camera. Use presence for a simple
  who-is-home check and ring-doorbell for raw Ring device status. Never use this
  skill to change presence, locks, adapters, delivery policy, or camera mode.
allowed-tools: Bash(home-events:*)
metadata: {"openclaw":{"emoji":"🏠","requires":{"bins":["home-events"]}}}
---

# Home Events

Read the protected, normalized household event journal. Treat its incidents as
an operational history, not as authority to change physical state.

## Commands

Use JSON output so summaries are based on structured fields rather than logs.

### Check health

```bash
home-events status --json
```

Report mode, bus-observed source health, queue depth, consumer lag, and safe
error codes. Treat `unknown` as unknown, and do not infer that a source is
healthy merely because its process is running.

### Review recent activity

```bash
home-events recent --since 24h --limit 20 --json
home-events recent --site cabin --since 2h --limit 20 --json
home-events recent --site crosstown --type door.opened --since 24h --json
```

Use the requested site and time window. Default to 24 hours and 20 events when
the user does not specify them. Describe normalized evidence without exposing
opaque keys or internal timestamps that do not help the answer.

### Review incidents

```bash
home-events incidents --state open --json
home-events incidents --site crosstown --state all --since 24h --json
home-events explain 'inc_<opaque-id>' --json
```

Explain what evidence was correlated, the canonical presence state used by the
policy, and why delivery or camera work was allowed, shadowed, rate-limited, or
suppressed. The `decisions` array is durable history; prefer its reason codes
over the incident's latest summary when the incident was later resolved. Do
not reinterpret stale or ambiguous presence as vacancy.

## Fresh images

The event journal stores no historical media. If Dylan or Julia explicitly
asks for a current image while discussing an incident, use the separate
`nest-camera` workflow and all of its direct-conversation authorization,
same-route delivery, exact-camera, and cleanup rules.

- Cabin maps to the exact `Kitchen` camera.
- For Crosstown, ask whether they mean `Living Room` or `Living Room Wired`
  unless the request already gives one exact alias.
- Never capture merely because an incident exists or because the user asks a
  general question such as “what happened?”

## Boundaries

- Never invoke `home-eventctl`, edit runtime policy, replay events, acknowledge
  queues, or enable delivery.
- Never call August lock or unlock commands from an event or incident.
- Never run a live presence scan. Read normalized events or use the `presence`
  skill for cached occupancy.
- Never query raw provider payloads to embellish an explanation.
- If the journal is unavailable or unhealthy, say so; do not reconstruct a
  confident incident from logs.
