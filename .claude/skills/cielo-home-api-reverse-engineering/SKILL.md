---
name: cielo-home-api-reverse-engineering
description: |
  Cielo Home / Mr Cool minisplit smart AC API details and CLI tool. Use when:
  (1) Controlling Mr Cool or Cielo Home minisplit AC units programmatically,
  (2) Building integrations with Cielo Home API (api.smartcielo.com),
  (3) Token expired and need to refresh credentials for cielo-cli,
  (4) Need to understand Cielo Home WebSocket command format for device control.
  Covers API endpoints, authentication flow, HAR-based token extraction, and
  the Chrome CORS header stripping workaround.
author: Claude Code
version: 1.0.0
date: 2026-02-08
---

# Cielo Home / Mr Cool API Reverse Engineering

## Problem
Cielo Home (home.cielowigle.com) provides a web app for controlling Mr Cool minisplit
AC units, but has no public API documentation. Building programmatic control requires
reverse-engineering the API from the obfuscated Angular frontend and the Home Assistant
community integration.

## Context / Trigger Conditions
- User wants to control Cielo Home / Mr Cool minisplit units from CLI or automation
- Need to integrate with OpenClaw or other home automation systems
- Token expired on cielo-cli and needs refreshing
- Building new features for the cielo-cli tool

## Key Findings

### Authentication
- **No programmatic login**: The login endpoint at `POST /auth/login` requires a
  reCAPTCHA token. Neither the HA integration nor node-smartcielo perform programmatic login.
- **Token-based auth**: All API calls use a JWT access token in the `authorization` header
  and an API key in `x-api-key`.
- **API Key**: Stored in `$CIELO_API_KEY` env var (extracted from live browser,
  differs from the obfuscated JS value in the web app).
- **Token refresh**: `POST /web/token/refresh` with body `{"local":"en","refreshToken":"..."}`.
  Requires `authorization` header with current access token.
- **Token expiry**: Tokens expire quickly (~1 hour). The browser's auto-refresh can
  invalidate tokens extracted for CLI use.

### API Endpoints
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `https://api.smartcielo.com/web/devices?limit=420` | GET | List all devices |
| `https://api.smartcielo.com/web/sync/db/6?applianceIdList=[ids]` | GET | Appliance details |
| `https://api.smartcielo.com/web/token/refresh` | POST | Refresh access token |
| `wss://apiwss.smartcielo.com/websocket/?sessionId=...&token=...` | WS | Real-time control |

### WebSocket Command Format
Commands are sent as JSON over WebSocket. Two main action types:
- `actionControl`: Change a single setting (power, temp, mode, fan, swing)
- `syncState`: Set multiple values at once

Required fields: `action`, `actionSource` ("WEB"), `macAddress`, `user_id`, `fw_version`,
`deviceTypeVersion`, `mid` ("WEB"), `connection_source`, `applianceId`, `applianceType`,
`actions` (object with power/mode/temp/fanspeed/swing/turbo/light/followme),
`oldPower`, and for actionControl: `actionType` + `actionValue`.

### Token Extraction via HAR Files
**Critical discovery**: Chrome HAR exports strip `authorization` headers from CORS
requests (cross-origin to api.smartcielo.com). However, **WebSocket URLs retain the
token as a query parameter** (`?sessionId=...&token=...`). This is the most reliable
way to extract tokens from a HAR file.

### Token Extraction Workflow
1. Log into home.cielowigle.com in Chrome
2. Open DevTools > Network tab
3. Right-click > "Save all as HAR with content"
4. Parse HAR file, find WebSocket entry to `apiwss.smartcielo.com`
5. Extract `token` and `sessionId` from the URL query parameters

## CLI Tool Location
`/Users/dylanbochman/repos/cielo-cli/`
- Config: `~/.config/cielo/config.json`
- Commands: `devices`, `status`, `on`, `off`, `temp`, `mode`, `fan`, `swing`, `set`, `load-har`

## User Details
- userId: `Do9ehqFdb6`
- 4 devices: Basement, Living Room, Dylan's Office, Bedroom
- All BREEZ-PLUS type, Fahrenheit

## References
- HA Integration: https://github.com/bodyscape/cielo_home
- node-smartcielo (old API): https://github.com/nicholasrobinson/node-smartcielo
- Cielo Home web app: https://home.cielowigle.com/

## Notes
- The old API at `home.cielowigle.com` used SignalR WebSocket and session cookies (node-smartcielo approach). The new API uses REST + standard WebSocket.
- Device `connectionSource` field matters for commands - use the value from the device object.
- The HA integration uses `application_version: "1.4.4"` in all messages.
- `isFaren: 1` means the device uses Fahrenheit.
