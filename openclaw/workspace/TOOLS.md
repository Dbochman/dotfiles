# TOOLS.md - Local Notes

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to your setup.

## Smart Home Devices

### Both Houses
- Philips Hue lights
- iRobot Roombas
- Nest thermostats
- Google Nest cameras
- Google smart speakers

## Spotify Connect Devices

These device IDs are used with the Andre Spotify Connect API (`api/spotify/transfer`).

| Device Name | Device ID | Type | Notes |
|---|---|---|---|
| Kitchen speaker | `b8581271559fd61aa994726df743285c` | CastAudio | Google Nest Audio (currently active) |
| Dylan's Mac mini | `0eb8b896cf741cd28d15b1ce52904ae7940e4aae` | Computer | Cabin server |
| Dylan's MacBook Pro | `173fd1e1d533e5a1c59fc25979c3baccc3af5d07` | Computer | |
| Dylan's Mac | `13bc12a88f007bf49d13997fc64c0a6640f49440` | Computer | |

## What Goes Here

Environment-specific notes: camera names, SSH hosts, speaker names, device nicknames, TTS preferences. Skills define _how_ tools work; this file captures _your_ specific setup details.
## BlueBubbles Private API

Base URL: `http://localhost:1234/api/v1`
Auth: `?password=${BLUEBUBBLES_PASSWORD}` (env var, no 1Password needed)

### Typing Indicator

```bash
curl -s -X POST "http://localhost:1234/api/v1/chat/${CHAT_GUID}/typing?password=${BLUEBUBBLES_PASSWORD}"
```

Stops automatically when you send a message.

### Send Reaction / Tapback

```bash
curl -s -X POST "http://localhost:1234/api/v1/message/react?password=${BLUEBUBBLES_PASSWORD}" \
  -H "Content-Type: application/json" \
  --data-raw '{"chatGuid":"<CHAT_GUID>","selectedMessageGuid":"<MESSAGE_GUID>","reaction":"<TYPE>"}'
```

Reaction types: `love`, `like`, `dislike`, `laugh`, `emphasize`, `question`

### Send Message (Private API method)

> **Note:** Prefer the OpenClaw message tool for sending messages — it handles
> routing, logging, and delivery queue. Use these Private API curl commands
> only for things OpenClaw doesn't expose yet (typing indicators, reactions).

```bash
curl -s -X POST "http://localhost:1234/api/v1/message/text?password=${BLUEBUBBLES_PASSWORD}" \
  -H "Content-Type: application/json" \
  --data-raw '{"chatGuid":"<CHAT_GUID>","message":"<TEXT>","tempGuid":"temp-<UNIQUE>","method":"private-api"}'
```

### Chat GUIDs

- DMs use `any;-;` prefix (e.g., `any;-;dylanbochman@gmail.com`)
- Group chats use `iMessage;+;` prefix
- Always read the GUID from inbound message metadata, don't hardcode
