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

### JSON output (for any status command)
```bash
/usr/local/bin/node ~/repos/cielo-cli/cli.js status --json
/usr/local/bin/node ~/repos/cielo-cli/cli.js status -d bedroom --json
/usr/local/bin/node ~/repos/cielo-cli/cli.js devices --json
```

## Token Management

Tokens expire approximately every hour. When a command fails with "Token expired", the tokens need to be refreshed.

### Refresh via HAR file
1. On a computer with Chrome, go to https://home.cielowigle.com/ and log in
2. Open DevTools (F12) > Network tab
3. Right-click in the network list > "Save all as HAR with content"
4. Transfer the HAR file to the Mac Mini
5. Run:
```bash
/usr/local/bin/node ~/repos/cielo-cli/cli.js load-har /path/to/file.har
```

### Manual token refresh
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
- Room temperature and humidity are from the Cielo Breez sensor, not the minisplit itself
