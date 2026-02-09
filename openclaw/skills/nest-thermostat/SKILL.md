---
name: nest-thermostat
description: Control Nest thermostats, check home climate status, view temperature history, check outdoor weather, and capture camera snapshots. Use when asked about home temperature, thermostat settings, heating, cooling, room climate, temperature history/trends, outdoor weather, or camera/what does the kitchen look like.
allowed-tools: Bash(nest:*)
metadata: {"openclaw":{"emoji":"T","requires":{"bins":["nest"]}}}
---

# Nest Thermostat & Camera Control

Control Google Nest thermostats and cameras via the `nest` CLI. All credentials are managed via 1Password.

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

### Capture a camera snapshot
```bash
nest camera snap [room] [output_path]
```
Examples:
- `nest camera snap` — Kitchen camera, saves to `~/.openclaw/workspace/camera-snap.jpg`
- `nest camera snap kitchen /tmp/snap.jpg` — Kitchen camera, custom output path

The output path is printed to stdout. The image can be viewed by the agent (Claude is multimodal).
**Note:** Camera must be online and streaming enabled. Takes ~5-10 seconds.

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
- **Kitchen** camera (matches: kitchen, kit)

### Crosstown (Boston — 19 Crosstown Ave)
- **19Crosstown Living Room** (matches: crosstown)
- **Cat room** cameras x2

Room names are fuzzy-matched — use any substring. "crosstown" matches the Crosstown thermostat, "solar" matches Philly Solarium, etc.

**Disambiguation:** When the user says "living room" without context, it's ambiguous — ask which home. Use "philly living" for Cabin or "crosstown" for Crosstown. Unique rooms (solarium, cat room) are unambiguous.

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
- Camera snapshots use WebRTC via the SDM API. Requires `aiortc` and `Pillow` Python packages.
- When asked "what does the kitchen look like?" or similar, use `nest camera snap` and then view the image
