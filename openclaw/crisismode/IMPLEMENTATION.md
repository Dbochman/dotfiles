# CrisisMode on Mac Mini

Infrastructure health monitoring for the OpenClaw Mac Mini, powered by [CrisisMode](https://github.com/trs-80/crisismode) v0.4.0.

## Architecture

```
crisismode CLI (wrapper)
  └─ node ~/repos/crisismode/dist/cli/index.js
       ├─ Zero-config service detection (PG, Redis, etcd, Kafka ports)
       ├─ Bundled check plugins (disk, memory, DNS, HTTP, TLS)
       ├─ AI diagnosis via Claude (requires ANTHROPIC_API_KEY)
       └─ MCP server (staged — OpenClaw MCP support not yet wired)

OpenClaw agent
  └─ crisismode skill (Bash(crisismode:*))
       └─ Agent can run any crisismode CLI command on demand
```

## What's Deployed

### Mac Mini (`dylans-mac-mini`)

| Component | Location |
|-----------|----------|
| Repo | `~/repos/crisismode` (cloned from GitHub, built with pnpm) |
| CLI wrapper | `~/.openclaw/bin/crisismode` |
| Skill | `~/.openclaw/skills/crisismode/SKILL.md` |
| Config | `~/.crisismode/crisismode.yaml` |
| MCP entry point | `~/repos/crisismode/mcp-entry.mjs` (staged, not active) |

### Dotfiles (`~/repos/dotfiles`)

| File | Purpose |
|------|---------|
| `openclaw/bin/crisismode` | CLI wrapper (deployed to Mini by dotfiles-pull) |
| `openclaw/skills/crisismode/SKILL.md` | OpenClaw skill definition |
| `openclaw/crisismode/crisismode.yaml` | Site config (deployed to `~/.crisismode/`) |
| `openclaw/bin/dotfiles-pull.command` | Updated: deploys wrapper, skill, and config |

### dotfiles-pull Integration

The `dotfiles-pull.command` script handles:
- Copying the `crisismode` wrapper to `~/.openclaw/bin/` (with `chmod +x`)
- Deploying `crisismode.yaml` to `~/.crisismode/`
- Deploying the skill to `~/.openclaw/skills/crisismode/`
- Smoke testing that `crisismode` resolves on PATH

## Current Health Scan

Running `crisismode scan` on the Mini produces three findings:

| ID | Service | Status | Notes |
|----|---------|--------|-------|
| PG-001 | PostgreSQL | unknown | Auto-probe on port 5432, no PG running (expected) |
| DNS-002 | DNS | recovering/healthy | Dual-stack resolvers (IPv4 + IPv6) sometimes trigger split-brain warning — benign on home network |
| DISK-003 | Disk | healthy | ~63% used, 684GB free |

**Score: 63-77/100** (varies based on DNS dual-stack timing)

## MCP Server Status

CrisisMode ships an MCP server (`mcp-entry.mjs`) exposing tools over stdio:
- `crisismode_scan` — zero-config health scan
- `crisismode_diagnose` — AI-powered diagnosis
- `crisismode_status` — quick UP/DOWN probe
- `crisismode_list_agents` — list recovery agents

**Status:** Entry point created and tested. OpenClaw v2026.3.13 accepts `mcpServers` in config but logs `"ignoring N MCP servers"` — not yet wired into the agent runtime. Revisit on next OpenClaw release.

## Usage

```bash
# Quick health scan
crisismode scan

# Machine-readable JSON
crisismode scan --json

# Diagnose a specific finding
crisismode diagnose DNS-002

# Ask a question (requires ANTHROPIC_API_KEY)
crisismode ask "is disk usage trending up"

# List available agents
crisismode agent list

# Demo mode (no infra needed)
crisismode demo
```

## Updating CrisisMode

```bash
ssh dylans-mac-mini
cd ~/repos/crisismode
git pull
PATH=/opt/homebrew/opt/node@22/bin:$PATH npx -y pnpm install && npx -y pnpm build
```

---

## Next Steps

### 1. Custom Check Plugins for Mini Services

The bundled check plugins cover system-level health (disk, memory, DNS). The Mini runs several application-level services that need custom check plugins:

#### check-bluebubbles

Monitor BlueBubbles server health.

```bash
crisismode init --plugin check-bluebubbles
```

What it should check:
- **health verb**: HTTP GET `http://localhost:1234/api/v1/server/info?password=$BLUEBUBBLES_PASSWORD` returns 200
- **diagnose verb**: Check Private API status (`private_api: true`), proxy mode, connected devices count, last message timestamp freshness
- **Signals**: server reachable, Private API enabled, proxy mode correct (`lan-url`), no stale socket (last message < 30min ago)
- **Credentials**: `BLUEBUBBLES_PASSWORD` from env (sourced from `~/.openclaw/.secrets-cache`)

Expected responses:
- Healthy: BB responding, Private API active, recent message activity
- Warning: BB responding but Private API disabled, or no messages in 30+ min
- Critical: BB unreachable on port 1234, or returning errors

#### check-openclaw-gateway

Monitor OpenClaw gateway health.

```bash
crisismode init --plugin check-openclaw-gateway
```

What it should check:
- **health verb**: HTTP GET `http://localhost:18789/health` returns 200
- **diagnose verb**: Check gateway uptime, connected channels (BlueBubbles plugin loaded), active agent sessions
- **Signals**: gateway reachable, BB plugin loaded, no crash loops (check `launchctl list ai.openclaw.gateway` exit status)

#### check-openclaw-dashboards

Monitor Nest and Usage dashboards.

```bash
crisismode init --plugin check-openclaw-dashboards
```

What it should check:
- **health verb**: HTTP GET `http://localhost:8550` (Nest dashboard) and `http://localhost:8551` (Usage dashboard) return 200
- **diagnose verb**: Check last data point freshness in history files (`~/.openclaw/nest-history/`, `~/.openclaw/usage-history/`)
- **Signals**: dashboard processes running, data not stale (< 1hr old)

#### check-launchd-services

Monitor critical LaunchAgent services.

```bash
crisismode init --plugin check-launchd-services
```

What it should check:
- **health verb**: `launchctl list` for each critical service returns PID > 0 or exit status 0
- **Services to monitor**:
  - `ai.openclaw.gateway` (must have PID)
  - `ai.openclaw.nest-dashboard` (must have PID)
  - `ai.openclaw.usage-dashboard` (must have PID)
  - `com.bluebubbles.server` (runs and exits, PID `-` is normal)
  - `ai.openclaw.nest-snapshot` (runs and exits, PID `-` is normal)
  - `com.openclaw.bb-watchdog` (runs and exits, PID `-` is normal)
- **Signals**: each service status, crash detection (exit code != 0 for persistent services)

#### Plugin deployment

Check plugins live in `~/.crisismode/checks/` on the Mini. Each plugin is a directory with:
- `check.sh` — executable (receives JSON stdin, outputs JSON stdout)
- `manifest.json` — metadata (name, description, targetKinds, verbs)

Exit codes: 0=OK, 1=warning, 2=critical, 3=unknown.

### 2. Cron Job for Periodic Health Scans

Add an OpenClaw cron job that runs `crisismode scan --json`, evaluates the score, and alerts Dylan via iMessage if the score drops below a threshold.

#### Job definition (for `~/.openclaw/cron/jobs.json`)

```json
{
  "id": "crisismode-health-scan",
  "name": "CrisisMode Health Scan",
  "schedule": {
    "expr": "0 */6 * * *"
  },
  "prompt": "Run `crisismode scan --json` and evaluate the results. If the health score is below 70, or any finding has status 'critical' or 'unknown' (excluding PG-001 which is expected), send a concise alert to Dylan summarizing the issues. If everything is healthy (score >= 70, no critical findings), do NOT send a message — silent success. Format any alert as: 'CrisisMode health score: X/100' followed by bullet points for each issue.",
  "delivery": {
    "channel": "bluebubbles",
    "target": "dylanbochman@gmail.com",
    "onlyOnOutput": true
  },
  "timeout": 60000
}
```

**Schedule**: Every 6 hours (00:00, 06:00, 12:00, 18:00 UTC).

**Behavior**: Errors-only alerting — only messages Dylan when something is wrong. Silent on healthy scans. Ignores PG-001 (no PostgreSQL expected on Mini).

#### Alternative: Standalone LaunchAgent

If preferred over an OpenClaw cron job, a lightweight LaunchAgent that runs the scan directly:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>ai.openclaw.crisismode-scan</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-c</string>
    <string>
      export PATH="$HOME/.openclaw/bin:/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:$PATH"
      RESULT=$(crisismode scan --json 2>/dev/null | tail -1)
      SCORE=$(echo "$RESULT" | /opt/homebrew/bin/jq -r '.score // 0')
      if [ "$SCORE" -lt 70 ]; then
        echo "$RESULT" >> "$HOME/.openclaw/logs/crisismode-alerts.log"
      fi
    </string>
  </array>
  <key>StartInterval</key>
  <integer>21600</integer>
  <key>StandardOutPath</key>
  <string>/tmp/crisismode-scan.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/crisismode-scan.err</string>
</dict>
</plist>
```

The OpenClaw cron job approach is recommended since it can send iMessage alerts natively via the BB channel.
