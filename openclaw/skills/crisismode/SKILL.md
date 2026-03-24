---
name: crisismode
description: Infrastructure health monitoring and recovery for the Mac Mini. Use when asked about system health, disk usage, memory, DNS, service status, infrastructure diagnostics, or "is everything running okay". Also use proactively when troubleshooting service outages (BlueBubbles, gateway, cron failures).
allowed-tools: Bash(crisismode:*)
metadata: {"openclaw":{"emoji":"🏥","requires":{"bins":["crisismode"]}}}
---

# CrisisMode — Infrastructure Health & Recovery

Monitor and diagnose the Mac Mini's infrastructure health using CrisisMode. Runs zero-config health scans, AI-powered diagnosis, and can plan recovery actions.

## Commands

### Quick health scan (start here)
```bash
crisismode scan
```
Returns a scored health summary (0-100) with findings for all detected services and check plugins (disk, memory, DNS, HTTP, TLS).

### Machine-readable scan
```bash
crisismode scan --json
```
Structured JSON output for parsing. Use this when you need to process results programmatically.

### Quick status probe
```bash
crisismode status
```
Fast UP/DOWN check for all configured services.

### Diagnose a specific finding
```bash
crisismode diagnose DISK-003
crisismode diagnose DNS-002
```
Drill into a specific finding ID from the scan output. Runs deeper inspection with severity levels.

### AI-powered diagnosis (requires ANTHROPIC_API_KEY)
```bash
crisismode diagnose --target primary-db
```
Full health assessment + Claude-powered root cause analysis for a configured target.

### Natural language questions
```bash
crisismode ask "why is DNS showing split-brain"
crisismode ask "is disk usage trending up"
```
Ask questions about infrastructure health in plain English.

### Interactive diagnostic REPL
```bash
crisismode ask
```
Opens an interactive session for conversational diagnosis.

### List available agents
```bash
crisismode agent list
```

### Demo mode (no infra needed)
```bash
crisismode demo
```
Walks through a simulated PostgreSQL recovery — useful for understanding the recovery pipeline.

## Health Score

The scan produces a score from 0-100:
- **90-100**: Healthy
- **70-89**: Minor issues, monitor
- **50-69**: Degraded, investigate
- **0-49**: Critical, action required

## Finding IDs

Each finding has an ID like `DISK-003`, `DNS-002`, `PG-001`. Use these IDs with `crisismode diagnose <ID>` to drill deeper.

## Bundled Check Plugins

| Plugin | What It Checks |
|--------|---------------|
| check-disk-usage | Filesystem usage (warn 80%, critical 90%) |
| check-memory-usage | System memory utilization |
| check-dns-resolution | DNS resolver health |
| check-http-endpoint | HTTP connectivity to localhost |
| check-certificate-expiry | TLS cert validity on localhost:443 |

## Configuration

Config file at `~/.crisismode/crisismode.yaml` (auto-detected). Without config, scan uses zero-config auto-detection on standard ports.

## When to Use This Skill

- **Proactive health checks**: Run `crisismode scan` when investigating any service issue
- **Disk alerts**: Before running large operations, check disk space
- **DNS issues**: If services can't resolve hosts, diagnose DNS
- **After incidents**: Run a full scan to verify system recovery
- **Routine monitoring**: Include in health check cron jobs

## Architecture

```
crisismode scan → auto-detect services → run check plugins → score & report
crisismode diagnose → deep inspection → AI analysis → recommendations
crisismode recover → plan → validate → execute (dry-run default)
```

All recovery operations are **dry-run by default** — they show the plan without executing. Use `--execute` only when explicitly instructed.

## Troubleshooting

### PG-001 unknown error
Expected if no PostgreSQL is running. CrisisMode auto-probes port 5432 — ignore if not applicable.

### DNS split-brain warning
Dual-stack resolvers (IPv4 + IPv6) may return different results. Usually benign on home networks.

### Low health score
Run `crisismode scan --json` and examine individual findings. Drill into critical ones with `crisismode diagnose <ID>`.
