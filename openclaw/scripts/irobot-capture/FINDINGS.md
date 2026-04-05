# iRobot Cloud API Reverse Engineering — Findings

**Date**: 2026-04-03
**Goal**: Discover additional iRobot cloud API endpoints beyond mission history, especially real-time robot state (shadow) and command capabilities for Cabin Roombas (Floomba, Philly) which lack local MQTT access.

## Background

Cabin Roombas (SKU Y354020, firmware v4) have `user_cert: false` and no local MQTT (port 8883 closed). We can only interact via the cloud. The existing `irobot-cloud.py` uses Gigya login → iRobot v2 login → AWS SigV4 signed requests to `auth1.prod.iot.irobotapi.com` for mission history. The goal was to unlock real-time shadow state and command execution.

## Discovery: API Endpoints

### Method: mitmproxy SNI passthrough

Since the iRobot iOS app **pins TLS certificates on ALL domains** (including content-prod), we used a mitmproxy addon that logs TLS Client Hello SNI hostnames without intercepting, allowing the app to function normally.

### Endpoints Contacted by iRobot iOS App

| Host | Port | Purpose |
|------|------|---------|
| `accounts.us1.gigya.com` | 443 | Gigya (SAP) authentication |
| `disc-prod.iot.irobotapi.com` | 443 | API discovery — returns deployment tiers, endpoints, keys |
| `unauth3.prod.iot.irobotapi.com` | 443 | Unauthenticated API (v011 deployment) — login endpoint |
| `auth3.prod.iot.irobotapi.com` | 443 | Authenticated API (v011 deployment) — mission history, pmaps |
| `content-prod.iot.irobotapi.com` | 443 | Content delivery — maps, images (different auth scheme) |
| `ecomm.prod.user-services.irobotapi.com` | 443 | E-commerce/user profile service |
| `certificatefactory.prod.security.irobotapi.com` | 443 | Client certificate issuance (for `user_cert: true` robots) |
| `firebaselogging-pa.googleapis.com` | 443 | Firebase analytics |
| `crashlyticsreports-pa.googleapis.com` | 443 | Crashlytics |
| `api.mixpanel.com` | 443 | Mixpanel analytics |
| `firebaseremoteconfigrealtime.googleapis.com` | 443 | Firebase remote config |

### Non-iRobot (Apple/system) endpoints also observed
`cstat.cdn-apple.com`, `ipcdn.apple.com`, `gateway.icloud.com`, `entitlements.itunes.apple.com`, `cds.apple.com`, `iphone-ld.apple.com`, `outlook.office365.com`

## Discovery: Deployment Tiers

The discovery endpoint (`disc-prod.iot.irobotapi.com/v1/discover/endpoints?country_code=US`) returns three deployment tiers:

| Tier | svcDeplId | Unauth Base | Auth Base | Notes |
|------|-----------|-------------|-----------|-------|
| v005 | v005 | unauth1.prod | auth1.prod | Oldest, used by existing code |
| v007 | v007 | unauth2.prod | auth2.prod | Middle tier |
| v011 | v011 | unauth3.prod | auth3.prod | **Current** — matches Cabin Roomba `svcDeplId` |

All share the same MQTT endpoint: `a2uowfjvhio0fa-ats.iot.us-east-1.amazonaws.com`

### Key Discovery Fields per Tier
```json
{
  "awsRegion": "us-east-1",
  "httpBase": "https://unauth3.prod.iot.irobotapi.com",
  "httpBaseAuth": "https://auth3.prod.iot.irobotapi.com",
  "httpProdSecBaseAuth": "https://certificatefactory.prod.security.irobotapi.com",
  "mqtt": "a2uowfjvhio0fa-ats.iot.us-east-1.amazonaws.com",
  "mqttApp": "a2uowfjvhio0fa-ats.iot.us-east-1.amazonaws.com",
  "irbtTopics": "v011-irbthbu",
  "userServicesBase": "prod.user-services.irobotapi.com",
  "vStream": "https://vstream.prod.user-services.irobotapi..."
}
```

## Discovery: Gigya API Key Rotation

**Critical finding**: The Gigya API key rotates periodically. The key hardcoded in `irobot-cloud.py` was stale.

- Old key: `3_rWtvjmFv0SYpuCXB-L6MMfFJt4gusZ_YkKK78nak16vd_ChWv5cZ5yfjc_JnaVb0`
- Current key (2026-04-03): `3_rWtvxmUKwgOzu3AUPTMLnM46lj-LxURGflmu5PcE_sGptTbD-wMeshVbLvYpq01K`

**Fix**: Always fetch the key from the discovery endpoint at login time rather than hardcoding.

## Discovery: v2 Login Response Contains MQTT Auth Tokens

The iRobot v2 login endpoint (`{httpBase}/v2/login`) returns more than just AWS Cognito credentials:

```json
{
  "credentials": {
    "AccessKeyId": "...",
    "SecretKey": "...",
    "SessionToken": "...",
    "Expiration": "...",
    "CognitoId": "..."
  },
  "robots": { ... },
  "iot_token": "<base64-encoded JSON>",
  "iot_clientid": "app-IOS-...-XXXXX",
  "iot_signature": "<RSA signature of token>",
  "iot_authorizer_name": "ElPaso248Login-AspenIoTAuthorizer-LHMK6DNLCGZI",
  "mtu": 0
}
```

### iot_token Structure (base64-decoded)
```json
{
  "cognito_id": "us-east-1:4da29890-7e6c-c23c-0f98-4655bbbcdae9",
  "clientid": "app-IOS-F700B76F-80EE-4AB9-9B02-34B210F3B148-5K24GT30",
  "expires_ts": 1775233508,
  "devices": {
    "<BLID_1>": 1,
    "<BLID_2>": 1,
    "<BLID_3>": 1,
    "<BLID_4>": 1
  }
}
```

The `iot_authorizer_name` indicates AWS IoT Custom Authorizer authentication. The `iot_signature` is an RSA signature of the token verified by the authorizer's Lambda function.

## REST API Probing Results

### Endpoints That Work (AWS SigV4 with Cognito credentials)

| Endpoint | Status | Notes |
|----------|--------|-------|
| `auth1/auth3 /v1/{blid}/missionhistory` | ✅ 200 | Both tiers accept SigV4 creds |
| `auth1/auth3 /v1/{blid}/pmaps` | ✅ 200 | Returns `[]` for Cabin Roombas (no persistent maps) |

### Endpoints That Return 403 (IAM policy blocks)

All return `"is not authorized to perform: execute-api:Invoke"` — the Cognito role (`ElPasoData001-LoginCognitoAuthRole`) lacks permissions for these:

- `/v1/{blid}` (robot info)
- `/v1/{blid}/state`
- `/v1/{blid}/shadow`
- `/v1/{blid}/status`
- `/v1/{blid}/maps`
- `/v1/{blid}/timeline`
- `/v1/{blid}/commands`
- `/v2/{blid}/missionhistory`

### Endpoints That Use Different Auth

| Endpoint | Error | Notes |
|----------|-------|-------|
| `content-prod.iot.irobotapi.com` | "Missing Authentication Token" | NOT using AWS SigV4 — different auth (possibly app-issued JWT) |
| `ecomm.prod.user-services.irobotapi.com` | "not authorized" (different API Gateway) | Separate IAM role, different API Gateway ID |
| `certificatefactory.prod.security.irobotapi.com` | "Missing Authentication Token" | For issuing client certs to `user_cert: true` robots |

### Map Endpoint
- `/v1/{blid}/pmaps/{p2map_id}` returns 404 `AspenError.MapNotFound` — endpoint exists but Cabin Roombas don't store persistent maps server-side.

## MQTT Connection Attempts

### Approach 1: SigV4 Presigned WebSocket URL
- **Result**: 403 Forbidden at WebSocket handshake
- **Reason**: Cognito credentials lack `iot:Connect` permission

### Approach 2: AWS IoT Custom Authorizer (query string)
- **Result**: WebSocket handshake succeeds (101 Switching Protocols), but MQTT CONNECT receives immediate WebSocket close frame (opcode 8)
- **Token**: Passed as `token=<base64>` in query string
- **Signature**: Passed as `x-amz-customauthorizer-signature=<RSA sig>`
- **Authorizer name**: `ElPaso248Login-AspenIoTAuthorizer-LHMK6DNLCGZI`

### Approach 3: AWS IoT Custom Authorizer (HTTP headers)
- **Result**: Same — handshake succeeds, MQTT CONNECT rejected with close frame

### Approach 4: MQTT username field auth (AWS recommended)
- **Result**: Same close frame rejection

### Approach 5: Official AWS IoT SDK (`awsiotsdk` Python)
- **Result**: `AWS_ERROR_MQTT_UNEXPECTED_HANGUP`
- Used `mqtt_connection_builder.websockets_with_custom_authorizer()`
- Tried with and without signature, different token key names

### Approach 6: MQTT 5.0 (to get error reason)
- **Result**: CONNACK with rc=135 (Not Authorized)
- **Reason string**: `"CONNACK:Client is not authorized to connect. ReturnCode: 5"`
- Confirmed the authorizer Lambda is executing and returning a deny policy

### Approach 7: Direct TLS MQTT (port 8883) with BLID/password
- **Result**: Connection reset by peer
- **Reason**: Cloud broker requires client certificates for direct TLS (only for `user_cert: true` robots)

### Hypothesis: Why Custom Authorizer Rejects

The authorizer Lambda accepts the WebSocket upgrade (the token/signature pass initial validation) but then rejects the MQTT CONNECT. Possible reasons:
1. **IP/origin allowlist** — authorizer checks the connecting IP against known app fingerprints
2. **Additional header/metadata** — the iOS app sends something in the WebSocket upgrade headers that we're not replicating (User-Agent, custom headers)
3. **Token binding** — the token may be bound to a specific TLS session or client certificate
4. **Rate limiting / device fingerprinting** — prevents non-app clients

## What Works Today

| Capability | Method | Status |
|------------|--------|--------|
| Robot list + credentials | Gigya → iRobot v2 login | ✅ |
| Mission history | REST API (SigV4) | ✅ |
| Persistent maps list | REST API (SigV4) | ✅ (empty for Cabin Roombas) |
| Real-time shadow state | MQTT (custom authorizer) | ❌ Blocked |
| Send commands (start/stop/dock) | MQTT (custom authorizer) | ❌ Blocked |
| Map images/content | content-prod API | ❌ Unknown auth scheme |

## Next Steps

1. **Deeper app analysis**: Extract the iRobot iOS app IPA and decompile to find exact MQTT connection parameters (headers, connection options)
2. **Android approach**: Use an Android emulator with Frida to bypass cert pinning and intercept the actual MQTT traffic
3. **Certificate factory**: Investigate if we can issue a client certificate for our user (would enable direct TLS MQTT)
4. **WebSocket headers**: Use mitmproxy on a jailbroken device or Android emulator to capture the exact WebSocket upgrade request the app sends
5. **MQTT topic exploration**: If we solve auth, subscribe to `v011-irbthbu/{blid}/#` for iRobot-specific topics

## Files

- `dns_logger.py` — mitmproxy addon for SNI hostname logging (passthrough mode)
- `selective_intercept.py` — mitmproxy addon for selective domain interception (blocked by cert pinning)
- `mqtt_shadow.py` — MQTT shadow subscriber using SigV4 presigned WebSocket (403'd)
- `captured_hosts.jsonl` — captured SNI hostnames from the app
- `captured_requests.jsonl` — (empty, cert pinning prevented interception)

## Tool Versions
- mitmproxy 12.2.1
- Python 3.14.0
- awsiotsdk 1.28.2 / awscrt 0.31.3
- websocket-client 1.9.0
