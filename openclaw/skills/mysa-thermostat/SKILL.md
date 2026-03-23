---
name: mysa-thermostat
description: Read Mysa baseboard heater thermostat status at Crosstown. Use when asked about baseboard heaters, electric heat, Mysa thermostats, or room temperature at Crosstown (for baseboard heaters specifically).
allowed-tools: Bash(mysa:*)
metadata: {"openclaw":{"emoji":"🔥","requires":{"bins":["mysa"]}}}
---

# Mysa Thermostat - Baseboard Heater Monitoring

Read-only monitoring of Mysa BB-V1-1 baseboard heater thermostats at Crosstown via the Mysa REST API.

## Check Status

```bash
mysa
```

Returns JSON with all devices:
```json
{
  "devices": [
    {
      "name": "Bedroom",
      "model": "BB-V1-1",
      "mac": "AA:BB:CC:DD:EE:FF",
      "temp_f": 68.5,
      "temp_c": 20.3,
      "setpoint_f": 70.0,
      "setpoint_c": 21.1,
      "humidity": 45,
      "duty_pct": 50,
      "current_a": 8.5,
      "line_voltage": 240,
      "rssi_dbm": -55,
      "firmware": "3.16.2.3"
    }
  ]
}
```

## Fields

| Field | Description |
|-------|-------------|
| `temp_f` / `temp_c` | Calibrated ambient temperature |
| `sensor_temp_f` / `sensor_temp_c` | Raw sensor temperature (before calibration) |
| `setpoint_f` / `setpoint_c` | Target temperature |
| `humidity` | Relative humidity % |
| `duty_pct` | Heater duty cycle (% of max current draw) |
| `current_a` | Amperage draw |
| `line_voltage` | Line voltage (V) |
| `heatsink_f` / `heatsink_c` | Heat sink temperature |
| `rssi_dbm` | WiFi signal strength (dBm) |
| `brightness_pct` | Display brightness % |
| `lock` | Child lock status |

## Limitations

- **Read-only** — this skill cannot change setpoints or turn heaters on/off
- Temperature changes must be made via the Mysa app or the physical thermostat
- Auth tokens expire after ~30 days of inactivity; if the script returns an auth error, Dylan needs to re-authenticate interactively on the Mini

## Disambiguation

These are **baseboard electric heaters** controlled by Mysa smart thermostats. When the user asks about:
- "Baseboard heater", "electric heat", "Mysa" — use this skill
- "Thermostat" — could be Nest (central HVAC), Mysa (baseboard), or Cielo (minisplit); ask if ambiguous
- "Temperature" at Crosstown — could be any of the three systems; ask which one, or check all three

The Nest thermostats control central HVAC. The Cielo minisplits are supplemental AC/heat. The Mysa thermostats control baseboard electric heaters.

## Notes

- Temperatures are provided in both Fahrenheit and Celsius
- Duty cycle indicates how hard the heater is working (0% = off, 100% = max)
- The API is reverse-engineered from the Mysa app — may break with app updates
- Auth is AWS Cognito; tokens cached at `~/.config/mysotherm`
