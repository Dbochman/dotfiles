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
- `@openai/codex` — this is not only an interactive CLI on the Mini.
  OpenClaw runs the configured global binary as a shared Codex app-server, so
  an app-server that was already running can keep the old executable after npm
  replaces it. After an actual Codex version change, restart
  `ai.openclaw.gateway`, wait for gateway health, and run the live no-delivery
  OpenAI smoke check documented in `LAUNCHAGENTS.md`. `codex --version` and a
  model-list check only validate the replacement binary on disk; they do not
  prove that the gateway retired its previous child process.
- `@anthropic-ai/claude-code` — powers the `claude` CLI used by
  `ai.openclaw.oauth-refresh` LaunchAgent. If the refresh agent
  stops working, OAuth tokens age out and `openclaw` agent calls
  fail. Recovery: re-auth on laptop, scp to Mini.
- `1password-cli` — used only by attended exact-field cache refreshes. No
  LaunchAgent, cron job, or gateway child may read `.env-token` or invoke it;
  version skew can still break attended biometric refresh.
- Tailscale — the logged-in macOS app/network extension is the authoritative
  backend. `/opt/homebrew/bin/tailscale` is a source-controlled wrapper that
  executes the app-bundled CLI with `TAILSCALE_BE_CLI=1`, keeping the CLI and
  daemon on the exact same build. Keep `/usr/sbin` on the gateway PATH so
  backend discovery can use `lsof`; an app update or backend-selection
  regression can disable private Control UI/node access. The Homebrew formula
  is retained unlinked only as a recovery artifact; do not relink its CLI.
  See `tailscale-macos-localapi-stale-port` skill.

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

### Codex CLI on Mini (Tier 2)

The gateway must recycle its shared Codex app-server after a real CLI version
change. On 2026-07-09, npm installed Codex `0.144.0` while the running gateway
continued using a shared `0.142.5` app-server; later `gpt-5.6-sol` turns failed
with a newer-Codex requirement and fell back to Anthropic. The stale child
eventually retired, but the upgrade procedure must not depend on that cleanup.

```bash
# 1. Snapshot the installed version
export PATH=/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:$PATH
before=$(codex --version)

# 2. Upgrade
npm install -g @openai/codex@latest

# 3. Restart only when the installed version actually changed
after=$(codex --version)
if [[ "$before" != "$after" ]]; then
  launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway
fi

# 4. Require the local health endpoint to recover
for _ in {1..30}; do
  curl -fsS http://127.0.0.1:18789/health >/dev/null && break
  sleep 1
done
curl -fsS http://127.0.0.1:18789/health >/dev/null
```

Then run the **OpenAI verification** no-delivery smoke request in
`LAUNCHAGENTS.md`. Require `agentMeta.provider=openai`,
`agentMeta.model=gpt-5.6-sol`, and `executionTrace.fallbackUsed=false`. A
successful fallback response is not a passing smoke test.

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
- `~/.openclaw/.secrets-refresh.env` and `.secrets-cache` survived; the legacy
  `.env-token` is not part of the LaunchAgent/cron credential contract

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
