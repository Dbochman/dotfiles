---
name: nest-thermostat
description: Control Nest thermostats, check home climate status, view temperature history, and check outdoor weather. Use when asked about home temperature, thermostat settings, heating, cooling, room climate, temperature history or trends, or outdoor weather.
allowed-tools: Bash(nest-agent:*) Bash(nest weather:*) Bash(nest history:*)
metadata: {"openclaw":{"emoji":"T","requires":{"bins":["nest-agent","nest"]}}}
---

# Nest Thermostat Control

Control Google Nest thermostats via `nest-agent`, using the dedicated agent client, Device Access project, credentials, and token cache. Missing agent credentials fail without conventional fallback. 1Password access is attended and exact-field only. Local history and public weather remain available through `nest`.

## Available Commands

### Check status of all thermostats + weather
```bash
nest-agent status
```
Returns outdoor weather conditions plus room name, current temperature, setpoint, mode, HVAC status, and humidity for each thermostat.

### Check outdoor weather only
```bash
nest weather
```

### Set temperature (Fahrenheit)
```bash
nest-agent set <room> <temp>
```
Example: `nest-agent set bedroom 72`

### Change thermostat mode
```bash
nest-agent mode <room> <HEAT|OFF>
```
Example: `nest-agent mode solarium off`

### Toggle eco mode
```bash
nest-agent eco <room> on
nest-agent eco <room> off
```

### Toggle an entire site's thermostat group
```bash
nest-agent eco-site cabin on --dry-run
nest-agent eco-site cabin on
nest-agent eco-site crosstown off
```
The dry run is read-only. Site groups require the exact thermostats listed
below; missing, duplicated, or unexpected site thermostats fail before writes.
Every target must pass state preflight before any target is changed. Each
change requires Eco-mode readback; already-satisfied targets are skipped.
A partial failure returns nonzero without rolling back other targets.
Do not clear vacancy markers to retry HVAC: use only the approved site command
and check `nest-agent status` afterward. See
[vacancy recovery](../../VACANCY-AUTOMATION.md#thermostat-targeting-and-recovery).

### Conventional collection
The scheduled collector runs `nest snapshot` every 30 minutes. Agent requests
must not invoke conventional live-device commands or write history as a workaround
for missing agent access. Read existing history with the commands below.

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
nest-agent raw
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
- Always run `nest-agent status` first to show the user current state before making changes
- When asked to change temperature, confirm the change was made by reporting the new setpoint
- The HVAC status shows whether the system is actively HEATING or OFF
- History snapshots are taken every 30 minutes automatically and stored in `~/.openclaw/nest-history/`
- Use `nest history` when the user asks about temperature trends, overnight temperatures, or how long the heat was running
- Weather data comes from Open-Meteo (no API key needed), fetched per-structure using `NEST_LOCATIONS` in `~/.openclaw/nest-location.conf`
- `nest weather` and `nest-agent status` show weather for each structure (Philly + 19Crosstown)
- Snapshots store weather as `{"Philly": {...}, "19Crosstown": {...}}` — old single-location snapshots still render fine

## Troubleshooting

### "Error refreshing token: invalid_grant"
The Google OAuth refresh token has been revoked. Common causes:
1. **GCP OAuth consent screen in "Testing" mode** — tokens expire after 7 days. Fix: switch to "In production" at [Google Auth Platform > Audience](https://console.cloud.google.com/apis/credentials/consent)
2. **User revoked access** or **password change** — need to re-authorize

**Re-auth flow (attended):**
1. Use `openclaw-refresh-secrets --nest-agent --interactive` to refresh only the dedicated agent fields from 1Password.
2. Run `nest-agent reauth` and open its generated consent URL. Keep all required devices selected; state expires after ten minutes.
3. Pass the full callback URL to `nest-agent reauth --stdin` through a private pipe. Never put codes, callbacks, or secrets in command arguments, chat, history, or logs.
4. Verify inventory with `nest-agent raw` privately. A failed exchange preserves previous tokens; begin a new flow before retrying.
5. See [Nest OAuth operations](../../NEST-OAUTH.md) for the loopback callback, recovery references, and conventional-client maintenance. Never use conventional credentials to work around an agent failure.

### "Can't link to HomeAutomation"
The OAuth consent screen is blocking auth. Check:
- GCP project OAuth consent screen publishing status — must be "In production" (not "Testing")
- Your Google account must be listed as a test user if still in Testing mode
- Device Access project must exist at [console.nest.google.com/device-access](https://console.nest.google.com/device-access)

### Credentials unavailable in unattended execution
`nest-agent` reads its owner-only `~/.openclaw/nest-agent/credentials/client.json`
and identity-bound token bundle. It ignores conventional credential/cache
overrides and never calls `op` unattended. Repair only the agent context with
`openclaw-refresh-secrets --nest-agent --interactive`; preserve the conventional
client, event listener, history, and camera policy.
