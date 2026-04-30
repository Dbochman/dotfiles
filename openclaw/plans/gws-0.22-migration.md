# GWS 0.22.x migration plan

**Status:** Deferred. `@googleworkspace/cli` is intentionally pinned at **0.4.4**.
**Owner:** Dylan
**Created:** 2026-04-29

## Why this exists

On 2026-04-29 we attempted to bump `@googleworkspace/cli` from `0.4.4` →
`0.22.5` on the Mini and immediately broke every gws-using skill. The
upgrade was rolled back within a minute. This document captures what
broke, what the new model looks like, and the work required to migrate
when there's a reason to.

There is no urgent reason to migrate today — 0.4.4 works for all
current automations. The new features in 0.22.x (Model Armor sanitize,
pagination, format options) are not load-bearing for our use cases.

## Breaking changes in 0.22.x

The `--account <email>` flag was **completely removed**, both at the
global level and per-command. Account selection now happens via the
`GOOGLE_WORKSPACE_CLI_CONFIG_DIR` environment variable, which points at
a per-account config directory (default `~/.config/gws`).

The old multi-account model — one shared `~/.config/gws/` containing
multiple `credentials.<base64email>.enc` files, switched at runtime
with `--account` — is gone. Each account now lives in its own dir.

Other changes:

- `gws auth list` removed → `gws auth status` reports state of one
  account only (the one in the active config dir)
- `gws auth login` no longer takes `--account`
- New env vars: `GOOGLE_WORKSPACE_CLI_CONFIG_DIR`,
  `GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND` (`keyring` or `file`),
  `GOOGLE_WORKSPACE_CLI_TOKEN`, etc.

The breakage on our side is concrete and reproducible: every skill that
embeds `--account julia.joy.jennings@gmail.com` (or any other account
flag) will return:

```
error: unexpected argument '--account' found
```

## Migration approach: per-account wrapper scripts

The cleanest pattern matching our existing `~/.openclaw/bin/` CLI
standardization is per-account wrapper scripts:

```sh
# ~/.openclaw/bin/gws-julia
#!/bin/sh
GOOGLE_WORKSPACE_CLI_CONFIG_DIR="$HOME/.config/gws/julia" exec gws "$@"
```

Agent and cron calls become:

```bash
# old
gws gmail users messages list --params '{"userId":"me",...}' --account julia.joy.jennings@gmail.com

# new
gws-julia gmail users messages list --params '{"userId":"me",...}'
```

Wrappers needed:

- `gws-dylan` → `~/.config/gws/dylanbochman/`
- `gws-julia` → `~/.config/gws/julia/`
- `gws-bochmanspam` → `~/.config/gws/bochmanspam/`
- `gws-clawdbot` → `~/.config/gws/clawdbot/`

Plain `gws` should remain on `$PATH` as a fallback for ad-hoc use,
defaulting to whichever dir is the "primary" (Dylan's). Either set
`GOOGLE_WORKSPACE_CLI_CONFIG_DIR` in `~/.zshrc` or symlink Dylan's
config dir to the default `~/.config/gws/` location.

## Files to touch

### Skills (~/.openclaw/skills, source: dotfiles/openclaw/skills/)

Search command to find every `--account` reference:

```bash
rg -l '\-\-account' openclaw/skills/
```

Known skills with `--account` in their SKILL.md (counts from
2026-04-29 audit):

- `gws-shared/SKILL.md` (4) — canonical Accounts table; rewrite the
  whole "Accounts" section to use wrappers
- `gws-calendar/SKILL.md` (6)
- `gws-drive/SKILL.md` (6)
- `gws-gmail/SKILL.md` (6)
- `gws-gmail-send/SKILL.md`
- `gws-gmail-triage/SKILL.md`
- `gws-calendar-insert/SKILL.md`
- `gws-calendar-agenda/SKILL.md`
- `gws-tasks/SKILL.md`
- `gws-drive-upload/SKILL.md`
- `recipe-create-gmail-filter/SKILL.md`
- `recipe-save-email-attachments/SKILL.md`
- `recipe-label-and-archive-emails/SKILL.md`
- `recipe-create-vacation-responder/SKILL.md`
- `recipe-find-free-time/SKILL.md`
- `red-apple-events/SKILL.md`
- `opentable/SKILL.md`

Replacement pattern for each:

```diff
- gws gmail users messages list --params '...' --account julia.joy.jennings@gmail.com
+ gws-julia gmail users messages list --params '...'

- gws calendar events list --params '...'    # implicit Dylan default
+ gws-dylan calendar events list --params '...'   # explicit
```

### Cron jobs (dotfiles/openclaw/cron/jobs.json)

Julia's morning briefing prompt embeds the literal string:

> TOOL: Use `gws` CLI (NOT gog). Always include `--account julia.joy.jennings@gmail.com`.

And later:

> gws gmail users messages list --params '{"userId": "me", ...}' --account julia.joy.jennings@gmail.com

Both must be rewritten to use `gws-julia`.

Dylan's morning briefing does not embed `--account` directly — it
delegates to the gws-* skills, which will be updated as part of the
skills work above.

### Documentation

- `dotfiles/openclaw/workspace/MEMORY.md` — GWS section references the
  `--account` flag and the danger of running `gws auth logout` without
  it. Both lines need updating to the new wrapper model.
- `dotfiles/openclaw/workspace/TOOLS.md` — same.
- `dotfiles/MEMORY.md` (auto-memory index) — GWS line says
  `(v0.4.4)`; update to whatever we land on.

## Operational migration steps

These run in order. Do not start until the file edits above are
complete and committed (otherwise mid-flight you have a half-broken
state).

### 1. Pre-flight on laptop

```bash
# Snapshot current 0.4.4 state in case we need it back
cp -a ~/.config/gws ~/.config/gws.0.4.4.backup
```

### 2. Upgrade gws on laptop

```bash
npm install -g @googleworkspace/cli@latest
gws --version  # confirm 0.22.x
```

### 3. Re-auth each account into its own dir

For each account, run a fresh OAuth flow targeting a per-account dir:

```bash
for acct in dylanbochman julia bochmanspam clawdbot; do
  GOOGLE_WORKSPACE_CLI_CONFIG_DIR="$HOME/.config/gws/$acct" \
    gws auth login --full
  # browser opens — sign in as the matching account
done
```

Verify each:

```bash
for acct in dylanbochman julia bochmanspam clawdbot; do
  echo "--- $acct ---"
  GOOGLE_WORKSPACE_CLI_CONFIG_DIR="$HOME/.config/gws/$acct" \
    gws auth status
done
```

Each should report `auth_method: "oauth"` (or similar) and
`encrypted_credentials_exists: true`.

### 4. SCP per-account config dirs to Mini

```bash
for acct in dylanbochman julia bochmanspam clawdbot; do
  ssh dylans-mac-mini "mkdir -p ~/.config/gws/$acct"
  scp -r "$HOME/.config/gws/$acct/" \
    "dylans-mac-mini:.config/gws/$acct/"
done
```

The encryption keys may need to land too — investigate
`GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND=file` mode if Keychain access
keeps biting us under launchd (this was the original reason for the
`.encryption_key` file in 0.4.4; 0.22.x has a new keyring backend
abstraction worth understanding before assuming the old approach
still applies).

### 5. Install wrappers on Mini

Add `~/.openclaw/bin/gws-{dylan,julia,bochmanspam,clawdbot}` (see
template above), make them executable, and confirm they're on
`$PATH`. Per memory, `~/.openclaw/bin` is symlinked into
`/opt/homebrew/bin/` for skill checker visibility — apply the same
pattern.

### 6. Upgrade gws on Mini

```bash
ssh dylans-mac-mini 'PATH=/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:$PATH \
  npm install -g @googleworkspace/cli@latest'
```

### 7. Smoke test

```bash
ssh dylans-mac-mini '~/.openclaw/bin/gws-dylan gmail users messages list \
  --params "{\"userId\":\"me\",\"maxResults\":1}"'
ssh dylans-mac-mini '~/.openclaw/bin/gws-julia gmail users messages list \
  --params "{\"userId\":\"me\",\"maxResults\":1}"'
```

Both should return one message JSON.

### 8. Manually fire one cron job to verify end-to-end

```bash
ssh dylans-mac-mini 'openclaw cron run gws-dylan-morning-briefing-0001 \
  --timeout 600000 --expect-final'
```

Run at a time you actually want a briefing — the cron run delivers.

### 9. Wait for next-morning live firing

Both Dylan's 8AM ET and Julia's 7AM ET fire next morning. Watch
delivery on iMessage. If either fails, check
`~/.openclaw/cron/runs/gws-*-morning-briefing-0001.jsonl` for the
error.

## Rollback plan

If things break and you need 0.4.4 back fast:

```bash
# laptop
npm install -g @googleworkspace/cli@0.4.4
rm -rf ~/.config/gws
cp -a ~/.config/gws.0.4.4.backup ~/.config/gws

# Mini
ssh dylans-mac-mini 'PATH=/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:$PATH \
  npm install -g @googleworkspace/cli@0.4.4'
# (Mini's ~/.config/gws is unchanged from 0.4.4 era as long as you
# created NEW per-account dirs above instead of overwriting the
# default — verify before nuking)
```

The wrapper scripts are harmless under 0.4.4 (env var is ignored),
but the prompts embedded in cron jobs and skills will still call
`gws-julia` etc. which won't exist. So either keep the wrappers as
identity stubs (`exec gws --account julia.joy.jennings@gmail.com "$@"`
for the rollback window) or revert the file changes via `git revert`
of the migration commit before rolling back the binary.

## Open questions to resolve before doing this

1. **Keyring backend on Mini under launchd.** 0.22.x defaults to
   `keyring` backend (macOS Keychain). Our 0.4.4 setup uses the
   `.encryption_key` file because Keychain access from launchd is
   unreliable. Test `GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND=file`
   before doing a real migration.

2. **Default account behavior.** Does plain `gws ...` (no env var)
   fail loudly or silently use whichever account was last logged
   into the default dir? Test before relying on a "primary"
   default.

3. **Multi-account in one dir.** It's possible 0.22.x has an
   undocumented mechanism for multiple accounts in one dir (like
   gcloud's `--configuration` flag). Worth a 15-minute look at the
   gws GitHub issues before committing to the per-dir design.

4. **`gws auth logout` blast radius.** In 0.4.4, running it without
   `--account` nuked everything. In 0.22.x with one-account-per-dir,
   logout should be naturally scoped. Verify.

## When to revisit

Trigger a re-evaluation if any of these become true:

- A 0.22.x feature becomes load-bearing (Model Armor sanitize for a
  compliance use case, e.g.)
- Security CVE or auth flow change in 0.4.4
- gws hits 1.0 (semver promise of stability — better migration moment)
- You're already touching all the gws skills for another reason
  (combine the work)

Until then, the pin holds.
