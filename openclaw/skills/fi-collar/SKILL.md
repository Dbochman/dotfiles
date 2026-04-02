---
name: fi-collar
description: Check Potato's Fi collar GPS location, battery, and connection status. Use when asked where Potato is, collar battery level, or whether Potato is at Crosstown or the Cabin.
allowed-tools: Bash(fi-collar:*)
metadata: {"openclaw":{"emoji":"F","requires":{"bins":["fi-collar"]}}}
---

# Fi Collar — Potato's GPS Tracker

Check Potato's real-time GPS location, battery, and connection status via the Fi Series 3+ collar API.

## Commands

### Get current location
```bash
fi-collar location
```
Returns JSON with GPS coordinates, nearest known location (crosstown/cabin), distance, and whether Potato is within the home geofence.

### Get full status
```bash
fi-collar status
```
Returns JSON with location + battery level, connection type (Base/User/Cellular), activity type (Rest/Walk), and Fi base station status.

### Test login
```bash
fi-collar login
```

## Example Output

```json
{
  "name": "Potato",
  "activity": "Rest",
  "latitude": 42.602,
  "longitude": -72.151,
  "location": "cabin",
  "distance_m": 26,
  "at_location": true,
  "battery": 97,
  "connection": "User",
  "connectionDetail": "Dylan",
  "place": "Philly",
  "address": "95 School House Rd"
}
```

## Integration with Ring Listener

Fi GPS is polled every 60s during dog walk return monitoring. When Potato re-enters the home geofence, Roombas are docked automatically. This works at both Crosstown and Cabin locations.

## Auth

- Account: `dylanbochman@gmail.com` (password in `TRYFI_PASSWORD` env var)
- Session cached at `~/.config/fi-collar/session.json` (12hr TTL, auto-re-login on 401)
- Credentials: `TRYFI_EMAIL` + `TRYFI_PASSWORD` in `~/.openclaw/.secrets-cache`

## Collar Details

- **Dog**: Potato
- **Collar**: Fi Series 3+, Serial FC35G072187
- **Pet ID**: 4WbrzFllED1YxCLqdT5SC4
- **Base station**: "Crosstown" at Crosstown Ave (always online, WiFi connected)
- **GPS**: GNSS (GPS + GLONASS + Galileo), updates every ~7 min at rest, more frequent during walks
- **Cellular**: Built-in LTE for GPS reporting when away from WiFi/base

## Geofence

Home locations loaded from env vars (`CROSSTOWN_LAT/LON`, `CABIN_LAT/LON` in secrets-cache):
- Crosstown: 150m radius
- Cabin: 300m radius (larger due to rural setting)
