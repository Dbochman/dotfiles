# Dependency maintenance plan (Mini + Crosstown MBP)

**Status:** Active reference. Last audited 2026-04-29.
**Owner:** Dylan
**Hosts in scope:** `dylans-mac-mini` (gateway, all cron jobs, briefings),
`dylans-macbook-pro` (Crosstown presence scanner).

This doc is a living maintenance checklist for the two macOS hosts that
run our home automation. Use it when:

- Doing a regular dependency audit (suggested cadence: monthly)
- Investigating a regression that might be a stale dependency
- Onboarding a third host into the fleet (extend the matrix below)

The goal is to keep dependencies fresh enough that security CVEs and
bug fixes flow through, while protecting load-bearing integrations
from breaking changes that need careful migration.

## Audit commands

Run these on each host. The output of each should be diffed against
the "Pinned / hold" section before any upgrade.

### Brew formulae + casks

```bash
HOMEBREW_NO_AUTO_UPDATE=1 brew outdated --verbose
HOMEBREW_NO_AUTO_UPDATE=1 brew outdated --cask --greedy
```

### npm globals

```bash
PATH=/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:$PATH npm outdated -g
```

### pipx

```bash
pipx list --short
# Then for each package, check upstream version manually
```

### macOS

```bash
sw_vers
softwareupdate --list
uptime
df -h /
```

### From a laptop, scan the Mini and MBP via SSH:

```bash
# Mini
ssh dylans-mac-mini '<command>'

# MBP (via Mini, since 1Password agent on laptop hangs SSH)
ssh dylans-mac-mini "ssh -i ~/.ssh/id_mini_to_mbp -o IdentityAgent=none dylans-macbook-pro '<command>'"
```

## Pinned / hold list

These are deliberately pinned. **Do not bump without consulting the
linked plan or skill.**

| Package | Pin | Reason | Plan/Skill |
|---|---|---|---|
| `@googleworkspace/cli` | 0.4.4 | Drops `--account` flag in 0.22.x; breaks every gws skill | `openclaw/plans/gws-0.22-migration.md` |
| `openclaw` | manual review | Auto-upgrade removed 2026-03-12. May overwrite LaunchAgent plist. | `openclaw-upgrade-plist-overwrite` skill |

The pin on `@googleworkspace/cli` is informational only — npm has no
global pinning mechanism. Future-Claude should read MEMORY.md /
TOOLS.md before any `npm install -g @googleworkspace/cli@latest`.

## Risk tiers

Categorize every outdated dep before deciding to bump.

### Tier 1 — Safe remote (do anytime)

- Brew formulae routine bumps (libraries, CLIs without persistent daemons)
- Brew casks where the app isn't in active use during the upgrade
- npm globals that are pure CLIs with no daemon/auth dependency

Recovery if broken: roll back via `brew install <name>@<old-version>`
or `npm install -g <pkg>@<old-version>`. SSH-recoverable.

### Tier 2 — Reversible but auth-sensitive

Touches an auth chain that takes meaningful effort to repair if it
breaks (browser OAuth re-auth, scp dance, etc).

Examples:
- `@anthropic-ai/claude-code` — powers the `claude` CLI used by
  `ai.openclaw.oauth-refresh` LaunchAgent. If the refresh agent
  stops working, OAuth tokens age out and `openclaw` agent calls
  fail. Recovery: re-auth on laptop, scp to Mini.
- `1password-cli` — used in `~/.openclaw/.env-token` chain. Cache-only
  pattern reduces blast radius but version skew can break biometric.
- `tailscale` (formula) — supplies the Mini's CLI for Serve/status while the
  logged-in macOS GUI/network extension remains the active backend. Keep
  `/usr/sbin` on the gateway PATH so backend discovery can use `lsof`; a CLI
  upgrade or backend-selection regression can disable private Control UI/node
  access. See `tailscale-macos-localapi-stale-port` skill.

Recovery: SSH-recoverable but with a re-auth detour.

### Tier 3 — Requires babysitting / physical access

- `openclaw` — service reinstall can replace the gateway plist. Back up the
  live plist and service-environment files, then verify the generated
  service-env → FDA app-wrapper chain after upgrade. Regenerate with
  `openclaw gateway install --force --wrapper ...` when invalid; do not blindly
  restore an older plist. Use `bootout` + `bootstrap` only when the loaded plist
  changes. See `openclaw-upgrade-plist-overwrite` and
  `openclaw-post-upgrade-scope-fix` skills.
- `pinchtab` — browser-automation library. Major version bumps may
  break grocery-reorder, presence-receive flows. Test against the
  highest-impact skill before committing.
- macOS major / minor updates — restart-required, ~30 min downtime
  for all cron jobs and the gateway. Schedule.

Recovery: SSH still works after most failures, but the cost of a
stuck state is high (e.g. gateway crash-loops require reading err
logs and editing plists). Do during a window where you can babysit.

### Tier 4 — Skip / pinned

See "Pinned / hold list" above.

## 2026-04-29 audit results

**Hosts:** Mini (uptime 26 days), MBP (uptime 5 min — fresh boot for
macOS 26.4.1 install).

### Done remotely (Tier 1)

| Host | What | Versions |
|---|---|---|
| MBP | `brew upgrade` | ~57 formulae, all routine |
| MBP | macOS Tahoe | 26.3 → 26.4.1 (user-driven, required reboot) |
| MBP | Tailscale (in-app self-update before audit) | 1.94.x → 1.96.5 |
| Mini | `brew upgrade` | ~30 formulae + 1password-cli cask |
| Mini | npm: `@openai/codex` | 0.114.0 → 0.125.0 |
| Mini | npm: `@steipete/summarize` | 0.11.1 → 0.14.1 |
| Mini | npm: `@tobilu/qmd` | 2.0.1 → 2.1.0 |

Verification after each: gateway log activity, presence push freshness,
gws auth still works.

### Held during this audit

| Host | What | Current → Latest | Why held |
|---|---|---|---|
| Mini | `@anthropic-ai/claude-code` | 2.1.76 → 2.1.123 | Tier 2 — OAuth refresh chain. Bump in a window where you can verify the next refresh fires cleanly. |
| Mini | `openclaw` | 2026.4.2 → 2026.4.27 | Tier 3 — see procedure below. ~25 versions of drift. **Done 2026-05-10** — bumped past 2026.4.27 to v2026.5.7. Doctor auto-rewrote `openclaw.json` (added `anthropic.enabled: true`, version-stamp bump only); see `~/repos/openclaw-operator/audit-log.md` 2026-05-10 entry. |
| Mini | `pinchtab` | 0.7.6 → 0.10.0 | Tier 3 — major bump, breaks browser automation downstream if incompatible. Test against grocery-reorder before. **Done 2026-05-10** — bumped to v0.11.0. Required: (1) manual `node scripts/postinstall.js` after `npm install -g` because npm postinstall didn't fire (downloads platform-specific Mach-O binary to `~/.pinchtab/bin/<version>/`), (2) `pinchtab config set security.allowEvaluate true` because `/evaluate` is now 403 by default — grocery-reorder hits this endpoint directly. New profile path is `~/.pinchtab/profiles/default/` (was `~/.pinchtab/chrome-profile/`); session cookies migrated cleanly. Backup at `~/.pinchtab.pre-0.11.0/` on Mini. |
| Mini | macOS 26.4 → 26.4.1 | system | Tier 3 — full downtime ~30 min. Schedule. |
| MBP | `pinchtab` | check at next audit | unknown if installed |
| Both | `@googleworkspace/cli` | 0.4.4 | **Tier 4 / pinned** — see migration plan |

## Deferred procedures

### Claude Code on Mini (Tier 2)

```bash
# 1. Snapshot current
ssh dylans-mac-mini 'PATH=/opt/homebrew/opt/node@22/bin:$PATH; \
  claude --version > /tmp/claude-version-pre.txt'

# 2. Upgrade
ssh dylans-mac-mini 'PATH=/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:$PATH; \
  npm install -g @anthropic-ai/claude-code@latest'

# 3. Verify CLI works
ssh dylans-mac-mini 'PATH=/opt/homebrew/opt/node@22/bin:$PATH; \
  claude --version'

# 4. Verify next OAuth refresh fires cleanly. The refresh runs every
#    6 hours via ai.openclaw.oauth-refresh. Watch:
ssh dylans-mac-mini 'tail -f ~/.openclaw/logs/oauth-refresh.log'
#    Or trigger manually:
ssh dylans-mac-mini 'launchctl kickstart -k gui/$(id -u)/ai.openclaw.oauth-refresh'

# 5. If refresh fails, roll back:
ssh dylans-mac-mini 'PATH=/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:$PATH; \
  npm install -g @anthropic-ai/claude-code@2.1.76'
```

### OpenClaw on Mini (Tier 3)

Follow the `openclaw-upgrade-plist-overwrite` skill. Outline:

```bash
# 1. Backup all OpenClaw plists
ssh dylans-mac-mini 'cp -a ~/Library/LaunchAgents/ai.openclaw.*.plist /tmp/openclaw-plists-backup/ \
  && cp -a ~/Library/LaunchAgents/com.openclaw.*.plist /tmp/openclaw-plists-backup/'

# 2. Backup gateway plist specifically
ssh dylans-mac-mini 'cp ~/Library/LaunchAgents/<the-gateway-plist>.plist /tmp/gateway-plist.bak'

# 3. Snapshot SQLite runtime state; canonical definitions already live in git
ssh dylans-mac-mini 'sqlite3 ~/.openclaw/state/openclaw.sqlite ".backup /tmp/openclaw.sqlite.pre-upgrade"'

# 4. Upgrade
ssh dylans-mac-mini 'PATH=/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:$PATH; \
  npm install -g openclaw@latest'

# 5. Inspect the post-upgrade launch contract. A generated regular plist is
#    healthy when service-env wrapper -> env file -> FDA app wrapper remains.
ssh dylans-mac-mini 'plutil -p ~/Library/LaunchAgents/ai.openclaw.gateway.plist'

# 6. If that chain is broken, follow openclaw-upgrade-plist-overwrite and
#    regenerate with: openclaw gateway install --force --wrapper <FDA-wrapper>

# 7. Check for new required scopes
ssh dylans-mac-mini 'cat ~/.openclaw/devices/paired.json'
# If "pairing required" / scope-upgrade in audit log, see openclaw-post-upgrade-scope-fix skill

# 8. Watch the current generated-service log
ssh dylans-mac-mini 'tail -f ~/Library/Logs/openclaw/gateway.log'

# 9. Smoke test: trigger a low-stakes cron job
ssh dylans-mac-mini 'openclaw cron run <test-job-id> --timeout 300000 --expect-final'
```

Strong signal to do this in the AM after Julia's 7AM briefing has
finished, so a botched upgrade only loses Dylan's 8AM briefing or
later jobs — not Julia's.

### pinchtab on Mini (Tier 3)

The risk is grocery-reorder breaking silently. Test before committing:

```bash
# 1. Save current
ssh dylans-mac-mini 'PATH=/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:$PATH; \
  pinchtab --version'  # or wherever the version flag is

# 2. Check the major-version changelog at npm/github for breaking changes
#    https://www.npmjs.com/package/pinchtab

# 3. If clean, upgrade
ssh dylans-mac-mini 'PATH=/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:$PATH; \
  npm install -g pinchtab@latest'

# 4. Test the highest-impact skill manually:
ssh dylans-mac-mini '<grocery-reorder dry-run command>'

# 5. If broken: roll back
ssh dylans-mac-mini 'PATH=/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:$PATH; \
  npm install -g pinchtab@0.7.6'
```

### Historical macOS 26.4.1 Mini upgrade (completed)

The commands below record the completed 26.4.1 maintenance window; do not rerun
that version-specific installer. The Mini is on macOS 26.5.1 as of 2026-06-27.

```bash
ssh dylans-mac-mini 'softwareupdate --install --restart --agree-to-license \
  "macOS Tahoe 26.4.1-25E253"'
```

For future macOS upgrades, retain the post-reboot verification:

- Gateway came back: `tail -20 ~/Library/Logs/openclaw/gateway.log`
- LaunchAgents loaded: `launchctl list | grep openclaw | head`
- Presence push: `stat -f '%Sm %N' ~/.openclaw/presence/crosstown-scan.json`
- Tailscale CLI works: `tailscale status` (this is where the LocalAPI
  stale-port issue could re-trigger; see skill)
- `~/.openclaw/.env-token` and other secret-cache files survived

## Audit cadence

Suggested rhythm:

- **Monthly**: run audit commands, file results in this doc's audit
  history section, do Tier 1 bumps remotely
- **Quarterly**: do one Tier 2 bump if the diff is substantial
- **Opportunistically**: when at Crosstown, do queued Tier 3 work
- **As needed**: macOS minor versions, security CVE responses

A `/schedule` recurring agent could remind us monthly. Not
implemented yet — flag if missed audits become a pattern.

## See also

- `openclaw/plans/gws-0.22-migration.md` — gws migration when we're
  ready to bump it
- `openclaw/plans/archive/system-hardening-2026-04.md` — completed broader hardening sprint
  context
- Skills: `openclaw-upgrade-plist-overwrite`,
  `openclaw-post-upgrade-scope-fix`,
  `tailscale-macos-localapi-stale-port`,
  `1password-cli-launchd-hang`
- `dotfiles/openclaw/workspace/TOOLS.md` — runtime tool reference
  (notes the gws pin)
