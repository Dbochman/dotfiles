# Crosstown Roomba — OpenClaw Skill Implementation Plan

## Context

Two iRobot Roombas at Crosstown (Boston — 19 Crosstown Ave):
- **Roomba** (aliases: 10max, combo, max) — iRobot Roomba Combo 10 Max (vacuums + mops)
- **scoomba** (aliases: j5) — iRobot Roomba J5 (vacuum only)

## Architecture (Final)

```
Roomba 10 Max ←─MQTT:8883─→ roomba-cmd.js ←──┐
                             (MacBook Pro)     ├─ SSH ─→ crosstown-roomba CLI (Mac Mini)
Roomba J5     ←─MQTT:8883─→ roomba-cmd.js ←──┘
```

**Connect-per-request** via SSH + dorita980 MQTT. No persistent rest980 server — avoids macOS launchd networking restrictions that block outbound TLS connections from LaunchAgent processes (EHOSTUNREACH on local LAN IPs).

### Why not rest980?

rest980 (persistent HTTP server wrapping dorita980) was the original plan but **macOS launchd blocks outbound TCP/TLS connections to LAN IPs** from LaunchAgent processes, even with firewall disabled. Direct interactive SSH works fine. The connect-per-request approach adds ~5-10s per command but is completely reliable.

## Components

| File | Location | Purpose |
|------|----------|---------|
| `openclaw/skills/crosstown-roomba/SKILL.md` | Dotfiles repo | Skill definition |
| `openclaw/skills/crosstown-roomba/crosstown-roomba` | Dotfiles repo | CLI script (bash) |
| `openclaw/skills/crosstown-roomba/roomba-cmd.js` | Dotfiles repo | dorita980 wrapper (Node.js) |
| `~/.openclaw/rest980/env-10max` | MacBook Pro only | 10 Max credentials |
| `~/.openclaw/rest980/env-j5` | MacBook Pro only | J5 credentials |
| `~/.openclaw/rest980/node_modules/dorita980` | MacBook Pro only | MQTT library |

## Robot Details

| Robot | SKU | IP | MAC | BLID (prefix) |
|-------|-----|-----|-----|---------------|
| Roomba (10 Max) | x085020 | 192.168.165.135 | 4c:b9:ea:96:bd:bc | 81039F92... |
| scoomba (J5) | j517020 | 192.168.165.154 | 4c:b9:ea:3c:c5:cc | 195EFAE5... |

## Status

- [x] Phase 1: Discovery & credential extraction
- [x] Phase 2: dorita980 installed, roomba-cmd.js working
- [x] Phase 3: CLI script and SKILL.md created
- [x] Phase 4: crosstown-routines updated (Away: start all, Welcome Home: dock all)
- [x] Phase 4: crosstown-network known devices updated
- [x] Deploy to Mac Mini (git push + dotfiles-pull + skill copy)
- [x] Fix Mini→MacBook Pro SSH (added IdentityFile/IdentityAgent none for id_mini_to_mbp)
- [x] DHCP reservations on AmpliFi router (set via AmpliFi app 2026-03-22)
