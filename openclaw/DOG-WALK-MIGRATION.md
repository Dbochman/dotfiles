# Dog Walk Skill Migration (2026-04-04)

## Summary

The dog walk automation system was extracted from the `ring-doorbell` skill into a standalone `dog-walk` skill. The primary change is architectural: **departure detection now uses Fi GPS exclusively** (Potato's collar leaving the geofence), replacing Ring doorbell vision analysis. Return monitoring is unchanged (Ring motion + WiFi + Fi GPS).

## Why

- Ring vision-based departure was fragile: fisheye distortion caused missed dogs, no Ring Protect at Cabin meant no video analysis at all
- Fi GPS departure detection was already running as a secondary loop and proving more reliable
- The `ring-doorbell` skill was overloaded: CLI queries, vision analysis, Roomba control, return monitoring, and confirmation prompts in a single 1,629-line file
- Splitting into `ring-doorbell` (CLI) + `dog-walk` (automation) makes each skill focused and maintainable

## What Changed

### New: `dog-walk` skill (`openclaw/skills/dog-walk/`)

| File | Purpose |
|------|---------|
| `dog-walk-listener.py` | Fi GPS departure + multi-signal return monitoring (680 lines, down from 1,629) |
| `dog-walk-listener-wrapper.sh` | Secrets loader for LaunchAgent |
| `SKILL.md` | Skill metadata and documentation |

### Simplified: `ring-doorbell` skill (CLI only)

| Kept | Removed |
|------|---------|
| `ring` (bash CLI wrapper) | `ring-listener.py` (moved to dog-walk) |
| `ring-api.py` (Python API) | `ring-listener-wrapper.sh` (moved to dog-walk) |
| `SKILL.md` (updated) | `findmy-locate.sh` (dead code, replaced by Fi GPS) |
| | `IMPLEMENTATION.md` (stale, referenced FindMy/Peekaboo) |

### Departure Detection: Before vs After

| Aspect | Before (ring-listener) | After (dog-walk-listener) |
|--------|------------------------|---------------------------|
| **Primary trigger** | Ring motion → Claude Haiku vision (5 frames) → people/dog count | Fi GPS: Potato leaves geofence (2 readings, >=3 min apart) |
| **Cabin handling** | All motion treated as person+dog → iMessage confirmation prompt | Fi GPS departure (same as Crosstown, no special case) |
| **Accumulation** | 3-min sliding window across Ring motion events | Not needed (GPS is deterministic) |
| **Vision retry** | 5 frames, then 10 frames if only 1 dog seen | Not needed |
| **WiFi pre-check** | Phone on WiFi at decision time → suppress | Not needed (GPS handles this) |

### Return Detection: Unchanged

All three signals remain active during return monitoring:
1. **Ring motion** — event-driven (person at doorbell)
2. **WiFi / network presence** — 60s poll (phone reconnects to WiFi)
3. **Fi GPS** — 60s poll (Potato re-enters geofence)

### Code Removed (~950 lines)

| Component | Lines | Why |
|-----------|-------|-----|
| `analyze_video()` + `extract_multi_frames()` | ~100 | No more vision-based departure |
| `parse_vision_result()` | ~15 | No more vision JSON parsing |
| `check_departure()` accumulator | ~90 | Fi GPS is deterministic, no accumulation needed |
| `_confirmation_poll_loop()` | ~80 | No more "Reply start roombas" flow |
| `_pending_confirmation` state | ~10 | No more confirmation tracking |
| `_cabin_prompt_sent` cooldown | ~10 | No more per-window prompt suppression |
| `send_imessage_image()` | ~35 | No more frame attachments |
| `download_recording()` | ~60 | No more video downloads |
| `_update_state_vision()` | ~15 | No more vision state events |
| `HOME_ADDRESSES` dict | ~15 | FindMy relic |
| Vision constants (`VISION_PROMPT`, `VISION_MODEL`, etc.) | ~15 | No more Claude Haiku calls |
| `findmy-locate.sh` | 89 | Dead code (FindMy removed in Phase 3) |
| `IMPLEMENTATION.md` | ~400 | Stale (referenced FindMy, Peekaboo, old architecture) |

### Renamed Files

| Before | After |
|--------|-------|
| `openclaw/ring-dashboard.py` | `openclaw/dog-walk-dashboard.py` |
| `ai.openclaw.ring-dashboard.plist` | `ai.openclaw.dog-walk-dashboard.plist` |
| `ai.openclaw.ring-listener.plist` | `ai.openclaw.ring-listener.plist.disabled` |

### New LaunchAgent

| Label | File |
|-------|------|
| `ai.openclaw.dog-walk-listener` | `openclaw/launchagents/ai.openclaw.dog-walk-listener.plist` |

### Updated Cross-References

| File | Change |
|------|--------|
| `openclaw/LAUNCHAGENTS.md` | Updated labels and descriptions |
| `openclaw/DASHBOARDS.md` | Updated file paths, data sources, server name |
| `openclaw/DOG-WALK-DASHBOARD.md` | All `ring-listener` → `dog-walk`, architecture diagram |
| `openclaw/HOME-STATE-DATA.md` | Event-driven section and file layout |
| `openclaw/logs/README.md` | Log file name and service label |
| `openclaw/bin/dog-walk-start` | Inbox path `ring-listener` → `dog-walk` |
| `openclaw/bin/oauth-refresh.sh` | Comment update |
| `openclaw/skills/presence/SKILL.md` | `ring-doorbell` → `dog-walk` cross-ref |
| `openclaw/skills/roomba/SKILL.md` | Updated dog walk explanation + cross-ref |
| `openclaw/skills/crosstown-roomba/SKILL.md` | `ring-doorbell` → `dog-walk` cross-ref |
| `openclaw/skills/cabin-routines/SKILL.md` | `ring-doorbell` → `dog-walk` cross-ref |
| `openclaw/skills/crosstown-routines/SKILL.md` | `ring-doorbell` → `dog-walk` cross-ref |
| `openclaw/plans/fi-gps-dog-walk-integration.md` | Marked SUPERSEDED |
| `openclaw/plans/ring-listener-dashboard-metrics.md` | Marked SUPERSEDED |

---

## Deployment Steps (Mac Mini)

After pulling dotfiles:

### 1. Stop old services
```bash
launchctl unload ~/Library/LaunchAgents/ai.openclaw.ring-listener.plist
launchctl unload ~/Library/LaunchAgents/ai.openclaw.ring-dashboard.plist
```

### 2. Deploy new skill
```bash
# dotfiles-pull.command handles this, but manually:
cp -r ~/dotfiles/openclaw/skills/dog-walk/ ~/.openclaw/skills/dog-walk/
chmod +x ~/.openclaw/skills/dog-walk/dog-walk-listener.py
chmod +x ~/.openclaw/skills/dog-walk/dog-walk-listener-wrapper.sh
```

### 3. Migrate state data
```bash
# Move existing walk history and state to new location
mkdir -p ~/.openclaw/dog-walk
cp ~/.openclaw/ring-listener/state.json ~/.openclaw/dog-walk/state.json
cp -r ~/.openclaw/ring-listener/history ~/.openclaw/dog-walk/history
cp ~/.openclaw/ring-listener/fcm-credentials.json ~/.openclaw/dog-walk/fcm-credentials.json
mkdir -p ~/.openclaw/dog-walk/inbox
```

### 4. Deploy new LaunchAgents
```bash
cp ~/dotfiles/openclaw/launchagents/ai.openclaw.dog-walk-listener.plist ~/Library/LaunchAgents/
cp ~/dotfiles/openclaw/launchagents/ai.openclaw.dog-walk-dashboard.plist ~/Library/LaunchAgents/
```

### 5. Deploy renamed dashboard
```bash
cp ~/dotfiles/openclaw/dog-walk-dashboard.py ~/.openclaw/bin/dog-walk-dashboard.py
```

### 6. Start new services
```bash
launchctl load ~/Library/LaunchAgents/ai.openclaw.dog-walk-listener.plist
launchctl load ~/Library/LaunchAgents/ai.openclaw.dog-walk-dashboard.plist
```

### 7. Verify
```bash
launchctl list | grep dog-walk     # Both should show PIDs
tail -20 ~/.openclaw/logs/dog-walk-listener.log   # Should show startup + heartbeat
curl -s http://localhost:8552/api/current | python3 -m json.tool  # Dashboard API
```

### 8. Clean up old services (after verification)
```bash
trash ~/Library/LaunchAgents/ai.openclaw.ring-listener.plist
trash ~/Library/LaunchAgents/ai.openclaw.ring-dashboard.plist
# Keep ~/.openclaw/ring-listener/ for a week as backup, then:
# trash ~/.openclaw/ring-listener/
```

---

## Rollback

If something breaks, restore the old listener:

```bash
launchctl unload ~/Library/LaunchAgents/ai.openclaw.dog-walk-listener.plist
cp ~/dotfiles/openclaw/launchagents/ai.openclaw.ring-listener.plist.disabled ~/Library/LaunchAgents/ai.openclaw.ring-listener.plist
launchctl load ~/Library/LaunchAgents/ai.openclaw.ring-listener.plist
```

Note: The old plist still points to `~/.openclaw/skills/ring-doorbell/ring-listener-wrapper.sh` which no longer exists in dotfiles. You'd need to restore `ring-listener.py` and `ring-listener-wrapper.sh` from git history to the Mini.
