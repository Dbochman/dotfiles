# CLI Path Standardization Plan

## Problem

Smart home CLIs are scattered across 5 different locations with no consistent pattern:

| Location | Examples | Count |
|----------|----------|-------|
| `/opt/homebrew/bin/` | `hue`, `speaker`, `spogo`, `resy` | ~15 |
| `~/.openclaw/bin/` | `nest`, `mysa-status.py`, `sag-wrapper` | ~8 |
| `~/.openclaw/skills/<name>/` | `roomba`, `crosstown-roomba`, `8sleep` | 3 |
| `~/repos/<name>/` | `cielo-cli/cli.js` | 1 |
| `/usr/local/bin/` | `node` (for cielo) | 1 |

This creates problems:
- Scripts like `vacancy-actions.sh` need hardcoded paths for each tool
- New integrations require guessing where the CLI lives
- PATH must include multiple directories for different tools
- Some tools need `node` prefix (`/usr/local/bin/node ~/repos/cielo-cli/cli.js`)
- `requires.bins` in SKILL.md can't resolve when binaries aren't on PATH
- Gateway plist PATH doesn't include `~/.openclaw/bin/`, so `requires.bins` checks fail

## Current State by Tool

### Smart Home CLIs

| Tool | Current Path | How Installed | On PATH? |
|------|-------------|---------------|----------|
| `hue` | `/opt/homebrew/bin/hue` | Homebrew | Yes |
| `nest` | `~/.openclaw/bin/nest` | Copied from dotfiles | No |
| `speaker` | `/opt/homebrew/bin/speaker` | Homebrew | Yes |
| `cielo` | `/usr/local/bin/node ~/repos/cielo-cli/cli.js` | Git clone + node | No (not a single binary) |
| `roomba` | `~/.openclaw/skills/roomba/roomba` | Skill dir | No |
| `crosstown-roomba` | `~/.openclaw/skills/crosstown-roomba/crosstown-roomba` | Skill dir | No |
| `8sleep` | `~/.openclaw/skills/8sleep/8sleep` | Skill dir | No |
| `mysa` | `~/.openclaw/mysa/venv/bin/python3 ~/.openclaw/bin/mysa-status.py` | Python venv | No (2-part invocation) |
| `spogo` | `/opt/homebrew/bin/spogo` | Homebrew | Yes |
| `resy` | `/opt/homebrew/bin/resy` | Homebrew | Yes |
| `samsung-tv` | `/opt/homebrew/bin/samsung-tv` | Homebrew | Yes |

---

## Approach: `~/.openclaw/bin/` as canonical CLI directory

### Why `~/.openclaw/bin/`

- Already used by `nest`, `mysa-status.py`, `sag-wrapper`, dashboards
- OpenClaw-specific — doesn't pollute Homebrew namespace
- Under `~/.openclaw/` which is the operational home for all skills
- Supports wrapper scripts (NOT symlinks — see below)

### Why NOT symlinks

**CRITICAL**: Several skill scripts (e.g., `8sleep`) use `SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"` to find sibling files like `8sleep-api.py`. A symlink from `~/.openclaw/bin/8sleep` → `~/.openclaw/skills/8sleep/8sleep` would resolve `SCRIPT_DIR` to `~/.openclaw/bin/`, which has no `8sleep-api.py`. **Always use wrapper scripts, never symlinks.**

### PATH ordering

```
~/.openclaw/bin : /opt/homebrew/bin : /opt/homebrew/opt/node@22/bin : /usr/local/bin : /usr/bin : /bin
```

`~/.openclaw/bin` goes **first** so custom wrappers take precedence. This is intentional — wrapper names (`cielo`, `roomba`, `8sleep`, `mysa`) don't collide with any Homebrew-installed binary. The only tool in both locations is `nest` (currently in both `~/.openclaw/bin/` and `/opt/homebrew/bin/` won't conflict since the `~/.openclaw/bin/` version wins).

**Rule**: Wrapper names in `~/.openclaw/bin/` MUST NOT shadow Homebrew tool names. Before creating a new wrapper, verify with `brew list --formula | grep <name>`.

### Hybrid strategy

1. **Homebrew-native tools stay in `/opt/homebrew/bin/`**: `hue`, `speaker`, `spogo`, `gws`, `resy`, `goplaces`, `samsung-tv`, `pinchtab`, `peekaboo`
2. **Custom/skill CLIs get wrapper scripts in `~/.openclaw/bin/`**: `cielo`, `roomba`, `crosstown-roomba`, `8sleep`, `mysa`
3. **Scripts already in `~/.openclaw/bin/` stay**: `nest`, `sag-wrapper`, dashboards
4. **`~/.openclaw/bin/` added to PATH** in ALL LaunchAgent plists (mandatory) and `~/.zshrc`
5. **`vacancy-actions.sh` and routines** use bare command names (resolved via PATH)

---

## Phase 1: Create wrapper scripts in `~/.openclaw/bin/`

All wrappers tracked in `openclaw/bin/` in the dotfiles repo, deployed by `dotfiles-pull.command`.

### 1.1 `cielo`

```bash
#!/bin/bash
# cielo — Cielo Home AC control
exec /opt/homebrew/bin/node "$HOME/repos/cielo-cli/cli.js" "$@"
```

### 1.2 `roomba` (Cabin)

```bash
#!/bin/bash
# roomba — Cabin Roomba control (Google Assistant)
SKILL_DIR="$HOME/.openclaw/skills/roomba"
exec "$SKILL_DIR/roomba" "$@"
```

### 1.3 `crosstown-roomba`

```bash
#!/bin/bash
# crosstown-roomba — Crosstown Roomba control (dorita980 MQTT)
SKILL_DIR="$HOME/.openclaw/skills/crosstown-roomba"
exec "$SKILL_DIR/crosstown-roomba" "$@"
```

### 1.4 `8sleep`

```bash
#!/bin/bash
# 8sleep — Eight Sleep Pod control
SKILL_DIR="$HOME/.openclaw/skills/8sleep"
exec "$SKILL_DIR/8sleep" "$@"
```

### 1.5 `mysa`

```bash
#!/bin/bash
# mysa — Mysa thermostat status
exec "$HOME/.openclaw/mysa/venv/bin/python3" "$HOME/.openclaw/bin/mysa-status.py" "$@"
```

---

## Phase 2: Update PATH in ALL LaunchAgent plists (MANDATORY)

Add `~/.openclaw/bin` as the **first** PATH entry in every plist:

```xml
<key>PATH</key>
<string>/Users/dbochman/.openclaw/bin:/opt/homebrew/bin:/opt/homebrew/opt/node@22/bin:/usr/local/bin:/usr/sbin:/usr/bin:/bin</string>
```

**Must update** (not optional):
- `ai.openclaw.gateway.plist` — **required** for `requires.bins` resolution
- `com.openclaw.vacancy-actions.plist`
- `ai.openclaw.dotfiles-pull.plist` (so deployed wrappers are on PATH for smoke tests)
- All other plists that invoke smart home CLIs

---

## Phase 3: Update `vacancy-actions.sh`

Replace hardcoded paths with bare command names:

```bash
# Before
CROSSTOWN_ROOMBA="$HOME/.openclaw/skills/crosstown-roomba/crosstown-roomba"
CABIN_ROOMBA="$HOME/.openclaw/skills/roomba/roomba"
HUE="/opt/homebrew/bin/hue"
NEST="$HOME/.openclaw/bin/nest"
CIELO="/usr/local/bin/node $HOME/repos/cielo-cli/cli.js"

# After — all resolved via PATH
hue --crosstown all-off
nest eco crosstown on
cielo off -d bedroom
crosstown-roomba start all
roomba start floomba
```

---

## Phase 4: Update SKILL.md — both `requires.bins` AND usage examples

### 4.1 Add missing `requires.bins`

| Skill | Current | After |
|-------|---------|-------|
| `cielo-ac` | none | `"requires":{"bins":["cielo"]}` |
| `mysa-thermostat` | none | `"requires":{"bins":["mysa"]}` |

### 4.2 Update usage examples in SKILL.md docs

Replace raw paths with bare command names in the command examples:

| Skill | Before | After |
|-------|--------|-------|
| `cielo-ac` | `/usr/local/bin/node ~/repos/cielo-cli/cli.js status` | `cielo status` |
| `cielo-ac` | `/usr/local/bin/node ~/repos/cielo-cli/cli.js off -d "living room"` | `cielo off -d "living room"` |
| `mysa-thermostat` | `~/.openclaw/mysa/venv/bin/python3 ~/.openclaw/bin/mysa-status.py` | `mysa` |

Other skills (`crosstown-roomba`, `8sleep`, `roomba`) already use bare command names in their SKILL.md examples.

---

## Phase 5: Update shell profile

Add to `~/.zshrc` on Mini:

```bash
export PATH="$HOME/.openclaw/bin:$PATH"
```

---

## Phase 6: Update `dotfiles-pull.command`

After deploying skills, deploy wrapper scripts and run smoke test:

```bash
# Deploy wrappers from dotfiles
BIN_SRC="$REPO/openclaw/bin"
BIN_DST="$HOME/.openclaw/bin"
for wrapper in cielo roomba crosstown-roomba 8sleep mysa; do
  if [ -f "$BIN_SRC/$wrapper" ]; then
    cp "$BIN_SRC/$wrapper" "$BIN_DST/$wrapper"
    chmod +x "$BIN_DST/$wrapper"
  fi
done

# Smoke test — verify all wrappers resolve
FAILED=0
for cmd in cielo roomba crosstown-roomba 8sleep mysa nest hue speaker; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) WARN: $cmd not on PATH" >> "$LOG"
    FAILED=$((FAILED + 1))
  fi
done
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) wrappers: smoke test ($FAILED failures)" >> "$LOG"
```

---

## Wrapper Target Drift

Wrappers are now the **single point of failure** for CLI resolution. If a target moves (skill renamed, repo path changes, venv rebuilt), the wrapper breaks instead of many hardcoded callers breaking — this is better, but only if failures are detected quickly.

**Mitigation:**
- `dotfiles-pull.command` smoke test catches missing targets at deploy time
- Wrappers use `exec` so errors propagate naturally (exit codes, stderr)
- Each wrapper is 2-3 lines — easy to update when targets move
- Wrapper target paths are logged in this plan for reference

**Target reference table:**

| Wrapper | Target | Breaks if... |
|---------|--------|-------------|
| `cielo` | `/opt/homebrew/bin/node` + `~/repos/cielo-cli/cli.js` | Node removed or repo moved |
| `roomba` | `~/.openclaw/skills/roomba/roomba` | Skill not deployed |
| `crosstown-roomba` | `~/.openclaw/skills/crosstown-roomba/crosstown-roomba` | Skill not deployed |
| `8sleep` | `~/.openclaw/skills/8sleep/8sleep` | Skill not deployed |
| `mysa` | `~/.openclaw/mysa/venv/bin/python3` + `~/.openclaw/bin/mysa-status.py` | Venv rebuilt or script moved |

---

## Files to modify

| File | Change |
|------|--------|
| `openclaw/bin/cielo` | **Create** — wrapper script |
| `openclaw/bin/roomba` | **Create** — wrapper to Cabin skill |
| `openclaw/bin/crosstown-roomba` | **Create** — wrapper to Crosstown skill |
| `openclaw/bin/8sleep` | **Create** — wrapper to 8sleep skill |
| `openclaw/bin/mysa` | **Create** — wrapper to venv + script |
| `openclaw/workspace/scripts/vacancy-actions.sh` | **Modify** — bare command names |
| `openclaw/com.openclaw.vacancy-actions.plist` | **Modify** — add `~/.openclaw/bin` to PATH |
| `openclaw/ai.openclaw.gateway.plist` | **Modify** — add `~/.openclaw/bin` to PATH (MANDATORY) |
| `openclaw/ai.openclaw.dotfiles-pull.plist` | **Modify** — add `~/.openclaw/bin` to PATH |
| `openclaw/skills/cielo-ac/SKILL.md` | **Modify** — add `requires.bins`, update examples to `cielo` |
| `openclaw/skills/mysa-thermostat/SKILL.md` | **Modify** — add `requires.bins`, update examples to `mysa` |
| `openclaw/bin/dotfiles-pull.command` | **Modify** — add wrapper deploy + smoke test |
| `zshrc` | **Modify** — add `~/.openclaw/bin` to PATH |

## Verification

1. **Wrappers resolve**: `command -v cielo roomba crosstown-roomba 8sleep mysa nest` — all found
2. **Wrappers work**: `cielo status`, `roomba list`, `crosstown-roomba status`, `8sleep status`, `mysa`
3. **vacancy-actions.sh**: runs with bare names, no hardcoded paths
4. **dotfiles-pull.command**: deploys wrappers + smoke test passes
5. **Gateway**: `requires.bins` resolves for all skills (gateway plist PATH updated)
6. **SKILL.md**: cielo and mysa examples use bare command names
7. **No Homebrew shadow**: `brew list --formula | grep -E '^(cielo|roomba|8sleep|mysa)$'` returns nothing

## Not changing

- Homebrew-native tools (`hue`, `speaker`, `spogo`, `gws`, etc.) — fine where they are
- `nest` CLI — already in `~/.openclaw/bin/`
- `sag-wrapper` — already in the right place
- Dashboard scripts — already in `~/.openclaw/bin/`
- Workspace scripts (`presence-detect.sh`, etc.) — internal scripts, not user-facing CLIs
