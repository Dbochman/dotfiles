# Skills deployment: why symlinks broke and how we fixed it

_2026-03-12_

## The setup

OpenClaw skills live in the dotfiles repo under `openclaw/skills/`. On the Mac Mini where the agent actually runs, they need to end up in `~/.openclaw/skills/`. The obvious approach: symlink each skill directory so changes to the repo are immediately visible to the agent.

```
~/.openclaw/skills/hue-lights → ~/dotfiles/openclaw/skills/hue-lights
~/.openclaw/skills/cielo-ac   → ~/dotfiles/openclaw/skills/cielo-ac
...
```

This worked fine for months. A daily LaunchAgent pulled the dotfiles repo and the symlinks meant the agent picked up changes on the next session reset without any extra deployment step.

## What broke

OpenClaw v2026.3.7 introduced a security check on skill paths. When loading a skill, the gateway calls `fs.realpathSync()` on the skill directory and verifies the resolved path falls within the configured `rootDir` (which is `~/.openclaw`). If it doesn't, the skill gets rejected.

Our symlinks resolved to `~/dotfiles/openclaw/skills/...`, which is outside `~/.openclaw`. Every managed skill failed to load.

The error wasn't obvious. The gateway didn't crash or log a clear rejection message. Skills just didn't appear in the agent's available tools. We noticed when the agent started trying to install the `openhue` npm package instead of using our managed `hue-lights` skill — it had fallen back to a bundled skill because the managed one was silently gone.

## Why the check exists

The `realpathSync` check prevents a skill from escaping its sandbox. Without it, a malicious or misconfigured skill definition could symlink to arbitrary locations on disk and get the agent to read or execute files outside the OpenClaw workspace. It's a reasonable security measure, just incompatible with our deployment model.

## The fix

Instead of fighting the security check, we changed the deployment model. Skills are now real copies, not symlinks. The `dotfiles-pull.command` script handles this after each `git pull`:

```bash
# Deploy skills as real copies (OpenClaw rejects symlinks via realPath check)
SKILLS_SRC="$REPO/openclaw/skills"
SKILLS_DST="$HOME/.openclaw/skills"
for skill_dir in "$SKILLS_SRC"/*/; do
    skill_name=$(basename "$skill_dir")
    rm -rf "$SKILLS_DST/$skill_name"
    cp -R "$skill_dir" "$SKILLS_DST/$skill_name"
    # Remove any nested symlinks that snuck in
    find "$SKILLS_DST/$skill_name" -type l -delete 2>/dev/null
done
```

Each skill directory gets wiped and re-copied on every pull. The `find -type l -delete` at the end catches any symlinks nested inside skill directories, since we had a few cases where a skill contained a symlink to a shared library that also resolved outside `rootDir`.

The tradeoff is that changes aren't instant anymore. After pushing a skill update to the repo, it takes until the next daily pull (6 AM) for the Mini to pick it up, plus a session reset for the agent to reload. Skill changes are infrequent enough that we just trigger a manual pull when it matters.

## Bundled skill conflicts

The symlink fix surfaced a second issue. OpenClaw ships bundled skills (like `openhue` for Philips Hue lights). When our managed `hue-lights` skill was silently rejected, the bundled `openhue` took over. The agent started trying to install the `openhue` npm package globally instead of using the `hue` CLI we'd already configured.

The fix was adding `"skills.entries.openhue.enabled": false` to `openclaw.json` to explicitly disable the bundled skill. This way even if the managed skill fails to load for some reason, the agent won't fall back to a bundled alternative with completely different behavior.

## What I'd take away

Symlinks are great until something resolves them. Any tool that calls `realpathSync`, `readlink -f`, or equivalent will see the actual path, not the symlink. If that path is outside an expected boundary, the symlink becomes invisible in a way that's hard to debug. No error, no warning, the thing just doesn't load.

The other lesson: when a managed skill disappears silently, bundled defaults can take over with different behavior. If you're overriding built-in functionality, explicitly disable the default. Failures should be visible, not quietly papered over by something you didn't configure.
