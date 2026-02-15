---
name: echonest
description: Control the EchoNest collaborative music queue and manage the EchoNest server. Use when asked about music, queue, now playing, adding songs, searching for music, skipping songs, deploying EchoNest, checking server health, or viewing logs.
allowed-tools: Bash(echonest:*)
metadata: {"openclaw":{"emoji":"🎵","requires":{"bins":["curl","ssh"]}}}
---

# EchoNest - Collaborative Music Queue

EchoNest is a collaborative music queue at https://echone.st. Control the queue via REST API and manage the server via SSH.

## Queue Management (REST API)

All queue endpoints are public (no auth needed for reads and basic writes).

### Now Playing
```bash
curl -s https://echone.st/playing/
```

### View Queue
```bash
curl -s https://echone.st/queue/
```

### View Specific Song
```bash
curl -s https://echone.st/queue/<track_id>
```

### Search for Music (Spotify)
```bash
curl -s 'https://echone.st/search/v2?q=bohemian+rhapsody'
```

### Add a Song
```bash
# Spotify
curl -s -X POST https://echone.st/add_song \
  -d 'track_uri=spotify:track:4u7EnebtmKWzUH433cf5Qv&email=dylanbochman@gmail.com'

# YouTube
curl -s -X POST https://echone.st/add_song \
  -d 'track_uri=youtube:dQw4w9WgXcQ&email=dylanbochman@gmail.com'

# SoundCloud
curl -s -X POST https://echone.st/add_song \
  -d 'track_uri=soundcloud:123456&email=dylanbochman@gmail.com'
```
The `track_uri` format is `<source>:<id>`. Get the ID from search results.

### Jam (Show Appreciation)
```bash
curl -s -X POST https://echone.st/jam \
  -d 'id=<track_id>&email=dylanbochman@gmail.com'
```

### Quick Jam (Now Playing)
```bash
curl -s https://echone.st/api/jammit/
```

### Blast Airhorn
```bash
curl -s -X POST https://echone.st/blast_airhorn \
  -d 'name=classic&email=dylanbochman@gmail.com'
```

### View Play History
```bash
# Last 10 plays
curl -s https://echone.st/history/10

# User history
curl -s https://echone.st/user_history/<userid>
```

### YouTube Lookup
```bash
curl -s 'https://echone.st/youtube/lookup?id=<video_id>'
curl -s 'https://echone.st/youtube/playlist?id=<playlist_id>'
```

### Health Check
```bash
curl -s https://echone.st/health
```

## Managing Queue (requires API token)

These endpoints require Bearer token authentication. The token is stored in 1Password at `op://OpenClaw/EchoNest API Token/password`.

### Skip Current Song
```bash
curl -s -X POST https://echone.st/api/queue/skip \
  -H "Authorization: Bearer $ECHONEST_API_TOKEN"
```

### Remove Song from Queue
```bash
curl -s -X POST https://echone.st/api/queue/remove \
  -H "Authorization: Bearer $ECHONEST_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"id": "<track_id>"}'
```

### Vote on a Song
```bash
# Upvote
curl -s -X POST https://echone.st/api/queue/vote \
  -H "Authorization: Bearer $ECHONEST_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"id": "<track_id>", "up": true}'

# Downvote
curl -s -X POST https://echone.st/api/queue/vote \
  -H "Authorization: Bearer $ECHONEST_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"id": "<track_id>", "up": false}'
```

### Pause Playback
```bash
curl -s -X POST https://echone.st/api/queue/pause \
  -H "Authorization: Bearer $ECHONEST_API_TOKEN"
```

### Resume Playback
```bash
curl -s -X POST https://echone.st/api/queue/resume \
  -H "Authorization: Bearer $ECHONEST_API_TOKEN"
```

### Clear Entire Queue
```bash
curl -s -X POST https://echone.st/api/queue/clear \
  -H "Authorization: Bearer $ECHONEST_API_TOKEN"
```

**Note:** All management endpoints return `{"ok": true}` on success or `{"error": "message"}` on failure.

## Spotify Connect (requires API token)

These endpoints allow playback transfer and device control via Spotify Connect.

### Get Available Devices
```bash
curl -s https://echone.st/api/spotify/devices \
  -H "Authorization: Bearer $ECHONEST_API_TOKEN"
```
Returns list of available Spotify Connect devices with their IDs and names.

### Transfer Playback to Device
```bash
curl -s -X POST https://echone.st/api/spotify/transfer \
  -H "Authorization: Bearer $ECHONEST_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"device_id": "<device_id>", "play": true}'
```
Transfers playback to the specified device. Set `"play": true` to resume playback, `"play": false` to transfer but keep paused.

### Get Spotify Playback Status
```bash
curl -s https://echone.st/api/spotify/status \
  -H "Authorization: Bearer $ECHONEST_API_TOKEN"
```
Returns info about what's currently playing and which device it's on.

## Rich Queue Data (requires API token)

Enhanced endpoints with full metadata including vote counts, jam counts, comments, duration, and score.

### Get Queue with Full Metadata
```bash
curl -s https://echone.st/api/queue \
  -H "Authorization: Bearer $ECHONEST_API_TOKEN"
```
Returns `{"queue": [...], "now": "<ISO timestamp>"}`. Each track includes: id, title, artist, trackid, src, user, img, big_img, duration, vote, jam, comments, score, auto.

### Get Now Playing with Full Metadata
```bash
curl -s https://echone.st/api/playing \
  -H "Authorization: Bearer $ECHONEST_API_TOKEN"
```
Returns: id, title, artist, trackid, src, user, img, big_img, duration, vote, jam, comments, starttime, endtime, paused, pos, type, now (server timestamp for clock sync).

### Real-Time Event Stream (SSE)
```bash
curl -N https://echone.st/api/events \
  -H "Authorization: Bearer $ECHONEST_API_TOKEN"
```
Server-Sent Events stream for real-time updates. Keepalive comments every 15 seconds. Use `Ctrl+C` to disconnect.

**Event types:**
- `queue_update` — Full queue array (same shape as `/api/queue`)
- `now_playing` — Now-playing object (same shape as `/api/playing`)
- `player_position` — `{"src": "...", "trackid": "...", "pos": N}`
- `volume` — `{"volume": N}`

## Server Operations (SSH)

SSH to the EchoNest DigitalOcean droplet via the `echonest-droplet` host.

### Check Container Status
```bash
ssh echonest-droplet 'docker compose -f /opt/andre/docker-compose.yaml ps'
```

### View Application Logs
```bash
# Recent logs
ssh echonest-droplet 'docker compose -f /opt/andre/docker-compose.yaml logs --tail=50 andre'

# Follow logs (use timeout to avoid hanging)
ssh echonest-droplet 'timeout 10 docker compose -f /opt/andre/docker-compose.yaml logs -f --tail=20 andre'

# Redis logs
ssh echonest-droplet 'docker compose -f /opt/andre/docker-compose.yaml logs --tail=20 redis'

# Player worker logs
ssh echonest-droplet 'docker compose -f /opt/andre/docker-compose.yaml logs --tail=20 player'
```

### Restart Services
```bash
# Restart all
ssh echonest-droplet 'cd /opt/andre && docker compose restart'

# Restart specific service
ssh echonest-droplet 'cd /opt/andre && docker compose restart andre'
ssh echonest-droplet 'cd /opt/andre && docker compose restart player'
ssh echonest-droplet 'cd /opt/andre && docker compose restart redis'
```

### Deploy Code Updates
```bash
# 1. Sync code from local repo to server
rsync -avz --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='.env' --exclude='local_config.yaml' --exclude='.cache' \
  ~/repos/andre/ echonest-droplet:/opt/andre/

# 2. Rebuild and restart
ssh echonest-droplet 'cd /opt/andre && docker compose up -d --build'

# 3. Verify
ssh echonest-droplet 'docker compose -f /opt/andre/docker-compose.yaml ps'
curl -s https://echone.st/health
```
**Note:** Always confirm with Dylan before deploying.

### Redis Operations
```bash
# Check Redis memory usage
ssh echonest-droplet 'docker exec andre_redis redis-cli -a \$REDIS_PASSWORD INFO memory 2>/dev/null | grep used_memory_human'

# Trigger Redis backup
ssh echonest-droplet 'docker exec andre_redis redis-cli -a \$REDIS_PASSWORD BGSAVE 2>/dev/null'

# Count Redis keys
ssh echonest-droplet 'docker exec andre_redis redis-cli -a \$REDIS_PASSWORD DBSIZE 2>/dev/null'
```

### Server Resource Usage
```bash
ssh echonest-droplet 'df -h / && echo --- && free -m && echo --- && docker stats --no-stream'
```

## Architecture

- **Server:** DigitalOcean Droplet (192.81.213.152, NYC1, $6/mo)
- **Reverse Proxy:** Caddy (auto HTTPS via Let's Encrypt)
- **Services:** Flask app (port 5001), Background worker (master_player.py), Redis 7
- **Source Code:** ~/repos/andre/ (local), /opt/andre/ (server)
- **Network:** Redis is internal-only (Docker network); not exposed to internet
- **SSH Host:** `echonest-droplet` (configured in ~/.ssh/config, uses deploy key)
- **Tailscale:** `100.92.192.62` (backup SSH access, bypasses fail2ban)

## Notes

- All REST API queue endpoints are public (no auth cookies needed)
- `email=dylanbochman@gmail.com` should be used as the user identifier for API calls
- Music sources: Spotify, YouTube, SoundCloud (podcasts also supported)
- Search always goes through Spotify's API
- The Bender auto-fill engine automatically queues songs when the queue runs low
- Always confirm with Dylan before deploying code or restarting services
- Vote, skip, remove, pause, and clear queue are available via token-authenticated REST API (see Managing Queue section)
