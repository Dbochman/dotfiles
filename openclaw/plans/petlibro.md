# Petlibro — OpenClaw Skill Implementation Plan

## Context

Dylan and Julia have Petlibro smart pet devices at both locations:

| Location | Feeder | Fountain | Status |
|----------|--------|----------|--------|
| Crosstown | Yes | Yes | Active |
| Cabin | Yes | Yes | Unplugged (seasonal) |

The goal is to create an OpenClaw skill for monitoring and controlling these devices via the Petlibro cloud API.

## Architecture

```
Petlibro Devices ←─cloud─→ Petlibro API (api.us.petlibro.com) ←─HTTPS─→ petlibro-api.py (Mac Mini)
```

Cloud-only — runs directly on Mac Mini. Same pattern as 8sleep.

## Key Technical Details

### API (reverse-engineered from HA integration)

- **Base URL**: `https://api.us.petlibro.com`
- **Auth**: POST `/member/auth/login` with email + MD5-hashed password
- **Password hashing**: `md5(password.encode("UTF-8")).hexdigest()`
- **App constants**: `appId: 1`, `appSn: "c35772530d1041699c87fe62348507a8"`
- **Headers**: `source: ANDROID`, `language: EN`, `version: 1.3.45`, `token: <session_token>`
- **Token refresh**: Re-login on error code `1009` (NOT_YET_LOGIN)
- **Single session limit**: Only one session per account. Phone app can invalidate. Recommended: create second account, share devices.

### Key Endpoints

| Endpoint | Purpose |
|----------|---------|
| `/member/auth/login` | Authenticate |
| `/device/device/list` | List all devices |
| `/device/device/realInfo` | Real-time device status |
| `/data/data/realInfo` | Data layer status |
| `/device/data/grainStatus` | Food hopper level |
| `/device/device/manualFeeding` | Trigger manual feed |
| `/device/feedingPlan/todayNew` | Today's feeding schedule |
| `/device/feedingPlan/list` | All feeding plans |
| `/data/deviceDrinkWater/todayDrinkData` | Today's water intake |
| `/device/device/filterReset` | Reset filter counter |
| `/device/device/waterModeSetting` | Set fountain mode |
| `/device/setting/updateLightSwitch` | Toggle light |
| `/device/setting/updateSoundSwitch` | Toggle sound |
| `/device/setting/updateChildLockSwitch` | Toggle child lock |

---

## Phase 0: Auth Proof

### 0.1 Credential setup

Credentials at `~/.config/petlibro/config.yaml` on Mini:

```yaml
email: <petlibro-email>
password: <petlibro-password>
region: US
timezone: America/New_York
```

**Important**: Consider creating a **second Petlibro account** and sharing devices to it, to avoid the phone app invalidating the API session. If not, the skill and phone app will compete for the single session.

### 0.2 Test auth

```python
import hashlib, urllib.request, json
md5_pass = hashlib.md5(password.encode("UTF-8")).hexdigest()
# POST to /member/auth/login with appId, appSn, email, password, country, timezone
```

### 0.3 List devices

After auth, POST to `/device/device/list` to discover all devices — get device IDs, product names, and model numbers.

---

## Phase 1: Python API Wrapper (petlibro-api.py)

Custom Python script (same pattern as 8sleep-api.py). No external dependencies — just `urllib`, `hashlib`, `json`.

### MVP Commands

| Command | API Call | Description |
|---------|----------|-------------|
| `status` | `realInfo` for all devices | All devices: online, battery, food level, water level |
| `feed <device> [portions]` | `manualFeeding` | Trigger manual feed (default: 1 portion) |
| `water <device>` | `todayDrinkData` | Today's water intake |
| `schedule <device>` | `feedingPlan/todayNew` | Today's feeding schedule |
| `devices` | `device/list` | List all devices with IDs |
| `device <id>` | `realInfo` | Detailed single device status |

### Deferred to v2
- Feeding plan management (create/update/delete schedules)
- Light/sound/child lock toggles
- Filter/desiccant reset
- Fountain water mode changes
- Device event history

---

## Phase 2: CLI Wrapper + SKILL.md

### Files

| File | Purpose |
|------|---------|
| `openclaw/skills/petlibro/SKILL.md` | Skill definition |
| `openclaw/skills/petlibro/petlibro-api.py` | API wrapper |
| `openclaw/skills/petlibro/petlibro` | CLI wrapper (bash) |
| `openclaw/bin/petlibro` | PATH wrapper |

### CLI commands

```
petlibro status              # All devices summary
petlibro feed <name> [N]     # Feed N portions (default 1)
petlibro water <name>        # Today's water intake
petlibro schedule <name>     # Today's feeding schedule
petlibro devices             # List all devices
```

Device names fuzzy-matched (same pattern as crosstown-roomba).

---

## Phase 3: Integration

- Add to `crosstown-routines` and `cabin-routines` if needed
- Add devices to `crosstown-network` known devices table
- Consider: vacancy-actions could check food/water levels and alert

---

## Caveats

- **Cloud-only**: No local API. Internet required.
- **Single session**: Phone app and API compete. Consider second account.
- **Unofficial API**: Reverse-engineered from HA integration. Can break.
- **MD5 password**: Weak hash but required by the API.
- **US region only**: API URL hardcoded to `api.us.petlibro.com`.
- **Cabin devices unplugged**: Will show offline until reconnected.

---

## Status

- [ ] Phase 0: Auth proof (test login + device list)
- [ ] Phase 1: Python API wrapper
- [ ] Phase 2: CLI wrapper + SKILL.md
- [ ] Phase 3: Integration
