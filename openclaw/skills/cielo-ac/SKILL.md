---
name: cielo-ac
description: Control Mr Cool / Cielo Home minisplit AC units. Use when asked about AC, air conditioning, minisplit, heating, cooling, room temperature, thermostat (for minisplits specifically), turning heat on/off, setting temperature on a minisplit, fan speed, or swing position.
allowed-tools: Bash(cielo:*)
metadata: {"openclaw":{"emoji":"❄","requires":{"bins":["cielo"]}}}
---

# Cielo AC - Mr Cool Minisplit Control

Control Mr Cool minisplit AC units via the Cielo Home API. The `cielo` CLI is on PATH (wrapper at `~/.openclaw/bin/cielo`).

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
cielo status
```
Returns device name, online status, power, mode, temperature setpoint, fan speed, swing position, room temperature, and humidity for each unit.

### Check status of a specific unit
```bash
cielo status -d bedroom
```

### List all devices
```bash
cielo devices
```

### Turn on a unit
```bash
cielo on -d bedroom
```

### Turn off a unit
```bash
cielo off -d "living room"
```

### Turn on and set temperature
To reliably turn on a unit and set its temperature, use separate commands — **do NOT use `set --power on`** as it updates the API state but often fails to send the IR signal to the physical unit:
```bash
cielo on -d bedroom
cielo temp 72 -d bedroom
```

### Set temperature (Fahrenheit)
```bash
cielo temp 72 -d bedroom
```
If the unit is off, this will turn it on automatically.

### Set mode
```bash
cielo mode heat -d bedroom
cielo mode cool -d office
```
Valid modes: `cool`, `heat`, `auto`, `dry`, `fan`

### Set fan speed
```bash
cielo fan high -d bedroom
```
Valid speeds: `auto`, `low`, `medium`, `high`

### Set swing position
```bash
cielo swing auto -d bedroom
```
Valid positions: `auto`, `auto/stop`, `adjust`, `pos1`, `pos2`, `pos3`, `pos4`, `pos5`, `pos6`

### Set multiple values at once
```bash
cielo set -d bedroom --temp 68 --mode heat --fan low --swing auto
```
All flags are optional — only include what you want to change. Available flags: `--temp`, `--mode`, `--fan`, `--swing`, `--power`

**WARNING:** `set --power on` is unreliable — it updates the cloud state but often does not send the IR signal. Use the explicit `on` command instead, then `set` for other parameters.

### JSON output (for any status command)
```bash
cielo status --json
cielo status -d bedroom --json
cielo devices --json
```

## Token Management

Tokens expire approximately every hour. A LaunchAgent (`com.openclaw.cielo-refresh`) runs every 30 minutes. It first uses the stored refresh token, then falls back to CDP capture in an isolated PinchTab tab when the API refresh is unavailable.

### Automated refresh (default)
The LaunchAgent at `~/Library/LaunchAgents/com.openclaw.cielo-refresh.plist` uses the PinchTab 0.11 `default` profile at `~/.pinchtab/profiles/default/`. Browser fallback starts or reuses a managed headless instance, opens an isolated Cielo tab, captures a fresh token through Chrome DevTools Protocol, verifies it, and cleans up only the tab and instance it created. It refuses to navigate a visible PinchTab instance. Logs are at `~/.openclaw/logs/cielo-refresh.log`.

### If automated refresh fails (session expired)
If the persistent browser session expires, reCAPTCHA prevents a fully headless login. Wait until the Mac Mini is not being used for viewing, keep the Cielo LaunchAgent unloaded, and perform a one-time visible login:
```bash
INSTANCE_ID=$(pinchtab instance start --profile default --mode headed \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
pinchtab instance navigate "$INSTANCE_ID" "https://home.cielowigle.com/"
# Sign in manually in the visible browser and solve reCAPTCHA.
pinchtab instance stop "$INSTANCE_ID"
~/.openclaw/workspace/scripts/cielo-refresh.sh
```
The final command restarts the profile headlessly, captures and verifies a token, then stops its temporary browser instance. Do not enable repeated headless credential login after a CAPTCHA failure; it will not solve the challenge and may trigger rate limiting.

When an agent coordinates the visible login, it must start `grab-cielo-tokens.py --passive` before the form is submitted. Set `CIELO_CAPTURE_TIMEOUT_SECONDS=600` to keep that listener armed while the user completes reCAPTCHA. The login response contains the new refresh token; a capture started only after login can recover an access token but misses that refresh token.

### Manual token refresh (fallback)
```bash
cielo setup
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
