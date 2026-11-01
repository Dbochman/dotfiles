# CrisisMode on Mac Mini

The Mini uses [CrisisMode v0.10.0](https://github.com/trs-80/crisismode/releases/tag/v0.10.0)
(commit `f38c795`) for read-only local health scans. The weekly report owns the
scheduled scan; `crisismode-health-scan-0001` was retired into
`weekly-report-0001` (Sundays at 3 PM Eastern).

## Deployment

| Component | Location |
|-----------|----------|
| Versioned source and build | `~/.local/share/openclaw/vendor/crisismode-v0.10.0` |
| Node runtime | `~/.local/share/openclaw/runtimes/node-v24.22.1-darwin-arm64/bin/node` |
| CLI wrapper | `~/.openclaw/bin/crisismode` |
| Config | `~/.crisismode/crisismode.yaml` |
| Custom health plugins | `~/.crisismode/checks/` |
| Skill | `~/.openclaw/skills/crisismode/SKILL.md` |
| Previous 0.8.0 source/build | `~/repos/crisismode` (preserved for rollback) |

Tracked sources live in `openclaw/bin/crisismode`, `openclaw/crisismode/`, and
`openclaw/skills/crisismode/`. `dotfiles-pull.command` copies the wrapper, config,
plugins and skill; it **does not install/build the versioned dependency**.
Stage and verify that dependency before deploying the wrapper on another Mini.
The old untracked `mcp-entry.mjs` is preserved; this change does not activate MCP.

## Config and scope

Upstream searches `--config`, then `CRISISMODE_CONFIG`, then the working directory;
it does **not** automatically discover `~/.crisismode/crisismode.yaml`. The wrapper
sets that file as the default environment path, preserving explicit overrides.
The weekly helper passes the path explicitly and scans from its directory, so
an unrelated repository's config/discovery files cannot change the scheduled scope.
Missing/invalid config fails visibly instead of falling back to a phantom database.

The config explicitly declares `local-dns` and `local-disk`. Version 0.10 requires
at least one target or service; the old empty list is no longer valid. PostgreSQL
is not configured on this Mini and is not excluded by finding ID.

Two local plugins supplement those targets:

- `check-openclaw-gateway`: loopback `/health` plus launchd process status.
- `check-launchd-services`: gateway, Nest/Usage dashboards, snapshot and presence
  LaunchAgents. This checks job state, not complete end-to-end service behavior.

These checks cover the Mini, not Crosstown or every household service. Upstream
labels generic plugin findings **best-effort**; the weekly report preserves that
qualification. Scan triage checks local interfaces, gateway, DNS and internet
access. No recovery commands are executed.

## Reporting contract

`openclaw-weekly-report.py` runs `crisismode scan --config ... --json` with a
45-second timeout and no AI-summary API key. It requires a successful process,
a valid score and nonempty findings. It reports all nonhealthy findings and
nonhealthy signal evidence, missing expected checks, blocked coverage, and failed
or unverified network triage. Missing scans are unavailable, never clean.

Finding IDs are positional and must not be allowlisted. There are no `PG-001`
or `DNS-002` exemptions. If DNS reports only differing answer sets while its
reachability and lookup signals pass, the report states those facts and leaves
the cause unverified. Different answers alone do not establish a benign dual-stack
condition; resolver failures, latency, DNSSEC errors and other DNS evidence remain
visible. The raw upstream score is not adjusted.

## Updating and rollback

Review the pinned tag's instructions, dependency hooks and build entry points.
Use a separate versioned Git worktree, preserving the old checkout and its
untracked MCP shim. The 0.10.0 build uses the repository's pinned pnpm 10.30.3:

```bash
# From the reviewed v0.10.0 worktree, with the pinned Node 24 runtime on PATH:
corepack pnpm install --frozen-lockfile --ignore-scripts
corepack pnpm --filter @crisismode/agent-sdk run build
corepack pnpm exec tsc
node scripts/copy-json-assets.mjs
```

Run the upstream scan/config/evidence tests and the dotfiles weekly-report tests,
then scan with the candidate CLI and tracked config before activation. Back up
the deployed wrapper, config, helper and skill into a private temporary directory;
replace each file atomically. Deploy config before the new wrapper. Confirm
`crisismode --version`, a successful JSON scan, expected check coverage and the
weekly helper's rendered health lines. No daemon restart is needed.

For rollback, restore the backed-up wrapper, config, helper and skill together.
The previous `~/repos/crisismode/dist/cli/index.js` and Node 22.23.4 remain intact.
Do not delete the versioned worktree while a deployed wrapper points to it.

## November 1, 2026 verification

The candidate 0.10.0 scan with the explicit config returned 90/100: disk and both
local plugins healthy; DNS answer-set differences remained visible, with resolver
reachability and lookup checks passing. Local/network triage passed. No PostgreSQL
finding was present. This snapshot is evidence for the deployment, not a permanent
health guarantee.
