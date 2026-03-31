# 8sleep Pod — OpenClaw Skill Implementation Plan

## Context

Dylan and Julia have an Eight Sleep Pod 3 (King) at Crosstown (West Roxbury). This skill controls the pod via the Eight Sleep cloud API using a custom Python wrapper (`8sleep-api.py`).

## Architecture (Final)

```
Pod 3 ←─cloud─→ Eight Sleep API ←─HTTPS─→ 8sleep-api.py (Mac Mini)
```

Direct cloud API calls from the Mini. No local network API exists. Auth via Dylan's account — household access covers both sides.

### Why not eightctl?

`eightctl` (Go CLI) was the original plan but has a bug: it sends `client_id: "sleep-client"` instead of the proper app credentials (from APK), causing the token auth endpoint to reject requests. It falls back to a legacy login endpoint which also has issues with Google Sign-In accounts. Built a custom Python wrapper using direct API calls instead.

### Auth discovery

The 8sleep account was created via **Google Sign-In** — the email+password API auth didn't work until a password was explicitly set via the **"Forgot Password"** email reset flow (not "Set Password" in app settings). The reset flow registers the account with the password auth system.

## Pod Details

| Field | Value |
|-------|-------|
| Model | Pod 3 |
| Size | King |
| Serial | 00027826 |
| HW Revision | H40 |
| Device ID | `69e00d7434b871d53075eccecf1c4aa283a0bf67` |

### Sides

| Side | Position | User ID | Temp Preference |
|------|----------|---------|-----------------|
| Dylan | Left | `$EIGHTSLEEP_DYLAN_USER_ID` | Cool |
| Julia | Right | `$EIGHTSLEEP_JULIA_USER_ID` | Warm |

Both sides are controllable via Dylan's auth token (household access).

## Components

| File | Location | Purpose |
|------|----------|---------|
| `openclaw/skills/8sleep/SKILL.md` | Dotfiles repo | Skill definition |
| `openclaw/skills/8sleep/8sleep` | Dotfiles repo | CLI wrapper (bash) |
| `openclaw/skills/8sleep/8sleep-api.py` | Dotfiles repo | Eight Sleep API wrapper (Python) |
| `~/.config/eightctl/config.yaml` | Mac Mini only | Credentials (email, password, timezone) |
| `~/.config/eightctl/token-cache.json` | Mac Mini only | Cached auth token |

## API Details

### Authentication
- **Endpoint**: `POST https://auth-api.8slp.net/v1/tokens`
- **Client ID**: `$EIGHTSLEEP_CLIENT_ID` (from .secrets-cache, originally extracted from 8sleep Android APK)
- **Client Secret**: `$EIGHTSLEEP_CLIENT_SECRET` (from .secrets-cache)
- **User-Agent**: `okhttp/4.9.3` (required — API rejects default Python UA)
- **Token caching**: File-based at `~/.config/eightctl/token-cache.json` (chmod 600)
- **Rate limiting**: Aggressive HTTP 429 on repeated auth failures. 5-10 min cooldown.

### Endpoints Used
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `users/{uid}/current-device` | GET | Get device ID and side assignment |
| `devices/{devId}` | GET | Device status, both sides' temps, water, connectivity |
| `users/{uid}/temperature` | GET/PUT | Temperature level and smart schedule |
| `users/{uid}/trends?tz=&from=&to=` | GET | Sleep data (score, duration, stages, snoring) |

## CLI Commands

| Command | Description |
|---------|-------------|
| `8sleep status` | Both sides: temp level, heating state, water, device connectivity |
| `8sleep temp <dylan\|julia> <level>` | Set temperature (-100 to +100) for specific side |
| `8sleep sleep <dylan\|julia> [date]` | Sleep data (score, duration, REM%, deep%, snoring) |
| `8sleep device` | Device info (model, serial, water, priming, connectivity) |
| `8sleep raw <path>` | Raw API GET for debugging |

## Temperature Scale

| Level | Temp | Feeling |
|-------|------|---------|
| -100 | ~55°F | Very cold |
| -50 | ~70°F | Cool |
| 0 | ~81°F | Neutral |
| +50 | ~97°F | Warm |
| +100 | ~111°F | Very hot |

## Status

- [x] Phase 0: Auth proof (Google Sign-In → password reset → API auth works)
- [x] Phase 0: eightctl bug discovered, pivoted to custom Python wrapper
- [x] Phase 1: Python API wrapper (8sleep-api.py) — status, temp, sleep, device
- [x] Phase 1: Both-side support (Dylan + Julia via single auth)
- [x] Phase 1: CLI wrapper (8sleep) with human-readable output
- [x] Phase 1: SKILL.md with side disambiguation guidance
- [x] Deploy to Mac Mini
- [ ] Phase 2: Add to crosstown-routines (Goodnight: set bedtime temp)
- [ ] Phase 2: Add Pod to crosstown-network known devices

## Caveats

- **Cloud-only**: No local API. Requires internet.
- **Unofficial API**: Reverse-engineered from mobile app. Can break at any time.
- **Rate limiting**: HTTP 429 on repeated auth. Token cached to avoid re-auth.
- **Google Sign-In accounts**: Must use "Forgot Password" reset flow to enable API auth. Setting password in app settings is not sufficient.
- **User-Agent required**: API rejects requests without `okhttp/4.9.3` user-agent header.
- **eightctl broken**: v0.1.0-dev sends wrong client_id. Custom wrapper used instead.
