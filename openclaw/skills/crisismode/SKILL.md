---
name: crisismode
description: Infrastructure health monitoring and recovery for the Mac Mini. Use when asked about system health, disk usage, memory, DNS, service status, infrastructure diagnostics, or "is everything running okay". Also use proactively when troubleshooting service outages (gateway, native iMessage, or cron failures).
allowed-tools: Bash(crisismode:*)
metadata: {"openclaw":{"emoji":"🏥","requires":{"bins":["crisismode"]}}}
---

# CrisisMode — Infrastructure Health & Recovery

Monitor and diagnose the Mac Mini's infrastructure health using CrisisMode. Uses the pinned CrisisMode 0.10.0 CLI, the explicit Mini config, and local health plugins. It can also diagnose findings and plan recovery actions.

## Commands

### Quick health scan (start here)
```bash
crisismode scan
```
Returns a raw health score (0–100), DNS/disk findings, local gateway and launchd plugin results, network triage and coverage information. This is Mini scope, not a health guarantee for Crosstown or every household service.

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
crisismode diagnose <ID-from-the-current-scan>
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

The raw 0–100 score is a weighted summary, not an all-clear. A score of 90 can
still include a DNS warning. Report the findings and coverage alongside the
score; never change the score or discard a failure to make the headline look healthy.

## Finding IDs

Finding IDs are positional and change when targets/order change. Use an ID from the current scan with `crisismode diagnose <ID>`. Never ignore or allowlist a finding by ID. Read its service, status, signals, triage and visibility evidence; `unhealthy`, `unknown`, missing checks and blocked coverage all need to remain visible. Preserve upstream best-effort labels on plugin checks.

## Installed checks and configuration

The wrapper pins `~/.local/share/openclaw/vendor/crisismode-v0.10.0` and defaults
`CRISISMODE_CONFIG` to `~/.crisismode/crisismode.yaml`; an explicit `--config`
still wins. Upstream does not auto-discover that home-directory config.
The tracked source is `~/dotfiles/openclaw/crisismode/crisismode.yaml`.

The config declares local DNS and disk targets; v0.10 rejects an empty target list.
Plugins in `~/.crisismode/checks/` check gateway HTTP/launchd status and the selected
Mini LaunchAgents. Memory, TLS and remote hosts are not implied by this scope.
The weekly report owns scheduled scans; the old standalone scan cron is retired.

## When to Use This Skill

- **Proactive health checks**: Run `crisismode scan` when investigating any service issue
- **Disk alerts**: Before running large operations, check disk space
- **DNS issues**: If services can't resolve hosts, diagnose DNS
- **After incidents**: Run a full scan to verify system recovery
- **Routine monitoring**: Include in health check cron jobs

## Architecture

```
crisismode scan → explicit Mini config + network triage → local checks/plugins → score & coverage
crisismode diagnose → deep inspection → AI analysis → recommendations
crisismode recover → plan → validate → execute (dry-run default)
```

All recovery operations are **dry-run by default** — they show the plan without executing. Use `--execute` only when explicitly instructed.

## Troubleshooting

### Unexpected PostgreSQL finding

PostgreSQL is not configured on this Mini. Verify the effective config and any
environment-derived targets; do not simply suppress the finding. Missing or
invalid config should be reported as an error.

### DNS split-brain warning

Compare `resolver_reachability`, `resolution_correctness` and `zone_consistency`.
If reachability and lookup checks pass but answer sets differ, say exactly that
and leave the cause unverified. Do not assume dual-stack/CDN differences are
benign without separate evidence. Timeouts, SERVFAIL, NXDOMAIN, DNSSEC errors,
latency and real resolver failures must remain visible.

### Low health score or incomplete scan

Run `crisismode scan --json` and examine findings and coverage. A high score does
not override an unhealthy finding, failed triage or missing check. An unavailable
scan is not a clean scan. Use the current finding ID for deeper diagnosis.
