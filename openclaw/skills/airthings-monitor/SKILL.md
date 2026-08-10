---
name: airthings-monitor
description: Read the exact locally enrolled Airthings Wave Enhance air-quality monitor. Use for Cabin Living Room CO2, VOC, temperature, humidity, pressure, ambient noise, light, battery, air-quality level, or Bluetooth availability. Do not use for HVAC control, occupancy inference, or unrelated Bluetooth sensors.
allowed-tools: Bash(airthings:*)
metadata: {"openclaw":{"emoji":"🌿","requires":{"bins":["airthings"]}}}
---

# Airthings Monitor

Use the read-only `airthings` CLI. It talks directly to the enrolled Wave
Enhance over Bluetooth and does not use an Airthings account or cloud API.
Status is cache-first; when a fresh read is required, the wrapper delegates it
through the macOS-authorized Homebrew `Python.app` identity and returns the
updated protected cache.

```bash
airthings status --json
airthings devices --json
```

The enrolled alias is `cabin-living-room-airthings`. Report unavailable or
stale readings as such; never substitute another Bluetooth device or an older
measurement as current.

Interpret the device's own published bands as follows:

- CO2: good below 800 ppm, fair from 800 through 999, poor at 1000 or above.
- VOC: good below 250 ppb, fair from 250 through 1999, poor at 2000 or above.
- Humidity: good from 30% through 59%, fair from 25% through 29% or 60% through
  69%, and poor outside those bands.

Treat these bands as environmental context, not a medical diagnosis. Prefer
plain suggestions such as ventilation when CO2 or VOC is elevated.

## Boundaries

- Read status only. Enrollment is an attended operator workflow and is not an
  ordinary skill action.
- Do not invoke `airthings-history-import`; CSV backfill is an attended
  operator workflow outside this skill's allowed tools.
- Do not infer presence from CO2, noise, light, or any other reading.
- Do not publish raw samples or threshold crossings to the home-event bus.
- Do not control HVAC, fans, windows, or other equipment merely because a
  reading is elevated. Controls require a separate explicit owner request or
  approved automation policy.
- Never expose the protected Bluetooth identifier or device serial.
