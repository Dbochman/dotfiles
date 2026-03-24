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
| `openclaw/crisismode/checks/` | Custom check plugins (BB, gateway, launchd) |
| `openclaw/cron/jobs.json` | Includes `crisismode-health-scan-0001` cron job |
| `openclaw/bin/dotfiles-pull.command` | Updated: deploys wrapper, skill, config, and check plugins |

### dotfiles-pull Integration

The `dotfiles-pull.command` script handles:
- Copying the `crisismode` wrapper to `~/.openclaw/bin/` (with `chmod +x`)
- Deploying `crisismode.yaml` to `~/.crisismode/`
- Deploying custom check plugins to `~/.crisismode/checks/`
- Deploying the skill to `~/.openclaw/skills/crisismode/`
- Smoke testing that `crisismode` resolves on PATH

## Custom Check Plugins

Three custom plugins deployed to `~/.crisismode/checks/`:

| Plugin | What It Checks |
|--------|---------------|
| check-bluebubbles | BB server health, Private API status, proxy mode (needs `BLUEBUBBLES_PASSWORD`) |
| check-openclaw-gateway | HTTP `/health` endpoint + launchctl process status (PID, exit code) |
| check-launchd-services | 6 critical LaunchAgents: gateway, nest-dashboard, usage-dashboard, nest-snapshot, bb-watchdog, presence-receive |

## Health Scan Cron Job

**Job ID:** `crisismode-health-scan-0001`
**Schedule:** 9AM and 9PM ET daily (`0 9,21 * * *`)
**Behavior:** Errors-only alerting — runs `crisismode scan --json`, ignores PG-001 and benign DNS split-brain, only messages Dylan when score < 70 or critical/warning findings.

## Current Health Scan

Running `crisismode scan` on the Mini produces 6 findings (3 built-in + 3 custom plugins):

| ID | Service | Status | Notes |
|----|---------|--------|-------|
| PG-001 | PostgreSQL | unknown | Auto-probe on port 5432, no PG running (expected) |
| DNS-002 | DNS | recovering/healthy | Dual-stack resolvers (IPv4 + IPv6) sometimes trigger split-brain warning — benign on home network |
| DISK-003 | Disk | healthy | ~63% used, 684GB free |
| PLUG-001 | check-bluebubbles | healthy | Private API active, proxy=lan-url |
| PLUG-002 | check-launchd-services | healthy | All 6 services running |
| PLUG-003 | check-openclaw-gateway | healthy | HTTP 200, gateway PID active |

**Score: 82/100** (PG auto-probe + DNS dual-stack are the only drags)

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

## Future Enhancements

### MCP Gateway Integration

When OpenClaw adds MCP support to the agent runtime (currently v2026.3.13 accepts but ignores `mcpServers`), wire in the CrisisMode MCP server for native tool access:

```json
{
  "mcpServers": {
    "crisismode": {
      "command": "/opt/homebrew/opt/node@22/bin/node",
      "args": ["/Users/dbochman/repos/crisismode/mcp-entry.mjs"],
      "cwd": "/Users/dbochman/repos/crisismode"
    }
  }
}
```

The entry point (`mcp-entry.mjs`) is already tested and deployed.

### Additional Check Plugins

- **check-openclaw-dashboards**: HTTP probes for Nest (8550) and Usage (8551) dashboards + data freshness checks against history JSONL files
- **check-presence-detection**: Verify presence scan is running on MacBook Pro, state file is fresh
- **check-cielo-token**: Verify Cielo AC token refresh LaunchAgent is working (token age < 30min)

### Recovery Playbooks

Write CrisisMode markdown playbooks for common Mini recovery scenarios:
- BB restart (soft restart via API, then hard restart via launchctl)
- Gateway restart with secrets reload
- Stale secrets refresh (`openclaw-refresh-secrets`)
- Full service restart sequence after reboot
