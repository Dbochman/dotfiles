---
name: nest-thermostat
description: Control Nest thermostats, check home climate status, view temperature history, and check outdoor weather. Use when asked about home temperature, thermostat settings, heating, cooling, room climate, temperature history or trends, or outdoor weather.
allowed-tools: Bash(nest:*)
metadata: {"openclaw":{"emoji":"T","requires":{"bins":["nest"]}}}
---

# Nest Thermostat Control

Control Google Nest thermostats via the `nest` CLI. Unattended commands use the protected OpenClaw cache; 1Password access is attended and exact-field only.

## Available Commands

### Check status of all thermostats + weather
```bash
nest status
```
Returns outdoor weather conditions plus room name, current temperature, setpoint, mode, HVAC status, and humidity for each thermostat.

### Check outdoor weather only
```bash
nest weather
```

### Set temperature (Fahrenheit)
```bash
nest set <room> <temp>
```
Example: `nest set bedroom 72`

### Change thermostat mode
```bash
nest mode <room> <HEAT|OFF>
```
Example: `nest mode solarium off`

### Toggle eco mode
```bash
nest eco <room> on
nest eco <room> off
```

### Toggle an entire site's thermostat group
```bash
nest eco-site cabin on --dry-run
nest eco-site cabin on
nest eco-site crosstown off
```
The dry run is read-only. Site groups require the exact thermostats listed
below; missing, duplicated, or unexpected site thermostats fail before writes.
Every target must pass state preflight before any target is changed. Each
change requires Eco-mode readback; already-satisfied targets are skipped.
A partial failure returns nonzero without rolling back other targets.
Do not clear vacancy markers to retry HVAC: use only the approved site command
and check `nest status` afterward. See
[vacancy recovery](../../VACANCY-AUTOMATION.md#thermostat-targeting-and-recovery).

### Record a snapshot to history
```bash
nest snapshot
```
Records current state of all thermostats + outdoor weather to `~/.openclaw/nest-history/YYYY-MM-DD.jsonl`. This runs automatically every 30 minutes via cron.

### View temperature history
```bash
nest history [hours] [room]
```
Examples:
- `nest history` — last 24 hours, all rooms
- `nest history 48` — last 48 hours, all rooms
- `nest history 24 bedroom` — last 24 hours, bedroom only

Shows indoor/outdoor min/max/avg temperature, humidity, setpoints, HVAC heating percentage, and indoor-outdoor delta.

### Raw JSON dump (for debugging)
```bash
nest raw
```

## Rooms & Home Disambiguation

There are two homes. Rooms are prefixed with home name in the Nest API.

### Cabin (Philly)
- **Philly Solarium** (matches: solar, sol)
- **Philly Living Room** (matches: philly living)
- **Philly Bedroom** (matches: bed, bedroom)

### Crosstown (Boston — Crosstown residence)
- **19Crosstown Living Room** (matches: crosstown)

Room commands prefer an exact qualified name, then accept a case-insensitive
substring only when it matches exactly one thermostat. Empty, missing, or
ambiguous queries fail before device commands; cameras never participate.
"crosstown" matches one thermostat and "solar" matches Philly Solarium.
"Philly" and "living room" are ambiguous. "cabin" is a site alias for
`eco-site`, not a room substring.

**Disambiguation:** When the user says "living room" without context, ask which home. Use "philly living" for Cabin or "crosstown" for Crosstown. Never substitute the first match or a similarly named camera.

## Notes

- All temperatures are in **Fahrenheit**
- Always run `nest status` first to show the user current state before making changes
- When asked to change temperature, confirm the change was made by reporting the new setpoint
- The HVAC status shows whether the system is actively HEATING or OFF
- History snapshots are taken every 30 minutes automatically and stored in `~/.openclaw/nest-history/`
- Use `nest history` when the user asks about temperature trends, overnight temperatures, or how long the heat was running
- Weather data comes from Open-Meteo (no API key needed), fetched per-structure using `NEST_LOCATIONS` in `~/.openclaw/nest-location.conf`
- `nest weather` and `nest status` show weather for each structure (Philly + 19Crosstown)
- Snapshots store weather as `{"Philly": {...}, "19Crosstown": {...}}` — old single-location snapshots still render fine

## Troubleshooting

### "Error refreshing token: invalid_grant"
The Google OAuth refresh token has been revoked. Common causes:
1. **GCP OAuth consent screen in "Testing" mode** — tokens expire after 7 days. Fix: switch to "In production" at [Google Auth Platform > Audience](https://console.cloud.google.com/apis/credentials/consent)
2. **User revoked access** or **password change** — need to re-authorize

**Re-auth flow:**
1. In an attended terminal, run `openclaw-refresh-secrets --interactive` if the conventional protected credentials need refreshing.
2. Run `nest reauth` and open its generated consent URL. Keep all required devices selected. The URL carries a single-use state that expires after ten minutes.
3. Supply the **full callback URL**, including `code` and `state`, to `nest reauth --stdin` through a private pipe. Never put it in command arguments, shell history, chat, or logs. Bare codes and callbacks from a different or expired flow are rejected.
4. Verify inventory with `nest raw` without logging private device identifiers. New tokens are stored atomically in an owner-only, client/project-bound bundle. A failed exchange preserves the previous tokens; start a new flow before retrying.
5. Never print token responses or call `op` from a LaunchAgent. See [Nest OAuth operations](../../NEST-OAUTH.md) for hidden terminal input and separate agent enrollment.

**Staged agent migration:** `nest-agent` has a separate client, project, token
store, and camera policy. Missing configuration fails without conventional
credential fallback. After enrollment and cutover validation, update this skill's
agent examples to `nest-agent`; conventional collectors keep `nest`. Agent
authorization uses `nest-agent reauth` / `nest-agent reauth --stdin` and the
isolated `openclaw-refresh-secrets --nest-agent --interactive` workflow.
Do not switch a caller merely because the new binary is present.

### "Can't link to HomeAutomation"
The OAuth consent screen is blocking auth. Check:
- GCP project OAuth consent screen publishing status — must be "In production" (not "Testing")
- Your Google account must be listed as a test user if still in Testing mode
- Device Access project must exist at [console.nest.google.com/device-access](https://console.nest.google.com/device-access)

### Credentials unavailable in unattended execution
The `nest` CLI reads `NEST_CLIENT_ID`, `NEST_CLIENT_SECRET`,
`NEST_REFRESH_TOKEN`, and `NEST_PROJECT_ID` from the protected cache. If a key
is missing under launchd it fails closed without invoking `op`. An attended
terminal may use exact `OP_REF_NEST_*` environment references as a temporary
fallback; repair the durable cache with `openclaw-refresh-secrets --interactive`.
