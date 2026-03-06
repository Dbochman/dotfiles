# Mysa Thermostat OpenClaw Integration Plan

**Status: COMPLETE** (2026-03-06) — All steps implemented. 3 devices reporting. Data integrated into climate dashboard.

## Context

Dylan has BB-V1-1 (Mysa Baseboard V1) thermostats at Crosstown. Goal is read-only monitoring via OpenClaw, using the reverse-engineered [mysotherm](https://github.com/dlenski/mysotherm) library.

## Architecture

- **mysotherm** installed on Mac Mini via poetry (Python 3.13)
- Wrapper script outputs JSON (temp, humidity, setpoint, duty cycle, voltage)
- OpenClaw skill wraps the CLI for agent access
- Optional: add to health check cron + Nest dashboard

## Data Available from BB-V1-1

From REST API (`GET /devices/state`):
- `SensorTemp` — raw sensor temperature
- `CorrectedTemp` — calibrated ambient temperature
- `SetPoint` — target temperature
- `Humidity` — relative humidity %
- `Duty` — heater duty cycle (% of max current)
- `Current` — amperage draw
- `LineVoltage` — line voltage
- `HeatSink` — heat sink temperature
- `Rssi` — WiFi signal strength
- `Lock` — child lock status
- `Brightness` — display brightness

Auth: AWS Cognito (pycognito). Tokens cached at `~/.config/mysotherm`. Refresh token lasts ~30 days without use.

## Dependencies

- Python 3.13 (Homebrew, already on Mini)
- poetry (needs install if not present)
- boto3, pycognito, requests, pytz, websockets
- mqttpacket (custom fork: github.com/dlenski/mqttpacket@6984add) — needed even for read-only (import chain)

## Implementation Steps

### Step 1: Install mysotherm on Mini

```bash
# Check if poetry is installed
ssh dylans-mac-mini 'which poetry || pip3 install poetry'

# Clone and install
ssh dylans-mac-mini 'git clone https://github.com/dlenski/mysotherm ~/.openclaw/mysa/mysotherm'
ssh dylans-mac-mini 'cd ~/.openclaw/mysa/mysotherm && poetry install'
```

### Step 2: First-time auth (interactive)

Requires Dylan's Mysa app credentials. Must run interactively:

```bash
ssh -t dylans-mac-mini 'cd ~/.openclaw/mysa/mysotherm && poetry run mysotherm --no-watch'
# Will prompt for username + password
# Caches tokens at ~/.config/mysotherm
```

### Step 3: Create wrapper script

`~/.openclaw/bin/mysa-status.sh` — runs mysotherm in a way that outputs parseable JSON.

Since mysotherm outputs human-readable text (not JSON), the wrapper needs to either:
- (a) Call the REST API directly using cached Cognito tokens (simpler, no MQTT)
- (b) Parse mysotherm's text output

**Option (a) is better** — write a small Python script that imports mysotherm's auth module and calls the REST API directly, outputting JSON.

Script: `~/.openclaw/bin/mysa-status.py`
```python
#!/usr/bin/env python3
"""Read Mysa thermostat state and output JSON."""
# Uses mysotherm's auth module for Cognito token management
# Calls GET /devices/state directly
# Outputs: {"devices": [{"name": ..., "temp_f": ..., "humidity": ..., ...}]}
```

### Step 4: Create OpenClaw skill

`openclaw/skills/mysa-thermostat/SKILL.md`

Similar to cielo-ac skill:
- `mysa-status.py` for reading current state
- Device info: model, MAC, firmware, serial
- Temperature in both F and C
- Humidity, duty cycle, voltage

### Step 5: Test

```bash
# Verify wrapper outputs valid JSON
ssh dylans-mac-mini '~/.openclaw/bin/mysa-status.py' | python3 -m json.tool

# Verify skill works via OpenClaw
# (manual cron test or direct agent query)
```

### Step 6: Optional — Add to health check

Add Mysa state check to the existing health check cron job (`128c4ed0`), similar to Nest/Cielo/Hue checks.

### Step 7: Optional — Add to Nest dashboard

Add Mysa thermostat data to the Nest dashboard (port 8550) as an additional data source for Crosstown rooms.

## Key Risks

- **Token expiry**: If unused for ~30 days, refresh token expires and requires interactive re-auth
- **API breakage**: Reverse-engineered API, could change with Mysa app updates
- **Poetry in launchd**: Poetry venvs need correct PATH; may need to use the venv's Python directly

## File Inventory

| File | Location | Purpose |
|------|----------|---------|
| mysotherm repo | `~/.openclaw/mysa/mysotherm/` | Library (poetry-managed) |
| Wrapper script | `~/.openclaw/bin/mysa-status.py` | JSON output wrapper |
| Skill | `openclaw/skills/mysa-thermostat/SKILL.md` | OpenClaw skill definition |
| Auth cache | `~/.config/mysotherm` | Cognito tokens (INI format) |
