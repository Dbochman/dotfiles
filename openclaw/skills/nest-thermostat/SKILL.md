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

## Troubleshooting

### "Error refreshing token: invalid_grant"
The Google OAuth refresh token has been revoked. Common causes:
1. **GCP OAuth consent screen in "Testing" mode** — tokens expire after 7 days. Fix: switch to "In production" at [Google Auth Platform > Audience](https://console.cloud.google.com/apis/credentials/consent)
2. **User revoked access** or **password change** — need to re-authorize

**Re-auth flow:**
1. Get credentials from 1Password (vault "OpenClaw", item "Google Nest"): `clientID`, `client_secret`, `project_id`
2. Open auth URL:
   ```
   https://nestservices.google.com/partnerconnections/<PROJECT_ID>/auth?redirect_uri=https://www.google.com&access_type=offline&prompt=consent&client_id=<CLIENT_ID>&response_type=code&scope=https://www.googleapis.com/auth/sdm.service
   ```
3. Authorize and copy the `code=` parameter from the redirect URL
4. Exchange the code:
   ```bash
   curl -s -X POST "https://www.googleapis.com/oauth2/v4/token" \
     -d "client_id=<CLIENT_ID>" \
     -d "client_secret=<CLIENT_SECRET>" \
     -d "code=<AUTH_CODE>" \
     -d "grant_type=authorization_code" \
     -d "redirect_uri=https://www.google.com"
   ```
5. Update `refresh_token` in 1Password
6. Clear cache on Mac Mini: `rm -rf ~/.cache/nest-sdm/`
7. Write new credentials to cache (if 1Password biometric is unavailable via SSH):
   ```bash
   mkdir -p ~/.cache/nest-sdm
   echo -n '<REFRESH_TOKEN>' > ~/.cache/nest-sdm/refresh_token
   echo -n '<CLIENT_ID>' > ~/.cache/nest-sdm/clientid
   echo -n '<CLIENT_SECRET>' > ~/.cache/nest-sdm/client_secret
   echo -n '<PROJECT_ID>' > ~/.cache/nest-sdm/project_id
   ```

### "Can't link to HomeAutomation"
The OAuth consent screen is blocking auth. Check:
- GCP project OAuth consent screen publishing status — must be "In production" (not "Testing")
- Your Google account must be listed as a test user if still in Testing mode
- Device Access project must exist at [console.nest.google.com/device-access](https://console.nest.google.com/device-access)

### 1Password unreachable via SSH
Mac Mini 1Password requires biometric unlock which can't be triggered over SSH. Workaround: write credentials directly to `~/.cache/nest-sdm/` cache files (see re-auth flow above). The `nest` CLI reads from cache first before hitting 1Password.
