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

## Rooms

There are 3 thermostats:
- **Solarium** (matches: solar, sol)
- **Living Room** (matches: living, liv)
- **Bedroom** (matches: bed, bedroom)

There is 1 camera:
- **Kitchen** (matches: kitchen, kit)

Room names are fuzzy-matched — use any substring.

## Notes

- All temperatures are in **Fahrenheit**
- Always run `nest status` first to show the user current state before making changes
- When asked to change temperature, confirm the change was made by reporting the new setpoint
- The HVAC status shows whether the system is actively HEATING or OFF
- History snapshots are taken every 30 minutes automatically and stored in `~/.openclaw/nest-history/`
- Use `nest history` when the user asks about temperature trends, overnight temperatures, or how long the heat was running
- Weather data comes from Open-Meteo (no API key needed). Location config: `~/.openclaw/nest-location.conf`
- Camera snapshots use WebRTC via the SDM API. Requires `aiortc` and `Pillow` Python packages.
- When asked "what does the kitchen look like?" or similar, use `nest camera snap` and then view the image
