---
name: cielo-ac
description: Control Mr Cool / Cielo Home minisplit AC units. Use when asked about AC, air conditioning, minisplit, heating, cooling, room temperature, thermostat (for minisplits specifically), turning heat on/off, setting temperature on a minisplit, fan speed, or swing position.
allowed-tools: Bash(cielo:*)
metadata: {"openclaw":{"emoji":"❄"}}
---

# Cielo AC - Mr Cool Minisplit Control

Control Mr Cool minisplit AC units via the Cielo Home API. The `cielo` CLI lives at `~/repos/cielo-cli/cli.js`.

## Available Devices

| Name | MAC | Location |
|------|-----|----------|
| Basement | B48A0ACEC6CC | Basement |
| Living Room | B48A0ACEC8B4 | Living room |
| Dylan's Office | B48A0AC4612D | Office |
| Bedroom | B48A0ACEC639 | Bedroom |

Device names are fuzzy-matched — use any substring. "bedroom" matches Bedroom, "office" matches Dylan's Office, "living" matches Living Room, etc.

## Available Commands

### Check status of all units
```bash
/usr/local/bin/node ~/repos/cielo-cli/cli.js status
```
Returns device name, online status, power, mode, temperature setpoint, fan speed, swing position, room temperature, and humidity for each unit.

### Check status of a specific unit
```bash
/usr/local/bin/node ~/repos/cielo-cli/cli.js status -d bedroom
```

### List all devices
```bash
/usr/local/bin/node ~/repos/cielo-cli/cli.js devices
```

### Turn on a unit
```bash
/usr/local/bin/node ~/repos/cielo-cli/cli.js on -d bedroom
```

### Turn off a unit
```bash
/usr/local/bin/node ~/repos/cielo-cli/cli.js off -d "living room"
```

### Turn on and set temperature
To reliably turn on a unit and set its temperature, use separate commands — **do NOT use `set --power on`** as it updates the API state but often fails to send the IR signal to the physical unit:
```bash
/usr/local/bin/node ~/repos/cielo-cli/cli.js on -d bedroom
/usr/local/bin/node ~/repos/cielo-cli/cli.js temp 72 -d bedroom
```

### Set temperature (Fahrenheit)
```bash
/usr/local/bin/node ~/repos/cielo-cli/cli.js temp 72 -d bedroom
```
If the unit is off, this will turn it on automatically.

### Set mode
```bash
/usr/local/bin/node ~/repos/cielo-cli/cli.js mode heat -d bedroom
/usr/local/bin/node ~/repos/cielo-cli/cli.js mode cool -d office
```
Valid modes: `cool`, `heat`, `auto`, `dry`, `fan`

### Set fan speed
```bash
/usr/local/bin/node ~/repos/cielo-cli/cli.js fan high -d bedroom
```
Valid speeds: `auto`, `low`, `medium`, `high`

### Set swing position
```bash
/usr/local/bin/node ~/repos/cielo-cli/cli.js swing auto -d bedroom
```
Valid positions: `auto`, `auto/stop`, `adjust`, `pos1`, `pos2`, `pos3`, `pos4`, `pos5`, `pos6`

### Set multiple values at once
```bash
/usr/local/bin/node ~/repos/cielo-cli/cli.js set -d bedroom --temp 68 --mode heat --fan low --swing auto
```
All flags are optional — only include what you want to change. Available flags: `--temp`, `--mode`, `--fan`, `--swing`, `--power`

**WARNING:** `set --power on` is unreliable — it updates the cloud state but often does not send the IR signal. Use the explicit `on` command instead, then `set` for other parameters.

### JSON output (for any status command)
```bash
/usr/local/bin/node ~/repos/cielo-cli/cli.js status --json
/usr/local/bin/node ~/repos/cielo-cli/cli.js status -d bedroom --json
/usr/local/bin/node ~/repos/cielo-cli/cli.js devices --json
```

## Token Management

Tokens expire approximately every hour. A LaunchAgent (`com.openclaw.cielo-refresh`) runs every 30 minutes to automatically refresh tokens via CDP browser capture. This should keep tokens fresh indefinitely as long as the Cielo session cookies haven't expired.

### Automated refresh (default)
The LaunchAgent at `~/Library/LaunchAgents/com.openclaw.cielo-refresh.plist` starts pinchtab, auto-logs in via persisted cookies in `~/.pinchtab/chrome-profile/`, captures a fresh token via Chrome DevTools Protocol, and verifies it works. Logs at `/tmp/cielo-refresh.log`.

### If automated refresh fails (session expired)
Cookies persist for weeks/months. If they expire, a one-time manual re-login is needed:
```bash
BRIDGE_HEADLESS=false pinchtab &
sleep 5
pinchtab nav "https://home.cielowigle.com/"
# Sign in manually in the visible browser window, solve CAPTCHA
# Then kill pinchtab — cookies are now persisted
pkill -f pinchtab
```

### Manual token refresh (fallback)
```bash
/usr/local/bin/node ~/repos/cielo-cli/cli.js setup
```
Then paste the accessToken and sessionId from browser DevTools.

## Disambiguation

These are **minisplit AC units**, not the Nest thermostats. When the user asks about:
- "Temperature" or "thermostat" — ask whether they mean the minisplits (Cielo) or the Nest thermostats
- "AC", "air conditioning", "minisplit", "Mr Cool" — always use this skill
- "Heat" — could be either; ask if ambiguous, but if they mention a room that only has a minisplit, use this skill

The Nest thermostats control central HVAC. The Cielo minisplits are supplemental heating/cooling in specific rooms.

## Notes

- All temperatures are in **Fahrenheit**
- Always run `status` first to show the user current state before making changes
- When asked to change temperature, confirm the change was made by showing the acknowledgment
- If the command returns "command sent (no ack received)", the command was sent but the device didn't confirm — it usually still works
- If a command returns "is already on/off", the device is already in the requested state
- The `set` command with `--temp` will automatically turn the unit on if it's off
- **`set --power on` is unreliable** — use explicit `on` command then `temp`/`mode`/etc. separately
- To turn on and configure: `on -d <name>` → `temp <F> -d <name>` → `mode <mode> -d <name>`
- Room temperature and humidity are from the Cielo Breez sensor, not the minisplit itself
