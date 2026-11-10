---
name: airvalent-monitor
description: Read Crosstown Living Room CO2 from the Airvalent monitor bridged over Wi-Fi by the ATOM Lite. Use for Crosstown air quality, CO2, or Airvalent availability; Cabin Airthings uses airthings-monitor.
allowed-tools: Bash(airvalent:*)
metadata: {"openclaw":{"emoji":"🌿","requires":{"bins":["airvalent"]}}}
---

# Airvalent Monitor

Run `airvalent status --json`. This reads the protected cache from the five-minute
sampler. Transport is Mini → always-on Crosstown Mac → ATOM Lite → Airvalent;
it does not depend on Dylan's laptop or macOS Bluetooth pairing.

Report `co2_ppm` only when `online` is true. If unavailable or stale, describe
`last_known_co2_ppm` explicitly as last known and include `read_at` or age.
`read_at` is the successful Bluetooth read time; `measurement_at` is unknown.
The sensor currently measures every five minutes; repeated polls need not differ.
Only CO2 decoding is validated. Do not invent temperature, humidity, or VOC values.

The scheduled collector can recover this enrolled sensor with its privately
provisioned fixed PIN: at most one PIN submission per run, after an actual
passkey request. Explicit device rejections and missing enrollment prerequisites
latch until operator repair. `ble_connection_timeout` and
`authentication_incomplete` retry automatically with a delay increasing from
five to thirty minutes; `next_retry_at` shows the earliest retry time. Do not
describe those transient failures as a rejected PIN or ask for re-pairing. Initial
enrollment and credential changes remain attended operator workflows. Do not
invoke the sampler, pairing tools, or PIN API actions from this skill, and never
read or disclose the recovery PIN. Report `reauth_required` as needing repair.
Do not infer occupancy, send alerts, or change HVAC based on a reading; those
require a separately requested policy. Never expose sensor addresses or API keys.
