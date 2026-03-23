# CLI Path Standardization Plan

## Problem

Smart home CLIs are scattered across 5 different locations with no consistent pattern:

| Location | Examples | Count |
|----------|----------|-------|
| `/opt/homebrew/bin/` | `hue`, `nest`, `speaker`, `spogo`, `resy` | ~15 |
| `~/.openclaw/bin/` | `nest`, `mysa-status.py`, `sag-wrapper` | ~8 |
| `~/.openclaw/skills/<name>/` | `roomba`, `crosstown-roomba`, `8sleep` | 3 |
| `~/repos/<name>/` | `cielo-cli/cli.js` | 1 |
| `/usr/local/bin/` | `node` (for cielo) | 1 |

This creates problems:
- Scripts like `vacancy-actions.sh` need hardcoded paths for each tool
- New integrations require guessing where the CLI lives
- PATH must include multiple directories for different tools
- Some tools need `node` prefix (`/usr/local/bin/node ~/repos/cielo-cli/cli.js`)
- Inconsistent `requires.bins` in SKILL.md can't resolve when binaries aren't on PATH

## Current State by Tool

### Smart Home CLIs (the ones vacancy-actions.sh and routines use)

| Tool | Current Path | How Installed | Notes |
|------|-------------|---------------|-------|
| `hue` | `/opt/homebrew/bin/hue` | Homebrew | Global, on PATH |
| `nest` | `~/.openclaw/bin/nest` | Copied from dotfiles | NOT on PATH |
| `speaker` | `/opt/homebrew/bin/speaker` | Homebrew | Global, on PATH |
| `cielo` | `/usr/local/bin/node ~/repos/cielo-cli/cli.js` | Git clone + node | NOT a single binary |
| `roomba` | `~/.openclaw/skills/roomba/roomba` | Skill dir | NOT on PATH |
| `crosstown-roomba` | `~/.openclaw/skills/crosstown-roomba/crosstown-roomba` | Skill dir | NOT on PATH |
| `8sleep` | `~/.openclaw/skills/8sleep/8sleep` | Skill dir | NOT on PATH |
| `mysa` | `~/.openclaw/mysa/venv/bin/python3 ~/.openclaw/bin/mysa-status.py` | Python venv | Requires 2-part invocation |
| `spogo` | `/opt/homebrew/bin/spogo` | Homebrew | Global, on PATH |
| `resy` | `/opt/homebrew/bin/resy` | Homebrew | Global, on PATH |
| `samsung-tv` | `/opt/homebrew/bin/samsung-tv` | Homebrew | Global, on PATH |

### Supporting Tools

| Tool | Current Path | Notes |
|------|-------------|-------|
| `sag-wrapper` | `/opt/homebrew/bin/sag-wrapper` | Wraps `sag` with secrets injection |
| `pinchtab` | `/opt/homebrew/bin/pinchtab` | Headless browser |
| `gws` | `/opt/homebrew/bin/gws` | Google Workspace CLI |
| `peekaboo` | `/opt/homebrew/bin/peekaboo` | GUI automation |
| `goplaces` | `/opt/homebrew/bin/goplaces` | Google Places |

## Recommended Approach: `~/.openclaw/bin/` as canonical CLI directory

### Why `~/.openclaw/bin/`

- Already used by `nest`, `mysa-status.py`, `sag-wrapper`, dashboards
- OpenClaw-specific — doesn't pollute Homebrew namespace
- Under `~/.openclaw/` which is the operational home for all skills
- Easy to add to PATH in LaunchAgent plists and shell profiles
- Supports both real scripts and wrapper scripts/symlinks

### Why NOT `/opt/homebrew/bin/`

- Managed by Homebrew — `brew cleanup` or upgrades can clobber custom scripts
- Mixes system tools with custom smart home CLIs
- Some tools (cielo, mysa) can't be installed via Homebrew
- Already have tools there that are Homebrew-native (hue, speaker, gws) — don't move those

### Hybrid strategy

1. **Homebrew-native tools stay in `/opt/homebrew/bin/`**: `hue`, `speaker`, `spogo`, `gws`, `resy`, `goplaces`, `samsung-tv`, `pinchtab`, `peekaboo`
2. **Custom/skill CLIs get wrapper scripts in `~/.openclaw/bin/`**: `cielo`, `roomba`, `crosstown-roomba`, `8sleep`, `mysa`
3. **Scripts already in `~/.openclaw/bin/` stay**: `nest`, `sag-wrapper`, dashboards
4. **`~/.openclaw/bin/` added to PATH** in all LaunchAgent plists and `~/.zshrc`
5. **`vacancy-actions.sh` and routines** use bare command names (resolved via PATH)

## Phase 1: Create wrapper scripts in `~/.openclaw/bin/`

### 1.1 `cielo` wrapper

```bash
#!/bin/bash
# cielo — Cielo Home AC control wrapper
exec /opt/homebrew/bin/node "$HOME/repos/cielo-cli/cli.js" "$@"
```

### 1.2 `roomba` wrapper (Cabin)

Already exists as `~/.openclaw/skills/roomba/roomba`. Create a symlink or thin wrapper:

```bash
#!/bin/bash
exec "$HOME/.openclaw/skills/roomba/roomba" "$@"
```

### 1.3 `crosstown-roomba` wrapper

```bash
#!/bin/bash
exec "$HOME/.openclaw/skills/crosstown-roomba/crosstown-roomba" "$@"
```

### 1.4 `8sleep` wrapper

```bash
#!/bin/bash
exec "$HOME/.openclaw/skills/8sleep/8sleep" "$@"
```

### 1.5 `mysa` wrapper

```bash
#!/bin/bash
exec "$HOME/.openclaw/mysa/venv/bin/python3" "$HOME/.openclaw/bin/mysa-status.py" "$@"
```

## Phase 2: Update PATH in LaunchAgent plists

Add `~/.openclaw/bin` to the PATH in all relevant plists:

```xml
<key>PATH</key>
<string>/Users/dbochman/.openclaw/bin:/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:/usr/local/bin:/usr/sbin:/usr/bin:/bin</string>
```

Plists to update:
- `com.openclaw.vacancy-actions.plist`
- `ai.openclaw.gateway.plist` (if not already)
- Any future LaunchAgents

## Phase 3: Update `vacancy-actions.sh`

Replace hardcoded paths with bare command names:

```bash
# Before
CROSSTOWN_ROOMBA="$HOME/.openclaw/skills/crosstown-roomba/crosstown-roomba"
CABIN_ROOMBA="$HOME/.openclaw/skills/roomba/roomba"
HUE="/opt/homebrew/bin/hue"
NEST="$HOME/.openclaw/bin/nest"
CIELO="/usr/local/bin/node $HOME/repos/cielo-cli/cli.js"

# After
# All on PATH via ~/.openclaw/bin + /opt/homebrew/bin
# Just use bare command names
```

## Phase 4: Update SKILL.md `requires.bins`

Verify each skill's `requires.bins` lists a binary name that resolves on PATH. Current state:

| Skill | requires.bins | Resolves? |
|-------|--------------|-----------|
| `hue-lights` | `hue` | Yes (Homebrew) |
| `nest-thermostat` | `nest` | Yes (after Phase 1, `~/.openclaw/bin/nest`) |
| `cielo-ac` | — | Needs `cielo` added |
| `roomba` | `roomba` | Yes (after Phase 1 wrapper) |
| `crosstown-roomba` | `crosstown-roomba` | Yes (after Phase 1 wrapper) |
| `8sleep` | `8sleep` | Yes (after Phase 1 wrapper) |
| `mysa-thermostat` | — | Needs `mysa` added |

## Phase 5: Update shell profile

Add to `~/.zshrc` on Mini (if not already):

```bash
export PATH="$HOME/.openclaw/bin:$PATH"
```

## Phase 6: Update `dotfiles-pull.command`

After deploying skills, also deploy the `~/.openclaw/bin/` wrapper scripts from a tracked location in the dotfiles repo (e.g., `openclaw/bin/`).

## Files to modify

| File | Change |
|------|--------|
| `openclaw/bin/cielo` | **Create** — wrapper script |
| `openclaw/bin/roomba` | **Create** — wrapper to skill |
| `openclaw/bin/crosstown-roomba` | **Create** — wrapper to skill |
| `openclaw/bin/8sleep` | **Create** — wrapper to skill |
| `openclaw/bin/mysa` | **Create** — wrapper to venv script |
| `openclaw/workspace/scripts/vacancy-actions.sh` | **Modify** — use bare command names |
| `openclaw/com.openclaw.vacancy-actions.plist` | **Modify** — add `~/.openclaw/bin` to PATH |
| `openclaw/skills/cielo-ac/SKILL.md` | **Modify** — add `requires.bins: ["cielo"]` |
| `openclaw/skills/mysa-thermostat/SKILL.md` | **Modify** — add `requires.bins: ["mysa"]` |
| `zshrc` | **Modify** — add `~/.openclaw/bin` to PATH (if not present) |

## Verification

1. All wrappers executable and working: `cielo status`, `roomba list`, `crosstown-roomba status`, `8sleep status`, `mysa`
2. `vacancy-actions.sh` works with bare command names
3. `dotfiles-pull.command` deploys wrappers
4. OpenClaw skill `requires.bins` resolves for all skills
5. New LaunchAgent PATH includes `~/.openclaw/bin`

## Not changing

- Homebrew-native tools (`hue`, `speaker`, `spogo`, `gws`, etc.) — they're fine where they are
- `nest` CLI — already in `~/.openclaw/bin/`
- `sag-wrapper` — already in the right place
- Dashboard scripts — already in `~/.openclaw/bin/`
- Workspace scripts (`presence-detect.sh`, etc.) — these aren't CLIs, they're internal scripts
