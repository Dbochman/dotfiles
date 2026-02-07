---
name: nest-thermostat
description: Control Nest thermostats and check home climate status. Use when asked about home temperature, thermostat settings, heating, cooling, or room climate control.
allowed-tools: Bash(nest:*)
metadata: {"openclaw":{"emoji":"T","requires":{"bins":["nest"]}}}
---

# Nest Thermostat Control

Control Google Nest thermostats via the `nest` CLI. All credentials are managed via 1Password.

## Available Commands

### Check status of all thermostats
```bash
nest status
```
Returns room name, current temperature, setpoint, mode, HVAC status, and humidity for each thermostat.

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

### Raw JSON dump (for debugging)
```bash
nest raw
```

## Rooms

There are 3 thermostats:
- **Solarium** (matches: solar, sol)
- **Living Room** (matches: living, liv)
- **Bedroom** (matches: bed, bedroom)

Room names are fuzzy-matched — use any substring.

## Notes

- All temperatures are in **Fahrenheit**
- Always run `nest status` first to show the user current state before making changes
- When asked to change temperature, confirm the change was made by reporting the new setpoint
- The HVAC status shows whether the system is actively HEATING or OFF
