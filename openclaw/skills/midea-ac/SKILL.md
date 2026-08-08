---
name: midea-ac
description: Inspect and control exact locally enrolled Midea or MSmartHome air conditioners. Use for Midea AC status, room temperature, power, mode, setpoint, fan, swing, eco, boost, sleep, display, energy use, device errors, or local AC availability. Do not use for Cielo-controlled minisplits or Nest central HVAC.
allowed-tools: Bash(midea-ac:*)
metadata: {"openclaw":{"emoji":"❄","requires":{"bins":["midea-ac"]}}}
---

# Midea AC

Use the `midea-ac` CLI. Runtime status and control stay on the local LAN using
per-device V3 credentials; they do not use or retain the Midea cloud account.

Exact enrolled aliases:

- `cabin-air-conditioner`
- `cabin-lil-air-conditioner`

## Read-only commands

```bash
midea-ac status --json
midea-ac status cabin-bedroom --json
midea-ac devices --json
```

Use only these exact aliases or aliases returned by `devices`. Report an offline device as
unavailable; do not substitute another unit.

Status temperatures are Fahrenheit. Energy fields are included only when the
unit reports them.

## Controls

Run status first, then execute one exact-device command:

```bash
midea-ac on cabin-bedroom --json
midea-ac off cabin-bedroom --json
midea-ac temperature cabin-bedroom 72 --json
midea-ac mode cabin-bedroom cool --json
midea-ac fan cabin-bedroom medium --json
midea-ac swing cabin-bedroom both --json
midea-ac eco cabin-bedroom on --json
midea-ac boost cabin-bedroom off --json
midea-ac sleep cabin-bedroom on --json
midea-ac display cabin-bedroom off --json
```

- Modes: `auto`, `cool`, `dry`, `heat`, `fan`.
- Fans: `auto`, `silent`, `low`, `medium`, `high`, `full`.
- Swing: `off`, `vertical`, `horizontal`, `both`.
- Temperatures: whole or half degrees from 60–86 °F. Setting a temperature
  preserves the current power state; use `on` as a separate command when the
  request also says to start the unit.
- `mode` follows the device protocol and turns the selected unit on.

The CLI sends a mutation once, refreshes status once, and reports whether the
requested state was verified. If it reports `outcome_unknown`, do not retry
automatically; reconcile with a fresh status request or the physical unit.

Use controls for an explicit owner request or an already-approved household
routine. Do not initiate comfort changes, vacancy actions, or event-driven
controls merely because status data is available.

## Enrollment boundary

Enrollment is operator-only and is not part of ordinary skill use. It briefly
uses `MIDEA_USERNAME` and `MIDEA_PASSWORD` to retrieve and verify each V3
token/key, then writes only the per-device local bindings to an owner-only
file. It never stores the cloud username or password.

Do not invoke `operator-inspect` or `operator-enroll` from an unattended job,
and never pass a password on the command line. Follow the `1password` skill for
attended exact-field injection.

## Event boundary

Midea readings are not occupancy evidence. Do not publish temperature samples,
energy samples, or control commands to the home-event bus. A future shadow
adapter may publish debounced unit unavailable/recovered transitions and
nonzero error-code transitions after the enrolled aliases have soaked cleanly;
it must remain observation-only and must not affect presence or control.
